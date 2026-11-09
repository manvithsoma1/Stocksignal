"""
StockSignal Action Recommendation Engine — Phase 8.

Rules-first decision engine that synthesises outputs from:
  - Reconciliation Engine (Phase 5): discrepancy reports.
  - Perishable Risk Module (Phase 7): batch expiry assessments.
  - Ledger Service (Phase 2): current stock balances.

Produces structured ActionRecommendation objects with:
  - action_type: one of PHYSICAL_COUNT, REPLENISH, MARKDOWN_REVIEW,
                 TRANSFER_REVIEW, INVESTIGATE, MONITOR, NO_ACTION.
  - priority: CRITICAL / HIGH / MEDIUM / LOW.
  - reason: human-readable explanation.
  - detail: machine-readable context dict.

Designed to run as a pure in-memory engine — no additional DB tables required
at this phase. Phase 9 will add persistence and outcome tracking.

All arithmetic uses Decimal for exact financial precision.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from app.services.perishable import ProductPerishableReport, RiskTier
from app.services.reconciliation import (
    AnomalySeverity,
    DiscrepancyClass,
    ProductReconciliationReport,
    SuggestedAction,
)


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #

class ActionType(str, Enum):
    PHYSICAL_COUNT = "PHYSICAL_COUNT"       # Trigger a physical count
    REPLENISH = "REPLENISH"                 # Place a replenishment order
    MARKDOWN_REVIEW = "MARKDOWN_REVIEW"     # Markdown / clearance action needed
    TRANSFER_REVIEW = "TRANSFER_REVIEW"     # Inter-store transfer candidate
    INVESTIGATE = "INVESTIGATE"             # Investigate discrepancy / anomaly
    MONITOR = "MONITOR"                     # Keep watching; no immediate action
    NO_ACTION = "NO_ACTION"                 # Everything OK


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


_PRIORITY_ORDER = [Priority.NONE, Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.CRITICAL]


def _max_priority(*priorities: Priority) -> Priority:
    return max(priorities, key=lambda p: _PRIORITY_ORDER.index(p))


# --------------------------------------------------------------------------- #
# Data Structures                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class ActionRecommendation:
    """
    A single recommendation for one store-product combination.

    May aggregate signals from reconciliation + perishable + stock level checks.
    """
    recommendation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store_id: uuid.UUID = field(default_factory=uuid.uuid4)
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    sku: str = ""
    store_code: str = ""
    action_type: ActionType = ActionType.NO_ACTION
    priority: Priority = Priority.NONE
    reason: str = ""
    detail: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Optional links to source signals
    reconciliation_severity: Optional[AnomalySeverity] = None
    perishable_tier: Optional[RiskTier] = None
    stock_vs_reorder: Optional[str] = None   # "BELOW_REORDER" | "OK" | None


# --------------------------------------------------------------------------- #
# Signal → Recommendation Mapping                                              #
# --------------------------------------------------------------------------- #

def _from_reconciliation(report: ProductReconciliationReport) -> Optional[ActionRecommendation]:
    """
    Map a reconciliation report to an ActionRecommendation.
    Returns None if no action is needed (severity OK, suggested OK).
    """
    sev = report.overall_severity
    cls = report.overall_classification
    action = report.suggested_action

    if sev == AnomalySeverity.OK:
        return None

    action_type_map = {
        SuggestedAction.PHYSICAL_COUNT: ActionType.PHYSICAL_COUNT,
        SuggestedAction.INVESTIGATE: ActionType.INVESTIGATE,
        SuggestedAction.CORRECT: ActionType.INVESTIGATE,
        SuggestedAction.ESCALATE: ActionType.PHYSICAL_COUNT,
        SuggestedAction.MONITOR: ActionType.MONITOR,
    }
    priority_map = {
        AnomalySeverity.LOW: Priority.LOW,
        AnomalySeverity.MEDIUM: Priority.MEDIUM,
        AnomalySeverity.HIGH: Priority.HIGH,
        AnomalySeverity.CRITICAL: Priority.CRITICAL,
    }

    return ActionRecommendation(
        org_id=report.org_id,
        store_id=report.store_id,
        product_id=report.product_id,
        sku=report.sku,
        store_code=report.store_code,
        action_type=action_type_map.get(action, ActionType.INVESTIGATE),
        priority=priority_map.get(sev, Priority.MEDIUM),
        reason=(
            f"{sev.value} inventory discrepancy detected ({cls.value}). "
            f"Gap: {report.total_gap} units. "
            f"Suggested: {action.value}."
        ),
        detail={
            "gap": str(report.total_gap),
            "total_ledger": str(report.total_ledger_physical),
            "total_physical": str(report.total_physical_count),
            "classification": cls.value,
            "severity": sev.value,
        },
        reconciliation_severity=sev,
    )


def _from_perishable(report: ProductPerishableReport) -> Optional[ActionRecommendation]:
    """
    Map a perishable risk report to an ActionRecommendation.
    Returns None if tier is SAFE.
    """
    tier = report.worst_tier

    if tier == RiskTier.SAFE:
        return None

    priority_map = {
        RiskTier.WATCH: Priority.LOW,
        RiskTier.URGENT: Priority.MEDIUM,
        RiskTier.CRITICAL: Priority.HIGH,
        RiskTier.EXPIRED: Priority.CRITICAL,
    }

    urgent_batches = [b for b in report.batches if b.risk_tier not in (RiskTier.SAFE, RiskTier.WATCH)]
    urgent_summary = "; ".join(
        f"batch {b.batch_code or b.batch_id}: {b.days_remaining}d left, {b.current_qty} units"
        for b in urgent_batches[:3]
    )

    return ActionRecommendation(
        org_id=report.org_id,
        store_id=report.store_id,
        product_id=report.product_id,
        sku=report.sku,
        store_code=report.store_code,
        action_type=ActionType.MARKDOWN_REVIEW,
        priority=priority_map.get(tier, Priority.MEDIUM),
        reason=(
            f"Perishable risk tier: {tier.value}. "
            f"Value at risk: ₹{report.total_value_at_risk}. "
            f"{report.fefo_recommendation}"
        ),
        detail={
            "worst_tier": tier.value,
            "total_at_risk_qty": str(report.total_at_risk_qty),
            "total_value_at_risk": str(report.total_value_at_risk),
            "urgent_batches": urgent_summary,
            "fefo_recommendation": report.fefo_recommendation,
        },
        perishable_tier=tier,
    )


def _from_stock_level(
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    sku: str,
    store_code: str,
    current_sellable: Decimal,
    reorder_level: Optional[Decimal],
    target_stock: Optional[Decimal],
) -> Optional[ActionRecommendation]:
    """
    Generate a REPLENISH recommendation if stock is below reorder_level.
    Returns None if reorder_level is not set or stock is sufficient.
    """
    if reorder_level is None or reorder_level <= Decimal("0.000"):
        return None
    if current_sellable > reorder_level:
        return None

    gap_to_target = (
        (target_stock - current_sellable).quantize(Decimal("0.001"))
        if target_stock else None
    )

    # Urgency: if stock is 0, it's critical
    if current_sellable <= Decimal("0.000"):
        priority = Priority.CRITICAL
        reason = f"Stock is ZERO. Reorder level: {reorder_level}. Immediate replenishment required."
    elif current_sellable < reorder_level * Decimal("0.5"):
        priority = Priority.HIGH
        reason = (
            f"Stock ({current_sellable}) is below 50% of reorder level ({reorder_level}). "
            "Replenishment overdue."
        )
    else:
        priority = Priority.MEDIUM
        reason = (
            f"Stock ({current_sellable}) has fallen below reorder level ({reorder_level}). "
            "Trigger replenishment order."
        )

    return ActionRecommendation(
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        sku=sku,
        store_code=store_code,
        action_type=ActionType.REPLENISH,
        priority=priority,
        reason=reason,
        detail={
            "current_sellable": str(current_sellable),
            "reorder_level": str(reorder_level),
            "gap_to_target": str(gap_to_target) if gap_to_target is not None else None,
        },
        stock_vs_reorder="BELOW_REORDER",
    )


# --------------------------------------------------------------------------- #
# Deduplication & Merging                                                      #
# --------------------------------------------------------------------------- #

def _merge_for_product(
    recommendations: list[ActionRecommendation],
) -> ActionRecommendation:
    """
    When multiple signals fire for the same store-product, merge them into one
    recommendation with the highest-priority action.

    Priority tie-breaking: PHYSICAL_COUNT > MARKDOWN_REVIEW > REPLENISH > INVESTIGATE > MONITOR > NO_ACTION
    """
    if not recommendations:
        raise ValueError("Cannot merge empty recommendations list")
    if len(recommendations) == 1:
        return recommendations[0]

    _action_rank = {
        ActionType.PHYSICAL_COUNT: 6,
        ActionType.MARKDOWN_REVIEW: 5,
        ActionType.REPLENISH: 4,
        ActionType.INVESTIGATE: 3,
        ActionType.TRANSFER_REVIEW: 2,
        ActionType.MONITOR: 1,
        ActionType.NO_ACTION: 0,
    }

    base = max(recommendations, key=lambda r: (
        _PRIORITY_ORDER.index(r.priority),
        _action_rank.get(r.action_type, 0),
    ))

    # Merge secondary reasons
    all_reasons = "; ".join(r.reason for r in recommendations if r.reason != base.reason)
    merged_reason = base.reason
    if all_reasons:
        merged_reason += f" | Additional signals: {all_reasons}"

    merged_detail = {}
    for r in recommendations:
        merged_detail.update(r.detail)

    return ActionRecommendation(
        org_id=base.org_id,
        store_id=base.store_id,
        product_id=base.product_id,
        sku=base.sku,
        store_code=base.store_code,
        action_type=base.action_type,
        priority=base.priority,
        reason=merged_reason,
        detail=merged_detail,
        reconciliation_severity=next(
            (r.reconciliation_severity for r in recommendations if r.reconciliation_severity), None
        ),
        perishable_tier=next(
            (r.perishable_tier for r in recommendations if r.perishable_tier), None
        ),
        stock_vs_reorder=next(
            (r.stock_vs_reorder for r in recommendations if r.stock_vs_reorder), None
        ),
    )


# --------------------------------------------------------------------------- #
# Main Engine                                                                  #
# --------------------------------------------------------------------------- #

def generate_recommendations(
    reconciliation_reports: list[ProductReconciliationReport] | None = None,
    perishable_reports: list[ProductPerishableReport] | None = None,
    stock_levels: list[dict] | None = None,
) -> list[ActionRecommendation]:
    """
    Generate ActionRecommendation objects from multiple signal sources.

    Args:
        reconciliation_reports: List of ProductReconciliationReport from Phase 5.
        perishable_reports: List of ProductPerishableReport from Phase 7.
        stock_levels: List of dicts with keys:
            {org_id, store_id, product_id, sku, store_code,
             current_sellable, reorder_level, target_stock}

    Returns:
        List of ActionRecommendation, one per store-product (merged if multiple signals).
        Sorted by priority (CRITICAL first), then by action_type rank.
    """
    # Collect per-product signals
    # Key: (store_id, product_id)
    signals: dict[tuple, list[ActionRecommendation]] = {}

    def add(key: tuple, rec: Optional[ActionRecommendation]):
        if rec is not None:
            signals.setdefault(key, []).append(rec)

    # Reconciliation signals
    for report in (reconciliation_reports or []):
        key = (report.store_id, report.product_id)
        add(key, _from_reconciliation(report))

    # Perishable signals
    for report in (perishable_reports or []):
        key = (report.store_id, report.product_id)
        add(key, _from_perishable(report))

    # Stock level signals
    for sl in (stock_levels or []):
        key = (sl["store_id"], sl["product_id"])
        add(key, _from_stock_level(
            org_id=sl["org_id"],
            store_id=sl["store_id"],
            product_id=sl["product_id"],
            sku=sl.get("sku", ""),
            store_code=sl.get("store_code", ""),
            current_sellable=Decimal(str(sl.get("current_sellable", 0))),
            reorder_level=Decimal(str(sl["reorder_level"])) if sl.get("reorder_level") else None,
            target_stock=Decimal(str(sl["target_stock"])) if sl.get("target_stock") else None,
        ))

    # Merge per product, fill NO_ACTION for products with only OK signals
    results = []
    for key, recs in signals.items():
        results.append(_merge_for_product(recs))

    # Sort: CRITICAL first, then by action rank
    _action_rank = {
        ActionType.PHYSICAL_COUNT: 6,
        ActionType.MARKDOWN_REVIEW: 5,
        ActionType.REPLENISH: 4,
        ActionType.INVESTIGATE: 3,
        ActionType.TRANSFER_REVIEW: 2,
        ActionType.MONITOR: 1,
        ActionType.NO_ACTION: 0,
    }
    results.sort(
        key=lambda r: (
            _PRIORITY_ORDER.index(r.priority) * -1,
            _action_rank.get(r.action_type, 0) * -1,
        )
    )

    return results


def filter_recommendations(
    recommendations: list[ActionRecommendation],
    min_priority: Priority = Priority.MEDIUM,
) -> list[ActionRecommendation]:
    """Return only recommendations at or above the given priority."""
    min_idx = _PRIORITY_ORDER.index(min_priority)
    return [r for r in recommendations if _PRIORITY_ORDER.index(r.priority) >= min_idx]

