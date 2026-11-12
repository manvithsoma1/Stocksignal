"""
StockSignal Outcome Tracking Service — Phase 9 (Priority 7).

Manages the operational lifecycle of action recommendations:
  PENDING -> APPROVED / REJECTED -> IN_PROGRESS -> COMPLETED -> VERIFIED

Distinguishes and measures:
  1. Predicted Opportunity Value (total financial exposure/gap before intervention)
  2. Estimated Benefit Value (expected margin or salvage recovery from intervention)
  3. Verified Realized Benefit (empirically confirmed financial benefit post-execution)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.recommendations import ActionRecommendationRecord, RecommendationStatus
from app.services.recommendations import ActionRecommendation


class RecommendationNotFoundError(Exception):
    pass


class InvalidLifecycleTransitionError(Exception):
    pass


def persist_recommendations(
    db: Session,
    recs: Sequence[ActionRecommendation],
) -> list[ActionRecommendationRecord]:
    """
    Idempotently persist or fetch ActionRecommendation records in the database.
    If a PENDING record already exists for the same (org, store, product, action_type),
    its predictions and details are refreshed.
    """
    persisted = []
    for r in recs:
        # Check existing pending recommendation for this product & action
        existing = db.execute(
            select(ActionRecommendationRecord).where(
                ActionRecommendationRecord.organization_id == r.org_id,
                ActionRecommendationRecord.store_id == r.store_id,
                ActionRecommendationRecord.product_id == r.product_id,
                ActionRecommendationRecord.action_type == r.action_type.value,
                ActionRecommendationRecord.status.in_([
                    RecommendationStatus.PENDING,
                    RecommendationStatus.APPROVED,
                    RecommendationStatus.IN_PROGRESS,
                ]),
            )
        ).scalar_one_or_none()

        if existing:
            # Update estimated values and reason if pending
            if existing.status == RecommendationStatus.PENDING:
                existing.priority = r.priority.value
                existing.reason = r.reason
                existing.detail = r.detail
                existing.predicted_opportunity_value = r.predicted_opportunity_value
                existing.estimated_benefit_value = r.estimated_benefit_value
            r.recommendation_id = str(existing.id)
            r.status = existing.status.value
            persisted.append(existing)
        else:
            record_id = uuid.UUID(r.recommendation_id) if isinstance(r.recommendation_id, str) and len(r.recommendation_id) == 36 else uuid.uuid4()
            new_rec = ActionRecommendationRecord(
                id=record_id,
                organization_id=r.org_id,
                store_id=r.store_id,
                product_id=r.product_id,
                action_type=r.action_type.value,
                priority=r.priority.value,
                status=RecommendationStatus.PENDING,
                reason=r.reason,
                detail=r.detail,
                predicted_opportunity_value=r.predicted_opportunity_value,
                estimated_benefit_value=r.estimated_benefit_value,
            )
            db.add(new_rec)
            r.recommendation_id = str(record_id)
            r.status = RecommendationStatus.PENDING.value
            persisted.append(new_rec)

    db.commit()
    for p in persisted:
        db.refresh(p)
    return persisted


def approve_recommendation(
    db: Session,
    rec_id: uuid.UUID,
    org_id: uuid.UUID,
    approver_name: str = "Store Manager",
    notes: Optional[str] = None,
) -> ActionRecommendationRecord:
    """Approve a PENDING recommendation for execution."""
    rec = db.execute(
        select(ActionRecommendationRecord).where(
            ActionRecommendationRecord.id == rec_id,
            ActionRecommendationRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not rec:
        raise RecommendationNotFoundError(f"Recommendation {rec_id} not found for organization {org_id}")

    if rec.status not in (RecommendationStatus.PENDING, RecommendationStatus.REJECTED):
        raise InvalidLifecycleTransitionError(
            f"Cannot approve recommendation in '{rec.status.value}' status."
        )

    rec.status = RecommendationStatus.APPROVED
    rec.resolution_notes = f"Approved by {approver_name}. {notes or ''}".strip()
    db.commit()
    db.refresh(rec)
    return rec


def reject_recommendation(
    db: Session,
    rec_id: uuid.UUID,
    org_id: uuid.UUID,
    rejecter_name: str = "Store Manager",
    reason: str = "Operationally unfeasible",
) -> ActionRecommendationRecord:
    """Reject or dismiss a recommendation."""
    rec = db.execute(
        select(ActionRecommendationRecord).where(
            ActionRecommendationRecord.id == rec_id,
            ActionRecommendationRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not rec:
        raise RecommendationNotFoundError(f"Recommendation {rec_id} not found for organization {org_id}")

    if rec.status in (RecommendationStatus.COMPLETED, RecommendationStatus.VERIFIED):
        raise InvalidLifecycleTransitionError(
            f"Cannot reject already executed/verified recommendation in '{rec.status.value}' status."
        )

    rec.status = RecommendationStatus.REJECTED
    rec.resolution_notes = f"Rejected by {rejecter_name}: {reason}"
    db.commit()
    db.refresh(rec)
    return rec


def assign_recommendation(
    db: Session,
    rec_id: uuid.UUID,
    org_id: uuid.UUID,
    assignee_name: str,
) -> ActionRecommendationRecord:
    """Assign an approved recommendation to an operational team member."""
    rec = db.execute(
        select(ActionRecommendationRecord).where(
            ActionRecommendationRecord.id == rec_id,
            ActionRecommendationRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not rec:
        raise RecommendationNotFoundError(f"Recommendation {rec_id} not found for organization {org_id}")

    if rec.status not in (RecommendationStatus.APPROVED, RecommendationStatus.PENDING):
        raise InvalidLifecycleTransitionError(
            f"Cannot assign recommendation in '{rec.status.value}' status."
        )

    rec.assigned_to = assignee_name
    rec.status = RecommendationStatus.IN_PROGRESS
    db.commit()
    db.refresh(rec)
    return rec


def execute_recommendation(
    db: Session,
    rec_id: uuid.UUID,
    org_id: uuid.UUID,
    execution_notes: str = "Action executed on floor",
) -> ActionRecommendationRecord:
    """Mark an approved or in-progress recommendation as executed/completed."""
    rec = db.execute(
        select(ActionRecommendationRecord).where(
            ActionRecommendationRecord.id == rec_id,
            ActionRecommendationRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not rec:
        raise RecommendationNotFoundError(f"Recommendation {rec_id} not found for organization {org_id}")

    if rec.status not in (RecommendationStatus.APPROVED, RecommendationStatus.IN_PROGRESS):
        raise InvalidLifecycleTransitionError(
            f"Cannot execute recommendation with status '{rec.status.value}'."
        )

    rec.status = RecommendationStatus.COMPLETED
    rec.completed_at = datetime.now(timezone.utc)
    rec.resolution_notes = (
        f"{rec.resolution_notes or ''}\nExecuted: {execution_notes}".strip()
    )
    db.commit()
    db.refresh(rec)
    return rec


def verify_recommendation_outcome(
    db: Session,
    rec_id: uuid.UUID,
    org_id: uuid.UUID,
    verifier_name: str,
    realized_benefit: Decimal,
    outcome_notes: str,
) -> ActionRecommendationRecord:
    """
    Post-execution verification: audits the actual financial and operational outcome.
    Distinguishes:
      - Predicted Opportunity Value (original exposure)
      - Estimated Benefit Value (pre-execution expectation)
      - Verified Realized Benefit (actual empirical value recovered/saved)
    """
    rec = db.execute(
        select(ActionRecommendationRecord).where(
            ActionRecommendationRecord.id == rec_id,
            ActionRecommendationRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not rec:
        raise RecommendationNotFoundError(f"Recommendation {rec_id} not found for organization {org_id}")

    if rec.status != RecommendationStatus.COMPLETED:
        raise InvalidLifecycleTransitionError(
            f"Only COMPLETED recommendations can be verified. Current status: '{rec.status.value}'."
        )

    rec.status = RecommendationStatus.VERIFIED
    rec.verified_at = datetime.now(timezone.utc)
    rec.verified_by = verifier_name
    rec.realized_benefit_value = Decimal(str(realized_benefit)).quantize(Decimal("0.001"))
    rec.actual_outcome_notes = outcome_notes
    db.commit()
    db.refresh(rec)
    return rec


def get_outcome_summary(
    db: Session,
    org_id: uuid.UUID,
    store_id: Optional[uuid.UUID] = None,
) -> dict:
    """
    Aggregate metrics tracking recommendation lifecycle outcomes and realized benefits.
    """
    query = select(ActionRecommendationRecord).where(
        ActionRecommendationRecord.organization_id == org_id
    )
    if store_id:
        query = query.where(ActionRecommendationRecord.store_id == store_id)

    records = db.execute(query).scalars().all()

    counts = {s.value: 0 for s in RecommendationStatus}
    total_predicted_opp = Decimal("0.000")
    total_estimated_ben = Decimal("0.000")
    total_realized_ben = Decimal("0.000")

    for r in records:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
        if r.predicted_opportunity_value:
            total_predicted_opp += r.predicted_opportunity_value
        if r.estimated_benefit_value:
            total_estimated_ben += r.estimated_benefit_value
        if r.status == RecommendationStatus.VERIFIED and r.realized_benefit_value:
            total_realized_ben += r.realized_benefit_value

    realization_rate = (
        (total_realized_ben / total_estimated_ben * Decimal("100")).quantize(Decimal("0.01"))
        if total_estimated_ben > Decimal("0.000") else Decimal("0.00")
    )

    return {
        "organization_id": org_id,
        "store_id": store_id,
        "total_recommendations": len(records),
        "status_counts": counts,
        "financial_outcomes": {
            "predicted_opportunity_value": str(total_predicted_opp.quantize(Decimal("0.001"))),
            "estimated_benefit_value": str(total_estimated_ben.quantize(Decimal("0.001"))),
            "verified_realized_benefit": str(total_realized_ben.quantize(Decimal("0.001"))),
            "benefit_realization_rate_pct": float(realization_rate),
        },
    }
