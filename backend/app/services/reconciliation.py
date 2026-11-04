"""
StockSignal Inventory Reconciliation Engine — Phase 5.

Compares ledger-reported stock against physical count observations and
produces a structured discrepancy report with:
  - Quantity gaps per bucket.
  - Anomaly severity scoring (LOW / MEDIUM / HIGH / CRITICAL).
  - Suggested action type (PHYSICAL_COUNT, INVESTIGATE, CORRECT, OK).
  - Discrepancy classification (SHRINK, PHANTOM, MISCLASSIFY, SUPPLIER_SHORT, SYSTEM_ERROR).

Also provides a PhysicalCountWorkflow that tracks physical count sessions:
  OPEN → COUNTED → REVIEWED → COMMITTED.

All arithmetic uses Decimal for exact financial precision.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.core import (
    BucketType,
    InventoryMovement,
    InventoryMovementPosting,
    MovementType,
    Organization,
    Product,
    Store,
    StoreProduct,
)
from app.services.ledger import get_stock_balance


# --------------------------------------------------------------------------- #
# Enums & Constants                                                            #
# --------------------------------------------------------------------------- #

class AnomalySeverity(str, Enum):
    OK = "OK"
    LOW = "LOW"          # < 2% gap
    MEDIUM = "MEDIUM"    # 2–10% gap
    HIGH = "HIGH"        # 10–25% gap
    CRITICAL = "CRITICAL"  # > 25% gap or absolute > threshold


class DiscrepancyClass(str, Enum):
    NONE = "NONE"
    SHRINK = "SHRINK"               # Ledger > physical (unrecorded losses)
    PHANTOM = "PHANTOM"             # Ledger < physical (unrecorded gains / ghost stock)
    MISCLASSIFICATION = "MISCLASSIFICATION"  # Wrong bucket, correct total
    SUPPLIER_SHORT = "SUPPLIER_SHORT"   # Supplier under-delivered
    SYSTEM_ERROR = "SYSTEM_ERROR"       # Unexplained data error


class SuggestedAction(str, Enum):
    OK = "OK"
    MONITOR = "MONITOR"
    INVESTIGATE = "INVESTIGATE"
    PHYSICAL_COUNT = "PHYSICAL_COUNT"
    CORRECT = "CORRECT"
    ESCALATE = "ESCALATE"


# Thresholds
CRITICAL_ABSOLUTE_THRESHOLD = Decimal("50.000")   # units
HIGH_ABS_THRESHOLD = Decimal("20.000")
LOW_PCT_THRESHOLD = Decimal("0.02")               # 2%
MEDIUM_PCT_THRESHOLD = Decimal("0.10")            # 10%
HIGH_PCT_THRESHOLD = Decimal("0.25")              # 25%


# --------------------------------------------------------------------------- #
# Data Structures                                                               #
# --------------------------------------------------------------------------- #

@dataclass
class BucketDiscrepancy:
    bucket: BucketType
    ledger_qty: Decimal
    physical_qty: Decimal
    gap: Decimal                    # physical - ledger (negative = shrinkage)
    gap_pct: Decimal                # relative to ledger, or 0 if ledger is 0
    severity: AnomalySeverity
    classification: DiscrepancyClass


@dataclass
class ProductReconciliationReport:
    """Full discrepancy report for one store-product combination."""
    org_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    store_code: str
    reconciled_at: datetime
    buckets: list[BucketDiscrepancy]
    overall_severity: AnomalySeverity
    overall_classification: DiscrepancyClass
    suggested_action: SuggestedAction
    total_ledger_physical: Decimal   # sum of all on-site ledger buckets
    total_physical_count: Decimal    # sum of all physical count buckets
    total_gap: Decimal
    notes: str = ""


@dataclass
class PhysicalCountEntry:
    """One counted line in a physical count session."""
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    sku: str = ""
    bucket: BucketType = BucketType.SELLABLE
    counted_qty: Decimal = Decimal("0.000")
    counted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    counted_by: str = ""


class CountSessionStatus(str, Enum):
    OPEN = "OPEN"
    COUNTED = "COUNTED"
    REVIEWED = "REVIEWED"
    COMMITTED = "COMMITTED"


@dataclass
class PhysicalCountSession:
    """A physical inventory count session for one store."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    store_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: CountSessionStatus = CountSessionStatus.OPEN
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    entries: list[PhysicalCountEntry] = field(default_factory=list)
    report: Optional[list[ProductReconciliationReport]] = None


# --------------------------------------------------------------------------- #
# Scoring Helpers                                                               #
# --------------------------------------------------------------------------- #

def _compute_gap_pct(ledger_qty: Decimal, gap: Decimal) -> Decimal:
    """Gap as a fraction of ledger qty. Returns 0 if ledger is zero."""
    if ledger_qty == Decimal("0.000"):
        return Decimal("0.000")
    return (gap / ledger_qty).quantize(Decimal("0.0001"))


