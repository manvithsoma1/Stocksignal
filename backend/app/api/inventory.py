"""
FastAPI router for Inventory Ledger API endpoints.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.schemas.inventory import (
    BatchBalancesResponse,
    MovementCreate,
    MovementResponse,
    ReclassifyCreate,
    ReversalCreate,
    StockBalanceResponse,
    TransferCreate,
)
from app.services import ledger

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/movements",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an immutable inventory movement",
)
def record_movement(
    payload: MovementCreate,
    db: Session = Depends(get_db),
):
    try:
        movement = ledger.record_movement(db, payload)
        db.commit()
        db.refresh(movement)
        return movement
    except ledger.EntityNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ledger.InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/movements/{movement_id}/reverse",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reverse an existing inventory movement",
)
def record_reversal(
    movement_id: uuid.UUID,
    payload: ReversalCreate,
    organization_id: uuid.UUID = Query(..., description="Organization ID"),
    db: Session = Depends(get_db),
):
    try:
        reversal = ledger.record_reversal(db, movement_id, payload, organization_id)
        db.commit()
        db.refresh(reversal)
        return reversal
    except ledger.EntityNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ledger.MovementAlreadyReversedError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/reclassify",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reclassify inventory between buckets (e.g. SELLABLE to QUARANTINED)",
)
def record_reclassify(
    payload: ReclassifyCreate,
    db: Session = Depends(get_db),
):
    try:
        movement = ledger.record_reclassify(db, payload)
        db.commit()
        db.refresh(movement)
        return movement
    except ledger.EntityNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ledger.InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/transfers",
    response_model=List[MovementResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Transfer stock between two stores",
)
def record_transfer(
    payload: TransferCreate,
    db: Session = Depends(get_db),
):
    try:
        outbound, inbound = ledger.record_transfer(db, payload)
        db.commit()
        db.refresh(outbound)
        db.refresh(inbound)
        return [outbound, inbound]
    except ledger.EntityNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ledger.InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/balance",
    response_model=StockBalanceResponse,
    summary="Get current stock balance by store and product",
)
def get_balance(
    organization_id: uuid.UUID = Query(...),
    store_id: uuid.UUID = Query(...),
    product_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
):
    return ledger.get_stock_balance(db, organization_id, store_id, product_id)


@router.get(
    "/batches",
    response_model=BatchBalancesResponse,
    summary="Get batch-level balances by store and product",
)
def get_batches(
    organization_id: uuid.UUID = Query(...),
    store_id: uuid.UUID = Query(...),
    product_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
):
    return ledger.get_batch_balances(db, organization_id, store_id, product_id)


@router.get(
    "/history",
    response_model=List[MovementResponse],
    summary="Get movement ledger history",
)
def get_history(
    organization_id: uuid.UUID = Query(...),
    store_id: Optional[uuid.UUID] = Query(None),
    product_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return ledger.get_movement_history(db, organization_id, store_id, product_id, limit, offset)

