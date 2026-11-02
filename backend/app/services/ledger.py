"""
StockSignal Inventory Ledger Service.

Provides immutable double-entry movement recording, reversals,
bucket reclassifications, store-to-store transfers, and balance queries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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
from app.schemas.inventory import (
    BatchBalanceItem,
    BatchBalancesResponse,
    MovementCreate,
    PostingSpec,
    ReclassifyCreate,
    ReversalCreate,
    StockBalanceResponse,
    TransferCreate,
)


# --------------------------------------------------------------------------- #
# Custom Ledger Exceptions                                                    #
# --------------------------------------------------------------------------- #

class LedgerError(Exception):
    """Base class for ledger business logic errors."""


class InsufficientStockError(LedgerError):
    """Raised when an operation would drive a non-negative stock bucket below zero."""


class EntityNotFoundError(LedgerError):
    """Raised when a referenced organization, store, product, or batch does not exist."""


class MovementAlreadyReversedError(LedgerError):
    """Raised when an inventory movement has already been reversed."""


class DuplicateIdempotencyError(LedgerError):
    """Raised when an idempotency key is reused with incompatible movement details."""


class InvalidMovementError(LedgerError):
    """Raised when movement parameters violate ledger invariants."""


# --------------------------------------------------------------------------- #
# Core Ledger Operations                                                      #
# --------------------------------------------------------------------------- #

def get_stock_balance(
    db: Session,
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
) -> StockBalanceResponse:
    """Calculate the net stock balance per bucket for a store product."""
    stmt = (
        select(
            InventoryMovementPosting.bucket_type,
            func.coalesce(func.sum(InventoryMovementPosting.quantity_delta), Decimal("0.000")),
        )
        .join(
            InventoryMovement,
            InventoryMovement.id == InventoryMovementPosting.movement_id,
        )
        .where(
            InventoryMovement.organization_id == organization_id,
            InventoryMovement.store_id == store_id,
            InventoryMovement.product_id == product_id,
        )
        .group_by(InventoryMovementPosting.bucket_type)
    )
    rows = db.execute(stmt).all()
    bucket_map: dict[BucketType, Decimal] = {b: Decimal("0.000") for b in BucketType}
    for b_type, qty in rows:
        bucket_map[b_type] = Decimal(str(qty))

    # Physical stock includes on-site inventory buckets
    physical = (
        bucket_map[BucketType.SELLABLE]
        + bucket_map[BucketType.QUARANTINED]
    )

    return StockBalanceResponse(
        organization_id=organization_id,
        store_id=store_id,
        product_id=product_id,
        sellable=bucket_map[BucketType.SELLABLE],
        quarantined=bucket_map[BucketType.QUARANTINED],
        physical=physical,
    )


def get_batch_balances(
    db: Session,
    organization_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
) -> BatchBalancesResponse:
    """Calculate bucket balances per batch for a store product."""
    stmt = (
        select(
            Batch.id.label("batch_id"),
            Batch.batch_code,
            Batch.expiry_date,
            InventoryMovementPosting.bucket_type,
            func.coalesce(func.sum(InventoryMovementPosting.quantity_delta), Decimal("0.000")).label("qty"),
        )
        .join(
            InventoryMovement,
            InventoryMovement.batch_id == Batch.id,
        )
        .join(
            InventoryMovementPosting,
            InventoryMovementPosting.movement_id == InventoryMovement.id,
        )
        .where(
            Batch.organization_id == organization_id,
            Batch.store_id == store_id,
            Batch.product_id == product_id,
        )
        .group_by(
            Batch.id,
            Batch.batch_code,
            Batch.expiry_date,
            InventoryMovementPosting.bucket_type,
        )
    )
    rows = db.execute(stmt).all()

    batch_dict: dict[uuid.UUID, dict] = {}
    for r in rows:
        bid = r.batch_id
        if bid not in batch_dict:
            batch_dict[bid] = {
                "batch_id": bid,
                "batch_code": r.batch_code,
                "expiry_date": r.expiry_date,
                "sellable": Decimal("0.000"),
                "quarantined": Decimal("0.000"),
                "total": Decimal("0.000"),
            }
        qty = Decimal(str(r.qty))
        if r.bucket_type == BucketType.SELLABLE:
            batch_dict[bid]["sellable"] += qty
        elif r.bucket_type == BucketType.QUARANTINED:
            batch_dict[bid]["quarantined"] += qty
        batch_dict[bid]["total"] += qty

    items = [BatchBalanceItem(**data) for data in batch_dict.values()]
    return BatchBalancesResponse(
        organization_id=organization_id,
        store_id=store_id,
        product_id=product_id,
        batches=items,
    )


def record_movement(
    db: Session,
    movement_in: MovementCreate,
    allow_negative_stock: bool = False,
) -> InventoryMovement:
    """
    Record an append-only inventory movement with its postings.
    
    Validates tenant integrity, references, idempotency, and non-negative sellable stock.
    """
    org_id = movement_in.organization_id
    store_id = movement_in.store_id
    product_id = movement_in.product_id

    # 1. Idempotency check: if movement with this key exists, return it
    existing = db.execute(
        select(InventoryMovement)
        .options(selectinload(InventoryMovement.postings))
        .where(
            InventoryMovement.organization_id == org_id,
            InventoryMovement.idempotency_key == movement_in.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    # 2. Reference validation
    store = db.execute(
        select(Store).where(Store.organization_id == org_id, Store.id == store_id)
    ).scalar_one_or_none()
    if not store:
        raise EntityNotFoundError(f"Store {store_id} not found in organization {org_id}")

    product = db.execute(
        select(Product).where(Product.organization_id == org_id, Product.id == product_id)
    ).scalar_one_or_none()
    if not product:
        raise EntityNotFoundError(f"Product {product_id} not found in organization {org_id}")

    if movement_in.batch_id:
        batch = db.execute(
            select(Batch).where(
                Batch.organization_id == org_id,
                Batch.store_id == store_id,
                Batch.product_id == product_id,
                Batch.id == movement_in.batch_id,
            )
        ).scalar_one_or_none()
        if not batch:
            raise EntityNotFoundError(f"Batch {movement_in.batch_id} not found for this store/product")

    # 3. Non-negative sellable stock validation
    if not allow_negative_stock:
        sellable_delta = sum(
            p.quantity_delta
            for p in movement_in.postings
            if p.bucket_type == BucketType.SELLABLE
        )
        if sellable_delta < 0:
            current_balance = get_stock_balance(db, org_id, store_id, product_id)
            if current_balance.sellable + sellable_delta < 0:
                raise InsufficientStockError(
                    f"Insufficient SELLABLE stock for product {product_id} at store {store_id}: "
                    f"current balance {current_balance.sellable}, requested deduction {abs(sellable_delta)}"
                )

    # 4. Insert Movement Header
    movement = InventoryMovement(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        batch_id=movement_in.batch_id,
        movement_type=movement_in.movement_type,
        quantity_delta=movement_in.quantity_delta,
        occurred_at=movement_in.occurred_at,
        source_system=movement_in.source_system,
        external_document_id=movement_in.external_document_id,
        source_line_number=movement_in.source_line_number,
        idempotency_key=movement_in.idempotency_key,
        notes=movement_in.notes,
        reversed_movement_id=None,
    )
    db.add(movement)
    db.flush()

    # 5. Insert Movement Postings
    for p in movement_in.postings:
        posting = InventoryMovementPosting(
            organization_id=org_id,
            movement_id=movement.id,
            bucket_type=p.bucket_type,
            quantity_delta=p.quantity_delta,
        )
        db.add(posting)

    db.flush()
    db.refresh(movement)
    return movement


def record_reversal(
    db: Session,
    original_movement_id: uuid.UUID,
    reversal_in: ReversalCreate,
    organization_id: uuid.UUID,
) -> InventoryMovement:
    """
    Reverse an existing movement by creating a REVERSAL movement with inverse postings.
    """
    # 1. Check idempotency for reversal
    existing_reversal = db.execute(
        select(InventoryMovement)
        .options(selectinload(InventoryMovement.postings))
        .where(
            InventoryMovement.organization_id == organization_id,
            InventoryMovement.idempotency_key == reversal_in.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing_reversal:
        return existing_reversal

    # 2. Retrieve original movement
    original = db.execute(
        select(InventoryMovement)
        .options(selectinload(InventoryMovement.postings))
        .where(
            InventoryMovement.organization_id == organization_id,
            InventoryMovement.id == original_movement_id,
        )
    ).scalar_one_or_none()
    if not original:
        raise EntityNotFoundError(
            f"Original movement {original_movement_id} not found in organization {organization_id}"
        )

    # 3. Check if already reversed
    already_reversed = db.execute(
        select(InventoryMovement).where(
            InventoryMovement.organization_id == organization_id,
            InventoryMovement.reversed_movement_id == original.id,
        )
    ).scalar_one_or_none()
    if already_reversed:
        raise MovementAlreadyReversedError(
            f"Movement {original.id} has already been reversed by {already_reversed.id}"
        )

    # 4. Create Reversal Movement header
    reversal_movement = InventoryMovement(
        organization_id=organization_id,
        store_id=original.store_id,
        product_id=original.product_id,
        batch_id=original.batch_id,
        movement_type=MovementType.REVERSAL,
        quantity_delta=-original.quantity_delta,
        occurred_at=reversal_in.occurred_at,
        source_system=reversal_in.source_system,
        external_document_id=original.external_document_id,
        source_line_number=original.source_line_number,
        idempotency_key=reversal_in.idempotency_key,
        notes=reversal_in.notes or f"Reversal of movement {original.id}",
        reversed_movement_id=original.id,
    )
    db.add(reversal_movement)
    db.flush()

    # 5. Create inverse postings
    for orig_post in original.postings:
        inv_posting = InventoryMovementPosting(
            organization_id=organization_id,
            movement_id=reversal_movement.id,
            bucket_type=orig_post.bucket_type,
            quantity_delta=-orig_post.quantity_delta,
        )
        db.add(inv_posting)

    db.flush()
    db.refresh(reversal_movement)
    return reversal_movement


def record_reclassify(
    db: Session,
    reclassify_in: ReclassifyCreate,
    allow_negative_stock: bool = False,
) -> InventoryMovement:
    """
    Reclassify inventory between SELLABLE and QUARANTINED buckets.
    """
    org_id = reclassify_in.organization_id
    store_id = reclassify_in.store_id
    product_id = reclassify_in.product_id
    qty = abs(reclassify_in.quantity)

    # Validate balance in source bucket
    if not allow_negative_stock and reclassify_in.from_bucket == BucketType.SELLABLE:
        current_balance = get_stock_balance(db, org_id, store_id, product_id)
        if current_balance.sellable < qty:
            raise InsufficientStockError(
                f"Insufficient SELLABLE stock to reclassify: available {current_balance.sellable}, requested {qty}"
            )

    postings = [
        PostingSpec(bucket_type=reclassify_in.from_bucket, quantity_delta=-qty),
        PostingSpec(bucket_type=reclassify_in.to_bucket, quantity_delta=qty),
    ]

    movement_create = MovementCreate(
        organization_id=org_id,
        store_id=store_id,
        product_id=product_id,
        batch_id=reclassify_in.batch_id,
        movement_type=MovementType.RECLASSIFY,
        quantity_delta=Decimal("0.000"),
        occurred_at=reclassify_in.occurred_at,
        source_system=reclassify_in.source_system,
        idempotency_key=reclassify_in.idempotency_key,
        notes=reclassify_in.notes,
        postings=postings,
    )
    return record_movement(db, movement_create, allow_negative_stock=allow_negative_stock)


def record_transfer(
    db: Session,
    transfer_in: TransferCreate,
    allow_negative_stock: bool = False,
) -> tuple[InventoryMovement, InventoryMovement]:
    """
    Atomically transfer stock between two stores of the same organization.
    Creates TRANSFER_OUT from source store and TRANSFER_IN to destination store.
    """
    org_id = transfer_in.organization_id
    qty = abs(transfer_in.quantity)

    # 1. Outbound from source store
    outbound_payload = MovementCreate(
        organization_id=org_id,
        store_id=transfer_in.from_store_id,
        product_id=transfer_in.product_id,
        batch_id=transfer_in.batch_id,
        movement_type=MovementType.TRANSFER_OUT,
        quantity_delta=-qty,
        occurred_at=transfer_in.occurred_at,
        source_system=transfer_in.source_system,
        idempotency_key=f"{transfer_in.idempotency_key}:OUT",
        notes=transfer_in.notes or f"Transfer out to store {transfer_in.to_store_id}",
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=-qty)],
    )
    outbound_mv = record_movement(db, outbound_payload, allow_negative_stock=allow_negative_stock)

    # 2. Inbound to destination store
    inbound_payload = MovementCreate(
        organization_id=org_id,
        store_id=transfer_in.to_store_id,
        product_id=transfer_in.product_id,
        batch_id=transfer_in.batch_id,
        movement_type=MovementType.TRANSFER_IN,
        quantity_delta=qty,
        occurred_at=transfer_in.occurred_at,
        source_system=transfer_in.source_system,
        idempotency_key=f"{transfer_in.idempotency_key}:IN",
        notes=transfer_in.notes or f"Transfer in from store {transfer_in.from_store_id}",
        postings=[PostingSpec(bucket_type=BucketType.SELLABLE, quantity_delta=qty)],
    )
    inbound_mv = record_movement(db, inbound_payload, allow_negative_stock=True)

    return outbound_mv, inbound_mv


def get_movement_history(
    db: Session,
    organization_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[InventoryMovement]:
    """Retrieve chronologically ordered inventory movement history with postings."""
    stmt = (
        select(InventoryMovement)
        .options(selectinload(InventoryMovement.postings))
        .where(InventoryMovement.organization_id == organization_id)
    )
    if store_id:
        stmt = stmt.where(InventoryMovement.store_id == store_id)
    if product_id:
        stmt = stmt.where(InventoryMovement.product_id == product_id)

    stmt = stmt.order_by(InventoryMovement.occurred_at.desc(), InventoryMovement.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()
