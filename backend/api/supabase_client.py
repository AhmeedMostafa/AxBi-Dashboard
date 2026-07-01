import json

import os
import jwt
from supabase import create_client, Client
from postgrest.types import CountMethod, ReturnMethod

RAW_DATA_BUCKET = 'raw_data'
CLEANED_DATA_BUCKET = 'cleaned_data'

# ======= AUTHENTICATION BEGIN==========================
def get_supabase_client() -> Client:
    """Get Supabase client using service role key (reads env vars at call time)"""
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_SERVICE_KEY', '')

    if not url or not key:
        raise ValueError(
            'Supabase credentials not configured. '
            'Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env'
        )
    return create_client(url, key)


def verify_supabase_token(auth_header: str) -> dict:
    """
    Verify the Supabase JWT token from the Authorization header.
    
    The token is signed with the Supabase JWT secret.
    We decode it to extract the user_id (sub claim).
    
    Args:
        auth_header: "Bearer eyJhbGciOi..." from the request
    
    Returns:
        {'user_id': 'uuid-here', 'email': 'user@example.com', ...}
    
    Raises:
        ValueError: If token is missing, invalid, or expired
    """
    if not auth_header or not auth_header.startswith('Bearer '):
        raise ValueError('Missing or invalid Authorization header')

    token = auth_header.split(' ', 1)[1]

    # The JWT secret is derived from the Supabase service role key
    # Supabase uses the JWT secret (from project settings) to sign tokens
    jwt_secret = os.environ.get('SUPABASE_JWT_SECRET', '')

    if not jwt_secret:
        # Fallback: verify via Supabase auth API instead of local decode
        return _verify_token_via_api(token)

    try:
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=['HS256'],
            audience='authenticated'
        )

        return {
            'user_id': payload.get('sub', ''),
            'email': payload.get('email', ''),
            'role': payload.get('role', ''),
        }
    except jwt.ExpiredSignatureError:
        raise ValueError('Token has expired')
    except jwt.InvalidTokenError as e:
        raise ValueError(f'Invalid token: {e}')


def _verify_token_via_api(token: str) -> dict:
    """
    Verify token by calling Supabase Auth API.
    Used as fallback when JWT_SECRET is not configured.
    """
    client = get_supabase_client()

    try:
        user_response = client.auth.get_user(token)
        user = user_response.user

        if not user:
            raise ValueError('Token is valid but no user found')

        return {
            'user_id': user.id,
            'email': user.email or '',
            'role': user.role or '',
        }
    except Exception as e:
        raise ValueError(f'Token verification failed: {e}')

# ======= AUTHENTICATION END==========================



# KAMAL and MINA for STEP 1 & 2
# =============================BEGIN==================================
def upload_file_to_bucket(file_data: bytes, file_name: str, content_type: str) -> str:
    """Upload file to Supabase Storage bucket. Returns storage path."""
    client = get_supabase_client()

    client.storage.from_(RAW_DATA_BUCKET).upload(
        path=file_name,
        file=file_data,
        file_options={'content-type': content_type}
    )

    return file_name


def insert_dataset(user_id: str, file_name: str, category_hint: str, storage_path: str,
                   project_name: str | None = None) -> dict:
    """Insert row into datasets table. Returns the created row."""
    client = get_supabase_client()

    record = {
        'user_id': user_id,
        'file_name': file_name,
        'category_hint': category_hint,
        'storage_path': storage_path,
        'status': 'pending'
    }
    if project_name and project_name.strip():
        record['project_name'] = project_name.strip()

    result = client.table('datasets').insert(record).execute()

    return result.data[0]


def insert_tracking_job(dataset_id: str, user_id: str) -> dict:
    """Insert row into tracking_jobs table. Returns the created row."""
    client = get_supabase_client()

    result = client.table('tracking_jobs').insert({
        'dataset_id': dataset_id,
        'user_id': user_id,
        'status': 'pending',
        'current_step': 1,
        'progress_message': 'File uploaded. Waiting for processing...',
        'error_log': ''
    }).execute()

    return result.data[0]
# =============================END==================================



