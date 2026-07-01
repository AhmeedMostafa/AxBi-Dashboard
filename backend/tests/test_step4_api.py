"""
End-to-end API test for Step 4.

This script:
  1. Uploads test_step4_data.csv to the cleaned_data bucket (simulating Step 3)
  2. Updates a dataset's processed_path to point to it
  3. Calls POST /api/process/{dataset_id}/ to trigger Step 4
  4. Then calls GET /api/check/{job_id}/ to verify results

Usage:
    # Make sure Django is running: python manage.py runserver
    cd backend
    python tests/test_step4_api.py
"""

import os
import sys
import json
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
from supabase import create_client

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
DJANGO_BASE = 'http://127.0.0.1:8000'
TEST_CSV = os.path.join(os.path.dirname(__file__), 'test_step4_data.csv')

# We'll use this user ID (from the existing datasets in the DB)
USER_ID = '574f452d-188c-40bf-9814-a48b64bc50f8'

client = create_client(SUPABASE_URL, SUPABASE_KEY)


def main():
    print("=" * 60)
    print("  Step 4 API Test (End-to-End)")
    print("=" * 60)

    # ── Step 0: Read test CSV ─────────────────────────────────
    if not os.path.exists(TEST_CSV):
        print(f"ERROR: {TEST_CSV} not found")
        sys.exit(1)

    with open(TEST_CSV, 'rb') as f:
        file_bytes = f.read()
    print(f"\nTest file: {TEST_CSV} ({len(file_bytes):,} bytes)")

    # ── Step 1: Upload to cleaned_data bucket ─────────────────
    # Simulates what Step 3 would do
    cleaned_path = f"{USER_ID}/test_step4_cleaned.csv"
    print(f"\n[1] Uploading to cleaned_data bucket as: {cleaned_path}")

    try:
        # Delete first in case it already exists from a previous run
        try:
            client.storage.from_('cleaned_data').remove([cleaned_path])
        except Exception:
            pass

        client.storage.from_('cleaned_data').upload(
            path=cleaned_path,
            file=file_bytes,
            file_options={'content-type': 'text/csv'}
        )
        print("    OK - File uploaded to cleaned_data bucket")
    except Exception as e:
        print(f"    FAILED: {e}")
        sys.exit(1)

    # ── Step 2: Pick a dataset and set processed_path ─────────
    # Use one of the existing datasets belonging to our user
    print(f"\n[2] Finding a dataset for user {USER_ID[:8]}...")

    result = client.table('datasets') \
        .select('id,file_name,status') \
        .eq('user_id', USER_ID) \
        .limit(1) \
        .execute()

    if not result.data:
        print("    No datasets found for this user. Upload a file first.")
        sys.exit(1)

    dataset = result.data[0]
    dataset_id = dataset['id']
    print(f"    Using dataset: {dataset_id[:8]}... ({dataset['file_name']})")

    # Clear any old columns_metadata for this dataset (clean slate)
    print("    Clearing old columns_metadata...")
    try:
        client.table('columns_metadata').delete().eq('dataset_id', dataset_id).execute()
    except Exception:
        pass

    # Clear old file_info
    try:
        client.table('datasets').update({
            'file_info': None,
            'processed_path': cleaned_path,
            'status': 'processing'
        }).eq('id', dataset_id).execute()
        print(f"    OK - Set processed_path = {cleaned_path}")
    except Exception as e:
        print(f"    FAILED to update dataset: {e}")
        sys.exit(1)

    # Also make sure tracking_jobs exists and is reset
    try:
        client.table('tracking_jobs').update({
            'status': 'processing',
            'current_step': 3,
            'progress_message': 'Ready for Step 4 test',
            'error_log': ''
        }).eq('dataset_id', dataset_id).execute()
        print("    OK - Reset tracking_jobs")
    except Exception as e:
        print(f"    Warning: Could not reset tracking_jobs: {e}")

    # ── Step 3: Get a JWT token for the API call ──────────────
    # We need a valid auth token. Generate one via Supabase admin.
    # For testing, we'll create a temporary JWT.
    print(f"\n[3] Generating auth token for user {USER_ID[:8]}...")

    jwt_secret = os.environ.get('SUPABASE_JWT_SECRET', '')
    if jwt_secret:
        import jwt as pyjwt
        import time
        token = pyjwt.encode(
            {
                'sub': USER_ID,
                'role': 'authenticated',
                'aud': 'authenticated',
                'exp': int(time.time()) + 3600,
                'iat': int(time.time()),
            },
            jwt_secret,
            algorithm='HS256'
        )
        print(f"    OK - JWT token generated (local)")
    else:
        print("    WARNING: No SUPABASE_JWT_SECRET in .env")
        print("    Trying admin API to get a token...")
        # Use the admin API to sign in as the user
        # This won't work without the user's password, so we'll
        # use the service role key as a workaround for testing.
        token = None

    if not token:
        print("    Cannot generate token. Add SUPABASE_JWT_SECRET to .env")
        print("    Falling back to direct function test (no HTTP)...")
        _test_direct(dataset_id, file_bytes)
        return

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    # ── Step 4: Call the Step 4 API endpoint ──────────────────
    url = f"{DJANGO_BASE}/api/process/{dataset_id}/"
    print(f"\n[4] Calling POST {url}")

    try:
        resp = requests.post(url, headers=headers, timeout=60)
    except requests.ConnectionError:
        print("    ERROR: Cannot connect to Django. Is `python manage.py runserver` running?")
        sys.exit(1)

    print(f"    Status: {resp.status_code}")
    try:
        body = resp.json()
        print(f"    Response: {json.dumps(body, indent=2)}")
    except Exception:
        print(f"    Body: {resp.text[:500]}")

    if resp.status_code != 200:
        print("\n    Step 4 API call failed. See response above.")
        sys.exit(1)

    # ── Step 5: Verify results in DB ──────────────────────────
    print(f"\n[5] Verifying results in database...")

    # Check columns_metadata
    cols = client.table('columns_metadata') \
        .select('clean_name,data_type,technical_stats') \
        .eq('dataset_id', dataset_id) \
        .execute()

    print(f"    columns_metadata rows: {len(cols.data)}")
    for c in cols.data:
        stats = c.get('technical_stats', {})
        if isinstance(stats, str):
            stats = json.loads(stats)
        null_r = stats.get('null_ratio', '?')
        print(f"      {c['clean_name']:25s}  type={c['data_type']:10s}  nulls={null_r}")

    # Check datasets.file_info
    ds = client.table('datasets').select('file_info,status').eq('id', dataset_id).execute()
    if ds.data:
        fi = ds.data[0].get('file_info')
        if isinstance(fi, str):
            fi = json.loads(fi)
        print(f"\n    datasets.file_info: {json.dumps(fi, indent=2) if fi else 'null'}")
        print(f"    datasets.status: {ds.data[0].get('status')}")

    # Check tracking_jobs
    tj = client.table('tracking_jobs').select('current_step,progress_message,status') \
        .eq('dataset_id', dataset_id).execute()
    if tj.data:
        print(f"\n    tracking_jobs:")
        print(f"      step:    {tj.data[0].get('current_step')}")
        print(f"      message: {tj.data[0].get('progress_message')}")
        print(f"      status:  {tj.data[0].get('status')}")

    print("\n" + "=" * 60)
    print("  TEST COMPLETE")
    print("=" * 60)


def _test_direct(dataset_id, file_bytes):
    """Fallback: test Step 4 directly without HTTP, skipping auth."""
    print("\n--- Direct function test (no HTTP) ---")

    from api.processing.step4_column_detection import run_step4
    from api.supabase_client import insert_columns_metadata, update_dataset, update_tracking_job

    print("Running run_step4()...")
    result = run_step4(file_bytes, 'test_step4_data.csv')
    print(f"  file_info: {json.dumps(result['file_info'])}")
    print(f"  columns: {len(result['columns'])}")

    print("Saving to Supabase...")
    insert_columns_metadata(dataset_id, result['columns'])
    update_dataset(dataset_id, {'file_info': result['file_info']})
    update_tracking_job(dataset_id, step=4, message='Step 4 done (direct test)', status='completed')

    print("Verifying...")
    cols = client.table('columns_metadata').select('clean_name,data_type') \
        .eq('dataset_id', dataset_id).execute()
    print(f"  columns_metadata rows in DB: {len(cols.data)}")
    for c in cols.data:
        print(f"    {c['clean_name']:25s}  {c['data_type']}")

    print("\nDirect test complete!")


if __name__ == '__main__':
    main()