def _classify_severity(gap: Decimal, ledger_qty: Decimal) -> AnomalySeverity:
    """Map magnitude of gap to a severity tier."""
    abs_gap = abs(gap)
    if abs_gap == Decimal("0.000"):
        return AnomalySeverity.OK
    if ledger_qty > Decimal("0.000"):
        pct = abs(gap / ledger_qty)
        if pct < LOW_PCT_THRESHOLD and abs_gap < HIGH_ABS_THRESHOLD:
            return AnomalySeverity.LOW
        if pct < MEDIUM_PCT_THRESHOLD:
            return AnomalySeverity.MEDIUM
        if pct < HIGH_PCT_THRESHOLD and abs_gap < CRITICAL_ABSOLUTE_THRESHOLD:
            return AnomalySeverity.HIGH
    if abs_gap >= CRITICAL_ABSOLUTE_THRESHOLD:
        return AnomalySeverity.CRITICAL
    return AnomalySeverity.HIGH


def _classify_discrepancy(
    buckets: list[BucketDiscrepancy],
    total_ledger: Decimal,
    total_physical: Decimal,
) -> DiscrepancyClass:
    """Infer the most likely cause of the discrepancy."""
    total_gap = total_physical - total_ledger
    if total_gap == Decimal("0.000") and all(b.gap == Decimal("0.000") for b in buckets):
        return DiscrepancyClass.NONE

    # If buckets individually have discrepancies but net is zero → misclassification
    bucket_gaps = [b.gap for b in buckets if b.gap != Decimal("0.000")]
    if total_gap == Decimal("0.000") and bucket_gaps:
        return DiscrepancyClass.MISCLASSIFICATION

    # Net negative gap (ledger shows more than physical) → shrinkage
    if total_gap < Decimal("0.000"):
        return DiscrepancyClass.SHRINK

    # Net positive gap (physical shows more than ledger) → phantom stock
    if total_gap > Decimal("0.000"):
        return DiscrepancyClass.PHANTOM

    return DiscrepancyClass.SYSTEM_ERROR


_SEVERITY_ORDER = [
    AnomalySeverity.OK,
    AnomalySeverity.LOW,
    AnomalySeverity.MEDIUM,
    AnomalySeverity.HIGH,
    AnomalySeverity.CRITICAL,
]


def _max_severity(*severities: AnomalySeverity) -> AnomalySeverity:
    return max(severities, key=lambda s: _SEVERITY_ORDER.index(s))


def _suggest_action(
    severity: AnomalySeverity,
    classification: DiscrepancyClass,
) -> SuggestedAction:
    if severity == AnomalySeverity.OK:
        return SuggestedAction.OK
    if severity == AnomalySeverity.LOW:
        return SuggestedAction.MONITOR
    if severity == AnomalySeverity.MEDIUM:
        return SuggestedAction.INVESTIGATE
    if severity == AnomalySeverity.HIGH:
        if classification == DiscrepancyClass.MISCLASSIFICATION:
            return SuggestedAction.CORRECT
        return SuggestedAction.PHYSICAL_COUNT
    # CRITICAL
    return SuggestedAction.ESCALATE


# --------------------------------------------------------------------------- #
# Core Reconciliation                                                           #
# --------------------------------------------------------------------------- #

def reconcile_product(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    physical_counts: dict[BucketType, Decimal],
    sku: str = "",
    store_code: str = "",
) -> ProductReconciliationReport:
    """
    Reconcile a single store-product against provided physical counts.

    `physical_counts` maps BucketType to the physically counted quantity.
    Only SELLABLE and QUARANTINED are required; others default to ledger value
    (i.e. assumed correct if not counted).
    """
    bal = get_stock_balance(db, org_id, store_id, product_id)

    ledger_by_bucket = {
        BucketType.SELLABLE: bal.sellable,
        BucketType.QUARANTINED: bal.quarantined,
    }
    on_site_buckets = [BucketType.SELLABLE, BucketType.QUARANTINED]

    bucket_reports: list[BucketDiscrepancy] = []
    for bucket in on_site_buckets:
        ledger_qty = ledger_by_bucket.get(bucket, Decimal("0.000"))
        physical_qty = physical_counts.get(bucket, ledger_qty)  # assume correct if not provided
        gap = physical_qty - ledger_qty
        gap_pct = _compute_gap_pct(ledger_qty, gap)
        severity = _classify_severity(gap, ledger_qty)

        bucket_reports.append(BucketDiscrepancy(
            bucket=bucket,
            ledger_qty=ledger_qty,
            physical_qty=physical_qty,
            gap=gap,
            gap_pct=gap_pct,
            severity=severity,
            classification=DiscrepancyClass.NONE,  # filled at product level
        ))

    total_ledger = sum(b.ledger_qty for b in bucket_reports)
    total_physical = sum(b.physical_qty for b in bucket_reports)
    total_gap = total_physical - total_ledger

    overall_classification = _classify_discrepancy(bucket_reports, total_ledger, total_physical)
    overall_severity = _max_severity(*[b.severity for b in bucket_reports])
    suggested_action = _suggest_action(overall_severity, overall_classification)

    # Back-fill per-bucket classification
    for br in bucket_reports:
        if br.gap != Decimal("0.000"):
            br.classification = overall_classification

    return ProductReconciliationReport(
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        sku=sku,
        store_code=store_code,
        reconciled_at=datetime.now(timezone.utc),
        buckets=bucket_reports,
        overall_severity=overall_severity,
        overall_classification=overall_classification,
        suggested_action=suggested_action,
        total_ledger_physical=total_ledger,
        total_physical_count=total_physical,
        total_gap=total_gap,
    )


