from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

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


# ── Forecast-based detectors ────────────────────────────────────────────────

def detect_forecast_decline(ctx: dict) -> Optional[dict]:
    """Fires when the forecast metric is trending down more than 10%."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    fd = _parse_json_maybe(fc.get("forecast_data")) or {}
    points = fd.get("forecast") or []
    if len(points) < 2:
        return None
    try:
        first = float(points[0]["value"])
        last  = float(points[-1]["value"])
        if first <= 0:
            return None
        drop_pct = (first - last) / first
        if drop_pct < 0.10:
            return None
        return {
            "type":            "forecast_decline",
            "severity":        "critical" if drop_pct > 0.25 else "warning",
            "affected_entity": fc.get("target_column", "metric"),
            "confidence":      0.9 if fc.get("confidence") == "high" else 0.65,
            "evidence": {
                "metric":          fc.get("target_column"),
                "first_value":     round(first, 2),
                "last_value":      round(last, 2),
                "drop_pct":        round(drop_pct * 100, 1),
                "horizon_periods": len(points),
                "best_model":      fc.get("best_model"),
            },
        }
    except Exception:
        return None


def detect_forecast_growth(ctx: dict) -> Optional[dict]:
    """Fires when the forecast shows strong growth above 20%."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    fd = _parse_json_maybe(fc.get("forecast_data")) or {}
    points = fd.get("forecast") or []
    if len(points) < 2:
        return None
    try:
        first = float(points[0]["value"])
        last  = float(points[-1]["value"])
        if first <= 0:
            return None
        growth_pct = (last - first) / first
        if growth_pct < 0.20:
            return None
        return {
            "type":            "forecast_growth",
            "severity":        "info",
            "affected_entity": fc.get("target_column", "metric"),
            "confidence":      0.85 if fc.get("confidence") == "high" else 0.6,
            "evidence": {
                "metric":      fc.get("target_column"),
                "first_value": round(first, 2),
                "last_value":  round(last, 2),
                "growth_pct":  round(growth_pct * 100, 1),
                "best_model":  fc.get("best_model"),
            },
        }
    except Exception:
        return None


def detect_low_confidence_forecast(ctx: dict) -> Optional[dict]:
    """Fires when forecast confidence is low or WAPE is very high (>30%)."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    confidence = fc.get("confidence", "high")
    wape = fc.get("best_wape")
    if confidence == "low" or (wape is not None and float(wape) > 0.30):
        return {
            "type":            "low_confidence_forecast",
            "severity":        "warning",
            "affected_entity": fc.get("target_column", "forecast"),
            "confidence":      0.9,
            "evidence": {
                "confidence_level": confidence,
                "wape_pct":         round(float(wape) * 100, 1) if wape else None,
                "best_model":       fc.get("best_model"),
            },
        }
    return None


def detect_severe_overfit(ctx: dict) -> Optional[dict]:
    """Fires when the selected best model has a severe overfit diagnosis."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    model_results = _parse_json_maybe(fc.get("model_results")) or []
    best_model = fc.get("best_model")
    if not best_model or not model_results:
        return None
    for mr in model_results:
        if mr.get("model") == best_model:
            diag = mr.get("fit_diagnosis")
            if diag in ("severe_overfit", "overfit"):
                return {
                    "type":            "severe_overfit",
                    "severity":        "warning",
                    "affected_entity": best_model,
                    "confidence":      0.95,
                    "evidence": {
                        "model":       best_model,
                        "diagnosis":   diag,
                        "fit_ratio":   mr.get("fit_ratio"),
                    },
                }
    return None


# ── Segmentation-based detectors ─────────────────────────────────────────────

def detect_shrinking_top_segment(ctx: dict) -> Optional[dict]:
    """Fires when the top-value segment (Champions, A-tier) is under 5%."""
    seg = ctx.get("segmentation")
    if not seg:
        return None
    segments = seg.get("segments") or []
    top_names = {"champions", "a-tier", "a tier", "high value", "high-value", "platinum"}
    for s in segments:
        name = str(s.get("name") or s.get("segment") or "").lower()
        size_pct = s.get("size_pct") or s.get("percentage") or 0
        try:
            size_pct = float(size_pct)
        except Exception:
            continue
        if any(t in name for t in top_names) and size_pct < 5.0:
            return {
                "type":            "shrinking_top_segment",
                "severity":        "critical" if size_pct < 2.0 else "warning",
                "affected_entity": s.get("name") or s.get("segment"),
                "confidence":      0.85,
                "evidence": {
                    "segment_name": s.get("name") or s.get("segment"),
                    "size_pct":     round(size_pct, 1),
                    "method":       seg.get("method"),
                },
            }
    return None


