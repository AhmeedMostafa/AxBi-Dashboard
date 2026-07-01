"""
Celery tasks for the BI Dashboard processing pipeline.

The main task is `process_dataset_pipeline` which runs Steps 3-7
sequentially for a single dataset inside a Celery worker process.

This keeps Django views fast (they just queue and return) while
heavy processing (pandas, AI calls) happens asynchronously.

Usage:
    from api.tasks import process_dataset_pipeline
    process_dataset_pipeline.delay(dataset_id)   # non-blocking
"""

import io
import json
import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from celery import shared_task

from .supabase_client import (
    get_dataset,
    download_file_bytes,
    CLEANED_DATA_BUCKET,
    upload_cleaned_file_to_bucket,
    insert_columns_metadata,
    delete_columns_metadata,
    update_dataset,
    update_tracking_job,
    get_columns_metadata,
    update_column_metadata,
    delete_dataset_rows,
    insert_dataset_rows,
)
from .processing.step4_column_detection import run_step4
from .processing.step6_smart_preprocessing import run_step6 as run_step6_smart
from .processing.step5_ai_semantic import run_step5   # ← NEW
from .processing.step7_dashboard_blueprint import run_step7 as run_step7_blueprint
from .processing.step8_ai_report import run_step8 as run_step8_report
from .forecasting import run_forecast_service


logger = logging.getLogger(__name__)

# Cap how many rows we persist to dataset_rows. The table is only consumed by the
# paginated display table and the PDF export — both cap at 2000 rows. Heavy analytics
# (forecast/segmentation/aggregate/correlations) read the Parquet artifact, not this
# table. Storing the full 400k+ rows is pure waste and the source of the delete/insert
# statement timeouts (57014) + table bloat. file_info.row_count keeps the TRUE count.
DATASET_ROWS_DISPLAY_CAP = 5000


# ══════════════════════════════════════════════════════════════
# MAIN PIPELINE TASK
# ══════════════════════════════════════════════════════════════

@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def process_dataset_pipeline(self, dataset_id: str):
    """
    Run the full processing pipeline for one dataset.

    Called asynchronously from file_upload view via .delay().
    Each step updates tracking_jobs so the frontend can poll progress.

    Pipeline:
        Step 3: Clean raw file
        Step 4: Technical column profiling
        Step 5: AI semantic analysis
        Step 6: Smart preprocessing
        Step 7: Dashboard blueprint generation
        Step 8: AI report generation

    Args:
        dataset_id: UUID of the dataset to process.
    """
    logger.info(f"Pipeline started for dataset {dataset_id}")

    # Per-step wall-clock timing so a single slow upload reveals WHERE the time goes
    # (full-file I/O vs Gemini latency vs persist) instead of guessing.
    timings: dict[str, float] = {}

    def _timed(label: str, fn):
        t0 = time.perf_counter()
        fn()
        dt = round(time.perf_counter() - t0, 2)
        timings[label] = dt
        logger.info("[timing] dataset=%s %s took %.2fs", dataset_id, label, dt)

    pipeline_t0 = time.perf_counter()
    try:
        update_dataset(dataset_id, {'status': 'processing'})
        # ──────────────────────────────────────────
        # STEP 3: Clean raw file
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=3, message='Step 3: Cleaning raw file...')
        _timed('step3_clean', lambda: _run_step3(dataset_id))

        # ──────────────────────────────────────────
        # STEP 4: Technical column profiling
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=4, message='Step 4: Downloading cleaned file...')
        _timed('step4_profile', lambda: _run_step4_pipeline(dataset_id))

        # ──────────────────────────────────────────
        # STEP 5: AI semantic analysis
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=5, message='Step 5: AI analyzing columns...')
        _timed('step5_ai_semantic', lambda: _run_step5(dataset_id))

        # ──────────────────────────────────────────
        # STEP 6: Smart preprocessing
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=6, message='Step 6: Smart preprocessing...')
        _timed('step6_smart', lambda: _run_step6(dataset_id))

        # ──────────────────────────────────────────
        # STEP 7: Global Confidence & Type Detection
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=7, message='Step 7: Generating dashboard blueprint...')
        _timed('step7_blueprint', lambda: _run_step7(dataset_id))

        # ──────────────────────────────────────────
        # STEP 8: AI Report Generation
        # ──────────────────────────────────────────
        _update_progress(dataset_id, step=8, message='Step 8: Generating AI report...')
        _timed('step8_report', lambda: _run_step8(dataset_id))
        _update_progress(dataset_id, step=8, message='Step 8: Persisting rows for dashboard...')
        _timed('persist_rows', lambda: _persist_dataset_rows(dataset_id))

        logger.info(
            "[timing] dataset=%s TOTAL=%.2fs breakdown=%s",
            dataset_id, round(time.perf_counter() - pipeline_t0, 2), timings,
        )

        # ──────────────────────────────────────────
        # DONE
        # ──────────────────────────────────────────
        _update_progress(
            dataset_id, step=8,
            message='All steps completed successfully.',
            status='completed',
        )
        update_dataset(dataset_id, {'status': 'completed'})
        logger.info(f"Pipeline completed for dataset {dataset_id}")

    except Exception as exc:
        logger.exception(f"Pipeline failed for dataset {dataset_id}: {exc}")
        _fail_pipeline(dataset_id, str(exc))

        # Retry once on transient errors (network issues, etc.)
        raise self.retry(exc=exc)


