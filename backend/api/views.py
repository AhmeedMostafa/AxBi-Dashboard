import concurrent.futures
import base64
import json
import io
import logging
import os
import threading
import time
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .supabase_client import (
    upload_file_to_bucket,
    insert_dataset,
    insert_tracking_job,
    insert_forecast_log,
    list_user_datasets,
    delete_dataset_full,
    get_tracking_job,
    get_dataset,
    get_columns_metadata,
    get_dataset_rows,
    verify_supabase_token,
    download_file_bytes,
    CLEANED_DATA_BUCKET,
    RAW_DATA_BUCKET,
    delete_columns_metadata,
    insert_columns_metadata,
    update_dataset,
    update_tracking_job,
    insert_forecast_result,
    get_forecast_results,
    get_forecast_result_by_id,
    delete_forecast_log,
    get_user_kpi_stats,
)
from .processing.step4_column_detection import run_step4
from .processing.step8_ai_report import detect_category_only
from .tasks import process_dataset_pipeline, append_dataset_pipeline
from .forecasting import run_forecast_service
from .segmentation import run_segmentation_service
from .recommendations import run_recommendations_service
from . import voice_logger

logger = logging.getLogger(__name__)


@api_view(['GET'])
def health_view(request):
    """Unauthenticated liveness probe for Docker healthchecks and load balancers."""
    return Response({'status': 'ok'})


# ── Forecast endpoint guards ───────────────────────────────────────────────
FORECAST_TIMEOUT_S   = 300          # 5-minute hard timeout per request
FORECAST_COOLDOWN_S  = 30           # seconds a user must wait between runs
_forecast_cooldowns: dict[str, float] = {}
_forecast_cooldowns_lock = threading.Lock()


def _check_and_set_cooldown(user_id: str) -> float:
    """Return remaining cooldown seconds (0 if not rate-limited)."""
    now = time.time()
    with _forecast_cooldowns_lock:
        last = _forecast_cooldowns.get(user_id, 0.0)
        remaining = FORECAST_COOLDOWN_S - (now - last)
        if remaining > 0:
            return remaining
        _forecast_cooldowns[user_id] = now
        return 0.0


ALLOWED_EXTENSIONS = ['.csv', '.xlsx', '.xls']

CONTENT_TYPES = {
    '.csv': 'text/csv',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel',
}

TOTAL_PIPELINE_STEPS = 8
DISPLAY_NAME_CONFIDENCE_THRESHOLD = 0.8


def _safe_step_int(value, default: int = 1) -> int:
    """Convert current_step to int safely (DB may return text)."""
    try:
        step = int(value)
        return step if step > 0 else default
    except (TypeError, ValueError):
        return default


def _build_progress(current_step_value) -> dict:
    """Build a frontend-friendly progress payload."""
    current_step = _safe_step_int(current_step_value)
    percent = min(100.0, round((current_step / TOTAL_PIPELINE_STEPS) * 100, 2))
    return {
        'current_step': current_step,
        'total_steps': TOTAL_PIPELINE_STEPS,
        'progress_percent': percent,
    }


def _parse_json_if_string(value):
    """Return parsed JSON for string payloads; otherwise return value as-is."""
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _prettify_column_name(name: str) -> str:
    if not name:
        return ""
    tokens = [token for token in str(name).replace("-", "_").split("_") if token]
    if not tokens:
        return str(name).strip()
    acronym_tokens = {"id", "kpi", "url", "api", "ip"}
    words = [token.upper() if token.lower() in acronym_tokens else token.capitalize() for token in tokens]
    return " ".join(words)


def _resolve_display_name(clean_name: str, original_name: str, ai_profile) -> str:
    if isinstance(ai_profile, dict):
        semantic_meaning = str(ai_profile.get("semantic_meaning") or "").strip()
        confidence = _safe_float(ai_profile.get("column_confidence"), default=0.0)
        if semantic_meaning and confidence >= DISPLAY_NAME_CONFIDENCE_THRESHOLD:
            return semantic_meaning

    return _prettify_column_name(clean_name or original_name or "")


def _normalize_columns_for_response(columns: list[dict]) -> list[dict]:
    """Normalize JSON-like fields so frontend receives objects, not JSON strings."""
    normalized = []
    for col in columns:
        row = dict(col)
        row['technical_stats'] = _parse_json_if_string(row.get('technical_stats'))
        row['ai_profile'] = _parse_json_if_string(row.get('ai_profile'))
        row['column_key'] = row.get('clean_name') or row.get('original_name') or ''
        row['display_name'] = _resolve_display_name(
            row.get('clean_name', ''),
            row.get('original_name', ''),
            row.get('ai_profile'),
        )
        normalized.append(row)
    return normalized

# function to authenticate user through his token
def _authenticate_request(request):
    auth_header = request.headers.get('Authorization', '')
    try:
        user_info = verify_supabase_token(auth_header)
        return user_info['user_id'], None
    except ValueError as e:
        return None, Response(
            {'error': 'Unauthorized', 'message': str(e)},
            status=status.HTTP_401_UNAUTHORIZED
        )


MAX_DATASETS_PER_USER = 4