#OMAR Mohassab preprocessing code Step 3
# =============================BEGIN==================================
def download_file_bytes(bucket: str, storage_path: str) -> bytes:
    """
    Download file bytes from a bucket (service role key).
    storage_path example: "{user_id}/file.xlsx"
    """
    client = get_supabase_client()
    data = client.storage.from_(bucket).download(storage_path)
    return data


def upload_cleaned_file_to_bucket(file_data: bytes, storage_path: str, content_type: str) -> str:
    """
    Upload CLEANED file to cleaned_data bucket at the given path.
    Uses upsert so reruns overwrite the cleaned version.
    """
    client = get_supabase_client()
    client.storage.from_(CLEANED_DATA_BUCKET).upload(
        path=storage_path,
        file=file_data,
        file_options={
            "content-type": content_type,
            "x-upsert": "true",
        },
    )
    return storage_path
    # ====================end====================
# =============================END==================================


# ======= STEP 4 FUNCTIONS BEGIN ==========================
def insert_columns_metadata(dataset_id: str, columns_list: list) -> list:
    """
    Batch-insert column metadata rows for a dataset.

    Args:
        dataset_id:   UUID of the dataset.
        columns_list: List of dicts, each with keys:
                      original_name, clean_name, data_type,
                      technical_stats, ai_profile.

    Returns:
        List of created rows from Supabase.
    """
    client = get_supabase_client()

    rows = []
    for col in columns_list:
        rows.append({
            'dataset_id': dataset_id,
            'original_name': col['original_name'],
            'clean_name': col['clean_name'],
            'data_type': col['data_type'],
            'technical_stats': json.dumps(col['technical_stats']),
            'ai_profile': None,
        })

    result = client.table('columns_metadata').insert(rows).execute()
    return result.data


def delete_columns_metadata(dataset_id: str) -> int:
    """
    Delete all columns_metadata rows for a dataset.

    Useful before re-running Step 4 so metadata writes are idempotent.
    Returns number of deleted rows reported by Supabase response payload.
    """
    client = get_supabase_client()
    result = (
        client.table('columns_metadata')
        .delete()
        .eq('dataset_id', dataset_id)
        .execute()
    )
    return len(result.data or [])


def update_dataset(dataset_id: str, updates: dict) -> dict:
    """
    Update a datasets row with the given fields.

    Args:
        dataset_id: UUID of the dataset.
        updates:    Dict of column->value pairs to update.
                    Example: {"file_info": {"row_count": 67, ...}}

    Returns:
        The updated row from Supabase.
    """
    client = get_supabase_client()

    # Serialize any dict/list values to JSON strings for JSONB columns
    payload = {}
    for key, value in updates.items():
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value)
        else:
            payload[key] = value

    result = (
        client.table('datasets')
        .update(payload)
        .eq('id', dataset_id)
        .execute()
    )

    return result.data[0] if result.data else {}


def update_tracking_job(dataset_id: str, step: int, message: str, status: str = 'processing') -> dict:
    """
    Update the tracking job for a given dataset.

    Finds the tracking job by dataset_id and updates its
    current_step, progress_message, and status.

    Args:
        dataset_id: UUID of the dataset.
        step:       Current step number (e.g. 4).
        message:    Progress message (e.g. "Detecting column types...").
        status:     Job status. Defaults to 'processing'.
                    Use 'completed' or 'failed' when done.

    Returns:
        The updated row from Supabase.
    """
    client = get_supabase_client()

    payload = {
        'current_step': step,
        'progress_message': message,
        'status': status,
    }

    # If marking as failed, also store the message in error_log
    if status == 'failed':
        payload['error_log'] = message

    result = (
        client.table('tracking_jobs')
        .update(payload)
        .eq('dataset_id', dataset_id)
        .execute()
    )

    return result.data[0] if result.data else {}

# ======= STEP 4 FUNCTIONS END ============================

# ======= STEP 5 FUNCTIONS BEGIN ==========================


def update_column_metadata(column_id: str, updates: dict) -> dict:
    """
    Update a single columns_metadata row (used by Step 5 to write ai_profile).


    Args:
        column_id: UUID of the columns_metadata row.
        updates:   Dict of column->value pairs to update.
                   Example: {"ai_profile": {...}, "is_primary_metric": True}


    Returns:
        The updated row from Supabase.
    """
    client = get_supabase_client()


    payload = {}
    for key, value in updates.items():
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value)
        else:
            payload[key] = value


    result = (
        client.table('columns_metadata')
        .update(payload)
        .eq('id', column_id)
        .execute()
    )


    return result.data[0] if result.data else {}