# ══════════════════════════════════════════════════════════════
# STEP IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════

def _run_step3(dataset_id: str):
    """
    Step 3: Clean raw file.

    1. Download the raw file from raw_data bucket
    2. Clean it (normalize columns, fix encoding, handle nulls, dedup)
    3. Convert to parquet and upload to cleaned_data bucket
    4. Update datasets.processed_path so Step 4 knows the cleaned location
    """
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    user_id = dataset['user_id']
    storage_path = dataset['storage_path']

    from preprocessing.pipeline import process_file_to_parquet
    cleaned_path = process_file_to_parquet(user_id, storage_path)

    # Update datasets.processed_path so Step 4 knows where the cleaned file is
    update_dataset(dataset_id, {'processed_path': cleaned_path})

    logger.info(f"Step 3 complete for dataset {dataset_id}: {cleaned_path}")


def _run_step4_pipeline(dataset_id: str):
    """
    Step 4: Technical column profiling.

    Downloads the cleaned file, runs pandas profiling,
    saves columns_metadata and file_info to Supabase.
    """
    # Get dataset to read processed_path
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    processed_path = dataset.get('processed_path')
    if not processed_path:
        raise ValueError(
            f"Dataset {dataset_id} has no processed_path. "
            "Step 3 must run first to clean the file."
        )

    # Download cleaned file
    _update_progress(dataset_id, step=4, message='Step 4: Downloading cleaned file...')
    file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, processed_path)

    # Run profiling (pass processed_path so _read_file sees .parquet extension)
    _update_progress(dataset_id, step=4, message='Step 4: Profiling columns...')
    result = run_step4(file_bytes, processed_path)

    # Save results
    _update_progress(dataset_id, step=4, message='Step 4: Saving column metadata...')
    delete_columns_metadata(dataset_id)
    insert_columns_metadata(dataset_id, result['columns'])
    update_dataset(dataset_id, {'file_info': result['file_info']})

    logger.info(
        f"Step 4 complete for dataset {dataset_id}: "
        f"{len(result['columns'])} columns profiled"
    )


def _run_step5(dataset_id: str):
    """
    Step 5: AI semantic analysis.

    1. Read columns_metadata (filled by Step 4) from Supabase
    2. Read category_hint from datasets
    3. Call Gemini via run_step5()
    4. Update columns_metadata.ai_profile per column
    5. Set is_primary_metric per column
    """
    # 1. Fetch dataset and columns
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        raise ValueError(
            f"No columns_metadata found for dataset {dataset_id}. "
            "Step 4 must run first."
        )

    category_hint = dataset.get('category_hint')

    # 2. Run AI analysis
    _update_progress(dataset_id, step=5, message='Step 5: Sending columns to AI...')
    results = run_step5(columns_metadata, category_hint)

    # 3. Save results back to Supabase
    _update_progress(dataset_id, step=5, message='Step 5: Saving AI profiles...')
    for result in results:
        update_column_metadata(result['column_id'], {
            'ai_profile': result['ai_profile'],
            'is_primary_metric': result['is_primary_metric'],
        })

    logger.info(
        f"Step 5 complete for dataset {dataset_id}: "
        f"{len(results)} columns enriched by AI"
    )



