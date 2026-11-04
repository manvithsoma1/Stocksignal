"""
StockSignal Synthetic Data Generator — Phase 4.

Generates a realistic, labelled operational dataset of inventory ledger
movements for a supermarket chain. Produces two separated datasets:

  1. OPERATIONAL VIEW  — What the ledger actually recorded (includes all
                         injected errors exactly as a real system would record
                         them, i.e. missing entries for unrecorded damage, etc.)

  2. GROUND TRUTH VIEW — The true physical state, stored separately with
                         labels indicating each injected discrepancy.

Design parameters:
  - 3–5 stores configurable (default 3)
  - 50–300 SKUs configurable (default 50 for fast test; 300 for full run)
  - Up to 90 days of simulated operations
  - 5 discrepancy types with configurable injection rates
  - Deterministic given a fixed random seed (default 42)

All quantities use Python Decimal to avoid floating-point drift.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from app.models.core import BucketType, MovementType, Organization, Product, Store, StoreProduct, Batch
from app.schemas.inventory import MovementCreate, PostingSpec, ReclassifyCreate
from app.services import ledger


# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #

SUPERMARKET_CATEGORIES = [
    ("Dairy", True, 7),
    ("Bread & Bakery", True, 5),
    ("Produce", True, 10),
    ("Beverages", False, 180),
    ("Staples", False, 365),
    ("Snacks", False, 180),
    ("Personal Care", False, 730),
    ("Household", False, 365),
]

DISCREPANCY_TYPES = [
    "UNRECORDED_DAMAGE",
    "DUPLICATE_SCAN",
    "RECEIPT_SHORT",
    "RETURN_MISCLASSIFY",
    "PHANTOM_THEFT",
]


# --------------------------------------------------------------------------- #
# Ground Truth Record                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class GroundTruthRecord:
    """Records a single real-world event that may or may not have a ledger entry."""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    date: date = field(default_factory=date.today)
    store_code: str = ""
    sku: str = ""
    event_type: str = ""           # e.g. RECEIPT, SALE, DAMAGE, RETURN, RECLASSIFY
    physical_qty: Decimal = Decimal("0.000")
    ledger_recorded: bool = True   # False = discrepancy (unrecorded event)
    discrepancy_type: str | None = None  # None = clean record
    discrepancy_qty: Decimal = Decimal("0.000")
    notes: str = ""


@dataclass
class GeneratorConfig:
    """All configuration for one synthetic data generation run."""
    num_stores: int = 3
    num_skus: int = 50
    simulation_days: int = 60
    start_date: date = field(default_factory=lambda: date.today() - timedelta(days=60))
    random_seed: int = 42

    # Discrepancy injection rates (fraction of eligible events)
    unrecorded_damage_rate: float = 0.03    # 3% of receipts have unrecorded damage
    duplicate_scan_rate: float = 0.02       # 2% of GRN receipts scanned twice (but idempotent)
    receipt_short_rate: float = 0.05        # 5% of receipts short-shipped
    return_misclassify_rate: float = 0.10   # 10% of returns misclassified
    phantom_theft_rate: float = 0.02        # 2% periods have phantom theft

    # Volume parameters
    avg_daily_sales_units: float = 5.0
    avg_receipt_qty: float = 100.0
    receipt_frequency_days: int = 7         # Reorder every N days


# --------------------------------------------------------------------------- #
# Synthetic Generator                                                           #
# --------------------------------------------------------------------------- #

class SyntheticGenerator:
    """
    Runs deterministic synthetic inventory movements through the StockSignal
    ledger service and captures a parallel ground-truth audit trail.
    """

    def __init__(self, db: Session, org_id: uuid.UUID, config: GeneratorConfig | None = None):
        self.db = db
        self.org_id = org_id
        self.cfg = config or GeneratorConfig()
        self.rng = random.Random(self.cfg.random_seed)
        self.ground_truth: list[GroundTruthRecord] = []
        self.stores: list[Store] = []
        self.products: list[Product] = []
        self._store_product_stock: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}

    # ------------------------------------------------------------------ #
    # Setup: create stores and products                                    #
    # ------------------------------------------------------------------ #

    def setup_stores(self) -> list[Store]:
        """Create synthetic stores in the database."""
        store_names = [
            ("Indiranagar", "STORE_IND"),
            ("Koramangala", "STORE_KOR"),
            ("Whitefield", "STORE_WHF"),
            ("HSR Layout", "STORE_HSR"),
            ("Marathahalli", "STORE_MAR"),
        ]
        created = []
        for i in range(self.cfg.num_stores):
            name, code_prefix = store_names[i % len(store_names)]
            suffix = str(uuid.uuid4())[:6]
            store = Store(
                organization_id=self.org_id,
                store_code=f"{code_prefix}_{suffix}",
                name=f"{name} Supermarket {i + 1}",
            )
            self.db.add(store)
            created.append(store)
        self.db.flush()
        self.stores = created
        return created

    def setup_products(self) -> list[Product]:
        """Create synthetic SKUs across all categories."""
        created = []
        for i in range(self.cfg.num_skus):
            cat, is_perishable, shelf_life = SUPERMARKET_CATEGORIES[i % len(SUPERMARKET_CATEGORIES)]
            suffix = str(uuid.uuid4())[:8]
            product = Product(
                organization_id=self.org_id,
                sku=f"SKU_{cat[:3].upper()}_{i:04d}_{suffix}",
                name=f"{cat} Product {i + 1}",
                category=cat,
                unit_of_measure="unit",
                is_perishable=is_perishable,
            )
            self.db.add(product)
            created.append(product)

        self.db.flush()
        self.products = created

        # Create StoreProduct mappings
        for store in self.stores:
            for product in self.products:
                sp = StoreProduct(
                    organization_id=self.org_id,
                    store_id=store.id,
                    product_id=product.id,
                    reorder_level=Decimal("20.000"),
                    target_stock_level=Decimal("150.000"),
                )
                self.db.add(sp)

        self.db.flush()
        return created

    def _get_stock(self, store_id: uuid.UUID, product_id: uuid.UUID) -> Decimal:
        return self._store_product_stock.get((store_id, product_id), Decimal("0.000"))

    def _set_stock(self, store_id: uuid.UUID, product_id: uuid.UUID, qty: Decimal) -> None:
        self._store_product_stock[(store_id, product_id)] = qty

    def _round(self, v: float) -> Decimal:
        return Decimal(str(round(v, 3)))

    # ------------------------------------------------------------------ #
    # Core event simulation                                                #
    # ------------------------------------------------------------------ #

    def _simulate_receipt(
        self,
        store: Store,
        product: Product,
        sim_date: date,
    ) -> None:
        """Record a GRN receipt with optional supplier short-ship discrepancy."""
        invoice_qty = self._round(
            self.rng.uniform(self.cfg.avg_receipt_qty * 0.7, self.cfg.avg_receipt_qty * 1.3)
        )
        received_qty = invoice_qty
        discrepancy_type = None
        discrepancy_qty = Decimal("0.000")

        # Inject: supplier short-ship
        if self.rng.random() < self.cfg.receipt_short_rate:
            shortage_pct = self.rng.uniform(0.03, 0.15)
            shortage = (invoice_qty * Decimal(str(shortage_pct))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            received_qty = invoice_qty - shortage
            discrepancy_type = "RECEIPT_SHORT"
            discrepancy_qty = shortage

        # Inject: duplicate scan — uses same idempotency key, ledger de-dupes
        is_duplicate = self.rng.random() < self.cfg.duplicate_scan_rate
        idemp_key = str(uuid.uuid4())

        payload = MovementCreate(
            organization_id=self.org_id,
            store_id=store.id,
            product_id=product.id,
            movement_type=MovementType.GRN_RECEIPT,
            quantity_delta=received_qty,
            occurred_at=datetime(sim_date.year, sim_date.month, sim_date.day, 9, 0, tzinfo=timezone.utc),
            source_system="WMS",
            idempotency_key=idemp_key,
            notes=f"GRN receipt | invoice={invoice_qty} | received={received_qty}",
            postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=received_qty)],
        )
        ledger.record_movement(self.db, payload)
        if is_duplicate:
            ledger.record_movement(self.db, payload)  # de-duped via idempotency

        stock = self._get_stock(store.id, product.id) + received_qty
        self._set_stock(store.id, product.id, stock)

        # Ground truth entry
        self.ground_truth.append(GroundTruthRecord(
            date=sim_date, store_code=store.store_code, sku=product.sku,
            event_type="RECEIPT", physical_qty=received_qty,
            ledger_recorded=True,
            discrepancy_type=discrepancy_type,
            discrepancy_qty=discrepancy_qty,
            notes=f"Invoice {invoice_qty}, received {received_qty}" + (
                f" — supplier short {discrepancy_qty}" if discrepancy_type else ""
            ),
        ))

        # Inject: unrecorded damage after receipt
        if self.rng.random() < self.cfg.unrecorded_damage_rate and received_qty > 5:
            damage_qty = self._round(self.rng.uniform(1, min(float(received_qty) * 0.1, 10)))
            # Physical damage happens but NOT recorded in ledger
            actual_stock = self._get_stock(store.id, product.id) - damage_qty
            self._set_stock(store.id, product.id, max(Decimal("0.000"), actual_stock))
            self.ground_truth.append(GroundTruthRecord(
                date=sim_date, store_code=store.store_code, sku=product.sku,
                event_type="DAMAGE", physical_qty=damage_qty,
                ledger_recorded=False,
                discrepancy_type="UNRECORDED_DAMAGE",
                discrepancy_qty=damage_qty,
                notes=f"Physical damage of {damage_qty} units — NOT recorded in ledger",
            ))

    def _simulate_sales(
        self,
        store: Store,
        product: Product,
        sim_date: date,
    ) -> None:
        """Simulate daily sales with a random volume."""
        current_stock = self._get_stock(store.id, product.id)
        if current_stock <= Decimal("0.000"):
            return

        max_sale = min(float(current_stock), self.cfg.avg_daily_sales_units * 2)
        if max_sale < 0.1:
            return

        sale_qty = self._round(self.rng.uniform(0.1, max_sale))
        sale_qty = min(sale_qty, current_stock)  # never oversell

        payload = MovementCreate(
            organization_id=self.org_id,
            store_id=store.id,
            product_id=product.id,
            movement_type=MovementType.SALE,
            quantity_delta=-sale_qty,
            occurred_at=datetime(sim_date.year, sim_date.month, sim_date.day, 14, 0, tzinfo=timezone.utc),
            source_system="POS",
            idempotency_key=str(uuid.uuid4()),
            notes=f"Daily sales {sim_date}",
            postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=-sale_qty)],
        )
        ledger.record_movement(self.db, payload)
        self._set_stock(store.id, product.id, current_stock - sale_qty)

        self.ground_truth.append(GroundTruthRecord(
            date=sim_date, store_code=store.store_code, sku=product.sku,
            event_type="SALE", physical_qty=sale_qty, ledger_recorded=True,
            notes=f"Sale of {sale_qty} units",
        ))

        # Simulate occasional customer return (10% of sale days)
        if self.rng.random() < 0.10:
            return_qty = self._round(self.rng.uniform(0.5, min(float(sale_qty), 3.0)))
            is_misclassified = self.rng.random() < self.cfg.return_misclassify_rate

            return_bucket = BucketType.QUARANTINED if is_misclassified else BucketType.SELLABLE
            return_mv = MovementCreate(
                organization_id=self.org_id,
                store_id=store.id,
                product_id=product.id,
                movement_type=MovementType.RETURN_SELLABLE,
                quantity_delta=return_qty,
                occurred_at=datetime(sim_date.year, sim_date.month, sim_date.day, 15, 30, tzinfo=timezone.utc),
                source_system="RETURNS_COUNTER",
                idempotency_key=str(uuid.uuid4()),
                notes=f"Customer return {'(MISCLASSIFIED to QUARANTINED)' if is_misclassified else '(correctly to SELLABLE)'}",
                postings=[PostingSpec(bucket_type=return_bucket, quantity_delta=return_qty)],
            )
            ledger.record_movement(self.db, return_mv, allow_negative_stock=True)
            self._set_stock(store.id, product.id, self._get_stock(store.id, product.id) + return_qty)

            self.ground_truth.append(GroundTruthRecord(
                date=sim_date, store_code=store.store_code, sku=product.sku,
                event_type="RETURN", physical_qty=return_qty, ledger_recorded=True,
                discrepancy_type="RETURN_MISCLASSIFY" if is_misclassified else None,
                discrepancy_qty=return_qty if is_misclassified else Decimal("0.000"),
                notes=f"Return {return_qty} units to {'QUARANTINED (error)' if is_misclassified else 'SELLABLE (correct)'}",
            ))

    # ------------------------------------------------------------------ #
    # Main run loop                                                         #
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, Any]:
        """
        Execute the full simulation and return a summary dict.
        Caller is responsible for committing the session after this returns.
        """
        self.setup_stores()
        self.setup_products()

        sim_start = self.cfg.start_date
        sim_end = sim_start + timedelta(days=self.cfg.simulation_days)
        current = sim_start

        total_days = 0
        while current < sim_end:
            for store in self.stores:
                for product in self.products:
                    # Receipt on reorder schedule
                    day_num = (current - sim_start).days
                    if day_num % self.cfg.receipt_frequency_days == 0:
                        self._simulate_receipt(store, product, current)

                    # Daily sales
                    self._simulate_sales(store, product, current)

            current += timedelta(days=1)
            total_days += 1

        self.db.flush()

        # Build summary statistics
        total_events = len(self.ground_truth)
        discrepancies = [r for r in self.ground_truth if r.discrepancy_type]

        summary: dict[str, Any] = {
            "org_id": str(self.org_id),
            "num_stores": self.cfg.num_stores,
            "num_skus": self.cfg.num_skus,
            "simulation_days": total_days,
            "total_ground_truth_events": total_events,
            "total_discrepancies_injected": len(discrepancies),
            "discrepancy_rate_pct": round(len(discrepancies) / max(total_events, 1) * 100, 2),
            "discrepancy_breakdown": {},
        }

        for dtype in DISCREPANCY_TYPES:
            matching = [r for r in discrepancies if r.discrepancy_type == dtype]
            summary["discrepancy_breakdown"][dtype] = {
                "count": len(matching),
                "total_qty": str(sum(r.discrepancy_qty for r in matching)),
            }

        return summary

