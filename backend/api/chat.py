"""
Chat endpoint — AI assistant powered by Gemini with function-calling.

Supports: navigation, data queries, chart generation, 3D visuals,
anomaly detection, dataset comparison, quality reports, recommendations,
PDF export, forecast history/accuracy, segmentation, and onboarding.
"""

import io
import json
import logging
import os
import re
import threading
import time
import uuid

import numpy as np
import pandas as pd

from django.http import StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from google import genai
from google.genai import types

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .supabase_client import (
    verify_supabase_token,
    list_user_datasets,
    delete_dataset_full,
    get_dataset,
    get_columns_metadata,
    download_file_bytes,
    get_forecast_results,
    get_forecast_result_by_id,
    get_supabase_client,
    CLEANED_DATA_BUCKET,
)

logger = logging.getLogger(__name__)

_client = None

GEMINI_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)

# ── Rate limiting ────────────────────────────────────────────────────────────
_chat_rate_limits: dict = {}
_chat_rate_lock = threading.Lock()
CHAT_RATE_LIMIT_SECONDS = 2

def _check_chat_rate_limit(user_id: str) -> float | None:
    """Returns seconds to wait if rate-limited, else None and updates timestamp."""
    now = time.time()
    with _chat_rate_lock:
        last = _chat_rate_limits.get(user_id, 0)
        if now - last < CHAT_RATE_LIMIT_SECONDS:
            return round(CHAT_RATE_LIMIT_SECONDS - (now - last), 1)
        _chat_rate_limits[user_id] = now
        return None

# ── Context window ───────────────────────────────────────────────────────────
MAX_HISTORY_MESSAGES = 30

def _truncate_history(messages: list) -> list:
    """Keep only the last MAX_HISTORY_MESSAGES messages to avoid token overflow."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    return messages[-MAX_HISTORY_MESSAGES:]


def _parse_json_if_string(value):
    """Parse JSON string or return value as-is."""
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _get_dataset_filename(ds: dict) -> str:
    """Extract filename from dataset record, checking file_info blob."""
    file_info = _parse_json_if_string(ds.get("file_info")) or {}
    if not isinstance(file_info, dict):
        file_info = {}
    return (
        file_info.get("original_filename")
        or file_info.get("filename")
        or file_info.get("original_name")
        or ds.get("file_name")
        or "Untitled dataset"
    )


def _ensure_gemini():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        _client = genai.Client(api_key=api_key)


def _load_dataset_df(dataset_id: str):
    """Load a cleaned parquet dataframe for a given dataset."""
    ds = get_dataset(dataset_id)
    if not ds:
        return None, None, "Dataset not found"
    cleaned_path = ds.get("processed_path")
    if not cleaned_path:
        return ds, None, "Dataset has not been processed yet"
    try:
        file_bytes = download_file_bytes(CLEANED_DATA_BUCKET, cleaned_path)
        df = pd.read_parquet(io.BytesIO(file_bytes))
        return ds, df, None
    except Exception as e:
        return ds, None, f"Failed to load data: {str(e)}"


SYSTEM_PROMPT = """You are AxBi Assistant, an AI helper embedded in the AxBi business-intelligence dashboard platform.

You help users with:
- Navigating the app (Projects, Upload Data, AI Insights, Forecast History, Recommendations, Reports)
- Answering questions about their data using query_data
- Generating charts and 3D visualizations from their data
- Running forecasts and segmentation
- Detecting anomalies and data quality issues
- Comparing datasets
- Managing (deleting) datasets
- Generating AI recommendations
- Exporting PDF reports
- Guiding new users through the platform

Available pages: /BI-Dashboard, /upload, /AI-Insights, /forecast-history, /recommendations, /report

DOMAIN BOUNDARY (mandatory — highest priority):
- You ONLY assist with AxBi platform features and the user's uploaded business data / analytics.
- IN SCOPE: their datasets, KPIs, metrics, charts, forecasts, segmentation, AI reports, recommendations, data quality, anomalies, comparisons, PDF export, and business insights derived from THEIR data.
- OUT OF SCOPE — politely REFUSE and redirect (do NOT answer even if you know the answer): general trivia, celebrities, sports, entertainment, politics, news, homework, unrelated coding, personal life advice, or any topic not tied to their data or this platform.
- Never offer to chat about "other topics" or "general business questions" unrelated to their dataset. You are NOT a general-purpose assistant.
- When refusing, stay brief and warm: explain you specialize in their data & AxBi tools, then suggest 1–2 concrete things you can do (e.g. show KPIs, build a chart, run a forecast).
- If a question is ambiguous, interpret it in a data/business-analytics context when reasonable; otherwise refuse.

