"""
FastAPI router for Manual Data Management & Dataset Upload Ingestion.
"""
from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.uploads import DataUploadBatch, DatasetCategory, UploadStatus
from app.services import ingestion

router = APIRouter(prefix="/api/v1/data", tags=["Data Ingestion & Management"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_dataset_file(
    file: UploadFile = File(...),
    organization_id: uuid.UUID = Form(...),
    category: DatasetCategory = Form(...),
    auto_process: bool = Form(True),
    db: Session = Depends(get_db),
):
    """
    Upload a CSV retail dataset (Sales, Stock Snapshot, GRN, Physical Count).
    Validates schema and staging status, and optionally executes immediate ingestion.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    # 1. Validate & Stage
    batch = ingestion.validate_and_stage_upload(
        db=db,
        org_id=organization_id,
        file_name=file.filename or "uploaded_data.csv",
        file_bytes=content,
        category=category,
    )

    # 2. Ingest if ready & requested
    if auto_process and batch.status == UploadStatus.READY_TO_IMPORT:
        if category == DatasetCategory.SALES:
            batch = ingestion.process_sales_csv(db, batch, content)
        elif category == DatasetCategory.GOODS_RECEIVED:
            batch = ingestion.process_goods_received_csv(db, batch, content)

    return {
        "batch_id": batch.id,
        "file_name": batch.file_name,
        "category": batch.dataset_category,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "valid_rows": batch.valid_rows,
        "rejected_rows": batch.rejected_rows,
        "error_summary": batch.error_summary,
        "validation_details": batch.validation_details,
        "created_at": batch.created_at,
    }


@router.get("/batches")
def list_upload_batches(
    organization_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    List history of all uploaded datasets and their validation / processing status.
    """
    batches = db.execute(
        select(DataUploadBatch)
        .where(DataUploadBatch.organization_id == organization_id)
        .order_by(desc(DataUploadBatch.created_at))
        .limit(limit)
    ).scalars().all()

    return [
        {
            "id": b.id,
            "file_name": b.file_name,
            "category": b.dataset_category,
            "status": b.status,
            "total_rows": b.total_rows,
            "valid_rows": b.valid_rows,
            "rejected_rows": b.rejected_rows,
            "error_summary": b.error_summary,
            "created_at": b.created_at,
            "completed_at": b.completed_at,
        }
        for b in batches
    ]


@router.post("/preview")
async def preview_dataset_file(
    file: UploadFile = File(...),
    category: DatasetCategory = Form(...),
):
    """
    Inspect an uploaded file (CSV or Excel) and return:
      - Detected headers
      - Expected columns for the category
      - Missing columns / matched columns
      - First 5 preview rows
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    headers, rows = ingestion.parse_tabular_stream(file.filename or "data.csv", content)
    required = ingestion.REQUIRED_COLUMNS.get(category, [])
    matched_cols = [c for c in required if c in headers]
    missing_cols = [c for c in required if c not in headers]

    return {
        "file_name": file.filename,
        "total_detected_rows": len(rows),
        "headers": headers,
        "required_columns": required,
        "matched_columns": matched_cols,
        "missing_columns": missing_cols,
        "is_valid_schema": len(missing_cols) == 0,
        "sample_preview": rows[:5],
    }

