"""
Tests for Phase 5 Inventory Reconciliation Engine.

Verifies:
  - reconcile_product() correctly classifies SHRINK, PHANTOM, MISCLASSIFICATION, NONE.
  - Severity scoring thresholds (OK, LOW, MEDIUM, HIGH, CRITICAL).
  - Suggested action mapping per severity and classification.
  - reconcile_store() handles multiple products in one call.
  - flag_anomalies() correctly filters by minimum severity.
  - PhysicalCountWorkflow lifecycle: OPEN → COUNTED → REVIEWED → COMMITTED.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Generator
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.models.core import BucketType, MovementType, Organization, Product, Store, StoreProduct
from app.schemas.inventory import MovementCreate, PostingSpec
from app.services import ledger
from app.services.reconciliation import (
    AnomalySeverity,
    CountSessionStatus,
    DiscrepancyClass,
    PhysicalCountWorkflow,
    SuggestedAction,
    flag_anomalies,
    reconcile_product,
    reconcile_store,
)
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Test DB Guard                                                               #
# --------------------------------------------------------------------------- #
_engine: Engine | None = None
_sm = None


def get_test_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.fail("TEST_DATABASE_URL not set", pytrace=False)
    parsed = urlparse(test_url)
    db_name = parsed.path.lstrip("/").lower()
    if not (db_name.endswith("_test") or db_name.startswith("test_")):
        pytest.fail(f"SAFETY: db '{db_name}' must end with _test", pytrace=False)
    _engine = create_engine(test_url, pool_pre_ping=True)
    return _engine


def get_sm():
    global _sm
    if _sm is None:
        _sm = sessionmaker(bind=get_test_engine(), autoflush=False, autocommit=False)
    return _sm


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = get_sm()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def recon_ctx(db_session: Session) -> Generator[dict, None, None]:
    """Creates org, store, and two products for reconciliation tests."""
    org_id = uuid.uuid4()
    store_id = uuid.uuid4()
    pid1 = uuid.uuid4()
    pid2 = uuid.uuid4()
    suffix = str(uuid.uuid4())[:8]

    org = Organization(id=org_id, name=f"ReconOrg {suffix}", code=f"REC_{suffix}")
    store = Store(id=store_id, organization_id=org_id, store_code=f"RST_{suffix}", name="Recon Store")
    p1 = Product(id=pid1, organization_id=org_id, sku=f"RECON_P1_{suffix}", name="Product 1", unit_of_measure="unit")
    p2 = Product(id=pid2, organization_id=org_id, sku=f"RECON_P2_{suffix}", name="Product 2", unit_of_measure="unit")
    sp1 = StoreProduct(organization_id=org_id, store_id=store_id, product_id=pid1)
    sp2 = StoreProduct(organization_id=org_id, store_id=store_id, product_id=pid2)

    db_session.add_all([org, store, p1, p2, sp1, sp2])
    db_session.flush()

    # Seed ledger: product 1 gets 100 SELLABLE, product 2 gets 50 SELLABLE
    ledger.record_movement(db_session, MovementCreate(
        organization_id=org_id, store_id=store_id, product_id=pid1,
        movement_type=MovementType.GRN_RECEIPT, quantity_delta=Decimal("100.000"),
        occurred_at=datetime.now(timezone.utc), source_system="RECON_TEST",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("100.000"))],
    ))
    ledger.record_movement(db_session, MovementCreate(
        organization_id=org_id, store_id=store_id, product_id=pid2,
        movement_type=MovementType.GRN_RECEIPT, quantity_delta=Decimal("50.000"),
        occurred_at=datetime.now(timezone.utc), source_system="RECON_TEST",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("50.000"))],
    ))
    db_session.commit()

    ctx = {"org_id": org_id, "store_id": store_id, "pid1": pid1, "pid2": pid2}

    try:
        yield ctx
    finally:
        try:
            db_session.rollback()
            db_session.execute(text("DELETE FROM inventory_movement_postings WHERE organization_id = :oid"), {"oid": org_id})
            db_session.execute(text("DELETE FROM inventory_movements WHERE organization_id = :oid AND movement_type = 'REVERSAL'"), {"oid": org_id})
            db_session.execute(text("DELETE FROM inventory_movements WHERE organization_id = :oid"), {"oid": org_id})
            db_session.execute(text("DELETE FROM store_products WHERE organization_id = :oid"), {"oid": org_id})
            db_session.execute(text("DELETE FROM products WHERE organization_id = :oid"), {"oid": org_id})
            db_session.execute(text("DELETE FROM stores WHERE organization_id = :oid"), {"oid": org_id})
            db_session.execute(text("DELETE FROM organizations WHERE id = :oid"), {"oid": org_id})
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise


# --------------------------------------------------------------------------- #
# Tests: reconcile_product                                                     #
# --------------------------------------------------------------------------- #

def test_reconcile_product_no_discrepancy_ok(db_session: Session, recon_ctx: dict):
    """When physical matches ledger exactly, severity is OK and no action needed."""
    ctx = recon_ctx
    report = reconcile_product(
        db_session, ctx["org_id"], ctx["store_id"], ctx["pid1"],
        physical_counts={BucketType.SELLABLE: Decimal("100.000")},
    )
    assert report.overall_severity == AnomalySeverity.OK
    assert report.overall_classification == DiscrepancyClass.NONE
    assert report.suggested_action == SuggestedAction.OK
    assert report.total_gap == Decimal("0.000")


def test_reconcile_product_small_shrink_low_severity(db_session: Session, recon_ctx: dict):
    """A 1-unit shrink on 100-unit stock is LOW severity (< 2%)."""
    ctx = recon_ctx
    report = reconcile_product(
        db_session, ctx["org_id"], ctx["store_id"], ctx["pid1"],
        physical_counts={BucketType.SELLABLE: Decimal("99.000")},  # 1 missing
    )
    assert report.overall_severity == AnomalySeverity.LOW
    assert report.overall_classification == DiscrepancyClass.SHRINK
    assert report.suggested_action == SuggestedAction.MONITOR
    assert report.total_gap == Decimal("-1.000")


def test_reconcile_product_large_shrink_critical(db_session: Session, recon_ctx: dict):
    """A 60-unit shrink on 100-unit stock exceeds CRITICAL threshold (> 25% and > 50 abs)."""
    ctx = recon_ctx
    report = reconcile_product(
        db_session, ctx["org_id"], ctx["store_id"], ctx["pid1"],
        physical_counts={BucketType.SELLABLE: Decimal("40.000")},  # 60 missing
    )
    assert report.overall_severity == AnomalySeverity.CRITICAL
    assert report.overall_classification == DiscrepancyClass.SHRINK
    assert report.suggested_action == SuggestedAction.ESCALATE


def test_reconcile_product_phantom_stock_detected(db_session: Session, recon_ctx: dict):
    """Physical count shows MORE than ledger — phantom stock / over-count."""
    ctx = recon_ctx
    report = reconcile_product(
        db_session, ctx["org_id"], ctx["store_id"], ctx["pid1"],
        physical_counts={BucketType.SELLABLE: Decimal("115.000")},  # 15 extra
    )
    assert report.overall_classification == DiscrepancyClass.PHANTOM
    assert report.total_gap == Decimal("15.000")


def test_reconcile_product_misclassification_zero_net_gap(db_session: Session, recon_ctx: dict):
    """
    When total physical matches ledger total but buckets differ,
    classification is MISCLASSIFICATION.
    """
    ctx = recon_ctx
    # Ledger: 100 SELLABLE, 0 QUARANTINED
    # Physical: 90 SELLABLE, 10 QUARANTINED → same total, different buckets
    report = reconcile_product(
        db_session, ctx["org_id"], ctx["store_id"], ctx["pid1"],
        physical_counts={
            BucketType.SELLABLE: Decimal("90.000"),
            BucketType.QUARANTINED: Decimal("10.000"),
        },
    )
    assert report.overall_classification == DiscrepancyClass.MISCLASSIFICATION
    assert report.total_gap == Decimal("0.000")
    assert report.suggested_action in (SuggestedAction.CORRECT, SuggestedAction.PHYSICAL_COUNT, SuggestedAction.INVESTIGATE)


# --------------------------------------------------------------------------- #
# Tests: reconcile_store + flag_anomalies                                     #
# --------------------------------------------------------------------------- #

def test_reconcile_store_returns_reports_for_all_products(db_session: Session, recon_ctx: dict):
    ctx = recon_ctx
    physical = {
        ctx["pid1"]: {BucketType.SELLABLE: Decimal("100.000")},   # exact
        ctx["pid2"]: {BucketType.SELLABLE: Decimal("35.000")},     # 15 shrink on 50
    }
    reports = reconcile_store(db_session, ctx["org_id"], ctx["store_id"], physical)
    assert len(reports) == 2

    by_product = {r.product_id: r for r in reports}
    assert by_product[ctx["pid1"]].overall_severity == AnomalySeverity.OK
    # 15/50 = 30% → CRITICAL
    assert by_product[ctx["pid2"]].overall_severity in (AnomalySeverity.HIGH, AnomalySeverity.CRITICAL)


def test_flag_anomalies_filters_correctly(db_session: Session, recon_ctx: dict):
    ctx = recon_ctx
    physical = {
        ctx["pid1"]: {BucketType.SELLABLE: Decimal("100.000")},   # OK
        ctx["pid2"]: {BucketType.SELLABLE: Decimal("30.000")},     # 20/50 = 40% → HIGH+
    }
    reports = reconcile_store(db_session, ctx["org_id"], ctx["store_id"], physical)
    flagged = flag_anomalies(reports, min_severity=AnomalySeverity.HIGH)
    # Only pid2 should be flagged
    assert all(r.product_id == ctx["pid2"] for r in flagged)
    assert len(flagged) >= 1


# --------------------------------------------------------------------------- #
# Tests: PhysicalCountWorkflow lifecycle                                      #
# --------------------------------------------------------------------------- #

def test_physical_count_workflow_full_lifecycle(db_session: Session, recon_ctx: dict):
    ctx = recon_ctx

    wf = PhysicalCountWorkflow(db_session, ctx["org_id"], ctx["store_id"])
    assert wf.session.status == CountSessionStatus.OPEN

    # Add counts
    wf.add_count(ctx["pid1"], sku="P1", bucket=BucketType.SELLABLE, counted_qty=Decimal("100.000"), counted_by="JOHN")
    wf.add_count(ctx["pid2"], sku="P2", bucket=BucketType.SELLABLE, counted_qty=Decimal("50.000"), counted_by="JOHN")

    # Close counting
    wf.close_count()
    assert wf.session.status == CountSessionStatus.COUNTED

    # Review — produces reconciliation reports
    reports = wf.review()
    assert wf.session.status == CountSessionStatus.REVIEWED
    assert len(reports) == 2
    assert all(r.overall_severity == AnomalySeverity.OK for r in reports)

    # Commit
    final_status = wf.commit()
    assert final_status == CountSessionStatus.COMMITTED
    assert wf.session.closed_at is not None


def test_physical_count_workflow_cannot_add_counts_after_close(db_session: Session, recon_ctx: dict):
    ctx = recon_ctx
    wf = PhysicalCountWorkflow(db_session, ctx["org_id"], ctx["store_id"])
    wf.add_count(ctx["pid1"], sku="P1", bucket=BucketType.SELLABLE, counted_qty=Decimal("90.000"))
    wf.close_count()
    with pytest.raises(ValueError, match="Cannot add counts"):
        wf.add_count(ctx["pid2"], sku="P2", bucket=BucketType.SELLABLE, counted_qty=Decimal("50.000"))


def test_physical_count_workflow_detects_discrepancy(db_session: Session, recon_ctx: dict):
    ctx = recon_ctx
    wf = PhysicalCountWorkflow(db_session, ctx["org_id"], ctx["store_id"])
    # Physical count shows 80 vs ledger's 100 (−20 = SHRINK HIGH/CRITICAL)
    wf.add_count(ctx["pid1"], sku="P1", bucket=BucketType.SELLABLE, counted_qty=Decimal("80.000"))
    wf.close_count()
    reports = wf.review()

    assert len(reports) == 1
    r = reports[0]
    assert r.overall_classification == DiscrepancyClass.SHRINK
    assert r.total_gap == Decimal("-20.000")
    assert r.overall_severity in (AnomalySeverity.HIGH, AnomalySeverity.CRITICAL)