def reconcile_store(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    physical_counts: dict[uuid.UUID, dict[BucketType, Decimal]],
) -> list[ProductReconciliationReport]:
    """
    Reconcile all provided products for a store.

    `physical_counts` maps product_id → {BucketType: Decimal}.
    Products not in physical_counts are skipped.
    """
    # Fetch store code and product SKUs for reporting
    store = db.execute(
        select(Store).where(Store.organization_id == org_id, Store.id == store_id)
    ).scalar_one_or_none()
    store_code = store.store_code if store else str(store_id)

    results = []
    for product_id, counts in physical_counts.items():
        product = db.execute(
            select(Product).where(Product.organization_id == org_id, Product.id == product_id)
        ).scalar_one_or_none()
        sku = product.sku if product else str(product_id)

        report = reconcile_product(
            db, org_id, store_id, product_id, counts,
            sku=sku, store_code=store_code,
        )
        results.append(report)

    return results


def flag_anomalies(
    reports: list[ProductReconciliationReport],
    min_severity: AnomalySeverity = AnomalySeverity.MEDIUM,
) -> list[ProductReconciliationReport]:
    """Filter reports to only those at or above a given severity threshold."""
    order = _SEVERITY_ORDER
    min_idx = order.index(min_severity)
    return [r for r in reports if order.index(r.overall_severity) >= min_idx]


# --------------------------------------------------------------------------- #
# Physical Count Workflow                                                       #
# --------------------------------------------------------------------------- #

class PhysicalCountWorkflow:
    """
    Manages a physical count session lifecycle:
      OPEN → COUNTED → REVIEWED → COMMITTED
    """

    def __init__(self, db: Session, org_id: uuid.UUID, store_id: uuid.UUID):
        self.db = db
        self.session = PhysicalCountSession(
            org_id=org_id,
            store_id=store_id,
        )

    def add_count(
        self,
        product_id: uuid.UUID,
        sku: str,
        bucket: BucketType,
        counted_qty: Decimal,
        counted_by: str = "STOCK_TAKER",
    ) -> PhysicalCountEntry:
        """Add a counted line to the open session."""
        if self.session.status != CountSessionStatus.OPEN:
            raise ValueError(f"Cannot add counts to a {self.session.status} session")
        entry = PhysicalCountEntry(
            product_id=product_id,
            sku=sku,
            bucket=bucket,
            counted_qty=counted_qty,
            counted_by=counted_by,
        )
        self.session.entries.append(entry)
        return entry

    def close_count(self) -> "PhysicalCountWorkflow":
        """Mark the session as fully counted and ready for review."""
        if self.session.status != CountSessionStatus.OPEN:
            raise ValueError("Session is not OPEN")
        self.session.status = CountSessionStatus.COUNTED
        return self

    def review(self) -> list[ProductReconciliationReport]:
        """
        Generate reconciliation reports for all counted products.
        Moves session to REVIEWED status.
        """
        if self.session.status not in (CountSessionStatus.COUNTED, CountSessionStatus.REVIEWED):
            raise ValueError(f"Cannot review a {self.session.status} session")

        # Group entries by product_id
        physical: dict[uuid.UUID, dict[BucketType, Decimal]] = {}
        for entry in self.session.entries:
            physical.setdefault(entry.product_id, {})[entry.bucket] = entry.counted_qty

        reports = reconcile_store(
            self.db,
            self.session.org_id,
            self.session.store_id,
            physical,
        )
        self.session.report = reports
        self.session.status = CountSessionStatus.REVIEWED
        return reports

    def commit(self) -> CountSessionStatus:
        """
        Mark session as committed (reports accepted by manager).
        In a production system, this would trigger adjustment movements.
        """
        if self.session.status != CountSessionStatus.REVIEWED:
            raise ValueError("Session must be REVIEWED before commit")
        self.session.status = CountSessionStatus.COMMITTED
        self.session.closed_at = datetime.now(timezone.utc)
        return self.session.status

