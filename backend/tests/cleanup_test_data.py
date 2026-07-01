"""Clean up old test data from Supabase before re-testing."""
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Both test dataset IDs from previous uploads
dataset_ids = [
    'c0d4ae13-7846-4b09-bc33-4b106e839635',
    '099c7ced-5e76-47b0-83ea-9ba5410a1675',
]

for ds_id in dataset_ids:
    # Delete columns_metadata
    r = client.table('columns_metadata').delete().eq('dataset_id', ds_id).execute()
    print(f"Deleted {len(r.data)} columns_metadata rows for {ds_id}")

    # Reset dataset file_info and processed_path so pipeline re-runs fully
    client.table('datasets').update({
        'file_info': None,
        'processed_path': None,
        'status': 'processing',
    }).eq('id', ds_id).execute()
    print(f"Reset dataset {ds_id}")

    # Reset tracking_job
    client.table('tracking_jobs').update({
        'current_step': 1,
        'status': 'processing',
        'progress_message': 'Waiting for re-test...',
        'error_log': '',
    }).eq('dataset_id', ds_id).execute()
    print(f"Reset tracking_job for {ds_id}")
    print()

print("Cleanup done. Ready for re-test.")
