"""
Unit and integration tests for Priority 7: Outcome Tracking Service and Lifecycle API.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.core import Base, Organization, Product, Store, StoreProduct
from app.models.recommendations import ActionRecommendationRecord, RecommendationStatus
from app.services.outcome_tracking import (
    InvalidLifecycleTransitionError,
    RecommendationNotFoundError,
    approve_recommendation,
    assign_recommendation,
    execute_recommendation,
    get_outcome_summary,
    persist_recommendations,
    reject_recommendation,
    verify_recommendation_outcome,
)
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
def tracking_context(db_session: Session):
    org_id = uuid.uuid4()
    store_id = uuid.uuid4()
    prod_id = uuid.uuid4()

    org = Organization(id=org_id, code=f"ORG-{uuid.uuid4().hex[:6]}", name="Tracking Test Org")
    store = Store(id=store_id, organization_id=org_id, store_code="STR01", name="Tracking Store 1")
    product = Product(id=prod_id, organization_id=org_id, sku="SKU-TRK-01", name="Test Milk", category="Dairy", is_perishable=True)
    sp = StoreProduct(organization_id=org_id, store_id=store_id, product_id=prod_id, is_active=True, reorder_level=Decimal("10.000"), target_stock_level=Decimal("30.000"))

    db_session.add_all([org, store, product, sp])
    db_session.commit()

    return {
        "org_id": org_id,
        "store_id": store_id,
        "product_id": prod_id,
    }


def test_persist_and_lifecycle_workflow(db_session: Session, tracking_context: dict):
    ctx = tracking_context
    org_id = ctx["org_id"]
    store_id = ctx["store_id"]
    prod_id = ctx["product_id"]

    rec = ActionRecommendation(
        recommendation_id=str(uuid.uuid4()),
        org_id=org_id,
        store_id=store_id,
        product_id=prod_id,
        sku="SKU-TRK-01",
        action_type=ActionType.MARKDOWN_REVIEW,
        priority=Priority.HIGH,
        reason="Urgent expiry risk. 25% markdown recommended.",
        predicted_opportunity_value=Decimal("1000.000"),
        estimated_benefit_value=Decimal("750.000"),
    )

    # 1. Persist
    persisted = persist_recommendations(db_session, [rec])
    assert len(persisted) == 1
    rec_record = persisted[0]
    assert rec_record.status == RecommendationStatus.PENDING
    assert rec_record.predicted_opportunity_value == Decimal("1000.000")
    assert rec_record.estimated_benefit_value == Decimal("750.000")
    rec_id = rec_record.id

    # 2. Approve
    approved = approve_recommendation(
        db=db_session,
        rec_id=rec_id,
        org_id=org_id,
        approver_name="Manager Rajesh",
        notes="Approved 25% markdown for front-shelf end-cap",
    )
    assert approved.status == RecommendationStatus.APPROVED
    assert "Manager Rajesh" in approved.resolution_notes

    # 3. Assign
    assigned = assign_recommendation(
        db=db_session,
        rec_id=rec_id,
        org_id=org_id,
        assignee_name="Associate Sunita",
    )
    assert assigned.status == RecommendationStatus.IN_PROGRESS
    assert assigned.assigned_to == "Associate Sunita"

    # 4. Execute
    executed = execute_recommendation(
        db=db_session,
        rec_id=rec_id,
        org_id=org_id,
        execution_notes="Stickers applied on floor; end-cap displayed.",
    )
    assert executed.status == RecommendationStatus.COMPLETED
    assert executed.completed_at is not None

    # 5. Verify & Record Empirical Realized Benefit
    verified = verify_recommendation_outcome(
        db=db_session,
        rec_id=rec_id,
        org_id=org_id,
        verifier_name="Auditor Deepak",
        realized_benefit=Decimal("720.000"),
        outcome_notes="Sold 96% of batch before expiry. Actual realized revenue ₹720.",
    )
    assert verified.status == RecommendationStatus.VERIFIED
    assert verified.verified_by == "Auditor Deepak"
    assert verified.realized_benefit_value == Decimal("720.000")
    assert verified.actual_outcome_notes == "Sold 96% of batch before expiry. Actual realized revenue ₹720."

    # 6. Check Outcome Summary Metrics
    summary = get_outcome_summary(db_session, org_id, store_id)
    assert summary["total_recommendations"] >= 1
    assert summary["status_counts"]["VERIFIED"] >= 1
    fin = summary["financial_outcomes"]
    assert Decimal(fin["predicted_opportunity_value"]) >= Decimal("1000.000")
    assert Decimal(fin["estimated_benefit_value"]) >= Decimal("750.000")
    assert Decimal(fin["verified_realized_benefit"]) >= Decimal("720.000")
    assert fin["benefit_realization_rate_pct"] > 0


def test_invalid_lifecycle_transitions(db_session: Session, tracking_context: dict):
    ctx = tracking_context
    org_id = ctx["org_id"]
    store_id = ctx["store_id"]
    prod_id = ctx["product_id"]

    rec = ActionRecommendation(
        recommendation_id=str(uuid.uuid4()),
        org_id=org_id,
        store_id=store_id,
        product_id=prod_id,
        sku="SKU-TRK-01",
        action_type=ActionType.REPLENISH,
        priority=Priority.MEDIUM,
        reason="Stock low.",
        predicted_opportunity_value=Decimal("500.000"),
        estimated_benefit_value=Decimal("300.000"),
    )
    persisted = persist_recommendations(db_session, [rec])
    rec_id = persisted[0].id

    # Trying to verify a PENDING recommendation without execution must fail
    with pytest.raises(InvalidLifecycleTransitionError):
        verify_recommendation_outcome(
            db=db_session,
            rec_id=rec_id,
            org_id=org_id,
            verifier_name="Auditor Deepak",
            realized_benefit=Decimal("300.000"),
            outcome_notes="Should fail",
        )

    # Reject recommendation
    rejected = reject_recommendation(
        db=db_session,
        rec_id=rec_id,
        org_id=org_id,
        rejecter_name="Manager Rajesh",
        reason="Supplier delayed shipment",
    )
    assert rejected.status == RecommendationStatus.REJECTED


def test_api_outcome_tracking_endpoints(db_session: Session, tracking_context: dict):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db

    ctx = tracking_context
    org_id = ctx["org_id"]
    store_id = ctx["store_id"]
    prod_id = ctx["product_id"]

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    try:
        rec = ActionRecommendation(
            recommendation_id=str(uuid.uuid4()),
            org_id=org_id,
            store_id=store_id,
            product_id=prod_id,
            sku="SKU-TRK-01",
            action_type=ActionType.MARKDOWN_REVIEW,
            priority=Priority.CRITICAL,
            reason="1 day left before expiry. 40% clearance markdown recommended.",
            predicted_opportunity_value=Decimal("800.000"),
            estimated_benefit_value=Decimal("480.000"),
        )
        persisted = persist_recommendations(db_session, [rec])
        rec_id = str(persisted[0].id)

        # 1. Approve via HTTP
        res_app = client.post(
            f"/api/v1/decision-support/recommendations/{rec_id}/approve",
            json={
                "organization_id": str(org_id),
                "approver_name": "Store Manager Anita",
                "notes": "Fast clearance authorized.",
            },
        )
        assert res_app.status_code == 200, res_app.text
        assert res_app.json()["status"] == "APPROVED"

        # 2. Assign via HTTP
        res_asg = client.post(
            f"/api/v1/decision-support/recommendations/{rec_id}/assign",
            json={
                "organization_id": str(org_id),
                "assignee_name": "Floor Lead Imran",
            },
        )
        assert res_asg.status_code == 200, res_asg.text
        assert res_asg.json()["status"] == "IN_PROGRESS"
        assert res_asg.json()["assigned_to"] == "Floor Lead Imran"

        # 3. Execute via HTTP
        res_exe = client.post(
            f"/api/v1/decision-support/recommendations/{rec_id}/execute",
            json={
                "organization_id": str(org_id),
                "execution_notes": "Placed in discount bin near entrance.",
            },
        )
        assert res_exe.status_code == 200, res_exe.text
        assert res_exe.json()["status"] == "COMPLETED"

        # 4. Verify via HTTP
        res_ver = client.post(
            f"/api/v1/decision-support/recommendations/{rec_id}/verify",
            json={
                "organization_id": str(org_id),
                "verifier_name": "Inventory Auditor Meera",
                "realized_benefit": 460.50,
                "outcome_notes": "All 8 units sold within 6 hours. Recovered ₹460.50.",
            },
        )
        assert res_ver.status_code == 200, res_ver.text
        assert res_ver.json()["status"] == "VERIFIED"
        assert float(res_ver.json()["realized_benefit_value"]) == 460.50

        # 5. Outcome summary endpoint
        res_sum = client.get(
            f"/api/v1/decision-support/recommendations/outcomes/summary?organization_id={org_id}&store_id={store_id}"
        )
        assert res_sum.status_code == 200, res_sum.text
        data = res_sum.json()
        assert data["total_recommendations"] >= 1
        assert float(data["financial_outcomes"]["verified_realized_benefit"]) >= 460.50
    finally:
        app.dependency_overrides.clear()

