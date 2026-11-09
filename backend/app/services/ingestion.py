"""
StockSignal Data Ingestion & Validation Service — Phase C.

Parses, validates, and imports tabular CSV datasets into the inventory ledger:
  - Validates schema, headers, and row types
  - Checks duplicate uploads via SHA-256 hash
  - Verifies referential integrity (store_code, sku)
  - Records inventory movements atomically via the ledger service
"""
from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import BucketType, MovementType, Organization, Product, Store, StoreProduct
from app.models.uploads import DataUploadBatch, DatasetCategory, UploadStatus
from app.schemas.inventory import MovementCreate, PostingSpec
from app.services import ledger


# Required columns per dataset category
REQUIRED_COLUMNS = {
    DatasetCategory.SALES: ["sku", "store_code", "quantity", "occurred_at"],
    DatasetCategory.INVENTORY_SNAPSHOT: ["sku", "store_code", "counted_qty"],
    DatasetCategory.GOODS_RECEIVED: ["sku", "store_code", "quantity", "invoice_number"],
    DatasetCategory.PHYSICAL_COUNT: ["sku", "store_code", "bucket", "quantity"],
}


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hash of file bytes for duplicate detection."""
    return hashlib.sha256(content).hexdigest()


def parse_csv_stream(content: bytes) -> Tuple[list[str], list[dict[str, str]]]:
    """Parse raw bytes into header list and list of row dicts."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip().lower() for h in rows[0]]
    data = []
    for r in rows[1:]:
        if not any(r):  # skip empty lines
            continue
        row_dict = {headers[i]: r[i].strip() if i < len(r) else "" for i in range(len(headers))}
        data.append(row_dict)
    return headers, data


def validate_and_stage_upload(
    db: Session,
    org_id: uuid.UUID,
    file_name: str,
    file_bytes: bytes,
    category: DatasetCategory,
    uploaded_by: str = "OPERATOR",
) -> DataUploadBatch:
    """
    Validate uploaded file schema, detect duplicates, and create a DataUploadBatch record.
    """
    file_hash = compute_sha256(file_bytes)

    # Check duplicate
    existing = db.execute(
        select(DataUploadBatch).where(
            DataUploadBatch.organization_id == org_id,
            DataUploadBatch.file_hash_sha256 == file_hash,
            DataUploadBatch.status == UploadStatus.COMPLETED,
        )
    ).scalar_one_or_none()

    if existing:
        batch = DataUploadBatch(
            organization_id=org_id,
            dataset_category=category,
            file_name=file_name,
            file_format="CSV",
            file_hash_sha256=file_hash,
            status=UploadStatus.VALIDATION_FAILED,
            total_rows=0,
            valid_rows=0,
            rejected_rows=0,
            error_summary=f"Duplicate upload: File with identical SHA-256 hash already processed in batch {existing.id}.",
            uploaded_by=uploaded_by,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    # Parse headers and rows
    headers, rows = parse_csv_stream(file_bytes)
    total_rows = len(rows)

    required = REQUIRED_COLUMNS.get(category, [])
    missing_cols = [c for c in required if c not in headers]

    if missing_cols:
        batch = DataUploadBatch(
            organization_id=org_id,
            dataset_category=category,
            file_name=file_name,
            file_format="CSV",
            file_hash_sha256=file_hash,
            status=UploadStatus.VALIDATION_FAILED,
            total_rows=total_rows,
            valid_rows=0,
            rejected_rows=total_rows,
            error_summary=f"Schema validation failed: Missing required columns: {', '.join(missing_cols)}.",
            uploaded_by=uploaded_by,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    # Schema is valid
    batch = DataUploadBatch(
        organization_id=org_id,
        dataset_category=category,
        file_name=file_name,
        file_format="CSV",
        file_hash_sha256=file_hash,
        status=UploadStatus.READY_TO_IMPORT,
        total_rows=total_rows,
        valid_rows=total_rows,
        rejected_rows=0,
        uploaded_by=uploaded_by,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def process_sales_csv(
    db: Session,
    batch: DataUploadBatch,
    file_bytes: bytes,
) -> DataUploadBatch:
    """
    Ingest validated SALES CSV records into inventory ledger movements.
    """
    batch.status = UploadStatus.PROCESSING
    db.commit()

    _, rows = parse_csv_stream(file_bytes)
    valid_count = 0
    rejected_count = 0
    errors = []

    for idx, r in enumerate(rows, start=2):
        sku = r.get("sku", "").strip()
        store_code = r.get("store_code", "").strip()
        qty_str = r.get("quantity", "").strip()

        # 1. Validate Store
        store = db.execute(
            select(Store).where(
                Store.organization_id == batch.organization_id,
                Store.store_code == store_code,
            )
        ).scalar_one_or_none()

        if not store:
            rejected_count += 1
            errors.append(f"Row {idx}: Unknown store_code '{store_code}'")
            continue

        # 2. Validate Product
        product = db.execute(
            select(Product).where(
                Product.organization_id == batch.organization_id,
                Product.sku == sku,
            )
        ).scalar_one_or_none()

        if not product:
            rejected_count += 1
            errors.append(f"Row {idx}: Unknown product SKU '{sku}'")
            continue

        # 3. Parse Quantity
        try:
            qty = Decimal(qty_str)
            if qty <= 0:
                raise ValueError("Quantity must be positive")
        except (InvalidOperation, ValueError):
            rejected_count += 1
            errors.append(f"Row {idx}: Invalid quantity value '{qty_str}'")
            continue

        # 4. Record SALE movement in ledger (deduct from SELLABLE)
        try:
            delta = -abs(qty)
            idempotency_key = f"UPLOAD_{batch.id}_ROW_{idx}"
            ledger.record_movement(
                db,
                MovementCreate(
                    organization_id=batch.organization_id,
                    store_id=store.id,
                    product_id=product.id,
                    movement_type=MovementType.SALE,
                    quantity_delta=delta,
                    occurred_at=datetime.now(timezone.utc),
                    source_system="CSV_INGESTION",
                    external_document_id=f"BATCH_{batch.id}",
                    source_line_number=str(idx),
                    idempotency_key=idempotency_key,
                    postings=[
                        PostingSpec(
                            bucket_type=BucketType.SELLABLE,
                            quantity_delta=delta,
                        )
                    ],
                ),
            )
            valid_count += 1
        except Exception as e:
            rejected_count += 1
            errors.append(f"Row {idx}: Ledger rejection: {str(e)}")

    # Update batch record
    batch.valid_rows = valid_count
    batch.rejected_rows = rejected_count
    batch.completed_at = datetime.now(timezone.utc)

    if rejected_count == 0:
        batch.status = UploadStatus.COMPLETED
    elif valid_count > 0:
        batch.status = UploadStatus.COMPLETED_WITH_ERRORS
        batch.error_summary = f"{rejected_count} rows rejected out of {len(rows)}."
        batch.validation_details = {"errors": errors[:50]}
    else:
        batch.status = UploadStatus.FAILED
        batch.error_summary = "All rows failed ingestion."
        batch.validation_details = {"errors": errors[:50]}

    db.commit()
    db.refresh(batch)
    return batch
