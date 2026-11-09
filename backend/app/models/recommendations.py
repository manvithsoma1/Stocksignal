"""
StockSignal Outcome Tracking & Recommendations API — Phase 9.

Models and API schemas for tracking the lifecycle of action recommendations:
  PENDING -> APPROVED / REJECTED -> IN_PROGRESS -> COMPLETED / CANCELLED -> VERIFIED.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecommendationStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    VERIFIED = "VERIFIED"


class ActionRecommendationRecord(Base):
    """
    Persisted action recommendation entity for auditability and outcome tracking.
    """
    __tablename__ = "action_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, name="recommendation_status"),
        default=RecommendationStatus.PENDING,
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Outcome tracking: predicted vs estimated vs verified realized benefit
    predicted_opportunity_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    estimated_benefit_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    realized_benefit_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    actual_outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "store_id"],
            ["stores.organization_id", "stores.id"],
            name="fk_action_recs_store",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            name="fk_action_recs_product",
        ),
    )

