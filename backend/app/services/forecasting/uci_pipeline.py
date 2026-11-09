"""
UCI Online Retail II Dataset Adapter — Phase D.

Processes UK online retail transactions (Invoice, StockCode, Description,
Quantity, InvoiceDate, Price, Customer ID, Country).
Standardizes transaction events into daily aggregated demand.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd


class UCIRetailAdapter:
    """
    Adapter for UCI Online Retail II dataset:
      - Cleans negative cancellation quantities (Credit notes)
      - Standardizes stock codes and dates
      - Aggregates transactions into daily demand units per SKU
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def load_and_aggregate_daily(self, n_rows: int = 50_000) -> pd.DataFrame:
        excel_path = self.data_dir / "online_retail_II.xlsx"
        if not excel_path.exists():
            raise FileNotFoundError(f"online_retail_II.xlsx not in {self.data_dir}")

        # Read first sheet
        df = pd.read_excel(excel_path, nrows=n_rows)
        # Drop cancelled orders (invoice starting with 'C' or negative qty)
        df = df[df["Quantity"] > 0].copy()
        df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])
        df["date"] = df["InvoiceDate"].dt.date

        # Aggregate daily sales per item
        daily = (
            df.groupby(["StockCode", "date"])
            .agg(sales=("Quantity", "sum"), revenue=("Price", lambda p: (p * df.loc[p.index, "Quantity"]).sum()))
            .reset_index()
            .rename(columns={"StockCode": "item_id"})
        )
        return daily

