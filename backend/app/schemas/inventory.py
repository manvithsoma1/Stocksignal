"""
Pydantic request and response schemas for the inventory ledger API.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.core import BucketType, MovementType


# --------------------------------------------------------------------------- #
# Sub-models                                                                    #
# --------------------------------------------------------------------------- #

class PostingSpec(BaseModel):
    """A single bucket posting that is part of a movement."""

    bucket_type: BucketType
    quantity_delta: Decimal = Field(
        ..., description="Non-zero signed quantity change for this bucket."
    )

    @field_validator("quantity_delta")
    @classmethod
    def must_be_nonzero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("posting quantity_delta must not be zero")
        return v


# --------------------------------------------------------------------------- #
# Request schemas                                                               #
# --------------------------------------------------------------------------- #

class MovementCreate(BaseModel):
    """Payload for recording a new inventory movement."""

    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    movement_type: MovementType
    quantity_delta: Decimal = Field(
        ...,
        description=(
            "Net physical delta across all buckets. "
            "Must be 0 for RECLASSIFY; non-zero for all other types "
            "(REVERSAL may be 0 when reversing a RECLASSIFY)."
        ),
    )
    occurred_at: datetime
    source_system: str = Field(..., max_length=80)
    external_document_id: str | None = Field(None, max_length=160)
    source_line_number: str | None = Field(None, max_length=80)
    idempotency_key: str = Field(..., max_length=128)
    notes: str | None = None
    postings: list[PostingSpec] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_quantity_and_postings(self) -> "MovementCreate":
        mt = self.movement_type

        if mt == MovementType.RECLASSIFY:
            if self.quantity_delta != 0:
                raise ValueError("RECLASSIFY movement must have quantity_delta = 0")
            if len(self.postings) != 2:
                raise ValueError("RECLASSIFY movement must have exactly 2 postings")
            buckets = {p.bucket_type for p in self.postings}
            if buckets != {BucketType.SELLABLE, BucketType.QUARANTINED}:
                raise ValueError(
                    "RECLASSIFY must have one SELLABLE posting and one QUARANTINED posting"
                )
            sell_delta = next(
                p.quantity_delta for p in self.postings
                if p.bucket_type == BucketType.SELLABLE
            )
            quar_delta = next(
                p.quantity_delta for p in self.postings
                if p.bucket_type == BucketType.QUARANTINED
            )
            if sell_delta + quar_delta != 0:
                raise ValueError(
                    "RECLASSIFY postings must sum to zero "
                    "(equal absolute quantities, opposite signs)"
                )
        else:
            posting_sum = sum(p.quantity_delta for p in self.postings)
            if posting_sum != self.quantity_delta:
                raise ValueError(
                    f"Sum of posting quantity_deltas ({posting_sum}) "
                    f"must equal movement quantity_delta ({self.quantity_delta})"
                )

        return self


class ReversalCreate(BaseModel):
    """Payload for reversing an existing movement."""

    idempotency_key: str = Field(..., max_length=128)
    source_system: str = Field(..., max_length=80)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None


class ReclassifyCreate(BaseModel):
    """Payload for reclassifying stock between SELLABLE and QUARANTINED."""

    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    from_bucket: BucketType = BucketType.SELLABLE
    to_bucket: BucketType = BucketType.QUARANTINED
    quantity: Decimal = Field(..., gt=0, description="Positive quantity to transfer between buckets")
    idempotency_key: str = Field(..., max_length=128)
    source_system: str = Field(..., max_length=80)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None

    @model_validator(mode="after")
    def validate_buckets(self) -> "ReclassifyCreate":
        valid_pair = {self.from_bucket, self.to_bucket} == {BucketType.SELLABLE, BucketType.QUARANTINED}
        if not valid_pair:
            raise ValueError("Reclassification is only supported between SELLABLE and QUARANTINED buckets")
        return self


class TransferCreate(BaseModel):
    """Payload for transferring stock between stores within an organization."""

    organization_id: uuid.UUID
    from_store_id: uuid.UUID
    to_store_id: uuid.UUID
    product_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    quantity: Decimal = Field(..., gt=0, description="Quantity to transfer")
    idempotency_key: str = Field(..., max_length=128)
    source_system: str = Field(..., max_length=80)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None

    @model_validator(mode="after")
    def validate_stores(self) -> "TransferCreate":
        if self.from_store_id == self.to_store_id:
            raise ValueError("Source store and destination store must be different")
        return self


# --------------------------------------------------------------------------- #
# Response schemas                                                              #
# --------------------------------------------------------------------------- #

class PostingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    movement_id: uuid.UUID
    bucket_type: BucketType
    quantity_delta: Decimal
    created_at: datetime


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    batch_id: uuid.UUID | None
    movement_type: MovementType
    quantity_delta: Decimal
    occurred_at: datetime
    recorded_at: datetime
    source_system: str
    external_document_id: str | None
    source_line_number: str | None
    idempotency_key: str
    notes: str | None
    reversed_movement_id: uuid.UUID | None
    created_at: datetime
    postings: list[PostingResponse] = []


class StockBalanceResponse(BaseModel):
    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    sellable: Decimal = Decimal("0.000")
    quarantined: Decimal = Decimal("0.000")
    physical: Decimal = Decimal("0.000")


class BatchBalanceItem(BaseModel):
    batch_id: uuid.UUID
    batch_code: str | None = None
    expiry_date: date | None = None
    sellable: Decimal = Decimal("0.000")
    quarantined: Decimal = Decimal("0.000")
    total: Decimal = Decimal("0.000")


class BatchBalancesResponse(BaseModel):
    organization_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    batches: list[BatchBalanceItem] = []
