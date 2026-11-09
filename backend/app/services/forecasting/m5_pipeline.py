"""
M5 Forecasting Accuracy Dataset Pipeline.

Ingests M5 Walmart data, validates schema, engineers features, and prepares
for forecasting model evaluation.

Reference: https://www.kaggle.com/competitions/m5-forecasting-accuracy/data
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


@dataclass
class M5Config:
    """Configuration for M5 data pipeline."""
    
    # Data paths
    data_dir: str = "data/m5"
    sales_file: str = "sales_train_validation.csv"
    calendar_file: str = "calendar.csv"
    prices_file: str = "sell_prices.csv"
    
    # Feature engineering
    lag_days: list[int] = field(default_factory=lambda: [1, 7, 28])
    rolling_windows: list[int] = field(default_factory=lambda: [7, 14, 28])
    
    # Evaluation
    validation_start: str = "2016-03-28"  # M5 validation period start
    test_start: str = "2016-04-25"  # M5 test period start
    forecast_horizon: int = 28  # days
    
    # Processing
    use_sample: bool = False  # Use small sample for testing without full data
    sample_n_items: int = 100  # Number of items to sample


class M5Pipeline:
    """
    Pipeline for M5 dataset ingestion, validation, and feature engineering.
    
    Expected M5 files (from Kaggle):
    - sales_train_validation.csv: item_id, dept_id, cat_id, store_id, state_id, d_1...d_1913
    - calendar.csv: date, wm_yr_wk, weekday, wday, month, year, event_name_1, event_type_1
    - sell_prices.csv: store_id, item_id, wm_yr_wk, sell_price
    """
    
    def __init__(self, config: M5Config | None = None):
        self.cfg = config or M5Config()
        self.data_dir = Path(self.cfg.data_dir)
        
    def validate_data_files(self) -> dict[str, bool]:
        """Check if required M5 files exist."""
        required = {
            "sales": self.data_dir / self.cfg.sales_file,
            "calendar": self.data_dir / self.cfg.calendar_file,
            "prices": self.data_dir / self.cfg.prices_file,
        }
        return {name: path.exists() for name, path in required.items()}
    
    def load_sales(self) -> pd.DataFrame:
        """
        Load sales data with validation.
        
        Returns:
            DataFrame with columns: item_id, dept_id, cat_id, store_id, state_id, d_1...d_N
        """
        sales_path = self.data_dir / self.cfg.sales_file
        
        if not sales_path.exists():
            raise FileNotFoundError(
                f"M5 sales file not found at {sales_path}. "
                "Download from https://www.kaggle.com/competitions/m5-forecasting-accuracy/data"
            )
        
        df = pd.read_csv(sales_path)
        
        # Validate schema
        required_cols = {"item_id", "dept_id", "cat_id", "store_id", "state_id"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Sales data missing required columns: {missing}")
        
        # Sample if configured
        if self.cfg.use_sample:
            unique_items = df["item_id"].unique()
            sampled_items = np.random.choice(
                unique_items, 
                min(len(unique_items), self.cfg.sample_n_items),
                replace=False
            )
            df = df[df["item_id"].isin(sampled_items)]
        
        return df
    
    def load_calendar(self) -> pd.DataFrame:
        """Load calendar data with date parsing."""
        calendar_path = self.data_dir / self.cfg.calendar_file
        
        if not calendar_path.exists():
            raise FileNotFoundError(
                f"M5 calendar file not found at {calendar_path}. "
                "Download from https://www.kaggle.com/competitions/m5-forecasting-accuracy/data"
            )
        
        df = pd.read_csv(calendar_path)
        df["date"] = pd.to_datetime(df["date"])
        
        return df
    
    def load_prices(self) -> pd.DataFrame:
        """Load sell prices data."""
        prices_path = self.data_dir / self.cfg.prices_file
        
        if not prices_path.exists():
            raise FileNotFoundError(
                f"M5 prices file not found at {prices_path}. "
                "Download from https://www.kaggle.com/competitions/m5-forecasting-accuracy/data"
            )
        
        df = pd.read_csv(prices_path)
        
        return df
    
    def melt_sales_to_long_format(self, sales_df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert wide-format sales (d_1, d_2, ...) to long format.
        
        Returns:
            DataFrame with columns: item_id, store_id, date, sales
        """
        # Identify day columns (d_1, d_2, ...)
        day_cols = [c for c in sales_df.columns if c.startswith("d_")]
        
        # Melt to long format
        id_vars = ["item_id", "dept_id", "cat_id", "store_id", "state_id"]
        long_df = sales_df.melt(
            id_vars=id_vars,
            value_vars=day_cols,
            var_name="day_id",
            value_name="sales"
        )
        
        # Convert day_id to integer
        long_df["day_num"] = long_df["day_id"].str.replace("d_", "").astype(int)
        
        return long_df
    
    def merge_calendar(self, long_sales: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
        """Merge sales with calendar to add dates."""
        # Create date mapping from calendar
        date_map = calendar[["d", "date"]].copy()
        date_map["day_num"] = date_map["d"].str.replace("d_", "").astype(int)
        
        # Merge
        merged = long_sales.merge(date_map[["day_num", "date"]], on="day_num", how="left")
        
        return merged
    
    def engineer_lag_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add lag features for time series forecasting."""
        df = df.sort_values(["item_id", "store_id", "date"])
        
        for lag in self.cfg.lag_days:
            df[f"sales_lag_{lag}"] = df.groupby(["item_id", "store_id"])["sales"].shift(lag)
        
        return df
    
    def engineer_rolling_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add rolling window features."""
        df = df.sort_values(["item_id", "store_id", "date"])
        
        for window in self.cfg.rolling_windows:
            df[f"sales_rolling_mean_{window}"] = (
                df.groupby(["item_id", "store_id"])["sales"]
                .transform(lambda x: x.rolling(window, min_periods=1).mean())
            )
            df[f"sales_rolling_std_{window}"] = (
                df.groupby(["item_id", "store_id"])["sales"]
                .transform(lambda x: x.rolling(window, min_periods=1).std())
            )
        
        return df
    
    def create_train_test_split(
        self, 
        df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Create chronological train/validation/test splits.
        
        Returns:
            (train_df, test_df) where test_df contains the forecast horizon
        """
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        
        # Define splits based on M5 evaluation setup
        train_end = pd.to_datetime(self.cfg.validation_start) - timedelta(days=1)
        validation_end = pd.to_datetime(self.cfg.test_start) - timedelta(days=1)
        
        train_df = df[df["date"] <= train_end].copy()
        test_df = df[df["date"] > validation_end].copy()
        
        return train_df, test_df
    
    def run(self) -> dict:
        """
        Execute full M5 pipeline.
        
        Returns:
            Summary dict with statistics about processed data
        """
        # Check files
        file_status = self.validate_data_files()
        if not all(file_status.values()):
            missing = [k for k, v in file_status.items() if not v]
            raise FileNotFoundError(
                f"Missing M5 data files: {missing}. "
                "Download from https://www.kaggle.com/competitions/m5-forecasting-accuracy/data"
            )
        
        # Load data
        sales = self.load_sales()
        calendar = self.load_calendar()
        prices = self.load_prices()
        
        # Transform
        long_sales = self.melt_sales_to_long_format(sales)
        merged = self.merge_calendar(long_sales, calendar)
        
        # Feature engineering
        with_lags = self.engineer_lag_features(merged)
        with_features = self.engineer_rolling_features(with_lags)
        
        # Split
        train, test = self.create_train_test_split(with_features)
        
        # Summary
        summary = {
            "total_items": sales["item_id"].nunique(),
            "total_stores": sales["store_id"].nunique(),
            "total_days": len(calendar),
            "train_rows": len(train),
            "test_rows": len(test),
            "features": [c for c in train.columns if c.startswith(("sales_lag", "sales_rolling"))],
            "date_range": f"{calendar['date'].min()} to {calendar['date'].max()}",
        }
        
        return summary
