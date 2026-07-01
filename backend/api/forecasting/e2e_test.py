"""
End-to-End UI Test Script for AxBi Forecasting
====================================================
Tests the full flow: Login → Upload CSV → Wait for Pipeline → Run Forecast → Verify Results

Usage:
    cd backend
    python -m api.forecasting.e2e_test

Requirements: all services must be running:
  - Redis          : redis-server
  - Django         : venv/Scripts/python.exe manage.py runserver
  - Celery         : venv/Scripts/python.exe -m celery -A core worker --loglevel=info --pool=solo
  - (Frontend is optional for this script)
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import requests

# ─── Config ──────────────────────────────────────────────────────────────────

API_BASE   = os.environ.get("API_BASE", "http://127.0.0.1:8000/api")
SUPABASE_URL = None   # auto-read from .env
SUPABASE_KEY = None   # auto-read from .env

POLL_INTERVAL_S = 3
MAX_WAIT_S      = 360   # 6 minutes max for pipeline

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):  print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):print(f"  {RED}✗{RESET}  {msg}"); sys.exit(1)
def info(msg):print(f"  {CYAN}→{RESET}  {msg}")
def warn(msg):print(f"  {YELLOW}!{RESET}  {msg}")
def sep():    print(f"\n{'─'*60}\n")

# ─── Load .env ────────────────────────────────────────────────────────────────

def _load_env():
    env_path = Path(__file__).parent.parent.parent / ".env"   # backend/.env
    if not env_path.exists():
        fail(f".env not found at {env_path}")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ─── Step 0: Check services ──────────────────────────────────────────────────

def check_services():
    print(f"\n{BOLD}Step 0 — Checking services{RESET}")
    # Django
    try:
        r = requests.get(f"{API_BASE}/check/healthcheck/", timeout=4)
        ok(f"Django API is reachable at {API_BASE}")
    except Exception:
        try:
            r = requests.get("http://127.0.0.1:8000/", timeout=4)
            ok(f"Django is up (got {r.status_code})")
        except Exception as e:
            fail(f"Django not reachable at {API_BASE} — start it first\n     Error: {e}")
    # Redis
    try:
        import redis as redis_lib
        rc = redis_lib.Redis(host="localhost", port=6379, socket_connect_timeout=2)
        rc.ping()
        ok("Redis is reachable on localhost:6379")
    except Exception as e:
        fail(f"Redis not reachable — start redis-server first\n     Error: {e}")

# ─── Step 1: Auth ─────────────────────────────────────────────────────────────

def get_token(email: str, password: str) -> str:
    print(f"\n{BOLD}Step 1 — Authenticating with Supabase{RESET}")
    if not SUPABASE_URL or not SUPABASE_KEY:
        fail("SUPABASE_URL or SUPABASE_SERVICE_KEY not set in .env")

    # Use Supabase REST auth endpoint
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    payload = {"email": email, "password": password}
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    if r.status_code != 200:
        fail(f"Auth failed ({r.status_code}): {r.text[:200]}")
    token = r.json().get("access_token", "")
    if not token:
        fail(f"No access_token in response: {r.json()}")
    ok(f"Logged in as {email}")
    return token

# ─── Step 2: Upload CSV ───────────────────────────────────────────────────────

def upload_csv(token: str, csv_path: str, category: str = "sales") -> tuple[str, str]:
    print(f"\n{BOLD}Step 2 — Uploading CSV{RESET}")
    info(f"File: {csv_path}")
    if not Path(csv_path).exists():
        fail(f"File not found: {csv_path}")

    with open(csv_path, "rb") as f:
        file_bytes = f.read()

    files = {"file": (Path(csv_path).name, io.BytesIO(file_bytes), "text/csv")}
    data  = {"category": category}
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.post(f"{API_BASE}/file-upload/", files=files, data=data,
                      headers=headers, timeout=30)
    if r.status_code not in (200, 201, 202):
        fail(f"Upload failed ({r.status_code}): {r.text[:300]}")

    resp = r.json()
    dataset_id = resp.get("dataset_id") or resp.get("id")
    job_id     = resp.get("job_id") or resp.get("tracking_job_id") or dataset_id

    if not dataset_id:
        fail(f"No dataset_id in upload response: {resp}")

    ok(f"Uploaded successfully")
    ok(f"Dataset ID : {dataset_id}")
    ok(f"Job ID     : {job_id}")
    return dataset_id, job_id

# ─── Step 3: Poll pipeline ────────────────────────────────────────────────────

def wait_for_pipeline(token: str, job_id: str) -> dict:
    print(f"\n{BOLD}Step 3 — Waiting for 7-step pipeline{RESET}")
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    last_step = -1
    last_msg  = ""

    while True:
        elapsed = time.time() - start
        if elapsed > MAX_WAIT_S:
            fail(f"Pipeline timed out after {int(elapsed)}s — check Celery worker logs")

        try:
            r = requests.get(f"{API_BASE}/check/{job_id}/", headers=headers, timeout=10)
        except Exception as e:
            warn(f"Poll error: {e} — retrying...")
            time.sleep(POLL_INTERVAL_S)
            continue

        if r.status_code != 200:
            warn(f"Poll returned {r.status_code} — retrying...")
            time.sleep(POLL_INTERVAL_S)
            continue

        data   = r.json()
        status = data.get("status", "")
        step   = data.get("current_step") or data.get("progress", {}).get("current_step", 0)
        msg    = data.get("message", "")
        pct    = data.get("progress_percent") or data.get("progress", {}).get("progress_percent", 0)

        if step != last_step or msg != last_msg:
            bar_len = 30
            filled  = int(bar_len * float(pct or 0) / 100)
            bar     = f"[{'█'*filled}{'░'*(bar_len-filled)}]"
            print(f"\r  {CYAN}{bar}{RESET} Step {step}/7  {pct:.0f}%  {msg[:40]:<40}", end="", flush=True)
            last_step = step
            last_msg  = msg

        if status == "completed":
            print()  # newline after progress bar
            ok(f"Pipeline completed in {int(elapsed)}s")
            return data

        if status in ("failed", "error"):
            print()
            fail(f"Pipeline failed: {data.get('error', 'unknown error')}")

        time.sleep(POLL_INTERVAL_S)

# ─── Step 4: Run Forecast ─────────────────────────────────────────────────────

def run_forecast(token: str, dataset_id: str) -> dict:
    print(f"\n{BOLD}Step 4 — Running Forecast{RESET}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "time_column":      "date",
        "target_column":    "sales",
        "feature_columns":  ["price", "discount", "units_sold"],
        "frequency":        None,          # auto-detect
        "horizon":          12,
        "candidate_models": ["naive", "seasonal_naive", "ets", "sarimax", "lightgbm", "prophet"],
    }

    info(f"Target  : sales (monthly)")
    info(f"Features: price, discount, units_sold")
    info(f"Horizon : 12 months ahead")
    info("Starting model competition …")

    t0 = time.time()
    r = requests.post(f"{API_BASE}/datasets/{dataset_id}/forecast/",
                      json=payload, headers=headers, timeout=400)
    elapsed = time.time() - t0

    if r.status_code != 200:
        fail(f"Forecast failed ({r.status_code}): {r.text[:400]}")

    result = r.json()
    ok(f"Forecast completed in {int(elapsed)}s")
    return result

# ─── Step 5: Print Results ────────────────────────────────────────────────────

def fmt(v, pct=False):
    if v is None or (isinstance(v, float) and v != v): return "   —   "
    if pct:  return f"{v*100:6.1f}%"
    if abs(v) >= 10000: return f"{v:10.0f}"
    if abs(v) >= 100:   return f"{v:9.1f}"
    return f"{v:9.4f}"

MODEL_LABELS = {
    "naive":"Naive", "seasonal_naive":"Seasonal Naive",
    "ets":"ETS (Holt-Winters)", "sarimax":"SARIMAX (Auto-ARIMA)",
    "catboost":"CatBoost", "lightgbm":"LightGBM", "prophet":"Prophet",
}

def print_results(result: dict):
    sep()
    print(f"{BOLD}{'='*68}{RESET}")
    print(f"  {BOLD}FORECAST RESULTS{RESET}")
    print(f"{'='*68}")
    print(f"  Best Model  : {BOLD}{CYAN}{result.get('best_model','?')}{RESET}")
    print(f"  Confidence  : {result.get('confidence','?')}")
    print(f"  Reason      : {result.get('confidence_reason','')}")
    print(f"  Frequency   : {result.get('frequency','?')}"
          + (" (auto)" if result.get('frequency_auto_detected') else ""))
    if result.get('ensemble'):
        print(f"  Mode        : Ensemble blend")
    tr   = result.get('training_rows', 0)
    tsr  = result.get('test_split_ratio', 0.3)
    print(f"  Train rows  : {int(tr*(1-tsr))}  /  Test rows: {int(tr*tsr)}")

    # Model table
    print(f"\n  {'Model':<22} {'Status':<7} {'CV MAE':>9} {'CV WAPE':>8} {'CV MASE':>8}"
          f" | {'Test MAE':>9} {'Test WAPE':>9} {'Test MASE':>9}")
    print("  " + "─"*22 + "─"*7 + "─"*9 + "─"*9 + "─"*9 + "─+-" + "─"*10 + "─"*10 + "─"*10)

    for mr in result.get("model_results", []):
        m  = mr.get("metrics") or {}
        tm = mr.get("test_metrics") or {}
        best_tag = f" {GREEN}<-- BEST{RESET}" if mr["model"] == result.get("best_model") else ""
        status   = f"{GREEN}OK{RESET}" if mr["status"] == "ok" else f"{RED}FAIL{RESET}"
        print(f"  {MODEL_LABELS.get(mr['model'], mr['model']):<22} {status:<7}"
              f" {fmt(m.get('mae')):>9} {fmt(m.get('wape'),pct=True):>8}"
              f" {fmt(m.get('mase')):>8} |"
              f" {fmt(tm.get('mae')):>9} {fmt(tm.get('wape'),pct=True):>9}"
              f" {fmt(tm.get('mase')):>9}{best_tag}")

    # Forecast table
    fc = result.get("forecast", [])
    pi = result.get("prediction_intervals", [])
    if fc:
        print(f"\n  Forecast (next 12 months):")
        print(f"  {'Date':<14} {'Predicted':>12} {'Lower':>10} {'Upper':>10}")
        print(f"  {'─'*14} {'─'*12} {'─'*10} {'─'*10}")
        for i, pt in enumerate(fc):
            lo = pi[i]["lower"] if i < len(pi) else None
            hi = pi[i]["upper"] if i < len(pi) else None
            print(f"  {pt['date']:<14} {pt['value']:>12,.1f}"
                  f" {lo:>10,.1f}" if lo else f"  {pt['date']:<14} {pt['value']:>12,.1f}   —",
                  end="")
            if lo and hi:
                print(f" {hi:>10,.1f}")
            else:
                print()

    # Feature importance
    fi = result.get("feature_importance", [])
    if fi:
        print(f"\n  Feature Importance ({result.get('best_model','')}):")
        for item in fi[:8]:
            bar = "█" * int(item["importance_pct"] / 5)
            print(f"  {item['feature']:<20} {bar:<20} {item['importance_pct']:6.1f}%")

    # Anomalies
    anomalies = result.get("anomalies", [])
    if anomalies:
        print(f"\n  {YELLOW}Anomalies detected: {len(anomalies)} outliers were capped by IQR{RESET}")
        for a in anomalies[:5]:
            print(f"    {a['date']}  original={a['original_value']:.1f}  capped={a['capped_value']:.1f}  [{a['direction']}]")

    # Warnings
    for w in result.get("warnings", []):
        print(f"\n  {YELLOW}Warning: {w}{RESET}")

    metrics = result.get("metrics", {})
    print(f"\n  {'─'*40}")
    print(f"  Final model MAE  : {fmt(metrics.get('mae'))}")
    print(f"  Final model RMSE : {fmt(metrics.get('rmse'))}")
    print(f"  Final model WAPE : {fmt(metrics.get('wape'), pct=True)}")
    print(f"  Final model MASE : {fmt(metrics.get('mase'))}")
    print(f"  Forecast horizon : {len(fc)} periods")
    print(f"  Forecast ID      : {result.get('forecast_id','not saved')}")
    print(f"{'='*68}\n")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{'='*68}")
    print(f"  AxBi — End-to-End Integration Test")
    print(f"{'='*68}{RESET}")

    # Get credentials from user
    print("\nEnter your AxBi account credentials (from Supabase auth):")
    email    = input("  Email    : ").strip()
    password = input("  Password : ").strip()

    # Run all steps
    check_services()
    token = get_token(email, password)

    # CSV path
    csv_default = str(Path(__file__).parent.parent.parent.parent /
                       "docs" / "test_sales_data.csv")
    sep()
    print(f"Test CSV: {csv_default}")
    csv_path = input(f"Press Enter to use the test CSV, or enter a different path: ").strip()
    if not csv_path:
        csv_path = csv_default

    dataset_id, job_id = upload_csv(token, csv_path, category="retail_sales")
    pipeline_result    = wait_for_pipeline(token, job_id)
    forecast_result    = run_forecast(token, dataset_id)
    print_results(forecast_result)

    # Summary
    sep()
    best = forecast_result.get("best_model", "?")
    conf = forecast_result.get("confidence", "?")
    mase = forecast_result.get("metrics", {}).get("mase")
    print(f"{BOLD}  END-TO-END TEST PASSED{RESET}")
    print(f"  Best model  : {GREEN}{best}{RESET}")
    print(f"  Confidence  : {conf}")
    print(f"  Final MASE  : {fmt(mase)}")
    print(f"\n  Now open your browser:")
    print(f"  Frontend     → {CYAN}http://localhost:5173/AI-Insights{RESET}")
    print(f"  Dataset ID   → {CYAN}{dataset_id}{RESET}")
    print(f"  KPI Dashboard→ {CYAN}http://localhost:5173/dashboard{RESET}")
    print(f"  Forecast Hist→ {CYAN}http://localhost:5173/forecast-history{RESET}\n")


if __name__ == "__main__":
    main()
