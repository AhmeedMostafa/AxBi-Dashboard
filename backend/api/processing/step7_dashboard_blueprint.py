"""
Step 7: Generate dashboard blueprint from full column metadata.

This module asks Gemini for a final dashboard plan and then validates/
normalizes the output into a strict, frontend-safe JSON structure.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, UTC
from typing import Any

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - depends on optional runtime dependency
    genai = None
    types = None


logger = logging.getLogger(__name__)

ALLOWED_DATASET_TYPES = {"HR", "Marketing", "Operations", "Sales", "Business"}
ALLOWED_CHART_TYPES = {
    "bar", "horizontal_bar", "line", "area", "pie", "donut", "treemap",
    "stacked_bar", "funnel", "kpi_card", "radial", "histogram", "pareto", "combo",
}
CHART_COVERAGE_GOALS = ("kpi", "trend", "comparison", "distribution", "contribution")
CATEGORY_CHART_MENU = {
    "Sales": [
        {"chart_type": "kpi_card", "goal": "total_revenue", "x_hint": None, "y_hint": "measure"},
        {"chart_type": "line", "goal": "revenue_trend", "x_hint": "date", "y_hint": "measure"},
        {"chart_type": "bar", "goal": "sales_by_category", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "donut", "goal": "market_share", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "horizontal_bar", "goal": "top_products", "x_hint": "dimension", "y_hint": "measure"},
    ],
    "HR": [
        {"chart_type": "kpi_card", "goal": "headcount", "x_hint": None, "y_hint": "measure"},
        {"chart_type": "bar", "goal": "salary_by_department", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "treemap", "goal": "headcount_by_department", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "line", "goal": "hiring_timeline", "x_hint": "date", "y_hint": "measure"},
        {"chart_type": "horizontal_bar", "goal": "attrition_by_team", "x_hint": "dimension", "y_hint": "measure"},
    ],
    "Marketing": [
        {"chart_type": "kpi_card", "goal": "total_leads", "x_hint": None, "y_hint": "measure"},
        {"chart_type": "bar", "goal": "clicks_by_campaign", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "combo", "goal": "spend_and_growth", "x_hint": "date", "y_hint": "measure"},
        {"chart_type": "donut", "goal": "channel_distribution", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "funnel", "goal": "conversion_funnel", "x_hint": "dimension", "y_hint": "measure"},
    ],
    "Operations": [
        {"chart_type": "kpi_card", "goal": "total_orders", "x_hint": None, "y_hint": "measure"},
        {"chart_type": "line", "goal": "inventory_levels", "x_hint": "date", "y_hint": "measure"},
        {"chart_type": "bar", "goal": "delay_by_carrier", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "pie", "goal": "status_breakdown", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "combo", "goal": "throughput_vs_trend", "x_hint": "date", "y_hint": "measure"},
    ],
    "Business": [
        {"chart_type": "kpi_card", "goal": "primary_metric", "x_hint": None, "y_hint": "measure"},
        {"chart_type": "line", "goal": "metric_over_time", "x_hint": "date", "y_hint": "measure"},
        {"chart_type": "bar", "goal": "metric_by_dimension", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "donut", "goal": "dimension_share", "x_hint": "dimension", "y_hint": "measure"},
        {"chart_type": "treemap", "goal": "dimension_contribution", "x_hint": "dimension", "y_hint": "measure"},
    ],
}

_client = None

GEMINI_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)


def run_step7(
    dataset: dict,
    columns_metadata: list[dict],
    step6_context: dict | None = None,
) -> dict:
    """
    Build a validated dashboard blueprint.

    Returns:
        {
          "status": "completed",
          "generated_at": "...",
          "dataset_type": "...",
          "global_confidence": 0.0..1.0,
          "suggested_title": "...",
          "suggested_charts": [...],
          "warnings": [...]
        }
    """
    _ensure_gemini()

    prompt = _build_prompt(dataset, columns_metadata, step6_context)
    raw = _call_gemini(prompt)
    parse_error: ValueError | None = None
    try:
        parsed = _parse_json_object(raw)
    except ValueError:
        logger.warning("Step 7: first parse failed, retrying Gemini once in strict JSON mode")
        try:
            raw_retry = _call_gemini(prompt, strict_json=True)
            parsed = _parse_json_object(raw_retry)
        except ValueError as e:
            parse_error = e
            logger.error(
                "Step 7: AI JSON parsing failed after retry. Falling back to deterministic blueprint. Error: %s",
                e,
            )
            parsed = _build_parse_failure_seed(dataset)
    normalized, warnings = _normalize_blueprint(parsed, columns_metadata)
    if parse_error is not None:
        warnings.append(f"ai_json_invalid_used_fallback_blueprint:{parse_error}")

    return {
        "status": "completed",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_type": normalized["dataset_type"],
        "global_confidence": normalized["global_confidence"],
        "suggested_title": normalized["suggested_title"],
        "suggested_charts": normalized["suggested_charts"],
        "warnings": warnings,
    }


def _build_parse_failure_seed(dataset: dict) -> dict:
    category_hint = str(dataset.get("category_hint") or "").strip()
    title_prefix = category_hint.title() if category_hint else "Business"
    return {
        "dataset_type": category_hint,
        "global_confidence": 0.35,
        "suggested_title": f"{title_prefix} Dashboard",
        "suggested_charts": [],
    }


def _ensure_gemini():
    global _client
    if _client is None:
        if genai is None:
            raise ValueError("google-genai is not installed. Install the dependency to run Step 7.")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)


def _build_prompt(dataset: dict, columns_metadata: list[dict], step6_context: dict | None) -> str:
    file_info = _parse_json_maybe(dataset.get("file_info"))
    global_context = _parse_json_maybe(dataset.get("global_context"))

    step6_summary = {}
    if isinstance(step6_context, dict):
        step6_summary = step6_context
    elif isinstance(global_context, dict):
        step6_summary = _parse_json_maybe(global_context.get("step6"))

    columns_summary = []
    for col in columns_metadata:
        ai_profile = _parse_json_maybe(col.get("ai_profile"))
        technical_stats = _parse_json_maybe(col.get("technical_stats"))
        # Send only essential fields — omitting full technical_stats prevents
        # the prompt + response from exceeding Gemini's output token limit.
        slim_stats: dict = {}
        if isinstance(technical_stats, dict):
            for k in ("min", "max", "mean", "top_5_samples"):
                if k in technical_stats:
                    slim_stats[k] = technical_stats[k]
        slim_profile: dict = {}
        if isinstance(ai_profile, dict):
            for k in ("role", "semantic_meaning", "column_confidence"):
                if k in ai_profile:
                    slim_profile[k] = ai_profile[k]
        columns_summary.append(
            {
                "column_name": col.get("clean_name") or col.get("original_name"),
                "data_type": col.get("data_type"),
                "is_primary_metric": bool(col.get("is_primary_metric", False)),
                "ai_profile": slim_profile,
                "stats": slim_stats,
            }
        )

    numeric_count = sum(1 for c in columns_metadata if str(c.get("data_type", "")).lower() == "numeric")
    datetime_count = sum(
        1 for c in columns_metadata
        if str(c.get("data_type", "")).lower() == "datetime"
        or str((_parse_json_maybe(c.get("ai_profile")) or {}).get("role", "")).lower() in {"time", "date"}
    )
    categorical_count = max(0, len(columns_metadata) - numeric_count - datetime_count)
    dataset_shape = {
        "numeric_count": numeric_count,
        "datetime_count": datetime_count,
        "categorical_count": categorical_count,
        "column_count": len(columns_metadata),
    }

    dataset_context = {
        "dataset_id": dataset.get("id"),
        "file_name": dataset.get("file_name"),
        "category_hint": dataset.get("category_hint"),
        "file_info": file_info,
        "step6_summary": step6_summary,
        "columns": columns_summary,
        "category_chart_menu": CATEGORY_CHART_MENU,
        "dataset_shape": dataset_shape,
    }

    return (
        "You are a BI dashboard architect AI.\n"
        "Classify the dataset and design useful charts using the category chart menu.\n"
        "The chart plan must follow these coverage goals in order: "
        f"{', '.join(CHART_COVERAGE_GOALS)}.\n\n"
        f"{json.dumps(dataset_context, indent=2, default=str)}\n\n"
        "Return ONLY one valid JSON object with EXACT keys:\n"
        '1) "dataset_type": one of ["HR", "Marketing", "Operations", "Sales", "Business"]. '
        'Use "Business" only when the data clearly does not fit a single department.\n'
        '2) "global_confidence": float 0.0 to 1.0 confidence about the dataset_type\n'
        '3) "suggested_title": professional dashboard title\n'
        '4) "suggested_charts": array of exactly 5 objects when feasible '
        '(minimum 3 if data constraints prevent 5).\n'
        "Each chart object must contain EXACT keys:\n"
        '   - "chart_type": one of ["bar","horizontal_bar","line","area","pie","donut","treemap","stacked_bar","funnel","kpi_card","radial","histogram","pareto","combo"]\n'
        '   - "title": short chart title\n'
        '   - "x_axis": column name or null\n'
        '   - "y_axis": column name or null\n'
        '   - "columns": array of involved column names\n'
        '   - "reason": 1-2 sentence explanation of WHY this chart type best suits this data '
        '(e.g. "Area chart chosen to show cumulative revenue trend over time" '
        'or "Donut chart to display proportional category breakdown")\n'
        "Chart type selection guide:\n"
        '  - "line": time-series trends, sequential data\n'
        '  - "area": time-series with emphasis on volume/magnitude\n'
        '  - "bar": vertical categorical comparisons, rankings\n'
        '  - "horizontal_bar": ranked comparison when category names are long or there are many categories (x = dimension, y = numeric measure)\n'
        '  - "stacked_bar": categorical breakdown with sub-categories\n'
        '  - "pie": proportions of a whole (use for <=6 categories)\n'
        '  - "donut": same as pie but for cleaner aesthetic\n'
        '  - "treemap": part-to-whole contribution when there are MANY categories (x = dimension, y = numeric measure)\n'
        '  - "funnel": ordered stage drop-off / conversion (x = stage dimension, y = numeric measure); ideal for Marketing & Sales pipelines\n'
        '  - "combo": one dimension/date with a measure shown as bars PLUS a growth/secondary trend line (x = dimension or date, y = numeric measure)\n'
        '  - "kpi_card": single aggregate metric (sum, avg, count)\n'
        '  - "radial": gauge for a single ratio/percentage or progress metric (x = null, y = numeric measure)\n'
        '  - "histogram": distribution of ONE numeric column (x = numeric column, y = null)\n'
        '  - "pareto": ranked categorical contribution with cumulative % (x = dimension, y = numeric measure); use when one dimension dominates\n'
        "Do NOT use scatter / dot plots — they are not supported.\n"
        "Prefer chart intents from category_chart_menu[dataset_type].\n"
        "Use dataset_shape to vary the plan: prefer histogram when numeric_count>=2 and datetime_count==0; "
        "prefer pareto when categorical_count>=1 and a dimension likely follows an 80/20 split; "
        "prefer treemap when categorical cardinality is high; prefer horizontal_bar when category labels are long; "
        "prefer funnel for conversion/stage data; prefer combo to pair a measure with its growth trend over time. "
        "Do not return the same 5 chart types for every dataset.\n"
        "No markdown fences. No extra keys."
    )


def _call_gemini(prompt: str, strict_json: bool = False) -> str:
    """Send prompt to Gemini with automatic model fallback.

    Tries each model in GEMINI_MODEL_CHAIN in order. If a model
    fails (rate-limit, deprecation, outage, etc.) the next one is
    attempted. Only raises if every model in the chain fails.
    """
    global _client
    if _client is None:
        _ensure_gemini()
    if types is None:
        raise ValueError("google-genai is not installed. Install the dependency to run Step 7.")

    config_kwargs = {
        "temperature": 0.2 if not strict_json else 0.0,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",  # always enforce JSON mode
    }

    last_error: Exception | None = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            logger.info("Step 7: Gemini call succeeded with model %s", model_name)
            return response.text or ""
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Step 7: model %s failed (%s: %s), trying next fallback",
                model_name,
                type(exc).__name__,
                exc,
            )

    raise ValueError(
        f"All Gemini models failed. Last error: {last_error}"
    )


def _parse_json_object(raw: str) -> dict:
    cleaned = _clean_response_text(raw)
    parsed = _parse_json_object_resilient(cleaned, raw)
    if not isinstance(parsed, dict):
        raise ValueError("Step 7 AI response must be a JSON object")
    return parsed


def _clean_response_text(raw_response: str) -> str:
    cleaned = (raw_response or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_json_object_resilient(cleaned: str, raw_response: str) -> dict:
    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)

    extracted = _extract_json_object_block(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)

    repaired_cleaned = _repair_json_text(cleaned)
    if repaired_cleaned and repaired_cleaned != cleaned:
        candidates.append(repaired_cleaned)

    if extracted:
        repaired_extracted = _repair_json_text(extracted)
        if repaired_extracted and repaired_extracted not in candidates:
            candidates.append(repaired_extracted)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            last_error = ValueError("Step 7 AI response must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e

    if repair_json is not None:
        try:
            repaired = repair_json(raw_response or "", return_objects=True)
            if isinstance(repaired, dict):
                return repaired
            if isinstance(repaired, list) and len(repaired) == 1 and isinstance(repaired[0], dict):
                return repaired[0]
        except Exception as e:
            logger.debug("json_repair also failed: %s", e)

    logger.error(
        "Step 7 AI returned invalid JSON after repair attempts: %s\nRaw response (first 600 chars): %s",
        last_error,
        (raw_response or "")[:600],
    )
    raise ValueError(f"Step 7 AI returned invalid JSON: {last_error}")


def _extract_json_object_block(text: str) -> str:
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1].strip()


def _repair_json_text(text: str) -> str:
    if not text:
        return text

    repaired = text
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"')
    repaired = repaired.replace("\u2018", "'").replace("\u2019", "'")
    repaired = re.sub(r"/\*.*?\*/", "", repaired, flags=re.DOTALL)
    repaired = re.sub(r"(^|\s)//.*?$", r"\1", repaired, flags=re.MULTILINE)
    repaired = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', repaired)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    return repaired.strip()


def _normalize_blueprint(raw: dict, columns_metadata: list[dict]) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    columns_index = _build_columns_index(columns_metadata)

    dataset_type = _normalize_dataset_type(raw.get("dataset_type"))
    if dataset_type is None:
        dataset_type = "Business"
        warnings.append("dataset_type_defaulted_to_business")

    confidence = _clamp_float(raw.get("global_confidence"), default=0.5)

    title = str(raw.get("suggested_title") or "").strip()
    if not title:
        title = f"{dataset_type} Performance Dashboard"
        warnings.append("suggested_title_missing_default_used")

    charts_raw = raw.get("suggested_charts")
    if not isinstance(charts_raw, list):
        charts_raw = []
        warnings.append("suggested_charts_invalid_type")

    charts: list[dict] = []
    existing_keys: set[tuple[str, str | None, str | None]] = set()

    def _add_chart_if_valid(candidate: dict | None):
        if not isinstance(candidate, dict):
            return
        normalized = _normalize_chart(candidate, columns_index, warnings)
        if normalized is None:
            return
        key = (normalized["chart_type"], normalized["x_axis"], normalized["y_axis"])
        if key in existing_keys:
            return
        existing_keys.add(key)
        charts.append(normalized)

    for chart in charts_raw:
        _add_chart_if_valid(chart)
        if len(charts) >= 5:
            break

    if len(charts) < 5:
        category_fallback = _build_category_fallback_charts(dataset_type, columns_index)
        for c in category_fallback:
            _add_chart_if_valid(c)
            if len(charts) >= 5:
                break
        if category_fallback:
            warnings.append("category_fallback_charts_added")

    if len(charts) < 5:
        fallback = _build_fallback_charts(columns_index)
        for c in fallback:
            _add_chart_if_valid(c)
            if len(charts) >= 5:
                break
        if fallback:
            warnings.append("fallback_charts_added")

    if len(charts) < 5:
        generic = _build_generic_kpi_fallback(columns_index)
        for c in generic:
            _add_chart_if_valid(c)
            if len(charts) >= 5:
                break
        if generic:
            warnings.append("generic_kpi_fallback_added")

    if len(charts) > 5:
        charts = charts[:5]
        warnings.append("charts_trimmed_to_max_5")

    if len(charts) < 3:
        warnings.append("less_than_3_charts_after_validation")

    normalized = {
        "dataset_type": dataset_type,
        "global_confidence": confidence,
        "suggested_title": title,
        "suggested_charts": charts,
    }
    return normalized, warnings


def _build_columns_index(columns_metadata: list[dict]) -> dict:
    by_name = {}
    numeric = []
    datetime_cols = []
    text_like = []
    primary_metrics = []

    for col in columns_metadata:
        clean = str(col.get("clean_name") or "").strip()
        original = str(col.get("original_name") or "").strip()
        data_type = str(col.get("data_type") or "unknown").strip().lower()
        is_primary = bool(col.get("is_primary_metric", False))
        ai_profile = _parse_json_maybe(col.get("ai_profile")) or {}
        role = str(ai_profile.get("role") or "unknown").strip().lower()
        role = {"time": "date", "geo": "geographic", "category": "dimension"}.get(role, role)

        meta = {
            "clean_name": clean,
            "original_name": original,
            "data_type": data_type,
            "role": role,
            "is_primary_metric": is_primary,
        }

        for name in [clean, original]:
            if name:
                by_name[name] = meta
                by_name[name.lower()] = meta

        if data_type == "numeric":
            numeric.append(clean or original)
            if is_primary:
                primary_metrics.append(clean or original)
        if data_type == "datetime" or role == "date":
            datetime_cols.append(clean or original)
        if data_type in {"text", "boolean"} or role in {"dimension", "descriptive", "geographic", "id"}:
            text_like.append(clean or original)

    return {
        "by_name": by_name,
        "numeric": _dedup(numeric),
        "datetime": _dedup(datetime_cols),
        "text_like": _dedup(text_like),
        "primary_metrics": _dedup(primary_metrics),
    }


def _normalize_chart(chart: Any, index: dict, warnings: list[str]) -> dict | None:
    if not isinstance(chart, dict):
        warnings.append("chart_skipped_not_object")
        return None

    chart_type = str(chart.get("chart_type") or "").strip().lower()
    if chart_type not in ALLOWED_CHART_TYPES:
        warnings.append(f"chart_skipped_invalid_type:{chart_type or 'empty'}")
        return None

    title = str(chart.get("title") or "").strip()
    reason = str(chart.get("reason") or "").strip()
    if not title:
        title = "Untitled Chart"
    if not reason:
        reason = "Useful view of key dataset relationships."

    x_axis = _resolve_col_name(chart.get("x_axis"), index)
    y_axis = _resolve_col_name(chart.get("y_axis"), index)

    cols = chart.get("columns")
    if not isinstance(cols, list):
        cols = []
    resolved_cols = []
    for c in cols:
        resolved = _resolve_col_name(c, index)
        if resolved:
            resolved_cols.append(resolved)

    if x_axis and x_axis not in resolved_cols:
        resolved_cols.append(x_axis)
    if y_axis and y_axis not in resolved_cols:
        resolved_cols.append(y_axis)

    if chart_type == "kpi_card":
        if not y_axis:
            y_axis = _pick_primary_metric(index)
        x_axis = None
        if not y_axis:
            if not resolved_cols:
                warnings.append("chart_skipped_kpi_no_metric_or_columns")
                return None

    y_axis_secondary = None

    # Horizontal bars put the category on the y-axis and the measure on the x-axis,
    # which the model frequently emits swapped. If x is numeric and y is not, swap
    # them so aggregation groups by the category and sums the measure.
    if chart_type == "horizontal_bar" and x_axis and y_axis:
        if _is_numeric_col(x_axis, index) and not _is_numeric_col(y_axis, index):
            x_axis, y_axis = y_axis, x_axis
            warnings.append("horizontal_bar_axes_swapped")

    if chart_type in {"bar", "line", "horizontal_bar", "combo"}:
        if not x_axis or not y_axis:
            warnings.append(f"chart_skipped_missing_axes:{chart_type}")
            return None

    if chart_type == "radial":
        if not y_axis:
            y_axis = _pick_primary_metric(index)
        x_axis = None
        if not y_axis or not _is_numeric_col(y_axis, index):
            warnings.append("chart_skipped_radial_non_numeric_metric")
            return None

    if chart_type == "combo":
        if not _is_numeric_col(y_axis, index):
            warnings.append("chart_skipped_combo_non_numeric_y")
            return None
        # Pick an optional secondary numeric measure for the trend line.
        for c in resolved_cols:
            if c != x_axis and c != y_axis and _is_numeric_col(c, index):
                y_axis_secondary = c
                break

    if chart_type in {"pie", "donut", "treemap", "funnel"}:
        if not x_axis:
            warnings.append(f"chart_skipped_missing_axes:{chart_type}")
            return None
        if y_axis and (not _is_numeric_col(y_axis, index) or _is_id_col(y_axis, index)):
            warnings.append(f"chart_{chart_type}_y_axis_invalid_dropped")
            resolved_cols = [c for c in resolved_cols if c != y_axis]
            y_axis = None

    if chart_type == "histogram":
        if not x_axis or not _is_numeric_col(x_axis, index):
            warnings.append("chart_skipped_histogram_requires_numeric_x")
            return None
        y_axis = None

    if chart_type == "pareto":
        if not x_axis or not y_axis:
            warnings.append("chart_skipped_pareto_missing_axes")
            return None
        if not _is_numeric_col(y_axis, index):
            warnings.append("chart_skipped_pareto_non_numeric_y")
            return None

    if chart_type == "line" and x_axis and not _is_datetime_col(x_axis, index):
        warnings.append("chart_line_x_axis_not_datetime")

    if chart_type == "kpi_card" and y_axis and not _is_numeric_col(y_axis, index):
        warnings.append("chart_skipped_kpi_non_numeric_metric")
        return None

    result = {
        "chart_type": chart_type,
        "title": title,
        "x_axis": x_axis,
        "y_axis": y_axis,
        "reason": reason,
        "columns": _dedup(resolved_cols),
    }
    if y_axis_secondary:
        result["y_axis_secondary"] = y_axis_secondary
    return result


def _build_fallback_charts(index: dict) -> list[dict]:
    metric = _pick_primary_metric(index)
    date_col = index["datetime"][0] if index["datetime"] else None
    dimension = index["text_like"][0] if index["text_like"] else None
    numeric = index["numeric"]

    fallback = []

    if metric:
        fallback.append(
            {
                "chart_type": "kpi_card",
                "title": f"Primary KPI: {metric}",
                "x_axis": None,
                "y_axis": metric,
                "reason": "Highlights the most important metric at a glance.",
                "columns": [metric],
            }
        )

    if date_col and metric:
        fallback.append(
            {
                "chart_type": "line",
                "title": f"{metric} Trend Over Time",
                "x_axis": date_col,
                "y_axis": metric,
                "reason": "Shows trend changes and seasonality over time.",
                "columns": [date_col, metric],
            }
        )

    if dimension and metric:
        fallback.append(
            {
                "chart_type": "bar",
                "title": f"{metric} by {dimension}",
                "x_axis": dimension,
                "y_axis": metric,
                "reason": "Compares metric performance across categories.",
                "columns": [dimension, metric],
            }
        )
        fallback.append(
            {
                "chart_type": "pie",
                "title": f"{metric} Share by {dimension}",
                "x_axis": dimension,
                "y_axis": metric,
                "reason": "Shows contribution breakdown across categories.",
                "columns": [dimension, metric],
            }
        )

    if date_col and metric:
        fallback.append(
            {
                "chart_type": "combo",
                "title": f"{metric} & Growth Trend",
                "x_axis": date_col,
                "y_axis": metric,
                "reason": "Bars show the measure over time while the line tracks its growth trend.",
                "columns": [date_col, metric],
            }
        )
    elif dimension and metric:
        fallback.append(
            {
                "chart_type": "treemap",
                "title": f"{metric} Contribution by {dimension}",
                "x_axis": dimension,
                "y_axis": metric,
                "reason": "Treemap shows part-to-whole contribution across many categories.",
                "columns": [dimension, metric],
            }
        )

    if len(numeric) >= 1 and not date_col:
        fallback.append(
            {
                "chart_type": "histogram",
                "title": f"Distribution of {numeric[0]}",
                "x_axis": numeric[0],
                "y_axis": None,
                "reason": "Histogram reveals the spread and skew of the key numeric field.",
                "columns": [numeric[0]],
            }
        )

    if dimension and metric:
        fallback.append(
            {
                "chart_type": "pareto",
                "title": f"Pareto of {metric} by {dimension}",
                "x_axis": dimension,
                "y_axis": metric,
                "reason": "Pareto highlights the vital few categories driving most of the value.",
                "columns": [dimension, metric],
            }
        )

    return fallback


def _build_category_fallback_charts(dataset_type: str, index: dict) -> list[dict]:
    metric = _pick_primary_metric(index)
    secondary_metric = _pick_secondary_metric(index, metric)
    date_col = index["datetime"][0] if index["datetime"] else None
    dimension = _pick_dimension_col(index)

    candidates_by_type = {
        "kpi_card": {
            "chart_type": "kpi_card",
            "title": f"{dataset_type} KPI Overview",
            "x_axis": None,
            "y_axis": metric,
            "reason": "High-level KPI summary for quick executive monitoring.",
            "columns": [c for c in [metric] if c],
        },
        "line": {
            "chart_type": "line",
            "title": f"{dataset_type} Trend Over Time",
            "x_axis": date_col,
            "y_axis": metric,
            "reason": "Tracks growth and trend changes over time.",
            "columns": [c for c in [date_col, metric] if c],
        },
        "bar": {
            "chart_type": "bar",
            "title": f"{dataset_type} Performance by Segment",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Compares performance across key categories.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "pie": {
            "chart_type": "pie",
            "title": f"{dataset_type} Distribution Share",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Shows proportional distribution across categories.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "donut": {
            "chart_type": "donut",
            "title": f"{dataset_type} Distribution Share",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Donut shows proportional distribution with a clean center.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "horizontal_bar": {
            "chart_type": "horizontal_bar",
            "title": f"{dataset_type} Ranking by Segment",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Ranks categories by the key measure; ideal for long category names.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "treemap": {
            "chart_type": "treemap",
            "title": f"{dataset_type} Contribution Breakdown",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Treemap shows part-to-whole contribution across many categories.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "funnel": {
            "chart_type": "funnel",
            "title": f"{dataset_type} Stage Funnel",
            "x_axis": dimension,
            "y_axis": metric,
            "reason": "Funnel reveals stage-by-stage drop-off and conversion.",
            "columns": [c for c in [dimension, metric] if c],
        },
        "combo": {
            "chart_type": "combo",
            "title": f"{dataset_type} Measure & Growth Trend",
            "x_axis": date_col or dimension,
            "y_axis": metric,
            "reason": "Bars show the measure while the line tracks its growth trend.",
            "columns": [c for c in [date_col or dimension, metric, secondary_metric] if c],
            **({"y_axis_secondary": secondary_metric} if secondary_metric else {}),
        },
        "radial": {
            "chart_type": "radial",
            "title": f"{dataset_type} Headline Gauge",
            "x_axis": None,
            "y_axis": metric,
            "reason": "Radial gauge shows the headline metric relative to its peak.",
            "columns": [c for c in [metric] if c],
        },
    }

    ordered_types = [entry["chart_type"] for entry in CATEGORY_CHART_MENU.get(dataset_type, [])]
    if not ordered_types:
        ordered_types = ["kpi_card", "line", "bar", "donut", "horizontal_bar"]

    return [candidates_by_type[t] for t in ordered_types if t in candidates_by_type]


def _build_generic_kpi_fallback(index: dict) -> list[dict]:
    names = _all_column_names(index)
    metric = _pick_primary_metric(index)
    fallback = []

    if metric:
        fallback.append(
            {
                "chart_type": "kpi_card",
                "title": f"Primary KPI: {metric}",
                "x_axis": None,
                "y_axis": metric,
                "reason": "Highlights the strongest measurable signal in this dataset.",
                "columns": [metric],
            }
        )

    for name in names:
        if len(fallback) >= 3:
            break
        fallback.append(
            {
                "chart_type": "kpi_card",
                "title": f"Key Signal: {name}",
                "x_axis": None,
                "y_axis": None,
                "reason": "Highlights a key field for quick monitoring via card-level summaries.",
                "columns": [name],
            }
        )
    return fallback


def _normalize_dataset_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    mapping = {
        "hr": "HR",
        "human resources": "HR",
        "marketing": "Marketing",
        "operation": "Operations",
        "operations": "Operations",
        "sales": "Sales",
        "business": "Business",
        "general": "Business",
        "other": "Business",
    }
    mapped = mapping.get(text)
    if mapped in ALLOWED_DATASET_TYPES:
        return mapped
    return None


def _resolve_col_name(value: Any, index: dict) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    by_name = index["by_name"]
    meta = by_name.get(text) or by_name.get(text.lower())
    if not meta:
        return None
    return meta["clean_name"] or meta["original_name"] or text


def _is_numeric_col(col_name: str, index: dict) -> bool:
    meta = index["by_name"].get(col_name) or index["by_name"].get(col_name.lower())
    return bool(meta and meta.get("data_type") == "numeric")


def _is_id_col(col_name: str, index: dict) -> bool:
    meta = index["by_name"].get(col_name) or index["by_name"].get(col_name.lower())
    return bool(meta and meta.get("role") == "id")


def _is_datetime_col(col_name: str, index: dict) -> bool:
    meta = index["by_name"].get(col_name) or index["by_name"].get(col_name.lower())
    if not meta:
        return False
    return meta.get("data_type") == "datetime" or meta.get("role") == "date"


def _pick_primary_metric(index: dict) -> str | None:
    if index["primary_metrics"]:
        return index["primary_metrics"][0]
    if index["numeric"]:
        return index["numeric"][0]
    return None


def _pick_secondary_metric(index: dict, primary: str | None) -> str | None:
    for col in index["numeric"]:
        if col != primary:
            return col
    return None


def _pick_dimension_col(index: dict) -> str | None:
    # Prefer non-id descriptive dimensions for category-based charts.
    seen = set()
    for meta in index["by_name"].values():
        name = meta.get("clean_name") or meta.get("original_name")
        if not name or name in seen:
            continue
        seen.add(name)
        role = str(meta.get("role") or "").lower()
        if role in {"dimension", "descriptive", "geographic"}:
            return name

    for name in index["text_like"]:
        if not _is_id_col(name, index):
            return name
    return None


def _all_column_names(index: dict) -> list[str]:
    names = []
    seen = set()
    for meta in index["by_name"].values():
        name = meta.get("clean_name") or meta.get("original_name")
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _clamp_float(value: Any, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return round(f, 4)


def _parse_json_maybe(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _dedup(values: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
