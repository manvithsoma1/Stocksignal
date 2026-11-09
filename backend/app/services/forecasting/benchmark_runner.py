"""
StockSignal Primary Demand Forecasting Benchmark Runner — Phase D.

Trains and comparatively evaluates:
  1. Naive Baseline: y_{t+h} = y_t
  2. Seasonal Naive Baseline: y_{t+h} = y_{t+h-7}
  3. LightGBM Regressor (Gradient Boosted Trees)
  4. XGBoost Regressor (Gradient Boosted Trees)

Using the official M5 Forecasting Accuracy Dataset schema:
  - Chronological Train / Test Split
  - Target: Next 28 days unit sales
  - Metrics: MAE, RMSE, WAPE, and Forecast Bias
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb

# Set project paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.services.forecasting.evaluator import ForecastEvaluator, ForecastMetrics

DATA_DIR = ROOT_DIR / "data" / "raw" / "m5"


def load_and_preprocess_m5(n_items: int = 150) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """
    Load M5 sales and calendar, melt to long format, engineer lag/rolling features,
    and split chronologically into train and test sets.
    """
    print(f"[*] Loading M5 dataset from: {DATA_DIR}")
    sales_path = DATA_DIR / "sales_train_validation.csv"
    calendar_path = DATA_DIR / "calendar.csv"

    if not sales_path.exists() or not calendar_path.exists():
        raise FileNotFoundError(f"Missing M5 files in {DATA_DIR}")

    # Load calendar
    calendar = pd.read_csv(calendar_path)
    calendar["date"] = pd.to_datetime(calendar["date"])
    
    # Load sales (sample items for fast, high-performance training)
    print(f"[*] Reading sales data (sampling {n_items} items for comparative benchmark)...")
    sales = pd.read_csv(sales_path)
    if len(sales) > n_items:
        sales = sales.iloc[:n_items].copy()

    # Identify day columns
    day_cols = [c for c in sales.columns if c.startswith("d_")]
    # Use last 180 days for training efficiency
    if len(day_cols) > 180:
        day_cols = day_cols[-180:]

    id_vars = ["item_id", "dept_id", "cat_id", "store_id", "state_id"]
    print(f"[*] Melting {len(sales)} store-SKU rows across {len(day_cols)} days to tabular format...")
    long_df = sales.melt(
        id_vars=id_vars,
        value_vars=day_cols,
        var_name="day_id",
        value_name="sales"
    )
    long_df["day_num"] = long_df["day_id"].str.replace("d_", "").astype(int)

    # Merge calendar dates
    date_map = calendar[["d", "date", "wday", "month", "year"]].copy()
    date_map["day_num"] = date_map["d"].str.replace("d_", "").astype(int)
    merged = long_df.merge(date_map, on="day_num", how="left")
    merged = merged.sort_values(["item_id", "store_id", "date"]).reset_index(drop=True)

    print("[*] Engineering lag and rolling features...")
    # Lags: 1, 7, 28 days
    for lag in [1, 7, 28]:
        merged[f"sales_lag_{lag}"] = merged.groupby(["item_id", "store_id"])["sales"].shift(lag)

    # Rolling windows: 7, 14, 28 days
    for window in [7, 14, 28]:
        merged[f"sales_rolling_mean_{window}"] = (
            merged.groupby(["item_id", "store_id"])["sales"]
            .transform(lambda x: x.rolling(window, min_periods=1).mean())
        )

    # Categorical encodings
    for cat in ["item_id", "dept_id", "cat_id", "store_id", "state_id"]:
        merged[cat] = merged[cat].astype("category")

    # Drop NaNs from lag creation
    merged = merged.dropna().reset_index(drop=True)

    feature_cols = [
        "sales_lag_1", "sales_lag_7", "sales_lag_28",
        "sales_rolling_mean_7", "sales_rolling_mean_14", "sales_rolling_mean_28",
        "wday", "month", "year"
    ]

    # Chronological Split: Last 28 days as Test Horizon
    max_date = merged["date"].max()
    split_date = max_date - pd.Timedelta(days=28)

    train_df = merged[merged["date"] <= split_date].copy()
    test_df = merged[merged["date"] > split_date].copy()

    print(f"[*] Chronological Split: Train rows={len(train_df)}, Test rows={len(test_df)}")
    return train_df, test_df, feature_cols


def run_benchmarks():
    print("=" * 70)
    print("  STOCKSIGNAL M5 DEMAND FORECASTING BENCHMARK SUITE")
    print("=" * 70)

    train_df, test_df, feature_cols = load_and_preprocess_m5(n_items=120)

    X_train = train_df[feature_cols]
    y_train = train_df["sales"].values
    X_test = test_df[feature_cols]
    y_test = test_df["sales"].values

    results = {}

    # 1. Naive Baseline (Last observed lag_1)
    print("\n[1/4] Evaluating Naive Baseline (y_{t} = y_{t-1})...")
    pred_naive = test_df["sales_lag_1"].values
    results["Naive Baseline"] = ForecastEvaluator.calculate_metrics(y_test, pred_naive)

    # 2. Seasonal Naive Baseline (y_{t} = y_{t-7})
    print("[2/4] Evaluating Seasonal Naive Baseline (7-Day Seasonality)...")
    pred_snaive = test_df["sales_lag_7"].values
    results["Seasonal Naive"] = ForecastEvaluator.calculate_metrics(y_test, pred_snaive)

    # 3. LightGBM Regressor
    print("\n[3/4] Training LightGBM Regressor...")
    t0 = time.time()
    lgb_model = lgb.LGBMRegressor(
        n_estimators=150,
        learning_rate=0.05,
        num_leaves=31,
        random_state=42,
        verbosity=-1
    )
    lgb_model.fit(X_train, y_train)
    lgb_time = time.time() - t0
    pred_lgb = lgb_model.predict(X_test)
    pred_lgb = np.clip(pred_lgb, 0, None)  # Demand cannot be negative
    results["LightGBM"] = ForecastEvaluator.calculate_metrics(y_test, pred_lgb)
    print(f"      Trained in {lgb_time:.2f}s")

    # 4. XGBoost Regressor
    print("\n[4/4] Training XGBoost Regressor...")
    t0 = time.time()
    xgb_model = xgb.XGBRegressor(
        n_estimators=150,
        learning_rate=0.05,
        max_depth=5,
        random_state=42,
        verbosity=0
    )
    xgb_model.fit(X_train, y_train)
    xgb_time = time.time() - t0
    pred_xgb = xgb_model.predict(X_test)
    pred_xgb = np.clip(pred_xgb, 0, None)
    results["XGBoost"] = ForecastEvaluator.calculate_metrics(y_test, pred_xgb)
    print(f"      Trained in {xgb_time:.2f}s")

    # Save model artifact
    models_dir = ROOT_DIR / "backend" / "app" / "services" / "forecasting" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    lgb_model.booster_.save_model(str(models_dir / "m5_lightgbm.txt"))
    print(f"\n[+] Saved LightGBM model artifact to: {models_dir / 'm5_lightgbm.txt'}")

    # Output Comparative Results Table
    print("\n" + "=" * 70)
    print(f"{'Model':<20} | {'MAE':<8} | {'RMSE':<8} | {'WAPE':<8} | {'Bias':<8}")
    print("-" * 70)
    for model_name, m in results.items():
        wape_str = f"{m.wape * 100:.2f}%" if not np.isnan(m.wape) else "N/A"
        bias_str = f"{m.bias * 100:+.2f}%" if not np.isnan(m.bias) else "N/A"
        print(f"{model_name:<20} | {m.mae:<8.3f} | {m.rmse:<8.3f} | {wape_str:<8} | {bias_str:<8}")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmarks()