@api_view(['GET'])
def list_datasets_view(request):
    """GET /api/datasets/ — list all datasets for the authenticated user."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error
    datasets = list_user_datasets(user_id)
    # Return lightweight card-friendly fields
    cards = []
    for ds in datasets:
        file_info = _parse_json_if_string(ds.get('file_info')) or {}
        if not isinstance(file_info, dict):
            file_info = {}
        global_ctx = _parse_json_if_string(ds.get('global_context')) or {}
        if not isinstance(global_ctx, dict):
            global_ctx = {}
        step4 = global_ctx.get('step4') or {}
        cat_detection = global_ctx.get('category_detection') or {}
        resolved_category = (
            cat_detection.get('resolved_category')
            or ds.get('category')
            or ds.get('category_hint')
        )
        filename = (
            file_info.get('original_filename')
            or file_info.get('filename')
            or file_info.get('original_name')
            or ds.get('file_name')
            or 'Untitled dataset'
        )
        cards.append({
            'id': ds['id'],
            'created_at': ds.get('created_at'),
            'name': ds.get('project_name') or None,
            'filename': filename,
            'category': ds.get('category') or ds.get('category_hint'),
            'resolved_category': resolved_category,
            'status': ds.get('status', 'pending'),
            'row_count': step4.get('total_rows') or file_info.get('row_count'),
            'column_count': step4.get('total_columns') or file_info.get('column_count'),
        })
    return Response({
        'datasets': cards,
        'count': len(cards),
        'limit': MAX_DATASETS_PER_USER,
        'at_limit': len(cards) >= MAX_DATASETS_PER_USER,
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_dataset_view(request, dataset_id):
    """DELETE /api/datasets/<dataset_id>/ — delete dataset and all dependencies."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error
    try:
        delete_dataset_full(dataset_id, user_id)
        return Response({'deleted': True, 'dataset_id': dataset_id}, status=status.HTTP_200_OK)
    except PermissionError as e:
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error('Failed to delete dataset %s: %s', dataset_id, e, exc_info=True)
        return Response({'error': 'Delete failed', 'message': str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def file_upload(request):
    """
    POST /api/file-upload/

    1. Receive file from frontend
    2. Store in Supabase bucket
    3. Create row in datasets table
    4. Create row in tracking_jobs table
    5. Return dataset_id
    """
    try:
        return _file_upload_inner(request)
    except Exception as exc:
        logger.exception('Unhandled error in file_upload: %s', exc)
        return Response(
            {'error': 'Upload failed', 'message': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _file_upload_inner(request):
    # ══════════════════════════════════════════
    # AUTHENTICATE USER FROM TOKEN
    # ══════════════════════════════════════════
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    # ══════════════════════════════════════════
    # ENFORCE PROJECT LIMIT
    # ══════════════════════════════════════════
    try:
        existing = list_user_datasets(user_id)
        # Only active datasets count toward the limit; failed ones are dead weight
        existing = [d for d in existing if d.get('status') not in ('failed',)]
    except Exception as e:
        logger.warning('Could not fetch existing datasets for limit check: %s', e)
        existing = []

    if len(existing) >= MAX_DATASETS_PER_USER:
        return Response(
            {'error': f'Project limit reached. You can have at most {MAX_DATASETS_PER_USER} projects. Delete one to upload a new file.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # ══════════════════════════════════════════
    # VALIDATE REQUEST
    # ══════════════════════════════════════════
    files = request.FILES.getlist('file')
    if not files:
        return Response({'error': 'No file provided'}, status=status.HTTP_400_BAD_REQUEST)

    category = request.POST.get('category')
    if category is None or not str(category).strip():
        return Response({'error': 'Category is required'}, status=status.HTTP_400_BAD_REQUEST)
    category = str(category).strip()
    if category.lower() in {'auto', 'undefined', 'null', 'none'}:
        return Response({'error': 'Category is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Optional user-facing project name from the onboarding wizard.
    project_name = (request.POST.get('project_name') or '').strip() or None

    raw_files: list[tuple[str, bytes]] = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return Response(
                {'error': f'Invalid file type for {f.name}', 'allowed': ALLOWED_EXTENSIONS},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw_files.append((f.name, f.read()))

    from api.accumulation.service import accumulate_files
    try:
        acc = accumulate_files(raw_files, allow_single=True)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    combined_df = acc['dataframe']
    primary_name = acc['accepted'][0]

    # ══════════════════════════════════════════
    # UPLOAD COMBINED FILE TO SUPABASE BUCKET
    # ══════════════════════════════════════════
    try:
        csv_bytes = combined_df.to_csv(index=False).encode('utf-8')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        storage_filename = f"{user_id}/{timestamp}_{uuid.uuid4().hex[:8]}.csv"
        storage_path = upload_file_to_bucket(csv_bytes, storage_filename, 'text/csv')
    except Exception as e:
        return Response(
            {'error': 'File upload to storage failed', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ══════════════════════════════════════════
    # CREATE DATASET ROW IN SUPABASE
    # ══════════════════════════════════════════
    try:
        dataset = insert_dataset(
            user_id=user_id,
            file_name=primary_name if len(acc['accepted']) == 1
            else f"{primary_name} (+{len(acc['accepted']) - 1} more)",
            category_hint=category,
            storage_path=storage_path,
            project_name=project_name,
        )
    except Exception as e:
        return Response(
            {'error': 'Failed to create dataset record', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ══════════════════════════════════════════
    # CREATE TRACKING JOB IN SUPABASE
    # ══════════════════════════════════════════
    try:
        tracking_job = insert_tracking_job(dataset_id=dataset['id'], user_id=user_id)
    except Exception as e:
        return Response(
            {'error': 'Failed to create tracking job', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ══════════════════════════════════════════
    # QUEUE ASYNC PROCESSING PIPELINE
    # ══════════════════════════════════════════
    # The pipeline runs Steps 3→4→5→6 in a Celery worker.
    # Django returns immediately; frontend polls /api/check/{job_id}/.
    try:
        process_dataset_pipeline.delay(dataset['id'])
    except Exception:
        # If Redis/Celery is down, the task won't be queued.
        # The upload still succeeded — processing can be retried later.
        pass

    # ══════════════════════════════════════════
    # RETURN RESPONSE
    # ══════════════════════════════════════════
    return Response(
        {
            'dataset_id': dataset['id'],
            'job_id': tracking_job['id'],
            'status': 'pending',
            **_build_progress(tracking_job.get('current_step', 1)),
            'accepted_files': acc['accepted'],
            'rejected_files': acc['rejected'],
            'message': f'File "{primary_name}" uploaded. Pending to Preprocessing process.',
        },
        status=status.HTTP_202_ACCEPTED
    )


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def append_to_dataset_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/append/

    Append one or more CSV/XLSX files to an existing dataset.
    New rows are accumulated on top of the already-cleaned data.
    The pipeline is re-queued so all downstream steps are refreshed.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    files = request.FILES.getlist('file')
    if not files:
        return Response({'error': 'No file provided'}, status=status.HTTP_400_BAD_REQUEST)

    raw_files: list[tuple[str, bytes]] = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return Response(
                {'error': f'Invalid file type for {f.name}', 'allowed': ALLOWED_EXTENSIONS},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw_files.append((f.name, f.read()))

    from api.accumulation.service import accumulate_files, schemas_match

    # Read the NEW files into one normalized frame (no base merge yet) so we can decide
    # between the incremental and full paths.
    try:
        acc = accumulate_files(raw_files, base_df=None, allow_single=True)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    new_df = acc['dataframe']

    # ── Incremental append (Approach B) ─────────────────────────────────────────────
    # If the appended file matches the existing schema and we have both prior artifacts
    # (cleaned + smart parquet), only the NEW rows get cleaned/smart-transformed; the
    # old rows are reused. Falls back to the full pipeline otherwise.
    context = _parse_json_if_string(dataset.get('global_context'))
    context = context if isinstance(context, dict) else {}
    prev_processed = dataset.get('processed_path')
    step6_ctx = context.get('step6') if isinstance(context.get('step6'), dict) else {}
    prev_smart = step6_ctx.get('output_path')
    existing_cols = get_columns_metadata(dataset_id) or []
    existing_clean = [c['clean_name'] for c in existing_cols if c.get('clean_name')]
    schema_ok = bool(existing_clean) and schemas_match(existing_clean, list(new_df.columns))[0]

    if prev_processed and prev_smart and schema_ok:
        try:
            buf = io.BytesIO()
            new_df.to_parquet(buf, index=False)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_raw_name = f"{user_id}/{timestamp}_{uuid.uuid4().hex[:8]}_append_new.parquet"
            new_raw_path = upload_file_to_bucket(buf.getvalue(), new_raw_name, 'application/octet-stream')
        except Exception as e:
            return Response(
                {'error': 'File upload to storage failed', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        context['append'] = {
            'prev_processed_path': prev_processed,
            'prev_smart_path': prev_smart,
            'new_raw_path': new_raw_path,
        }
        try:
            # Keep processed_path — the incremental task needs the prior cleaned artifact.
            update_dataset(dataset_id, {'global_context': context, 'status': 'pending'})
        except Exception as e:
            return Response(
                {'error': 'Failed to update dataset record', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            tracking_job = insert_tracking_job(dataset_id=dataset_id, user_id=user_id)
        except Exception as e:
            return Response(
                {'error': 'Failed to create tracking job', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            append_dataset_pipeline.delay(dataset_id)
        except Exception:
            pass

        return Response(
            {
                'dataset_id': dataset_id,
                'job_id': tracking_job['id'],
                'status': 'pending',
                **_build_progress(tracking_job.get('current_step', 1)),
                'accepted_files': acc['accepted'],
                'rejected_files': acc['rejected'],
                'rows_added': len(new_df),
                'mode': 'incremental',
                'message': f'Appended {len(acc["accepted"])} file(s) incrementally. Re-processing started.',
            },
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Fallback: full reprocess (schema changed / missing prior artifacts) ──────────
    base_df = None
    if prev_processed:
        try:
            existing_bytes = download_file_bytes(CLEANED_DATA_BUCKET, prev_processed)
            ext = os.path.splitext(str(prev_processed).lower())[1]
            if ext == '.parquet':
                base_df = pd.read_parquet(io.BytesIO(existing_bytes))
            else:
                base_df = pd.read_csv(io.BytesIO(existing_bytes))
            from api.accumulation.service import normalize_columns
            base_df = normalize_columns(base_df)
        except Exception as e:
            logger.warning('append_to_dataset_view: could not load existing data (%s); starting fresh. %s', prev_processed, e)
            base_df = None

    try:
        acc = accumulate_files(raw_files, base_df=base_df, allow_single=True)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    combined_df = acc['dataframe']

    # Upload combined data as a new raw Parquet. CSV of the merged frame (600k+
    # rows) blows past Supabase Storage's per-object size limit -> HTTP 413. Parquet
    # is ~4x smaller and step 3 reads it back by extension.
    try:
        buf = io.BytesIO()
        # base_df is type-cleaned (read from parquet); freshly-read files are raw.
        # Concat leaves mixed-type object columns (datetime+str, int+str) that pyarrow
        # can't serialize. Cast object cols to nullable string; step 3 re-coerces types.
        for _c in combined_df.select_dtypes(include=['object']).columns:
            combined_df[_c] = combined_df[_c].astype('string')
        combined_df.to_parquet(buf, index=False)
        parquet_bytes = buf.getvalue()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        storage_filename = f"{user_id}/{timestamp}_{uuid.uuid4().hex[:8]}_appended.parquet"
        new_storage_path = upload_file_to_bucket(parquet_bytes, storage_filename, 'application/octet-stream')
    except Exception as e:
        return Response(
            {'error': 'File upload to storage failed', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Reset the dataset to re-run the full pipeline
    try:
        update_dataset(dataset_id, {
            'storage_path': new_storage_path,
            'processed_path': None,
            'status': 'pending',
        })
    except Exception as e:
        return Response(
            {'error': 'Failed to update dataset record', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        tracking_job = insert_tracking_job(dataset_id=dataset_id, user_id=user_id)
    except Exception as e:
        return Response(
            {'error': 'Failed to create tracking job', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        process_dataset_pipeline.delay(dataset_id)
    except Exception:
        pass

    return Response(
        {
            'dataset_id': dataset_id,
            'job_id': tracking_job['id'],
            'status': 'pending',
            **_build_progress(tracking_job.get('current_step', 1)),
            'accepted_files': acc['accepted'],
            'rejected_files': acc['rejected'],
            'total_rows': len(combined_df),
            'mode': 'full',
            'message': f'Appended {len(acc["accepted"])} file(s). Re-processing pipeline started.',
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
def check_job_status(request, job_id):
    """
    GET /api/check/{job_id}/

    Check processing status by looking up tracking_jobs table by job id.
    If completed, also return columns_metadata.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    # Get tracking job
    job = get_tracking_job(job_id)

    if not job:
        return Response(
            {'error': 'Job not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    if str(job.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this job'},
            status=status.HTTP_403_FORBIDDEN
        )

    dataset_id = job.get('dataset_id')

    # Base response
    progress_payload = _build_progress(job.get('current_step'))
    response_data = {
        'job_id': job['id'],
        'dataset_id': dataset_id,
        'status': job['status'],
        **progress_payload,
        'progress_message': job['progress_message'],
    }

    # If completed, include dataset info and columns
    if job['status'] == 'completed':
        dataset = get_dataset(dataset_id)
        columns = get_columns_metadata(dataset_id)
        columns = _normalize_columns_for_response(columns)
        global_context = _parse_json_if_string(dataset.get('global_context')) if dataset else None

        response_data['data'] = {
            'dataset_id': dataset_id,
            'file_name': dataset['file_name'] if dataset else '',
            'category_hint': dataset['category_hint'] if dataset else '',
            'global_context': global_context,
            'file_info': _parse_json_if_string(dataset.get('file_info')) if dataset else {},
        }
        response_data['columns'] = columns

    # If failed, include error
    if job['status'] == 'failed':
        response_data['error_log'] = job['error_log']

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(['PATCH'])
def update_dataset_category_view(request, dataset_id):
    """
    PATCH /api/datasets/<dataset_id>/category/

    Allows the user to confirm or override the AI-detected category.
    Updates resolved_category in global_context.category_detection
    and category_hint on the dataset record so all future operations use it.

    Body: { "category": "Sales" | "HR" | "Operations" | "Marketing" }
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    new_category = str(request.data.get('category') or '').strip()
    valid = {'sales', 'hr', 'operations', 'marketing', 'business'}
    if new_category.lower() not in valid:
        return Response(
            {'error': f'Invalid category. Must be one of: {", ".join(v.title() for v in valid)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    resolved = new_category.title()

    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    cat_detection = global_context.get('category_detection') or {}
    cat_detection['resolved_category'] = resolved
    cat_detection['user_confirmed'] = True
    cat_detection['mismatch_warning'] = False
    global_context['category_detection'] = cat_detection

    # Also mirror into step8.department so the report shows the updated category
    step8 = global_context.get('step8') or {}
    if isinstance(step8, dict):
        step8['department'] = resolved
        global_context['step8'] = step8

    update_dataset(dataset_id, {
        'global_context': global_context,
        'category_hint': resolved,
    })

    logger.info("Category updated by user: dataset=%s category=%s", dataset_id, resolved)
    return Response({
        'dataset_id': dataset_id,
        'resolved_category': resolved,
        'message': f'Category updated to {resolved}',
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def detect_dataset_category_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/detect-category/

    Lightweight Gemini call that detects the true category of a dataset
    from its column metadata. Stores the result in global_context.category_detection
    and returns it. Used to backfill detection for datasets processed before
    the full category detection was added to Step 8.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        return Response(
            {'error': 'No column metadata found. Pipeline must complete first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        cat_detection = detect_category_only(dataset, columns_metadata)
    except Exception as e:
        logger.error("detect_category_only failed: dataset=%s error=%s", dataset_id, e)
        return Response({'error': 'Category detection failed', 'message': str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Persist in global_context and update step8.department with resolved_category
    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    global_context['category_detection'] = cat_detection

    step8 = global_context.get('step8') or {}
    if isinstance(step8, dict) and step8.get('sections'):
        step8['department'] = cat_detection['resolved_category']
        global_context['step8'] = step8

    update_dataset(dataset_id, {'global_context': global_context})
    logger.info(
        "detect_dataset_category_view: dataset=%s resolved=%s overridden=%s",
        dataset_id, cat_detection['resolved_category'], cat_detection['overridden'],
    )

    return Response({
        'dataset_id': dataset_id,
        'category_detection': cat_detection,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_dataset_dashboard_view(request, dataset_id):
    """
    GET /api/datasets/<dataset_id>/dashboard/

    Returns completed dashboard data for a dataset (same shape as check_job_status
    when status=completed). Used by the Projects page when opening a project card.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    ds_status = dataset.get('status', 'pending')
    global_context = _parse_json_if_string(dataset.get('global_context')) or {}

    response_data = {
        'job_id': None,
        'dataset_id': dataset_id,
        'status': ds_status,
        'current_step': 7 if ds_status == 'completed' else 1,
        'total_steps': TOTAL_PIPELINE_STEPS,
        'progress_percent': 100.0 if ds_status == 'completed' else 0.0,
        'progress_message': 'Completed' if ds_status == 'completed' else ds_status,
    }

    if ds_status == 'completed':
        columns = get_columns_metadata(dataset_id)
        columns = _normalize_columns_for_response(columns)
        response_data['data'] = {
            'dataset_id': dataset_id,
            'file_name': dataset.get('file_name', ''),
            'category_hint': dataset.get('category_hint', ''),
            'global_context': global_context,
            'file_info': _parse_json_if_string(dataset.get('file_info')) or {},
        }
        response_data['columns'] = columns

    return Response(response_data, status=status.HTTP_200_OK)


def _chart_cache_key(chart: dict) -> tuple:
    """Canonical identity for a chart spec, used to match a request against the cache."""
    return (
        str(chart.get('chart_type', '')).lower().replace('-', '_'),
        chart.get('x_axis'),
        chart.get('y_axis'),
        chart.get('y_axis_secondary'),
    )


def _match_cached_charts(charts: list, cache: dict):
    """Return the cached results list (in request order) if EVERY requested chart is
    present in the cache; otherwise None so the caller falls back to a live parquet read.

    `cache` shape: {'by_key': {key_str: result_dict}}.
    """
    by_key = (cache or {}).get('by_key')
    if not isinstance(by_key, dict) or not by_key:
        return None
    out = []
    for chart in charts:
        key = '|'.join('' if p is None else str(p) for p in _chart_cache_key(chart))
        hit = by_key.get(key)
        if hit is None:
            return None
        out.append(hit)
    return out


def _is_numeric_column(df, col) -> bool:
    """True if the column is (or parses cleanly as) numeric."""
    if not col or col not in df.columns:
        return False
    if pd.api.types.is_numeric_dtype(df[col]):
        return True
    parsed = pd.to_numeric(df[col], errors='coerce')
    non_null = int(df[col].notna().sum())
    return non_null > 0 and (parsed.notna().sum() / non_null) >= 0.9


def _corrected_bar_axes(df, x_axis, y_axis):
    """Fix swapped axes for (horizontal) bars: category belongs on x, measure on y.

    If x is numeric and y is a non-numeric (categorical) column, swap them so the
    aggregation groups by the category and sums the measure instead of trying to
    sum a text column (which yields all-zero bars).
    """
    if not x_axis or not y_axis:
        return x_axis, y_axis
    if x_axis not in df.columns or y_axis not in df.columns:
        return x_axis, y_axis
    x_numeric = _is_numeric_column(df, x_axis)
    y_numeric = _is_numeric_column(df, y_axis)
    if x_numeric and not y_numeric:
        return y_axis, x_axis
    return x_axis, y_axis


def aggregate_charts_from_df(df, charts: list) -> list:
    """Pre-aggregate a list of chart specs against an in-memory DataFrame.

    Single source of truth for chart aggregation — used both by the live
    aggregate endpoint (parquet read) and by the pipeline cache builder.
    Returns a list of {chart_type, x_axis, y_axis, data:[...]} in input order.
    """
    MAX_BAR_LINE_GROUPS = 200
    MAX_PIE_SLICES      = 12

    results = []
    for chart in charts:
        raw_type = chart.get('chart_type', '').lower().replace('-', '_')
        chart_type = raw_type
        # Normalise aliases to a shared aggregation shape
        if chart_type in ('donut', 'treemap', 'funnel'):
            chart_type = 'pie'        # part-to-whole → {name, value}
        elif chart_type in ('area', 'stacked_bar', 'area_chart', 'horizontal_bar'):
            chart_type = 'bar'        # category/measure → {label, value}
        elif chart_type == 'line':
            chart_type = 'line'
        x_axis     = chart.get('x_axis')
        y_axis     = chart.get('y_axis')
        # For horizontal bars the AI sometimes swaps axes (category on x, measure on y).
        # If x is numeric and y is categorical, correct it so we group by the category
        # and aggregate the measure (otherwise every bar sums a text column → 0).
        if raw_type == 'horizontal_bar':
            x_axis, y_axis = _corrected_bar_axes(df, x_axis, y_axis)
        y_axis_2   = chart.get('y_axis_secondary')

        try:
            if chart_type == 'kpi_card':
                col = y_axis or x_axis
                if col and col in df.columns:
                    numeric = pd.to_numeric(df[col], errors='coerce')
                    value = float(numeric.sum())
                else:
                    value = 0.0
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': [{'value': value}]})

            elif chart_type in ('bar', 'line'):
                if not x_axis or x_axis not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                if y_axis and y_axis in df.columns:
                    grouped = df.groupby(x_axis, sort=False)[y_axis].apply(
                        lambda s: pd.to_numeric(s, errors='coerce').sum()
                    ).reset_index()
                    grouped.columns = ['label', 'value']
                else:
                    grouped = df.groupby(x_axis, sort=False).size().reset_index()
                    grouped.columns = ['label', 'value']

                grouped['label'] = grouped['label'].astype(str)
                grouped['value'] = pd.to_numeric(grouped['value'], errors='coerce').fillna(0)

                # Sort: dates chronologically, others by value desc
                sample_val = str(grouped['label'].iloc[0]) if len(grouped) else ''
                try:
                    pd.to_datetime(sample_val)
                    grouped['_t'] = pd.to_datetime(grouped['label'], errors='coerce')
                    grouped = grouped.sort_values('_t').drop(columns='_t')
                except Exception:
                    grouped = grouped.sort_values('value', ascending=False)

                grouped = grouped.head(MAX_BAR_LINE_GROUPS)
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': grouped[['label', 'value']].to_dict(orient='records')})

            elif chart_type == 'pie':
                if not x_axis or x_axis not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                if y_axis and y_axis in df.columns:
                    grouped = df.groupby(x_axis, sort=False)[y_axis].apply(
                        lambda s: pd.to_numeric(s, errors='coerce').sum()
                    ).reset_index()
                    grouped.columns = ['name', 'value']
                else:
                    grouped = df.groupby(x_axis, sort=False).size().reset_index()
                    grouped.columns = ['name', 'value']

                grouped['name']  = grouped['name'].astype(str)
                grouped['value'] = pd.to_numeric(grouped['value'], errors='coerce').fillna(0)
                grouped = grouped[grouped['value'] > 0].sort_values('value', ascending=False)

                if len(grouped) > MAX_PIE_SLICES:
                    head = grouped.iloc[:MAX_PIE_SLICES - 1]
                    others_val = float(grouped.iloc[MAX_PIE_SLICES - 1:]['value'].sum())
                    others_row = pd.DataFrame([{'name': 'Others', 'value': others_val}])
                    grouped = pd.concat([head, others_row], ignore_index=True)

                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': grouped.to_dict(orient='records')})

            elif chart_type == 'combo':
                # Bars = measure grouped by x; line = secondary measure (if given) else growth %.
                if not x_axis or x_axis not in df.columns or not y_axis or y_axis not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                grouped = df.groupby(x_axis, sort=False)[y_axis].apply(
                    lambda s: pd.to_numeric(s, errors='coerce').sum()
                ).reset_index()
                grouped.columns = ['label', 'value']
                grouped['label'] = grouped['label'].astype(str)
                grouped['value'] = pd.to_numeric(grouped['value'], errors='coerce').fillna(0)

                sample_val = str(grouped['label'].iloc[0]) if len(grouped) else ''
                try:
                    pd.to_datetime(sample_val)
                    grouped['_t'] = pd.to_datetime(grouped['label'], errors='coerce')
                    grouped = grouped.sort_values('_t').drop(columns='_t')
                except Exception:
                    grouped = grouped.sort_values('value', ascending=False)
                grouped = grouped.head(MAX_BAR_LINE_GROUPS)

                sec_series = None
                if y_axis_2 and y_axis_2 in df.columns:
                    sec_series = df.groupby(x_axis, sort=False)[y_axis_2].apply(
                        lambda s: pd.to_numeric(s, errors='coerce').sum()
                    )
                    # Key the secondary map with the SAME stringification used for the
                    # primary labels (grouped['label'] used pandas .astype(str), NOT
                    # Python str(Timestamp)). Otherwise a datetime x-axis renders keys as
                    # '2024-01-01' vs '2024-01-01 00:00:00' → every lookup misses → the line
                    # is a flat 0. Categorical x-axes were unaffected (identical rendering).
                    sec_index = sec_series.copy()
                    sec_index.index = sec_index.index.astype(str)
                    sec_map = sec_index.to_dict()
                    grouped['line'] = grouped['label'].map(sec_map).fillna(0.0).astype(float)
                    line_name, line_is_pct = y_axis_2, False

                # Fall back to the growth-% line when there is no secondary column OR the
                # secondary series is empty/all-zero (a dead flat line labeled with the
                # column is worse than showing the trend).
                if sec_series is None or float(pd.to_numeric(grouped['line'], errors='coerce').abs().sum()) == 0.0:
                    vals = grouped['value'].tolist()
                    line_vals = [0.0]
                    for i in range(1, len(vals)):
                        prev = vals[i - 1]
                        line_vals.append(round((vals[i] - prev) / prev * 100.0, 2) if prev else 0.0)
                    grouped['line'] = line_vals
                    line_name, line_is_pct = 'Growth %', True

                combo_data = [
                    {'label': r['label'], 'value': float(r['value']), 'line': float(r['line']),
                     'line_name': line_name, 'line_is_pct': line_is_pct}
                    for _, r in grouped.iterrows()
                ]
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': combo_data})

            elif chart_type == 'radial':
                # Gauge: average of metric relative to peak (or 100% for ratio/percentage metrics).
                col = y_axis or x_axis
                if not col or col not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                numeric = pd.to_numeric(df[col], errors='coerce').dropna()
                if numeric.empty:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                     'data': [{'value': 0.0, 'max': 0.0, 'unit': ''}]})
                    continue
                vmin, vmax, vmean = float(numeric.min()), float(numeric.max()), float(numeric.mean())
                if vmin >= 0 and vmax <= 1:
                    value, max_v, unit = round(vmean * 100.0, 2), 100.0, '%'
                elif vmin >= 0 and vmax <= 100:
                    value, max_v, unit = round(vmean, 2), 100.0, '%'
                else:
                    value, max_v, unit = round(vmean, 2), round(vmax, 2), ''
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': [{'value': value, 'max': max_v, 'unit': unit}]})

            elif chart_type == 'histogram':
                if not x_axis or x_axis not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                series = pd.to_numeric(df[x_axis], errors='coerce').dropna()
                if series.empty:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                counts, edges = np.histogram(series.to_numpy(dtype=float), bins=min(20, max(5, series.nunique())))
                hist_data = [
                    {'label': f'{edges[i]:.2g}–{edges[i + 1]:.2g}', 'value': int(counts[i])}
                    for i in range(len(counts))
                ]
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': hist_data})

            elif chart_type == 'pareto':
                if not x_axis or x_axis not in df.columns or not y_axis or y_axis not in df.columns:
                    results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})
                    continue
                grouped = df.groupby(x_axis, sort=False)[y_axis].apply(
                    lambda s: pd.to_numeric(s, errors='coerce').sum()
                ).reset_index()
                grouped.columns = ['label', 'value']
                grouped['value'] = pd.to_numeric(grouped['value'], errors='coerce').fillna(0)
                grouped = grouped[grouped['value'] > 0].sort_values('value', ascending=False)
                grouped = grouped.head(MAX_BAR_LINE_GROUPS)
                total = float(grouped['value'].sum()) or 1.0
                grouped['label'] = grouped['label'].astype(str)
                cum = 0.0
                pareto_data = []
                for _, r in grouped.iterrows():
                    cum += float(r['value'])
                    pareto_data.append({
                        'label': r['label'],
                        'value': float(r['value']),
                        'cumulative': round(cum / total * 100.0, 2),
                    })
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis,
                                 'data': pareto_data})

            else:
                results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})

        except Exception as e:
            logger.warning('aggregate_charts_from_df: chart %s failed: %s', chart_type, e)
            results.append({'chart_type': chart_type, 'x_axis': x_axis, 'y_axis': y_axis, 'data': []})

    return results


def build_chart_cache(df, charts: list) -> dict:
    """Aggregate the default charts ONCE (during the pipeline, while the frame is in
    memory) so the dashboard/report can read tiny JSON instead of re-crunching the
    full parquet on every open. Stored under global_context.chart_cache."""
    results = aggregate_charts_from_df(df, charts)
    by_key = {}
    for chart, result in zip(charts, results):
        key = '|'.join('' if p is None else str(p) for p in _chart_cache_key(chart))
        by_key[key] = result
    return {'by_key': by_key, 'chart_count': len(by_key)}


@api_view(['POST'])
def aggregate_charts_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/aggregate/

    Returns pre-aggregated data per chart. Serves a cached aggregation built during
    the pipeline when the requested charts match (the default dashboard/report case),
    avoiding a full parquet download+parse on every open. Falls back to a live parquet
    aggregation for custom/edited charts not in the cache.

    Body: { charts: [{chart_type, x_axis, y_axis, ...}] }
    Response: { results: [{chart_type, x_axis, y_axis, data: [...]}], cached: bool }
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    charts = request.data.get('charts', [])
    if not isinstance(charts, list):
        return Response({'error': 'charts must be a list'}, status=status.HTTP_400_BAD_REQUEST)

    # Fast path: every requested chart was pre-aggregated during the pipeline.
    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    cached = _match_cached_charts(charts, global_context.get('chart_cache') or {})
    if cached is not None:
        return Response({'results': cached, 'cached': True}, status=status.HTTP_200_OK)

    # Miss (custom/edited charts) → aggregate live from the parquet.
    df, err = _load_smart_dataframe(dataset)
    if err is not None:
        return err

    results = aggregate_charts_from_df(df, charts)
    return Response({'results': results, 'cached': False}, status=status.HTTP_200_OK)


def _load_smart_dataframe(dataset):
    """Resolve + load the Step-6 'smart' parquet for a dataset.

    Returns (df, None) on success or (None, Response) on failure.
    """
    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    step6 = global_context.get('step6') or {}
    smart_path = step6.get('output_path')
    if not smart_path:
        processed_path = dataset.get('processed_path', '')
        base = processed_path.rsplit('.', 1)[0] if processed_path else ''
        smart_path = base + '_smart.parquet' if base else None
    if not smart_path:
        return None, Response({'error': 'Processed file not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, smart_path)
        df = pd.read_parquet(io.BytesIO(file_bytes))
        return df, None
    except Exception as e:
        logger.error('_load_smart_dataframe: failed to load parquet %s: %s', smart_path, e)
        return None, Response({'error': 'Could not load dataset file', 'detail': str(e)},
                              status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _auto_time_grain(parsed):
    """Pick a reasonable time bucket for a datetime Series based on its span.

    Used when the caller asks for a date x-axis without specifying an interval,
    so "X over time" charts render chronologically at a sensible granularity
    instead of as one category per raw timestamp.
    """
    valid = parsed.dropna()
    if len(valid) < 2:
        return 'day'
    try:
        span_days = int((valid.max() - valid.min()).days)
    except Exception:
        return 'day'
    if span_days <= 31:
        return 'day'
    if span_days <= 183:        # ~6 months
        return 'week'
    if span_days <= 1095:       # ~3 years
        return 'month'
    if span_days <= 3650:       # ~10 years
        return 'quarter'
    return 'year'


def _bucket_datetime(parsed, grain):
    """Map a datetime Series to sortable string bucket labels."""
    if grain == 'day':
        return parsed.dt.strftime('%Y-%m-%d')
    if grain == 'week':
        return parsed.dt.strftime('%G-W%V')
    if grain == 'month':
        return parsed.dt.strftime('%Y-%m')
    if grain == 'quarter':
        return parsed.dt.year.astype(int).astype(str) + '-Q' + parsed.dt.quarter.astype(int).astype(str)
    if grain == 'year':
        return parsed.dt.strftime('%Y')
    return parsed.dt.strftime('%Y-%m-%d')


def _apply_date_filter(frame, date_field, date_from, date_to):
    """Filter rows of `frame` whose `date_field` falls within [date_from, date_to].

    `date_to` is treated as an inclusive day (the whole end day is kept).
    Returns the original frame on any parsing failure.
    """
    try:
        dts = pd.to_datetime(frame[date_field], errors='coerce')
        mask = dts.notna()
        if date_from:
            from_ts = pd.to_datetime(date_from, errors='coerce')
            if pd.notna(from_ts):
                mask &= dts >= from_ts
        if date_to:
            to_ts = pd.to_datetime(date_to, errors='coerce')
            if pd.notna(to_ts):
                mask &= dts <= (to_ts + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        return frame[mask]
    except Exception:
        return frame


def _scalar_agg(series, agg):
    """Aggregate a Series down to a single number for KPI cards."""
    if agg == 'count':
        return int(series.notna().sum())
    s = pd.to_numeric(series, errors='coerce').dropna()
    if len(s) == 0:
        return 0.0
    if agg in ('avg', 'mean', 'average'):
        return float(s.mean())
    if agg == 'min':
        return float(s.min())
    if agg == 'max':
        return float(s.max())
    if agg == 'median':
        return float(s.median())
    return float(s.sum())


@api_view(['POST'])
def customize_chart_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/customize-chart/

    Interactive (Power BI / Tableau style) chart aggregation. Re-aggregates the
    dataset on the fly from chosen options and returns single- or multi-series data.

    Body: {
      chart_type, x_axis, y_axis, agg, breakdown, time_grain, top_n, sort
    }
    Response (single): { mode:'single', x_label, y_label, data:[{label,value}] }
    Response (multi):  { mode:'multi',  x_label, y_label, series:[{key,label}],
                         data:[{label, <seriesKey>:value, ...}] }
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    df, err = _load_smart_dataframe(dataset)
    if err:
        return err

    body = request.data if isinstance(request.data, dict) else {}
    chart_type = str(body.get('chart_type', 'bar')).lower().replace('-', '_')
    x_axis = body.get('x_axis')
    y_axis = body.get('y_axis')
    agg = str(body.get('agg', 'sum')).lower()
    breakdown = body.get('breakdown') or None
    time_grain = (str(body.get('time_grain') or '').lower()) or None
    sort = str(body.get('sort', 'value_desc')).lower()
    cumulative = bool(body.get('cumulative'))
    date_field = body.get('date_field') or None
    date_from = body.get('date_from') or None
    date_to = body.get('date_to') or None
    try:
        top_n = int(body.get('top_n') or 12)
    except (TypeError, ValueError):
        top_n = 12
    top_n = max(1, min(top_n, 50))

    cols = set(df.columns)
    # Correct swapped axes for horizontal bars (category on x, measure on y) before
    # any validation nulls a mismatched measure.
    if chart_type == 'horizontal_bar':
        x_axis, y_axis = _corrected_bar_axes(df, x_axis, y_axis)
    if date_field and date_field not in cols:
        date_field = None
    if y_axis and y_axis not in cols:
        y_axis = None
    if breakdown and breakdown not in cols:
        breakdown = None

    # Apply the (optional) date-range filter once for every branch.
    base = df
    if date_field and (date_from or date_to):
        base = _apply_date_filter(df, date_field, date_from, date_to)

    # ── KPI / scalar mode (no x_axis) ──
    if chart_type == 'kpi':
        if not y_axis:
            return Response({'mode': 'scalar', 'value': 0.0}, status=status.HTTP_200_OK)
        try:
            value = _scalar_agg(base[y_axis], agg)
        except Exception as e:
            logger.warning('customize_chart_view: kpi failed: %s', e)
            value = 0.0
        return Response({'mode': 'scalar', 'value': float(value)}, status=status.HTTP_200_OK)

    if not x_axis or x_axis not in cols:
        return Response({'mode': 'single', 'x_label': '', 'y_label': '', 'data': []},
                        status=status.HTTP_200_OK)

    PART_TO_WHOLE = {'pie', 'donut', 'treemap', 'funnel'}
    AGG_MAP = {'sum': 'sum', 'avg': 'mean', 'mean': 'mean', 'average': 'mean',
               'min': 'min', 'max': 'max', 'median': 'median', 'count': 'size'}
    pdagg = AGG_MAP.get(agg, 'sum')
    use_count = (pdagg == 'size') or not y_axis

    MAX_POINTS = 750

    try:
        work = base.copy()

        # Build the x bucket (optional time bucketing for datetime columns)
        x_is_dt = False
        if time_grain:
            parsed = pd.to_datetime(work[x_axis], errors='coerce')
            if parsed.notna().mean() > 0.5:
                x_is_dt = True
                grain = time_grain
                mask = parsed.notna()
                work = work[mask].copy()
                parsed = parsed[mask]
                work['__x'] = _bucket_datetime(parsed, grain).values
            else:
                work['__x'] = work[x_axis].astype(str)
        else:
            # No explicit interval: auto-detect a datetime x-axis so time-series
            # ("X over time") render chronologically at a sensible grain instead
            # of one scrambled, Top-N-truncated category per raw timestamp.
            # Guard against numeric columns (years/IDs) being mis-read as dates.
            col = work[x_axis]
            parsed = None
            if pd.api.types.is_datetime64_any_dtype(col):
                parsed = pd.to_datetime(col, errors='coerce')
            elif col.dtype == object:
                maybe = pd.to_datetime(col, errors='coerce')
                if maybe.notna().mean() > 0.7:
                    parsed = maybe
            if parsed is not None and parsed.notna().mean() > 0.7:
                x_is_dt = True
                grain = _auto_time_grain(parsed)
                mask = parsed.notna()
                work = work[mask].copy()
                parsed = parsed[mask]
                work['__x'] = _bucket_datetime(parsed, grain).values
            else:
                work['__x'] = work[x_axis].astype(str)

        if not use_count:
            work['__y'] = pd.to_numeric(work[y_axis], errors='coerce')

        y_label = 'Count' if use_count else str(y_axis)

        # ── Multi-series (breakdown) ──
        if breakdown and chart_type not in PART_TO_WHOLE:
            work['__series'] = work[breakdown].astype(str)
            if use_count:
                g = work.groupby(['__x', '__series']).size().reset_index(name='__v')
            else:
                g = (work.groupby(['__x', '__series'])['__y'].agg(pdagg)
                     .reset_index().rename(columns={'__y': '__v'}))
            g['__v'] = pd.to_numeric(g['__v'], errors='coerce').fillna(0.0)

            totals = g.groupby('__series')['__v'].sum().sort_values(ascending=False)
            keep = [str(k) for k in totals.head(top_n).index]
            g = g[g['__series'].isin(keep)]

            pivot = g.pivot_table(index='__x', columns='__series', values='__v',
                                  aggfunc='sum', fill_value=0.0)
            if x_is_dt or cumulative:
                pivot = pivot.sort_index()
            else:
                pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]
            if len(pivot) > MAX_POINTS:
                pivot = pivot.tail(MAX_POINTS) if (x_is_dt or cumulative) else pivot.head(MAX_POINTS)
            if cumulative:
                pivot = pivot.cumsum()

            rows = []
            for idx, r in pivot.iterrows():
                row = {'label': str(idx)}
                for k in keep:
                    row[k] = float(r.get(k, 0.0) or 0.0)
                rows.append(row)
            series = [{'key': k, 'label': k} for k in keep]
            return Response({'mode': 'multi', 'x_label': str(x_axis), 'y_label': y_label,
                             'series': series, 'data': rows}, status=status.HTTP_200_OK)

        # ── Single series ──
        if use_count:
            g = work.groupby('__x').size().reset_index(name='value')
        else:
            g = work.groupby('__x')['__y'].agg(pdagg).reset_index().rename(columns={'__y': 'value'})
        g['value'] = pd.to_numeric(g['value'], errors='coerce').fillna(0.0)
        g = g.rename(columns={'__x': 'label'})
        g['label'] = g['label'].astype(str)

        if x_is_dt or cumulative:
            # Chronological / label order so a running total rises monotonically.
            g = g.sort_values('label')
        elif sort == 'value_asc':
            g = g.sort_values('value')
        elif sort == 'label_asc':
            g = g.sort_values('label')
        elif sort == 'label_desc':
            g = g.sort_values('label', ascending=False)
        else:
            g = g.sort_values('value', ascending=False)

        # Cumulative needs every bucket to actually reach the all-time total.
        limit = MAX_POINTS if (x_is_dt or cumulative) else top_n
        g = g.head(limit)

        if cumulative:
            g['value'] = pd.to_numeric(g['value'], errors='coerce').fillna(0.0).cumsum()

        if chart_type in PART_TO_WHOLE:
            data = [{'name': str(r['label']), 'value': float(r['value'])} for _, r in g.iterrows()]
        else:
            data = [{'label': str(r['label']), 'value': float(r['value'])} for _, r in g.iterrows()]
        return Response({'mode': 'single', 'x_label': str(x_axis), 'y_label': y_label,
                         'data': data}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.warning('customize_chart_view: failed: %s', e)
        return Response({'error': 'Could not aggregate chart', 'detail': str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_dataset_rows_view(request, dataset_id):
    """
    GET /api/datasets/<dataset_id>/rows/?limit=500&offset=0

    Returns paginated dataset_rows for the authenticated owner.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN
        )

    try:
        limit = int(request.query_params.get('limit', 500))
        offset = int(request.query_params.get('offset', 0))
    except (TypeError, ValueError):
        return Response(
            {'error': 'Invalid pagination parameters'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Keep payloads bounded for frontend and API stability.
    limit = max(1, min(limit, 2000))
    offset = max(0, offset)

    rows = get_dataset_rows(dataset_id, limit=limit, offset=offset)

    # dataset_rows stores only a bounded display sample (DATASET_ROWS_DISPLAY_CAP).
    # `stored_rows` = how many rows are actually browsable here; pagination is bounded
    # by it. `total_rows` = the TRUE dataset size (file_info), used only for the
    # "showing N of M" note. `is_sampled` is set when the table is a sample.
    stored_rows = None
    total_rows = None
    global_context = _parse_json_if_string(dataset.get('global_context'))
    if isinstance(global_context, dict):
        storage = global_context.get('storage')
        if isinstance(storage, dict):
            try:
                parsed = int(storage.get('row_count'))
                stored_rows = parsed if parsed >= 0 else None
            except (TypeError, ValueError):
                stored_rows = None
            try:
                parsed = int(storage.get('total_rows'))
                total_rows = parsed if parsed >= 0 else None
            except (TypeError, ValueError):
                total_rows = None
    # Fallback: true total from file_info if not in storage
    file_info = _parse_json_if_string(dataset.get('file_info')) or {}
    if total_rows is None and isinstance(file_info, dict):
        try:
            parsed = int(file_info.get('row_count', 0))
            total_rows = parsed if parsed > 0 else None
        except (TypeError, ValueError):
            total_rows = None
    if total_rows is None:
        total_rows = stored_rows

    # Pagination bound: you can only page through what's stored.
    if stored_rows is not None:
        has_more = offset + len(rows) < stored_rows
    else:
        has_more = len(rows) >= limit

    next_offset = offset + len(rows) if has_more else None
    is_sampled = bool(
        stored_rows is not None and total_rows is not None and total_rows > stored_rows
    )

    return Response(
        {
            'dataset_id': dataset_id,
            'limit': limit,
            'offset': offset,
            'rows_count': len(rows),
            'stored_rows': stored_rows,
            'total_rows': total_rows,
            'is_sampled': is_sampled,
            'has_more': has_more,
            'next_offset': next_offset,
            'rows': rows,
        },
        status=status.HTTP_200_OK
    )


@api_view(['GET'])
def get_feature_recommendations_view(request, dataset_id):
    """
    GET /api/datasets/<dataset_id>/feature-recommendations/?target=<col>&time=<col>

    Returns correlation-ranked feature columns for the forecasting UI:
      { "recommendations": [{feature, score, method}], "reason": "" }
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'},
                        status=status.HTTP_404_NOT_FOUND)

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN,
        )

    target_column = str(request.query_params.get('target', '')).strip()
    time_column = str(request.query_params.get('time', '')).strip() or None
    if not target_column:
        return Response({'error': "Query param 'target' is required"},
                        status=status.HTTP_400_BAD_REQUEST)

    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    step6_context = global_context.get('step6') if isinstance(global_context, dict) else {}
    source_path = step6_context.get('output_path') if isinstance(step6_context, dict) else None
    if not source_path:
        source_path = dataset.get('processed_path')
    if not source_path:
        return Response({'recommendations': [], 'reason': 'No processed artifact found.'},
                        status=status.HTTP_200_OK)

    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, source_path)
        ext = os.path.splitext(str(source_path).lower())[1]
        if ext == '.parquet':
            df = pd.read_parquet(io.BytesIO(file_bytes))
        elif ext == '.csv':
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception as e:
        logger.error('feature-recommendations: failed to load %s: %s', source_path, e)
        return Response({'recommendations': [], 'reason': 'Could not load dataset artifact.'},
                        status=status.HTTP_200_OK)

    from api.forecasting.feature_recommender import recommend_features
    result = recommend_features(df, target_column=target_column, time_column=time_column)
    return Response(result, status=status.HTTP_200_OK)


@api_view(['POST'])
def forecast_dataset_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/forecast/

    Body:
    {
      "time_column": "order_date",
      "target_column": "revenue",
      "id_columns": [],
      "feature_columns": ["discount", "region"],
      "frequency": "D",
      "horizon": 30,
      "candidate_models": ["naive", "seasonal_naive", "sarimax", "catboost"]
    }
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN
        )

    body = request.data if isinstance(request.data, dict) else {}
    time_column = str(body.get('time_column', '')).strip()
    target_column = str(body.get('target_column', '')).strip()
    if not time_column or not target_column:
        return Response(
            {'error': 'time_column and target_column are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    id_columns = body.get('id_columns', []) or []
    feature_columns = body.get('feature_columns', []) or []
    candidate_models = body.get('candidate_models')
    frequency = body.get('frequency')
    mode = str(body.get('mode', 'fast')).strip().lower()
    if mode not in ('fast', 'accurate'):
        mode = 'fast'
    missing_periods_policy = body.get('missing_periods_policy', 'drop')
    try:
        horizon = int(body.get('horizon', 30))
    except (TypeError, ValueError):
        return Response(
            {'error': 'horizon must be an integer'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not isinstance(id_columns, list) or not isinstance(feature_columns, list):
        return Response(
            {'error': 'id_columns and feature_columns must be arrays'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if candidate_models is not None and not isinstance(candidate_models, list):
        return Response(
            {'error': 'candidate_models must be an array when provided'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if missing_periods_policy is not None and not isinstance(missing_periods_policy, str):
        return Response(
            {'error': 'missing_periods_policy must be a string: "drop" or "zero"'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if isinstance(missing_periods_policy, str) and missing_periods_policy.lower() not in {'drop', 'zero'}:
        return Response(
            {'error': 'missing_periods_policy must be either "drop" or "zero"'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Prefer Step 6 smart artifact if present, fallback to Step 3 processed_path.
    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    step6_context = global_context.get('step6') if isinstance(global_context, dict) else {}
    source_path = step6_context.get('output_path') if isinstance(step6_context, dict) else None
    if not source_path:
        source_path = dataset.get('processed_path')

    if not source_path:
        return Response(
            {'error': 'No processed artifact found for forecasting. Run pipeline first.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ── Accurate mode → run async on the dedicated Celery queue ──────────────
    # All models, higher point cap; can take minutes, so we queue it and let the
    # frontend poll GET /api/forecasts/status/<job_id>/ instead of blocking here.
    if mode == 'accurate':
        remaining = _check_and_set_cooldown(user_id)
        if remaining > 0:
            return Response(
                {
                    'error': 'Too many requests',
                    'message': f'Please wait {int(remaining) + 1}s before running another forecast.',
                    'retry_after_seconds': int(remaining) + 1,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        from .tasks import run_forecast_task
        task = run_forecast_task.delay(
            dataset_id=dataset_id,
            user_id=user_id,
            source_path=source_path,
            time_column=time_column,
            target_column=target_column,
            id_columns=id_columns,
            feature_columns=feature_columns,
            frequency=frequency,
            horizon=horizon,
            candidate_models=candidate_models,
            missing_periods_policy=missing_periods_policy,
            mode=mode,
        )
        logger.info("Async forecast queued: dataset=%s user=%s job=%s", dataset_id, user_id, task.id)
        return Response(
            {'status': 'queued', 'job_id': task.id, 'mode': 'accurate'},
            status=status.HTTP_202_ACCEPTED,
        )

    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, source_path)
        ext = os.path.splitext(str(source_path).lower())[1]
        if ext == '.parquet':
            df = pd.read_parquet(io.BytesIO(file_bytes))
        elif ext == '.csv':
            df = pd.read_csv(io.BytesIO(file_bytes))
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            return Response(
                {'error': f'Unsupported artifact extension for forecasting: {ext or "unknown"}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Raw file bytes are no longer needed once parsed into a DataFrame.
        # Free them so large uploads don't keep ~2x the data in memory.
        del file_bytes
    except Exception as e:
        return Response(
            {'error': 'Failed to load processed dataset artifact', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ── Rate limit ────────────────────────────────────────────────────────
    remaining = _check_and_set_cooldown(user_id)
    if remaining > 0:
        return Response(
            {
                'error': 'Too many requests',
                'message': f'Please wait {int(remaining) + 1}s before running another forecast.',
                'retry_after_seconds': int(remaining) + 1,
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    logger.info(
        "Forecast request: dataset=%s user=%s target=%s horizon=%d",
        dataset_id, user_id, target_column, horizon,
    )

    error_message = None
    result = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_forecast_service,
                df=df,
                time_column=time_column,
                target_column=target_column,
                id_columns=id_columns,
                feature_columns=feature_columns,
                frequency=frequency,
                horizon=horizon,
                candidate_models=candidate_models,
                missing_periods_policy=missing_periods_policy,
                mode=mode,
            )
            try:
                result = future.result(timeout=FORECAST_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                error_message = (
                    f'Forecast timed out after {FORECAST_TIMEOUT_S}s. '
                    'Try a shorter horizon or fewer feature columns.'
                )
                logger.error("Forecast timed out: dataset=%s user=%s", dataset_id, user_id)
    except Exception as e:
        error_message = str(e)
        logger.error("Forecast failed: dataset=%s error=%s", dataset_id, error_message)

    if error_message:
        # Log the failed attempt for history visibility
        _persist_forecast_log(
            dataset_id=dataset_id,
            user_id=user_id,
            time_column=time_column,
            target_column=target_column,
            feature_columns=feature_columns,
            frequency_hint=frequency,
            horizon=horizon,
            missing_policy=missing_periods_policy,
            input_rows=len(df),
            candidate_models=candidate_models,
            result=None,
            error_message=error_message,
        )
        is_timeout = 'timed out' in error_message.lower()
        return Response(
            {'error': 'Forecast timed out' if is_timeout else 'Forecasting failed', 'message': error_message},
            status=status.HTTP_504_GATEWAY_TIMEOUT if is_timeout else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Persist forecast result for history (includes forecast_data for accuracy view)
    forecast_config = {
        'time_column': time_column,
        'target_column': target_column,
        'feature_columns': feature_columns,
        'frequency': result.get('frequency'),
        'horizon': horizon,
    }
    try:
        saved = insert_forecast_result(dataset_id, user_id, forecast_config, result)
        forecast_id = saved.get('id')
    except Exception as _fe:
        logger.warning("Failed to persist forecast result: %s", _fe, exc_info=True)
        forecast_id = None

    logger.info(
        "Forecast complete: dataset=%s best=%s mae=%s duration=%sms",
        dataset_id, result.get("best_model"), result.get("metrics", {}).get("mae"),
        result.get("duration_ms"),
    )

    # Auto-refresh recommendations with new forecast data
    try:
        run_recommendations_service(dataset_id, force=True)
    except Exception as _re:
        logger.warning("Auto-refresh recommendations after forecast failed: %s", _re)

    return Response(
        {
            'dataset_id': dataset_id,
            'source_path': source_path,
            'forecast_id': forecast_id,
            **result,
        },
        status=status.HTTP_200_OK
    )


@api_view(['GET'])
def get_forecast_history_view(request, dataset_id):
    """GET /api/datasets/<dataset_id>/forecasts/ — list recent forecasts."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    forecasts = get_forecast_results(dataset_id, limit=20)
    return Response({'dataset_id': dataset_id, 'forecasts': forecasts}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_forecast_detail_view(request, forecast_id):
    """GET /api/forecasts/<forecast_id>/ — get full forecast result."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    record = get_forecast_result_by_id(forecast_id)
    if not record:
        return Response({'error': 'Forecast not found'}, status=status.HTTP_404_NOT_FOUND)

    dataset = get_dataset(record.get('dataset_id', ''))
    if not dataset or str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    return Response(record, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_forecast_status_view(request, job_id):
    """
    GET /api/forecasts/status/<job_id>/

    Poll an async ("accurate" mode) forecast queued on the Celery `forecasts` queue.
    Returns {status: pending|completed|failed}. On completion the full forecast record
    (persisted to forecast_logs) is returned under `forecast`.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    from celery.result import AsyncResult
    from core.celery import app as celery_app

    res = AsyncResult(job_id, app=celery_app)
    state = res.state

    if state in ('PENDING', 'RECEIVED', 'STARTED', 'RETRY'):
        return Response({'status': 'pending', 'state': state}, status=status.HTTP_200_OK)

    if state == 'FAILURE':
        return Response(
            {'status': 'failed', 'error': str(res.result)},
            status=status.HTTP_200_OK,
        )

    if state == 'SUCCESS':
        payload = res.result if isinstance(res.result, dict) else {}
        if payload.get('status') == 'failed':
            return Response(
                {'status': 'failed', 'error': payload.get('error')},
                status=status.HTTP_200_OK,
            )
        # Ownership check via the persisted log record (forecast_logs has user_id).
        forecast_log_id = payload.get('forecast_log_id')
        record = get_forecast_result_by_id(forecast_log_id) if forecast_log_id else None
        if record and str(record.get('user_id', '')) != str(user_id):
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        # Hand back the full service-shape result so the UI renders it like the sync path.
        return Response(
            {
                'status': 'completed',
                'forecast': payload.get('result'),
                'forecast_log_id': forecast_log_id,
            },
            status=status.HTTP_200_OK,
        )

    return Response({'status': state.lower()}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_kpi_stats_view(request):
    """GET /api/dashboard/stats/ — aggregated KPI stats for the current user."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error
    try:
        stats = get_user_kpi_stats(user_id)
    except Exception as e:
        logger.error("KPI stats failed: user=%s error=%s", user_id, e)
        return Response({'error': 'Failed to load stats', 'message': str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(stats, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_forecast_accuracy_view(request, forecast_id):
    """
    GET /api/forecasts/<forecast_id>/accuracy/

    Returns stored test-holdout comparison (predicted vs actual) for a past forecast.
    The data comes from the test_comparison stored inside forecast_data JSONB at run time.
    """
    import math

    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    try:
        record = get_forecast_result_by_id(forecast_id)
    except Exception as exc:
        logger.error("get_forecast_accuracy_view: DB error for %s: %s", forecast_id, exc)
        return Response(
            {'error': 'Database error retrieving forecast', 'message': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not record:
        return Response({'error': 'Forecast not found'}, status=status.HTTP_404_NOT_FOUND)

    dataset = get_dataset(record.get('dataset_id', ''))
    if not dataset or str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    # forecast_data is a JSONB column — may be None for rows saved before the
    # column was added, or for old logs that used insert_forecast_log without it.
    raw_fd = record.get('forecast_data')
    forecast_data = _parse_json_if_string(raw_fd) if raw_fd is not None else {}
    test_comparison = (forecast_data or {}).get('test_comparison') or []

    if not test_comparison:
        return Response({
            'forecast_id': forecast_id,
            'dataset_id': record.get('dataset_id'),
            'message': (
                'No test comparison data stored for this forecast. '
                'Re-run the forecast to populate it — accuracy data is saved from the next run onwards.'
            ),
            'comparison': [],
            'aggregate': None,
        }, status=status.HTTP_200_OK)

    # Compute aggregate metrics from the stored holdout comparison
    try:
        actuals   = [float(r['actual'])    for r in test_comparison if r.get('actual')    is not None]
        predicted = [float(r['predicted']) for r in test_comparison if r.get('predicted') is not None]
        n = min(len(actuals), len(predicted))
        if n > 0:
            errors = [abs(actuals[i] - predicted[i]) for i in range(n)]
            mae    = round(sum(errors) / n, 4)
            rmse   = round(math.sqrt(sum(e ** 2 for e in errors) / n), 4)
            total  = sum(abs(a) for a in actuals[:n])
            mape_vals = [
                abs(actuals[i] - predicted[i]) / abs(actuals[i]) * 100
                for i in range(n) if abs(actuals[i]) > 1e-9
            ]
            mape = round(sum(mape_vals) / len(mape_vals), 2) if mape_vals else None
            aggregate = {
                'n_matched': n,
                'mae':  mae,
                'rmse': rmse,
                'mape': mape,
                'wape': round(sum(errors) / total, 4) if total > 1e-9 else None,
                'best_model': record.get('best_model'),
            }
        else:
            aggregate = None
    except Exception as exc:
        logger.error("get_forecast_accuracy_view: metric computation failed: %s", exc)
        aggregate = None

    # Enrich each comparison row with per-row abs_error and pct_error
    enriched = []
    for row in test_comparison:
        actual = row.get('actual')
        pred   = row.get('predicted')
        abs_err = None
        pct_err = None
        if actual is not None and pred is not None:
            try:
                abs_err = round(abs(float(actual) - float(pred)), 4)
                if abs(float(actual)) > 1e-9:
                    pct_err = round(abs(float(actual) - float(pred)) / abs(float(actual)) * 100, 2)
            except Exception:
                pass
        enriched.append({**row, 'abs_error': abs_err, 'pct_error': pct_err})

    return Response({
        'forecast_id': forecast_id,
        'dataset_id':  record.get('dataset_id'),
        'best_model':  record.get('best_model'),
        'comparison':  enriched,
        'aggregate':   aggregate,
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_forecast_view(request, forecast_id):
    """DELETE /api/forecasts/<forecast_id>/delete/"""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    record = get_forecast_result_by_id(forecast_id)
    if not record:
        return Response({'error': 'Forecast not found'}, status=status.HTTP_404_NOT_FOUND)

    if str(record.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    ok = delete_forecast_log(forecast_id)
    if not ok:
        return Response(
            {'error': 'Failed to delete forecast'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({'deleted': forecast_id}, status=status.HTTP_200_OK)


@api_view(['POST'])
def process_file_step_4(request, dataset_id):
    """
    POST /api/process/{dataset_id}/

    Step 4: Technical Column Profiling.

    1. Authenticate user and verify dataset ownership
    2. Download the cleaned file from cleaned_data bucket
    3. Run technical profiling (pandas - no AI)
    4. Insert columns_metadata rows
    5. Update datasets.file_info
    6. Update tracking_jobs progress
    7. Return summary
    """
    # ══════════════════════════════════════════
    # AUTHENTICATE USER
    # ══════════════════════════════════════════
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    # ══════════════════════════════════════════
    # FETCH DATASET AND VERIFY OWNERSHIP
    # ══════════════════════════════════════════
    dataset = get_dataset(dataset_id)

    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN
        )

    # ══════════════════════════════════════════
    # CHECK THAT STEP 3 HAS RUN (processed_path exists)
    # ══════════════════════════════════════════
    processed_path = dataset.get('processed_path')
    if not processed_path:
        return Response(
            {
                'error': 'Step 3 has not run yet',
                'message': 'The file must be cleaned before profiling. '
                           'datasets.processed_path is empty.',
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    # ══════════════════════════════════════════
    # UPDATE TRACKING: Step 4 starting
    # ══════════════════════════════════════════
    try:
        update_tracking_job(
            dataset_id=dataset_id,
            step=4,
            message='Step 4: Downloading cleaned file...',
        )
    except Exception:
        pass  # Non-critical — continue even if tracking update fails

    # ══════════════════════════════════════════
    # DOWNLOAD CLEANED FILE FROM BUCKET
    # ══════════════════════════════════════════
    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, processed_path)
    except Exception as e:
        _fail_tracking(dataset_id, f'Failed to download cleaned file: {e}')
        return Response(
            {'error': 'Failed to download cleaned file', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ══════════════════════════════════════════
    # UPDATE TRACKING: Profiling in progress
    # ══════════════════════════════════════════
    try:
        update_tracking_job(
            dataset_id=dataset_id,
            step=4,
            message='Step 4: Profiling columns...',
        )
    except Exception:
        pass

    # ══════════════════════════════════════════
    # RUN STEP 4 PROCESSING (pure pandas)
    # ══════════════════════════════════════════
    try:
        result = run_step4(file_bytes, processed_path)
    except Exception as e:
        _fail_tracking(dataset_id, f'Step 4 profiling failed: {e}')
        return Response(
            {'error': 'Step 4 profiling failed', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ══════════════════════════════════════════
    # STORE RESULTS IN SUPABASE
    # ══════════════════════════════════════════
    try:
        # 1. Insert columns_metadata rows
        update_tracking_job(
            dataset_id=dataset_id,
            step=4,
            message='Step 4: Saving column metadata...',
        )
        delete_columns_metadata(dataset_id)
        insert_columns_metadata(dataset_id, result['columns'])

        # 2. Update datasets.file_info
        update_dataset(dataset_id, {'file_info': result['file_info']})

        # 3. Mark Step 4 as done in tracking
        update_tracking_job(
            dataset_id=dataset_id,
            step=4,
            message='Step 4 completed: Technical profiling done.',
            status='completed',
        )

    except Exception as e:
        _fail_tracking(dataset_id, f'Failed to save Step 4 results: {e}')
        return Response(
            {'error': 'Failed to save profiling results', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ══════════════════════════════════════════
    # RETURN SUCCESS RESPONSE
    # ══════════════════════════════════════════
    return Response(
        {
            'dataset_id': dataset_id,
            'status': 'step4_done',
            'file_info': result['file_info'],
            'columns_count': len(result['columns']),
            'message': (
                f'Technical profiling complete. '
                f'{len(result["columns"])} columns detected.'
            ),
        },
        status=status.HTTP_200_OK
    )


@api_view(['POST'])
def export_pdf(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/export-pdf/

    Generate a premium PDF report with cover page, KPI cards, embedded
    chart images (via matplotlib), and the AI narrative from Step 8.
    """
    from django.http import HttpResponse
    from .pdf_charts import render_chart_from_agg

    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN,
        )

    global_context = _parse_json_if_string(dataset.get('global_context'))
    if not isinstance(global_context, dict):
        return Response(
            {'error': 'No report available. Pipeline must complete first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    step8 = global_context.get('step8')
    if not isinstance(step8, dict) or not step8.get('sections'):
        return Response(
            {'error': 'No AI report found. Pipeline Step 8 must complete first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    step7 = global_context.get('step7') or {}
    suggested_title = step7.get('suggested_title', '') if isinstance(step7, dict) else ''
    suggested_charts = step7.get('suggested_charts', []) if isinstance(step7, dict) else []

    department = step8.get('department', 'Business')
    generated_at = step8.get('generated_at', datetime.now().isoformat())
    title = suggested_title or f'{department} Report'

    # ── Format date ──
    try:
        generated_dt = datetime.fromisoformat(generated_at.replace('Z', '+00:00'))
        formatted_date = generated_dt.strftime('%B %d, %Y at %H:%M UTC')
    except (ValueError, AttributeError):
        formatted_date = generated_at

    # ── Dataset metadata ──
    file_info = _parse_json_if_string(dataset.get('file_info'))
    file_name = dataset.get('file_name', 'Unknown')
    row_count = 'N/A'
    column_count = 'N/A'
    if isinstance(file_info, dict):
        row_count = str(file_info.get('row_count', 'N/A'))
        column_count = str(file_info.get('column_count', 'N/A'))

    columns_meta = get_columns_metadata(dataset_id) or []

    segmentation = global_context.get('segmentation')
    if not isinstance(segmentation, dict):
        segmentation = None

    # ── Chart data: full parquet aggregation (matches Report page) ──
    cached_charts = _match_cached_charts(suggested_charts, global_context.get('chart_cache') or {})
    if cached_charts is not None:
        agg_results = cached_charts
    else:
        df, _load_err = _load_smart_dataframe(dataset)
        if df is not None:
            agg_results = aggregate_charts_from_df(df, suggested_charts)
        else:
            agg_results = []

    if len(agg_results) < len(suggested_charts):
        agg_results = list(agg_results) + [{}] * (len(suggested_charts) - len(agg_results))

    kpi_cards: list[dict] = []
    non_kpi_chart_items: list[dict] = []
    for spec, agg in zip(suggested_charts, agg_results):
        if spec.get('chart_type') == 'kpi_card':
            data = (agg or {}).get('data') or []
            raw_val = data[0].get('value') if data else None
            if raw_val is not None:
                kpi_cards.append({
                    'title': spec.get('title', '') or _prettify_column(spec.get('y_axis') or ''),
                    'metric': _prettify_column(spec.get('y_axis') or ''),
                    'value': _format_pdf_number(raw_val),
                })
            continue
        b64 = render_chart_from_agg(spec, agg or {})
        if b64:
            non_kpi_chart_items.append({
                'title': spec.get('title', ''),
                'base64': b64,
            })

    # ── Forecast band: latest saved forecast for this dataset (best-effort) ──
    forecast_summary = None
    forecast_b64 = None
    try:
        hist = get_forecast_results(dataset_id, limit=1)
        if hist:
            row = hist[0]
            detail = get_forecast_result_by_id(row.get('id'))
            fd = _parse_json_if_string((detail or {}).get('forecast_data'))
            if isinstance(fd, dict) and fd.get('forecast'):
                from .pdf_charts import render_forecast_chart
                forecast_b64 = render_forecast_chart(fd)
                wape = row.get('best_wape')
                accuracy = max(0.0, 100.0 - float(wape) * 100.0) if wape is not None else None
                forecast_summary = {
                    'target': row.get('target_column'),
                    'best_model': row.get('best_model'),
                    'accuracy': accuracy,
                    'horizon': row.get('horizon'),
                }
    except Exception as _fe:
        logger.warning('PDF forecast band skipped: %s', _fe)

    # ── Column-relationships band: numeric correlation heatmap (best-effort) ──
    correlation_b64 = None
    try:
        corr_df, _cerr = _load_smart_dataframe(dataset)
        if corr_df is not None:
            from .pdf_charts import render_correlation_heatmap
            correlation_b64 = render_correlation_heatmap(corr_df)
    except Exception as _ce:
        logger.warning('PDF correlation band skipped: %s', _ce)

    # ── Build modern PDF HTML ──
    from .pdf_report import build_pdf_html
    full_html = build_pdf_html(
        title=title,
        department=department,
        formatted_date=formatted_date,
        file_name=file_name,
        row_count=row_count,
        column_count=column_count,
        kpi_cards=kpi_cards,
        non_kpi_chart_items=non_kpi_chart_items,
        sections=step8.get('sections', []),
        columns_meta=columns_meta,
        segmentation=segmentation,
        forecast_summary=forecast_summary,
        forecast_b64=forecast_b64,
        correlation_b64=correlation_b64,
    )

    pdf_bytes = _html_to_pdf_bytes(full_html)

    if pdf_bytes is None:
        return Response(
            {'error': 'PDF generation failed',
             'message': 'PDF renderer could not produce the report. Check server logs.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
    filename = f'{safe_name}_{department}_Report.pdf'

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@api_view(['POST'])
def run_segmentation_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/segmentation/

    Run generic data segmentation on the dataset. Auto-detects the entity
    type and applies the best strategy (RFM / ABC / K-Means).
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN,
        )

    columns_metadata = get_columns_metadata(dataset_id)
    if not columns_metadata:
        return Response(
            {'error': 'No column metadata found. Pipeline must complete first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    step6_context = global_context.get('step6') if isinstance(global_context, dict) else {}
    source_path = step6_context.get('output_path') if isinstance(step6_context, dict) else None
    if not source_path:
        source_path = dataset.get('processed_path')

    if not source_path:
        return Response(
            {'error': 'No processed artifact found. Run pipeline first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, source_path)
        ext = os.path.splitext(str(source_path).lower())[1]
        if ext == '.parquet':
            df = pd.read_parquet(io.BytesIO(file_bytes))
        elif ext == '.csv':
            df = pd.read_csv(io.BytesIO(file_bytes))
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            return Response(
                {'error': f'Unsupported file type: {ext or "unknown"}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    except Exception as e:
        return Response(
            {'error': 'Failed to load dataset', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Use resolved_category (AI-detected or user-confirmed) when available
    cat_detection = global_context.get('category_detection') or {}
    category_hint = (
        cat_detection.get('resolved_category')
        or dataset.get('category_hint')
    )

    try:
        result = run_segmentation_service(df, columns_metadata, category_hint)
    except Exception as e:
        logger.error("Segmentation failed: dataset=%s error=%s", dataset_id, e)
        return Response(
            {'error': 'Segmentation failed', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Persist in global_context.segmentation
    if not isinstance(global_context, dict):
        global_context = {}
    global_context['segmentation'] = result
    update_dataset(dataset_id, {'global_context': global_context})

    # Auto-refresh recommendations with new segmentation data
    try:
        run_recommendations_service(dataset_id, force=True)
    except Exception as _re:
        logger.warning("Auto-refresh recommendations after segmentation failed: %s", _re)

    return Response(
        {'dataset_id': dataset_id, **result},
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
def get_segmentation_results_view(request, dataset_id):
    """
    GET /api/datasets/<dataset_id>/segmentation/results/

    Retrieve previously computed segmentation results from global_context.
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response(
            {'error': 'Dataset not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if str(dataset.get('user_id', '')) != str(user_id):
        return Response(
            {'error': 'Forbidden', 'message': 'You do not have access to this dataset'},
            status=status.HTTP_403_FORBIDDEN,
        )

    global_context = _parse_json_if_string(dataset.get('global_context'))
    if not isinstance(global_context, dict):
        return Response(
            {'error': 'No segmentation results found. Run segmentation first.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    segmentation = global_context.get('segmentation')
    if not segmentation:
        return Response(
            {'error': 'No segmentation results found. Run segmentation first.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(
        {'dataset_id': dataset_id, **segmentation},
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
def get_recommendations_view(request, dataset_id):
    """GET /api/datasets/<dataset_id>/recommendations/ — return cached blob or empty."""
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    global_context = _parse_json_if_string(dataset.get('global_context')) or {}
    blob = global_context.get('recommendations') or {
        'recommendations': [], 'signals': [], 'generated_at': None, 'snapshot_hash': None,
    }
    return Response(blob, status=status.HTTP_200_OK)


@api_view(['POST'])
def generate_recommendations_view(request, dataset_id):
    """
    POST /api/datasets/<dataset_id>/recommendations/generate/
    Body: { "force": true|false }  — force=true bypasses cache
    """
    user_id, auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    dataset = get_dataset(dataset_id)
    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get('user_id', '')) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    force = bool(request.data.get('force', False))
    try:
        blob = run_recommendations_service(dataset_id, force=force)
        return Response(blob, status=status.HTTP_200_OK)
    except Exception as exc:
        logger.error("generate_recommendations_view: %s", exc, exc_info=True)
        return Response(
            {'error': 'Recommendation generation failed', 'message': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _escape(text: str) -> str:
    """HTML-escape a string for safe template insertion."""
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _strip_markdown_bold(text: str) -> str:
    import re
    return re.sub(r'\*\*(.+?)\*\*', r'\1', str(text))


def _format_segment_insight(insight) -> str:
    if isinstance(insight, dict):
        title = str(insight.get('title') or '').strip()
        content = str(insight.get('content') or '').strip()
        if title and content:
            return f'{title}: {content}'
        return title or content
    return str(insight)


def _segment_key_metric(seg: dict) -> str:
    avg = seg.get('avg_metrics') or {}
    if isinstance(avg, str):
        avg = _parse_json_if_string(avg) or {}
    for key in ('monetary', 'total_value', 'revenue', 'value', 'value_share', 'score'):
        raw = avg.get(key) if isinstance(avg, dict) else None
        if raw is None:
            raw = seg.get(key)
        if raw is None:
            continue
        try:
            return f'{float(raw):,.2f}'
        except (TypeError, ValueError):
            return str(raw)
    return ''


def _format_pdf_number(value) -> str:
    from .pdf_charts import _compact_number
    try:
        return _compact_number(float(value))
    except (TypeError, ValueError):
        return str(value)


def _prettify_column(name: str) -> str:
    if not name:
        return 'Value'
    return ' '.join(t.capitalize() for t in str(name).replace('-', '_').split('_') if t)


def _html_to_pdf_bytes(html: str) -> bytes | None:
    """Render HTML to PDF — WeasyPrint first (Docker), xhtml2pdf fallback."""
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except Exception as exc:
        logger.warning('WeasyPrint PDF failed, trying xhtml2pdf: %s', exc)
    try:
        from xhtml2pdf import pisa
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(html, dest=pdf_buffer, encoding='utf-8')
        if not pisa_status.err:
            return pdf_buffer.getvalue()
        logger.error('xhtml2pdf reported errors during PDF generation')
    except ImportError:
        logger.error('Neither WeasyPrint nor xhtml2pdf is available')
    except Exception as exc:
        logger.error('PDF generation failed (xhtml2pdf): %s', exc)
    return None


def _fail_tracking(dataset_id: str, error_message: str):
    """Helper to mark the tracking job as failed."""
    try:
        update_tracking_job(
            dataset_id=dataset_id,
            step=4,
            message=error_message,
            status='failed',
        )
    except Exception:
        pass


def _persist_forecast_log(
    *,
    dataset_id: str,
    user_id: str,
    time_column: str,
    target_column: str,
    feature_columns: list,
    frequency_hint: str | None,
    horizon: int,
    missing_policy: str,
    input_rows: int,
    candidate_models: list | None,
    result: dict | None,
    error_message: str | None,
):
    """Save forecast run details to forecast_logs table for analysis."""
    metrics = (result or {}).get("metrics", {})
    log_entry = {
        "dataset_id": dataset_id,
        "user_id": user_id,
        "time_column": time_column,
        "target_column": target_column,
        "feature_columns": feature_columns or [],
        "frequency_hint": frequency_hint,
        "frequency_used": (result or {}).get("frequency"),
        "horizon": horizon,
        "missing_policy": missing_policy or "drop",
        "input_rows": input_rows,
        "prepared_rows": None,
        "season_length": None,
        "log_transformed": False,
        "non_negative": False,
        "candidate_models": candidate_models or [],
        "eligible_models": [],
        "skipped_models": (result or {}).get("skipped_models", []),
        "forecast_possible": (result or {}).get("forecast_possible", False),
        "model_results": (result or {}).get("model_results", []),
        "best_model": (result or {}).get("best_model"),
        "best_mae": metrics.get("mae"),
        "best_rmse": metrics.get("rmse"),
        "best_wape": metrics.get("wape"),
        "forecast_points": len((result or {}).get("forecast", [])),
        "duration_ms": (result or {}).get("duration_ms"),
        "error_message": error_message,
        "readiness_reasons": (result or {}).get("readiness", {}).get("reasons", []),
    }

    if result:
        mr = result.get("model_results", [])
        eligible = [m["model"] for m in mr]
        skipped = [s["model"] for s in result.get("skipped_models", [])]
        log_entry["eligible_models"] = eligible
        all_candidates = eligible + skipped
        if not log_entry["candidate_models"]:
            log_entry["candidate_models"] = all_candidates

    # Save forecast_data (test_comparison + forecast points) for the accuracy view.
    # The forecast_data JSONB column is optional — fall back gracefully if absent.
    if result:
        import json as _json
        forecast_data_payload = {
            "forecast":             result.get("forecast") or [],
            "prediction_intervals": result.get("prediction_intervals") or [],
            "test_comparison":      result.get("test_comparison") or [],
        }
        try:
            # Ensure the payload is plain-Python (no numpy/pandas types)
            forecast_data_payload = _json.loads(_json.dumps(forecast_data_payload, default=str))
        except Exception:
            forecast_data_payload = None

        if forecast_data_payload:
            try:
                return insert_forecast_log({**log_entry, "forecast_data": forecast_data_payload})
            except Exception:
                # forecast_data column may not exist yet — fall back to row without it
                try:
                    return insert_forecast_log(log_entry)
                except Exception as exc:
                    logger.warning("Failed to persist forecast log: %s", exc)
        else:
            try:
                return insert_forecast_log(log_entry)
            except Exception as exc:
                logger.warning("Failed to persist forecast log: %s", exc)
    else:
        try:
            return insert_forecast_log(log_entry)
        except Exception as exc:
            logger.warning("Failed to persist forecast log: %s", exc)
    return None


# ── Model-Category Stats endpoint ────────────────────────────────────────────

@api_view(['GET'])
def model_category_stats_view(request):
    """Aggregate forecast_logs by dataset category + winning model."""
    from .supabase_client import get_supabase_client

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    client = get_supabase_client()

    try:
        fc_resp = (
            client.table("forecast_logs")
            .select("id,dataset_id,best_model,best_wape")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        forecast_rows = fc_resp.data or []
    except Exception as e:
        return Response({'error': f'Failed to query forecast logs: {e}'}, status=500)

    ds_resp = (
        client.table("datasets")
        .select("id,global_context")
        .eq("user_id", user_id)
        .execute()
    )
    datasets = {d['id']: d for d in (ds_resp.data or [])}

    category_stats: dict = {}
    for row in forecast_rows:
        ds = datasets.get(row.get("dataset_id"))
        if not ds:
            continue
        gc = ds.get("global_context") or {}
        if isinstance(gc, str):
            import json as _j
            try:
                gc = _j.loads(gc)
            except Exception:
                gc = {}
        step8 = gc.get("step8") or {}
        cat_det = step8.get("category_detection") or {}
        category = cat_det.get("detected_category") or "Unknown"

        model = row.get("best_model") or "unknown"
        wape = row.get("best_wape")
        accuracy = round((1 - (wape or 0)) * 100, 1) if wape is not None else None

        if category not in category_stats:
            category_stats[category] = {"total_runs": 0, "models": {}}
        category_stats[category]["total_runs"] += 1

        if model not in category_stats[category]["models"]:
            category_stats[category]["models"][model] = {"wins": 0, "accuracies": []}
        category_stats[category]["models"][model]["wins"] += 1
        if accuracy is not None:
            category_stats[category]["models"][model]["accuracies"].append(accuracy)

    result = {}
    for cat, data in category_stats.items():
        models_ranked = []
        for model_name, model_data in data["models"].items():
            accs = model_data["accuracies"]
            avg_acc = round(sum(accs) / len(accs), 1) if accs else None
            models_ranked.append({
                "model": model_name,
                "wins": model_data["wins"],
                "avg_accuracy": avg_acc,
                "win_rate": round(model_data["wins"] / data["total_runs"] * 100, 1),
            })
        models_ranked.sort(key=lambda m: m["wins"], reverse=True)
        result[cat] = {"total_runs": data["total_runs"], "models": models_ranked}

    STATIC_RECOMMENDATIONS = {
        "Sales": {
            "models": ["CatBoost", "LightGBM", "ETS"],
            "justification": "Revenue/demand data has strong seasonality + nonlinear promotions; tree models capture exogenous effects like discounts and holidays.",
        },
        "Marketing": {
            "models": ["Prophet", "SARIMAX"],
            "justification": "Campaign data has trend changes + weekly seasonality; Prophet handles changepoints well; SARIMAX captures autocorrelation.",
        },
        "Operations": {
            "models": ["ETS", "Seasonal Naive"],
            "justification": "Operational metrics are often smooth and seasonal; simpler models avoid overfitting on repetitive patterns.",
        },
        "HR": {
            "models": ["ETS", "Naive"],
            "justification": "Workforce metrics are stable/slowly trending; complex models tend to overfit on smaller HR datasets.",
        },
        "Business": {
            "models": ["CatBoost", "Prophet"],
            "justification": "General business KPIs vary widely; tree models adapt to feature-rich data; Prophet handles mixed trend/seasonal patterns.",
        },
    }

    return Response({
        "dynamic_stats": result,
        "static_recommendations": STATIC_RECOMMENDATIONS,
    })


# ── Column Correlations endpoint ──────────────────────────────────────────────

@api_view(['GET'])
def column_correlations_view(request, dataset_id):
    """Compute pairwise correlations between columns and return nodes + edges for a mind map."""
    from sklearn.feature_selection import mutual_info_regression
    import numpy as np

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    ds = get_dataset(dataset_id)
    if not ds:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if ds.get('user_id') != user_id:
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    gc = ds.get('global_context') or {}
    if isinstance(gc, str):
        import json as _j
        try:
            gc = _j.loads(gc)
        except Exception:
            gc = {}

    step6_path = (gc.get("step6") or {}).get("smart_path")
    processed_path = ds.get('processed_path')
    artifact_path = step6_path or processed_path
    if not artifact_path:
        return Response({'error': 'No processed data available'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, artifact_path)
    except Exception:
        return Response({'error': 'Failed to download data'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    import io
    if artifact_path.endswith('.parquet'):
        import pyarrow.parquet as pq
        df = pq.read_table(io.BytesIO(file_bytes)).to_pandas()
    else:
        df = pd.read_csv(io.BytesIO(file_bytes))

    if len(df) > 10000:
        df = df.sample(n=10000, random_state=42)

    cols_meta = get_columns_metadata(dataset_id)
    meta_map = {}
    for cm in (cols_meta or []):
        name = cm.get('clean_name') or cm.get('original_name', '')
        ai = cm.get('ai_profile') or {}
        if isinstance(ai, str):
            import json as _j2
            try:
                ai = _j2.loads(ai)
            except Exception:
                ai = {}
        meta_map[name] = {
            "type": cm.get('data_type', 'unknown'),
            "role": ai.get('role', 'unknown'),
            "semantic": ai.get('semantic_meaning', name),
        }

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    categorical_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()

    nodes = []
    for col in numeric_cols + categorical_cols:
        info = meta_map.get(col, {"type": "unknown", "role": "unknown", "semantic": col})
        nodes.append({
            "id": col,
            "label": info["semantic"] if info["semantic"] != col else col.replace('_', ' ').title(),
            "type": info["type"],
            "role": info["role"],
            "is_numeric": col in numeric_cols,
        })

    edges = []
    threshold = 0.3

    if len(numeric_cols) >= 2:
        corr_matrix = df[numeric_cols].corr(method='pearson')
        for i, col_a in enumerate(numeric_cols):
            for j, col_b in enumerate(numeric_cols):
                if j <= i:
                    continue
                r = corr_matrix.iloc[i, j]
                if pd.notna(r) and abs(r) >= threshold:
                    edges.append({
                        "source": col_a,
                        "target": col_b,
                        "weight": round(float(r), 3),
                        "method": "pearson",
                    })

    for cat_col in categorical_cols[:5]:
        try:
            encoded = df[cat_col].astype('category').cat.codes.values.reshape(-1, 1)
            for num_col in numeric_cols[:10]:
                target_vals = df[num_col].values
                mask = ~np.isnan(target_vals) & (encoded.ravel() >= 0)
                if mask.sum() < 10:
                    continue
                mi = mutual_info_regression(
                    encoded[mask], target_vals[mask], random_state=42, n_neighbors=5
                )
                score = float(mi[0])
                if score >= threshold * 0.5:
                    normalized = min(score / max(1.0, score), 1.0)
                    edges.append({
                        "source": cat_col,
                        "target": num_col,
                        "weight": round(normalized, 3),
                        "method": "mutual_info",
                    })
        except Exception:
            continue

    return Response({"nodes": nodes, "edges": edges})


# ── Text-to-Speech (Google Cloud Chirp 3 HD) endpoint ────────────────────────

# Rate-limit by sliding window instead of fixed cooldown, so sentence-level
# streaming (which fires several TTS requests back-to-back) isn't throttled.
_tts_request_window: dict[str, list[float]] = {}
_tts_request_window_lock = threading.Lock()
TTS_MAX_REQ_PER_10S = 25  # plenty of headroom for streaming convo mode

# Module-level HTTP session for upstream TTS calls. Reusing a single Session
# pools TCP/TLS connections to generativelanguage.googleapis.com and
# texttospeech.googleapis.com — saves ~50-100ms of handshake per sentence
# in conversation mode where 5-10 TTS requests fire back to back.
import requests as _requests_pkg  # noqa: E402
_tts_http_session = _requests_pkg.Session()

# Per-user daily char/cost accounting so we can monitor (and cap) Chirp 3 HD spend.
# Resets each UTC day. Lives in-process (cleared on server restart) — fine for dev;
# for prod consider persisting to Supabase if you care across restarts.
# Pricing (Chirp 3 HD = "Premium" tier): $0.000016 per character after the
# 100,000 free-character monthly quota. See https://cloud.google.com/text-to-speech/pricing
TTS_PRICE_PER_CHAR_USD = 0.000016
TTS_DAILY_HARD_CAP_CHARS = int(os.environ.get("TTS_DAILY_HARD_CAP_CHARS", "50000") or 50000)

_tts_usage: dict[str, dict] = {}  # user_id -> { date: 'YYYY-MM-DD', chars: int, requests: int }
_tts_usage_lock = threading.Lock()


def _get_user_tts_usage(user_id: str) -> dict:
    """Return today's usage dict for a user (auto-resets at UTC midnight)."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = _tts_usage.get(user_id)
    if not entry or entry.get("date") != today:
        entry = {"date": today, "chars": 0, "requests": 0}
        _tts_usage[user_id] = entry
    return entry


def _bump_tts_usage(user_id: str, char_count: int) -> dict:
    with _tts_usage_lock:
        entry = _get_user_tts_usage(user_id)
        entry["chars"] += char_count
        entry["requests"] += 1
        return dict(entry)  # snapshot

# ── TTS model registry ──────────────────────────────────────────────────────
# We support two model families:
#   1. Chirp 3 HD (en-US-Chirp3-HD-<speaker>, ar-XA-Chirp3-HD-<speaker>) — stable
#      MP3 output, low latency, regional voices. Good for streaming.
#   2. Gemini 3.1 Flash TTS Preview — newer model with a natural-language `prompt`
#      field that steers tone/pacing/style. Returns LINEAR16 (raw PCM) which we
#      wrap in a WAV header for browser playback. 30 multilingual prebuilt voices
#      (Achernar, Kore, Aoede, Callirrhoe, …). Significantly higher quality for
#      narrative content.

TTS_MODEL_GEMINI = "gemini-3.1-flash-tts-preview"
# Faster preview model — ~30-50% lower latency per sentence than 3.1, used for
# live conversation mode where time-to-first-sound matters more than max quality.
TTS_MODEL_GEMINI_FAST = "gemini-2.5-flash-preview-tts"
TTS_MODEL_CHIRP3 = "chirp3-hd"

# Default Chirp 3 HD voice per language.
CHIRP3_DEFAULT_VOICES = {
    "en":    "en-US-Chirp3-HD-Aoede",
    "en-US": "en-US-Chirp3-HD-Aoede",
    "ar":    "ar-XA-Chirp3-HD-Laomedeia",
    "ar-EG": "ar-XA-Chirp3-HD-Laomedeia",
    "ar-XA": "ar-XA-Chirp3-HD-Laomedeia",
}

# Chirp 3 HD preset → fully-qualified voice name.
CHIRP3_VOICE_PRESETS = {
    "en-US": {
        "warm-female":    "en-US-Chirp3-HD-Aoede",
        "anchor-female":  "en-US-Chirp3-HD-Despina",
        "calm-female":    "en-US-Chirp3-HD-Leda",
        "radio-male":     "en-US-Chirp3-HD-Charon",
        "narrator-male":  "en-US-Chirp3-HD-Iapetus",
        "strong-male":    "en-US-Chirp3-HD-Fenrir",
        "news-male":      "en-US-Chirp3-HD-Orus",
    },
    "ar-XA": {
        "warm-female":    "ar-XA-Chirp3-HD-Laomedeia",
        "anchor-female":  "ar-XA-Chirp3-HD-Despina",
        "calm-female":    "ar-XA-Chirp3-HD-Leda",
        "radio-male":     "ar-XA-Chirp3-HD-Charon",
        "narrator-male":  "ar-XA-Chirp3-HD-Iapetus",
        "strong-male":    "ar-XA-Chirp3-HD-Fenrir",
        "news-male":      "ar-XA-Chirp3-HD-Orus",
    },
}

# Gemini 3.1 Flash TTS voices — language-agnostic (multilingual). Tuned by ear
# from the official 30-voice catalog for narration-friendly characters.
GEMINI_TTS_VOICE_PRESETS = {
    "radio-male":     "Achernar",      # deep broadcast male (user's tested choice)
    "news-male":      "Charon",        # clear news anchor male
    "narrator-male":  "Iapetus",       # warm storyteller male
    "strong-male":    "Fenrir",        # strong assertive male
    "warm-female":    "Aoede",         # warm female narrator
    "anchor-female":  "Despina",       # sharp news anchor female
    "calm-female":    "Leda",          # calm soothing female
    "expressive-female": "Callirrhoe", # expressive narrator (Gemini docs example)
    "lively-female":  "Kore",          # lively versatile female
}

# Default Gemini voice when the user hasn't picked one.
GEMINI_DEFAULT_VOICE = "Achernar"

# Natural-language prompt presets — these steer the Gemini TTS model's tone.
# Maps style key → prompt string.  Used when the frontend doesn't supply one.
GEMINI_TTS_TONE_PROMPTS = {
    "warm":        "Read aloud in a warm, welcoming tone.",
    "broadcaster": (
        "Read aloud as a confident senior business analyst delivering an executive "
        "briefing. Polished, articulate, broadcast-quality narration with natural "
        "pacing. Warm but professional."
    ),
    "casual":      "Read aloud in a friendly conversational tone, like briefing a colleague over coffee.",
    "energetic":   "Read aloud with an upbeat, enthusiastic energy — like sharing exciting news.",
    "calm":        "Read aloud in a calm, measured, reassuring tone.",
}


def _normalize_tts_language(language: str) -> str:
    """Return a canonical BCP-47 language code for the TTS payload."""
    lang = (language or "en").strip()
    low = lang.lower()
    if low in ("ar-eg", "ar-xa", "ar"):
        return "ar-eg"  # Gemini TTS prefers lowercase regional code
    if low.startswith("en"):
        return "en-us"
    return lang.lower()


def _resolve_tts_voice(
    language: str,
    voice_override: str | None,
    model: str,
) -> tuple[str, str]:
    """Return (language_code, voice_name) for the chosen model family.

    For Chirp 3 HD voices keep the legacy 'en-US-Chirp3-HD-<x>' format. For
    Gemini TTS strip any language prefix — the model takes bare voice names.
    """
    is_gemini = model in (TTS_MODEL_GEMINI, TTS_MODEL_GEMINI_FAST)
    lang = (language or "en").strip()

    if is_gemini:
        lang_code = _normalize_tts_language(lang)
        if voice_override:
            v = voice_override.strip()
            # Lookup by preset key
            if v.lower() in GEMINI_TTS_VOICE_PRESETS:
                return lang_code, GEMINI_TTS_VOICE_PRESETS[v.lower()]
            # Already a bare voice name (e.g. "Achernar") — pass through
            if "-" not in v:
                return lang_code, v
            # If user passed a Chirp3 voice name, just use the speaker part.
            if "Chirp3-HD" in v:
                speaker = v.rsplit("-", 1)[-1]
                return lang_code, speaker
            return lang_code, v
        return lang_code, GEMINI_DEFAULT_VOICE

    # Chirp 3 HD path
    if lang in ("ar-EG", "ar-XA", "ar"):
        lang_code = "ar-XA"
    elif lang.lower().startswith("en"):
        lang_code = "en-US"
    else:
        lang_code = lang

    if voice_override:
        v = voice_override.strip()
        if "Chirp3-HD" in v and "-" in v:
            return lang_code, v
        preset_map = CHIRP3_VOICE_PRESETS.get(lang_code, {})
        if v.lower() in preset_map:
            return lang_code, preset_map[v.lower()]
        return lang_code, f"{lang_code}-Chirp3-HD-{v}"

    default_name = CHIRP3_DEFAULT_VOICES.get(lang) or CHIRP3_DEFAULT_VOICES.get(lang_code) or "en-US-Chirp3-HD-Aoede"
    return lang_code, default_name


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw LINEAR16 PCM bytes in a minimal WAV header (RIFF). Browsers can
    play the result directly via an <audio> element."""
    import struct
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    data_size = len(pcm_bytes)
    chunk_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, sample_width * 8,
        b"data", data_size,
    )
    return header + pcm_bytes


# Backwards-compat alias for any old callers
def _resolve_chirp3_voice(language: str, voice_override: str | None) -> tuple[str, str]:
    return _resolve_tts_voice(language, voice_override, TTS_MODEL_CHIRP3)


import re as _re_tts

# Tokens that clearly look like data identifiers — they contain at least one
# underscore or hyphen joining alphanumeric runs (e.g. marketing_timeseries,
# spend-vs-conversions, KPI_Q4_2025). These are always safe to humanize.
_IDENT_TOKEN_RE = _re_tts.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[_\-][A-Za-z0-9]+)+\b")

# Pure camelCase / PascalCase tokens with ≥3 internal word boundaries — e.g.
# `revenueByMarketingChannel` (3 boundaries: ueBy/yMa/gCh). Three boundaries
# rules out short proper nouns like 'iPhone' / 'PayPal' / 'GitHub'.
_CAMEL_IDENT_RE = _re_tts.compile(
    r"\b(?:[a-z]+(?:[A-Z][a-z]+){3,}|[A-Z][a-z]+(?:[A-Z][a-z]+){2,})\b"
)

# camelCase / PascalCase boundary (a lowercase-then-uppercase pair).
_CAMEL_BOUND_1_RE = _re_tts.compile(r"([a-z0-9])([A-Z])")
# Sequence of capitals followed by a capital + lowercase (e.g. "KPIList" → "KPI List").
_CAMEL_BOUND_2_RE = _re_tts.compile(r"([A-Z]+)([A-Z][a-z])")

# Common business / chart-title abbreviations that get mispronounced by TTS.
# Keys MUST be lowercased; matching is case-insensitive on whole-word boundaries.
_ABBREV_EXPANSIONS_EN = {
    "vs.": "versus",
    "vs":  "versus",
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "et cetera",
    "&":   "and",
    "yoy": "year over year",
    "qoq": "quarter over quarter",
    "mom": "month over month",
    "wow": "week over week",
    "kpi": "key performance indicator",
    "kpis": "key performance indicators",
    "roi": "return on investment",
    "roe": "return on equity",
    "arpu": "average revenue per user",
    "cac":  "customer acquisition cost",
    "ltv":  "lifetime value",
    "ctr":  "click through rate",
}
_ABBREV_EXPANSIONS_AR = {
    "vs.":  "مقابل",
    "vs":   "مقابل",
    "&":    "و",
    "kpi":  "مؤشر الأداء",
    "kpis": "مؤشرات الأداء",
    "roi":  "العائد على الاستثمار",
    "roe":  "العائد على حقوق الملكية",
    "ctr":  "معدل النقر",
    "ltv":  "قيمة العميل",
    "cac":  "تكلفة اكتساب العميل",
    "arpu": "متوسط الإيراد لكل مستخدم",
    "yoy":  "سنويا",
    "qoq":  "ربعيا",
    "mom":  "شهريا",
    "wow":  "أسبوعيا",
    "etc.": "وهكذا",
    "e.g.": "مثلا",
    "i.e.": "يعني",
}


def _humanize_identifier(text: str) -> str:
    """Convert snake_case / kebab-case / camelCase identifiers into spaced words.

    Examples:
        marketing_timeseries → marketing time series (lower-cased preserved)
        Spend-vs-Conversions → Spend vs Conversions
        revenueByMarketingChannel → revenue By Marketing Channel
        KPIList → KPI List
        snake_camelCase_mix → snake camel Case mix

    Pure prose without identifier patterns is returned untouched.
    """
    if not isinstance(text, str) or not text:
        return "" if text is None else str(text)

    def _split_token(match):
        token = match.group(0)
        token = token.replace("_", " ").replace("-", " ")
        token = _CAMEL_BOUND_1_RE.sub(r"\1 \2", token)
        token = _CAMEL_BOUND_2_RE.sub(r"\1 \2", token)
        return _re_tts.sub(r"[ \t]{2,}", " ", token).strip()

    out = _IDENT_TOKEN_RE.sub(_split_token, text)

    def _split_camel(match):
        token = match.group(0)
        token = _CAMEL_BOUND_1_RE.sub(r"\1 \2", token)
        token = _CAMEL_BOUND_2_RE.sub(r"\1 \2", token)
        return token

    out = _CAMEL_IDENT_RE.sub(_split_camel, out)

    # Strip trivial wrapping quotes around single identifier-looking tokens —
    # 'spend_vs_conversions' becomes "spend vs conversions" without quote pauses.
    out = _re_tts.sub(r"['\"`‘’“”]([A-Za-z][A-Za-z0-9 ]{2,40})['\"`‘’“”]", r"\1", out)
    return out


# ── Arabic (Fusha / MSA) number-to-words converter ──────────────────────────
# Why MSA and not Egyptian colloquial: written Arabic numbers ARE in MSA form;
# the Chirp 3 HD Egyptian voice reads MSA words with Egyptian accent/prosody,
# which gives the best perceived quality. Digits left raw get pronounced in
# robotic Fusha, which is exactly what we're trying to avoid.
#
# Case choice: ACCUSATIVE / GENITIVE form (منصوب / مجرور), which is what
# Egyptian Arabic uses for numbers AND what Fusha uses when a number is
# followed by a counted noun. Examples:
#   ✓ ثلاثين جنيه, أربعين موظف, اثنين وخمسين عميل
#   ✗ ثلاثون جنيه, أربعون موظف, اثنان وخمسون عميل (nominative — sounds formal/odd in audio)
# No tanween is added to the counted noun ('جنيه' not 'جنيهاً').

_AR_ONES = ["", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_AR_TEENS = [
    "عشرة", "أحد عشر", "اثني عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر",
]
# Accusative/genitive form for tens (-een, not -oon)
_AR_TENS = ["", "", "عشرين", "ثلاثين", "أربعين", "خمسين", "ستين", "سبعين", "ثمانين", "تسعين"]
# مئتين (accusative dual) instead of مئتان (nominative dual)
_AR_HUNDREDS = [
    "", "مئة", "مئتين", "ثلاثمئة", "أربعمئة", "خمسمئة",
    "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة",
]


def _arabic_int_words(n: int) -> str:
    """Convert a non-negative integer into Modern Standard Arabic words.

    Covers 0 up to ~999 trillion. Applies Arabic plural rules for scale
    words (مليار / ملياران / مليارات, ألف / ألفان / آلاف, …).
    """
    if n < 0:
        return "ناقص " + _arabic_int_words(-n)
    if n == 0:
        return "صفر"

    def _below_1000(x: int) -> str:
        parts: list[str] = []
        if x >= 100:
            parts.append(_AR_HUNDREDS[x // 100])
            x %= 100
        if x >= 20:
            ones_digit = x % 10
            tens_word = _AR_TENS[x // 10]
            if ones_digit == 0:
                parts.append(tens_word)
            else:
                parts.append(_AR_ONES[ones_digit] + " و" + tens_word)
        elif x >= 10:
            parts.append(_AR_TEENS[x - 10])
        elif x >= 1:
            parts.append(_AR_ONES[x])
        return " و".join(parts)

    def _scale(value: int, sing: str, dual: str, plural: str) -> str:
        if value == 1:
            return sing
        if value == 2:
            return dual
        if 3 <= value <= 10:
            return _below_1000(value) + " " + plural
        # 11+ takes singular form (Arabic plural rules)
        return _below_1000(value) + " " + sing

    parts: list[str] = []
    if n >= 1_000_000_000_000:
        t = n // 1_000_000_000_000
        n %= 1_000_000_000_000
        parts.append(_scale(t, "تريليون", "تريليونين", "تريليونات"))
    if n >= 1_000_000_000:
        b = n // 1_000_000_000
        n %= 1_000_000_000
        parts.append(_scale(b, "مليار", "مليارين", "مليارات"))
    if n >= 1_000_000:
        m = n // 1_000_000
        n %= 1_000_000
        parts.append(_scale(m, "مليون", "مليونين", "ملايين"))
    if n >= 1_000:
        k = n // 1_000
        n %= 1_000
        parts.append(_scale(k, "ألف", "ألفين", "آلاف"))
    if n > 0:
        parts.append(_below_1000(n))

    return " و".join(parts) if parts else "صفر"


# ── Egyptian-dialect number words → Fusha (MSA) ─────────────────────────────
# Gemini, when told to write Egyptian Arabic prose, also writes numbers in
# Egyptian dialect (واتناشر, خمسمية, تمنمية). The Chirp 3 HD voice handles those
# unevenly, so we normalize them to Fusha word forms. The voice still has an
# Egyptian accent, so it sounds natural — only the WRITTEN form changes.

_AR_EG_TO_FUSHA: dict[str, str] = {
    # Ones — Egyptian forms map to ACCUSATIVE Fusha (which sounds natural in audio)
    "اتنين":   "اثنين",   # accusative dual (NOT اثنان which is nominative)
    "إتنين":   "اثنين",
    "تلاتة":   "ثلاثة",
    "تلات":    "ثلاثة",
    "تمنية":   "ثمانية",
    "تمانية":  "ثمانية",
    # Teens (Egyptian "-aashar" → Fusha "-ahar"). 12 = اثني عشر (accusative)
    "حداشر":   "أحد عشر",
    "إحداشر":  "أحد عشر",
    "اتناشر":  "اثني عشر",
    "إتناشر":  "اثني عشر",
    "اثناشر":  "اثني عشر",
    "تلاتاشر": "ثلاثة عشر",
    "أربعتاشر": "أربعة عشر",
    "اربعتاشر": "أربعة عشر",
    "خمستاشر": "خمسة عشر",
    "ستاشر":   "ستة عشر",
    "سبعتاشر": "سبعة عشر",
    "تمنتاشر": "ثمانية عشر",
    "تمانتاشر": "ثمانية عشر",
    "تسعتاشر": "تسعة عشر",
    # Tens — Egyptian form IS already the accusative Fusha form, just orthographic fixes
    # (ت→ث, add hamza on alef). "عشرين", "خمسين", "ستين", "سبعين", "تسعين" need no change.
    "تلاتين": "ثلاثين",
    "اربعين": "أربعين",
    "تمانين": "ثمانين",
    # Hundreds (Egyptian "-mia" → Fusha "-mi'a", "تلت-/ربع-" → "ثلاث-/أربع-")
    "مية":     "مئة",
    "ميه":     "مئة",
    "ميتين":   "مئتين",   # accusative dual
    "تلتمية":  "ثلاثمئة",
    "تلتميه":  "ثلاثمئة",
    "ربعمية":  "أربعمئة",
    "اربعمية": "أربعمئة",
    "ربعميه":  "أربعمئة",
    "خمسمية":  "خمسمئة",
    "خمسميه":  "خمسمئة",
    "ستمية":   "ستمئة",
    "ستميه":   "ستمئة",
    "سبعمية":  "سبعمئة",
    "سبعميه":  "سبعمئة",
    "تمنمية":  "ثمانمئة",
    "تمانمية": "ثمانمئة",
    "تمنميه":  "ثمانمئة",
    "تسعمية":  "تسعمئة",
    "تسعميه":  "تسعمئة",
    # Thousands — accusative dual (ألفين). Egyptian "ألفين" already matches.
    "الفين":   "ألفين",
    # Fractions / common money words
    "نص":      "نصف",
    "تلت":     "ثلث",
    "تلتين":   "ثلثين",  # accusative dual ('two-thirds')
    # ── Nominative Fusha → Accusative Fusha ────────────────────────────
    # Gemini sometimes emits the formal nominative form (e.g. 'ثلاثون', 'أربعون',
    # 'اثنان') even when told otherwise. The accusative form is what's needed
    # for natural-sounding audio when followed by a counted noun.
    "اثنان":    "اثنين",
    "إثنان":    "اثنين",
    "اثنا":     "اثني",       # 'اثنا عشر' → 'اثني عشر' (12 accusative)
    "إثنا":     "اثني",
    "عشرون":    "عشرين",
    "ثلاثون":   "ثلاثين",
    "أربعون":   "أربعين",
    "اربعون":   "أربعين",
    "خمسون":    "خمسين",
    "ستون":     "ستين",
    "سبعون":    "سبعين",
    "ثمانون":   "ثمانين",
    "تسعون":    "تسعين",
    "مئتان":    "مئتين",
    "مائتان":   "مئتين",
    "ألفان":    "ألفين",
    "مليونان":  "مليونين",
    "ملياران":  "مليارين",
    "تريليونان": "تريليونين",
}

# Arabic letter range for boundary detection (Arabic + extended + presentation forms).
_AR_LETTER_CLASS = r"\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"

# Build one alternation regex of all Egyptian keys, longest-first so multi-letter
# words are tried before shorter substrings. Allows a single optional Arabic
# prefix letter (و / ف / ب / ل / ك), optionally followed by ال (definite article).
_AR_EG_KEYS_SORTED = sorted(_AR_EG_TO_FUSHA.keys(), key=len, reverse=True)
_AR_EG_PATTERN = _re_tts.compile(
    r"(?<![" + _AR_LETTER_CLASS + r"])"
    r"((?:[وفبلك])?(?:ال)?)"
    r"(" + "|".join(_re_tts.escape(k) for k in _AR_EG_KEYS_SORTED) + r")"
    r"(?![" + _AR_LETTER_CLASS + r"])"
)


def _normalize_arabic_to_fusha(text: str) -> str:
    """Replace Egyptian-dialect number forms with their Fusha (MSA) equivalents.

    Preserves any prefix particle (و / ف / ب / ل / ك) and optional ال so
    'وخمسمية' becomes 'وخمسمئة' (not 'و خمسمئة'). Skips matches that are part
    of a longer Arabic word (uses Arabic-aware boundary classes).
    """
    if not text:
        return text

    # Fast path: if the string contains zero Arabic codepoints (U+0600-U+06FF
    # plus extended ranges) there is nothing to normalize. Saves several
    # regex passes per English-only TTS chunk in convo mode.
    if not any(
        '\u0600' <= ch <= '\u06FF'
        or '\u0750' <= ch <= '\u077F'
        or '\uFB50' <= ch <= '\uFDFF'
        or '\uFE70' <= ch <= '\uFEFF'
        for ch in text
    ):
        return text

    def _sub(match: "_re_tts.Match[str]") -> str:
        prefix = match.group(1) or ""
        word = match.group(2)
        replacement = _AR_EG_TO_FUSHA.get(word, word)
        return f"{prefix}{replacement}"

    out = _AR_EG_PATTERN.sub(_sub, text)

    # Handle multi-word "percent" phrases separately (Egyptian "في المية" /
    # "بالمية" → Fusha "بالمئة"). Run after token map so we don't double-replace.
    out = _re_tts.sub(r"\bفي\s+الم(?:ي|ائ|ئ)ة\b", "بالمئة", out)
    out = _re_tts.sub(r"\bبالم(?:ي|ائ)ة\b", "بالمئة", out)

    # Prefix prepositions written with a tatweel-stretch (e.g. 'لـ خمسمئة') —
    # remove the tatweel AND any whitespace so the prefix attaches to the next
    # word ('لخمسمئة'). Otherwise stripping the tatweel later leaves a stranded
    # single-letter 'ل' that the TTS reads as its own word.
    out = _re_tts.sub(r"(?<![\u0600-\u06FF])([وفبلك])ـ\s*", r"\1", out)

    # Strip Arabic diacritics & tanween — the user prefers no tanween on counted
    # nouns (جنيه not جنيهاً), and stripping all vowel marks is safe for TTS
    # since the engine reads the base letters with its own learned prosody.
    # Range covers: fatha-tanween (ً), damma-tanween (ٌ), kasra-tanween (ٍ),
    # fatha (َ), damma (ُ), kasra (ِ), shadda (ّ), sukun (ْ), small alef (ٰ),
    # and tatweel (ـ).
    out = _re_tts.sub(r"[\u064B-\u065F\u0670\u0640]", "", out)
    return out


def _arabic_number_words(value: str) -> str:
    """Convert a number-string (with commas, decimal, K/M/B/T suffix) to MSA words.

    Examples:
        '15'         → 'خمسة عشر'
        '25.5'       → 'خمسة وعشرون فاصلة خمسة'
        '1500'       → 'ألف وخمسمئة'
        '1,250,000'  → 'مليون ومئتان وخمسون ألف'
        '1.5M'       → 'مليون وخمسمئة ألف'
        '50K'        → 'خمسون ألف'

    Returns the original value untouched if parsing fails.
    """
    if not value:
        return ""

    s = value.replace(",", "").strip()
    mult = 1
    if s and s[-1] in "KkMmBbTt":
        suffix = s[-1].upper()
        s = s[:-1]
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}[suffix]
    if not s:
        return value

    try:
        # Version-style with multiple dots (e.g. 1.2.3) — read with separators
        if s.count(".") > 1:
            return " نقطة ".join(
                _arabic_int_words(int(p)) for p in s.split(".") if p.isdigit()
            )

        if "." in s:
            int_str, dec_str = s.split(".")
            int_val = int(int_str) if int_str else 0
            dec_val = int(dec_str) if dec_str else 0
            if mult > 1:
                # Combine integer + decimal into single value, then spell
                total = int_val * mult + dec_val * (mult // (10 ** len(dec_str)))
                return _arabic_int_words(total)
            # Read decimal digits individually after "فاصلة"
            int_words = _arabic_int_words(int_val)
            dec_words = " ".join(_arabic_int_words(int(d)) for d in dec_str)
            return f"{int_words} فاصلة {dec_words}"

        return _arabic_int_words(int(s) * mult)
    except (ValueError, IndexError):
        return value


def _expand_abbreviations(text: str, language: str = "en") -> str:
    """Replace common abbreviations & symbols so TTS pronounces them naturally.

    For Arabic, ALL digit sequences are converted to MSA word form because the
    Chirp 3 HD Arabic voice mispronounces raw digits (reads them in flat Fusha
    instead of with the natural prosody we want).
    """
    if not text:
        return ""
    is_ar = (language or "").lower().startswith("ar")
    mapping = _ABBREV_EXPANSIONS_AR if is_ar else _ABBREV_EXPANSIONS_EN

    def _replace(match):
        key = match.group(0).lower()
        return mapping.get(key, match.group(0))

    keys = sorted(mapping.keys(), key=len, reverse=True)
    pattern = _re_tts.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(_re_tts.escape(k) for k in keys) + r")(?![A-Za-z0-9])",
        flags=_re_tts.IGNORECASE,
    )
    out = pattern.sub(_replace, text)

    number_re = r"(\d+(?:[,.]\d+)*)"
    number_with_suffix_re = number_re + r"([KkMmBbTt]?)"

    if is_ar:
        # Percent: '15%' → 'خمسة عشر بالمئة' (handles 25.5% and 1.5M% too)
        out = _re_tts.sub(
            number_with_suffix_re + r"\s*%",
            lambda m: f"{_arabic_number_words((m.group(1) or '') + (m.group(2) or ''))} بالمئة",
            out,
        )
        # Currencies (whole amount + word)
        out = _re_tts.sub(
            r"\$\s*" + number_with_suffix_re,
            lambda m: f"{_arabic_number_words((m.group(1) or '') + (m.group(2) or ''))} دولار",
            out,
        )
        out = _re_tts.sub(
            r"€\s*" + number_with_suffix_re,
            lambda m: f"{_arabic_number_words((m.group(1) or '') + (m.group(2) or ''))} يورو",
            out,
        )
        out = _re_tts.sub(
            r"£\s*" + number_with_suffix_re,
            lambda m: f"{_arabic_number_words((m.group(1) or '') + (m.group(2) or ''))} جنيه إسترليني",
            out,
        )
        # Hash / number sign
        out = _re_tts.sub(
            r"#\s*" + number_re,
            lambda m: f"رقم {_arabic_number_words(m.group(1) or '')}",
            out,
        )
        # Catch-all: standalone numbers (years, counts, IDs). Runs LAST so it
        # doesn't trigger on digits inside earlier-converted constructs.
        out = _re_tts.sub(
            r"\b" + number_with_suffix_re + r"\b",
            lambda m: _arabic_number_words((m.group(1) or "") + (m.group(2) or "")),
            out,
        )
    else:
        out = _re_tts.sub(number_re + r"\s*%", r"\1 percent", out)
        out = _re_tts.sub(r"\$\s*" + number_re, r"\1 dollars", out)
        out = _re_tts.sub(r"#\s*" + number_re, r"number \1", out)
    return out


def _strip_markdown_for_tts(text: str, language: str = "en") -> str:
    """Remove markdown + technical noise so TTS reads natural speech.

    Pipeline:
      1. Strip markdown (code fences, bold/italic, list markers, headings, links).
      2. Humanize snake_case / kebab-case / camelCase identifiers so the engine
         doesn't pronounce underscores / dashes literally.
      3. Expand abbreviations and symbols ('vs.' → 'versus', '15%' → '15 percent'
         or '15 في المية' for Arabic) so they read like natural speech.
      4. Collapse whitespace.
    """
    import re
    t = text or ""
    t = re.sub(r"```[\s\S]*?```", " ", t)            # code fences
    t = re.sub(r"`([^`]+)`", r"\1", t)               # inline code
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)           # bold
    t = re.sub(r"\*(.+?)\*", r"\1", t)               # italic
    t = re.sub(r"^\s*[-*]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", t)        # links
    t = _humanize_identifier(t)
    t = _expand_abbreviations(t, language=language)
    # For Arabic outputs, normalize Egyptian-dialect number words to Fusha so the
    # TTS engine pronounces them cleanly. (The voice is still Egyptian-accented;
    # only the written form changes.)
    if (language or "").lower().startswith("ar"):
        t = _normalize_arabic_to_fusha(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _parse_audio_overview_body(body: dict) -> dict | None:
    """Client metadata for Listen → one combined voice-log row after TTS."""
    ao = body.get("audio_overview") or body.get("audioOverview")
    if not isinstance(ao, dict):
        return None
    dataset_id = str(ao.get("dataset_id") or ao.get("datasetId") or "").strip()
    if not dataset_id:
        return None
    return ao


@api_view(['POST'])
def tts_view(request):
    """Convert text to speech using Google Cloud Chirp 3 HD voices. Returns audio/mpeg (MP3)."""
    import base64
    from django.http import HttpResponse

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    now = time.time()
    with _tts_request_window_lock:
        window = _tts_request_window.get(user_id, [])
        window = [t for t in window if now - t < 10.0]
        if len(window) >= TTS_MAX_REQ_PER_10S:
            oldest = window[0]
            retry_after = max(1, int(10.0 - (now - oldest)) + 1)
            logger.warning(
                f"[TTS] user={user_id[:8]} RATE-LIMITED (>{TTS_MAX_REQ_PER_10S} reqs/10s)"
            )
            return Response(
                {'error': 'Rate limited', 'retry_after_seconds': retry_after},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        window.append(now)
        _tts_request_window[user_id] = window

    # Daily hard cap check (before we incur the cost)
    with _tts_usage_lock:
        today_entry = _get_user_tts_usage(user_id)
        if today_entry["chars"] >= TTS_DAILY_HARD_CAP_CHARS:
            logger.warning(
                f"[TTS] user={user_id[:8]} BLOCKED — daily cap reached "
                f"({today_entry['chars']} chars / cap {TTS_DAILY_HARD_CAP_CHARS})"
            )
            return Response(
                {
                    'error': 'Daily TTS character cap reached',
                    'used_chars': today_entry["chars"],
                    'cap_chars': TTS_DAILY_HARD_CAP_CHARS,
                    'resets_at': 'UTC midnight',
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

    body = request.data if isinstance(request.data, dict) else {}
    ao_session = _parse_audio_overview_body(body)
    raw_text = body.get('text', '')
    if not isinstance(raw_text, str):
        return Response({'error': 'text must be a string'}, status=status.HTTP_400_BAD_REQUEST)
    text = _strip_markdown_for_tts(raw_text, language=body.get('language', 'en'))
    if not text:
        return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)
    # Google Cloud TTS allows up to 5000 chars per request, but Chirp 3 HD
    # accepts longer text. Cap at 4500 to leave headroom for SSML-like markers.
    MAX_TTS_CHARS = 4500
    if len(text) > MAX_TTS_CHARS:
        # Try to truncate at the last sentence boundary so we don't cut mid-word
        slice_text = text[:MAX_TTS_CHARS]
        for sep in ('. ', '! ', '? ', '؟ ', '. ', '。'):
            idx = slice_text.rfind(sep)
            if idx > MAX_TTS_CHARS * 0.7:
                slice_text = slice_text[: idx + len(sep)].rstrip()
                break
        text = slice_text

    language = body.get('language', 'en')
    voice_override = body.get('voice')
    speaking_rate = body.get('speaking_rate')
    pitch = body.get('pitch')
    prompt_field = body.get('prompt') or body.get('tone_prompt')
    raw_model = (body.get('model') or '').strip().lower()

    # Model selection: explicit `model` field wins, otherwise default to Gemini
    # TTS for richer narration (the user's tested config). Frontend can still
    # request chirp3 explicitly for low-latency streaming use cases.
    #   - 'gemini'      → 3.1 Flash TTS Preview (best quality, used for overviews)
    #   - 'gemini-fast' → 2.5 Flash Preview TTS (~30-50% faster, used for convo)
    #   - 'chirp3'      → Cloud TTS Chirp 3 HD (MP3 streaming)
    if raw_model in ('gemini-fast', 'gemini-2.5', 'fast', TTS_MODEL_GEMINI_FAST):
        model_used = TTS_MODEL_GEMINI_FAST
    elif raw_model in ('gemini', 'gemini-tts', TTS_MODEL_GEMINI):
        model_used = TTS_MODEL_GEMINI
    elif raw_model in ('chirp3', 'chirp', 'chirp3-hd', TTS_MODEL_CHIRP3):
        model_used = TTS_MODEL_CHIRP3
    else:
        model_used = TTS_MODEL_GEMINI  # default for non-convo (overview, manual playback)
    is_gemini = model_used in (TTS_MODEL_GEMINI, TTS_MODEL_GEMINI_FAST)

    # Auto-pick a tone prompt for Gemini if none provided, based on language.
    if is_gemini and not prompt_field:
        prompt_field = GEMINI_TTS_TONE_PROMPTS["broadcaster"]
    if isinstance(prompt_field, str):
        prompt_field = prompt_field.strip()[:4000]  # Gemini caps prompt at 4000 bytes
    if isinstance(prompt_field, str) and not prompt_field:
        prompt_field = None

    language_code, voice_name = _resolve_tts_voice(language, voice_override, model_used)

    # Pick the right API key for each model family:
    #   - Gemini TTS Preview → Generative Language API (needs GEMINI_API_KEY)
    #   - Chirp 3 HD → Cloud Text-to-Speech API (needs GOOGLE_TTS_API_KEY)
    # Fall back across both because some users provision a single unrestricted key.
    if is_gemini:
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
            or os.environ.get("GOOGLE_TTS_API_KEY")
            or os.environ.get("GOOGLE_CLOUD_API_KEY")
            or ""
        ).strip()
    else:
        api_key = (
            os.environ.get("GOOGLE_TTS_API_KEY")
            or os.environ.get("GOOGLE_CLOUD_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        ).strip()
    if not api_key:
        return Response(
            {'error': 'TTS not configured: set GEMINI_API_KEY (Gemini TTS) or GOOGLE_TTS_API_KEY (Chirp 3 HD) in environment'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Build (url, payload, audio_b64_extractor) per model. The two model families
    # use completely different APIs:
    #   - Gemini TTS Preview → Generative Language API (generateContent endpoint).
    #     Only needs GEMINI_API_KEY, no Vertex AI / Agent Platform enabled.
    #   - Chirp 3 HD → Cloud Text-to-Speech API (texttospeech.googleapis.com).
    if is_gemini:
        # Inline the style prompt into the text as a leading directive — this is
        # how the Gemini Developer API takes style guidance ("Say cheerfully: ...").
        if prompt_field:
            combined_text = f"{prompt_field}\n\n{text}"
        else:
            combined_text = text
        payload = {
            "contents": [{"parts": [{"text": combined_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name},
                    },
                },
            },
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model_used}:generateContent?key={api_key}"
        )
    else:
        audio_config = {
            "audioEncoding": "MP3",
            "sampleRateHertz": 24000,
        }
        if isinstance(speaking_rate, (int, float)):
            audio_config["speakingRate"] = max(0.25, min(2.0, float(speaking_rate)))
        if isinstance(pitch, (int, float)):
            audio_config["pitch"] = max(-20.0, min(20.0, float(pitch)))
        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": language_code,
                "name": voice_name,
            },
            "audioConfig": audio_config,
        }
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"

    def _log_failure(status_code: int, err_msg: str) -> None:
        try:
            if ao_session:
                voice_logger.log_audio_overview_session(
                    user_id=user_id,
                    dataset_id=str(ao_session.get("dataset_id") or ao_session.get("datasetId") or ""),
                    output_text=text,
                    language=language_code,
                    voice=voice_name,
                    style=str(ao_session.get("style") or "formal")[:32],
                    duration_seconds=ao_session.get("duration_seconds") or ao_session.get("durationSeconds"),
                    user_name=str(ao_session.get("user_name") or ao_session.get("userName") or "")[:80],
                    overview_model=str(ao_session.get("overview_model") or ao_session.get("overviewModel") or "")[:64],
                    tts_model=model_used,
                    speaking_rate=speaking_rate if isinstance(speaking_rate, (int, float)) else None,
                    audio_bytes=None,
                    audio_format="wav" if is_gemini else "mp3",
                    duration_ms=int((time.time() - now) * 1000),
                    status_code=status_code,
                    error=err_msg,
                    extra={"prompt": prompt_field or ""},
                )
            else:
                voice_logger.log_tts_request(
                    user_id=user_id,
                    raw_text=raw_text,
                    stripped_text=text,
                    language=language_code,
                    voice=voice_name,
                    speaking_rate=speaking_rate if isinstance(speaking_rate, (int, float)) else None,
                    pitch=pitch if isinstance(pitch, (int, float)) else None,
                    audio_bytes=None,
                    duration_ms=int((time.time() - now) * 1000),
                    status_code=status_code,
                    error=err_msg,
                    extra={
                        "model": model_used,
                        "prompt": prompt_field or "",
                    },
                )
        except Exception:
            pass

    try:
        r = _tts_http_session.post(url, json=payload, timeout=60 if is_gemini else 30)
    except Exception as e:
        logger.warning(f"TTS request failed ({model_used}): {e}")
        _log_failure(502, f"network: {e}")
        return Response({'error': f'TTS request failed: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

    if r.status_code != 200:
        if is_gemini:
            detail = r.text[:500] if r.text else f"HTTP {r.status_code}"
            logger.warning(f"Gemini TTS error {r.status_code}: {detail}")
            _log_failure(r.status_code, f"upstream: {detail}")
            return Response(
                {'error': f'TTS upstream error ({r.status_code}): {detail}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Chirp 3 HD: try once with the regional endpoint as a fallback.
        regional_url = f"https://us-texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
        try:
            r2 = _tts_http_session.post(regional_url, json=payload, timeout=30)
            if r2.status_code == 200:
                r = r2
            else:
                detail = r.text[:500] if r.text else f"HTTP {r.status_code}"
                logger.warning(f"Chirp 3 HD TTS error {r.status_code}: {detail}")
                _log_failure(r.status_code, f"upstream: {detail}")
                return Response(
                    {'error': f'TTS upstream error: {detail}'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        except Exception as e:
            logger.warning(f"Chirp 3 HD regional TTS failed: {e}")
            _log_failure(502, f"regional: {e}")
            return Response({'error': f'TTS request failed: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

    try:
        data = r.json()
        if is_gemini:
            # Generative Language API shape:
            # { candidates: [{ content: { parts: [{ inlineData: { mimeType, data } }] } }] }
            audio_b64 = ""
            try:
                candidates = data.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    for part in parts:
                        inline = part.get("inlineData") or part.get("inline_data") or {}
                        if inline.get("data"):
                            audio_b64 = inline["data"]
                            break
            except Exception:
                audio_b64 = ""
        else:
            audio_b64 = data.get("audioContent", "")
        if not audio_b64:
            logger.warning(f"TTS empty audio response ({model_used}): {str(data)[:300]}")
            _log_failure(502, "empty audio in response")
            return Response({'error': 'TTS returned empty audio'}, status=status.HTTP_502_BAD_GATEWAY)
        raw_audio = base64.b64decode(audio_b64)
    except Exception as e:
        logger.warning(f"Failed to decode TTS response: {e}")
        _log_failure(502, f"decode: {e}")
        return Response({'error': 'Failed to decode TTS audio'}, status=status.HTTP_502_BAD_GATEWAY)

    # Gemini TTS returns raw LINEAR16 PCM at 24kHz mono. Wrap in WAV header so
    # <audio> elements in the browser can play it directly.
    if is_gemini:
        audio_bytes = _pcm_to_wav(raw_audio, sample_rate=24000, channels=1, sample_width=2)
        content_type = "audio/wav"
        file_ext = "wav"
    else:
        audio_bytes = raw_audio
        content_type = "audio/mpeg"
        file_ext = "mp3"

    # Account for usage + log
    char_count = len(text)
    usage_snapshot = _bump_tts_usage(user_id, char_count)
    elapsed_ms = int((time.time() - now) * 1000)
    today_cost_usd = usage_snapshot["chars"] * TTS_PRICE_PER_CHAR_USD
    logger.info(
        f"[TTS] user={user_id[:8]} model={model_used} voice={voice_name} lang={language_code} "
        f"chars={char_count} kb={len(audio_bytes)//1024} took={elapsed_ms}ms | "
        f"today: {usage_snapshot['requests']} reqs, {usage_snapshot['chars']} chars, "
        f"~${today_cost_usd:.4f}"
    )

    log_id = None
    try:
        if ao_session:
            log_id = voice_logger.log_audio_overview_session(
                user_id=user_id,
                dataset_id=str(ao_session.get("dataset_id") or ao_session.get("datasetId") or ""),
                output_text=text,
                language=language_code,
                voice=voice_name,
                style=str(ao_session.get("style") or "formal")[:32],
                duration_seconds=ao_session.get("duration_seconds") or ao_session.get("durationSeconds"),
                user_name=str(ao_session.get("user_name") or ao_session.get("userName") or "")[:80],
                overview_model=str(ao_session.get("overview_model") or ao_session.get("overviewModel") or "")[:64],
                tts_model=model_used,
                speaking_rate=speaking_rate if isinstance(speaking_rate, (int, float)) else None,
                audio_bytes=audio_bytes,
                audio_format=file_ext,
                duration_ms=elapsed_ms,
                status_code=200,
                extra={
                    "prompt": prompt_field or "",
                },
            )
        else:
            log_id = voice_logger.log_tts_request(
                user_id=user_id,
                raw_text=raw_text,
                stripped_text=text,
                language=language_code,
                voice=voice_name,
                speaking_rate=speaking_rate if isinstance(speaking_rate, (int, float)) else None,
                pitch=pitch if isinstance(pitch, (int, float)) else None,
                audio_bytes=audio_bytes,
                duration_ms=elapsed_ms,
                status_code=200,
                extra={
                    "model": model_used,
                    "prompt": prompt_field or "",
                    "audio_format": file_ext,
                },
            )
    except Exception as e:
        logger.warning(f"[TTS] voice_logger failed: {e}")

    resp = HttpResponse(audio_bytes, content_type=content_type)
    resp['X-TTS-Model'] = model_used
    resp['X-TTS-Voice'] = voice_name
    resp['X-TTS-Language'] = language_code
    resp['X-TTS-Audio-Format'] = file_ext
    if prompt_field:
        # Truncate so the HTTP header doesn't get huge
        resp['X-TTS-Prompt'] = prompt_field[:200]
    resp['X-TTS-Chars'] = str(char_count)
    resp['X-TTS-Today-Chars'] = str(usage_snapshot["chars"])
    resp['X-TTS-Today-Requests'] = str(usage_snapshot["requests"])
    resp['X-TTS-Today-Cost-USD'] = f"{today_cost_usd:.4f}"
    resp['X-TTS-Cap-Chars'] = str(TTS_DAILY_HARD_CAP_CHARS)
    if log_id:
        resp['X-TTS-Log-Id'] = log_id
    resp['Cache-Control'] = 'no-store'
    return resp


@api_view(['GET'])
def tts_usage_view(request):
    """Return today's TTS usage for the authenticated user."""
    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err
    with _tts_usage_lock:
        entry = _get_user_tts_usage(user_id)
        snapshot = dict(entry)
    cost_usd = snapshot["chars"] * TTS_PRICE_PER_CHAR_USD
    return Response({
        "date": snapshot["date"],
        "requests": snapshot["requests"],
        "chars": snapshot["chars"],
        "cap_chars": TTS_DAILY_HARD_CAP_CHARS,
        "remaining_chars": max(0, TTS_DAILY_HARD_CAP_CHARS - snapshot["chars"]),
        "estimated_cost_usd": round(cost_usd, 4),
        "price_per_char_usd": TTS_PRICE_PER_CHAR_USD,
    })


# ── Translate / rewrite a snippet into a target language for TTS ──────────────

_translate_cooldowns: dict[str, float] = {}
_translate_cooldowns_lock = threading.Lock()
TRANSLATE_COOLDOWN_S = 0.5
TRANSLATE_MAX_CHARS_IN = 8000
TRANSLATE_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
)


@api_view(['POST'])
def translate_for_tts_view(request):
    """
    Rewrite a text snippet into a target language for natural spoken delivery.
    POST { text, target_language } → { text, source_chars, output_chars, model, target_language }
    Supported target_language: 'en' (or 'en-US') and 'ar-EG' (or 'ar').
    """
    from google import genai
    from google.genai import types

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    now = time.time()
    with _translate_cooldowns_lock:
        last = _translate_cooldowns.get(user_id, 0.0)
        if now - last < TRANSLATE_COOLDOWN_S:
            return Response(
                {'error': 'Rate limited', 'retry_after_seconds': TRANSLATE_COOLDOWN_S},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        _translate_cooldowns[user_id] = now

    body = request.data if isinstance(request.data, dict) else {}
    raw_text = body.get('text', '')
    target = (body.get('target_language') or 'en').strip()
    if not isinstance(raw_text, str) or not raw_text.strip():
        return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)
    if len(raw_text) > TRANSLATE_MAX_CHARS_IN:
        raw_text = raw_text[:TRANSLATE_MAX_CHARS_IN]

    tl = target.lower()
    style = (body.get('style') or 'formal').strip().lower()  # formal | casual
    if tl.startswith('ar'):
        target_norm = 'ar-EG'
        if style == 'casual':
            instruction = (
                "Rewrite the following business analysis text in CASUAL EGYPTIAN ARABIC dialect "
                "(Masri / Cairene). Friendly, conversational, like chatting with a colleague. "
                "STRICT: do NOT use Modern Standard Arabic (Fusha/MSA). "
                "Use Egyptian pronunciation: ج = g, ث = s, ذ = z, ق = ʔ. "
                "Use Egyptian colloquial words: إحنا، إنتو، عايز، دلوقتي، عشان، فيه، مفيش. "
                "Keep numbers as English digits and core English business terms as-is where natural. "
                "Return ONLY the rewritten text — no quotes, no preamble, no markdown."
            )
        else:
            # POLISHED EGYPTIAN ARABIC — broadcaster / executive briefing register.
            # The trick: it's Egyptian colloquial GRAMMAR + ELEVATED VOCABULARY,
            # NOT Fusha. Explicit examples are required because models default to MSA.
            instruction = (
                "Rewrite the following business analysis text in POLISHED EGYPTIAN ARABIC — "
                "the register used by Egyptian TV business anchors (CBC Business, Al-Mal TV), "
                "executive briefings, and senior Cairene corporate analysts.\n\n"
                "CRITICAL — DO NOT USE MODERN STANDARD ARABIC (FUSHA / MSA / الفصحى).\n"
                "The output must be Egyptian Arabic in structure, particles, and pronouns — "
                "NOT translated Fusha. Models often default to MSA; you MUST resist that.\n\n"
                "REQUIRED EGYPTIAN STRUCTURE (use these — they're non-negotiable):\n"
                "• Pronouns: إحنا (not نحن), إنتو (not أنتم), هما (not هم)\n"
                "• Negation: مش / ما...ش (not ليس / لا)\n"
                "• Future: ه + verb (هنشوف، هيزيد) — NOT سوف or س-\n"
                "• Present continuous: بـ + verb (بنشوف، بيزيد، بتنمو)\n"
                "• Particles: دلوقتي (not الآن), عشان / علشان (not لأن / لكي), فيه / مفيش, كمان (not أيضاً), بس (not فقط/لكن), زي (not مثل), أوي (not جداً)\n"
                "• Question words: ايه (not ما), فين (not أين), إزاي (not كيف), امتى (not متى), ليه (not لماذا)\n"
                "• Verbs: شاف (not رأى), عاوز (not يريد), قدر (not استطاع), جاب (not أحضر)\n"
                "• Demonstratives: ده / دي / دول (not هذا / هذه / هؤلاء)\n"
                "• Pronunciation cues: write ج as g-sound words naturally (جنيه = ginēh), keep ق as it is in writing.\n\n"
                "BUSINESS POLISH (keep tone refined):\n"
                "• Use real business vocabulary in Arabic: المبيعات، الإيرادات، الأرباح، النمو، الحصة السوقية، مؤشرات الأداء، العملاء، الأداء، التوجهات\n"
                "• Keep English terms (KPI, ROI, dashboard) only when no clean Arabic exists\n"
                "• Numbers in Arabic digits when possible, or English digits — either is fine\n"
                "• Avoid pure street slang: يعني كده، خالص، اوعى، طب\n\n"
                "EXAMPLES of the target register:\n"
                "✓ 'المبيعات بتاعتنا زادت بحوالي خمستاشر في المية الربع ده، وده مؤشر إيجابي على نمو الحصة السوقية'\n"
                "✓ 'لو بصينا على البيانات، هنلاقي إن العملاء في القاهرة بيمثلوا تقريباً تلتين الإيرادات'\n"
                "✓ 'مؤشرات الأداء بتاعتنا بتدل على أداء قوي، بس فيه فرصة لتحسين معدل الاحتفاظ بالعملاء'\n"
                "✗ AVOID: 'إن مبيعاتنا قد ارتفعت بنسبة 15% خلال هذا الربع، وهذا مؤشر إيجابي' (Fusha — wrong)\n"
                "✗ AVOID: 'الـ KPIs بتاعتنا' (mixed English-Arabic — TTS garbles it)\n"
                "✗ AVOID: 'المبيعات زادت بـ 15%' (raw digits → TTS reads them in Fusha)\n\n"
                "TTS-SAFETY (mandatory — the audio engine handles these badly):\n"
                "• SPELL OUT all numbers in Egyptian Arabic words ('خمستاشر في المية' not '15%', "
                "'تلتين' not '66%', 'ألفين وخمسمية' not '2500'). Approximate when needed.\n"
                "• TRANSLATE every English word into Egyptian Arabic (KPIs → مؤشرات الأداء, "
                "ROI → معدل العائد على الاستثمار, dashboard → لوحة المعلومات, "
                "Revenue by Channel → الإيرادات حسب القناة). Mixed English-Arabic gets garbled.\n"
                "• NEVER include identifiers with underscores like 'marketing_timeseries' — say "
                "'تحليل التسويق الزمني'.\n\n"
                "Tone: confident, articulate, broadcast-quality. Sentences flow naturally when spoken.\n"
                "Return ONLY the rewritten text — no quotes, no preamble, no markdown, no commentary."
            )
    elif tl.startswith('en'):
        target_norm = 'en-US'
        if style == 'casual':
            instruction = (
                "Rewrite the following text in friendly conversational English. "
                "Warm, approachable, like a colleague briefing you over coffee. "
                "Return ONLY the rewritten text — no quotes, no preamble, no markdown."
            )
        else:
            instruction = (
                "Rewrite the following text in polished, professional English suitable for an "
                "executive audio briefing. Confident, articulate, broadcast-quality narration — "
                "like a Bloomberg/CNBC analyst delivering insights. Use precise business vocabulary. "
                "Sentences should flow naturally when read aloud. "
                "Return ONLY the rewritten text — no quotes, no preamble, no markdown."
            )
    else:
        return Response(
            {'error': f"Unsupported target_language '{target}'. Use 'en' or 'ar-EG'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return Response({'error': 'GEMINI_API_KEY not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    client = genai.Client(api_key=api_key)
    prompt = f"{instruction}\n\n--- TEXT TO REWRITE ---\n{raw_text}"

    last_error = None
    started = time.time()
    for model in TRANSLATE_MODEL_CHAIN:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    max_output_tokens=2048,
                ),
            )
            out = (getattr(response, "text", "") or "").strip()
            if not out:
                last_error = f"empty response from {model}"
                continue
            # Strip surrounding quotes the model sometimes adds
            if out.startswith(('"', '"', '«', '«')) and out.endswith(('"', '"', '»', '»')):
                out = out[1:-1].strip()
            elapsed_ms = int((time.time() - started) * 1000)
            logger.info(
                f"[TRANSLATE-TTS] user={user_id[:8]} model={model} "
                f"target={target_norm} in={len(raw_text)}c out={len(out)}c"
            )
            log_id = None
            try:
                log_id = voice_logger.log_translate_request(
                    user_id=user_id,
                    source_text=raw_text,
                    output_text=out,
                    target_language=target_norm,
                    style=style,
                    model=model,
                    duration_ms=elapsed_ms,
                    status_code=200,
                )
            except Exception as e:
                logger.warning(f"[TRANSLATE-TTS] voice_logger failed: {e}")
            payload = {
                'text': out,
                'source_chars': len(raw_text),
                'output_chars': len(out),
                'model': model,
                'target_language': target_norm,
            }
            if log_id:
                payload['log_id'] = log_id
            return Response(payload)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[TRANSLATE-TTS] model {model} failed: {e}")
            continue

    try:
        voice_logger.log_translate_request(
            user_id=user_id,
            source_text=raw_text,
            output_text="",
            target_language=target_norm,
            style=style,
            model="(none)",
            duration_ms=int((time.time() - started) * 1000),
            status_code=502,
            error=last_error,
        )
    except Exception:
        pass

    return Response(
        {'error': f'Translation failed: {last_error}'},
        status=status.HTTP_502_BAD_GATEWAY,
    )


# ── Full analytical audio overview generation from dataset content ───────────

def _safe_json_parse(value):
    """Supabase can return JSONB columns as either dict or JSON string. Normalize."""
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            import json as _json
            return _json.loads(value)
        except Exception:
            return {}
    return {}


def _summarize_columns_for_overview(columns: list, limit: int = 15) -> str:
    """Build a compact text summary of columns + their key stats for an LLM prompt.

    Column names are humanized (snake_case → 'snake case') so the LLM doesn't
    parrot raw identifiers into the narration — TTS reads underscores literally
    as 'underscore' otherwise.
    """
    out = []
    for col in (columns or [])[:limit]:
        if not isinstance(col, dict):
            continue
        raw_name = col.get("column_name") or col.get("name") or "?"
        human_name = _humanize_identifier(raw_name)
        stats = _safe_json_parse(col.get("technical_stats")) or {}
        ai = _safe_json_parse(col.get("ai_profile")) or {}
        if not isinstance(stats, dict): stats = {}
        if not isinstance(ai, dict): ai = {}
        dtype = stats.get("type") or stats.get("dtype") or ""
        role = ai.get("column_role") or ai.get("role") or ""
        meaning = ai.get("semantic_meaning") or ""
        parts = [f"- {human_name}"]
        if dtype: parts.append(f"({dtype})")
        if role: parts.append(f"role={role}")
        if meaning and len(meaning) < 100: parts.append(f"meaning='{meaning}'")
        if stats.get("min") is not None and stats.get("max") is not None:
            parts.append(f"range={stats.get('min')}..{stats.get('max')}")
        if stats.get("mean") is not None:
            parts.append(f"mean={stats.get('mean')}")
        if stats.get("null_ratio") is not None:
            parts.append(f"nulls={stats.get('null_ratio'):.1%}" if isinstance(stats.get('null_ratio'), float) else f"nulls={stats.get('null_ratio')}")
        top = stats.get("top_5_samples") or stats.get("top_values")
        if top and isinstance(top, list):
            sample = ", ".join(_humanize_identifier(str(x)) for x in top[:3])
            parts.append(f"top=[{sample}]")
        out.append(" ".join(parts))
    return "\n".join(out)


def _summarize_charts_for_overview(charts: list[dict], limit: int = 10) -> str:
    """Build a chart blueprint summary; humanize all identifiers."""
    out = []
    for ch in (charts or [])[:limit]:
        title = _humanize_identifier(ch.get("title") or ch.get("name") or "")
        kind = _humanize_identifier(ch.get("type") or ch.get("chart_type") or "")
        cols_list = ch.get("columns") or []
        cols = ", ".join(_humanize_identifier(c) for c in cols_list[:3]) if isinstance(cols_list, list) else ""
        out.append(f"- [{kind}] {title}" + (f" using {cols}" if cols else ""))
    return "\n".join(out)


def _summarize_report_for_overview(report_sections: list[dict], limit_chars: int = 4000) -> str:
    out = []
    for s in (report_sections or []):
        t = s.get("title", "") or ""
        c = s.get("content", "") or s.get("text", "") or ""
        if c:
            out.append(f"## {t}\n{c.strip()}")
        if sum(len(x) for x in out) > limit_chars:
            break
    return "\n\n".join(out)[:limit_chars]


def _build_overview_prompt(
    dataset: dict,
    columns: list,
    language: str,
    style: str,
    duration_seconds: int,
    user_name: str = "",
) -> str:
    """Compose a strong prompt for Gemini to generate an analytical spoken overview."""
    gc = _safe_json_parse(dataset.get("global_context")) or {}
    if not isinstance(gc, dict): gc = {}
    file_info = _safe_json_parse(dataset.get("file_info")) or {}
    if not isinstance(file_info, dict): file_info = {}

    cat_det = _safe_json_parse(gc.get("category_detection")) or {}
    if not isinstance(cat_det, dict): cat_det = {}
    cat = (
        cat_det.get("resolved_category")
        or dataset.get("category")
        or dataset.get("category_hint")
        or "general"
    )
    name = file_info.get("filename") or dataset.get("file_name") or "Dataset"
    rows = file_info.get("rows") or dataset.get("row_count") or "?"
    cols_n = file_info.get("columns") or len(columns or [])

    step7 = _safe_json_parse(gc.get("step7")) or {}
    if not isinstance(step7, dict): step7 = {}
    title = step7.get("suggested_title") or "Business Dashboard"
    charts = step7.get("suggested_charts") or []
    if not isinstance(charts, list): charts = []
    step8 = _safe_json_parse(gc.get("step8")) or {}
    if not isinstance(step8, dict): step8 = {}
    sections = step8.get("sections") or []
    if not isinstance(sections, list): sections = []

    chart_summary = _summarize_charts_for_overview(charts)
    column_summary = _summarize_columns_for_overview(columns)
    report_summary = _summarize_report_for_overview(sections)

    # Language + style instructions
    lang_norm = (language or "en").lower()
    is_ar = lang_norm.startswith("ar")

    if is_ar and style == "formal":
        lang_instructions = (
            "Language required: POLISHED EGYPTIAN ARABIC (the register of Egyptian TV business "
            "anchors like CBC Business / Al-Mal TV, and senior Cairene corporate analysts).\n\n"
            "CRITICAL — DO NOT USE MODERN STANDARD ARABIC (Fusha / MSA / الفصحى).\n"
            "The narration must be Egyptian Arabic in structure, pronouns, particles, and verb "
            "patterns — NOT translated Fusha. Models default to MSA; resist that.\n\n"
            "Required Egyptian structure:\n"
            "• Pronouns: إحنا (not نحن), إنتو (not أنتم), هما (not هم)\n"
            "• Negation: مش / ما...ش (not ليس / لا)\n"
            "• Future: ه + verb (هنشوف، هيزيد) — NOT سوف or س-\n"
            "• Present continuous: بـ + verb (بنشوف، بيزيد، بتنمو)\n"
            "• Particles: دلوقتي (not الآن), عشان (not لأن/لكي), كمان (not أيضاً), بس (not فقط), زي (not مثل)\n"
            "• Question words / demonstratives: ايه, إزاي, ده / دي / دول (not هذا / هذه)\n"
            "• Verbs: شاف، عاوز، قدر، جاب — not رأى، يريد، استطاع، أحضر\n\n"
            "Business polish (refined, not street):\n"
            "• Use proper Arabic business vocabulary: المبيعات، الإيرادات، الأرباح، النمو، "
            "الحصة السوقية، مؤشرات الأداء، العملاء، التوزيع، المتوسط\n"
            "• Avoid pure street slang: يعني كده، خالص، اوعى\n\n"
            "TTS-SAFETY RULES (mandatory — the audio engine handles these badly otherwise):\n"
            "• NUMBERS: the prose around them is Egyptian, but NUMBER WORDS themselves MUST be "
            "Fusha in the ACCUSATIVE / GENITIVE form (the form Egyptians use anyway — '-een', not "
            "the formal '-oon'). NO TANWEEN on the counted noun.\n"
            "   ✓ CORRECT (Fusha accusative): اثنين, خمسين, ثلاثين, أربعين, ستين, مئتين, اثني عشر, "
            "خمسمئة, ثمانمئة, ألفين\n"
            "   ✗ WRONG (nominative — sounds stiff/formal): اثنان, خمسون, ثلاثون, أربعون, ستون, "
            "مئتان, اثنا عشر, ألفان\n"
            "   ✗ WRONG (Egyptian spelling): اتناشر, خمسمية, تمنمية, ميتين, ربعمية\n"
            "   ✓ 'حققنا خمسين ألف جنيه' (NOT 'خمسون ألف جنيهاً' — no -oon, no tanween)\n"
            "   ✓ 'بنخدم مئتين عميل' (NOT 'مئتان عميلاً')\n"
            "   ✓ 'الإيرادات وصلت لـ ثلاثين بالمئة' (NOT 'ثلاثون بالمئة')\n"
            "   ✓ 'عندنا اثني عشر منتج' (NOT 'اثنا عشر منتجاً')\n"
            "  Or leave raw digits — '15%', '$1500', '2025' — the pipeline auto-converts them "
            "to Fusha accusative words. Either approach works.\n"
            "  Approximations are fine in pure Egyptian: 'حوالي تلت السوق' for ~33%.\n"
            "• ALL ENGLISH WORDS — chart titles, KPI names, column names, business terms — MUST be "
            "TRANSLATED into Egyptian Arabic. The TTS engine cannot pronounce English mixed with "
            "Arabic. Translate every label, even if the source data uses English. Examples:\n"
            "   ✓ 'الإيرادات بحسب القناة التسويقية' (NOT 'Revenue by Marketing Channel')\n"
            "   ✓ 'المصاريف مقابل التحويلات' (NOT 'Spend vs Conversions')\n"
            "   ✓ 'مؤشرات الأداء' (NOT 'KPIs')\n"
            "   ✓ 'معدل العائد على الاستثمار' (NOT 'ROI')\n"
            "   ✓ 'لوحة المعلومات' (NOT 'dashboard')\n"
            "   ✓ 'تحليل المبيعات الزمني' (NOT 'sales timeseries')\n"
            "  Only keep an English term if there is genuinely no Arabic equivalent.\n"
            "• NEVER include identifiers with underscores or technical formatting like "
            "'marketing_timeseries' or 'spend_vs_conversions' — those are computer column names. "
            "Translate them to natural Arabic: 'تحليل التسويق الزمني', 'المصاريف مقابل التحويلات'.\n\n"
            "Target register examples (note: numbers and English are handled correctly):\n"
            "✓ 'لو بصينا على البيانات، هنلاقي إن المبيعات زادت بحوالي خمستاشر في المية الربع ده'\n"
            "✓ 'العملاء في القاهرة بيمثلوا تقريباً تلتين الإيرادات، وده مؤشر مهم'\n"
            "✓ 'مخطط الإيرادات حسب القناة التسويقية بيوضّح إن القناة الرقمية هي الأقوى'\n"
            "✗ AVOID: 'المبيعات ارتفعت بنسبة 15%' (digits in Arabic → Fusha pronunciation)\n"
            "✗ AVOID: 'مخطط Revenue by Marketing Channel بيوضّح...' (mixed English-Arabic → garbled)\n\n"
            "Tone: confident, analytical, broadcast-quality. Flows naturally when spoken aloud."
        )
    elif is_ar:
        lang_instructions = (
            "Language required: CASUAL EGYPTIAN ARABIC (Cairene), friendly and natural, "
            "like explaining to a colleague over coffee.\n\n"
            "DO NOT use Modern Standard Arabic / Fusha.\n"
            "Use Egyptian pronouns (إحنا، إنتو، هما), negation (مش، ما...ش), Egyptian particles "
            "(دلوقتي، عشان، فيه، مفيش، كمان، بس، أوي), Egyptian verbs (شاف، عاوز، قدر، جاب).\n"
            "Use everyday Egyptian phrasing — but stay clear and articulate, not slangy gibberish.\n\n"
            "TTS-SAFETY RULES (the audio engine handles these badly):\n"
            "• NUMBERS: prefer MSA Arabic words ('خمسة عشر بالمئة', 'ألفان'). Raw digits like '15%' "
            "or '2000' are also OK — the pipeline auto-converts them to MSA words before TTS.\n"
            "• TRANSLATE every English word (chart titles, column names, KPIs) into Egyptian Arabic. "
            "Mixed English-Arabic gets garbled by TTS. Say 'الإيرادات حسب القناة' not 'Revenue by Channel'.\n"
            "• NEVER include identifiers with underscores like 'marketing_timeseries' — translate to "
            "natural Arabic phrasing.\n"
        )
    elif style == "formal":
        lang_instructions = (
            "Language: polished, professional English suitable for an executive briefing. "
            "Confident, articulate, broadcast-quality narration — Bloomberg / McKinsey analyst tone. "
            "Use precise business vocabulary. Avoid filler words.\n\n"
            "TTS-SAFETY RULES:\n"
            "• NEVER include identifiers with underscores or technical formatting like "
            "'marketing_timeseries' or 'spend_vs_conversions' — the TTS engine reads underscores "
            "as the word 'underscore'. Always humanize: 'marketing time series', "
            "'spend versus conversions'.\n"
            "• Spell out abbreviations the first time you use them (KPI → 'key performance "
            "indicators', ROI → 'return on investment') so listeners follow easily.\n"
            "• For percentages and large numbers, prefer natural phrasing: 'roughly a quarter' "
            "instead of '25%', 'around three thousand' instead of '3,000'."
        )
    else:
        lang_instructions = (
            "Language: warm conversational English, like briefing a colleague over coffee.\n\n"
            "TTS-SAFETY: Never use identifiers with underscores ('marketing_timeseries' → "
            "'marketing time series'). Spell out percentages and counts naturally."
        )

    target_words = max(180, int(duration_seconds * 2.4))  # ~2.4 spoken words/sec
    min_words = max(140, int(duration_seconds * 1.9))

    # Personal greeting based on the user's name (first word only, capitalized).
    display_name = ""
    if user_name and isinstance(user_name, str):
        # Take the first name only, normalized
        first = user_name.strip().split()[0] if user_name.strip() else ""
        if first and len(first) <= 40:
            display_name = first

    if display_name:
        if is_ar and style == "formal":
            greeting_rules = (
                f"\nUSER PERSONALIZATION (mandatory):\n"
                f"- The listener's first name is: {display_name}\n"
                f"- OPEN with a warm, professional greeting using their name in Egyptian Arabic. "
                f"Examples (pick one or adapt): 'أهلاً يا {display_name}، خليني أوريك إيه اللي ظهر في البيانات دي', "
                f"'يا {display_name}، عندنا نتائج مهمة من تحليل البيانات النهاردة'.\n"
                f"- REFERENCE the name AT LEAST ONE MORE TIME mid-narration when emphasizing a key insight "
                f"or recommendation (e.g. 'وده اللي محتاج تركيز منك يا {display_name}').\n"
                f"- CLOSE with a brief personal sign-off addressing them (e.g. 'لو احتجت تفاصيل أكتر يا {display_name}، البيانات قدامك في الـ dashboard').\n"
            )
        elif is_ar:
            greeting_rules = (
                f"\nUSER PERSONALIZATION (mandatory):\n"
                f"- The listener's first name is: {display_name}\n"
                f"- Open with a friendly Egyptian greeting using their name: 'أهلاً يا {display_name}!' or similar.\n"
                f"- Mention the name AT LEAST ONCE more during the narration.\n"
                f"- Close with a friendly personal sign-off.\n"
            )
        else:
            greeting_rules = (
                f"\nUSER PERSONALIZATION (mandatory):\n"
                f"- The listener's first name is: {display_name}\n"
                f"- OPEN with a warm professional greeting using their name. "
                f"Examples: 'Hi {display_name}, here's what stood out in your data today.', "
                f"'{display_name}, the dataset reveals a few important things.'\n"
                f"- REFERENCE the name AT LEAST ONE MORE TIME mid-narration when emphasizing a key insight or recommendation.\n"
                f"- CLOSE with a brief personal sign-off (e.g. 'Hope that helps, {display_name}. Let me know if you want me to drill into any of these.').\n"
            )
    else:
        greeting_rules = "\nUSER PERSONALIZATION: (no user name provided — open with a generic warm greeting)\n"

    return f"""You are a senior business analyst producing a SPOKEN AUDIO OVERVIEW of a dataset for a non-technical executive audience.

{lang_instructions}

{greeting_rules}
TASK
Produce a flowing narration of approximately {min_words}-{target_words} words (about {duration_seconds} seconds when read aloud) that delivers genuine analytical value, not just a description of charts.

The overview MUST include, in this rough order:
1. **Personal greeting** — open warmly using the listener's name (if provided above).
2. **Opening hook** — what this dataset is about, why it matters (1-2 sentences). Mention category ({cat}) and scale ({rows} rows, {cols_n} columns).
3. **Headline insights** — the 3-5 most important findings WITH ACTUAL NUMBERS from the data summaries below (top categories, key ranges, dominant segments, growth indicators). Cite specific real values, percentages, top-N items.
4. **Notable patterns or anomalies** — anything unusual: high null ratios, outliers, dominant categories, surprising distributions, missing periods. At least one specific observation.
5. **Strategic takeaway** — 2-3 sentences on what the listener should DO with this information. Specific, actionable. Reference them by name here for emphasis.
6. **Closing** — what to look at next (which chart, which segment, which timeframe) + personal sign-off using their name.

CRITICAL RULES
- HIT the {min_words}-{target_words} word target. Don't cut short. A proper executive briefing has substance.
- Use REAL numbers from the data summaries below. Cite specific values, ranges, top categories.
- Do NOT just list chart names. Synthesize INSIGHTS from them.
- Output PURE NARRATIVE PROSE only — no bullet points, no markdown, no headings, no asterisks, no quotation marks around the whole output.
- Output ONLY the narration text (this will be fed directly to TTS).
- TTS-FRIENDLY OUTPUT (mandatory — re-read the language instructions above):
  • NEVER include identifiers with underscores or technical column names ('marketing_timeseries', 'spend_vs_conversions'). Always rewrite as natural language ('marketing timeseries', 'spend versus conversions', or the Arabic equivalent).
  • For numbers: prefer natural spoken forms over raw digits/percent signs ('around a quarter', 'three thousand customers', 'roughly two thirds').
  {('• Egyptian Arabic only: spell EVERY number out in Arabic words and translate EVERY English label into Arabic — mixed English-Arabic and bare digits both sound wrong via TTS.' if is_ar else '')}

DATA AVAILABLE FOR YOU
--- DATASET ---
Name: {name}
Category: {cat}
Rows: {rows} | Columns: {cols_n}
Suggested title: {title}

--- COLUMN PROFILES (first {min(len(columns or []), 15)} of {len(columns or [])}) ---
{column_summary or "(no column profiles available)"}

--- SUGGESTED CHARTS (top {min(len(charts), 10)}) ---
{chart_summary or "(no chart blueprints available)"}

--- EXISTING WRITTEN REPORT (use this as primary source of insights when available) ---
{report_summary or "(no narrative report available — synthesize directly from columns + charts above)"}

Now write the spoken overview. ONLY the narration text. Hit the word target. Reference the listener by name."""


@api_view(['POST'])
def dataset_audio_overview_view(request, dataset_id):
    """
    Generate a rich analytical audio overview narration for a dataset.
    POST { language: 'en'|'ar-EG', style: 'formal'|'casual', duration_seconds: int (15-120) }
    Returns: { text, language, style, duration_seconds, model, output_chars }
    """
    from google import genai
    from google.genai import types
    from .supabase_client import get_columns_metadata

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    # Reuse the translate cooldown — same Gemini budget
    now = time.time()
    with _translate_cooldowns_lock:
        last = _translate_cooldowns.get(user_id, 0.0)
        if now - last < TRANSLATE_COOLDOWN_S:
            return Response(
                {'error': 'Rate limited', 'retry_after_seconds': TRANSLATE_COOLDOWN_S},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        _translate_cooldowns[user_id] = now

    try:
        dataset = get_dataset(dataset_id)
    except Exception as e:
        logger.exception(f"[AUDIO-OVERVIEW] get_dataset({dataset_id}) failed: {e}")
        return Response({'error': f'Failed to load dataset: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if not dataset:
        return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
    if str(dataset.get("user_id", "")) != str(user_id):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    body = request.data if isinstance(request.data, dict) else {}
    language = (body.get('language') or 'en').strip()
    style = (body.get('style') or 'formal').strip().lower()
    if style not in ('formal', 'casual'):
        style = 'formal'
    try:
        duration_seconds = int(body.get('duration_seconds') or 75)
    except (TypeError, ValueError):
        duration_seconds = 75
    duration_seconds = max(15, min(240, duration_seconds))

    skip_voice_log = bool(body.get("skip_voice_log") or body.get("skipVoiceLog"))

    raw_name = body.get('user_name') or ''
    if isinstance(raw_name, str):
        user_name = raw_name.strip()[:80]
    else:
        user_name = ''

    try:
        columns = get_columns_metadata(dataset_id) or []
    except Exception as e:
        logger.warning(f"[AUDIO-OVERVIEW] get_columns_metadata failed: {e}")
        columns = []

    try:
        prompt = _build_overview_prompt(dataset, columns, language, style, duration_seconds, user_name)
    except Exception as e:
        logger.exception(f"[AUDIO-OVERVIEW] prompt build failed: {e}")
        return Response(
            {'error': f'Failed to build overview prompt: {e}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return Response({'error': 'GEMINI_API_KEY not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    client = genai.Client(api_key=api_key)
    last_error = None
    overview_started = time.time()
    for model in TRANSLATE_MODEL_CHAIN:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.6,
                    max_output_tokens=8192,
                ),
            )
            out = (getattr(response, "text", "") or "").strip()
            if not out:
                last_error = f"empty response from {model}"
                continue
            # Strip stray quotes/preambles
            if out.startswith(('"', '"', '«')) and out.endswith(('"', '"', '»')):
                out = out[1:-1].strip()
            # Remove any leftover markdown headers/bullets/asterisks just in case
            import re
            out = re.sub(r"^#+\s*", "", out, flags=re.MULTILINE)
            out = re.sub(r"^\s*[-*•]\s*", "", out, flags=re.MULTILINE)
            out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
            out = re.sub(r"\*(.+?)\*", r"\1", out)
            # TTS-safety backstop: if the model leaked any snake_case identifiers
            # despite the prompt rules, humanize them so the TTS engine doesn't
            # read 'marketing_timeseries' as 'marketing underscore timeseries'.
            out = _humanize_identifier(out)
            out = out.strip()

            elapsed_ms = int((time.time() - overview_started) * 1000)
            normalized_lang = 'ar-EG' if language.lower().startswith('ar') else 'en-US'
            logger.info(
                f"[AUDIO-OVERVIEW] user={user_id[:8]} dataset={dataset_id[:8]} "
                f"lang={language} style={style} dur={duration_seconds}s user_name={user_name or '-'} "
                f"model={model} chars={len(out)}"
            )
            log_id = None
            if not skip_voice_log:
                try:
                    log_id = voice_logger.log_overview_request(
                        user_id=user_id,
                        dataset_id=dataset_id,
                        output_text=out,
                        language=normalized_lang,
                        style=style,
                        duration_seconds=duration_seconds,
                        user_name=user_name,
                        model=model,
                        elapsed_ms=elapsed_ms,
                        status_code=200,
                    )
                except Exception as e:
                    logger.warning(f"[AUDIO-OVERVIEW] voice_logger failed: {e}")
            payload = {
                'text': out,
                'language': normalized_lang,
                'style': style,
                'duration_seconds': duration_seconds,
                'model': model,
                'output_chars': len(out),
            }
            if log_id:
                payload['log_id'] = log_id
            return Response(payload)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[AUDIO-OVERVIEW] model {model} failed: {e}")
            continue

    try:
        voice_logger.log_overview_request(
            user_id=user_id,
            dataset_id=dataset_id,
            output_text="",
            language='ar-EG' if language.lower().startswith('ar') else 'en-US',
            style=style,
            duration_seconds=duration_seconds,
            user_name=user_name,
            model="(none)",
            elapsed_ms=int((time.time() - overview_started) * 1000),
            status_code=502,
            error=last_error,
        )
    except Exception:
        pass

    return Response(
        {'error': f'Overview generation failed: {last_error}'},
        status=status.HTTP_502_BAD_GATEWAY,
    )


# ── Voice/TTS request logs (dev-facing audit trail) ──────────────────────────


@api_view(['GET'])
def voice_logs_list_view(request):
    """List the authenticated user's recent TTS / translate / overview requests.

    Query params:
      - kind: 'tts' | 'translate' | 'overview' (optional filter)
      - limit: int (1..200, default 50)
      - offset: int (default 0)
    """
    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err

    kind = request.GET.get("kind") or None
    if kind not in (None, "tts", "translate", "overview", "audio_overview"):
        return Response({'error': "kind must be one of: tts, translate, overview, audio_overview"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        limit = max(1, min(200, int(request.GET.get("limit", 50))))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except ValueError:
        offset = 0

    entries = voice_logger.list_entries(user_id, kind=kind, limit=limit, offset=offset)
    summary = voice_logger.usage_summary(user_id)
    return Response({
        "entries": entries,
        "count": len(entries),
        "summary": summary,
    })


@api_view(['GET'])
def voice_log_audio_view(request, entry_id):
    """Stream back the MP3 audio captured for one TTS entry (if it was saved)."""
    from django.http import HttpResponse, HttpResponseNotFound

    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err
    audio_bytes = voice_logger.read_audio_bytes(user_id, entry_id)
    if not audio_bytes:
        return HttpResponseNotFound('Audio not found for this entry')
    entry = voice_logger.get_entry(user_id, entry_id) or {}
    audio_fmt = ""
    extra = entry.get("extra") or {}
    if isinstance(extra, dict):
        audio_fmt = (extra.get("audio_format") or "").lower()
    if not audio_fmt and entry.get("audio_path"):
        audio_fmt = str(entry["audio_path"]).rsplit(".", 1)[-1].lower()
    content_type = "audio/wav" if audio_fmt == "wav" else "audio/mpeg"
    file_ext = audio_fmt or "mp3"
    resp = HttpResponse(audio_bytes, content_type=content_type)
    resp['Cache-Control'] = 'private, max-age=3600'
    resp['Content-Disposition'] = f'inline; filename="voice-{entry_id}.{file_ext}"'
    return resp


@api_view(['DELETE'])
def voice_log_delete_view(request, entry_id):
    """Delete a single voice log entry (and its audio file)."""
    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err
    ok = voice_logger.delete_entry(user_id, entry_id)
    return Response({"deleted": ok}, status=(status.HTTP_200_OK if ok else status.HTTP_404_NOT_FOUND))


@api_view(['DELETE'])
def voice_logs_clear_view(request):
    """Wipe ALL voice log entries for the authenticated user."""
    user_id, auth_err = _authenticate_request(request)
    if auth_err:
        return auth_err
    removed = voice_logger.clear_user(user_id)
    return Response({"removed": removed})