def detect_growing_at_risk_segment(ctx: dict) -> Optional[dict]:
    """Fires when the at-risk / low-value segment exceeds 25% of population."""
    seg = ctx.get("segmentation")
    if not seg:
        return None
    segments = seg.get("segments") or []
    risk_names = {"at risk", "at-risk", "churning", "lost", "c-tier", "c tier", "low value", "low-value"}
    for s in segments:
        name = str(s.get("name") or s.get("segment") or "").lower()
        size_pct = s.get("size_pct") or s.get("percentage") or 0
        try:
            size_pct = float(size_pct)
        except Exception:
            continue
        if any(t in name for t in risk_names) and size_pct > 25.0:
            return {
                "type":            "growing_at_risk_segment",
                "severity":        "critical" if size_pct > 40.0 else "warning",
                "affected_entity": s.get("name") or s.get("segment"),
                "confidence":      0.85,
                "evidence": {
                    "segment_name": s.get("name") or s.get("segment"),
                    "size_pct":     round(size_pct, 1),
                    "method":       seg.get("method"),
                },
            }
    return None


def detect_concentration_risk(ctx: dict) -> Optional[dict]:
    """Fires when a single segment holds >70% of total value (ABC over-concentration)."""
    seg = ctx.get("segmentation")
    if not seg or seg.get("method") != "abc":
        return None
    segments = seg.get("segments") or []
    for s in segments:
        name = str(s.get("name") or s.get("segment") or "").lower()
        value_pct = s.get("value_pct") or s.get("revenue_pct") or 0
        try:
            value_pct = float(value_pct)
        except Exception:
            continue
        if "a" in name and value_pct > 70.0:
            return {
                "type":            "concentration_risk",
                "severity":        "warning",
                "affected_entity": s.get("name") or s.get("segment"),
                "confidence":      0.9,
                "evidence": {
                    "segment_name": s.get("name") or s.get("segment"),
                    "value_pct":    round(value_pct, 1),
                },
            }
    return None


# ── Data quality detectors ───────────────────────────────────────────────────

def detect_high_null_columns(ctx: dict) -> Optional[dict]:
    """Fires when any column has more than 30% null values."""
    columns = ctx.get("columns") or []
    bad_cols = []
    for col in columns:
        stats = col.get("technical_stats") or {}
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except Exception:
                stats = {}
        null_ratio = stats.get("null_ratio") or stats.get("null_pct") or 0
        try:
            null_ratio = float(null_ratio)
        except Exception:
            continue
        if null_ratio > 0.30:
            bad_cols.append({
                "column":     col.get("column_name") or col.get("name"),
                "null_ratio": round(null_ratio * 100, 1),
            })
    if not bad_cols:
        return None
    return {
        "type":            "high_null_columns",
        "severity":        "critical" if len(bad_cols) > 3 else "warning",
        "affected_entity": ", ".join(c["column"] for c in bad_cols[:3] if c["column"]),
        "confidence":      1.0,
        "evidence": {
            "columns":    bad_cols[:5],
            "count":      len(bad_cols),
        },
    }


def detect_stale_data(ctx: dict) -> Optional[dict]:
    """Fires when the dataset's most recent date is more than 90 days old."""
    columns = ctx.get("columns") or []
    for col in columns:
        ai_profile = col.get("ai_profile") or {}
        if isinstance(ai_profile, str):
            try:
                ai_profile = json.loads(ai_profile)
            except Exception:
                ai_profile = {}
        role = (ai_profile.get("column_role") or "").lower()
        if "time" not in role and "date" not in role:
            continue
        stats = col.get("technical_stats") or {}
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except Exception:
                stats = {}
        max_val = stats.get("max") or stats.get("max_value")
        if not max_val:
            continue
        try:
            latest = datetime.fromisoformat(str(max_val).replace("Z", "+00:00"))
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - latest).days
            if days_old > 90:
                return {
                    "type":            "stale_data",
                    "severity":        "critical" if days_old > 180 else "warning",
                    "affected_entity": col.get("column_name") or col.get("name"),
                    "confidence":      0.95,
                    "evidence": {
                        "latest_date": str(max_val),
                        "days_old":    days_old,
                        "column":      col.get("column_name") or col.get("name"),
                    },
                }
        except Exception:
            continue
    return None