def _run_step6(dataset_id: str):
    """
    Step 6: Smart preprocessing.

    Uses Step 5 semantic labels to apply deterministic post-cleaning:
      1. Load Step 3 cleaned file from cleaned_data bucket
      2. Apply role-based transforms via run_step6_smart()
      3. Upload smart output parquet to cleaned_data
      4. Persist step report and output path in datasets.global_context
    """
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    processed_path = dataset.get('processed_path')
    if not processed_path:
        raise ValueError(
            f"Dataset {dataset_id} has no processed_path. "
            "Step 3 must run first."
        )

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        raise ValueError(
            f"No columns_metadata found for dataset {dataset_id}. "
            "Step 4 and Step 5 must run first."
        )

    _update_progress(dataset_id, step=6, message='Step 6: Downloading cleaned file...')
    file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, processed_path)
    df = _read_dataframe_for_step6(file_bytes, processed_path)

    _update_progress(dataset_id, step=6, message='Step 6: Applying smart rules...')
    df_smart, step6_report = run_step6_smart(df, columns_metadata)

    smart_path = _build_smart_path(processed_path)
    out_buffer = io.BytesIO()
    df_smart.to_parquet(out_buffer, index=False)

    _update_progress(dataset_id, step=6, message='Step 6: Uploading smart file...')
    upload_cleaned_file_to_bucket(
        file_data=out_buffer.getvalue(),
        storage_path=smart_path,
        content_type='application/octet-stream',
    )

    _update_progress(dataset_id, step=6, message='Step 6: Saving report...')
    existing_context = _parse_json_object(dataset.get('global_context'))
    existing_context['step6'] = {
        'status': 'completed',
        'input_path': processed_path,
        'output_path': smart_path,
        'rows_before': int(len(df)),
        'rows_after': int(len(df_smart)),
        'columns_count': int(len(df_smart.columns)),
        'report': step6_report,
    }
    update_dataset(dataset_id, {'global_context': existing_context})

    logger.info(
        f"Step 6 complete for dataset {dataset_id}: "
        f"smart output uploaded to {smart_path}"
    )


def _run_step7(dataset_id: str):
    """
    Step 7: Build dashboard blueprint from enriched metadata.

    1. Read dataset row + columns metadata
    2. Read Step 6 summary from global_context (if present)
    3. Ask Gemini for dataset classification and chart plan
    4. Validate/normalize plan and save in datasets.global_context.step7
    """
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        raise ValueError(
            f"No columns_metadata found for dataset {dataset_id}. "
            "Step 4 and Step 5 must run first."
        )

    existing_context = _parse_json_object(dataset.get('global_context'))
    step6_context = existing_context.get('step6') if isinstance(existing_context, dict) else None

    _update_progress(dataset_id, step=7, message='Step 7: Sending metadata to AI...')
    blueprint = run_step7_blueprint(dataset, columns_metadata, step6_context)

    _update_progress(dataset_id, step=7, message='Step 7: Saving dashboard blueprint...')
    existing_context['step7'] = blueprint
    update_dataset(dataset_id, {'global_context': existing_context})

    logger.info(
        f"Step 7 complete for dataset {dataset_id}: "
        f"type={blueprint.get('dataset_type')} charts={len(blueprint.get('suggested_charts', []))}"
    )


def _run_step8(dataset_id: str):
    """
    Step 8: AI Report Generation.

    Uses the enriched metadata from Steps 4-7 to generate a
    department-specific narrative business report via Gemini.
    Stores the result in datasets.global_context.step8.
    """
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        raise ValueError(
            f"No columns_metadata found for dataset {dataset_id}. "
            "Steps 4-5 must run first."
        )

    existing_context = _parse_json_object(dataset.get('global_context'))
    step6_context = existing_context.get('step6') if isinstance(existing_context, dict) else None
    step7_context = existing_context.get('step7') if isinstance(existing_context, dict) else None

    _update_progress(dataset_id, step=8, message='Step 8: Sending data to AI for report...')
    report = run_step8_report(dataset, columns_metadata, step6_context, step7_context)

    _update_progress(dataset_id, step=8, message='Step 8: Saving AI report...')
    existing_context['step8'] = report
    # Also store category_detection at the top level of global_context for easy access
    if report.get('category_detection'):
        existing_context['category_detection'] = report['category_detection']
    update_dataset(dataset_id, {'global_context': existing_context})

    logger.info(
        f"Step 8 complete for dataset {dataset_id}: "
        f"department={report.get('department')} sections={len(report.get('sections', []))}"
    )


