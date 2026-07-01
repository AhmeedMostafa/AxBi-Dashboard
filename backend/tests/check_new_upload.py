"""Check the latest upload status."""
import os, json
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Get the most recent tracking job
jobs = client.table('tracking_jobs').select('*').order('created_at', desc=True).limit(3).execute()
for j in jobs.data:
    print(f"Job {j['id']}")
    print(f"  dataset_id: {j['dataset_id']}")
    print(f"  status: {j['status']}")
    print(f"  current_step: {j['current_step']}")
    print(f"  progress_message: {j['progress_message']}")
    print(f"  error_log: {j.get('error_log', '')}")
    print()

# Check columns_metadata count for the newest dataset
if jobs.data:
    newest_ds = jobs.data[0]['dataset_id']
    cm = client.table('columns_metadata').select('id', count='exact').eq('dataset_id', newest_ds).execute()
    print(f"columns_metadata rows for newest dataset ({newest_ds}): {cm.count}")
