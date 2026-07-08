"""
Generic Data Segmentation Service.

Orchestrates entity detection, strategy selection, segmentation execution,
and AI-powered insight generation. Works across all 4 categories
(HR, Marketing, Sales, Operations) by adapting the method to the data shape.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, UTC

import numpy as np
import pandas as pd

from .strategies import rfm_segmentation, abc_analysis, kmeans_segmentation

logger = logging.getLogger(__name__)

# K-Means + PCA fit on the full DataFrame with no bound. Cap the rows fed to the
# clustering path so a 400k-row upload doesn't blow up memory / fit time. RFM and ABC
# stay on the full df — they aggregate to one row per entity anyway, so row count does
# not inflate their cost. Mirrors the sampling used elsewhere (feature_recommender,
# scatter sampling) with a fixed seed for determinism.
SEGMENTATION_MAX_ROWS = 50000
# Hard wall on the clustering fit so it can't hang a worker on pathological data.
SEGMENTATION_TIMEOUT_S = 120
# Entity-level Pareto curve is capped for readability + payload size.
PARETO_MAX_POINTS = 40
# Evidence-strength heuristics for grounding the Gemini insight prompt. Below this
# entity count the sample is flagged "small"; segments whose primary metric are
# within this relative % are flagged "marginally separated". Tunable — they only
# change how much the prompt hedges, never the math.
SEG_SMALL_N = 30
SEG_MARGIN_PCT = 10


def _run_with_timeout(fn, timeout_seconds: int, *args, **kwargs):
    """Run *fn* in a background thread; raise on timeout.

    Mirrors forecasting.service._run_with_timeout but kept local so segmentation does
    not import the heavy forecasting module.
    """
    result: dict = {}

    def _target():
        try:
            result["value"] = fn(*args, **kwargs)
        except Exception as exc:  # propagate the real error to the caller
            result["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    if t.is_alive():
        raise ValueError(
            f"Segmentation timed out after {timeout_seconds}s. "
            "Try a smaller dataset or fewer numeric columns."
        )
    if "error" in result:
        raise result["error"]
    return result.get("value")

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

_client = None

GEMINI_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)


def _ensure_gemini():
    global _client
    if _client is None:
        if genai is None:
            raise ValueError("google-genai is not installed.")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        _client = genai.Client(api_key=api_key)


def _parse_json_maybe(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def run_segmentation_service(
    df: pd.DataFrame,
    columns_metadata: list[dict],
    category_hint: str | None = None,
) -> dict:
    """
    Run generic data segmentation.

    1. Detect entity type and pick strategy from column metadata
    2. Execute the segmentation (RFM / ABC / K-Means)
    3. Build segment summaries
    4. Generate AI insights via Gemini

    Returns a dict suitable for storing in global_context.segmentation.
    """
    start_ms = time.time()

    detection = _detect_entity_and_strategy(columns_metadata, category_hint)
    method = detection["method"]
    entity_col = detection["entity_column"]
    logger.info(
        "Segmentation: method=%s entity=%s category=%s",
        method, entity_col, category_hint,
    )

    if method == "rfm":
        segments_df = rfm_segmentation(
            df,
            entity_col=entity_col,
            date_col=detection["date_column"],
            monetary_col=detection["monetary_column"],
        )
        segments_summary = _build_rfm_summary(segments_df)
        scatter_data = None
        method_meta = {
            "entity_count": len(segments_df),
            "score_columns": ["r_score", "f_score", "m_score"],
        }

    elif method == "abc":
        segments_df = abc_analysis(
            df,
            entity_col=entity_col,
            value_col=detection["value_column"],
        )
        segments_summary = _build_abc_summary(segments_df)
        scatter_data = None
        method_meta = {
            "entity_count": len(segments_df),
            "value_column": detection["value_column"],
        }

    else:
        numeric_cols = detection["numeric_columns"]
        # Bound the rows fed to K-Means/PCA and wrap the fit in a timeout.
        kmeans_df = df
        if len(df) > SEGMENTATION_MAX_ROWS:
            kmeans_df = df.sample(n=SEGMENTATION_MAX_ROWS, random_state=42)
            logger.info(
                "Segmentation: sampled %d -> %d rows for K-Means",
                len(df), len(kmeans_df),
            )
        segments_df, kmeans_meta = _run_with_timeout(
            kmeans_segmentation,
            SEGMENTATION_TIMEOUT_S,
            kmeans_df,
            entity_col=entity_col,
            numeric_cols=numeric_cols,
        )
        kmeans_meta = {**kmeans_meta, "sampled_rows": len(kmeans_df), "input_rows": len(df)}
        segments_summary = _build_kmeans_summary(segments_df, numeric_cols)
        scatter_data = _build_scatter_data(segments_df)
        method_meta = {
            "entity_count": len(segments_df),
            **kmeans_meta,
        }

    # AI-powered segment naming + insights
    try:
        _ensure_gemini()
        ai_result = _generate_segment_insights(
            segments_summary, category_hint or "Business", method, detection, method_meta
        )
    except Exception as e:
        logger.warning("Segmentation AI insights failed: %s", e)
        ai_result = _fallback_insights(segments_summary, method)

    # Apply AI-generated names to segments if available
    if method == "kmeans" and ai_result.get("segment_names"):
        name_map = ai_result["segment_names"]
        for seg in segments_summary:
            old_name = seg["name"]
            if old_name in name_map:
                seg["name"] = name_map[old_name]

    # Build pre-computed chart data
    charts = _build_chart_data(segments_summary, method)
    if method == "abc":
        charts.append(_build_pareto_chart(segments_df))

    duration_ms = int((time.time() - start_ms) * 1000)

    return {
        "status": "completed",
        "generated_at": datetime.now(UTC).isoformat(),
        "method": method,
        "method_label": {"rfm": "RFM Analysis", "abc": "ABC / Pareto Analysis", "kmeans": "K-Means Clustering"}[method],
        "entity_column": entity_col,
        "category": category_hint or "Business",
        "method_meta": method_meta,
        "segments": segments_summary,
        "insights": ai_result.get("insights", []),
        "charts": charts,
        "scatter_data": scatter_data,
        "duration_ms": duration_ms,
    }


# ══════════════════════════════════════════════════════════════
# ENTITY & STRATEGY DETECTION
# ══════════════════════════════════════════════════════════════

# Names that signal a non-additive (rate/ratio/%/average) metric — summing them
# is meaningless, so ABC must not pick them as its value column.
_NON_ADDITIVE_NAME_RE = re.compile(
    r"(rate|ratio|percent|pct|margin|\baverage\b|\bavg\b|\bmean\b)", re.I
)


def _is_non_additive_measure(name: str, ai: dict, stats: dict) -> bool:
    """True for rate/ratio/%/average-style columns that must not be summed."""
    agg = str(ai.get("suggested_aggregation", "")).lower()
    if agg in ("avg", "mean", "average"):
        return True
    if _NON_ADDITIVE_NAME_RE.search(name or ""):
        return True
    try:
        mn, mx = stats.get("min"), stats.get("max")
        if mn is not None and mx is not None and float(mn) >= 0 and float(mx) <= 1:
            return True  # bounded fraction → ratio
    except (TypeError, ValueError):
        pass
    return False


def _detect_entity_and_strategy(
    columns_metadata: list[dict],
    category_hint: str | None,
) -> dict:
    """
    Analyze column metadata to pick the best segmentation strategy.

    Priority:
    1. If id + date + monetary columns exist -> RFM
    2. If dimension + numeric value column -> ABC
    3. Fallback -> K-Means on all numeric columns
    """
    id_cols = []
    date_cols = []
    measure_cols = []
    additive_measures = []   # subset of measure_cols safe to sum (ABC value candidates)
    dimension_cols = []
    all_numeric = []

    for col in columns_metadata:
        ai = _parse_json_maybe(col.get("ai_profile"))
        stats = _parse_json_maybe(col.get("technical_stats")) or {}
        role = str(ai.get("role", "")).lower()
        data_type = str(col.get("data_type", "")).lower()
        name = col.get("clean_name") or col.get("original_name") or ""
        is_primary = bool(col.get("is_primary_metric", False))

        if role == "id":
            id_cols.append(name)
        if role == "date" or data_type == "datetime":
            date_cols.append(name)
        if role == "measure" or data_type == "numeric":
            all_numeric.append(name)
            non_additive = _is_non_additive_measure(name, ai, stats)
            if is_primary:
                measure_cols.insert(0, name)
                if not non_additive:
                    additive_measures.insert(0, name)
            else:
                measure_cols.append(name)
                if not non_additive:
                    additive_measures.append(name)
        if role in ("dimension", "descriptive", "geographic"):
            dimension_cols.append(name)

    category = (category_hint or "").lower()

    # Strategy 1: RFM if we have id + date + monetary
    if id_cols and date_cols and measure_cols:
        return {
            "method": "rfm",
            "entity_column": id_cols[0],
            "date_column": date_cols[0],
            "monetary_column": measure_cols[0],
        }

    # Strategy 2: ABC if we have a dimension and an ADDITIVE value column.
    # ABC sums the value per entity, so a non-additive metric (rate/ratio/%) is
    # excluded — such datasets fall through to K-Means (groups without summing).
    entity_for_abc = dimension_cols[0] if dimension_cols else (id_cols[0] if id_cols else None)
    if entity_for_abc and additive_measures:
        return {
            "method": "abc",
            "entity_column": entity_for_abc,
            "value_column": additive_measures[0],
        }

    # Strategy 3: K-Means fallback
    entity_col = id_cols[0] if id_cols else (dimension_cols[0] if dimension_cols else None)
    numeric_for_kmeans = [c for c in all_numeric if c != entity_col]
    if len(numeric_for_kmeans) < 2:
        numeric_for_kmeans = all_numeric[:] if len(all_numeric) >= 2 else all_numeric

    if len(numeric_for_kmeans) < 1:
        raise ValueError(
            "Cannot segment this dataset: no numeric columns found. "
            "At least 2 numeric columns are needed for clustering."
        )

    return {
        "method": "kmeans",
        "entity_column": entity_col,
        "numeric_columns": numeric_for_kmeans,
    }


# ══════════════════════════════════════════════════════════════
# SUMMARY BUILDERS
# ══════════════════════════════════════════════════════════════

def _safe_json_value(val):
    """Convert numpy/pandas types to JSON-safe Python primitives."""
    # pandas NA/NaT/None → null
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        f = float(val)
        return None if not np.isfinite(f) else round(f, 2)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    if isinstance(val, float):
        return None if not np.isfinite(val) else round(val, 2)
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return val
    # Fallback: try converting to float, otherwise stringify
    try:
        f = float(val)
        return None if not np.isfinite(f) else round(f, 2)
    except (TypeError, ValueError):
        return str(val)


def _build_rfm_summary(rfm_df: pd.DataFrame) -> list[dict]:
    total = len(rfm_df)
    segments = []
    for seg_name, group in rfm_df.groupby("segment"):
        top_entities = (
            group.nlargest(5, "monetary")["entity"]
            .astype(str).tolist()
        )
        segments.append({
            "name": str(seg_name),
            "size": int(len(group)),
            "percentage": round(len(group) / total * 100, 1),
            "avg_metrics": {
                "recency": _safe_json_value(group["recency"].mean()),
                "frequency": _safe_json_value(group["frequency"].mean()),
                "monetary": _safe_json_value(group["monetary"].mean()),
            },
            "top_entities": top_entities,
        })
    segments.sort(key=lambda s: s["size"], reverse=True)
    return segments


def _build_abc_summary(abc_df: pd.DataFrame) -> list[dict]:
    total = len(abc_df)
    total_value = abc_df["total_value"].sum()
    segments = []
    for seg_name, group in abc_df.groupby("segment"):
        top_entities = (
            group.nlargest(5, "total_value")["entity"]
            .astype(str).tolist()
        )
        segments.append({
            "name": str(seg_name),
            "size": int(len(group)),
            "percentage": round(len(group) / total * 100, 1),
            "avg_metrics": {
                # Segment SUM (not per-entity mean) so it reconciles with value_share.
                "total_value": _safe_json_value(group["total_value"].sum()),
                "value_share": _safe_json_value(group["total_value"].sum() / total_value * 100),
            },
            "top_entities": top_entities,
        })
    segments.sort(key=lambda s: s["avg_metrics"].get("value_share", 0), reverse=True)
    return segments


def _build_kmeans_summary(result_df: pd.DataFrame, numeric_cols: list[str]) -> list[dict]:
    total = len(result_df)
    segments = []
    for cluster_id, group in result_df.groupby("cluster"):
        avg_metrics = {}
        for col in numeric_cols:
            if col in group.columns:
                avg_metrics[col] = _safe_json_value(group[col].mean())

        top_entities = []
        if "entity" in group.columns:
            top_entities = group["entity"].head(5).astype(str).tolist()

        segments.append({
            "name": f"Cluster {int(cluster_id) + 1}",
            "size": int(len(group)),
            "percentage": round(len(group) / total * 100, 1),
            "avg_metrics": avg_metrics,
            "top_entities": top_entities,
        })
    segments.sort(key=lambda s: s["size"], reverse=True)
    return segments


def _build_scatter_data(result_df: pd.DataFrame) -> list[dict]:
    """Build scatter plot data from K-Means PCA results (sampled for frontend)."""
    if "pca_x" not in result_df.columns:
        return []
    sampled = result_df.sample(n=min(500, len(result_df)), random_state=42)
    points = []
    for _, row in sampled.iterrows():
        points.append({
            "x": _safe_json_value(row["pca_x"]),
            "y": _safe_json_value(row["pca_y"]),
            "cluster": int(row["cluster"]),
            "entity": str(row.get("entity", "")),
        })
    return points


# ══════════════════════════════════════════════════════════════
# CHART DATA BUILDER
# ══════════════════════════════════════════════════════════════

def _build_pareto_chart(abc_df: pd.DataFrame) -> dict:
    """Entity-level Pareto curve from the (already value-desc-sorted) ABC frame.

    Both series are percentages of the grand total so they share ONE 0–100 axis
    (no dual-axis): bar = each entity's individual value share, line = cumulative
    share (precomputed over ALL entities upstream, so accurate where drawn).
    """
    grand_total = float(abc_df["total_value"].sum()) or 1.0
    head = abc_df.head(PARETO_MAX_POINTS)
    data = [
        {
            "entity": str(r["entity"]),
            "value_share": _safe_json_value(r["total_value"] / grand_total * 100),
            "cumulative_pct": _safe_json_value(r["cumulative_pct"]),
            "total_value": _safe_json_value(r["total_value"]),
            "segment": str(r["segment"]),
        }
        for _, r in head.iterrows()
    ]
    return {
        "chart_type": "pareto",
        "title": "Pareto Analysis — Cumulative Value",
        "data": data,
    }


def _build_chart_data(segments: list[dict], method: str) -> list[dict]:
    """Build pre-computed chart data for the frontend."""
    charts = []

    # Pie chart: segment distribution
    pie_data = [{"name": s["name"], "value": s["size"]} for s in segments]
    charts.append({
        "chart_type": "pie",
        "title": "Segment Distribution",
        "data": pie_data,
    })

    # Bar chart: primary VALUE metric by segment. (The old "Entities per Segment"
    # count bar was dropped — it duplicated the pie above.) Title reflects the
    # metric's semantics: a total for ABC, an average for RFM/K-Means.
    if segments and segments[0].get("avg_metrics"):
        metrics = segments[0]["avg_metrics"]
        if method == "abc":
            primary_key, title = "total_value", "Total Value by Segment"
        elif method == "rfm":
            primary_key, title = "monetary", "Average Monetary by Segment"
        else:
            primary_key = next(iter(metrics), None)
            title = (
                f"Average {primary_key.replace('_', ' ').title()} by Segment"
                if primary_key else None
            )

        if primary_key:
            metric_data = [
                {"label": s["name"], "value": s["avg_metrics"].get(primary_key, 0)}
                for s in segments
            ]
            charts.append({
                "chart_type": "bar",
                "title": title,
                "data": metric_data,
            })

    return charts


# ══════════════════════════════════════════════════════════════
# AI INSIGHTS GENERATION
# ══════════════════════════════════════════════════════════════

# What each method's segment labels actually mean — so Gemini reads magnitude
# tiers as magnitude, not as performance/quality judgments.
_METHOD_SEMANTICS = {
    "abc": (
        "A/B/C are High/Medium/Low tiers by cumulative share of an additive value "
        "metric — they rank contribution to the total, NOT efficiency, quality, or "
        "performance. A high-share entity is simply large on that metric."
    ),
    "rfm": (
        "Tiers come from Recency/Frequency/Monetary quantile scores of the "
        "customer's own transactions — they describe buying behavior, not "
        "profitability or intent."
    ),
    "kmeans": (
        "Clusters are unsupervised proximity groups on the numeric columns; their "
        "names are descriptive, not evaluative judgments."
    ),
}


def _primary_metric_key(method: str, avg_metrics: dict) -> str | None:
    """The single per-segment metric used for separation checks."""
    if method == "abc":
        return "value_share" if "value_share" in avg_metrics else None
    if method == "rfm":
        return "monetary" if "monetary" in avg_metrics else None
    return next(iter(avg_metrics), None)  # kmeans: first numeric


def _build_evidence_context(
    segments: list[dict],
    method: str,
    detection: dict,
    method_meta: dict | None,
) -> dict:
    """Dataset-agnostic reliability signals so Gemini can scale its confidence.

    Computed only from data already in hand — no recompute of the segmentation.
    """
    method_meta = method_meta or {}
    entity_count = sum(int(s.get("size", 0)) for s in segments)

    if method == "abc":
        metrics_used = [detection.get("value_column")]
    elif method == "rfm":
        metrics_used = [
            detection.get("monetary_column"),
            detection.get("date_column"),
        ]
    else:
        metrics_used = list(detection.get("numeric_columns", []))
    metrics_used = [m for m in metrics_used if m]

    single_entity_segments = [
        str(s["name"]) for s in segments if int(s.get("size", 0)) == 1
    ]

    # Marginal separation: any two segments whose primary metric are within
    # SEG_MARGIN_PCT relative %. For K-Means also treat a low silhouette as weak.
    marginal_separation = False
    key = _primary_metric_key(method, segments[0].get("avg_metrics", {})) if segments else None
    if key:
        vals = [
            s["avg_metrics"].get(key)
            for s in segments
            if isinstance(s.get("avg_metrics"), dict) and s["avg_metrics"].get(key) is not None
        ]
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                a, b = float(vals[i]), float(vals[j])
                denom = max(abs(a), abs(b)) or 1.0
                if abs(a - b) / denom * 100 <= SEG_MARGIN_PCT:
                    marginal_separation = True
    sil = method_meta.get("silhouette_score")
    if method == "kmeans" and sil is not None and float(sil) < 0.25:
        marginal_separation = True

    return {
        "entity_count": entity_count,
        "segment_count": len(segments),
        "metrics_used": metrics_used,
        "small_sample": entity_count < SEG_SMALL_N,
        "single_entity_segments": single_entity_segments,
        "marginal_separation": marginal_separation,
        "silhouette_score": _safe_json_value(sil) if sil is not None else None,
        "metric_semantics": _METHOD_SEMANTICS.get(method, ""),
    }


def _generate_segment_insights(
    segments: list[dict],
    category: str,
    method: str,
    detection: dict,
    method_meta: dict | None = None,
) -> dict:
    """Ask Gemini to name clusters (for K-Means) and generate business insights."""
    global _client
    if _client is None:
        _ensure_gemini()
    if genai_types is None:
        raise ValueError("google-genai not installed")

    method_label = {"rfm": "RFM", "abc": "ABC/Pareto", "kmeans": "K-Means Clustering"}[method]

    segments_for_prompt = []
    for s in segments:
        segments_for_prompt.append({
            "name": s["name"],
            "size": s["size"],
            "percentage": s["percentage"],
            "avg_metrics": s["avg_metrics"],
        })

    needs_naming = method == "kmeans"

    evidence = _build_evidence_context(segments, method, detection, method_meta)
    metrics_used = evidence["metrics_used"]
    metrics_str = ", ".join(metrics_used) if metrics_used else "(none identified)"

    prompt = (
        f"You are a senior {category} analyst. A {method_label} segmentation was performed "
        f"on a {category} dataset.\n\n"
        f"Entity column: {detection.get('entity_column', 'N/A')}\n"
        f"Segmentation method: {method_label}\n\n"
        # (a) Data scope + no-invention guardrail
        f"DATA SCOPE — this segmentation used ONLY these field(s): {metrics_str}. "
        "Base every statement strictly on these fields and the segment stats below. "
        "Do NOT infer efficiency, cost, resource usage, quality, growth, profitability, "
        "or root causes — the data does not measure them. If a claim is not supported by "
        "the provided numbers, do not make it.\n\n"
        # (b) What the labels mean
        f"WHAT THE LABELS MEAN — {evidence['metric_semantics']}\n\n"
        # (c) Evidence strength
        f"DATA RELIABILITY (scale your confidence to this):\n"
        f"{json.dumps({k: evidence[k] for k in ('entity_count', 'segment_count', 'small_sample', 'single_entity_segments', 'marginal_separation', 'silhouette_score')}, default=str)}\n"
        "If small_sample or marginal_separation is true, or a segment has a single entity, "
        "state that limitation plainly and frame recommendations as hypotheses to investigate "
        "('investigate whether…'), not directives. Do NOT recommend high-stakes actions "
        "(divestment, layoffs, major restructuring) unless the numbers strongly justify them. "
        "When reliability is low, include ONE insight covering the segmentation's limitations "
        "and what additional data would sharpen it.\n\n"
        f"Segments found:\n{json.dumps(segments_for_prompt, indent=2, default=str)}\n\n"
    )

    if needs_naming:
        prompt += (
            "For each cluster, provide a professional, descriptive business name "
            "(e.g., 'High Performers', 'At Risk', 'Budget Champions').\n\n"
        )

    prompt += (
        "Return a JSON object with:\n"
    )
    if needs_naming:
        prompt += (
            '1. "segment_names": object mapping old cluster name -> new business name\n'
            '2. "insights": array of 3-5 insight objects, each with "title" and "content"\n'
        )
    else:
        prompt += (
            '1. "insights": array of 3-5 insight objects, each with "title" and "content"\n'
        )

    prompt += (
        "\nEach insight should reference specific segment names and the actual metric "
        "value(s) above, and be honest about what the data can and cannot show. "
        "Give recommendations only where the evidence supports them; scale their stakes "
        "to the DATA RELIABILITY signals.\n"
        "Return ONLY valid JSON. No markdown fences."
    )

    config = genai_types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=8192,   # 4096 truncated mid-JSON on multi-segment datasets
        response_mime_type="application/json",
    )

    last_error = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            raw = response.text or ""
            parsed = _parse_ai_response(raw)
            logger.info("Segmentation AI insights generated with model %s", model_name)
            return parsed
        except Exception as exc:
            last_error = exc
            logger.warning("Segmentation AI model %s failed: %s", model_name, exc)

    raise ValueError(f"All Gemini models failed for segmentation insights: {last_error}")


def _parse_ai_response(raw: str) -> dict:
    """Parse the Gemini JSON response for segment insights."""
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start:end + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Salvage: Gemini occasionally emits slightly malformed / truncated JSON
        # (missing comma, cut off mid-array). Rather than throw — which sends the whole
        # request through the model fallback chain (~slow) and then to rule-based
        # insights — pull out whatever complete title/content + name pairs we can.
        return _salvage_ai_response(cleaned)

    if not isinstance(parsed, dict):
        raise ValueError("AI response is not a JSON object")

    result = {}
    if "segment_names" in parsed and isinstance(parsed["segment_names"], dict):
        result["segment_names"] = parsed["segment_names"]

    insights = parsed.get("insights", [])
    if isinstance(insights, list):
        valid = []
        for ins in insights:
            if isinstance(ins, dict) and ins.get("title") and ins.get("content"):
                valid.append({
                    "title": str(ins["title"]).strip(),
                    "content": str(ins["content"]).strip(),
                })
        result["insights"] = valid
    else:
        result["insights"] = []

    return result


def _salvage_ai_response(text: str) -> dict:
    """Best-effort extraction of insights/segment_names from imperfect JSON.

    Regex-pulls complete {"title": ..., "content": ...} objects (order-independent) and
    any "segment_names" map, so a single malformed delimiter doesn't discard a whole
    otherwise-usable Gemini response. Raises only if nothing usable is found.
    """
    import re

    insights: list[dict] = []
    # title/content in either order
    for m in re.finditer(
        r'\{[^{}]*?"title"\s*:\s*"(?P<t>(?:[^"\\]|\\.)*)"[^{}]*?'
        r'"content"\s*:\s*"(?P<c>(?:[^"\\]|\\.)*)"[^{}]*?\}',
        text, re.DOTALL,
    ):
        insights.append({"title": m.group("t").strip(), "content": m.group("c").strip()})

    result: dict = {"insights": insights}

    names_block = re.search(r'"segment_names"\s*:\s*(\{.*?\})', text, re.DOTALL)
    if names_block:
        try:
            names = json.loads(names_block.group(1))
            if isinstance(names, dict):
                result["segment_names"] = {str(k): str(v) for k, v in names.items()}
        except json.JSONDecodeError:
            pass

    if not insights and "segment_names" not in result:
        raise ValueError("AI response unparseable and unsalvageable")
    return result


def _fallback_insights(segments: list[dict], method: str) -> dict:
    """Generate basic insights when Gemini is unavailable."""
    method_label = {"rfm": "RFM", "abc": "ABC/Pareto", "kmeans": "Clustering"}[method]
    total = sum(s["size"] for s in segments)
    largest = max(segments, key=lambda s: s["size"]) if segments else None

    insights = [
        {
            "title": f"{method_label} Segmentation Complete",
            "content": (
                f"The analysis identified {len(segments)} distinct segments across "
                f"{total} entities. Review each segment's metrics to understand "
                "behavioral patterns and prioritize actions."
            ),
        },
    ]

    if largest:
        insights.append({
            "title": f"Largest Segment: {largest['name']}",
            "content": (
                f"The '{largest['name']}' segment contains {largest['size']} entities "
                f"({largest['percentage']}% of total). Understanding this group's "
                "characteristics is key to optimizing overall performance."
            ),
        })

    return {"insights": insights}