def detect_anomaly_cluster(ctx: dict) -> Optional[dict]:
    """Fires when the latest forecast detected many anomalies (>5% of rows)."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    input_rows = fc.get("input_rows") or 0
    if input_rows <= 0:
        return None
    wape = fc.get("best_wape")
    if wape and float(wape) > 0.5:
        return {
            "type":            "high_forecast_error",
            "severity":        "warning",
            "affected_entity": fc.get("target_column", "metric"),
            "confidence":      0.75,
            "evidence": {
                "wape_pct":    round(float(wape) * 100, 1),
                "input_rows":  input_rows,
                "note":        "Very high error rate may indicate data anomalies or structural breaks",
            },
        }
    return None


def detect_moderate_forecast_error(ctx: dict) -> Optional[dict]:
    """Fires when WAPE is between 20-50% — moderate forecast error worth flagging."""
    fc = ctx.get("forecast")
    if not fc:
        return None
    wape = fc.get("best_wape")
    if not wape:
        return None
    try:
        wape_f = float(wape)
    except Exception:
        return None
    if 0.20 <= wape_f <= 0.50:
        mae = fc.get("best_mae")
        return {
            "type":            "high_forecast_error",
            "severity":        "warning",
            "affected_entity": fc.get("target_column", "metric"),
            "confidence":      0.80,
            "evidence": {
                "wape_pct":   round(wape_f * 100, 1),
                "best_mae":   round(float(mae), 4) if mae else None,
                "best_model": fc.get("best_model"),
                "note":       "Moderate forecast error — consider adding more features or collecting more data",
            },
        }
    return None


# ── Step-8 report detector ───────────────────────────────────────────────────

def detect_step8_insights(ctx: dict) -> Optional[dict]:
    """
    Fires when an AI-generated Step-8 report exists.
    Extracts section summaries so Gemini can derive domain-specific recommendations
    even when no forecast or segmentation has been run yet.
    """
    step8 = ctx.get("step8")
    if not step8:
        return None
    sections = step8.get("sections") or []
    if not sections:
        return None

    insights = []
    for section in sections[:5]:
        title   = section.get("title") or ""
        content = str(section.get("content") or "")[:400].strip()
        if content:
            insights.append({"section": title, "summary": content})

    if not insights:
        return None

    return {
        "type":            "report_insights",
        "severity":        "info",
        "affected_entity": ctx.get("filename", "dataset"),
        "confidence":      0.85,
        "evidence": {
            "category": ctx.get("category"),
            "filename":  ctx.get("filename"),
            "row_count": ctx.get("row_count"),
            "insights":  insights,
        },
    }


# ── Missing-analysis detectors ───────────────────────────────────────────────

def detect_no_forecast(ctx: dict) -> Optional[dict]:
    """Fires when no forecast has ever been run on this dataset."""
    if ctx.get("forecast"):
        return None
    return {
        "type":            "missing_forecast",
        "severity":        "info",
        "affected_entity": ctx.get("filename", "dataset"),
        "confidence":      1.0,
        "evidence": {
            "filename": ctx.get("filename"),
            "category": ctx.get("category"),
            "row_count": ctx.get("row_count"),
            "note":     "No forecast has been run yet — forecasting can reveal trends and future risks",
        },
    }


def detect_no_segmentation(ctx: dict) -> Optional[dict]:
    """Fires when no segmentation has ever been run on this dataset."""
    if ctx.get("segmentation"):
        return None
    return {
        "type":            "missing_segmentation",
        "severity":        "info",
        "affected_entity": ctx.get("filename", "dataset"),
        "confidence":      1.0,
        "evidence": {
            "filename": ctx.get("filename"),
            "category": ctx.get("category"),
            "row_count": ctx.get("row_count"),
            "note":     "No segmentation has been run yet — segmentation can identify high-value vs at-risk groups",
        },
    }


# ── Registry ─────────────────────────────────────────────────────────────────

DETECTORS = [
    detect_step8_insights,
    detect_forecast_decline,
    detect_forecast_growth,
    detect_low_confidence_forecast,
    detect_severe_overfit,
    detect_shrinking_top_segment,
    detect_growing_at_risk_segment,
    detect_concentration_risk,
    detect_high_null_columns,
    detect_stale_data,
    detect_anomaly_cluster,
    detect_moderate_forecast_error,
    detect_no_forecast,
    detect_no_segmentation,
]


def run_all_detectors(ctx: dict) -> list:
    """Run every detector and return all signals that fired."""
    signals = []
    for detector in DETECTORS:
        try:
            signal = detector(ctx)
            if signal is not None:
                signals.append(signal)
        except Exception as exc:
            logger.warning("Detector %s raised: %s", detector.__name__, exc)
    return signals
