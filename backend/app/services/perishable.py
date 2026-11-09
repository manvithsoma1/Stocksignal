"""
StockSignal Perishable Risk Module — Phase 7.

Calculates expiry risk for perishable inventory batches:
  - Days remaining before expiry.
  - Expected demand before expiry (from average daily sales or ledger history).
  - Estimated unsold quantity at expiry.
  - Inventory value at risk.
  - FEFO (First-Expiry-First-Out) allocation suggestion.
  - Risk tier: SAFE / WATCH / URGENT / CRITICAL / EXPIRED.

All arithmetic uses Decimal for exact financial precision.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

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


# --------------------------------------------------------------------------- #
# Enums & Constants                                                            #
# --------------------------------------------------------------------------- #

class RiskTier(str, Enum):
    SAFE = "SAFE"           # > 14 days remaining, adequate turnover
    WATCH = "WATCH"         # 7–14 days
    URGENT = "URGENT"       # 3–7 days
    CRITICAL = "CRITICAL"   # 0–3 days
    EXPIRED = "EXPIRED"     # expiry_date < today


# Days-remaining thresholds (inclusive lower bound)
CRITICAL_DAYS = 3
URGENT_DAYS = 7
WATCH_DAYS = 14

# Default lookback window for average daily sales calculation (days)
DEFAULT_LOOKBACK_DAYS = 30

# Default unit cost used if no cost information is available
DEFAULT_UNIT_COST = Decimal("0.000")


# --------------------------------------------------------------------------- #
# Data Structures                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class BatchRiskAssessment:
    """Full risk assessment for a single batch."""
    batch_id: uuid.UUID
    batch_code: Optional[str]
    org_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    expiry_date: Optional[date]
    days_remaining: Optional[int]           # None if no expiry date
    current_qty: Decimal                    # current sellable stock in this batch (from postings)
    avg_daily_demand: Decimal               # historical avg daily sales
    expected_demand_before_expiry: Decimal  # avg_daily * days_remaining
    estimated_unsold_qty: Decimal           # max(0, current_qty - expected_demand)
    unit_cost: Decimal
    value_at_risk: Decimal                  # estimated_unsold_qty * unit_cost
    risk_tier: RiskTier
    fefo_rank: int = 0                      # 1 = expires soonest; populated by rank_batches()
    notes: str = ""


@dataclass
class ProductPerishableReport:
    """Aggregated perishable risk for all batches of one store-product."""
    org_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    store_code: str
    assessed_at: datetime
    batches: list[BatchRiskAssessment] = field(default_factory=list)
    total_at_risk_qty: Decimal = Decimal("0.000")
    total_value_at_risk: Decimal = Decimal("0.000")
    worst_tier: RiskTier = RiskTier.SAFE
    fefo_recommendation: str = ""


# --------------------------------------------------------------------------- #
# Helper Functions                                                             #
# --------------------------------------------------------------------------- #

def _days_remaining(expiry: Optional[date], today: Optional[date] = None) -> Optional[int]:
    """Return (expiry - today) in days, or None if no expiry date."""
    if expiry is None:
        return None
    today = today or date.today()
    return (expiry - today).days


def _tier_from_days(days: Optional[int]) -> RiskTier:
    """Map days remaining to a RiskTier."""
    if days is None:
        return RiskTier.SAFE          # no expiry = not perishable
    if days < 0:
        return RiskTier.EXPIRED
    if days <= CRITICAL_DAYS:
        return RiskTier.CRITICAL
    if days <= URGENT_DAYS:
        return RiskTier.URGENT
    if days <= WATCH_DAYS:
        return RiskTier.WATCH
    return RiskTier.SAFE


_TIER_ORDER = [RiskTier.SAFE, RiskTier.WATCH, RiskTier.URGENT, RiskTier.CRITICAL, RiskTier.EXPIRED]


def _max_tier(*tiers: RiskTier) -> RiskTier:
    return max(tiers, key=lambda t: _TIER_ORDER.index(t))


def _get_avg_daily_demand(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Decimal:
    """
    Compute average daily demand by summing SALE movement deltas over lookback window
    and dividing by lookback_days.

    Returns Decimal("0.000") if no sales history exists.
    """
    # Sum of SALE postings (negatives, so we take abs) for SELLABLE bucket
    result = db.execute(
        text(
            """
            SELECT COALESCE(SUM(ABS(p.quantity_delta)), 0)
            FROM inventory_movement_postings p
            JOIN inventory_movements m ON m.id = p.movement_id
            WHERE m.organization_id = :org_id
              AND m.store_id = :store_id
              AND m.product_id = :product_id
              AND m.movement_type = 'SALE'
              AND p.bucket_type = 'SELLABLE'
              AND m.occurred_at >= NOW() - INTERVAL '1 day' * :lookback
            """
        ),
        {
            "org_id": org_id,
            "store_id": store_id,
            "product_id": product_id,
            "lookback": lookback_days,
        },
    ).scalar()

    total_sold = Decimal(str(result or 0))
    return (total_sold / Decimal(str(lookback_days))).quantize(Decimal("0.001"))


def _get_batch_sellable_qty(
    db: Session,
    org_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> Decimal:
    """
    Calculate the current sellable quantity for a specific batch
    by summing all SELLABLE postings for movements that reference this batch.
    """
    result = db.execute(
        text(
            """
            SELECT COALESCE(SUM(p.quantity_delta), 0)
            FROM inventory_movement_postings p
            JOIN inventory_movements m ON m.id = p.movement_id
            WHERE m.organization_id = :org_id
              AND m.batch_id = :batch_id
              AND p.bucket_type = 'SELLABLE'
            """
        ),
        {"org_id": org_id, "batch_id": batch_id},
    ).scalar()

    qty = Decimal(str(result or 0)).quantize(Decimal("0.001"))
    return max(qty, Decimal("0.000"))


# --------------------------------------------------------------------------- #
# Core Assessment Functions                                                    #
# --------------------------------------------------------------------------- #

def assess_batch(
    db: Session,
    batch: Batch,
    avg_daily_demand: Decimal,
    unit_cost: Decimal = DEFAULT_UNIT_COST,
    today: Optional[date] = None,
) -> BatchRiskAssessment:
    """
    Assess perishable risk for a single Batch.

    Args:
        db: Database session.
        batch: The Batch ORM object.
        avg_daily_demand: Pre-computed average daily demand for the product.
        unit_cost: Unit cost for value-at-risk calculation.
        today: Reference date (defaults to today).

    Returns:
        BatchRiskAssessment with all risk fields populated.
    """
    days = _days_remaining(batch.expiry_date, today)
    tier = _tier_from_days(days)

    current_qty = _get_batch_sellable_qty(db, batch.organization_id, batch.id)

    if days is not None and days >= 0:
        expected_demand = (avg_daily_demand * Decimal(str(days))).quantize(Decimal("0.001"))
    else:
        expected_demand = Decimal("0.000")

    unsold = max(current_qty - expected_demand, Decimal("0.000")).quantize(Decimal("0.001"))
    value_at_risk = (unsold * unit_cost).quantize(Decimal("0.001"))

    return BatchRiskAssessment(
        batch_id=batch.id,
        batch_code=batch.batch_code,
        org_id=batch.organization_id,
        store_id=batch.store_id,
        product_id=batch.product_id,
        sku="",  # populated by caller
        expiry_date=batch.expiry_date,
        days_remaining=days,
        current_qty=current_qty,
        avg_daily_demand=avg_daily_demand,
        expected_demand_before_expiry=expected_demand,
        estimated_unsold_qty=unsold,
        unit_cost=unit_cost,
        value_at_risk=value_at_risk,
        risk_tier=tier,
    )


def rank_batches(assessments: list[BatchRiskAssessment]) -> list[BatchRiskAssessment]:
    """
    Assign FEFO rank (1 = expires soonest) to a list of BatchRiskAssessment objects.
    Batches without expiry dates are ranked last.

    Returns the list sorted by rank (in-place modification + return).
    """
    def sort_key(a: BatchRiskAssessment):
        # EXPIRED and CRITICAL first, then ascending days_remaining, None last
        if a.expiry_date is None:
            return (1, 9999999)
        days = a.days_remaining if a.days_remaining is not None else 9999999
        return (0, days)

    assessments.sort(key=sort_key)
    for rank, a in enumerate(assessments, start=1):
        a.fefo_rank = rank
    return assessments


def assess_product(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    unit_cost: Decimal = DEFAULT_UNIT_COST,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: Optional[date] = None,
) -> ProductPerishableReport:
    """
    Assess perishable risk for all batches of one store-product.

    Returns:
        ProductPerishableReport with per-batch details and aggregate metrics.
    """
    # Fetch product SKU
    product = db.execute(
        select(Product).where(
            Product.organization_id == org_id,
            Product.id == product_id,
        )
    ).scalar_one_or_none()
    sku = product.sku if product else str(product_id)

    # Fetch store code
    store = db.execute(
        select(Store).where(
            Store.organization_id == org_id,
            Store.id == store_id,
        )
    ).scalar_one_or_none()
    store_code = store.store_code if store else str(store_id)

    # Average daily demand
    avg_daily = _get_avg_daily_demand(db, org_id, store_id, product_id, lookback_days)

    # Fetch all batches for this store-product
    batches: Sequence[Batch] = db.execute(
        select(Batch).where(
            Batch.organization_id == org_id,
            Batch.store_id == store_id,
            Batch.product_id == product_id,
        )
    ).scalars().all()

    assessments = []
    for batch in batches:
        a = assess_batch(db, batch, avg_daily, unit_cost, today)
        a.sku = sku
        assessments.append(a)

    # Apply FEFO ranking
    rank_batches(assessments)

    # Build aggregate metrics
    total_at_risk = sum(a.estimated_unsold_qty for a in assessments)
    total_value = sum(a.value_at_risk for a in assessments)
    worst = _max_tier(*[a.risk_tier for a in assessments]) if assessments else RiskTier.SAFE

    # FEFO recommendation text
    if assessments:
        first = assessments[0]  # expires soonest
        if first.risk_tier in (RiskTier.EXPIRED, RiskTier.CRITICAL, RiskTier.URGENT):
            fefo_rec = (
                f"Prioritise batch {first.batch_code or first.batch_id} "
                f"({first.days_remaining} days remaining, qty={first.current_qty}). "
                "Allocate to customer orders before later-expiry batches."
            )
        else:
            fefo_rec = "Stock rotation is on track. Continue FEFO allocation."
    else:
        fefo_rec = "No batches found for this store-product."

    return ProductPerishableReport(
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        sku=sku,
        store_code=store_code,
        assessed_at=datetime.now(timezone.utc),
        batches=assessments,
        total_at_risk_qty=total_at_risk.quantize(Decimal("0.001")),
        total_value_at_risk=total_value.quantize(Decimal("0.001")),
        worst_tier=worst,
        fefo_recommendation=fefo_rec,
    )


def find_urgent_batches(
    db: Session,
    org_id: uuid.UUID,
    store_id: Optional[uuid.UUID] = None,
    min_tier: RiskTier = RiskTier.URGENT,
    today: Optional[date] = None,
) -> list[BatchRiskAssessment]:
    """
    Find all batches at or above min_tier across all products (or a specific store).

    Returns a flat list of BatchRiskAssessment objects, FEFO-ranked per store-product.
    """
    today = today or date.today()

    query = select(Batch).where(Batch.organization_id == org_id)
    if store_id:
        query = query.where(Batch.store_id == store_id)

    batches: Sequence[Batch] = db.execute(query).scalars().all()

    # Group by (store_id, product_id) for avg demand + ranking
    groups: dict[tuple, list[Batch]] = {}
    for b in batches:
        key = (b.store_id, b.product_id)
        groups.setdefault(key, []).append(b)

    all_assessments = []
    for (sid, pid), group_batches in groups.items():
        avg_daily = _get_avg_daily_demand(db, org_id, sid, pid)
        group_assessments = []
        for batch in group_batches:
            a = assess_batch(db, batch, avg_daily, today=today)
            group_assessments.append(a)
        rank_batches(group_assessments)
        all_assessments.extend(group_assessments)

    # Filter to min_tier
    min_idx = _TIER_ORDER.index(min_tier)
    urgent = [a for a in all_assessments if _TIER_ORDER.index(a.risk_tier) >= min_idx]

    # Sort globally: EXPIRED first, then CRITICAL, then by days ascending
    urgent.sort(key=lambda a: (_TIER_ORDER.index(a.risk_tier) * -1, a.days_remaining or 9999))
    return urgent

