"""
Prepare Kaggle datasets for forecasting benchmarks.

Loads each raw dataset, aggregates to daily revenue, and splits into
train (70%), validation (15%), and test (15%) by chronological order.

Output structure per dataset:
    data/<name>/train.csv   (columns: date, total_revenue)
    data/<name>/val.csv
    data/<name>/test.csv
"""

import os
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _split_and_save(daily: pd.DataFrame, name: str):
    """Split a date-sorted daily DataFrame into train/val/test and save."""
    daily = daily.sort_values("date").reset_index(drop=True)

    n = len(daily)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    train = daily.iloc[:train_end]
    val = daily.iloc[train_end:val_end]
    test = daily.iloc[val_end:]

    out_dir = os.path.join(BASE_DIR, name)
    os.makedirs(out_dir, exist_ok=True)

    train.to_csv(os.path.join(out_dir, "prepared_train.csv"), index=False)
    val.to_csv(os.path.join(out_dir, "prepared_val.csv"), index=False)
    test.to_csv(os.path.join(out_dir, "prepared_test.csv"), index=False)

    print(f"  {name}: {n} days -> train={len(train)}, val={len(val)}, test={len(test)}")
    print(f"    date range: {daily['date'].min()} to {daily['date'].max()}")
    print(f"    revenue: mean={daily['total_revenue'].mean():.2f}, "
          f"std={daily['total_revenue'].std():.2f}")
    return train, val, test


def prepare_store_sales():
    """Store Sales (Favorita): aggregate across all stores/families per day."""
    print("\n=== Store Sales (Favorita) ===")
    path = os.path.join(BASE_DIR, "store-sales", "train.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])

    daily = df.groupby("date")["sales"].sum().reset_index()
    daily.columns = ["date", "total_revenue"]
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    return _split_and_save(daily, "store-sales")


def prepare_superstore():
    """Superstore: aggregate by Order Date."""
    print("\n=== Superstore ===")
    train_path = os.path.join(BASE_DIR, "superstore", "superstore_train.csv")
    test_path = os.path.join(BASE_DIR, "superstore", "superstore_test.csv")

    frames = [pd.read_csv(train_path)]
    if os.path.exists(test_path):
        frames.append(pd.read_csv(test_path))
    df = pd.concat(frames, ignore_index=True)

    df["Order Date"] = pd.to_datetime(df["Order Date"])
    daily = df.groupby("Order Date")["Sales"].sum().reset_index()
    daily.columns = ["date", "total_revenue"]
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    return _split_and_save(daily, "superstore")


def prepare_online_retail_ii():
    """Online Retail II: compute line revenue = Quantity * Price, aggregate daily."""
    print("\n=== Online Retail II ===")
    path = os.path.join(BASE_DIR, "online-retail-ii", "online_retail_II.csv")
    df = pd.read_csv(path, encoding="latin1")

    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="coerce")
    df = df.dropna(subset=["InvoiceDate"])

    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df = df.dropna(subset=["Quantity", "Price"])

    df["line_revenue"] = df["Quantity"] * df["Price"]

    daily = df.groupby(df["InvoiceDate"].dt.date)["line_revenue"].sum().reset_index()
    daily.columns = ["date", "total_revenue"]
    daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")

    return _split_and_save(daily, "online-retail-ii")


if __name__ == "__main__":
    print("Preparing datasets for forecasting benchmarks...")

    results = {}
    results["store-sales"] = prepare_store_sales()
    results["superstore"] = prepare_superstore()
    results["online-retail-ii"] = prepare_online_retail_ii()

    print("\n=== Summary ===")
    for name, (train, val, test) in results.items():
        print(f"  {name}: train={len(train)} val={len(val)} test={len(test)}")
    print("\nDone.")
