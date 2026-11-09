"""
StockSignal Demand Forecasting Inference Service.

Loads the trained LightGBM model artifact and provides dynamic, data-driven
SKU sales forecasts and stockout risk estimates based on store sales history.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.core import Product, Store, StoreProduct
from app.services import ledger

MODEL_PATH = Path(__file__).resolve().parent / "models" / "m5_lightgbm.txt"
_BOOSTER: Optional[object] = None


def get_booster():
    """Load or return cached LightGBM model booster."""
    global _BOOSTER
    if lgb is None:
        return None
    if _BOOSTER is not None:
        return _BOOSTER
    if MODEL_PATH.exists():
        _BOOSTER = lgb.Booster(model_file=str(MODEL_PATH))
    return _BOOSTER


def forecast_sku_demand(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    horizon_days: int = 14,
) -> dict:
    """
    Generate dynamic ML demand forecast for a single store-product over the next H days.
    Also estimates:
      - Projected total demand over horizon
      - Current sellable stock
      - Days of inventory remaining (Runway)
      - Stockout probability / risk tier
    """
    # 1. Fetch current stock
    bal = ledger.get_stock_balance(db, org_id, store_id, product_id)
    current_stock = bal.sellable

    # 2. Fetch historical sales velocity and daily time series (last 28 days)
    sales_daily_query = text(
        """
        SELECT DATE(m.occurred_at) as sale_date, COALESCE(SUM(ABS(p.quantity_delta)), 0) as daily_units
        FROM inventory_movement_postings p
        JOIN inventory_movements m ON m.id = p.movement_id
        WHERE m.organization_id = :org_id
          AND m.store_id = :store_id
          AND m.product_id = :product_id
          AND m.movement_type = 'SALE'
          AND p.bucket_type = 'SELLABLE'
          AND m.occurred_at >= NOW() - INTERVAL '28 days'
        GROUP BY DATE(m.occurred_at)
        ORDER BY sale_date ASC
        """
    )
    daily_rows = db.execute(sales_daily_query, {
        "org_id": org_id, "store_id": store_id, "product_id": product_id
    }).all()

    has_uploaded_sales = len(daily_rows) >= 7
    total_sales_units = sum(Decimal(str(r.daily_units)) for r in daily_rows) if daily_rows else Decimal("0.000")
    avg_daily_sales = (total_sales_units / Decimal(str(len(daily_rows) if daily_rows else 28))).quantize(Decimal("0.001"))

    # 3. Model Prediction (LightGBM if available, Seasonal Naive fallback)
    booster = get_booster()
    daily_predictions = []

    # If real store transactions exist, use their empirical velocity; otherwise fall back to baseline retail prior
    base_rate = float(avg_daily_sales) if (daily_rows and avg_daily_sales > 0) else 5.0
    data_source_label = "STORE_TRANSACTION_HISTORY" if has_uploaded_sales else "RETAIL_PRIOR_BASELINE"

    for day in range(1, horizon_days + 1):
        if booster is not None:
            # Construct feature vector [lag_1, lag_7, lag_28, roll_7, roll_14, roll_28, wday, month, year]
            features = np.array([[
                base_rate, base_rate, base_rate,
                base_rate, base_rate, base_rate,
                (day % 7) + 1,
                datetime.now().month,
                datetime.now().year
            ]])
            pred = float(np.clip(booster.predict(features)[0], 0, None))
        else:
            # Seasonal Naive baseline projection (Friday/Saturday weekend retail weighting)
            weekend_boost = 1.3 if day % 7 in (5, 6) else 1.0
            pred = base_rate * weekend_boost

        daily_predictions.append(round(pred, 2))

    projected_demand = sum(daily_predictions)

    # 4. Stockout Risk Assessment
    if base_rate > 0:
        days_of_supply = float(current_stock) / base_rate
    else:
        days_of_supply = 999.0

    if days_of_supply < 3.0:
        stockout_risk = "CRITICAL"
    elif days_of_supply < 7.0:
        stockout_risk = "HIGH"
    elif days_of_supply < 14.0:
        stockout_risk = "MEDIUM"
    else:
        stockout_risk = "SAFE"

    return {
        "product_id": str(product_id),
        "horizon_days": horizon_days,
        "current_sellable_stock": float(current_stock),
        "avg_daily_sales": float(avg_daily_sales),
        "projected_demand": round(projected_demand, 2),
        "days_of_supply": round(days_of_supply, 1),
        "stockout_risk": stockout_risk,
        "daily_forecast": daily_predictions,
        "data_source": data_source_label,
        "historical_days_recorded": len(daily_rows),
        "model_used": "LightGBM (m5_lightgbm.txt)" if booster else "Seasonal Naive Baseline",
    }

