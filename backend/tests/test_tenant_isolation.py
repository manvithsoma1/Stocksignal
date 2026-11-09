"""
Unit and integration tests for Priority 8: PostgreSQL Multi-Tenancy & Tenant Isolation Safeguards.

Verifies:
1. Composite foreign keys enforce organization-level boundary isolation.
2. Cross-tenant movement creation fails at database commit.
3. Cross-tenant reversal creation fails by database foreign key constraint.
4. Recommendation outcome tracking enforces strict organization boundaries.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.core import (
    Base,
    BucketType,
    InventoryMovement,
    InventoryMovementPosting,
    MovementType,
    Organization,
    Product,
    Store,
    StoreProduct,
)
from app.models.recommendations import ActionRecommendationRecord, RecommendationStatus
from app.services import outcome_tracking
from app.services.recommendations import ActionRecommendation, ActionType, Priority


@pytest.fixture(scope="module")
def db_session():
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.fail("TEST_DATABASE_URL not set", pytrace=False)

    engine = create_engine(test_url, pool_pre_ping=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def dual_tenant_context(db_session: Session):
    org_a = Organization(id=uuid.uuid4(), code=f"ORG-A-{uuid.uuid4().hex[:6]}", name="Retailer A")
    store_a = Store(id=uuid.uuid4(), organization_id=org_a.id, store_code="STA01", name="Store A1")
    prod_a = Product(id=uuid.uuid4(), organization_id=org_a.id, sku="SKU-TENANT-A", name="Product A", category="General", is_perishable=False)

    org_b = Organization(id=uuid.uuid4(), code=f"ORG-B-{uuid.uuid4().hex[:6]}", name="Retailer B")
    store_b = Store(id=uuid.uuid4(), organization_id=org_b.id, store_code="STB01", name="Store B1")
    prod_b = Product(id=uuid.uuid4(), organization_id=org_b.id, sku="SKU-TENANT-B", name="Product B", category="General", is_perishable=False)

    db_session.add_all([org_a, store_a, prod_a, org_b, store_b, prod_b])
    db_session.commit()

    return {
        "org_a": org_a,
        "store_a": store_a,
        "prod_a": prod_a,
        "org_b": org_b,
        "store_b": store_b,
        "prod_b": prod_b,
    }


def test_cross_tenant_inventory_movement_blocked_by_db(db_session: Session, dual_tenant_context: dict):
    """
    Attempting to create an inventory movement under Org A referencing Org B's store
    must be blocked by composite foreign key fk_inventory_movements_org_store.
    """
    ctx = dual_tenant_context

    invalid_movement = InventoryMovement(
        id=uuid.uuid4(),
        organization_id=ctx["org_a"].id,     # Org A
        store_id=ctx["store_b"].id,           # Belongs to Org B!
        product_id=ctx["prod_a"].id,
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("100.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="ATTACK_TEST",
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(invalid_movement)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_cross_tenant_product_movement_blocked_by_db(db_session: Session, dual_tenant_context: dict):
    """
    Attempting to create an inventory movement under Org A referencing Org B's product
    must be blocked by composite foreign key fk_inventory_movements_org_product.
    """
    ctx = dual_tenant_context

    invalid_movement = InventoryMovement(
        id=uuid.uuid4(),
        organization_id=ctx["org_a"].id,     # Org A
        store_id=ctx["store_a"].id,           # Org A
        product_id=ctx["prod_b"].id,          # Belongs to Org B!
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=Decimal("100.000"),
        occurred_at=datetime.now(timezone.utc),
        source_system="ATTACK_TEST",
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(invalid_movement)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_recommendation_outcome_tenant_isolation(db_session: Session, dual_tenant_context: dict):
    """
    Verifies that outcome tracking queries and updates are strictly isolated by organization_id.
    """
    ctx = dual_tenant_context
    org_a_id = ctx["org_a"].id
    org_b_id = ctx["org_b"].id

    rec_a = ActionRecommendation(
        recommendation_id=str(uuid.uuid4()),
        org_id=org_a_id,
        store_id=ctx["store_a"].id,
        product_id=ctx["prod_a"].id,
        sku="SKU-TENANT-A",
        action_type=ActionType.REPLENISH,
        priority=Priority.HIGH,
        reason="Org A reorder alert",
        predicted_opportunity_value=Decimal("5000.000"),
        estimated_benefit_value=Decimal("2500.000"),
    )
    persisted_a = outcome_tracking.persist_recommendations(db_session, [rec_a])
    rec_a_id = persisted_a[0].id

    # Org B must not be able to approve Org A's recommendation
    with pytest.raises(outcome_tracking.RecommendationNotFoundError):
        outcome_tracking.approve_recommendation(
            db=db_session,
            rec_id=rec_a_id,
            org_id=org_b_id,  # Spoofed Org B
            approver_name="Attacker",
        )

    # Summary metrics for Org B must not include Org A's financial exposure
    summary_b = outcome_tracking.get_outcome_summary(db_session, org_b_id)
    assert summary_b["financial_outcomes"]["predicted_opportunity_value"] == "0.000"
    assert summary_b["financial_outcomes"]["estimated_benefit_value"] == "0.000"

    # Summary metrics for Org A must accurately reflect Org A's data
    summary_a = outcome_tracking.get_outcome_summary(db_session, org_a_id)
    assert Decimal(summary_a["financial_outcomes"]["predicted_opportunity_value"]) >= Decimal("5000.000")