# ══════════════════════════════════════════════════════════════
def _persist_dataset_rows(dataset_id: str):
    """
    Persist Step 6 smart rows into dataset_rows.

    This is the post-Step-7 storage step used by dashboard APIs:
      - reads smart parquet
      - writes each row as JSONB into dataset_rows
      - keeps stable row ordering via row_index
    """
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    processed_path = dataset.get('processed_path')
    if not processed_path:
        raise ValueError(
            f"Dataset {dataset_id} has no processed_path. "
            "Step 3 must run first."
        )

    existing_context = _parse_json_object(dataset.get('global_context'))
    step6_context = existing_context.get('step6') if isinstance(existing_context, dict) else None
    smart_path = step6_context.get('output_path') if isinstance(step6_context, dict) else None
    if not smart_path:
        smart_path = _build_smart_path(processed_path)

    file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, smart_path)
    df_smart = pd.read_parquet(io.BytesIO(file_bytes))

    # Pre-aggregate the dashboard charts ONCE here (full frame in memory) so the
    # dashboard/report serve tiny cached JSON instead of re-downloading + re-crunching
    # the full parquet on every open. Best-effort: never let it break the pipeline.
    try:
        step7_ctx = existing_context.get('step7') if isinstance(existing_context, dict) else None
        suggested_charts = (step7_ctx or {}).get('suggested_charts') or []
        if suggested_charts:
            from .views import build_chart_cache
            existing_context['chart_cache'] = build_chart_cache(df_smart, suggested_charts)
            logger.info(
                "Chart cache built for %s: %d charts",
                dataset_id, existing_context['chart_cache'].get('chart_count', 0),
            )
    except Exception as e:
        logger.warning("Chart cache build failed for %s (non-fatal): %s", dataset_id, e)

    # Only persist a bounded display sample (the table is browse-only, capped at 2000
    # per page downstream). The TRUE row count lives in datasets.file_info.row_count.
    total_rows = len(df_smart)
    if total_rows > DATASET_ROWS_DISPLAY_CAP:
        df_smart = df_smart.head(DATASET_ROWS_DISPLAY_CAP)

    records = df_smart.to_dict(orient='records')
    rows = [
        {
            'dataset_id': dataset_id,
            'row_index': i,
            'row_data': _normalize_row_data(record),
        }
        for i, record in enumerate(records)
    ]

    # Idempotency on reruns: replace old persisted rows for this dataset.
    delete_dataset_rows(dataset_id)
    inserted_count = insert_dataset_rows(rows, batch_size=1000)

    existing_context['storage'] = {
        'status': 'completed',
        'table': 'dataset_rows',
        'source_path': smart_path,
        'row_count': int(inserted_count),
        'total_rows': int(total_rows),
        'is_sampled': bool(total_rows > inserted_count),
    }
    update_dataset(dataset_id, {'global_context': existing_context})

    logger.info(
        f"Dataset rows persisted for {dataset_id}: "
        f"stored={inserted_count} of total={total_rows} source={smart_path}"
    )


