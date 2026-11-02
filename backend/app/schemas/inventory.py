"""
Pydantic request and response schemas for the inventory ledger API.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
    def validate_quantity_and_postings(self) -> "MovementCreate":  # noqa: D401
        mt = self.movement_type

        if mt == MovementType.RECLASSIFY:
            # --- RECLASSIFY rules (mirror the DB trigger) ---
            if self.quantity_delta != 0:
                raise ValueError(
                    "RECLASSIFY movement must have quantity_delta = 0"
                )
            if len(self.postings) != 2:
                raise ValueError(
                    "RECLASSIFY movement must have exactly 2 postings"
                )
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
            # --- Standard rules ---
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
    occurred_at: datetime
    notes: str | None = None


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
    sellable: Decimal
    quarantined: Decimal
    physical: Decimal

