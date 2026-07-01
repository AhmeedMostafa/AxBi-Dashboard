"""Delete all datasets for the E2E test user (frees the 4-dataset upload slot)."""
import os
import sys

import httpx

API_BASE = os.environ.get("API_BASE", "http://frontend/api").rstrip("/")


def main() -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    email = os.environ["E2E_TEST_EMAIL"]
    password = os.environ.get("E2E_TEST_PASSWORD", "").strip().strip('"').strip("'")

    r = httpx.post(
        f"{base}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers={"apikey": key, "Content-Type": "application/json"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"auth failed: {r.status_code}", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ds_resp = httpx.get(f"{API_BASE}/datasets/", headers=headers, timeout=30)
    ds_resp.raise_for_status()
    body = ds_resp.json()
    items = body if isinstance(body, list) else body.get("datasets", body.get("results", []))

    print(f"found {len(items)} dataset(s)")
    for d in items:
        did = d.get("id") or d.get("dataset_id")
        name = d.get("file_name") or d.get("name") or did
        dr = httpx.delete(f"{API_BASE}/datasets/{did}/", headers=headers, timeout=120)
        print(f"deleted {name}: {dr.status_code}")


if __name__ == "__main__":
    main()