CRITICAL BEHAVIOR RULES:
1. BE PROACTIVE: When a user asks to generate charts, visuals, or any data output — DO IT IMMEDIATELY. Do NOT ask follow-up questions if you already know their dataset ID. Use the first/only available dataset if the user hasn't specified one.
2. When a user says "generate charts for my data" or similar, call generate_chart directly. Pick meaningful columns yourself based on the data.
3. If the user only has ONE dataset, use it automatically — never ask them to provide the dataset ID.
4. If you need to list datasets first to get the ID, call list_projects, then immediately proceed with the next action.
5. Use query_data when user asks factual questions about their data (averages, totals, counts, etc.)
6. Use generate_chart when user wants a visualization or chart of their data. ALWAYS pass `dimension` (the exact column to group/slice by) and, when aggregating a value, `measure` — copy these names VERBATIM from the dataset's column list in the context above. Map the user's wording to the closest real column (e.g. "marketing channels" → a "marketing_channel" column, not "campaign_type"). For a pie/donut, `dimension` must be a categorical column; omit `measure` to count rows.
6b. Use generate_metrics when the user asks for "key metrics", "KPIs", "summary numbers", "top metrics", a headline overview, or a "KPI/metric card" — it returns KPI summary cards (totals/averages/counts). You decide which metrics are most useful. You may also call generate_chart in the same turn to add a supporting trend chart.
6c. CRITICAL — NEVER claim you created or displayed a KPI card, chart, metric card, or visual unless you ACTUALLY called the matching tool (generate_metrics / generate_chart / generate_3d_visual) in this same turn. If the user asks for a KPI/metric card, you MUST call generate_metrics — do NOT just state the numbers in prose as a substitute and do NOT say "I've created a card" without calling the tool. The card only appears when the tool runs.
7. Use generate_3d_visual when user specifically asks for 3D visualization
8. Use detect_anomalies when user asks about outliers or unusual values
9. Use data_quality_report when user asks about data health or quality
10. Use compare_datasets when user wants to compare two datasets
11. Use get_recommendations when user wants AI-generated business recommendations
12. Use export_pdf when user wants to export or download a report
13. Use check_forecast_accuracy or get_forecast_history for forecast-related queries
14. Use onboarding_guide for "how do I" questions about the platform
15. If a user asks to delete, ALWAYS confirm first before calling delete_dataset
16. Be concise and helpful. Format responses clearly.
17. When a user asks for "graphs", "charts", or "visuals" without specifying type, generate a bar chart as default.
"""

# ══════════════════════════════════════════════════════════════════════
# TOOL DECLARATIONS
# ══════════════════════════════════════════════════════════════════════

TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="navigate_to",
            description="Navigate the user to a specific page in the app",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "page": types.Schema(type="STRING", description="Route path: /BI-Dashboard, /upload, /AI-Insights, /forecast-history, /recommendations, /report"),
                },
                required=["page"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_projects",
            description="List all datasets/projects the user has uploaded",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="get_dataset_summary",
            description="Get metadata and column info for a dataset",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="query_data",
            description="Ask a factual question about dataset rows (averages, totals, top values, filters, etc.)",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "question": types.Schema(type="STRING", description="The question to answer about the data"),
                },
                required=["dataset_id", "question"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_chart",
            description="Generate a 2D chart visualization from dataset data. Returns chart config for frontend rendering.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "description": types.Schema(type="STRING", description="What the chart should show"),
                    "chart_type": types.Schema(type="STRING", description="Chart type: bar, horizontal_bar, line, area, pie, treemap, funnel. If unsure, leave empty."),
                    "dimension": types.Schema(type="STRING", description="EXACT column name to group/slice by (the category or time axis), copied verbatim from the dataset's column list. Strongly recommended for pie/bar charts so the right column is used."),
                    "measure": types.Schema(type="STRING", description="EXACT column name of the numeric value to aggregate, copied verbatim from the dataset's column list. Omit to count rows."),
                },
                required=["dataset_id", "description"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_metrics",
            description=(
                "Generate KPI summary cards (key metrics) from dataset data — e.g. total revenue, "
                "average CTR, total rows. Use this whenever the user asks for 'key metrics', 'KPIs', "
                "'summary numbers', 'top metrics', or an overview of headline figures. Returns metric "
                "cards for frontend rendering; pick the metrics that are most useful for the request."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "description": types.Schema(type="STRING", description="What metrics the user wants summarized"),
                    "metrics": types.Schema(
                        type="ARRAY",
                        items=types.Schema(type="STRING"),
                        description="Optional list of column-name hints to surface as KPI cards",
                    ),
                },
                required=["dataset_id", "description"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_3d_visual",
            description="Generate a 3D visualization (scatter, bar chart, or globe) from dataset data. Describe what metrics and groupings you want.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "description": types.Schema(type="STRING", description="What the 3D visual should represent, including column names and grouping (e.g. 'spend, revenue, conversions by channel')"),
                },
                required=["dataset_id", "description"],
            ),
        ),
        types.FunctionDeclaration(
            name="detect_anomalies",
            description="Find outliers/anomalies in numeric columns using z-score analysis",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "column": types.Schema(type="STRING", description="Specific column to check (optional, checks all numeric if omitted)"),
                },
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="compare_datasets",
            description="Compare summary statistics between two datasets",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id_1": types.Schema(type="STRING", description="First dataset UUID"),
                    "dataset_id_2": types.Schema(type="STRING", description="Second dataset UUID"),
                },
                required=["dataset_id_1", "dataset_id_2"],
            ),
        ),
        types.FunctionDeclaration(
            name="data_quality_report",
            description="Generate a data quality report: null percentages, duplicates, type issues, overall score",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_recommendations",
            description="Get or generate AI business recommendations for a dataset",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="export_pdf",
            description="Trigger PDF report export for a dataset",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="check_forecast_accuracy",
            description="Check accuracy metrics of a specific forecast run",
            parameters=types.Schema(
                type="OBJECT",
                properties={"forecast_id": types.Schema(type="STRING", description="Forecast UUID")},
                required=["forecast_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_forecast_history",
            description="List past forecast runs for a dataset with models and accuracy scores",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_forecast",
            description="Run a new forecast on a dataset",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "dataset_id": types.Schema(type="STRING", description="Dataset UUID"),
                    "time_column": types.Schema(type="STRING", description="Datetime column name"),
                    "target_column": types.Schema(type="STRING", description="Numeric target column"),
                    "horizon": types.Schema(type="INTEGER", description="Periods to forecast (default 30)"),
                },
                required=["dataset_id", "time_column", "target_column"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_segmentation",
            description="Run segmentation analysis on a dataset",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="delete_dataset",
            description="Delete a dataset. ONLY call after user explicitly confirms.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"dataset_id": types.Schema(type="STRING", description="Dataset UUID")},
                required=["dataset_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="onboarding_guide",
            description="Provide step-by-step guidance about how to use the platform",
            parameters=types.Schema(
                type="OBJECT",
                properties={"topic": types.Schema(type="STRING", description="Topic: upload, forecast, segmentation, charts, general")},
                required=["topic"],
            ),
        ),
    ])
]


# ══════════════════════════════════════════════════════════════════════
# TOOL EXECUTORS
# ══════════════════════════════════════════════════════════════════════

def _execute_function(func_name: str, args: dict, user_id: str) -> tuple[dict, dict | None, dict | None, dict | None]:
    """
    Execute a tool function.
    Returns: (result_dict, frontend_action, chart_data, visual_3d_data)
    """
    action = None
    chart = None
    visual_3d = None

    if func_name == "navigate_to":
        page = args.get("page", "/BI-Dashboard")
        action = {"type": "navigate", "payload": {"path": page}}
        return {"success": True, "message": f"Navigating to {page}"}, action, None, None

    elif func_name == "list_projects":
        datasets = list_user_datasets(user_id)
        summary = []
        for ds in datasets:
            gc = _parse_json_if_string(ds.get("global_context")) or {}
            resolved = ((gc.get("category_detection") or {}).get("resolved_category")
                        or ds.get("category") or ds.get("category_hint"))
            summary.append({"id": ds.get("id"), "filename": _get_dataset_filename(ds),
                             "category": resolved, "status": ds.get("status", "unknown")})
        return {"projects": summary, "count": len(summary)}, None, None, None

    elif func_name == "get_dataset_summary":
        dataset_id = args.get("dataset_id", "")
        ds = get_dataset(dataset_id)
        if not ds:
            return {"error": "Dataset not found"}, None, None, None
        cols = get_columns_metadata(dataset_id)
        col_summaries = [
            {"name": c.get("clean_name") or c.get("original_name", ""), "type": c.get("detected_type", "unknown")}
            for c in cols[:20]
        ]
        return {"filename": _get_dataset_filename(ds), "category": ds.get("category") or ds.get("category_hint"),
                "status": ds.get("status"), "columns": col_summaries, "column_count": len(cols)}, None, None, None

    elif func_name == "query_data":
        dataset_id = args.get("dataset_id", "")
        question = args.get("question", "").lower()
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        # ── Detect a categorical filter named in the question (e.g. "meta ads" →
        # platform == "Meta Ads") so "show meta ads revenue" aggregates ONLY the
        # matching rows instead of summing the entire column. ──
        work = df
        filter_applied = None
        best = None  # (match_len, column, original_value)
        for col in df.select_dtypes(include=["object", "category"]).columns:
            uniques = df[col].dropna().unique()
            if len(uniques) > 500:
                continue
            for val in uniques:
                nval = str(val).strip().lower()
                if len(nval) >= 3 and nval in question:
                    if best is None or len(nval) > best[0]:
                        best = (len(nval), col, str(val).strip())
        if best is not None:
            _, fcol, fval = best
            mask = df[fcol].astype(str).str.strip().str.lower() == fval.lower()
            if mask.any():
                work = df[mask]
                filter_applied = {"column": fcol, "value": fval, "matched_rows": int(mask.sum())}

        result: dict = {"question": args.get("question", ""), "row_count": len(work), "column_count": len(df.columns)}
        if filter_applied:
            # Make the scope explicit so the model never reports a filtered figure
            # as a grand total (or vice-versa).
            result["filter_applied"] = filter_applied
            result["note"] = (f"Aggregations below are for rows where {filter_applied['column']} = "
                              f"'{filter_applied['value']}' ({filter_applied['matched_rows']} of {len(df)} rows), "
                              f"NOT the whole dataset.")

        numeric_df = work.select_dtypes(include=[np.number])
        cat_df = work.select_dtypes(include=["object", "category"])
        dt_df = work.select_dtypes(include=["datetime64"])

        # Full aggregations so Gemini can answer "total", "average", "max" etc accurately
        if not numeric_df.empty:
            agg = numeric_df.agg(["sum", "mean", "min", "max", "count"]).round(2)
            result["numeric_aggregations"] = agg.to_dict()

        # Top values per categorical column
        result["categorical_summaries"] = {}
        for col in list(cat_df.columns)[:8]:
            vc = work[col].value_counts().head(10)
            result["categorical_summaries"][col] = {str(k): int(v) for k, v in vc.items()}

        # Date range
        if not dt_df.empty:
            col = dt_df.columns[0]
            result["date_range"] = {"column": col, "earliest": str(work[col].min()), "latest": str(work[col].max())}

        # Proactively compute top-N groupby when question implies ranking
        rank_keywords = ("top", "highest", "most", "best", "largest", "biggest", "least", "lowest", "worst", "bottom")
        if any(kw in question for kw in rank_keywords) and not numeric_df.empty and not cat_df.empty:
            for num_col in list(numeric_df.columns)[:3]:
                for cat_col in list(cat_df.columns)[:2]:
                    ascending = any(kw in question for kw in ("least", "lowest", "worst", "bottom", "smallest"))
                    top = work.groupby(cat_col)[num_col].sum().nlargest(10) if not ascending else work.groupby(cat_col)[num_col].sum().nsmallest(10)
                    result[f"ranked_{cat_col}_by_{num_col}"] = top.round(2).reset_index().to_dict(orient="records")

        return result, None, None, None

    elif func_name == "generate_chart":
        dataset_id = args.get("dataset_id", "")
        description = args.get("description", "").lower()
        chart_type = args.get("chart_type", "bar")
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        numeric_cols = list(df.select_dtypes(include=[np.number]).columns)
        cat_cols = list(df.select_dtypes(include=["object", "category"]).columns)
        datetime_cols = list(df.select_dtypes(include=["datetime", "datetimetz"]).columns)
        # Some date columns survive cleaning as strings — detect by name and parse
        # them so "over time" requests aren't treated as plain categories.
        for c in list(cat_cols):
            if any(k in c.lower() for k in ("date", "time", "month", "year", "day")):
                parsed = pd.to_datetime(df[c], errors="coerce")
                if parsed.notna().mean() > 0.8:
                    df[c] = parsed
                    datetime_cols.append(c)
                    cat_cols.remove(c)

        if not chart_type:
            chart_type = "bar"
        if chart_type in ("scatter", "dot"):
            chart_type = "bar"  # scatter/dot charts are no longer supported

        def _norm(s):
            return str(s).replace("_", " ").strip().lower()

        def _resolve_col(hint):
            """Map a column-name hint from the model to a real dataframe column:
            exact normalized match → containment (longest name wins) → token overlap."""
            if not hint:
                return None
            h = _norm(hint)
            for c in df.columns:
                if _norm(c) == h:
                    return c
            for c in sorted(df.columns, key=lambda x: -len(_norm(x))):
                cn = _norm(c)
                if cn and (cn in h or h in cn):
                    return c
            htok = set(h.split())
            best, best_n = None, 0
            for c in df.columns:
                ov = len(htok & set(_norm(c).split()))
                if ov > best_n:
                    best, best_n = c, ov
            return best

        # Explicit column hints from the model take priority over fuzzy text matching.
        forced_dim = _resolve_col(args.get("dimension") or "")
        forced_measure = _resolve_col(args.get("measure") or "")

        # Match columns from description
        matched_numeric = [c for c in numeric_cols if _norm(c) in description or c.lower() in description]
        matched_cat = [c for c in cat_cols if _norm(c) in description or c.lower() in description]

        # "total / sum / revenue / over all" → sum aggregation; otherwise average.
        use_sum = any(k in description for k in (
            "total", "sum", "overall", "over all", "all the data", "all data",
            "revenue", "sales", "count", "how many",
        ))

        def _aggregate(grouped_series):
            return grouped_series.sum() if use_sum else grouped_series.mean()

        # Pick the metric (y) first so we never group a column by itself.
        metric_col = matched_numeric[0] if matched_numeric else (numeric_cols[0] if numeric_cols else None)
        if forced_measure is not None:
            metric_col = forced_measure

        # Detect an explicit "by <dimension>" phrase and match it against ALL
        # columns. A campaign/customer/product id is often stored as a number, so
        # numeric columns must be allowed to act as the grouping dimension too —
        # otherwise "ROAS by campaign" degrades into one bar per row indexed by
        # row number, which is meaningless.
        def _find_dimension(phrase, exclude=()):
            phrase = _norm(phrase)
            if not phrase:
                return None
            cols = [c for c in df.columns if c not in exclude]
            for c in sorted(cols, key=lambda x: -len(_norm(x))):
                cn = _norm(c)
                if cn == phrase or cn in phrase or phrase in cn:
                    return c
            phrase_tokens = set(phrase.split())
            for c in cols:
                if phrase_tokens & set(_norm(c).split()):
                    return c
            return None

        def _pick_dimension_for_parts(exclude=()):
            """Best column to slice a pie/donut by: a low-cardinality dimension
            (2..25 distinct values). Prefer text/categorical, then low-cardinality
            numeric codes. Returns None when nothing is suitable (so the caller can
            fall back to an honest histogram instead of a meaningless pie)."""
            best, best_score = None, None
            for c in df.columns:
                if c in exclude:
                    continue
                try:
                    n = int(df[c].nunique(dropna=True))
                except Exception:
                    continue
                if n < 2 or n > 25:
                    continue
                is_text = df[c].dtype == object or str(df[c].dtype) == "category"
                score = (0 if is_text else 1, n)  # text first, then fewer slices
                if best is None or score < best_score:
                    best, best_score = c, score
            return best

        by_match = re.search(
            r"\bby\s+([a-z0-9_ ]+?)(?:\s+(?:over|per|for|in|and|with|vs)\b|$)", description
        )
        by_phrase = by_match.group(1) if by_match else ""

        group_col = _find_dimension(by_phrase, exclude=(metric_col,) if metric_col else ())
        if group_col is None and matched_cat:
            group_col = matched_cat[0]
        # Fall back to matching a dimension against the WHOLE description (catches
        # "pie chart of marketing channels" where there is no explicit "by X").
        if group_col is None:
            group_col = _find_dimension(description, exclude=(metric_col,) if metric_col else ())
        # An explicit dimension hint from the model always wins.
        if forced_dim is not None and forced_dim != metric_col:
            group_col = forced_dim
        # If the requested metric was itself chosen as the dimension, pick another
        # numeric column to be the metric.
        if group_col is not None and metric_col == group_col:
            metric_col = next((c for c in numeric_cols if c != group_col), None)

        # Detect a time-axis request and pick the time column.
        wants_time = any(k in description for k in (
            "over time", "trend", "timeline", "time series", "over all time",
            "by date", "by day", "by week", "by month", "by year", "by quarter",
            "daily", "weekly", "monthly", "yearly", "quarterly", "per day", "per month",
        ))
        time_col = None
        if datetime_cols:
            time_col = next(
                (c for c in datetime_cols if c.lower() in description or c.replace("_", " ").lower() in description),
                datetime_cols[0],
            )

        chart_data = []
        x_key = "name"
        y_key = "value"

        # ── Time-series branch: aggregate the metric across the FULL date range ──
        # Chooses a grain (day/week/month/year) so the whole series fits in a
        # readable number of points instead of truncating to the first 30 rows.
        if time_col and numeric_cols and (wants_time or chart_type in ("line", "area") or "time" in description):
            y_col = metric_col or numeric_cols[0]
            s = df[[time_col, y_col]].copy()
            s[time_col] = pd.to_datetime(s[time_col], errors="coerce")
            s = s.dropna(subset=[time_col, y_col]).sort_values(time_col)
            if not s.empty:
                span_days = max((s[time_col].max() - s[time_col].min()).days, 1)
                if span_days <= 60:
                    freq, fmt = "D", "%Y-%m-%d"
                elif span_days <= 400:
                    freq, fmt = "W", "%Y-%m-%d"
                elif span_days <= 2000:
                    freq, fmt = "ME", "%Y-%m"
                else:
                    freq, fmt = "YE", "%Y"
                grouped = _aggregate(s.groupby(pd.Grouper(key=time_col, freq=freq))[y_col]).reset_index()
                grouped = grouped.dropna(subset=[y_col])
                x_key, y_key = time_col, y_col
                chart_data = [
                    {x_key: d.strftime(fmt), y_key: round(float(v), 2)}
                    for d, v in zip(grouped[time_col], grouped[y_col])
                ]
                if chart_type not in ("line", "area", "bar"):
                    chart_type = "line"

        chart_title = args.get("description", "")

        if not chart_data and group_col is not None and metric_col is not None:
            # Aggregate the metric per dimension value. A numeric id (e.g. campaign
            # id) is cast to text so it groups by value, and we keep the top 20 so a
            # high-cardinality field stays readable instead of rendering thousands
            # of bars (or one bar per row).
            g = df[[group_col, metric_col]].copy()
            g[group_col] = g[group_col].astype(str)
            grouped = (
                _aggregate(g.groupby(group_col)[metric_col])
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )
            x_key, y_key = group_col, metric_col
            chart_data = grouped.round(2).to_dict(orient="records")
        elif not chart_data and chart_type in ("pie", "donut"):
            # A pie/donut must split a whole into a few named parts, so it has to
            # group by a REAL categorical dimension. Using a synthetic "name" key
            # breaks re-aggregation, and binning a continuous metric into a pie is
            # meaningless — so when no suitable dimension exists we downgrade to an
            # honest histogram instead of faking a pie.
            dim = group_col or _pick_dimension_for_parts(
                exclude=(metric_col,) if metric_col else ()
            )
            if dim is not None:
                if metric_col is not None and dim != metric_col and matched_numeric:
                    # The user named a metric → sum/avg that metric per category.
                    g = df[[dim, metric_col]].copy()
                    g[dim] = g[dim].astype(str)
                    grouped = (
                        _aggregate(g.groupby(dim)[metric_col])
                        .sort_values(ascending=False)
                        .head(12)
                    )
                    x_key, y_key = dim, metric_col
                    chart_data = [
                        {dim: str(k), metric_col: round(float(v), 2)}
                        for k, v in grouped.items()
                    ]
                else:
                    # Otherwise show how records are distributed across the category.
                    counts = df[dim].astype(str).value_counts().head(12)
                    x_key, y_key = dim, "value"
                    chart_data = [{dim: str(k), "value": int(v)} for k, v in counts.items()]
            else:
                # No categorical dimension at all → render the metric distribution
                # as a histogram (handled by the branch below) rather than a pie.
                chart_type = "histogram"
        elif not chart_data and chart_type != "histogram" and metric_col is not None and cat_cols:
            x_col = cat_cols[0]
            grouped = (
                _aggregate(df.groupby(x_col)[metric_col])
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )
            x_key, y_key = x_col, metric_col
            chart_data = grouped.round(2).to_dict(orient="records")
        elif not chart_data and metric_col is not None:
            # No dimension to group by → show the DISTRIBUTION of the metric as a
            # histogram with readable value-range labels (never row indices).
            series = pd.to_numeric(df[metric_col], errors="coerce").dropna()
            if not series.empty:
                bin_count = min(20, max(5, int(series.nunique())))
                try:
                    cut = pd.cut(series, bins=bin_count)
                    counts = cut.value_counts().sort_index()
                    chart_data = [
                        {"name": f"{iv.left:.1f}–{iv.right:.1f}", "value": int(v)}
                        for iv, v in counts.items()
                    ]
                    x_key, y_key = "name", "value"
                    # Render as a true (non-customizable) histogram: its x-axis is
                    # synthetic value-range bins, not a real column, so it must not
                    # be exposed to the re-aggregating "Customize" toolbar.
                    chart_type = "histogram"
                    chart_title = chart_title or f"Distribution of {_norm(metric_col).title()}"
                except (ValueError, TypeError):
                    chart_data = []
        if not chart_data:
            return {"error": "No suitable columns for charting"}, None, None, None

        chart = {
            "type": chart_type,
            "title": chart_title or f"{y_key} by {x_key}",
            "data": chart_data,
            "xKey": x_key,
            "yKey": y_key,
        }
        return {"success": True, "message": f"Generated {chart_type} chart", "chart_type": chart_type}, None, chart, None

    elif func_name == "generate_metrics":
        dataset_id = args.get("dataset_id", "")
        description = (args.get("description", "") or "").lower()
        requested = [str(m).lower() for m in (args.get("metrics") or [])]
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        numeric_cols = list(df.select_dtypes(include=[np.number]).columns)

        def _fmt(value, is_currency):
            try:
                v = float(value)
            except (TypeError, ValueError):
                return str(value)
            sign = "-" if v < 0 else ""
            a = abs(v)
            if a >= 1_000_000_000:
                num = f"{a / 1_000_000_000:.1f}B"
            elif a >= 1_000_000:
                num = f"{a / 1_000_000:.1f}M"
            elif a >= 1_000:
                num = f"{a / 1_000:.1f}K"
            elif a >= 1:
                num = f"{a:,.0f}" if a == int(a) else f"{a:,.2f}"
            else:
                num = f"{a:.2f}"
            return f"{sign}{'$' if is_currency else ''}{num}"

        def _pick_agg(col):
            cl = col.lower()
            if any(k in cl for k in ("rate", "ratio", "ctr", "avg", "average", "percent", "pct", "score", "margin")):
                return "mean"
            return "sum"

        # Decide which numeric columns to surface as KPI cards. Match by name,
        # token overlap, and a few BI synonyms (ROI→ROAS, sales→revenue, …) so a
        # specific request like "average return on investment" surfaces the ROAS
        # card instead of a generic set of totals.
        def _toks(s):
            return set(str(s).replace("_", " ").lower().split())

        SYN = {
            "roi": "roas", "return": "roas", "investment": "roas", "investments": "roas",
            "sales": "revenue", "earnings": "revenue", "income": "revenue", "turnover": "revenue",
            "cost": "spend", "costs": "spend", "budget": "spend", "spending": "spend",
            "ctr": "click", "clickthrough": "click",
            "views": "impressions", "impression": "impressions",
            "conversion": "conversions", "orders": "conversions", "purchases": "conversions",
        }

        def _expand(tokens):
            out = set(tokens)
            for t in tokens:
                if t in SYN:
                    out.add(SYN[t])
            return out

        desc_tokens = _expand(_toks(description))
        hint_tokens = set()
        for r in requested:
            hint_tokens |= _expand(_toks(r))

        chosen = []
        for c in numeric_cols:
            ctoks = _toks(c)
            if (c.lower() in description or c.replace("_", " ").lower() in description
                    or (ctoks & desc_tokens) or (ctoks & hint_tokens)):
                chosen.append(c)
        if not chosen:
            ranked = sorted(numeric_cols, key=lambda c: abs(float(df[c].sum() or 0)), reverse=True)
            chosen = ranked[:4]
        chosen = chosen[:6]

        cards = [{"label": "Total Rows", "value": int(len(df)),
                  "formatted": _fmt(len(df), False), "agg": "count", "column": None}]
        for col in chosen:
            agg = _pick_agg(col)
            try:
                value = float(df[col].mean() if agg == "mean" else df[col].sum())
            except (TypeError, ValueError):
                continue
            if pd.isna(value):
                continue
            is_currency = any(k in col.lower() for k in (
                "revenue", "sales", "price", "cost", "spend", "amount", "profit", "income", "$"))
            label = col.replace("_", " ").title()
            prefix = "Avg " if agg == "mean" else "Total "
            cards.append({
                "label": f"{prefix}{label}",
                "value": round(value, 2),
                "formatted": _fmt(value, is_currency),
                "agg": agg,
                "column": col,
            })

        if len(cards) <= 1:
            return {"error": "No numeric columns available for metrics"}, None, None, None

        title = args.get("description") or "Key Metrics"
        return {"__kind__": "metrics", "title": title, "cards": cards}, None, None, None

    elif func_name == "generate_3d_visual":
        dataset_id = args.get("dataset_id", "")
        description = args.get("description", "").lower()
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        numeric_cols = list(df.select_dtypes(include=[np.number]).columns)
        cat_cols = list(df.select_dtypes(include=["object", "category"]).columns)

        # Try to match requested columns from description
        matched_numeric = [c for c in numeric_cols if c.lower() in description or c.replace("_", " ").lower() in description]
        matched_cat = [c for c in cat_cols if c.lower() in description or c.replace("_", " ").lower() in description]

        # Detect "by [category]" pattern
        group_col = None
        for cat in cat_cols:
            if f"by {cat.lower()}" in description or f"by {cat.replace('_', ' ').lower()}" in description:
                group_col = cat
                break

        # If no explicit grouping found but categorical columns matched, use first match
        if not group_col and matched_cat:
            group_col = matched_cat[0]

        # Strategy: If we have a group column + numeric columns → bar3d (aggregated)
        if group_col and (matched_numeric or numeric_cols):
            value_cols = matched_numeric if matched_numeric else numeric_cols[:3]
            # Aggregate data by the grouping column
            grouped = df.groupby(group_col)[value_cols].mean().head(12).reset_index()
            
            if len(value_cols) >= 3:
                # Multi-metric: use scatter3d with aggregated data (each point = one category)
                vis_type = "scatter3d"
                data = grouped[[group_col] + value_cols[:3]].round(2).to_dict(orient="records")
                config = {"xKey": value_cols[0], "yKey": value_cols[1], "zKey": value_cols[2], "labelKey": group_col}
            else:
                # Fewer metrics: use bar3d with the primary value
                vis_type = "bar3d"
                primary_value = value_cols[0]
                data = grouped[[group_col, primary_value]].round(2).to_dict(orient="records")
                config = {"categoryKey": group_col, "valueKey": primary_value}

        # 3 specific numeric columns requested without category → scatter3d
        elif len(matched_numeric) >= 3:
            vis_type = "scatter3d"
            cols = matched_numeric[:3]
            sample = df[cols].dropna().head(100).round(2)
            data = sample.to_dict(orient="records")
            config = {"xKey": cols[0], "yKey": cols[1], "zKey": cols[2]}

        # Fallback: use first available columns
        elif len(numeric_cols) >= 3:
            vis_type = "scatter3d"
            cols = numeric_cols[:3]
            if cat_cols:
                # Add grouping by first categorical to make it meaningful
                group_col = cat_cols[0]
                grouped = df.groupby(group_col)[cols].mean().head(20).reset_index()
                data = grouped.round(2).to_dict(orient="records")
                config = {"xKey": cols[0], "yKey": cols[1], "zKey": cols[2], "labelKey": group_col}
            else:
                sample = df[cols].dropna().head(100).round(2)
                data = sample.to_dict(orient="records")
                config = {"xKey": cols[0], "yKey": cols[1], "zKey": cols[2]}

        elif len(numeric_cols) >= 1:
            vis_type = "bar3d"
            if cat_cols:
                grouped = df.groupby(cat_cols[0])[numeric_cols[0]].sum().head(12).reset_index()
                data = grouped.round(2).to_dict(orient="records")
                config = {"categoryKey": cat_cols[0], "valueKey": numeric_cols[0]}
            else:
                data = [{"name": str(i), "value": round(float(v), 2)} for i, v in enumerate(df[numeric_cols[0]].head(12))]
                config = {"categoryKey": "name", "valueKey": "value"}
        else:
            return {"error": "Need at least 1 numeric column for 3D visualization"}, None, None, None

        desc_title = args.get("description", "") or f"3D {vis_type}"
        visual_3d = {
            "type": vis_type,
            "title": desc_title,
            "data": data,
            "config": config,
        }
        return {"success": True, "message": f"Generated 3D {vis_type} visualization"}, None, None, visual_3d

    elif func_name == "detect_anomalies":
        dataset_id = args.get("dataset_id", "")
        column = args.get("column")
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        numeric_cols = list(df.select_dtypes(include=[np.number]).columns)
        if column and column in numeric_cols:
            check_cols = [column]
        else:
            check_cols = numeric_cols[:5]

        anomalies = {}
        for col in check_cols:
            series = df[col].dropna()
            if len(series) < 10:
                continue
            mean = series.mean()
            std = series.std()
            if std == 0:
                continue
            z_scores = ((series - mean) / std).abs()
            outlier_mask = z_scores > 2.5
            count = int(outlier_mask.sum())
            if count > 0:
                anomalies[col] = {
                    "outlier_count": count,
                    "total_rows": len(series),
                    "percentage": round(count / len(series) * 100, 2),
                    "threshold": 2.5,
                    "mean": round(float(mean), 2),
                    "std": round(float(std), 2),
                }

        chart_data = [{"name": col, "value": info["outlier_count"]} for col, info in anomalies.items()]
        if chart_data:
            chart = {"type": "bar", "title": "Anomalies by Column", "data": chart_data, "xKey": "name", "yKey": "value"}

        return {"anomalies": anomalies, "columns_checked": len(check_cols),
                "total_anomalies": sum(a["outlier_count"] for a in anomalies.values())}, None, chart, None

    elif func_name == "compare_datasets":
        id1 = args.get("dataset_id_1", "")
        id2 = args.get("dataset_id_2", "")
        _, df1, err1 = _load_dataset_df(id1)
        if err1:
            return {"error": f"Dataset 1: {err1}"}, None, None, None
        _, df2, err2 = _load_dataset_df(id2)
        if err2:
            return {"error": f"Dataset 2: {err2}"}, None, None, None

        num1 = set(df1.select_dtypes(include=[np.number]).columns)
        num2 = set(df2.select_dtypes(include=[np.number]).columns)
        shared = list(num1 & num2)[:10]

        comparison = {"shared_columns": shared, "dataset_1_rows": len(df1), "dataset_2_rows": len(df2), "columns": {}}
        for col in shared:
            comparison["columns"][col] = {
                "ds1_mean": round(float(df1[col].mean()), 2),
                "ds2_mean": round(float(df2[col].mean()), 2),
                "ds1_sum": round(float(df1[col].sum()), 2),
                "ds2_sum": round(float(df2[col].sum()), 2),
            }
        return comparison, None, None, None

    elif func_name == "data_quality_report":
        dataset_id = args.get("dataset_id", "")
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None

        null_pcts = (df.isnull().sum() / len(df) * 100).round(2).to_dict()
        dup_count = int(df.duplicated().sum())
        total_nulls = int(df.isnull().sum().sum())
        total_cells = len(df) * len(df.columns)
        completeness = round((1 - total_nulls / total_cells) * 100, 1) if total_cells > 0 else 100

        report = {
            "rows": len(df),
            "columns": len(df.columns),
            "duplicate_rows": dup_count,
            "completeness_pct": completeness,
            "null_percentages": {k: v for k, v in null_pcts.items() if v > 0},
            "quality_score": round(completeness - (dup_count / max(len(df), 1)) * 10, 1),
        }

        chart_data = [{"name": col, "value": pct} for col, pct in null_pcts.items() if pct > 0][:10]
        if chart_data:
            chart = {"type": "bar", "title": "Null Percentage by Column", "data": chart_data, "xKey": "name", "yKey": "value"}

        return report, None, chart, None

    elif func_name == "get_recommendations":
        from .recommendations import run_recommendations_service
        dataset_id = args.get("dataset_id", "")
        if not dataset_id:
            return {"error": "dataset_id is required"}, None, None, None
        try:
            result = run_recommendations_service(dataset_id=dataset_id, force=False)
            return {"success": True, "recommendations": result}, None, None, None
        except Exception as e:
            return {"error": f"Recommendations failed: {str(e)}"}, None, None, None

    elif func_name == "export_pdf":
        dataset_id = args.get("dataset_id", "")
        ds = get_dataset(dataset_id)
        if not ds:
            return {"error": "Dataset not found"}, None, None, None
        # Navigate to the report page where the user can trigger the PDF download
        action = {"type": "navigate", "payload": {"path": "/report"}}
        return {"success": True, "message": "Opening the Report page where you can download the PDF."}, action, None, None

    elif func_name == "check_forecast_accuracy":
        forecast_id = args.get("forecast_id", "")
        try:
            result = get_forecast_result_by_id(forecast_id)
            if not result:
                return {"error": "Forecast not found"}, None, None, None
            return {
                "model": result.get("best_model"),
                "mae": result.get("best_mae"),
                "rmse": result.get("best_rmse"),
                "wape": result.get("best_wape"),
                "accuracy_pct": round((1 - (result.get("best_wape") or 0)) * 100, 1),
            }, None, None, None
        except Exception as e:
            return {"error": str(e)}, None, None, None

    elif func_name == "get_forecast_history":
        dataset_id = args.get("dataset_id", "")
        try:
            results = get_forecast_results(dataset_id)
            history = []
            for r in (results or [])[:10]:
                history.append({
                    "id": r.get("id"),
                    "model": r.get("best_model"),
                    "accuracy": round((1 - (r.get("best_wape") or 0)) * 100, 1),
                    "created_at": r.get("created_at"),
                })
            return {"forecasts": history, "count": len(history)}, None, None, None
        except Exception as e:
            return {"error": str(e)}, None, None, None

    elif func_name == "run_forecast":
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        from .forecasting import run_forecast_service
        from .views import _persist_forecast_log, _check_and_set_cooldown
        dataset_id = args.get("dataset_id", "")
        time_col = args.get("time_column", "")
        target_col = args.get("target_column", "")
        horizon = args.get("horizon", 30)

        remaining = _check_and_set_cooldown(user_id)
        if remaining > 0:
            return {"error": f"Rate limited. Please wait {int(remaining)+1}s before running another forecast."}, None, None, None

        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_forecast_service, df=df, time_column=time_col, target_column=target_col, horizon=int(horizon))
                result = future.result(timeout=300)
            best = result.get("best_model", "unknown")
            try:
                _persist_forecast_log(
                    dataset_id=dataset_id, user_id=user_id,
                    time_column=time_col, target_column=target_col,
                    feature_columns=[], frequency_hint=None,
                    horizon=int(horizon), missing_policy="drop",
                    input_rows=len(df), candidate_models=None,
                    result=result, error_message=None,
                )
            except Exception as pe:
                logger.warning(f"Chat: failed to persist forecast log: {pe}")
            action = {"type": "navigate", "payload": {"path": "/AI-Insights"}}
            return {"success": True, "best_model": best, "message": f"Forecast complete! Best model: {best}"}, action, None, None
        except FuturesTimeoutError:
            return {"error": "Forecast timed out after 300 seconds."}, None, None, None
        except Exception as e:
            return {"error": f"Forecast failed: {str(e)}"}, None, None, None

    elif func_name == "run_segmentation":
        from .segmentation import run_segmentation_service
        from .supabase_client import update_dataset
        dataset_id = args.get("dataset_id", "")
        ds, df, err = _load_dataset_df(dataset_id)
        if err:
            return {"error": err}, None, None, None
        try:
            cols_meta = get_columns_metadata(dataset_id)
            gc = _parse_json_if_string(ds.get("global_context")) or {}
            cat = ((gc.get("category_detection") or {}).get("resolved_category")
                   or ds.get("category_hint"))
            result = run_segmentation_service(df=df, columns_metadata=cols_meta, category_hint=cat)
            method = result.get("method", "unknown")
            segments = result.get("segments", [])
            # Persist results so recommendations/report can see them
            if not isinstance(gc, dict):
                gc = {}
            gc["segmentation"] = result
            try:
                update_dataset(dataset_id, {"global_context": gc})
            except Exception as pe:
                logger.warning(f"Chat: failed to persist segmentation: {pe}")
            return {"success": True, "method": method, "segment_count": len(segments),
                    "message": f"Segmentation done: {method}, {len(segments)} segments"}, None, None, None
        except Exception as e:
            return {"error": f"Segmentation failed: {str(e)}"}, None, None, None

    elif func_name == "delete_dataset":
        dataset_id = args.get("dataset_id", "")
        try:
            delete_dataset_full(dataset_id, user_id)
            action = {"type": "refresh", "payload": {}}
            return {"success": True, "message": "Dataset deleted."}, action, None, None
        except Exception as e:
            return {"error": f"Delete failed: {str(e)}"}, None, None, None

    elif func_name == "onboarding_guide":
        topic = args.get("topic", "general")
        guides = {
            "general": "AxBi workflow: 1) Upload CSV/XLSX on Upload page, 2) Wait for AI pipeline (cleaning, profiling, chart generation), 3) View auto-generated dashboard, 4) Run forecasts in AI Insights, 5) Check Reports for narrative analysis.",
            "upload": "Go to Upload Data (/upload): drag-drop or browse a CSV/XLSX file (max 10MB). Pick a department category. The 8-step AI pipeline processes it automatically.",
            "forecast": "Go to AI Insights (/AI-Insights): select a project, pick time & target columns, set horizon, click Run Forecast. Multiple models compete and the best wins.",
            "segmentation": "Segmentation auto-detects RFM, ABC/Pareto, or K-Means based on your data. Trigger via the API or ask me to run it.",
            "charts": "Charts are auto-generated in Step 7 of the pipeline. Types: bar, horizontal_bar, line, area, pie, donut, treemap, stacked_bar, funnel, combo, radial, histogram, pareto. You can also ask me to generate custom charts.",
        }
        guide_text = guides.get(topic, guides["general"])
        return {"guide": guide_text, "topic": topic}, None, None, None

    return {"error": f"Unknown function: {func_name}"}, None, None, None


# ══════════════════════════════════════════════════════════════════════
# VIEW
# ══════════════════════════════════════════════════════════════════════

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


def _get_or_create_conversation(user_id: str, conversation_id: str | None) -> str | None:
    """Get existing conversation or create one. Returns conversation_id or None on failure."""
    try:
        client = get_supabase_client()
        if conversation_id:
            resp = (
                client.table('conversations')
                .select('id')
                .eq('id', conversation_id)
                .eq('user_id', user_id)
                .maybe_single()
                .execute()
            )
            if resp.data:
                return conversation_id

        conv_id = str(uuid.uuid4())
        from datetime import datetime
        client.table('conversations').insert({
            "id": conv_id,
            "user_id": user_id,
            "title": "Chat Session",
            "share_token": None,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
        return conv_id
    except Exception as e:
        logger.warning(f"Failed to create/get conversation: {e}")
        return None


def _persist_chat_messages(conversation_id: str, user_content: str, assistant_reply: str,
                           chart: dict | None, visual_3d: dict | None):
    """Save user message + assistant reply to conversation_messages table."""
    if not conversation_id:
        return
    try:
        client = get_supabase_client()
        from datetime import datetime
        now = datetime.utcnow().isoformat()

        records = [
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "role": "user",
                "content": user_content,
                "chart_data": None,
                "visual_3d": None,
                "created_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": assistant_reply,
                "chart_data": json.dumps(chart) if chart else None,
                "visual_3d": json.dumps(visual_3d) if visual_3d else None,
                "created_at": now,
            },
        ]
        client.table('conversation_messages').insert(records).execute()
    except Exception as e:
        logger.warning(f"Failed to persist chat messages: {e}")


def _update_conversation_title(conversation_id: str, first_message: str):
    """Set conversation title based on first user message (if still default)."""
    if not conversation_id:
        return
    try:
        client = get_supabase_client()
        resp = (
            client.table('conversations')
            .select('title')
            .eq('id', conversation_id)
            .maybe_single()
            .execute()
        )
        if resp.data and resp.data.get("title") == "Chat Session":
            title = first_message[:60] + ("..." if len(first_message) > 60 else "")
            client.table('conversations').update({"title": title}).eq('id', conversation_id).execute()
    except Exception:
        pass


@api_view(['POST'])
def chat_view(request):
    """
    POST /api/chat/
    Body: { messages: [{role, content}], dataset_id?: string, conversation_id?: string }
    Returns: { reply, action?, chart?, visual3d?, conversation_id }
    """
    user_id, err_response = _authenticate_request(request)
    if err_response:
        return err_response

    retry_after = _check_chat_rate_limit(user_id)
    if retry_after is not None:
        return Response({"error": "Too many requests", "retry_after_seconds": retry_after}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    body = request.data
    messages = body.get("messages", [])
    if not messages:
        return Response({"error": "No messages provided"}, status=status.HTTP_400_BAD_REQUEST)

    messages = _truncate_history(messages)
    conversation_id = body.get("conversation_id") or None
    active_dataset_id = body.get("dataset_id") or None

    try:
        _ensure_gemini()
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Get or create conversation for persistence
    conversation_id = _get_or_create_conversation(user_id, conversation_id)

    # Build dynamic system prompt with active dataset context
    system_prompt = SYSTEM_PROMPT
    if active_dataset_id:
        ds = get_dataset(active_dataset_id)
        if ds:
            fname = _get_dataset_filename(ds)
            gc = _parse_json_if_string(ds.get("global_context")) or {}
            cat = ((gc.get("category_detection") or {}).get("resolved_category")
                   or ds.get("category") or ds.get("category_hint") or "unknown")
            system_prompt = (
                SYSTEM_PROMPT
                + f"\n\nCURRENT DATASET CONTEXT:\n"
                f"- dataset_id: {active_dataset_id}\n"
                f"- filename: {fname}\n"
                f"- category: {cat}\n"
                f"Use this dataset_id directly for any data operations. Do NOT call list_projects first."
            )

    chat_history = []
    for msg in messages[:-1]:
        role = "user" if msg.get("role") == "user" else "model"
        chat_history.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg.get("content", ""))]
        ))

    last_message = messages[-1].get("content", "")

    last_error = None
    response = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=[
                    *chat_history,
                    types.Content(role="user", parts=[types.Part.from_text(text=last_message)]),
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=TOOLS,
                    temperature=0.7,
                    max_output_tokens=2048,
                ),
            )
            break
        except Exception as e:
            last_error = e
            logger.warning(f"Chat: model {model_name} failed: {e}")
            continue
    else:
        return Response(
            {"error": f"AI service unavailable: {str(last_error)}"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    action = None
    chart = None
    visual_3d = None

    if response.candidates and response.candidates[0].content.parts:
        parts = response.candidates[0].content.parts

        for part in parts:
            if part.function_call:
                func_name = part.function_call.name
                func_args = dict(part.function_call.args) if part.function_call.args else {}

                logger.info(f"Chat: executing {func_name}({func_args})")
                result, fn_action, fn_chart, fn_vis = _execute_function(func_name, func_args, user_id)
                if fn_action:
                    action = fn_action
                if fn_chart:
                    chart = fn_chart
                if fn_vis:
                    visual_3d = fn_vis

                try:
                    follow_up_contents = [
                        *chat_history,
                        types.Content(role="user", parts=[types.Part.from_text(text=last_message)]),
                        types.Content(role="model", parts=[part]),
                        types.Content(role="user", parts=[
                            types.Part.from_function_response(name=func_name, response=result)
                        ]),
                    ]

                    follow_up = None
                    for model_name in GEMINI_MODEL_CHAIN:
                        try:
                            follow_up = _client.models.generate_content(
                                model=model_name,
                                contents=follow_up_contents,
                                config=types.GenerateContentConfig(
                                    system_instruction=SYSTEM_PROMPT,
                                    tools=TOOLS,
                                    temperature=0.7,
                                    max_output_tokens=2048,
                                ),
                            )
                            break
                        except Exception:
                            continue

                    if follow_up and follow_up.candidates and follow_up.candidates[0].content.parts:
                        reply_text = follow_up.candidates[0].content.parts[0].text or ""
                    else:
                        reply_text = result.get("message", json.dumps(result)[:500])

                    _persist_chat_messages(conversation_id, last_message, reply_text, chart, visual_3d)
                    _update_conversation_title(conversation_id, last_message)

                    return Response({"reply": reply_text, "action": action, "chart": chart, "visual3d": visual_3d, "conversation_id": conversation_id})

                except Exception as e:
                    logger.error(f"Chat follow-up failed: {e}")
                    fallback_reply = result.get("message", str(result)[:300])
                    _persist_chat_messages(conversation_id, last_message, fallback_reply, chart, visual_3d)
                    _update_conversation_title(conversation_id, last_message)

                    return Response({
                        "reply": fallback_reply,
                        "action": action, "chart": chart, "visual3d": visual_3d,
                        "conversation_id": conversation_id,
                    })

        reply_text = ""
        for part in parts:
            if hasattr(part, 'text') and part.text:
                reply_text += part.text

        if reply_text:
            _persist_chat_messages(conversation_id, last_message, reply_text, chart, visual_3d)
            _update_conversation_title(conversation_id, last_message)
            return Response({"reply": reply_text, "action": action, "chart": chart, "visual3d": visual_3d, "conversation_id": conversation_id})

    fallback = "I'm not sure how to help with that. Could you rephrase?"
    _persist_chat_messages(conversation_id, last_message, fallback, None, None)
    return Response({"reply": fallback, "action": None, "chart": None, "visual3d": None, "conversation_id": conversation_id})


# ══════════════════════════════════════════════════════════════════════
# STREAMING ENDPOINT
# ══════════════════════════════════════════════════════════════════════

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _build_system_prompt(active_dataset_id: str | None) -> str:
    if not active_dataset_id:
        return SYSTEM_PROMPT
    ds = get_dataset(active_dataset_id)
    if not ds:
        return SYSTEM_PROMPT
    fname = _get_dataset_filename(ds)
    gc = _parse_json_if_string(ds.get("global_context")) or {}
    cat = ((gc.get("category_detection") or {}).get("resolved_category")
           or ds.get("category") or ds.get("category_hint") or "unknown")
    return (
        SYSTEM_PROMPT
        + f"\n\nCURRENT DATASET CONTEXT:\n"
        f"- dataset_id: {active_dataset_id}\n"
        f"- filename: {fname}\n"
        f"- category: {cat}\n"
        f"Use this dataset_id directly for any data operations. Do NOT call list_projects first."
    )


def _stream_chat_generator(user_id: str, messages: list, conversation_id: str | None, system_prompt: str, active_dataset_id: str | None = None):
    """SSE generator for streaming chat responses.

    Streams text chunks from Gemini as they arrive (using `generate_content_stream`)
    so the frontend can start TTS for the first sentence while later sentences
    are still being generated. Tool/function calls are detected mid-stream and
    handled by switching to a tool-execution loop.
    """
    try:
        _ensure_gemini()
    except ValueError as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    conversation_id = _get_or_create_conversation(user_id, conversation_id)

    chat_history = []
    for msg in messages[:-1]:
        role = "user" if msg.get("role") == "user" else "model"
        chat_history.append(types.Content(role=role, parts=[types.Part.from_text(text=msg.get("content", ""))]))

    last_message = messages[-1].get("content", "")
    cfg = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=TOOLS,
        temperature=0.7,
        max_output_tokens=2048,
    )
    contents = [
        *chat_history,
        types.Content(role="user", parts=[types.Part.from_text(text=last_message)]),
    ]

    action = None
    chart = None
    visual_3d = None
    metrics_emitted = False
    reply_text = ""

    def _stream_one(call_contents):
        """Open a streaming generation against the Gemini model chain.

        Returns the iterator from the first model that accepts the request. If
        every model fails, returns ``None``.
        """
        for model_name in GEMINI_MODEL_CHAIN:
            try:
                return _client.models.generate_content_stream(
                    model=model_name, contents=call_contents, config=cfg
                )
            except Exception as e:
                logger.warning(f"Stream call {model_name} failed: {e}")
        return None

    def _consume_stream(stream):
        """Iterate a streaming response.

        Yields ('text', text_chunk) tuples for incremental text and finally
        returns (function_call_part_or_None, accumulated_text). The caller
        forwards each text tuple onto the SSE wire.
        """
        func_part = None
        acc_text = ""
        try:
            for chunk in stream:
                if not getattr(chunk, "candidates", None):
                    continue
                cand = chunk.candidates[0]
                cparts = (getattr(cand, "content", None).parts
                          if getattr(cand, "content", None) else None) or []
                for part in cparts:
                    if getattr(part, "function_call", None):
                        func_part = part
                        continue
                    text = getattr(part, "text", None)
                    if text:
                        acc_text += text
                        yield ("text", text)
        except Exception as e:
            logger.warning(f"Stream iteration failed: {e}")
        yield ("done", {"func_part": func_part, "text": acc_text})

    # ── First call: stream tokens, but watch for a function_call part ────────
    stream = _stream_one(contents)
    if not stream:
        yield _sse({"type": "error", "message": "AI service unavailable"})
        return

    func_part = None
    pre_tool_text = ""  # text emitted before a function_call appears (kept in reply)
    for kind, payload in _consume_stream(stream):
        if kind == "text":
            reply_text += payload
            pre_tool_text += payload
            yield _sse({"type": "chunk", "text": payload})
        elif kind == "done":
            func_part = payload["func_part"]

    if func_part:
        # ── Function-call path: loop up to 3 chained tool calls ─────────────
        # If the model also emitted prose before the function_call we keep it
        # streamed (the user already saw it). The follow-up will append the
        # tool-result narration to it.
        current_contents = list(contents)
        current_func_part = func_part
        MAX_TOOL_ROUNDS = 3

        for _round in range(MAX_TOOL_ROUNDS):
            func_name = current_func_part.function_call.name
            func_args = dict(current_func_part.function_call.args) if current_func_part.function_call.args else {}

            yield _sse({"type": "function_call", "name": func_name})

            result, fn_action, fn_chart, fn_vis = _execute_function(func_name, func_args, user_id)
            if fn_action:
                action = fn_action
            if fn_chart:
                chart = fn_chart
                yield _sse({"type": "chart", "data": fn_chart})
            if fn_vis:
                visual_3d = fn_vis
                yield _sse({"type": "visual3d", "data": fn_vis})
            if isinstance(result, dict) and result.get("__kind__") == "metrics":
                metrics_emitted = True
                yield _sse({"type": "metrics", "data": result})

            current_contents = [
                *current_contents,
                types.Content(role="model", parts=[current_func_part]),
                types.Content(role="user", parts=[types.Part.from_function_response(name=func_name, response=result)]),
            ]

            follow_stream = _stream_one(current_contents)
            if not follow_stream:
                fallback = result.get("message", json.dumps(result)[:300])
                reply_text += fallback
                yield _sse({"type": "chunk", "text": fallback})
                break

            next_func_part = None
            follow_text = ""
            for kind, payload in _consume_stream(follow_stream):
                if kind == "text":
                    reply_text += payload
                    follow_text += payload
                    yield _sse({"type": "chunk", "text": payload})
                elif kind == "done":
                    next_func_part = payload["func_part"]

            if next_func_part:
                current_func_part = next_func_part
                continue

            if not follow_text:
                if chart or visual_3d:
                    msg = "Here's your visualization."
                    reply_text += msg
                    yield _sse({"type": "chunk", "text": msg})
                else:
                    fallback = result.get("message", json.dumps(result)[:300])
                    reply_text += fallback
                    yield _sse({"type": "chunk", "text": fallback})
            break

    # ── Deterministic safety net ─────────────────────────────────────────────
    # Models sometimes *claim* to have made a KPI/metric card without actually
    # calling generate_metrics, leaving the board empty. If the user clearly
    # asked for a card and nothing visual was produced, build it ourselves.
    if active_dataset_id and not metrics_emitted and not chart and not visual_3d:
        lm = last_message.lower()
        wants_card = (
            "kpi" in lm or "kbi" in lm or "metric" in lm or "key metrics" in lm
            or ("card" in lm and any(v in lm for v in
                ("create", "make", "add", "generate", "show", "give", "build", "want", "have")))
        )
        if wants_card:
            try:
                m_result, _, _, _ = _execute_function(
                    "generate_metrics",
                    {"dataset_id": active_dataset_id, "description": last_message},
                    user_id,
                )
                if isinstance(m_result, dict) and m_result.get("__kind__") == "metrics":
                    metrics_emitted = True
                    yield _sse({"type": "metrics", "data": m_result})
            except Exception:
                logger.exception("generate_metrics safety-net failed")

    # If the entire pipeline produced no text at all (rare), emit a fallback
    # so the user always hears *something* in voice mode.
    if not reply_text.strip():
        fallback = "I'm not sure how to help with that."
        reply_text = fallback
        yield _sse({"type": "chunk", "text": fallback})

    # ── Persist in background so the SSE 'done' event isn't blocked ──────────
    # Two Supabase round-trips (~200-500ms) used to gate the final event; now
    # they run on a daemon thread and the user's audio queue can keep playing.
    def _bg_persist():
        try:
            _persist_chat_messages(conversation_id, last_message, reply_text, chart, visual_3d)
            _update_conversation_title(conversation_id, last_message)
        except Exception:
            logger.exception("Background chat persistence failed")

    threading.Thread(target=_bg_persist, daemon=True).start()
    yield _sse({"type": "done", "conversation_id": conversation_id, "action": action})


@csrf_exempt
def chat_stream_view(request):
    """
    POST /api/chat/stream/
    SSE endpoint — streams reply tokens as they're generated.
    """
    if request.method != "POST":
        from django.http import JsonResponse
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        from django.http import JsonResponse
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    auth_header = request.headers.get("Authorization", "")
    try:
        user_info = verify_supabase_token(auth_header)
        user_id = user_info["user_id"]
    except ValueError as e:
        from django.http import JsonResponse
        return JsonResponse({"error": str(e)}, status=401)

    retry_after = _check_chat_rate_limit(user_id)
    if retry_after is not None:
        from django.http import JsonResponse
        return JsonResponse({"error": "Too many requests", "retry_after_seconds": retry_after}, status=429)

    messages = body.get("messages", [])
    if not messages:
        from django.http import JsonResponse
        return JsonResponse({"error": "No messages"}, status=400)

    messages = _truncate_history(messages)
    conversation_id = body.get("conversation_id") or None
    active_dataset_id = body.get("dataset_id") or None
    voice_mode = bool(body.get("voice_mode"))
    voice_language = (body.get("voice_language") or "en").strip()
    system_prompt = _build_system_prompt(active_dataset_id)
    if voice_mode:
        domain_voice = (
            "\n\nDOMAIN REMINDER (voice): You are NOT a general chatbot. "
            "If the user asks anything outside their AxBi data or platform features, "
            "refuse in one short spoken sentence and redirect to data/analytics help."
        )
        if voice_language == "ar-EG":
            system_prompt += (
                "\n\nVOICE CONVERSATION MODE (Egyptian Arabic):\n"
                "- The user is talking to you out loud and listening to your voice reply.\n"
                "- Reply ONLY in casual Egyptian Arabic (Masri). Do NOT use Fusha / MSA.\n"
                "- Be VERY concise: 1-2 short sentences (max ~40 words).\n"
                "- No markdown, no bullet points, no code blocks, no headings.\n"
                "- Plain spoken sentences only. Numbers as words where natural.\n"
                "- Sound warm, friendly, and natural — like chatting with a friend.\n"
                "- لو السؤال برا الداتا أو منصة AxBi (رياضة، مشاهير، أسئلة عامة): ارفض بلطف وارجّع للتحليلات والداتا بتاعته."
                + domain_voice
            )
        else:
            system_prompt += (
                "\n\nVOICE CONVERSATION MODE:\n"
                "- The user is talking to you out loud and listening to your voice reply.\n"
                "- Be VERY concise: 1-2 short sentences (max ~40 words).\n"
                "- No markdown, no bullet points, no code blocks, no headings.\n"
                "- Plain spoken sentences only.\n"
                "- Sound warm, friendly, and natural — like talking, not writing."
                + domain_voice
            )

    resp = StreamingHttpResponse(
        _stream_chat_generator(user_id, messages, conversation_id, system_prompt, active_dataset_id),
        content_type="text/event-stream",
    )
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp
