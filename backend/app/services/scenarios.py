"""
StockSignal Deterministic Evaluation Scenarios — Phase 3.

Provides five canonical discrepancy injection scenarios for evaluation and
demonstration:

  1. CORRECT_STOCK      — Perfectly balanced operations; no discrepancy.
  2. UNRECORDED_DAMAGE  — Physical damage not entered in the ledger.
  3. DUPLICATE_SCAN     — Same GRN document scanned twice (duplicate transaction).
  4. RECEIPT_DISCREPANCY— Supplier invoice says 100 units, only 90 received.
  5. RETURN_MISCLASSIFY — Sellable customer return incorrectly sent to QUARANTINED.

Each scenario returns a dict describing:
  - What was recorded in the ledger.
  - What the ground truth should be.
  - What discrepancy exists (if any).

These scenarios are used by integration tests and by the synthetic data
generator to inject labelled ground-truth errors.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.core import BucketType, MovementType
from app.schemas.inventory import MovementCreate, PostingSpec, ReclassifyCreate
from app.services import ledger


# --------------------------------------------------------------------------- #
# Data Classes                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class LedgerState:
    """Observed inventory bucket state derived from the ledger."""
    sellable: Decimal = Decimal("0.000")
    quarantined: Decimal = Decimal("0.000")
    physical: Decimal = Decimal("0.000")


@dataclass
class GroundTruth:
    """Actual physical state that should exist if operations were perfect."""
    sellable: Decimal = Decimal("0.000")
    quarantined: Decimal = Decimal("0.000")
    physical: Decimal = Decimal("0.000")
    notes: str = ""


@dataclass
class Discrepancy:
    """Computed gap between ledger and ground truth."""
    sellable_delta: Decimal = Decimal("0.000")
    quarantined_delta: Decimal = Decimal("0.000")
    physical_delta: Decimal = Decimal("0.000")
    has_discrepancy: bool = False
    scenario_type: str = ""
    description: str = ""


@dataclass
class ScenarioResult:
    """Full output of one evaluation scenario."""
    scenario_name: str
    org_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    movements_recorded: list[uuid.UUID]
    ledger_state: LedgerState
    ground_truth: GroundTruth
    discrepancy: Discrepancy
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Scenario Builder                                                              #
# --------------------------------------------------------------------------- #

def _ts() -> datetime:
    return datetime.now(timezone.utc)


def _mk_payload(
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    movement_type: MovementType,
    quantity_delta: Decimal,
    bucket: BucketType,
    source_system: str = "SCENARIO_ENGINE",
    batch_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> MovementCreate:
    return MovementCreate(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        batch_id=batch_id,
        movement_type=movement_type,
        quantity_delta=quantity_delta,
        occurred_at=_ts(),
        source_system=source_system,
        idempotency_key=str(uuid.uuid4()),
        notes=notes,
        postings=[PostingSpec(bucket_type=bucket, quantity_delta=quantity_delta)],
    )


def _read_balance(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
) -> LedgerState:
    bal = ledger.get_stock_balance(db, org_id, store_id, product_id)
    return LedgerState(
        sellable=bal.sellable,
        quarantined=bal.quarantined,
        physical=bal.physical,
    )


def _compute_discrepancy(
    observed: LedgerState,
    truth: GroundTruth,
    scenario_type: str,
    description: str,
) -> Discrepancy:
    s_delta = truth.sellable - observed.sellable
    q_delta = truth.quarantined - observed.quarantined
    p_delta = truth.physical - observed.physical
    has = any(d != 0 for d in (s_delta, q_delta, p_delta))
    return Discrepancy(
        sellable_delta=s_delta,
        quarantined_delta=q_delta,
        physical_delta=p_delta,
        has_discrepancy=has,
        scenario_type=scenario_type,
        description=description,
    )


# --------------------------------------------------------------------------- #
# Scenario 1: Correct Stock                                                   #
# --------------------------------------------------------------------------- #

def scenario_correct_stock(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    receipt_qty: Decimal = Decimal("100.000"),
    sale_qty: Decimal = Decimal("30.000"),
) -> ScenarioResult:
    """
    CORRECT_STOCK: A GRN receipt followed by a sale, fully reconciled.
    Ledger should exactly match ground truth.
    """
    movement_ids = []

    # Receipt
    mv = ledger.record_movement(db, _mk_payload(
        org_id, store_id, product_id,
        MovementType.GRN_RECEIPT, receipt_qty, BucketType.SELLABLE,
        notes=f"GRN receipt of {receipt_qty} units",
    ))
    db.commit()
    movement_ids.append(mv.id)

    # Sale
    mv2 = ledger.record_movement(db, _mk_payload(
        org_id, store_id, product_id,
        MovementType.SALE, -sale_qty, BucketType.SELLABLE,
        source_system="POS",
        notes=f"Sale of {sale_qty} units",
    ))
    db.commit()
    movement_ids.append(mv2.id)

    expected_sellable = receipt_qty - sale_qty
    observed = _read_balance(db, org_id, store_id, product_id)
    truth = GroundTruth(
        sellable=expected_sellable,
        quarantined=Decimal("0.000"),
        physical=expected_sellable,
        notes="Perfectly balanced receipt and sale",
    )

    return ScenarioResult(
        scenario_name="CORRECT_STOCK",
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movements_recorded=movement_ids,
        ledger_state=observed,
        ground_truth=truth,
        discrepancy=_compute_discrepancy(
            observed, truth,
            "NONE", "No discrepancy — ledger matches physical reality",
        ),
    )


# --------------------------------------------------------------------------- #
# Scenario 2: Unrecorded Damage                                               #
# --------------------------------------------------------------------------- #

def scenario_unrecorded_damage(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    receipt_qty: Decimal = Decimal("100.000"),
    damage_qty: Decimal = Decimal("8.000"),
) -> ScenarioResult:
    """
    UNRECORDED_DAMAGE: Goods are physically damaged/destroyed but no ledger
    entry is made. Ledger shows more stock than actually exists on shelf.
    """
    movement_ids = []

    mv = ledger.record_movement(db, _mk_payload(
        org_id, store_id, product_id,
        MovementType.GRN_RECEIPT, receipt_qty, BucketType.SELLABLE,
        notes="GRN receipt",
    ))
    db.commit()
    movement_ids.append(mv.id)

    # Damage physically occurs but is NOT recorded in the ledger

    observed = _read_balance(db, org_id, store_id, product_id)
    # Ground truth: actual physical sellable is less by damage_qty
    truth = GroundTruth(
        sellable=receipt_qty - damage_qty,
        quarantined=Decimal("0.000"),
        physical=receipt_qty - damage_qty,
        notes=f"{damage_qty} units physically damaged but not recorded",
    )

    return ScenarioResult(
        scenario_name="UNRECORDED_DAMAGE",
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movements_recorded=movement_ids,
        ledger_state=observed,
        ground_truth=truth,
        discrepancy=_compute_discrepancy(
            observed, truth,
            "SHRINK",
            f"Ledger overstates SELLABLE by {damage_qty} units — unrecorded physical damage",
        ),
        metadata={"unrecorded_damage_qty": str(damage_qty)},
    )


# --------------------------------------------------------------------------- #
# Scenario 3: Duplicate Transaction (Double Scan)                             #
# --------------------------------------------------------------------------- #

def scenario_duplicate_scan(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    receipt_qty: Decimal = Decimal("50.000"),
) -> ScenarioResult:
    """
    DUPLICATE_SCAN: The same GRN document is scanned twice. Without idempotency
    the ledger would record double the received quantity.

    This scenario intentionally uses the same idempotency_key for both calls
    to demonstrate that the ledger correctly de-duplicates the transaction.
    """
    movement_ids = []
    shared_idempotency_key = f"GRN-DUPE-{uuid.uuid4()}"

    payload = MovementCreate(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movement_type=MovementType.GRN_RECEIPT,
        quantity_delta=receipt_qty,
        occurred_at=_ts(),
        source_system="WMS",
        idempotency_key=shared_idempotency_key,
        notes="GRN first scan",
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=receipt_qty)],
    )

    mv1 = ledger.record_movement(db, payload)
    db.commit()
    movement_ids.append(mv1.id)

    # Second scan with same idempotency key — should return existing movement
    mv2 = ledger.record_movement(db, payload)
    db.commit()
    assert mv1.id == mv2.id, "Idempotency failed: duplicate movement was inserted"

    observed = _read_balance(db, org_id, store_id, product_id)
    truth = GroundTruth(
        sellable=receipt_qty,
        quarantined=Decimal("0.000"),
        physical=receipt_qty,
        notes="Idempotency key correctly prevented double-recording",
    )

    return ScenarioResult(
        scenario_name="DUPLICATE_SCAN",
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movements_recorded=movement_ids,
        ledger_state=observed,
        ground_truth=truth,
        discrepancy=_compute_discrepancy(
            observed, truth,
            "NONE", "Idempotency key correctly de-duplicated the GRN scan",
        ),
        metadata={"shared_idempotency_key": shared_idempotency_key},
    )


# --------------------------------------------------------------------------- #
# Scenario 4: Receipt Quantity Discrepancy                                    #
# --------------------------------------------------------------------------- #

def scenario_receipt_discrepancy(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    invoice_qty: Decimal = Decimal("100.000"),
    actual_received_qty: Decimal = Decimal("90.000"),
) -> ScenarioResult:
    """
    RECEIPT_DISCREPANCY: Supplier invoice claims 100 units but the receiver
    only physically counts 90. The WMS operator records the actual received
    quantity (90). Ledger is correct; ground truth shows 10-unit supplier
    shortage that should be flagged for supplier credit/dispute.
    """
    movement_ids = []

    mv = ledger.record_movement(db, _mk_payload(
        org_id, store_id, product_id,
        MovementType.GRN_RECEIPT, actual_received_qty, BucketType.SELLABLE,
        notes=f"GRN received {actual_received_qty} of {invoice_qty} invoiced (supplier short)",
    ))
    db.commit()
    movement_ids.append(mv.id)

    observed = _read_balance(db, org_id, store_id, product_id)
    # Ledger correctly reflects what was physically received
    truth = GroundTruth(
        sellable=actual_received_qty,
        quarantined=Decimal("0.000"),
        physical=actual_received_qty,
        notes=f"Ledger correctly records {actual_received_qty} received; invoice shows {invoice_qty}",
    )

    shortage = invoice_qty - actual_received_qty

    return ScenarioResult(
        scenario_name="RECEIPT_DISCREPANCY",
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movements_recorded=movement_ids,
        ledger_state=observed,
        ground_truth=truth,
        discrepancy=_compute_discrepancy(
            observed, truth,
            "NONE",
            "Ledger matches physical receipt; supplier short-shipped "
            f"{shortage} units vs invoice — raise credit note",
        ),
        metadata={
            "invoice_qty": str(invoice_qty),
            "actual_received_qty": str(actual_received_qty),
            "supplier_shortage": str(shortage),
        },
    )


# --------------------------------------------------------------------------- #
# Scenario 5: Return Misclassification                                        #
# --------------------------------------------------------------------------- #

def scenario_return_misclassify(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    receipt_qty: Decimal = Decimal("100.000"),
    return_qty: Decimal = Decimal("5.000"),
) -> ScenarioResult:
    """
    RETURN_MISCLASSIFY: A customer returns a perfectly sellable item but
    the warehouse operator incorrectly routes it to QUARANTINED instead of
    back to SELLABLE. This scenario records the error then fixes it via
    reclassification to demonstrate the correction workflow.

    State captured after the error (before fix):
      Ledger: SELLABLE = receipt - return_qty, QUARANTINED = return_qty
      Ground truth: SELLABLE = receipt (all returned units are sellable)
    """
    movement_ids = []

    # 1. Receipt
    mv = ledger.record_movement(db, _mk_payload(
        org_id, store_id, product_id,
        MovementType.GRN_RECEIPT, receipt_qty, BucketType.SELLABLE,
        notes="GRN receipt",
    ))
    db.commit()
    movement_ids.append(mv.id)

    # 2. Erroneous return — sends sellable return to QUARANTINED bucket
    mv_return = ledger.record_movement(db, MovementCreate(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movement_type=MovementType.RETURN_SELLABLE,
        quantity_delta=return_qty,
        occurred_at=_ts(),
        source_system="RETURNS_COUNTER",
        idempotency_key=str(uuid.uuid4()),
        notes=f"Customer return {return_qty} units — incorrectly routed to QUARANTINED",
        postings=[PostingSpec(bucket_type=BucketType.QUARANTINED, quantity_delta=return_qty)],
    ), allow_negative_stock=True)
    db.commit()
    movement_ids.append(mv_return.id)

    # Capture state BEFORE fix
    observed_before_fix = _read_balance(db, org_id, store_id, product_id)

    # Ground truth: the returned goods ARE sellable — they should be in SELLABLE
    truth = GroundTruth(
        sellable=receipt_qty + return_qty,
        quarantined=Decimal("0.000"),
        physical=receipt_qty + return_qty,
        notes=f"{return_qty} units wrongly classified as QUARANTINED; should be SELLABLE",
    )

    discrepancy = _compute_discrepancy(
        observed_before_fix, truth,
        "MISCLASSIFICATION",
        f"SELLABLE understated by {return_qty}; QUARANTINED overstated by {return_qty} — "
        "customer return incorrectly routed. Reclassify to correct.",
    )

    # 3. Demonstrate the correction: reclassify QUARANTINED → SELLABLE
    fix = ReclassifyCreate(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        from_bucket=BucketType.QUARANTINED,
        to_bucket=BucketType.SELLABLE,
        quantity=return_qty,
        idempotency_key=str(uuid.uuid4()),
        source_system="QA_RECLASSIFY",
        notes=f"Correcting misclassified return — {return_qty} units moved QUARANTINED → SELLABLE",
    )
    mv_fix = ledger.record_reclassify(db, fix)
    db.commit()
    movement_ids.append(mv_fix.id)

    return ScenarioResult(
        scenario_name="RETURN_MISCLASSIFY",
        org_id=org_id,
        store_id=store_id,
        product_id=product_id,
        movements_recorded=movement_ids,
        ledger_state=observed_before_fix,       # state when error was present
        ground_truth=truth,
        discrepancy=discrepancy,
        metadata={
            "discrepancy_detected_before_fix": True,
            "correction_movement_id": str(mv_fix.id),
            "post_fix_sellable": str(receipt_qty + return_qty),
        },
    )


# --------------------------------------------------------------------------- #
# Run All Scenarios                                                             #
# --------------------------------------------------------------------------- #

def run_all_scenarios(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_ids: dict[str, uuid.UUID],
) -> list[ScenarioResult]:
    """
    Execute all five evaluation scenarios against five distinct products
    so their ledger states don't interfere.

    `product_ids` should map scenario names to unique product UUIDs:
      {
        "CORRECT_STOCK": <uuid>,
        "UNRECORDED_DAMAGE": <uuid>,
        "DUPLICATE_SCAN": <uuid>,
        "RECEIPT_DISCREPANCY": <uuid>,
        "RETURN_MISCLASSIFY": <uuid>,
      }
    """
    return [
        scenario_correct_stock(db, org_id, store_id, product_ids["CORRECT_STOCK"]),
        scenario_unrecorded_damage(db, org_id, store_id, product_ids["UNRECORDED_DAMAGE"]),
        scenario_duplicate_scan(db, org_id, store_id, product_ids["DUPLICATE_SCAN"]),
        scenario_receipt_discrepancy(db, org_id, store_id, product_ids["RECEIPT_DISCREPANCY"]),
        scenario_return_misclassify(db, org_id, store_id, product_ids["RETURN_MISCLASSIFY"]),
    ]
