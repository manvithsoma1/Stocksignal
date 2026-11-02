"""
Comprehensive Integration Tests for StockSignal Inventory Ledger Service & API.

Verifies:
1. Append-only movement recording (GRN receipt, Sale, Returns, Damage).
2. Balance calculations per bucket (SELLABLE, QUARANTINED, etc.) and batch.
3. Non-negative sellable stock enforcement (InsufficientStockError).
4. Idempotent movement recording.
5. Bucket reclassifications (SELLABLE <-> QUARANTINED) adhering to DB constraints.
6. Movement reversals with exact inverse postings and single-reversal enforcement.
7. Atomic store-to-store transfers.
8. Full FastAPI HTTP endpoint workflows via TestClient.

Executes strictly against isolated TEST_DATABASE_URL with dependency-safe cleanup.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Generator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.inventory import get_db
from app.core.config import settings
from app.main import app
from app.models.core import (
    Batch,
    BucketType,
    InventoryMovement,
    InventoryMovementPosting,
    MovementType,
    Organization,
    Product,
    Store,
    StoreProduct,
)
from app.schemas.inventory import (
    MovementCreate,
    PostingSpec,
    ReclassifyCreate,
    ReversalCreate,
    TransferCreate,
)
from app.services import ledger


# --------------------------------------------------------------------------- #
# Isolated Test Database Guard                                                #
# --------------------------------------------------------------------------- #
_isolated_engine: Engine | None = None
_IsolatedSessionLocal: sessionmaker[Session] | None = None


def get_test_engine() -> Engine:
    global _isolated_engine
    if _isolated_engine is not None:
        return _isolated_engine

    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.strip():
        pytest.fail(
            "TEST_DATABASE_URL is not set. Ledger integration tests must run against "
            "an isolated test database.",
            pytrace=False,
        )

    parsed_test = urlparse(test_db_url)
    test_db_name = parsed_test.path.lstrip("/").lower()

    if not (test_db_name.endswith("_test") or test_db_name.startswith("test_")):
        pytest.fail(
            f"SAFETY VIOLATION: TEST_DATABASE_URL database name '{test_db_name}' must start with 'test_' or end with '_test'.",
            pytrace=False,
        )

    dev_url = settings.database_url
    parsed_dev = urlparse(dev_url)
    dev_target = (parsed_dev.hostname, parsed_dev.port or 5432, parsed_dev.path.lstrip("/").lower())
    test_target = (parsed_test.hostname, parsed_test.port or 5432, test_db_name)

    if dev_target == test_target:
        pytest.fail("SAFETY VIOLATION: TEST_DATABASE_URL matches development database target.", pytrace=False)

    engine = create_engine(test_db_url, pool_pre_ping=True)
    _isolated_engine = engine
    return _isolated_engine


def get_test_sessionmaker() -> sessionmaker[Session]:
    global _IsolatedSessionLocal
    if _IsolatedSessionLocal is None:
        engine = get_test_engine()
        _IsolatedSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _IsolatedSessionLocal


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    sm = get_test_sessionmaker()
    session = sm()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def ledger_context(db_session: Session) -> Generator[dict, None, None]:
    """
    Creates an organization, two stores, a product, store-products, and a batch.
    Cleans up all created records in safe dependency order at teardown.
    """
    org_id = uuid.uuid4()
    store1_id = uuid.uuid4()
    store2_id = uuid.uuid4()
    product_id = uuid.uuid4()
    batch_id = uuid.uuid4()

    suffix = str(uuid.uuid4())[:8]

    # 1. Organization
    org = Organization(id=org_id, name=f"Test Org {suffix}", code=f"ORG_{suffix}")
    db_session.add(org)
    db_session.flush()

    # 2. Stores & Product
    store1 = Store(id=store1_id, organization_id=org_id, store_code=f"S1_{suffix}", name="Store 1 Indiranagar")
    store2 = Store(id=store2_id, organization_id=org_id, store_code=f"S2_{suffix}", name="Store 2 Koramangala")
    product = Product(
        id=product_id,
        organization_id=org_id,
        sku=f"SKU_{suffix}",
        name=f"Organic Milk 1L {suffix}",
        category="Dairy",
        unit_of_measure="unit",
        is_perishable=True,
    )
    db_session.add_all([store1, store2, product])
    db_session.flush()

    # 3. Store Products & Batch
    sp1 = StoreProduct(
        organization_id=org_id,
        store_id=store1_id,
        product_id=product_id,
        reorder_level=Decimal("10.000"),
    )
    sp2 = StoreProduct(
        organization_id=org_id,
        store_id=store2_id,
        product_id=product_id,
        reorder_level=Decimal("10.000"),
    )
    today = date.today()
    batch = Batch(
        id=batch_id,
        organization_id=org_id,
        store_id=store1_id,
        product_id=product_id,
        batch_code=f"BATCH_{suffix}",
        received_date=today - timedelta(days=2),
        expiry_date=today + timedelta(days=5),
    )
    db_session.add_all([sp1, sp2, batch])
    db_session.commit()

    ctx = {
        "org_id": org_id,
        "store1_id": store1_id,
        "store2_id": store2_id,
        "product_id": product_id,
        "batch_id": batch_id,
    }

    try:
        yield ctx
    finally:
        # Dependency-safe cleanup
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
                text("DELETE FROM batches WHERE organization_id = :oid"),
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
# Tests: Core Movement Operations                                             #
# --------------------------------------------------------------------------- #

def test_record_movement_grn_receipt_and_stock_balance(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    payload = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("100.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[
            PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("100.000"))
        ],
    )
    movement = ledger.record_movement(db_session, payload)
    db_session.commit()

    assert movement.id is not None
    assert movement.movement_type == MovementType.GRN_RECEIPT
    assert movement.quantity_delta == Decimal("100.000")

    balance = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert balance.sellable == Decimal("100.000")
    assert balance.physical == Decimal("100.000")
    assert balance.quarantined == Decimal("0.000")


def test_sale_decreases_stock_balance(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    # 1. Receipt 50
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("50.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("50.000"))],
    )
    ledger.record_movement(db_session, receipt)
    db_session.commit()

    # 2. Sale 15
    sale = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.SALE,
        quantity_delta=Decimal("-15.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="POS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("-15.000"))],
    )
    ledger.record_movement(db_session, sale)
    db_session.commit()

    balance = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert balance.sellable == Decimal("35.000")
    assert balance.physical == Decimal("35.000")


def test_negative_sellable_stock_rejected_by_service(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    # Attempt sale with 0 balance
    sale = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.SALE,
        quantity_delta=Decimal("-10.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="POS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("-10.000"))],
    )
    with pytest.raises(ledger.InsufficientStockError) as exc_info:
        ledger.record_movement(db_session, sale, allow_negative_stock=False)

    assert "Insufficient SELLABLE stock" in str(exc_info.value)


def test_idempotent_replay_returns_same_movement(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    idemp_key = f"IDEMP_{uuid.uuid4()}"
    payload = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("25.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=idemp_key,
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("25.000"))],
    )

    mv1 = ledger.record_movement(db_session, payload)
    db_session.commit()

    mv2 = ledger.record_movement(db_session, payload)
    assert mv1.id == mv2.id
    assert mv2.quantity_delta == Decimal("25.000")

    # Verify no double balance
    balance = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert balance.sellable == Decimal("25.000")


# --------------------------------------------------------------------------- #
# Tests: Reclassification                                                     #
# --------------------------------------------------------------------------- #

def test_record_reclassify_sellable_to_quarantined(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    # Seed 40 SELLABLE
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("40.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("40.000"))],
    )
    ledger.record_movement(db_session, receipt)
    db_session.commit()

    # Reclassify 12 units to QUARANTINED
    reclassify_req = ReclassifyCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        from_bucket=BucketType.SELLABLE,
        to_bucket=BucketType.QUARANTINED,
        quantity=Decimal("12.000"),
        idempotency_key=str(uuid.uuid4()),
        source_system="QA_AUDIT",
        notes="Suspect batch temperature excursion",
    )
    reclassify_mv = ledger.record_reclassify(db_session, reclassify_req)
    db_session.commit()

    assert reclassify_mv.movement_type == MovementType.RECLASSIFY
    assert reclassify_mv.quantity_delta == Decimal("0.000")
    assert len(reclassify_mv.postings) == 2

    balance = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert balance.sellable == Decimal("28.000")
    assert balance.quarantined == Decimal("12.000")
    assert balance.physical == Decimal("40.000")  # Total physical stock unchanged


# --------------------------------------------------------------------------- #
# Tests: Movement Reversal                                                    #
# --------------------------------------------------------------------------- #

def test_record_reversal_creates_exact_inverse_and_restores_balance(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    # 1. Receipt 50
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("50.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("50.000"))],
    )
    ledger.record_movement(db_session, receipt)
    db_session.commit()

    # 2. Sale 10
    sale = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        movement_type=MovementType.SALE,
        quantity_delta=Decimal("-10.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="POS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("-10.000"))],
    )
    sale_mv = ledger.record_movement(db_session, sale)
    db_session.commit()

    bal1 = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert bal1.sellable == Decimal("40.000")

    # 3. Reverse the sale (e.g. cashier void)
    reversal_req = ReversalCreate(
        idempotency_key=str(uuid.uuid4()),
        source_system="POS_VOID",
        notes="Cashier scanning correction",
    )
    reversal_mv = ledger.record_reversal(db_session, sale_mv.id, reversal_req, ctx["org_id"])
    db_session.commit()

    assert reversal_mv.movement_type == MovementType.REVERSAL
    assert reversal_mv.quantity_delta == Decimal("10.000")
    assert reversal_mv.reversed_movement_id == sale_mv.id
    assert len(reversal_mv.postings) == 1
    assert reversal_mv.postings[0].quantity_delta == Decimal("10.000")

    bal2 = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert bal2.sellable == Decimal("50.000")


def test_reversing_already_reversed_movement_rejected(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("20.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("20.000"))],
    )
    rc_mv = ledger.record_movement(db_session, receipt)
    db_session.commit()

    rev1 = ReversalCreate(
        idempotency_key=str(uuid.uuid4()),
        source_system="WMS_REV",
    )
    ledger.record_reversal(db_session, rc_mv.id, rev1, ctx["org_id"])
    db_session.commit()

    # Second reversal of the same movement
    rev2 = ReversalCreate(
        idempotency_key=str(uuid.uuid4()),
        source_system="WMS_REV_2",
    )
    with pytest.raises(ledger.MovementAlreadyReversedError):
        ledger.record_reversal(db_session, rc_mv.id, rev2, ctx["org_id"])


# --------------------------------------------------------------------------- #
# Tests: Store Transfers                                                      #
# --------------------------------------------------------------------------- #

def test_record_transfer_between_stores(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    # Store 1 gets 100 units
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("100.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("100.000"))],
    )
    ledger.record_movement(db_session, receipt)
    db_session.commit()

    # Transfer 30 units from Store 1 to Store 2
    transfer_req = TransferCreate(
        organization_id=ctx["org_id"],
        from_store_id=ctx["store1_id"],
        to_store_id=ctx["store2_id"],
        product_id=ctx["product_id"],
        quantity=Decimal("30.000"),
        idempotency_key=f"TRANS_{uuid.uuid4()}",
        source_system="TRANSFER_SERVICE",
    )
    outbound, inbound = ledger.record_transfer(db_session, transfer_req)
    db_session.commit()

    assert outbound.movement_type == MovementType.TRANSFER_OUT
    assert outbound.quantity_delta == Decimal("-30.000")
    assert inbound.movement_type == MovementType.TRANSFER_IN
    assert inbound.quantity_delta == Decimal("30.000")

    bal_s1 = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    bal_s2 = ledger.get_stock_balance(db_session, ctx["org_id"], ctx["store2_id"], ctx["product_id"])
    assert bal_s1.sellable == Decimal("70.000")
    assert bal_s2.sellable == Decimal("30.000")


# --------------------------------------------------------------------------- #
# Tests: Batch Balances                                                       #
# --------------------------------------------------------------------------- #

def test_batch_balance_tracking(db_session: Session, ledger_context: dict):
    ctx = ledger_context
    receipt = MovementCreate(
        organization_id=ctx["org_id"],
        store_id=ctx["store1_id"],
        product_id=ctx["product_id"],
        batch_id=ctx["batch_id"],
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("60.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="WMS",
        idempotency_key=str(uuid.uuid4()),
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=Decimal("60.000"))],
    )
    ledger.record_movement(db_session, receipt)
    db_session.commit()

    batch_resp = ledger.get_batch_balances(db_session, ctx["org_id"], ctx["store1_id"], ctx["product_id"])
    assert len(batch_resp.batches) == 1
    assert batch_resp.batches[0].batch_id == ctx["batch_id"]
    assert batch_resp.batches[0].sellable == Decimal("60.000")
    assert batch_resp.batches[0].total == Decimal("60.000")


# --------------------------------------------------------------------------- #
# Tests: HTTP API Integration via TestClient                                 #
# --------------------------------------------------------------------------- #

def test_api_movement_and_balance_endpoints(db_session: Session, ledger_context: dict):
    ctx = ledger_context

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        # 1. POST /api/v1/inventory/movements (GRN)
        mv_payload = {
            "organization_id": str(ctx["org_id"]),
            "store_id": str(ctx["store1_id"]),
            "product_id": str(ctx["product_id"]),
            "batch_id": str(ctx["batch_id"]),
            "movement_type": "GRN_RECEIPT",
            "quantity_delta": "50.000",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "source_system": "API_TEST",
            "idempotency_key": str(uuid.uuid4()),
            "postings": [{"bucket_type": "SELLABLE", "quantity_delta": "50.000"}],
        }
        res = client.post("/api/v1/inventory/movements", json=mv_payload)
        assert res.status_code == 201, res.text
        data = res.json()
        mv_id = data["id"]
        assert data["movement_type"] == "GRN_RECEIPT"

        # 2. GET /api/v1/inventory/balance
        res_bal = client.get(
            "/api/v1/inventory/balance",
            params={
                "organization_id": str(ctx["org_id"]),
                "store_id": str(ctx["store1_id"]),
                "product_id": str(ctx["product_id"]),
            },
        )
        assert res_bal.status_code == 200, res_bal.text
        bal_data = res_bal.json()
        assert Decimal(bal_data["sellable"]) == Decimal("50.000")

        # 3. POST /api/v1/inventory/reclassify
        reclass_payload = {
            "organization_id": str(ctx["org_id"]),
            "store_id": str(ctx["store1_id"]),
            "product_id": str(ctx["product_id"]),
            "batch_id": str(ctx["batch_id"]),
            "from_bucket": "SELLABLE",
            "to_bucket": "QUARANTINED",
            "quantity": "10.000",
            "idempotency_key": str(uuid.uuid4()),
            "source_system": "API_TEST",
        }
        res_rc = client.post("/api/v1/inventory/reclassify", json=reclass_payload)
        assert res_rc.status_code == 201, res_rc.text

        # Verify updated balance
        res_bal2 = client.get(
            "/api/v1/inventory/balance",
            params={
                "organization_id": str(ctx["org_id"]),
                "store_id": str(ctx["store1_id"]),
                "product_id": str(ctx["product_id"]),
            },
        )
        bal_data2 = res_bal2.json()
        assert Decimal(bal_data2["sellable"]) == Decimal("40.000")
        assert Decimal(bal_data2["quarantined"]) == Decimal("10.000")

        # 4. POST /api/v1/inventory/movements/{id}/reverse
        res_rev = client.post(
            f"/api/v1/inventory/movements/{mv_id}/reverse",
            params={"organization_id": str(ctx["org_id"])},
            json={
                "idempotency_key": str(uuid.uuid4()),
                "source_system": "API_TEST",
            },
        )
        assert res_rev.status_code == 201, res_rev.text
        rev_data = res_rev.json()
        assert rev_data["movement_type"] == "REVERSAL"
        assert rev_data["reversed_movement_id"] == mv_id

    finally:
        app.dependency_overrides.clear()