# ======= STEP 5 FUNCTIONS END ============================

def delete_dataset_rows(dataset_id: str, batch_size: int = 5000) -> int:
    """
    Delete all dataset_rows for a dataset, in row_index windows.

    A single bulk delete on large datasets (200k+ rows) blows past Supabase's
    statement timeout (error 57014). We delete in contiguous row_index windows
    instead:
      DELETE ... WHERE dataset_id = X AND row_index >= lo AND row_index < hi
    Each window is bounded by idx_dataset_rows_dataset_row (dataset_id, row_index),
    so every statement stays small and index-backed. This avoids:
      - the single massive delete (statement timeout 57014),
      - any ORDER BY scan,
      - shipping a 5000-id `.in_()` list back as a multi-KB request URL.
    returning=minimal so no row payload is sent back.

    Used before re-inserting rows so persistence is idempotent across reruns.
    """
    client = get_supabase_client()

    # Highest row_index for this dataset (one index-backed read). If the table
    # has no rows for the dataset, nothing to do.
    top = (
        client.table('dataset_rows')
        .select('row_index')
        .eq('dataset_id', dataset_id)
        .order('row_index', desc=True)
        .limit(1)
        .execute()
    )
    if not top.data:
        return 0
    max_index = top.data[0]['row_index']

    deleted = 0
    lo = 0
    while lo <= max_index:
        hi = lo + batch_size
        resp = (
            client.table('dataset_rows')
            .delete(count=CountMethod.exact, returning=ReturnMethod.minimal)
            .eq('dataset_id', dataset_id)
            .gte('row_index', lo)
            .lt('row_index', hi)
            .execute()
        )
        deleted += resp.count or 0
        lo = hi
    return deleted


def insert_dataset_rows(rows: list, batch_size: int = 1000) -> int:
    """
    Insert dataset_rows in batches.

    Args:
        rows: List of dicts with dataset_id, row_index, row_data.
        batch_size: Insert chunk size to keep payloads manageable.

    Returns:
        Number of rows attempted for insertion.
    """
    if not rows:
        return 0

    client = get_supabase_client()
    inserted = 0

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        client.table('dataset_rows').insert(batch).execute()
        inserted += len(batch)

    return inserted


def get_tracking_job(job_id: str):
    """Get tracking job by job id. Returns dict or None."""
    client = get_supabase_client()

    result = (
        client.table('tracking_jobs')
        .select('*')
        .eq('id', job_id)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]
    return None


def list_user_datasets(user_id: str) -> list:
    """Return all datasets for a user, newest first."""
    client = get_supabase_client()
    resp = (
        client.table('datasets')
        .select('*')
        .eq('user_id', user_id)
        .order('created_at', desc=True)
        .execute()
    )
    return resp.data or []


