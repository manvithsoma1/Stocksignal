"""
StockSignal Data Upload & Ingestion Tracking Models — Phase C.

Tracks the full lifecycle of uploaded retail datasets (sales, stock snapshots,
goods received notes, batch expirations, physical count audits):
  UPLOADED -> VALIDATING -> VALIDATION_FAILED -> READY_TO_IMPORT ->
  PROCESSING -> COMPLETED -> COMPLETED_WITH_ERRORS -> FAILED.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DatasetCategory(str, enum.Enum):
    SALES = "SALES"
    INVENTORY_SNAPSHOT = "INVENTORY_SNAPSHOT"
    GOODS_RECEIVED = "GOODS_RECEIVED"
    BATCH_EXPIRY = "BATCH_EXPIRY"
    PHYSICAL_COUNT = "PHYSICAL_COUNT"
    PRODUCT_CATALOG = "PRODUCT_CATALOG"


class UploadStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    READY_TO_IMPORT = "READY_TO_IMPORT"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class DataUploadBatch(Base):
    """
    Persisted log of manual and scheduled dataset imports.
    Ensures data lineage, auditable record tracking, and duplicate prevention.
    """
    __tablename__ = "data_upload_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    store_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    dataset_category: Mapped[DatasetCategory] = mapped_column(
        Enum(DatasetCategory, name="dataset_category"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(20), nullable=False, default="CSV")
    file_hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"),
        default=UploadStatus.UPLOADED,
        nullable=False,
    )

    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    uploaded_by: Mapped[str] = mapped_column(String(100), default="OPERATOR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_upload_batch_organization",
        ),
    )
