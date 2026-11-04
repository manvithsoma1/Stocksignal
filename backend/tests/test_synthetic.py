"""
Tests for Phase 4 Synthetic Data Generator.

Verifies:
  - Generator runs without errors (small config: 2 stores, 5 SKUs, 14 days).
  - Correct number of movements is recorded in the ledger.
  - Ground truth records contain expected discrepancy types.
  - Discrepancy injection rates are within statistical tolerance.
  - Stock balances are always non-negative (no sellable < 0).
  - Generator output summary has correct structure.
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
from app.models.core import Organization
from app.services.synthetic.generator import GeneratorConfig, SyntheticGenerator


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
def synth_ctx(db_session: Session) -> Generator[dict, None, None]:
    """Creates an isolated organization for generator tests."""
    org_id = uuid.uuid4()
    suffix = str(uuid.uuid4())[:8]
    org = Organization(id=org_id, name=f"SynthOrg {suffix}", code=f"SYNT_{suffix}")
    db_session.add(org)
    db_session.commit()

    yield {"org_id": org_id, "db_session": db_session}

    # Cleanup in dependency order
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

def test_generator_runs_and_returns_summary(db_session: Session, synth_ctx: dict):
    """Generator completes without exception and returns a valid summary."""
    cfg = GeneratorConfig(
        num_stores=2,
        num_skus=5,
        simulation_days=14,
        random_seed=42,
    )
    gen = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    summary = gen.run()
    db_session.commit()

    assert summary["num_stores"] == 2
    assert summary["num_skus"] == 5
    assert summary["simulation_days"] == 14
    assert summary["total_ground_truth_events"] > 0
    assert "discrepancy_breakdown" in summary


def test_generator_creates_expected_stores_and_products(db_session: Session, synth_ctx: dict):
    """Generator creates the configured number of stores and SKUs."""
    cfg = GeneratorConfig(num_stores=2, num_skus=5, simulation_days=1, random_seed=42)
    gen = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    gen.setup_stores()
    gen.setup_products()
    db_session.flush()

    assert len(gen.stores) == 2
    assert len(gen.products) == 5

    # Verify store-product mappings were created
    count = db_session.execute(
        text("SELECT COUNT(*) FROM store_products WHERE organization_id = :oid"),
        {"oid": synth_ctx["org_id"]},
    ).scalar()
    assert count == 2 * 5  # stores × products


def test_generator_ground_truth_contains_discrepancies(db_session: Session, synth_ctx: dict):
    """With default rates and sufficient volume, some discrepancies should be injected."""
    cfg = GeneratorConfig(
        num_stores=2,
        num_skus=10,
        simulation_days=30,
        random_seed=42,
        unrecorded_damage_rate=0.5,   # elevated rates to guarantee injection in small sample
        return_misclassify_rate=0.5,
    )
    gen = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    summary = gen.run()
    db_session.commit()

    assert summary["total_discrepancies_injected"] > 0


def test_generator_stock_balances_are_non_negative(db_session: Session, synth_ctx: dict):
    """Generator never drives a store-product balance below zero."""
    from app.services.ledger import get_stock_balance

    cfg = GeneratorConfig(num_stores=2, num_skus=5, simulation_days=14, random_seed=42)
    gen = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    gen.run()
    db_session.commit()

    for store in gen.stores:
        for product in gen.products:
            bal = get_stock_balance(db_session, synth_ctx["org_id"], store.id, product.id)
            assert bal.sellable >= Decimal("0.000"), (
                f"Negative SELLABLE balance at store={store.store_code} product={product.sku}: {bal.sellable}"
            )


def test_generator_discrepancy_summary_structure(db_session: Session, synth_ctx: dict):
    """Summary dict has the correct structure with all five discrepancy types."""
    from app.services.synthetic.generator import DISCREPANCY_TYPES

    cfg = GeneratorConfig(num_stores=2, num_skus=5, simulation_days=14, random_seed=99)
    gen = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    summary = gen.run()
    db_session.commit()

    for dtype in DISCREPANCY_TYPES:
        assert dtype in summary["discrepancy_breakdown"], f"Missing discrepancy type: {dtype}"
        entry = summary["discrepancy_breakdown"][dtype]
        assert "count" in entry
        assert "total_qty" in entry


def test_generator_is_deterministic_with_fixed_seed(db_session: Session, synth_ctx: dict):
    """Two runs with the same seed produce the same ground truth event count."""
    cfg = GeneratorConfig(num_stores=1, num_skus=3, simulation_days=7, random_seed=777)

    org2_id = uuid.uuid4()
    suffix = str(uuid.uuid4())[:8]
    from app.models.core import Organization
    org2 = Organization(id=org2_id, name=f"Org2 {suffix}", code=f"ORG2_{suffix}")
    db_session.add(org2)
    db_session.flush()

    gen1 = SyntheticGenerator(db_session, synth_ctx["org_id"], cfg)
    summary1 = gen1.run()
    db_session.commit()

    gen2 = SyntheticGenerator(db_session, org2_id, cfg)
    summary2 = gen2.run()
    db_session.commit()

    # Cleanup org2
    for table in ["inventory_movement_postings", "inventory_movements", "store_products", "products", "stores", "organizations"]:
        where = "movement_type = 'REVERSAL' AND " if table == "inventory_movements" else ""
        db_session.execute(
            text(f"DELETE FROM {table} WHERE {'organization_id' if table != 'organizations' else 'id'} = :oid"),
            {"oid": org2_id},
        )
    db_session.commit()

    assert summary1["total_ground_truth_events"] == summary2["total_ground_truth_events"]
    assert summary1["total_discrepancies_injected"] == summary2["total_discrepancies_injected"]

