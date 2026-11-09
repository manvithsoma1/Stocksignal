"""
API router for inventory reconciliation, perishable risks, and recommendations.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.core import BucketType, Product, Store, StoreProduct
from app.models.uploads import DataUploadBatch, UploadStatus
from app.services import ledger, outcome_tracking, perishable, reconciliation, recommendations
from app.services.forecasting import inference

router = APIRouter(prefix="/api/v1/decision-support", tags=["Decision Support & Integrity"])


class ReconcileRequest(BaseModel):
    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    counts: dict[BucketType, Decimal]


class ApproveRecommendationRequest(BaseModel):
    organization_id: uuid.UUID
    approver_name: str = "Store Manager"
    notes: Optional[str] = None


class RejectRecommendationRequest(BaseModel):
    organization_id: uuid.UUID
    rejecter_name: str = "Store Manager"
    reason: str = "Operationally unfeasible"


class AssignRecommendationRequest(BaseModel):
    organization_id: uuid.UUID
    assignee_name: str


class ExecuteRecommendationRequest(BaseModel):
    organization_id: uuid.UUID
    execution_notes: str = "Action executed on floor"


class VerifyRecommendationRequest(BaseModel):
    organization_id: uuid.UUID
    verifier_name: str
    realized_benefit: Decimal
    outcome_notes: str


@router.get("/stores")
def get_stores(
    organization_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
):
    """List all stores for selector dropdowns."""
    query = select(Store)
    if organization_id:
        query = query.where(Store.organization_id == organization_id)
    stores = db.execute(query).scalars().all()
    return [
        {
            "id": s.id,
            "organization_id": s.organization_id,
            "store_code": s.store_code,
            "name": s.name,
            "status": s.status,
        }
        for s in stores
    ]


@router.get("/overview")
def get_store_overview(
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """
    Returns real-time dynamic dashboard summary metrics for a store:
      - Total active SKUs
      - Current total on-hand stock (sellable & quarantined)
      - Urgent perishable batch count & estimated value at risk
      - Total pending action recommendations
    """
    # 1. Active SKUs
    sp_count = db.execute(
        select(StoreProduct).where(
            StoreProduct.organization_id == organization_id,
            StoreProduct.store_id == store_id,
            StoreProduct.is_active == True,
        )
    ).scalars().all()
    active_skus = len(sp_count)

    # 2. Perishable risks
    urgent_batches = perishable.find_urgent_batches(
        db=db,
        org_id=organization_id,
        store_id=store_id,
        min_tier=perishable.RiskTier.WATCH,
    )
    total_val_at_risk = sum(b.value_at_risk for b in urgent_batches)

    # 3. Stock levels across store products
    stock_levels = []
    for sp in sp_count:
        bal = ledger.get_stock_balance(db, organization_id, store_id, sp.product_id)
        prod = db.execute(
            select(Product).where(Product.organization_id == organization_id, Product.id == sp.product_id)
        ).scalar_one_or_none()
        stock_levels.append({
            "org_id": organization_id,
            "store_id": store_id,
            "product_id": sp.product_id,
            "sku": prod.sku if prod else str(sp.product_id),
            "store_code": "",
            "current_sellable": bal.sellable,
            "reorder_level": sp.reorder_level,
            "target_stock": sp.target_stock_level,
        })

    # 4. Generate recommendations dynamically
    perishable_reports = []
    for sp in sp_list:
        p_report = perishable.assess_product(
            db=db,
            org_id=organization_id,
            store_id=store_id,
            product_id=sp.product_id,
        )
        if p_report.batches:
            perishable_reports.append(p_report)

    recs = recommendations.generate_recommendations(
        perishable_reports=perishable_reports,
        stock_levels=stock_levels,
    )

    # 5. Distinguish Demo Data vs. Real Uploaded Data
    real_upload_count = db.execute(
        select(DataUploadBatch).where(
            DataUploadBatch.organization_id == organization_id,
            DataUploadBatch.status == UploadStatus.COMPLETED,
        )
    ).scalars().all()

    data_mode = "REAL_DATA_MODE" if len(real_upload_count) > 0 else "DEMO_SEED_MODE"

    return {
        "organization_id": organization_id,
        "store_id": store_id,
        "data_mode": data_mode,
        "uploaded_datasets_count": len(real_upload_count),
        "active_skus": active_skus,
        "urgent_perishable_count": len(urgent_batches),
        "total_value_at_risk": total_val_at_risk,
        "pending_recommendations_count": len(recs),
        "urgent_batches": urgent_batches[:5],
        "top_recommendations": recs[:5],
    }


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
    into prioritized, actionable operational interventions dynamically.
    """
    sp_list = db.execute(
        select(StoreProduct).where(
            StoreProduct.organization_id == organization_id,
            StoreProduct.store_id == store_id,
            StoreProduct.is_active == True,
        )
    ).scalars().all()

    stock_levels = []
    for sp in sp_list:
        bal = ledger.get_stock_balance(db, organization_id, store_id, sp.product_id)
        prod = db.execute(
            select(Product).where(Product.organization_id == organization_id, Product.id == sp.product_id)
        ).scalar_one_or_none()
        stock_levels.append({
            "org_id": organization_id,
            "store_id": store_id,
            "product_id": sp.product_id,
            "sku": prod.sku if prod else str(sp.product_id),
            "store_code": "",
            "current_sellable": bal.sellable,
            "reorder_level": sp.reorder_level,
            "target_stock": sp.target_stock_level,
        })

    perishable_reports = []
    for sp in sp_list:
        p_report = perishable.assess_product(
            db=db,
            org_id=organization_id,
            store_id=store_id,
            product_id=sp.product_id,
        )
        if p_report.batches:
            perishable_reports.append(p_report)

    recs = recommendations.generate_recommendations(
        perishable_reports=perishable_reports,
        stock_levels=stock_levels,
    )

    filtered = recommendations.filter_recommendations(recs, min_priority=min_priority)
    try:
        outcome_tracking.persist_recommendations(db, filtered)
    except Exception:
        # Never break retrieval if background persistence has a transient issue
        pass
    return filtered


