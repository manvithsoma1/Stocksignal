"""
Integration test suite for migration 0002_inventory_ledger_rules.

Rules:
- Operates strictly on TEST_DATABASE_URL.
- Rejects connection to development database.
- Commits transactions to trigger deferred database checks.
- Safely cleans up committed test rows by tracked organization IDs in proper FK order.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# --------------------------------------------------------------------------- #
# Dynamic Loader for Migration 0002 Module                                    #
# --------------------------------------------------------------------------- #
def load_migration_0002():
    migration_path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0002_inventory_ledger_rules.py"
    )
    if not migration_path.is_file():
        raise FileNotFoundError(f"Migration file not found at: {migration_path}")

    spec = importlib.util.spec_from_file_location("migration_0002_module", migration_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for: {migration_path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Database Connection Isolation Guard                                         #
# --------------------------------------------------------------------------- #
_isolated_engine: Engine | None = None
_IsolatedSessionLocal: sessionmaker[Session] | None = None


def get_isolated_engine() -> Engine:
    global _isolated_engine
    if _isolated_engine is not None:
        return _isolated_engine

    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.strip():
        pytest.fail(
            "TEST_DATABASE_URL is not set. Migration 0002 integration tests require a "
            "dedicated test database and must never run against development data.",
            pytrace=False,
        )

    parsed_test = urlparse(test_db_url)
    test_db_name = parsed_test.path.lstrip("/").lower()

    # Strengthened Guard 1: Require an explicitly named disposable test database
    if not (test_db_name.endswith("_test") or test_db_name.startswith("test_")):
        pytest.fail(
            f"SAFETY VIOLATION: TEST_DATABASE_URL database name '{test_db_name}' must explicitly "
            "start with 'test_' or end with '_test' (e.g. stocksignal_test).",
            pytrace=False,
        )

    # Strengthened Guard 2: Reject known development / production database names
    known_dev_dbs = {"stocksignal", "postgres", "production", "development", "dev"}
    if test_db_name in known_dev_dbs:
        pytest.fail(
            f"SAFETY VIOLATION: TEST_DATABASE_URL database name '{test_db_name}' is in prohibited "
            f"non-test databases list: {known_dev_dbs}.",
            pytrace=False,
        )

    # Strengthened Guard 3: Reject match against configured dev database target
    dev_url = settings.database_url
    parsed_dev = urlparse(dev_url)
    dev_target = (parsed_dev.hostname, parsed_dev.port or 5432, parsed_dev.path.lstrip("/").lower())
    test_target = (parsed_test.hostname, parsed_test.port or 5432, test_db_name)

    if dev_target == test_target:
        pytest.fail(
            f"SAFETY VIOLATION: TEST_DATABASE_URL matches development database target: {test_target}. "
            "Aborting test execution to prevent database pollution.",
            pytrace=False,
        )

    engine = create_engine(test_db_url, pool_pre_ping=True)

    # Strengthened Guard 4: Check actual server connection identity via current_database()
    with engine.connect() as conn:
        active_db = conn.execute(text("SELECT current_database()")).scalar()
        if not active_db:
            pytest.fail("SAFETY VIOLATION: Could not retrieve current_database() from test connection.", pytrace=False)
        active_db_lower = str(active_db).lower()
        if not (active_db_lower.endswith("_test") or active_db_lower.startswith("test_")):
            pytest.fail(
                f"SAFETY VIOLATION: Connected database '{active_db}' does not follow test naming convention (*_test / test_*).",
                pytrace=False,
            )
        if active_db_lower in known_dev_dbs:
            pytest.fail(
                f"SAFETY VIOLATION: Connected database '{active_db}' is in prohibited database list.",
                pytrace=False,
            )

    _isolated_engine = engine
    return _isolated_engine


def get_isolated_sessionmaker() -> sessionmaker[Session]:
    global _IsolatedSessionLocal
    if _IsolatedSessionLocal is None:
        engine = get_isolated_engine()
        _IsolatedSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _IsolatedSessionLocal


# --------------------------------------------------------------------------- #
# Pytest Fixtures                                                             #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def verify_test_database_revision():
    """Verify that the test database has migration 0002 applied."""
    sm = get_isolated_sessionmaker()
    with sm() as session:
        try:
            rev = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
        except Exception as err:
            pytest.fail(
                f"Cannot query alembic_version on test database: {err}. "
                "Ensure TEST_DATABASE_URL is initialized and migrated.",
                pytrace=False,
            )

        if rev not in ("0002_ledger_rules", "0003_action_recommendations"):
            pytest.fail(
                f"Test database revision is '{rev}', but '0002_ledger_rules' or later is required. "
                "Execute `alembic upgrade head` on the test database first.",
                pytrace=False,
            )


@pytest.fixture
def db_session(verify_test_database_revision) -> Generator[Session, None, None]:
    """Yield an independent session for each test against the test database."""
    sm = get_isolated_sessionmaker()
    session = sm()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def isolated_context(db_session: Session) -> Generator[dict, None, None]:
    """
    Creates an isolated organization, store, and product for each test.
    Tracks all created organization IDs and deletes them in reverse dependency
    order during teardown.
    """
    created_org_ids: list[uuid.UUID] = []

    def _create_org_bundle(label: str = "T") -> dict:
        org_id = uuid.uuid4()
        org_code = f"TEST_{label}_{uuid.uuid4().hex[:8]}"
        store_id = uuid.uuid4()
        product_id = uuid.uuid4()

        db_session.execute(
            text(
                "INSERT INTO organizations (id, name, code, status, created_at, updated_at) "
                "VALUES (:id, :name, :code, 'ACTIVE', now(), now())"
            ),
            {"id": org_id, "name": f"Test Org {label}", "code": org_code},
        )
        db_session.execute(
            text(
                "INSERT INTO stores (id, organization_id, store_code, name, timezone, status, created_at) "
                "VALUES (:id, :org_id, 'STR01', 'Test Store', 'Asia/Kolkata', 'ACTIVE', now())"
            ),
            {"id": store_id, "org_id": org_id},
        )
        db_session.execute(
            text(
                "INSERT INTO products (id, organization_id, sku, name, unit_of_measure, is_perishable, status, created_at) "
                "VALUES (:id, :org_id, 'SKU-001', 'Test Product', 'unit', false, 'ACTIVE', now())"
            ),
            {"id": product_id, "org_id": org_id},
        )

        db_session.commit()
        created_org_ids.append(org_id)

        return {
            "org_id": org_id,
            "store_id": store_id,
            "product_id": product_id,
        }

    ctx = _create_org_bundle("MAIN")
    ctx["create_secondary_bundle"] = lambda: _create_org_bundle("SEC")

    yield ctx

    # Clean up committed rows in strict foreign-key dependency order:
    # 1. Inventory movement postings.
    # 2. REVERSAL movements (which reference other movements via fk_movements_reversal_source).
    # 3. Remaining inventory movements.
    # 4. Batches.
    # 5. Store products.
    # 6. Products.
    # 7. Stores.
    # 8. Organizations.
    sm = get_isolated_sessionmaker()
    with sm() as cleanup_session:
        try:
            for oid in created_org_ids:
                # 1. Inventory movement postings
                cleanup_session.execute(
                    text("DELETE FROM inventory_movement_postings WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 2. REVERSAL movements (referencing other movements)
                cleanup_session.execute(
                    text(
                        "DELETE FROM inventory_movements "
                        "WHERE organization_id = :oid AND movement_type::text = 'REVERSAL'"
                    ),
                    {"oid": oid},
                )
                # 3. Remaining inventory movements
                cleanup_session.execute(
                    text("DELETE FROM inventory_movements WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 4. Batches
                cleanup_session.execute(
                    text("DELETE FROM batches WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 5. Store products
                cleanup_session.execute(
                    text("DELETE FROM store_products WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 6. Products
                cleanup_session.execute(
                    text("DELETE FROM products WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 7. Stores
                cleanup_session.execute(
                    text("DELETE FROM stores WHERE organization_id = :oid"),
                    {"oid": oid},
                )
                # 8. Organizations
                cleanup_session.execute(
                    text("DELETE FROM organizations WHERE id = :oid"),
                    {"oid": oid},
                )
            cleanup_session.commit()
        except Exception as cleanup_err:
            cleanup_session.rollback()
            raise RuntimeError(f"Teardown cleanup failed for orgs {created_org_ids}: {cleanup_err}") from cleanup_err


# --------------------------------------------------------------------------- #
# 1. Valid RECLASSIFY Tests                                                   #
# --------------------------------------------------------------------------- #
def test_valid_reclassify_sellable_to_quarantined(db_session: Session, isolated_context: dict):
    """Direction 1: SELLABLE -5.000, QUARANTINED +5.000 succeeds at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'QUARANTINED', 5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    db_session.commit()

    count = db_session.execute(
        text("SELECT COUNT(*) FROM inventory_movement_postings WHERE movement_id = :mv_id"),
        {"mv_id": mv_id},
    ).scalar()
    assert count == 2


