"""
Benchmark the forecasting pipeline across prepared Kaggle datasets.

For each dataset:
  1. Load train split, run run_forecast_service with horizon = len(val).
  2. Compare predicted values against held-out validation actuals.
  3. Report per-model CV metrics (from the service) and out-of-sample accuracy.

Usage:
    cd backend
    python data/benchmark.py
"""

import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import django
django.setup()

import numpy as np
import pandas as pd

from api.forecasting.service import run_forecast_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATASETS = [
    {
        "name": "store-sales",
        "time_column": "date",
        "target_column": "total_revenue",
    },
    {
        "name": "superstore",
        "time_column": "date",
        "target_column": "total_revenue",
    },
    {
        "name": "online-retail-ii",
        "time_column": "date",
        "target_column": "total_revenue",
    },
]


def _load_split(dataset_name: str, split: str) -> pd.DataFrame:
    path = os.path.join(BASE_DIR, dataset_name, f"prepared_{split}.csv")
    return pd.read_csv(path)


def _compute_oos_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    """Compute out-of-sample accuracy metrics."""
    errors = actual - predicted
    abs_errors = np.abs(errors)
    mae = float(np.mean(abs_errors))
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    wape = float(np.sum(abs_errors) / np.sum(np.abs(actual))) if np.sum(np.abs(actual)) > 0 else float("inf")

    naive_errors = np.abs(np.diff(actual))
    mase = float(mae / np.mean(naive_errors)) if len(naive_errors) > 0 and np.mean(naive_errors) > 1e-9 else float("inf")

    return {"mae": mae, "rmse": rmse, "wape": wape, "mase": mase}


def benchmark_dataset(config: dict, max_horizon: int = 30) -> dict:
    """Run forecast on one dataset and evaluate against validation set."""
    name = config["name"]
    print(f"\n{'='*60}")
    print(f"  BENCHMARKING: {name}")
    print(f"{'='*60}")

    train_df = _load_split(name, "train")
    val_df = _load_split(name, "val")
    test_df = _load_split(name, "test")

    horizon = min(len(val_df), max_horizon)

    print(f"  Train: {len(train_df)} days | Val: {len(val_df)} days | Test: {len(test_df)} days")
    print(f"  Forecast horizon: {horizon}")

    t0 = time.time()
    result = run_forecast_service(
        train_df,
        time_column=config["time_column"],
        target_column=config["target_column"],
        horizon=horizon,
    )
    elapsed = time.time() - t0

    if not result.get("forecast_possible", False):
        print(f"  FAILED: {result.get('message', 'Unknown error')}")
        return {"name": name, "status": "failed", "message": result.get("message")}

    predicted = np.array([f["value"] for f in result["forecast"]])
    actual_vals = val_df[config["target_column"]].values[:horizon].astype(float)

    oos_metrics = _compute_oos_metrics(actual_vals, predicted)

    print(f"\n  Best model: {result['best_model']}")
    print(f"  Ensemble: {result.get('ensemble', False)}")
    print(f"  Confidence: {result['confidence']} - {result.get('confidence_reason', '')}")
    print(f"  Duration: {elapsed:.1f}s")

    print(f"\n  --- CV Metrics (in-sample, from service) ---")
    cv = result.get("metrics", {})
    print(f"    MAE:  {cv.get('mae', 'N/A'):>12}")
    print(f"    RMSE: {cv.get('rmse', 'N/A'):>12}")
    print(f"    WAPE: {cv.get('wape', 'N/A'):>12}")
    print(f"    MASE: {cv.get('mase', 'N/A'):>12}")

    print(f"\n  --- Out-of-Sample Metrics (predicted vs actual val) ---")
    print(f"    MAE:  {oos_metrics['mae']:>12.2f}")
    print(f"    RMSE: {oos_metrics['rmse']:>12.2f}")
    print(f"    WAPE: {oos_metrics['wape']:>12.4f}")
    print(f"    MASE: {oos_metrics['mase']:>12.4f}")

    print(f"\n  --- Model Competition ---")
    for mr in result.get("model_results", []):
        m = mr["metrics"]
        star = " *" if mr["model"] in result["best_model"] else ""
        print(f"    {mr['model']:20s}  MAE={m['mae']:12.2f}  RMSE={m['rmse']:12.2f}  "
              f"WAPE={m['wape']:.4f}  MASE={m['mase']:.4f}{star}")

    if result.get("warnings"):
        print(f"\n  --- Warnings ---")
        for w in result["warnings"]:
            print(f"    - {w}")

    # Interval coverage on actual validation data
    intervals = result.get("prediction_intervals", [])
    if intervals:
        covered = sum(
            1 for i in range(min(len(intervals), len(actual_vals)))
            if intervals[i]["lower"] <= actual_vals[i] <= intervals[i]["upper"]
        )
        coverage = covered / min(len(intervals), len(actual_vals))
        print(f"\n  --- Prediction Interval Coverage (on val actuals) ---")
        print(f"    {covered}/{min(len(intervals), len(actual_vals))} = {coverage:.1%}")

    return {
        "name": name,
        "status": "ok",
        "best_model": result["best_model"],
        "ensemble": result.get("ensemble", False),
        "confidence": result["confidence"],
        "cv_metrics": cv,
        "oos_metrics": oos_metrics,
        "duration_s": elapsed,
        "horizon": horizon,
    }


def main():
    print("=" * 60)
    print("  FORECASTING BENCHMARK SUITE")
    print("=" * 60)

    all_results = []
    for config in DATASETS:
        try:
            r = benchmark_dataset(config)
            all_results.append(r)
        except Exception as e:
            print(f"\n  ERROR on {config['name']}: {e}")
            all_results.append({"name": config["name"], "status": "error", "error": str(e)})

    print("\n\n" + "=" * 60)
    print("  FINAL SUMMARY")
    print("=" * 60)
    print(f"  {'Dataset':<20s}  {'Model':<22s}  {'OOS MAE':>12s}  {'OOS RMSE':>12s}  "
          f"{'OOS WAPE':>10s}  {'OOS MASE':>10s}  {'Time':>6s}")
    print("  " + "-" * 100)

    for r in all_results:
        if r["status"] == "ok":
            oos = r["oos_metrics"]
            print(f"  {r['name']:<20s}  {r['best_model']:<22s}  "
                  f"{oos['mae']:>12.2f}  {oos['rmse']:>12.2f}  "
                  f"{oos['wape']:>10.4f}  {oos['mase']:>10.4f}  "
                  f"{r['duration_s']:>5.1f}s")
        else:
            print(f"  {r['name']:<20s}  {'FAILED':<22s}")

    print()


if __name__ == "__main__":
    main()
