"""
Favorita Grocery Sales Dataset Adapter — Phase D.

Adapts Corporación Favorita tabular transactions and item records into
the StockSignal analytical forecasting schema without mixing raw schemas with M5.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd


class FavoritaAdapter:
    """
    Adapter for Corporación Favorita Grocery Sales dataset:
      - train.csv: id, date, store_nbr, item_nbr, unit_sales, onpromotion
      - stores.csv: store_nbr, city, state, type, cluster
      - items.csv: item_nbr, family, class, perishable
      - holidays_events.csv: date, type, locale, locale_name, description, transferred
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def validate_files(self) -> dict[str, bool]:
        expected = ["stores.csv", "items.csv", "transactions.csv", "holidays_events.csv", "oil.csv"]
        return {f: (self.data_dir / f).exists() for f in expected}

    def load_store_item_metadata(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        stores = pd.read_csv(self.data_dir / "stores.csv")
        items = pd.read_csv(self.data_dir / "items.csv")
        return stores, items

    def load_daily_sales_sample(self, n_rows: int = 100_000) -> pd.DataFrame:
        """
        Stream a sample from the large train.csv to allow lightweight processing.
        """
        train_path = self.data_dir / "train.csv"
        if not train_path.exists():
            raise FileNotFoundError(f"train.csv not found in {self.data_dir}")

        df = pd.read_csv(train_path, nrows=n_rows)
        df["date"] = pd.to_datetime(df["date"])
        # Standardize to common analytical columns
        df = df.rename(columns={
            "store_nbr": "store_id",
            "item_nbr": "item_id",
            "unit_sales": "sales",
        })
        return df
