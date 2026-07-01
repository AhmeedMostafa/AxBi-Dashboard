from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _parse_json_maybe(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def build_dataset_context(dataset_id: str) -> dict:
    """
    Gather every analytic output for a dataset into one context dict.
    This is the single source of truth used by signal detectors and Gemini.
    Also reused by the chatbot when it's built.
    """
    from api.supabase_client import (
        get_dataset,
        get_columns_metadata,
        get_forecast_results,
        get_forecast_result_by_id,
    )

    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError(f"Dataset {dataset_id} not found")

    columns_meta  = get_columns_metadata(dataset_id)
    forecasts     = get_forecast_results(dataset_id, limit=1)
    latest_forecast = None
    if forecasts:
        try:
            latest_forecast = get_forecast_result_by_id(forecasts[0]["id"])
        except Exception:
            latest_forecast = forecasts[0]

    global_context = _parse_json_maybe(dataset.get("global_context")) or {}
    file_info      = _parse_json_maybe(dataset.get("file_info")) or {}

    return {
        "dataset_id":   dataset_id,
        "category":     (
            (global_context.get("category_detection") or {}).get("resolved_category")
            or dataset.get("category")
            or dataset.get("category_hint")
            or "general"
        ),
        "filename":     dataset.get("filename") or dataset.get("file_name") or "dataset",
        "row_count":    file_info.get("rows") or file_info.get("row_count"),
        "columns":      columns_meta or [],
        "step7_charts": (global_context.get("step7") or {}).get("suggested_charts", []),
        "step8":        _parse_json_maybe(global_context.get("step8")),
        "segmentation": _parse_json_maybe(global_context.get("segmentation")),
        "forecast":     latest_forecast,
        "global_context": global_context,
    }
