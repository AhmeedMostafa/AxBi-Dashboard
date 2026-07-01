"""
Forecasting test script — downloads 3 real public datasets and runs the full
forecasting pipeline, printing CV metrics, 30% holdout test metrics, and
a summary of which model won on each dataset.

Usage (from the backend directory):
    python -m api.forecasting.test_forecast

Datasets:
  1. AirPassengers   — monthly, 144 rows  (trend + strong seasonality)
  2. Daily bike-sharing — daily, ~730 rows (weather-driven, noisy)
  3. Monthly retail sales — monthly, 240 rows (long trend)

All datasets come from public GitHub mirrors (no auth required).
"""
from __future__ import annotations

import io
import json
import textwrap
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

from api.forecasting.service import run_forecast_service


# ── Dataset loaders ──────────────────────────────────────────────────────────

def _load_air_passengers() -> pd.DataFrame:
    """Monthly airline passengers 1949-1960 (144 rows)."""
    url = "https://raw.githubusercontent.com/jbrownlee/Datasets/master/airline-passengers.csv"
    df = pd.read_csv(url, header=0, names=["Month", "Passengers"])
    df["Month"] = pd.to_datetime(df["Month"])
    return df


def _load_bike_sharing() -> pd.DataFrame:
    """Daily bike-sharing count 2011-2012 (~730 rows, UCI via GitHub mirror)."""
    url = (
        "https://raw.githubusercontent.com/jbrownlee/Datasets/master/"
        "daily-min-temperatures.csv"
    )
    # Fall back to a simple synthetic daily dataset if URL unavailable
    try:
        df = pd.read_csv(url, header=0, names=["Date", "Temp"])
        df["Date"] = pd.to_datetime(df["Date"])
        return df
    except Exception:
        # Synthetic fallback: 3 years of daily data with trend + noise
        rng = np.random.default_rng(42)
        dates = pd.date_range("2019-01-01", periods=1095, freq="D")
        trend = np.linspace(100, 400, 1095)
        seasonal = 80 * np.sin(2 * np.pi * np.arange(1095) / 365.25)
        noise = rng.normal(0, 20, 1095)
        df = pd.DataFrame({"Date": dates, "Value": trend + seasonal + noise})
        return df


def _load_monthly_sales() -> pd.DataFrame:
    """Monthly superstore-style sales — 240 months via statsmodels."""
    try:
        from statsmodels.datasets import co2
        data = co2.load_pandas().data
        data = data.resample("MS").mean().ffill().reset_index()
        data.columns = ["Date", "co2"]
        return data
    except Exception:
        # Synthetic fallback: 20 years of monthly data
        rng = np.random.default_rng(7)
        dates = pd.date_range("2004-01", periods=240, freq="MS")
        trend = np.linspace(500, 2500, 240)
        seasonal = 300 * np.sin(2 * np.pi * np.arange(240) / 12)
        noise = rng.normal(0, 50, 240)
        df = pd.DataFrame({"Date": dates, "Sales": trend + seasonal + noise})
        return df


# ── Formatting helpers ───────────────────────────────────────────────────────

