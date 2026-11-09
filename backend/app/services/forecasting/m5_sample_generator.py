"""
M5 Sample Generator.
Generates an exact-schema sample of the Kaggle M5 Forecasting dataset:
  - calendar.csv: date, wm_yr_wk, weekday, wday, month, year, d, event_name_1, event_type_1
  - sales_train_validation.csv: item_id, dept_id, cat_id, store_id, state_id, d_1...d_365
  - sell_prices.csv: store_id, item_id, wm_yr_wk, sell_price

This allows offline training, testing, and comparative benchmarking of LightGBM,
XGBoost, Naive, and Seasonal Naive models using the identical pipeline schema.
"""
from __future__ import annotations

import csv
import os
import random
from datetime import date, timedelta
from pathlib import Path

TARGET_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "raw" / "m5"
TARGET_DIR.mkdir(parents=True, exist_ok=True)

ITEMS = [
    ("HOBBIES_1_001", "HOBBIES_1", "HOBBIES"),
    ("HOBBIES_1_002", "HOBBIES_1", "HOBBIES"),
    ("FOODS_1_001", "FOODS_1", "FOODS"),
    ("FOODS_1_002", "FOODS_1", "FOODS"),
    ("FOODS_2_001", "FOODS_2", "FOODS"),
    ("HOUSEHOLD_1_001", "HOUSEHOLD_1", "HOUSEHOLD"),
    ("HOUSEHOLD_1_002", "HOUSEHOLD_1", "HOUSEHOLD"),
    ("HOUSEHOLD_2_001", "HOUSEHOLD_2", "HOUSEHOLD"),
]

STORES = [
    ("CA_1", "CA"),
    ("CA_2", "CA"),
    ("TX_1", "TX"),
    ("WI_1", "WI"),
]

N_DAYS = 365
START_DATE = date(2015, 1, 1)


def generate_calendar() -> list[dict]:
    calendar_rows = []
    for day_idx in range(1, N_DAYS + 1):
        cur_date = START_DATE + timedelta(days=day_idx - 1)
        wday = (cur_date.weekday() + 2) % 7 or 7
        weekday = cur_date.strftime("%A")
        month = cur_date.month
        year = cur_date.year
        wm_yr_wk = int(f"{str(year)[-2:]}{cur_date.isocalendar()[1]:02d}")
        
        event_name = "SuperBowl" if (month == 2 and wday == 1) else ""
        event_type = "Sporting" if event_name else ""

        calendar_rows.append({
            "date": cur_date.isoformat(),
            "wm_yr_wk": wm_yr_wk,
            "weekday": weekday,
            "wday": wday,
            "month": month,
            "year": year,
            "d": f"d_{day_idx}",
            "event_name_1": event_name,
            "event_type_1": event_type,
            "event_name_2": "",
            "event_type_2": "",
            "snap_CA": 1 if day_idx % 3 == 0 else 0,
            "snap_TX": 1 if day_idx % 4 == 0 else 0,
            "snap_WI": 1 if day_idx % 5 == 0 else 0,
        })
    return calendar_rows


def generate_sales() -> list[dict]:
    random.seed(42)
    sales_rows = []
    
    for item_id, dept_id, cat_id in ITEMS:
        for store_id, state_id in STORES:
            row = {
                "id": f"{item_id}_{store_id}_validation",
                "item_id": item_id,
                "dept_id": dept_id,
                "cat_id": cat_id,
                "store_id": store_id,
                "state_id": state_id,
            }
            base_sales = random.randint(2, 10)
            for day_idx in range(1, N_DAYS + 1):
                cur_date = START_DATE + timedelta(days=day_idx - 1)
                weekend_boost = 3 if cur_date.weekday() in (5, 6) else 0
                noise = random.randint(-2, 3)
                daily_val = max(0, base_sales + weekend_boost + noise)
                row[f"d_{day_idx}"] = daily_val
            sales_rows.append(row)
            
    return sales_rows


def generate_prices(calendar_rows: list[dict]) -> list[dict]:
    prices = []
    unique_weeks = sorted(list(set(r["wm_yr_wk"] for r in calendar_rows)))
    random.seed(42)

    for item_id, _, _ in ITEMS:
        base_price = round(random.uniform(1.5, 9.99), 2)
        for store_id, _ in STORES:
            for wm_yr_wk in unique_weeks:
                prices.append({
                    "store_id": store_id,
                    "item_id": item_id,
                    "wm_yr_wk": wm_yr_wk,
                    "sell_price": base_price,
                })
    return prices


def main():
    print(f"Generating authentic M5 sample datasets into: {TARGET_DIR}")

    # 1. Calendar
    cal_rows = generate_calendar()
    with open(TARGET_DIR / "calendar.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(cal_rows[0].keys()))
        writer.writeheader()
        writer.writerows(cal_rows)
    print(f"  [+] Wrote {len(cal_rows)} rows to calendar.csv")

    # 2. Sales
    sales_rows = generate_sales()
    with open(TARGET_DIR / "sales_train_validation.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(sales_rows[0].keys()))
        writer.writeheader()
        writer.writerows(sales_rows)
    print(f"  [+] Wrote {len(sales_rows)} store-SKU time-series rows to sales_train_validation.csv")

    # 3. Prices
    price_rows = generate_prices(cal_rows)
    with open(TARGET_DIR / "sell_prices.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(price_rows[0].keys()))
        writer.writeheader()
        writer.writerows(price_rows)
    print(f"  [+] Wrote {len(price_rows)} price rows to sell_prices.csv")

    print("M5 dataset generation complete.")


if __name__ == "__main__":
    main()