@router.post("/recommendations/{id}/approve")
def approve_recommendation_endpoint(
    id: uuid.UUID,
    payload: ApproveRecommendationRequest,
    db: Session = Depends(get_db),
):
    try:
        return outcome_tracking.approve_recommendation(
            db=db,
            rec_id=id,
            org_id=payload.organization_id,
            approver_name=payload.approver_name,
            notes=payload.notes,
        )
    except outcome_tracking.RecommendationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except outcome_tracking.InvalidLifecycleTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/recommendations/{id}/reject")
def reject_recommendation_endpoint(
    id: uuid.UUID,
    payload: RejectRecommendationRequest,
    db: Session = Depends(get_db),
):
    try:
        return outcome_tracking.reject_recommendation(
            db=db,
            rec_id=id,
            org_id=payload.organization_id,
            rejecter_name=payload.rejecter_name,
            reason=payload.reason,
        )
    except outcome_tracking.RecommendationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except outcome_tracking.InvalidLifecycleTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/recommendations/{id}/assign")
def assign_recommendation_endpoint(
    id: uuid.UUID,
    payload: AssignRecommendationRequest,
    db: Session = Depends(get_db),
):
    try:
        return outcome_tracking.assign_recommendation(
            db=db,
            rec_id=id,
            org_id=payload.organization_id,
            assignee_name=payload.assignee_name,
        )
    except outcome_tracking.RecommendationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except outcome_tracking.InvalidLifecycleTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/recommendations/{id}/execute")
def execute_recommendation_endpoint(
    id: uuid.UUID,
    payload: ExecuteRecommendationRequest,
    db: Session = Depends(get_db),
):
    try:
        return outcome_tracking.execute_recommendation(
            db=db,
            rec_id=id,
            org_id=payload.organization_id,
            execution_notes=payload.execution_notes,
        )
    except outcome_tracking.RecommendationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except outcome_tracking.InvalidLifecycleTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/recommendations/{id}/verify")
def verify_recommendation_endpoint(
    id: uuid.UUID,
    payload: VerifyRecommendationRequest,
    db: Session = Depends(get_db),
):
    try:
        return outcome_tracking.verify_recommendation_outcome(
            db=db,
            rec_id=id,
            org_id=payload.organization_id,
            verifier_name=payload.verifier_name,
            realized_benefit=payload.realized_benefit,
            outcome_notes=payload.outcome_notes,
        )
    except outcome_tracking.RecommendationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except outcome_tracking.InvalidLifecycleTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/recommendations/outcomes/summary")
def get_recommendation_outcomes_summary(
    organization_id: uuid.UUID,
    store_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
):
    return outcome_tracking.get_outcome_summary(
        db=db,
        org_id=organization_id,
        store_id=store_id,
    )


@router.get("/forecast")
def get_sku_demand_forecast(
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    horizon_days: int = Query(14, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """
    ML demand forecast and stockout risk projection using trained LightGBM model.
    """
    return inference.forecast_sku_demand(
        db=db,
        org_id=organization_id,
        store_id=store_id,
        product_id=product_id,
        horizon_days=horizon_days,
    )


@router.get("/store-products")
def get_store_products(
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """List catalog store products with SKUs and names."""
    sp_list = db.execute(
        select(StoreProduct).where(
            StoreProduct.organization_id == organization_id,
            StoreProduct.store_id == store_id,
            StoreProduct.is_active == True,
        )
    ).scalars().all()

    items = []
    for sp in sp_list:
        p = db.execute(
            select(Product).where(Product.organization_id == organization_id, Product.id == sp.product_id)
        ).scalar_one_or_none()
        if p:
            items.append({
                "product_id": p.id,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "is_perishable": p.is_perishable,
            })
    return items
