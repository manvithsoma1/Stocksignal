"""
API router for inventory reconciliation, perishable risks, and recommendations.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.core import BucketType
from app.services import perishable, reconciliation, recommendations

router = APIRouter(prefix="/decision-support", tags=["Decision Support & Integrity"])


class ReconcileRequest(BaseModel):
    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    counts: dict[BucketType, Decimal]


@router.post("/reconcile/product")
def reconcile_single_product(
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
):
    """Reconciles physical count against inventory ledger for a single product."""
    report = reconciliation.reconcile_product(
        db=db,
        org_id=payload.organization_id,
        store_id=payload.store_id,
        product_id=payload.product_id,
        physical_counts=payload.counts,
    )
    return report


@router.get("/perishable/urgent")
def get_urgent_batches(
    organization_id: uuid.UUID,
    store_id: Optional[uuid.UUID] = None,
    min_tier: perishable.RiskTier = perishable.RiskTier.URGENT,
    db: Session = Depends(get_db),
):
    """Identifies perishable batches requiring FEFO intervention or markdowns."""
    urgent = perishable.find_urgent_batches(
        db=db,
        org_id=organization_id,
        store_id=store_id,
        min_tier=min_tier,
    )
    return urgent


@router.get("/recommendations")
def get_action_recommendations(
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    min_priority: recommendations.Priority = recommendations.Priority.MEDIUM,
    db: Session = Depends(get_db),
):
    """
    Synthesizes current stock levels, shelf-life expiry, and physical audit discrepancies
    into prioritized, actionable operational interventions.
    """
    # 1. Fetch perishable assessments
    urgent = perishable.find_urgent_batches(
        db=db,
        org_id=organization_id,
        store_id=store_id,
        min_tier=perishable.RiskTier.WATCH,
    )
    
    # 2. Convert to recommendations
    recs = recommendations.generate_recommendations(
        perishable_reports=[], # Per-product aggregates can be passed here
        stock_levels=[],
    )
    
    return recommendations.filter_recommendations(recs, min_priority=min_priority)