# ══════════════════════════════════════════════════════════════
# ASYNC FORECAST TASK  (Accurate mode)
# ══════════════════════════════════════════════════════════════
# "fast" forecasts run synchronously in the request (bounded by the point cap, so
# they finish in seconds). "accurate" forecasts run all models and can take minutes,
# so they run here on a dedicated `forecasts` queue with its own higher time limit —
# the global 6-minute CELERY_TASK_TIME_LIMIT does NOT apply when set per task. Run a
# second worker for this queue so it never blocks the upload pipeline (Windows
# --pool=solo = one task at a time):
#   celery -A core worker --loglevel=info --pool=solo -Q forecasts
@shared_task(queue='forecasts', time_limit=600, soft_time_limit=570)
def run_forecast_task(
    *,
    dataset_id: str,
    user_id: str,
    source_path: str,
    time_column: str,
    target_column: str,
    id_columns: list | None,
    feature_columns: list | None,
    frequency: str | None,
    horizon: int,
    candidate_models: list | None,
    missing_periods_policy: str,
    mode: str,
) -> dict:
    """Run a forecast in the background and persist it to forecast_logs.

    Returns {'status': 'completed'|'failed', 'forecast_log_id': <id|None>, ...}.
    The HTTP status endpoint polls this task's result and, on success, serves the
    persisted forecast_logs row by id.
    """
    # Lazy import to avoid a tasks <-> views circular import at module load.
    from .views import _persist_forecast_log

    # Load the processed artifact (DataFrames can't cross the Celery JSON boundary,
    # so we pass the storage path and read it here).
    file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, source_path)
    ext = os.path.splitext(str(source_path).lower())[1]
    if ext == '.parquet':
        df = pd.read_parquet(io.BytesIO(file_bytes))
    elif ext == '.csv':
        df = pd.read_csv(io.BytesIO(file_bytes))
    elif ext in ('.xlsx', '.xls'):
        df = pd.read_excel(io.BytesIO(file_bytes))
    else:
        raise ValueError(f'Unsupported artifact extension for forecasting: {ext or "unknown"}')
    del file_bytes

    input_rows = len(df)
    try:
        result = run_forecast_service(
            df=df,
            time_column=time_column,
            target_column=target_column,
            id_columns=id_columns or [],
            feature_columns=feature_columns or [],
            frequency=frequency,
            horizon=horizon,
            candidate_models=candidate_models,
            missing_periods_policy=missing_periods_policy,
            mode=mode,
        )
    except Exception as exc:
        saved = _persist_forecast_log(
            dataset_id=dataset_id, user_id=user_id,
            time_column=time_column, target_column=target_column,
            feature_columns=feature_columns or [], frequency_hint=frequency,
            horizon=horizon, missing_policy=missing_periods_policy,
            input_rows=input_rows, candidate_models=candidate_models,
            result=None, error_message=str(exc),
        )
        logger.error("Async forecast failed: dataset=%s error=%s", dataset_id, exc)
        return {
            'status': 'failed',
            'error': str(exc),
            'forecast_log_id': (saved or {}).get('id'),
        }

    saved = _persist_forecast_log(
        dataset_id=dataset_id, user_id=user_id,
        time_column=time_column, target_column=target_column,
        feature_columns=feature_columns or [], frequency_hint=frequency,
        horizon=horizon, missing_policy=missing_periods_policy,
        input_rows=input_rows, candidate_models=candidate_models,
        result=result, error_message=None,
    )
    # Return the full result (service shape) so the status endpoint can hand it back
    # exactly like the sync path. Sanitize numpy/NaN so it survives the Celery JSON
    # result backend.
    from .supabase_client import _to_python
    safe_result = json.loads(json.dumps(_to_python(result), default=str))
    return {
        'status': 'completed',
        'forecast_log_id': (saved or {}).get('id'),
        'result': safe_result,
    }


# HELPERS
# ══════════════════════════════════════════════════════════════

def _update_progress(dataset_id: str, step: int, message: str, status: str = 'processing'):
    """Update tracking_jobs progress. Non-critical — swallows errors."""
    try:
        update_tracking_job(
            dataset_id=dataset_id,
            step=step,
            message=message,
            status=status,
        )
    except Exception as e:
        logger.warning(f"Failed to update tracking for {dataset_id}: {e}")


def _fail_pipeline(dataset_id: str, error_message: str):
    """Mark the pipeline as failed in tracking_jobs and datasets."""
    try:
        update_dataset(dataset_id, {'status': 'failed'})
    except Exception as e:
        logger.warning(f"Failed to mark dataset as failed for {dataset_id}: {e}")

    try:
        update_tracking_job(
            dataset_id=dataset_id,
            step=0,
            message=f'Pipeline failed: {error_message}',
            status='failed',
        )
    except Exception as e:
        logger.warning(f"Failed to mark pipeline as failed for {dataset_id}: {e}")


def _read_dataframe_for_step6(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Read cleaned file bytes into DataFrame for Step 6 transforms.
    """
    ext = os.path.splitext(filename.lower())[1]
    if ext == '.parquet':
        return pd.read_parquet(io.BytesIO(file_bytes))
    if ext == '.csv':
        return pd.read_csv(io.BytesIO(file_bytes))
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(io.BytesIO(file_bytes))
    raise ValueError(f'Unsupported file type for Step 6: {ext or "unknown"}')


def _build_smart_path(processed_path: str) -> str:
    """
    Build output path for Step 6 smart parquet artifact.
    """
    base, _ = os.path.splitext(processed_path)
    return f'{base}_smart.parquet'


def _parse_json_object(value) -> dict:
    """
    Parse a JSON object stored as dict or JSON string.
    """
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_row_data(row: dict) -> dict:
    """Normalize a DataFrame row record into JSON-safe values."""
    normalized = {}
    for key, value in row.items():
        normalized[str(key)] = _to_json_compatible(value)
    return normalized


def _to_json_compatible(value):
    """
    Convert pandas/numpy/date/decimal values to JSON-safe primitives.
    """
    if value is None:
        return None

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, dict):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(v) for v in value]

    if hasattr(value, 'item'):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, float):
        if pd.isna(value) or value in (float('inf'), float('-inf')):
            return None
        return value

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', errors='ignore')

    return value


