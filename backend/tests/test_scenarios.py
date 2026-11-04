"""
Tests for Phase 3 Deterministic Evaluation Scenarios.

Each scenario is run against an isolated TEST_DATABASE_URL and verified for:
  - Correct ledger state after operations.
  - Correct discrepancy detection (has_discrepancy flag and deltas).
  - Idempotency and reclassification correctness.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Generator
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.core import Organization, Product, Store, StoreProduct
from app.services.scenarios import (
    scenario_correct_stock,
    scenario_duplicate_scan,
    scenario_receipt_discrepancy,
    scenario_return_misclassify,
    scenario_unrecorded_damage,
    run_all_scenarios,
)


# --------------------------------------------------------------------------- #
# Test DB Guard                                                               #
# --------------------------------------------------------------------------- #
_engine: Engine | None = None
_sm: sessionmaker[Session] | None = None


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
        pytest.fail(f"SAFETY: TEST_DATABASE_URL db '{db_name}' must end with _test", pytrace=False)
    dev = urlparse(settings.database_url)
    if (dev.hostname, dev.port or 5432, dev.path.lstrip("/").lower()) == (
        parsed.hostname, parsed.port or 5432, db_name
    ):
        pytest.fail("SAFETY: TEST_DATABASE_URL matches dev database", pytrace=False)
    _engine = create_engine(test_url, pool_pre_ping=True)
    return _engine


def get_sm() -> sessionmaker[Session]:
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
def scenario_ctx(db_session: Session) -> Generator[dict, None, None]:
    """Provides one org, one store, and 5 distinct products for scenario tests."""
    org_id = uuid.uuid4()
    store_id = uuid.uuid4()
    suffix = str(uuid.uuid4())[:8]

    org = Organization(id=org_id, name=f"ScenOrg {suffix}", code=f"SORG_{suffix}")
    store = Store(id=store_id, organization_id=org_id, store_code=f"SSTR_{suffix}", name="Scenario Store")
    db_session.add_all([org, store])
    db_session.flush()

    scenario_names = [
        "CORRECT_STOCK", "UNRECORDED_DAMAGE", "DUPLICATE_SCAN",
        "RECEIPT_DISCREPANCY", "RETURN_MISCLASSIFY",
    ]
    products: dict[str, uuid.UUID] = {}
    for name in scenario_names:
        pid = uuid.uuid4()
        p = Product(
            id=pid,
            organization_id=org_id,
            sku=f"SKU_{name[:4]}_{suffix}",
            name=f"Product for {name}",
            category="Dairy",
            unit_of_measure="unit",
            is_perishable=True,
        )
        sp = StoreProduct(
            organization_id=org_id,
            store_id=store_id,
            product_id=pid,
            reorder_level=Decimal("5.000"),
        )
        db_session.add_all([p, sp])
        products[name] = pid

    db_session.commit()

    ctx = {"org_id": org_id, "store_id": store_id, "product_ids": products}

    try:
        yield ctx
    finally:
        try:
            db_session.rollback()
            db_session.execute(
                text("DELETE FROM inventory_movement_postings WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM inventory_movements WHERE organization_id = :oid AND movement_type = 'REVERSAL'"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM inventory_movements WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM store_products WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM products WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM stores WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            db_session.execute(
                text("DELETE FROM organizations WHERE id = :oid"),
                {"oid": org_id},
            )
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #

def test_scenario_correct_stock_no_discrepancy(db_session: Session, scenario_ctx: dict):
    result = scenario_correct_stock(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"]["CORRECT_STOCK"],
        receipt_qty=Decimal("100.000"),
        sale_qty=Decimal("30.000"),
    )
    assert result.scenario_name == "CORRECT_STOCK"
    assert not result.discrepancy.has_discrepancy
    assert result.ledger_state.sellable == Decimal("70.000")
    assert result.ledger_state.physical == Decimal("70.000")
    assert result.discrepancy.sellable_delta == Decimal("0.000")


def test_scenario_unrecorded_damage_produces_discrepancy(db_session: Session, scenario_ctx: dict):
    result = scenario_unrecorded_damage(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"]["UNRECORDED_DAMAGE"],
        receipt_qty=Decimal("100.000"),
        damage_qty=Decimal("8.000"),
    )
    assert result.scenario_name == "UNRECORDED_DAMAGE"
    assert result.discrepancy.has_discrepancy
    # Ledger shows 100, truth is 92 → truth - ledger = -8 (ledger overstates by 8)
    assert result.discrepancy.sellable_delta == Decimal("-8.000")
    assert result.ledger_state.sellable == Decimal("100.000")
    assert result.ground_truth.sellable == Decimal("92.000")


def test_scenario_duplicate_scan_idempotency_prevents_double_count(db_session: Session, scenario_ctx: dict):
    result = scenario_duplicate_scan(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"]["DUPLICATE_SCAN"],
        receipt_qty=Decimal("50.000"),
    )
    assert result.scenario_name == "DUPLICATE_SCAN"
    # After idempotency, only one movement recorded, stock should be 50 (not 100)
    assert not result.discrepancy.has_discrepancy
    assert result.ledger_state.sellable == Decimal("50.000")
    assert result.discrepancy.sellable_delta == Decimal("0.000")
    assert len(result.movements_recorded) == 1


def test_scenario_receipt_discrepancy_ledger_reflects_actual_received(db_session: Session, scenario_ctx: dict):
    result = scenario_receipt_discrepancy(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"]["RECEIPT_DISCREPANCY"],
        invoice_qty=Decimal("100.000"),
        actual_received_qty=Decimal("90.000"),
    )
    assert result.scenario_name == "RECEIPT_DISCREPANCY"
    # Ledger correctly records what was physically received
    assert not result.discrepancy.has_discrepancy
    assert result.ledger_state.sellable == Decimal("90.000")
    assert result.metadata["supplier_shortage"] == "10.000"


def test_scenario_return_misclassify_detects_bucket_error_and_fixes(db_session: Session, scenario_ctx: dict):
    result = scenario_return_misclassify(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"]["RETURN_MISCLASSIFY"],
        receipt_qty=Decimal("100.000"),
        return_qty=Decimal("5.000"),
    )
    assert result.scenario_name == "RETURN_MISCLASSIFY"
    # The discrepancy is detected at the point of the error (before fix)
    assert result.discrepancy.has_discrepancy
    assert result.discrepancy.scenario_type == "MISCLASSIFICATION"
    # Sellable understated by 5 in the errored state
    assert result.discrepancy.sellable_delta == Decimal("5.000")
    # Correction movement ID is present in metadata
    assert "correction_movement_id" in result.metadata
    assert result.metadata["post_fix_sellable"] == "105.000"


def test_run_all_scenarios_produces_five_results(db_session: Session, scenario_ctx: dict):
    results = run_all_scenarios(
        db_session,
        scenario_ctx["org_id"],
        scenario_ctx["store_id"],
        scenario_ctx["product_ids"],
    )
    assert len(results) == 5

    names = [r.scenario_name for r in results]
    assert "CORRECT_STOCK" in names
    assert "UNRECORDED_DAMAGE" in names
    assert "DUPLICATE_SCAN" in names
    assert "RECEIPT_DISCREPANCY" in names
    assert "RETURN_MISCLASSIFY" in names

    discrepancies_found = [r for r in results if r.discrepancy.has_discrepancy]
    # UNRECORDED_DAMAGE and RETURN_MISCLASSIFY should flag discrepancies
    assert len(discrepancies_found) == 2

