"""
Ad-hoc stress test: run the compute-heavy pipeline stages directly on the large
generated datasets (no Celery / Supabase / Gemini needed). Validates the system
handles 400k-row files per category.

Stages exercised per category:
  Step 3  preprocess_dataframe        (cleaning)
  Step 4  run_step4                    (technical profiling)
  Forecast run_forecast_service        (model competition, fast models only)
  Segment run_segmentation_service     (RFM/ABC/K-Means, rule-based insights)

Run: backend/venv/Scripts/python.exe backend/stress_test_large.py
"""
import io
import time
import traceback
from pathlib import Path

import pandas as pd

from preprocessing.cleaning import preprocess_dataframe
from api.processing.step4_column_detection import run_step4
from api.forecasting.service import run_forecast_service
from api.segmentation.service import run_segmentation_service

DATA = Path(__file__).resolve().parent.parent / "docs" / "sample-datasets"

# Fast, always-available models — avoids multi-minute SARIMAX/Prophet fits while
# still pushing the full data-prep / backtest path through 400k rows.
FAST_MODELS = ["naive", "seasonal_naive", "ets"]

CASES = {
    "sales":      {"time": "date", "target": "gross_revenue"},
    "marketing":  {"time": "date", "target": "revenue"},
    "operations": {"time": "date", "target": "units_produced"},
    "hr":         {"time": "date", "target": "headcount"},
}

SIZE = "400k"


def timed(label, fn):
    t = time.perf_counter()
    try:
        out = fn()
        dt = time.perf_counter() - t
        print(f"    [OK]   {label:<10} {dt:7.2f}s")
        return out, dt, None
    except Exception as e:
        dt = time.perf_counter() - t
        print(f"    [FAIL] {label:<10} {dt:7.2f}s  -> {type(e).__name__}: {e}")
        traceback.print_exc()
        return None, dt, e


def run_case(cat, cfg):
    path = DATA / f"{cat}_timeseries_{SIZE}.csv"
    print(f"\n=== {cat.upper()} ({path.name}) ===")
    raw = path.read_bytes()
    df_raw = pd.read_csv(io.BytesIO(raw))
    print(f"    rows={len(df_raw):,}  cols={len(df_raw.columns)}  size={len(raw)/1e6:.1f}MB")

    # Step 3 — cleaning
    df_clean, _, _ = timed("step3", lambda: preprocess_dataframe(df_raw))

    # Step 4 — profiling (needs bytes)
    step4, _, _ = timed("step4", lambda: run_step4(raw, path.name))

    # Forecast — fast models
    fc, _, _ = timed("forecast", lambda: run_forecast_service(
        df_clean if df_clean is not None else df_raw,
        time_column=cfg["time"],
        target_column=cfg["target"],
        candidate_models=FAST_MODELS,
        horizon=14,
    ))
    if fc:
        g = fc.get("series_grain", {})
        print(f"           freq={fc.get('frequency')} auto={fc.get('frequency_auto_detected')} "
              f"grain: {g.get('input_rows'):,} rows -> {g.get('series_points'):,} periods "
              f"(sum across {g.get('collapsed_dimensions')})")

    # Bad-model validation must fail loud
    try:
        run_forecast_service(df_raw, time_column=cfg["time"], target_column=cfg["target"],
                             candidate_models=["theta"])
        print("           [WARN] bad model 'theta' did NOT raise")
    except Exception as e:
        print(f"           validation OK: {type(e).__name__}: {e}")

    # Segmentation — uses Step 4 column metadata, rule-based insights (no Gemini)
    if step4 is not None:
        timed("segment", lambda: run_segmentation_service(
            df_clean if df_clean is not None else df_raw,
            columns_metadata=step4["columns"],
            category_hint=cat,
        ))


def main():
    print(f"Stress test @ {SIZE} per category\n" + "=" * 50)
    t0 = time.perf_counter()
    for cat, cfg in CASES.items():
        run_case(cat, cfg)
    print("\n" + "=" * 50)
    print(f"Total wall time: {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