def test_valid_reclassify_quarantined_to_sellable(db_session: Session, isolated_context: dict):
    """Direction 2: QUARANTINED -5.000, SELLABLE +5.000 succeeds at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'QUARANTINED', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    db_session.commit()

    count = db_session.execute(
        text("SELECT COUNT(*) FROM inventory_movement_postings WHERE movement_id = :mv_id"),
        {"mv_id": mv_id},
    ).scalar()
    assert count == 2


# --------------------------------------------------------------------------- #
# 2. Invalid RECLASSIFY Tests                                                 #
# --------------------------------------------------------------------------- #
def test_invalid_reclassify_missing_posting_rejected_at_commit(db_session: Session, isolated_context: dict):
    """Only 1 posting present must fail trigger check at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    with pytest.raises(DBAPIError) as exc_info:
        db_session.commit()

    db_session.rollback()
    assert "must have exactly 2 postings" in str(exc_info.value)


def test_invalid_reclassify_same_bucket_twice_rejected(db_session: Session, isolated_context: dict):
    """Two postings to the same bucket must be rejected by uq_posting_movement_bucket."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
                "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 5.000, :now)"
            ),
            {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
        )
        db_session.flush()

    db_session.rollback()
    assert "uq_posting_movement_bucket" in str(exc_info.value)


def test_invalid_reclassify_unequal_absolute_quantities_rejected_at_commit(
    db_session: Session, isolated_context: dict
):
    """Postings with unequal absolute quantities must fail trigger check at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'QUARANTINED', 4.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    with pytest.raises(DBAPIError) as exc_info:
        db_session.commit()

    db_session.rollback()
    assert "equal absolute quantities" in str(exc_info.value)


