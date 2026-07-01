"""
Non-interactive Docker stack smoke test.

Usage (from repo root, stack running on :8080):
  docker compose exec backend python scripts/docker_smoke_test.py

Optional env (backend/.env or container env):
  E2E_TEST_EMAIL / E2E_TEST_PASSWORD — enables upload + pipeline + forecast checks
  E2E_RUN_ACCURATE_FORECAST=1 — also run async accurate-mode forecast (~3 min extra)
  API_BASE — default http://frontend/api (nginx proxy from backend container)
"""
from __future__ import annotations

import io
import os
import sys
import time

import httpx
import pandas as pd
import redis

API_BASE = os.environ.get("API_BASE", "http://frontend/api").rstrip("/")
POLL_INTERVAL_S = 3
MAX_WAIT_S = 600


def _sample_sales_csv_bytes() -> bytes:
    """Minimal monthly sales series for upload smoke test (no docs/ mount needed)."""
    rng = pd.date_range("2023-01-01", periods=48, freq="MS")
    df = pd.DataFrame(
        {
            "date": rng.strftime("%Y-%m-%d"),
            "sales": [1000 + i * 12 + (i % 6) * 40 for i in range(len(rng))],
            "price": [9.5 + (i % 10) * 0.1 for i in range(len(rng))],
            "discount": [i % 5 for i in range(len(rng))],
            "units_sold": [80 + i for i in range(len(rng))],
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def check_health(client: httpx.Client) -> None:
    r = client.get(f"{API_BASE}/health/", timeout=10)
    if r.status_code != 200 or r.json().get("status") != "ok":
        fail(f"health check failed: {r.status_code} {r.text[:200]}")
    ok("GET /api/health/")


def check_redis() -> None:
    url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    if not redis.Redis.from_url(url).ping():
        fail("redis ping failed")
    ok("redis broker reachable")


def check_supabase_egress() -> None:
    base = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not base or not key:
        fail("SUPABASE_URL / SUPABASE_SERVICE_KEY missing")
    r = httpx.get(
        f"{base.rstrip('/')}/rest/v1/",
        headers={"apikey": key},
        timeout=15,
    )
    if r.status_code != 200:
        fail(f"supabase egress failed: {r.status_code}")
    ok("supabase egress (200)")


def get_token(email: str, password: str) -> str:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = httpx.post(
        f"{base}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers={"apikey": key, "Content-Type": "application/json"},
        timeout=15,
    )
    if r.status_code != 200:
        fail(f"auth failed ({r.status_code}): {r.text[:200]}")
    token = r.json().get("access_token")
    if not token:
        fail("no access_token in auth response")
    ok(f"authenticated as {email}")
    return token


def _run_accurate_forecast(
    client: httpx.Client, headers: dict, dataset_id: str, fc_payload: dict
) -> None:
    """Optional: async accurate mode on worker-forecasts (slow — full model tournament)."""
    cooldown = 35
    print(f"  ... waiting {cooldown}s for forecast cooldown ...")
    time.sleep(cooldown)

    payload = {**fc_payload, "mode": "accurate"}
    ar = _post_forecast(client, headers, dataset_id, payload, timeout=30)
    if ar.status_code != 202:
        fail(f"accurate forecast enqueue failed ({ar.status_code}): {ar.text[:300]}")
    async_job = ar.json().get("job_id")
    if not async_job:
        fail(f"accurate forecast missing job_id: {ar.json()}")
    ok(f"accurate forecast queued job_id={async_job}")

    start = time.time()
    while time.time() - start < MAX_WAIT_S:
        sr = client.get(f"{API_BASE}/forecasts/status/{async_job}/", headers=headers, timeout=15)
        if sr.status_code != 200:
            time.sleep(POLL_INTERVAL_S)
            continue
        poll_status = sr.json().get("status")
        if poll_status == "completed":
            ok(f"accurate forecast completed via worker-forecasts ({int(time.time() - start)}s)")
            return
        if poll_status in ("FAILURE", "failed"):
            fail(f"accurate forecast failed: {sr.json()}")
        time.sleep(POLL_INTERVAL_S)
    fail("accurate forecast poll timed out")


def _post_forecast(client, headers, dataset_id, payload, timeout=320):
    """POST forecast with one automatic retry on 429 cooldown."""
    r = client.post(
        f"{API_BASE}/datasets/{dataset_id}/forecast/",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    if r.status_code == 429:
        wait = int(r.json().get("retry_after_seconds", 35)) + 1
        print(f"  ... forecast cooldown, waiting {wait}s ...")
        time.sleep(wait)
        r = client.post(
            f"{API_BASE}/datasets/{dataset_id}/forecast/",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    return r


def run_authenticated_flow(client: httpx.Client, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    csv_bytes = _sample_sales_csv_bytes()

    r = client.post(
        f"{API_BASE}/file-upload/",
        files={"file": ("docker_smoke_sales.csv", io.BytesIO(csv_bytes), "text/csv")},
        data={"category": "sales"},
        headers=headers,
        timeout=60,
    )
    if r.status_code not in (200, 201, 202):
        fail(f"upload failed ({r.status_code}): {r.text[:300]}")
    body = r.json()
    job_id = body.get("job_id") or body.get("tracking_job_id")
    dataset_id = body.get("dataset_id") or body.get("id")
    if not job_id or not dataset_id:
        fail(f"upload response missing ids: {body}")
    ok(f"upload accepted dataset_id={dataset_id}")

    start = time.time()
    while time.time() - start < MAX_WAIT_S:
        pr = client.get(f"{API_BASE}/check/{job_id}/", headers=headers, timeout=15)
        if pr.status_code != 200:
            time.sleep(POLL_INTERVAL_S)
            continue
        data = pr.json()
        status = data.get("status", "")
        if status == "completed":
            ok(f"pipeline completed in {int(time.time() - start)}s")
            break
        if status in ("failed", "error"):
            fail(f"pipeline failed: {data.get('error', data)}")
        time.sleep(POLL_INTERVAL_S)
    else:
        fail(f"pipeline timed out after {MAX_WAIT_S}s")

    dr = client.get(f"{API_BASE}/datasets/{dataset_id}/dashboard/", headers=headers, timeout=30)
    if dr.status_code != 200:
        fail(f"dashboard fetch failed ({dr.status_code})")
    ok("dashboard data loaded")

    fc_payload = {
        "time_column": "date",
        "target_column": "sales",
        "feature_columns": ["price", "discount", "units_sold"],
        "horizon": 6,
        "mode": "fast",
    }
    fr = _post_forecast(client, headers, dataset_id, fc_payload)
    if fr.status_code != 200:
        fail(f"fast forecast failed ({fr.status_code}): {fr.text[:300]}")
    ok("fast forecast completed")

    run_accurate = os.environ.get("E2E_RUN_ACCURATE_FORECAST", "").strip().lower() in (
        "1", "true", "yes",
    )
    if not run_accurate:
        print("  SKIP  accurate forecast (set E2E_RUN_ACCURATE_FORECAST=1 to enable)")
    else:
        _run_accurate_forecast(client, headers, dataset_id, fc_payload)

    seg = client.post(f"{API_BASE}/datasets/{dataset_id}/segmentation/", headers=headers, timeout=180)
    if seg.status_code != 200:
        fail(f"segmentation failed ({seg.status_code}): {seg.text[:200]}")
    ok("segmentation completed")

    pdf = client.post(f"{API_BASE}/datasets/{dataset_id}/export-pdf/", headers=headers, timeout=120)
    if pdf.status_code != 200 or pdf.content[:4] != b"%PDF":
        fail(f"pdf export failed ({pdf.status_code})")
    ok("pdf export returned PDF")

    de = client.delete(f"{API_BASE}/datasets/{dataset_id}/", headers=headers, timeout=120)
    if de.status_code not in (200, 204):
        fail(f"dataset delete failed ({de.status_code}): {de.text[:200]}")
    ok("dataset deleted")


def main() -> None:
    print("Docker smoke test")
    check_redis()
    check_supabase_egress()

    with httpx.Client() as client:
        check_health(client)
        email = os.environ.get("E2E_TEST_EMAIL", "").strip()
        password = os.environ.get("E2E_TEST_PASSWORD", "").strip().strip('"').strip("'")
        if email and password:
            token = get_token(email, password)
            run_authenticated_flow(client, token)
        else:
            print("  SKIP  authenticated flow (set E2E_TEST_EMAIL + E2E_TEST_PASSWORD)")

    print("Smoke test passed")


if __name__ == "__main__":
    main()
