"""
Verify pipeline output for Steps 3 and 4.
Checks the actual data in Supabase to confirm correctness.
"""
import os
import sys
import io
import json
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

DATASET_ID = 'ece78ece-3f27-48b6-aa8b-049fecc5b27f'

print("=" * 70)
print("PIPELINE OUTPUT VERIFICATION")
print("=" * 70)

# ── 1. DATASET RECORD ──────────────────────────────────────────────
print("\n[1] DATASET RECORD")
print("-" * 50)
ds = client.table('datasets').select('*').eq('id', DATASET_ID).execute()
d = ds.data[0]
print(f"  file_name:      {d['file_name']}")
print(f"  storage_path:   {d['storage_path']}")
print(f"  processed_path: {d['processed_path']}")
print(f"  status:         {d['status']}")
print(f"  category_hint:  {d['category_hint']}")

fi = d.get('file_info')
if fi:
    if isinstance(fi, str):
        fi = json.loads(fi)
    print(f"  file_info:")
    for k, v in fi.items():
        print(f"    {k}: {v}")
else:
    print("  file_info: None (PROBLEM - Step 4 should have set this)")

# ── 2. TRACKING JOB ────────────────────────────────────────────────
print("\n[2] TRACKING JOB")
print("-" * 50)
tj = client.table('tracking_jobs').select('*').eq('dataset_id', DATASET_ID).execute()
t = tj.data[0]
print(f"  status:           {t['status']}")
print(f"  current_step:     {t['current_step']}")
print(f"  progress_message: {t['progress_message']}")
print(f"  error_log:        {t.get('error_log', '')}")

# ── 3. STEP 3 OUTPUT: Download and inspect the cleaned parquet ─────
print("\n[3] STEP 3 OUTPUT: Cleaned Parquet File")
print("-" * 50)
processed_path = d['processed_path']
if processed_path:
    parquet_bytes = client.storage.from_('cleaned_data').download(processed_path)
    df_clean = pd.read_parquet(io.BytesIO(parquet_bytes))
    print(f"  Rows:    {len(df_clean)}")
    print(f"  Columns: {len(df_clean.columns)}")
    print(f"  Column names: {list(df_clean.columns)}")
    print(f"\n  First 3 rows:")
    print(df_clean.head(3).to_string(index=False, max_colwidth=25))

    # Check cleaning was applied
    print(f"\n  Step 3 Cleaning Checks:")
    # Check for duplicates
    dupes = df_clean.duplicated().sum()
    print(f"    Duplicates remaining: {dupes}")
    # Check for empty strings
    obj_cols = df_clean.select_dtypes(include=['object']).columns
    empty_strings = 0
    for col in obj_cols:
        empty_strings += (df_clean[col].astype(str).str.strip() == '').sum()
    print(f"    Empty strings remaining: {empty_strings}")
else:
    print("  ERROR: processed_path is empty - Step 3 did not set it")

# ── 4. STEP 4 OUTPUT: columns_metadata ─────────────────────────────
print("\n[4] STEP 4 OUTPUT: columns_metadata")
print("-" * 50)
cm = client.table('columns_metadata').select('*').eq('dataset_id', DATASET_ID).execute()
columns = cm.data
print(f"  Total columns stored: {len(columns)}")
print()

for col in columns:
    stats = col.get('technical_stats')
    if isinstance(stats, str):
        stats = json.loads(stats)

    print(f"  Column: {col['original_name']}")
    print(f"    clean_name: {col['clean_name']}")
    print(f"    data_type:  {col['data_type']}")
    if stats:
        print(f"    null_ratio:   {stats.get('null_ratio', 'N/A')}")
        print(f"    unique_ratio: {stats.get('unique_ratio', 'N/A')}")
        # Show type-specific stats
        for k in ['min', 'max', 'mean', 'std', 'min_date', 'max_date', 'avg_char_length']:
            if k in stats:
                print(f"    {k}: {stats[k]}")
        samples = stats.get('top_5_samples', [])
        if samples:
            print(f"    samples: {samples[:3]}")
    else:
        print(f"    technical_stats: None (PROBLEM)")
    print(f"    ai_profile: {col.get('ai_profile', 'None (expected - Step 5 not yet run)')}")
    print()

# ── 5. COMPARE: raw file vs cleaned file ───────────────────────────
print("\n[5] RAW vs CLEANED COMPARISON")
print("-" * 50)
raw_path = d['storage_path']
raw_bytes = client.storage.from_('raw_data').download(raw_path)
df_raw = pd.read_csv(io.BytesIO(raw_bytes))
print(f"  Raw file:     {len(df_raw)} rows x {len(df_raw.columns)} cols")
print(f"  Cleaned file: {len(df_clean)} rows x {len(df_clean.columns)} cols")
rows_removed = len(df_raw) - len(df_clean)
print(f"  Rows removed by cleaning: {rows_removed}")
print(f"  Columns unchanged: {len(df_raw.columns) == len(df_clean.columns)}")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
