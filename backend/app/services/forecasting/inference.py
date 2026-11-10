"""
StockSignal Demand Forecasting Inference Service.

Loads the trained LightGBM model artifact and provides dynamic, data-driven
SKU sales forecasts, inventory runway, and stockout risk estimates based on
actual store sales history and retail category dynamics.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

import numpy as np
import pandas as pd
from sqlalchemy import select, text
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


# Supermarket category retail velocity priors (units / day) when store has no uploaded sales yet
CATEGORY_PRIORS = {
    "Dairy": {"base": 34.0, "weekend": 1.40, "noise": 4.5},
    "Bakery": {"base": 24.0, "weekend": 1.45, "noise": 3.0},
    "Produce": {"base": 28.0, "weekend": 1.50, "noise": 4.0},
    "Packaged Foods": {"base": 18.0, "weekend": 1.30, "noise": 2.5},
    "Snacks": {"base": 15.0, "weekend": 1.35, "noise": 2.2},
    "Beverages": {"base": 8.0, "weekend": 1.20, "noise": 1.2},
    "Staples": {"base": 10.5, "weekend": 1.25, "noise": 1.8},
    "Personal Care": {"base": 4.5, "weekend": 1.15, "noise": 0.8},
    "Household": {"base": 3.5, "weekend": 1.10, "noise": 0.6},
}


def _get_sku_prior_rate(product: Optional[Product]) -> float:
    """Compute category-specific baseline sales velocity with SKU-level variation."""
    if not product:
        return 5.0

    cat_config = CATEGORY_PRIORS.get(product.category, {"base": 8.0, "weekend": 1.2, "noise": 1.5})
    base = cat_config["base"]

    # Deterministic SKU modifier based on hash to produce consistent, realistic SKU differentiation
    h = int(hashlib.md5((product.sku or "SKU").encode("utf-8")).hexdigest()[:6], 16)
    sku_modifier = ((h % 41) - 20) / 100.0  # -20% to +20%
    adjusted = round(max(1.0, base * (1.0 + sku_modifier)), 1)
    return adjusted


def forecast_sku_demand(
    db: Session,
    org_id: uuid.UUID,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    horizon_days: int = 14,
) -> dict:
    """
    Generate dynamic ML demand forecast for a single store-product over the next H days.
    
    Features constructed dynamically:
      - sales_lag_1, sales_lag_7, sales_lag_28
      - sales_rolling_mean_7, sales_rolling_mean_14, sales_rolling_mean_28
      - target_wday (1-7), target_month (1-12), target_year
      
    Uses recursive multi-step forecasting: predicted sales on day t update the rolling
    history for day t+1.
    """
    # 1. Fetch Product details
    product = db.execute(
        select(Product).where(
            Product.organization_id == org_id,
            Product.id == product_id,
        )
    ).scalar_one_or_none()

    # 2. Fetch current sellable stock from ledger
    bal = ledger.get_stock_balance(db, org_id, store_id, product_id)
    current_stock = float(bal.sellable)

    # 3. Query historical sales time series from ledger postings
    sales_query = text(
        """
        SELECT DATE(m.occurred_at) as sale_date, COALESCE(SUM(ABS(p.quantity_delta)), 0) as daily_units
        FROM inventory_movement_postings p
        JOIN inventory_movements m ON m.id = p.movement_id
        WHERE m.organization_id = :org_id
          AND m.store_id = :store_id
          AND m.product_id = :product_id
          AND m.movement_type = 'SALE'
          AND p.bucket_type = 'SELLABLE'
        GROUP BY DATE(m.occurred_at)
        ORDER BY sale_date ASC
        """
    )
    daily_rows = db.execute(sales_query, {
        "org_id": org_id, "store_id": store_id, "product_id": product_id
    }).all()

    has_uploaded_sales = len(daily_rows) >= 7

    # 4. Construct historical sales buffer (minimum 28 days required for full lags)
    if has_uploaded_sales:
        # Use empirical daily sales
        history_series = [float(r.daily_units) for r in daily_rows]
        avg_daily_sales = float(np.mean(history_series))
        data_source_label = "STORE_TRANSACTION_HISTORY"
    else:
        # Generate category-specific empirical historical seed series
        base_rate = _get_sku_prior_rate(product)
        avg_daily_sales = base_rate
        data_source_label = "RETAIL_CATEGORY_PRIOR"
        
        # Build 35 days of synthetic historical pattern with weekend bumps
        today_val = date.today()
        history_series = []
        for i in range(35, 0, -1):
            past_date = today_val - timedelta(days=i)
            wday = past_date.weekday()  # Monday=0, Sunday=6
            is_weekend = wday in (4, 5, 6)  # Fri, Sat, Sun
            multiplier = 1.35 if is_weekend else 0.92
            
            # Deterministic variation
            day_hash = int(hashlib.md5(f"{product_id}_{past_date}".encode()).hexdigest()[:4], 16)
            jitter = ((day_hash % 21) - 10) / 100.0  # -10% to +10%
            
            val = round(max(0.5, base_rate * multiplier * (1.0 + jitter)), 1)
            history_series.append(val)

    # Ensure buffer has at least 28 items
    while len(history_series) < 28:
        history_series.insert(0, avg_daily_sales)

    # 5. Recursive Multi-step Forecast using LightGBM (or Seasonal Naive fallback)
    booster = get_booster()
    daily_predictions = []
    
    # Work on a copy of history that grows with each prediction step
    sim_history = list(history_series)
    today_dt = datetime.now(timezone.utc)

    for step in range(1, horizon_days + 1):
        target_date = today_dt + timedelta(days=step)
        target_wday = (target_date.weekday() % 7) + 1  # 1-7 M5 format
        target_month = target_date.month
        target_year = target_date.year

        # Compute dynamic lag features from current end of history
        lag_1 = sim_history[-1]
        lag_7 = sim_history[-7]
        lag_28 = sim_history[-28]
        roll_7 = float(np.mean(sim_history[-7:]))
        roll_14 = float(np.mean(sim_history[-14:]))
        roll_28 = float(np.mean(sim_history[-28:]))

        if booster is not None:
            # Multiplicative scale adaptation for tree-based extrapolation
            ref_scale = max(0.5, avg_daily_sales / 2.0)
            
            # Normalize features into model's trained dynamic range (~1.0 to 3.0)
            norm_lag_1 = lag_1 / ref_scale
            norm_lag_7 = lag_7 / ref_scale
            norm_lag_28 = lag_28 / ref_scale
            norm_roll_7 = roll_7 / ref_scale
            norm_roll_14 = roll_14 / ref_scale
            norm_roll_28 = roll_28 / ref_scale

            # Feature order matching trained benchmark:
            # [sales_lag_1, sales_lag_7, sales_lag_28, roll_7, roll_14, roll_28, wday, month, year]
            features = np.array([[
                norm_lag_1, norm_lag_7, norm_lag_28,
                norm_roll_7, norm_roll_14, norm_roll_28,
                target_wday, target_month, target_year
            ]], dtype=np.float32)

            norm_pred = float(booster.predict(features)[0])
            pred_val = float(np.clip(norm_pred * ref_scale, 0.0, None))
        else:
            # Seasonal Naive baseline projection (7-day seasonal lag)
            weekend_factor = 1.30 if target_wday in (5, 6, 7) else 0.95
            pred_val = roll_7 * weekend_factor

        pred_rounded = round(pred_val, 1)
        daily_predictions.append(pred_rounded)
        sim_history.append(pred_val)

    projected_demand = round(sum(daily_predictions), 1)

    # 6. Stockout Runway and Risk Assessment
    if avg_daily_sales > 0:
        days_of_supply = round(current_stock / avg_daily_sales, 1)
    else:
        days_of_supply = 999.0

    if current_stock <= 0:
        stockout_risk = "CRITICAL"
    elif days_of_supply < 3.0:
        stockout_risk = "CRITICAL"
    elif days_of_supply < 7.0:
        stockout_risk = "HIGH"
    elif days_of_supply < 14.0:
        stockout_risk = "MEDIUM"
    else:
        stockout_risk = "SAFE"

    model_label = "LightGBM Regressor (m5_lightgbm.txt)" if booster else "Seasonal Naive Baseline"

    return {
        "product_id": str(product_id),
        "product_name": product.name if product else "Unknown SKU",
        "sku": product.sku if product else "SKU",
        "category": product.category if product else "General",
        "horizon_days": horizon_days,
        "current_sellable_stock": current_stock,
        "avg_daily_sales": round(avg_daily_sales, 1),
        "projected_demand": projected_demand,
        "days_of_supply": days_of_supply,
        "stockout_risk": stockout_risk,
        "daily_forecast": daily_predictions,
        "data_source": data_source_label,
        "historical_days_recorded": len(daily_rows),
        "model_used": model_label,
    }