def test_invalid_reclassify_both_postings_positive_rejected_at_commit(
    db_session: Session, isolated_context: dict
):
    """Two positive postings must fail trigger check at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'QUARANTINED', 5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    with pytest.raises(DBAPIError) as exc_info:
        db_session.commit()

    db_session.rollback()
    assert "one positive and one negative posting" in str(exc_info.value)


def test_invalid_reclassify_both_postings_negative_rejected_at_commit(
    db_session: Session, isolated_context: dict
):
    """Two negative postings must fail trigger check at commit."""
    ctx = isolated_context
    mv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'RECLASSIFY', 0, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": mv_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'QUARANTINED', -5.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": mv_id, "now": now},
    )

    with pytest.raises(DBAPIError) as exc_info:
        db_session.commit()

    db_session.rollback()
    assert "one positive and one negative posting" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# 3. REVERSAL Lifecycle & Integrity Tests                                     #
# --------------------------------------------------------------------------- #
def test_valid_reversal_with_exact_inverse_postings_succeeds(db_session: Session, isolated_context: dict):
    """Original SALE of -10 followed by valid REVERSAL of +10 commits cleanly."""
    ctx = isolated_context
    now = datetime.now(timezone.utc)
    orig_id = uuid.uuid4()
    rev_id = uuid.uuid4()

    # Original movement
    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'SALE', -10.000, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": orig_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -10.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": orig_id, "now": now},
    )
    db_session.commit()

    # Reversal movement
    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', 10.000, :now, :now, 'test', :idem, :rev_id, :now)"
        ),
        {
            "id": rev_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
            "rev_id": orig_id,
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 10.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": rev_id, "now": now},
    )
    db_session.commit()

    # Net quantity delta across all postings must now equal 0
    total = db_session.execute(
        text(
            "SELECT COALESCE(SUM(quantity_delta), 0) FROM inventory_movement_postings "
            "WHERE organization_id = :org_id AND bucket_type = 'SELLABLE'"
        ),
        {"org_id": ctx["org_id"]},
    ).scalar()
    assert total == 0


def test_duplicate_reversal_prevented_by_unique_index(db_session: Session, isolated_context: dict):
    """Attempting to reverse the same movement twice must fail uq_movements_reversed_once."""
    ctx = isolated_context
    now = datetime.now(timezone.utc)
    orig_id = uuid.uuid4()
    rev1_id = uuid.uuid4()
    rev2_id = uuid.uuid4()

    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'GRN_RECEIPT', 12.000, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": orig_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 12.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": orig_id, "now": now},
    )
    db_session.commit()

    # First reversal succeeds
    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', -12.000, :now, :now, 'test', :idem, :rev_id, :now)"
        ),
        {
            "id": rev1_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
            "rev_id": orig_id,
        },
    )
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', -12.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": rev1_id, "now": now},
    )
    db_session.commit()

    # Second reversal referencing the same orig_id must fail
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
                "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
                "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', -12.000, :now, :now, 'test', :idem, :rev_id, :now)"
            ),
            {
                "id": rev2_id,
                "org_id": ctx["org_id"],
                "store_id": ctx["store_id"],
                "product_id": ctx["product_id"],
                "now": now,
                "idem": str(uuid.uuid4()),
                "rev_id": orig_id,
            },
        )
        db_session.flush()

    db_session.rollback()
    assert "uq_movements_reversed_once" in str(exc_info.value)


def test_reversal_missing_reference_rejected(db_session: Session, isolated_context: dict):
    """REVERSAL with NULL reversed_movement_id must be rejected by check constraint."""
    ctx = isolated_context
    now = datetime.now(timezone.utc)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
                "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
                "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', 5.000, :now, :now, 'test', :idem, NULL, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "org_id": ctx["org_id"],
                "store_id": ctx["store_id"],
                "product_id": ctx["product_id"],
                "now": now,
                "idem": str(uuid.uuid4()),
            },
        )
        db_session.flush()

    db_session.rollback()
    assert "ck_movement_reversal_reference" in str(exc_info.value)


def test_non_reversal_with_reference_rejected(db_session: Session, isolated_context: dict):
    """Non-REVERSAL (e.g. SALE) with reversed_movement_id must be rejected by check constraint."""
    ctx = isolated_context
    now = datetime.now(timezone.utc)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
                "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
                "VALUES (:id, :org_id, :store_id, :product_id, 'SALE', -5.000, :now, :now, 'test', :idem, :rev_id, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "org_id": ctx["org_id"],
                "store_id": ctx["store_id"],
                "product_id": ctx["product_id"],
                "now": now,
                "idem": str(uuid.uuid4()),
                "rev_id": uuid.uuid4(),
            },
        )
        db_session.flush()

    db_session.rollback()
    assert "ck_movement_reversal_reference" in str(exc_info.value)


def test_reversal_self_reference_rejected(db_session: Session, isolated_context: dict):
    """REVERSAL referencing its own id must be rejected by check constraint."""
    ctx = isolated_context
    now = datetime.now(timezone.utc)
    self_id = uuid.uuid4()

    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
                "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
                "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', 5.000, :now, :now, 'test', :idem, :id, :now)"
            ),
            {
                "id": self_id,
                "org_id": ctx["org_id"],
                "store_id": ctx["store_id"],
                "product_id": ctx["product_id"],
                "now": now,
                "idem": str(uuid.uuid4()),
            },
        )
        db_session.flush()

    db_session.rollback()
    assert "ck_movement_reversal_reference" in str(exc_info.value)


def test_cross_organization_reversal_rejected_by_foreign_key(db_session: Session, isolated_context: dict):
    """Reversing a movement belonging to a different organization must violate fk_movements_reversal_source."""
    ctx = isolated_context
    sec = ctx["create_secondary_bundle"]()
    now = datetime.now(timezone.utc)
    orig_id = uuid.uuid4()
    rev_id = uuid.uuid4()

    # Original movement in primary organization
    db_session.execute(
        text(
            "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
            "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, created_at) "
            "VALUES (:id, :org_id, :store_id, :product_id, 'GRN_RECEIPT', 10.000, :now, :now, 'test', :idem, :now)"
        ),
        {
            "id": orig_id,
            "org_id": ctx["org_id"],
            "store_id": ctx["store_id"],
            "product_id": ctx["product_id"],
            "now": now,
            "idem": str(uuid.uuid4()),
        },
    )
    # Required Fix for Issue 1: Valid SELLABLE posting of +10.000 so the original movement commits cleanly
    db_session.execute(
        text(
            "INSERT INTO inventory_movement_postings (id, organization_id, movement_id, bucket_type, quantity_delta, created_at) "
            "VALUES (:id, :org_id, :mv_id, 'SELLABLE', 10.000, :now)"
        ),
        {"id": uuid.uuid4(), "org_id": ctx["org_id"], "mv_id": orig_id, "now": now},
    )
    db_session.commit()

    # Reversal in secondary organization referencing orig_id must fail composite foreign key check
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO inventory_movements (id, organization_id, store_id, product_id, movement_type, "
                "quantity_delta, occurred_at, recorded_at, source_system, idempotency_key, reversed_movement_id, created_at) "
                "VALUES (:id, :org_id, :store_id, :product_id, 'REVERSAL', -10.000, :now, :now, 'test', :idem, :rev_id, :now)"
            ),
            {
                "id": rev_id,
                "org_id": sec["org_id"],
                "store_id": sec["store_id"],
                "product_id": sec["product_id"],
                "now": now,
                "idem": str(uuid.uuid4()),
                "rev_id": orig_id,
            },
        )
        db_session.flush()

    db_session.rollback()
    assert "fk_movements_reversal_source" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# 4. Downgrade Guard Unit Tests                                               #
# --------------------------------------------------------------------------- #
def test_migration_0002_downgrade_guard_blocks_when_reclassify_records_exist():
    """Downgrade must raise RuntimeError and halt when RECLASSIFY records are present."""
    mig = load_migration_0002()
    assert hasattr(mig, "upgrade"), "Migration 0002 missing upgrade function"
    assert hasattr(mig, "downgrade"), "Migration 0002 missing downgrade function"

    class MockConnection:
        def execute(self, statement):
            stmt_text = str(statement)
            count = 3 if "'RECLASSIFY'" in stmt_text else 0

            class MockScalarResult:
                def scalar(self):
                    return count

            return MockScalarResult()

    class MockOp:
        @staticmethod
        def get_bind():
            return MockConnection()

    original_op = getattr(mig, "op", None)
    try:
        mig.op = MockOp
        with pytest.raises(RuntimeError) as exc_info:
            mig.downgrade()
        msg = str(exc_info.value)
        assert "Cannot downgrade migration 0002: 3 RECLASSIFY record(s) and 0 REVERSAL record(s)" in msg
        assert "Downgrade would lose inventory classification or reversal history" in msg
    finally:
        mig.op = original_op


def test_migration_0002_downgrade_guard_blocks_when_reversal_records_exist():
    """Downgrade must raise RuntimeError and halt when REVERSAL records are present (even with 0 RECLASSIFY)."""
    mig = load_migration_0002()
    assert hasattr(mig, "upgrade"), "Migration 0002 missing upgrade function"
    assert hasattr(mig, "downgrade"), "Migration 0002 missing downgrade function"

    class MockConnection:
        def execute(self, statement):
            stmt_text = str(statement)
            count = 2 if "'REVERSAL'" in stmt_text else 0

            class MockScalarResult:
                def scalar(self):
                    return count

            return MockScalarResult()

    class MockOp:
        @staticmethod
        def get_bind():
            return MockConnection()

    original_op = getattr(mig, "op", None)
    try:
        mig.op = MockOp
        with pytest.raises(RuntimeError) as exc_info:
            mig.downgrade()
        msg = str(exc_info.value)
        assert "Cannot downgrade migration 0002: 0 RECLASSIFY record(s) and 2 REVERSAL record(s)" in msg
        assert "Downgrade would lose inventory classification or reversal history" in msg
    finally:
        mig.op = original_op
