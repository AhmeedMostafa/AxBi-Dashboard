"""Quick check: what's in Supabase storage buckets and datasets table."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

print("=== cleaned_data bucket ===")
try:
    files = client.storage.from_('cleaned_data').list()
    if files:
        for f in files:
            name = f.get('name', '?')
            if f.get('id') is None:
                inner = client.storage.from_('cleaned_data').list(name)
                for i in inner:
                    print(f"  {name}/{i.get('name', '?')}")
            else:
                print(f"  {name}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== raw_data bucket ===")
try:
    files = client.storage.from_('raw_data').list()
    if files:
        for f in files:
            name = f.get('name', '?')
            if f.get('id') is None:
                inner = client.storage.from_('raw_data').list(name)
                for i in inner:
                    print(f"  {name}/{i.get('name', '?')}")
            else:
                print(f"  {name}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== datasets table ===")
try:
    result = client.table('datasets').select('id,file_name,storage_path,processed_path,status').execute()
    for row in result.data:
        print(f"  id: {row['id'][:8]}...")
        print(f"    file_name:      {row.get('file_name')}")
        print(f"    storage_path:   {row.get('storage_path')}")
        print(f"    processed_path: {row.get('processed_path')}")
        print(f"    status:         {row.get('status')}")
        print()
    if not result.data:
        print("  (no datasets)")
except Exception as e:
    print(f"  Error: {e}")