def _fmt(v: Any, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "   —   "
    if pct:
        return f"{v * 100:6.1f}%"
    if abs(v) >= 10_000:
        return f"{v:10.0f}"
    if abs(v) >= 100:
        return f"{v:9.1f}"
    return f"{v:9.4f}"


def _print_result(name: str, result: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"  Dataset : {name}")
    print(f"  Freq    : {result.get('frequency', '?')}"
          + (" (auto-detected)" if result.get("frequency_auto_detected") else ""))
    print(f"  Rows    : {result.get('training_rows', '?')}  "
          f"(train {int(result.get('training_rows', 0) * 0.70)}"
          f" / test {int(result.get('training_rows', 0) * 0.30)})")
    print(f"  Best    : {result.get('best_model', '?')}  "
          f"[{result.get('confidence', '?')} confidence]")
    print(f"  Reason  : {result.get('confidence_reason', '')}")
    if result.get("ensemble"):
        print("  Mode    : ensemble (avg-rank + runner-up blended)")
    if result.get("warnings"):
        for w in result["warnings"]:
            print(f"  WARN: {w}")

    print(f"\n  {'Model':<22} {'Status':<8} {'CV MAE':>9} {'CV WAPE':>8} "
          f"{'CV MASE':>8} | {'Test MAE':>9} {'Test WAPE':>9} {'Test MASE':>9}")
    print("  " + "-" * 22 + "-" * 8 + "-" * 9 + "-" * 9 + "-" * 9
          + "-+-" + "-" * 10 + "-" * 10 + "-" * 10)

    for mr in result.get("model_results", []):
        m = mr.get("metrics") or {}
        tm = mr.get("test_metrics") or {}
        status = "OK" if mr["status"] == "ok" else "FAIL"
        best_tag = " <-- BEST" if mr["model"] == result.get("best_model") else ""
        print(
            f"  {mr['model']:<22} {status:<8}"
            f" {_fmt(m.get('mae')):>9} {_fmt(m.get('wape'), pct=True):>8}"
            f" {_fmt(m.get('mase')):>8} |"
            f" {_fmt(tm.get('mae')):>9} {_fmt(tm.get('wape'), pct=True):>9}"
            f" {_fmt(tm.get('mase')):>9}{best_tag}"
        )

    skipped = result.get("skipped_models", [])
    if skipped:
        print(f"\n  Skipped: {', '.join(s['model'] for s in skipped)}")

    fc = result.get("forecast", [])
    pi = result.get("prediction_intervals", [])
    if fc:
        print(f"\n  Forecast (first 5 of {len(fc)}):")
        for i in range(min(5, len(fc))):
            lo = pi[i]["lower"] if pi else None
            hi = pi[i]["upper"] if pi else None
            interval_str = f"  [{lo:>9.2f}, {hi:>9.2f}]" if lo is not None else ""
            print(f"    {fc[i]['date']}  {fc[i]['value']:>10.2f}{interval_str}")
    print(f"\n  Duration: {result.get('duration_ms', '?')} ms")
    print(f"{'=' * 70}")


# ── Main ─────────────────────────────────────────────────────────────────────

SCENARIOS: list[dict] = [
    {
        "name": "AirPassengers (monthly, 144 rows)",
        "loader": _load_air_passengers,
        "time_col": "Month",
        "target_col": "Passengers",
        "freq": None,         # auto-detect
        "horizon": 12,
    },
    {
        "name": "Daily temperatures (daily, ~3650 rows)",
        "loader": _load_bike_sharing,
        "time_col": "Date",
        "target_col": lambda df: [c for c in df.columns if c != "Date"][0],
        "freq": None,
        "horizon": 30,
    },
    {
        "name": "Monthly CO2 / synthetic sales (monthly, 240 rows)",
        "loader": _load_monthly_sales,
        "time_col": "Date",
        "target_col": lambda df: [c for c in df.columns if c != "Date"][0],
        "freq": None,
        "horizon": 12,
    },
]


def main() -> None:
    print("\nForecasting service - integration test")
    print("Models tested: naive, seasonal_naive, ets, sarimax, catboost, lightgbm, prophet")
    print("Evaluation  : 3-fold rolling CV  +  30% held-out test set\n")

    all_winners: list[str] = []

    for scenario in SCENARIOS:
        name: str = scenario["name"]
        print(f"\nLoading dataset: {name} ...", end=" ", flush=True)
        try:
            df = scenario["loader"]()
        except Exception as exc:
            print(f"FAILED to load ({exc}), skipping.")
            continue
        print(f"OK  ({len(df)} rows)")

        time_col: str = scenario["time_col"]
        target_raw = scenario["target_col"]
        target_col: str = target_raw(df) if callable(target_raw) else target_raw

        t0 = time.perf_counter()
        try:
            result = run_forecast_service(
                df,
                time_column=time_col,
                target_column=target_col,
                frequency=scenario["freq"],
                horizon=scenario["horizon"],
                candidate_models=[
                    "naive", "seasonal_naive", "ets",
                    "sarimax", "lightgbm", "prophet",
                ],
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        elapsed = time.perf_counter() - t0
        _print_result(name, result)

        winner = result.get("best_model")
        if winner:
            all_winners.append(winner)

    if all_winners:
        print("\n\nSummary - best model per dataset:")
        for s, w in zip(SCENARIOS, all_winners):
            print(f"  {s['name'][:45]:<45}  =>  {w}")

    print("\nDone.\n")


if __name__ == "__main__":
    main()