def delete_dataset_full(dataset_id: str, user_id: str) -> bool:
    """Delete a dataset and ALL its dependencies.

    Order matters — foreign-key children first, then the parent datasets row.
    Also removes storage objects from raw_data and cleaned_data buckets.
    Returns True on success, raises on error.
    """
    client = get_supabase_client()

    # 1. Verify ownership before deleting anything
    check = (
        client.table('datasets')
        .select('id,file_info,processed_path,global_context,storage_path')
        .eq('id', dataset_id)
        .eq('user_id', user_id)
        .limit(1)
        .execute()
    )
    if not check.data:
        raise PermissionError('Dataset not found or access denied')

    record = check.data[0]

    # 2. Delete child rows (order: leaf tables first)
    # dataset_rows can hold 200k+ rows — batch it so we don't hit the
    # statement timeout (57014). Other child tables are small, delete in one.
    delete_dataset_rows(dataset_id)
    client.table('columns_metadata').delete().eq('dataset_id', dataset_id).execute()
    client.table('forecast_logs').delete().eq('dataset_id', dataset_id).execute()
    client.table('tracking_jobs').delete().eq('dataset_id', dataset_id).execute()

    # 3. Remove storage objects (best-effort — don't fail if files are already gone)

    # Raw uploaded file — file_info is stored as a JSON string, parse it first
    try:
        raw_file_info = record.get('file_info') or record.get('storage_path')
        if isinstance(raw_file_info, str):
            try:
                raw_file_info = json.loads(raw_file_info)
            except (ValueError, TypeError):
                raw_file_info = {}
        if isinstance(raw_file_info, dict):
            raw_path = (
                raw_file_info.get('storage_path')
                or raw_file_info.get('path')
                or raw_file_info.get('file_path')
            )
        else:
            raw_path = record.get('storage_path')
        if raw_path:
            client.storage.from_('raw_data').remove([raw_path])
    except Exception:
        pass

    # Step-3 cleaned file (processed_path)
    try:
        cleaned_path = record.get('processed_path')
        if cleaned_path:
            client.storage.from_('cleaned_data').remove([cleaned_path])
    except Exception:
        pass

    # Step-6 smart-preprocessed file (different path stored in global_context)
    try:
        global_ctx = record.get('global_context') or {}
        if isinstance(global_ctx, str):
            try:
                global_ctx = json.loads(global_ctx)
            except (ValueError, TypeError):
                global_ctx = {}
        step6_path = None
        if isinstance(global_ctx, dict):
            step6 = global_ctx.get('step6') or {}
            if isinstance(step6, dict):
                step6_path = step6.get('output_path')
        if step6_path and step6_path != cleaned_path:
            client.storage.from_('cleaned_data').remove([step6_path])
    except Exception:
        pass

    # 4. Delete the dataset record itself
    client.table('datasets').delete().eq('id', dataset_id).eq('user_id', user_id).execute()
    return True


def get_dataset(dataset_id: str):
    """Get dataset by id. Returns dict or None."""
    client = get_supabase_client()

    result = (
        client.table('datasets')
        .select('*')
        .eq('id', dataset_id)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]
    return None


def get_columns_metadata(dataset_id: str) -> list:
    """Get all columns_metadata rows for a dataset."""
    client = get_supabase_client()

    result = (
        client.table('columns_metadata')
        .select('*')
        .eq('dataset_id', dataset_id)
        .execute()
    )

    return result.data or []


def insert_forecast_log(log_entry: dict) -> dict | None:
    """Insert a row into forecast_logs. Returns the created row or None on failure."""
    try:
        client = get_supabase_client()
        result = client.table('forecast_logs').insert(log_entry).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


def _to_python(obj):
    """Recursively convert numpy scalars/arrays to plain Python types for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_python(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_to_python(v) for v in obj.tolist()]
    if isinstance(obj, float) and (obj != obj):  # NaN
        return None
    return obj


def insert_forecast_result(
    dataset_id: str, user_id: str, config: dict, result: dict
) -> dict:
    """Persist a forecast result into forecast_logs for history/retrieval."""
    client = get_supabase_client()
    metrics = result.get("metrics") or {}
    row = _to_python({
        "dataset_id": dataset_id,
        "user_id": user_id,
        "time_column": config.get("time_column"),
        "target_column": config.get("target_column"),
        "feature_columns": config.get("feature_columns") or [],
        "frequency_used": result.get("frequency"),
        "horizon": config.get("horizon"),
        "missing_policy": result.get("missing_periods_policy", "drop"),
        "input_rows": result.get("training_rows"),
        "candidate_models": result.get("candidate_models") or [],
        "eligible_models": [
            m["model"] for m in (result.get("model_results") or [])
        ],
        "skipped_models": [s["model"] for s in (result.get("skipped_models") or [])],
        "forecast_possible": result.get("forecast_possible", True),
        "model_results": result.get("model_results") or [],
        "best_model": result.get("best_model"),
        "best_mae": metrics.get("mae"),
        "best_rmse": metrics.get("rmse"),
        "best_wape": metrics.get("wape"),
        "forecast_points": len(result.get("forecast") or []),
        "duration_ms": result.get("duration_ms"),
        "error_message": None,
        "readiness_reasons": result.get("readiness", {}).get("reasons") or [],
    })

    # Store forecast values + test comparison for accuracy view.
    # Requires `forecast_data JSONB` column — run migration first:
    #   ALTER TABLE forecast_logs ADD COLUMN IF NOT EXISTS forecast_data JSONB;
    forecast_data = _to_python({
        "forecast": result.get("forecast") or [],
        "prediction_intervals": result.get("prediction_intervals") or [],
        "test_comparison": result.get("test_comparison") or [],
    })

    # Try inserting with forecast_data; fall back to without it if column missing
    try:
        resp = client.table("forecast_logs").insert({**row, "forecast_data": forecast_data}).execute()
    except Exception:
        resp = client.table("forecast_logs").insert(row).execute()

    return resp.data[0] if resp.data else {}


def get_forecast_results(dataset_id: str, limit: int = 20) -> list:
    """Return recent forecast results for a dataset."""
    client = get_supabase_client()
    resp = (
        client.table("forecast_logs")
        .select("id,created_at,dataset_id,best_model,best_mae,best_rmse,best_wape,target_column,time_column,horizon,frequency_used,model_results,skipped_models,forecast_points,duration_ms")
        .eq("dataset_id", dataset_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def get_forecast_result_by_id(forecast_id: str) -> dict | None:
    """Return a single forecast result by its ID, including forecast_data if present."""
    client = get_supabase_client()
    try:
        # Use * (includes forecast_data if the column exists) + limit(1) to avoid
        # .single() raising APIError when the row is not found.
        resp = (
            client.table("forecast_logs")
            .select("*")
            .eq("id", forecast_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def delete_forecast_log(forecast_id: str) -> bool:
    """Delete a single forecast_logs row by ID. Returns True on success."""
    client = get_supabase_client()
    try:
        client.table("forecast_logs").delete().eq("id", forecast_id).execute()
        return True
    except Exception:
        return False


def get_dataset_rows(dataset_id: str, limit: int = 500, offset: int = 0) -> list:
    """
    Get paginated dataset_rows sorted by row_index.
    """
    client = get_supabase_client()

    start = max(0, int(offset))
    page_size = max(1, int(limit))
    end = start + page_size - 1

    result = (
        client.table('dataset_rows')
        .select('row_index,row_data')
        .eq('dataset_id', dataset_id)
        .order('row_index', desc=False)
        .range(start, end)
        .execute()
    )

    return result.data or []


def get_user_kpi_stats(user_id: str) -> dict:
    """Return aggregated KPI stats for the dashboard home page.

    Queries datasets + forecast_results for the given user and computes:
    - total datasets uploaded
    - total forecasts run
    - model usage distribution
    - average best-model MAE
    - 5 most recent forecasts (lightweight summary rows)
    """
    client = get_supabase_client()

    # Dataset count
    try:
        ds_resp = (
            client.table("datasets")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        dataset_count = ds_resp.count or len(ds_resp.data or [])
    except Exception:
        dataset_count = 0

    # Forecast logs — lightweight fields only
    try:
        fc_resp = (
            client.table("forecast_logs")
            .select("id,created_at,dataset_id,best_model,best_mae,best_wape,target_column,horizon")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        forecast_rows = fc_resp.data or []
    except Exception:
        forecast_rows = []

    forecast_count = len(forecast_rows)

    # Model distribution
    model_counts: dict[str, int] = {}
    for row in forecast_rows:
        m = row.get("best_model") or "unknown"
        primary = m.split("+")[0] if "+" in m else m
        model_counts[primary] = model_counts.get(primary, 0) + 1

    most_common_model = (
        max(model_counts, key=lambda k: model_counts[k]) if model_counts else None
    )

    # Average MAE across all forecasts
    maes = [float(row["best_mae"]) for row in forecast_rows if row.get("best_mae") is not None]
    avg_mae = round(float(sum(maes) / len(maes)), 4) if maes else None

    # 5 most recent forecast summaries
    recent: list[dict] = []
    for row in forecast_rows[:5]:
        recent.append(
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "dataset_id": row.get("dataset_id"),
                "best_model": row.get("best_model"),
                "target": row.get("target_column"),
                "horizon": row.get("horizon"),
                "mae": row.get("best_mae"),
                "wape": row.get("best_wape"),
            }
        )

    return {
        "dataset_count": dataset_count,
        "forecast_count": forecast_count,
        "model_distribution": model_counts,
        "most_common_model": most_common_model,
        "avg_mae": avg_mae,
        "recent_forecasts": recent,
    }

