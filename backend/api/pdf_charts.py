"""
Server-side chart rendering for PDF reports.

Uses matplotlib (Agg backend) to render each chart spec from Step 7
into a base64-encoded PNG that can be embedded directly in HTML via
<img src="data:image/png;base64,..."> tags.  WeasyPrint handles these
natively, so no temp files are needed.
"""

from __future__ import annotations

import base64
import io
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

logger = logging.getLogger(__name__)

BRAND_COLORS = ["#5A5AF6", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4"]
BRAND_PRIMARY = "#5A5AF6"

# Light print-friendly theme (matches AxBi app branding)
BG_PAGE = "#ffffff"
BG_CARD = "#f8fafc"
TEXT_PRIMARY = "#0f172a"
TEXT_MUTED = "#64748b"
GRID_COLOR = "#e2e8f0"
BORDER_COLOR = "#e2e8f0"

CHART_WIDTH = 5.4
CHART_HEIGHT = 2.15
CHART_DPI = 120

MAX_BAR_CATEGORIES = 15
MAX_PIE_SLICES = 10


def render_chart(chart_spec: dict, rows: list[dict]) -> str | None:
    """
    Render a single chart spec to a base64-encoded PNG string.

    Returns None if the chart cannot be rendered (insufficient data,
    unknown type, etc.).  Errors are logged but never raised.
    """
    chart_type = str(chart_spec.get("chart_type", "")).lower()
    renderer = _RENDERERS.get(chart_type)
    if renderer is None:
        logger.warning("pdf_charts: unsupported chart_type '%s'", chart_type)
        return None

    try:
        return renderer(chart_spec, rows)
    except Exception:
        logger.exception("pdf_charts: failed to render %s chart '%s'",
                         chart_type, chart_spec.get("title", "?"))
        return None


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _fig_to_base64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _new_figure(width: float = CHART_WIDTH, height: float = CHART_HEIGHT) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(width, height))
    fig.set_facecolor(BG_PAGE)
    ax.set_facecolor(BG_PAGE)
    return fig, ax


def _style_axes(ax: plt.Axes, xlabel: str = "", ylabel: str = ""):
    ax.tick_params(colors=TEXT_MUTED, labelsize=7)
    ax.xaxis.label.set_color(TEXT_MUTED)
    ax.yaxis.label.set_color(TEXT_MUTED)
    ax.title.set_color(TEXT_PRIMARY)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.6, alpha=0.9)
    ax.grid(axis="x", visible=False)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9, color=TEXT_MUTED)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9, color=TEXT_MUTED)


def _parse_numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        try:
            v = float(cleaned)
            return v if np.isfinite(v) else None
        except ValueError:
            return None
    return None


def _normalize_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_date_label(value: Any) -> str | None:
    text = _normalize_label(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return text


def _is_date_like(column_key: str, rows: list[dict]) -> bool:
    sample_count = 0
    for row in rows[:20]:
        val = row.get(column_key)
        if val is None:
            continue
        text = str(val).strip()
        if not text:
            continue
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            sample_count += 1
        except (ValueError, TypeError):
            pass
    return sample_count >= 3


def _build_grouped(rows: list[dict], x_key: str | None, y_key: str | None,
                   count_when_no_y: bool = False) -> list[dict]:
    if not x_key:
        return []

    date_axis = _is_date_like(x_key, rows)
    grouped: dict[str, dict] = {}

    for row in rows:
        raw_x = row.get(x_key)
        label = _normalize_date_label(raw_x) if date_axis else _normalize_label(raw_x)
        if not label:
            continue

        num_y = _parse_numeric(row.get(y_key)) if y_key else None
        if num_y is None and not count_when_no_y:
            continue
        contribution = num_y if num_y is not None else 1.0

        if label in grouped:
            grouped[label]["value"] += contribution
        else:
            sort_time = None
            if date_axis:
                try:
                    sort_time = datetime.fromisoformat(label).timestamp()
                except (ValueError, TypeError):
                    pass
            grouped[label] = {"label": label, "value": contribution, "sort_time": sort_time}

    items = list(grouped.values())
    if date_axis:
        items.sort(key=lambda d: d.get("sort_time") or 0)
    else:
        items.sort(key=lambda d: d["value"], reverse=True)
    return items


def _compact_number(value: float) -> str:
    abs_val = abs(value)
    if abs_val >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_val >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_val >= 1_000:
        return f"{value / 1_000:.1f}K"
    if abs_val == int(abs_val):
        return f"{int(value)}"
    return f"{value:.1f}"


def _prettify(name: str) -> str:
    tokens = name.replace("-", "_").split("_")
    return " ".join(t.capitalize() for t in tokens if t)


# ══════════════════════════════════════════════════════════════
# Chart Renderers
# ══════════════════════════════════════════════════════════════

def _render_bar_points(spec: dict, labels: list[str], values: list[float]) -> str | None:
    if not labels or not values:
        return None

    colors = [BRAND_COLORS[i % len(BRAND_COLORS)] for i in range(len(labels))]

    fig, ax = _new_figure()
    bars = ax.bar(range(len(labels)), values, color=colors, width=0.6,
                  edgecolor="none", zorder=3)

    for bar_rect, val in zip(bars, values):
        ax.text(bar_rect.get_x() + bar_rect.get_width() / 2, bar_rect.get_height(),
                _compact_number(val), ha="center", va="bottom",
                fontsize=7, color=TEXT_MUTED, fontweight="bold")

    ax.set_xticks(range(len(labels)))
    truncated = [l[:14] + "..." if len(l) > 14 else l for l in labels]
    ax.set_xticklabels(truncated, rotation=35, ha="right", fontsize=7, color=TEXT_MUTED)

    y_label = _prettify(spec.get("y_axis") or "Value")
    ax.set_title(spec.get("title", ""), fontsize=9, fontweight="bold",
                 color=TEXT_PRIMARY, pad=8)
    _style_axes(ax, ylabel=y_label)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))

    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_bar(spec: dict, rows: list[dict]) -> str | None:
    data = _build_grouped(rows, spec.get("x_axis"), spec.get("y_axis"))
    if not data:
        return None
    data = data[:MAX_BAR_CATEGORIES]
    return _render_bar_points(
        spec,
        [d["label"] for d in data],
        [d["value"] for d in data],
    )


def _render_line_points(spec: dict, labels: list[str], values: list[float]) -> str | None:
    if len(labels) < 2 or len(values) < 2:
        return None

    fig, ax = _new_figure()
    x_positions = range(len(labels))

    ax.plot(x_positions, values, color=BRAND_COLORS[0], linewidth=2.5, zorder=3)
    ax.fill_between(x_positions, values, alpha=0.15, color=BRAND_COLORS[0], zorder=2)

    step = max(1, len(labels) // 10)
    tick_positions = list(range(0, len(labels), step))
    ax.set_xticks(tick_positions)
    tick_labels = [labels[i][-10:] if len(labels[i]) > 10 else labels[i] for i in tick_positions]
    ax.set_xticklabels(tick_labels, rotation=30, ha="right", fontsize=7, color=TEXT_MUTED)

    y_label = _prettify(spec.get("y_axis") or "Value")
    ax.set_title(spec.get("title", ""), fontsize=9, fontweight="bold",
                 color=TEXT_PRIMARY, pad=8)
    _style_axes(ax, ylabel=y_label)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))

    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_line(spec: dict, rows: list[dict]) -> str | None:
    data = _build_grouped(rows, spec.get("x_axis"), spec.get("y_axis"))
    if len(data) < 2:
        return None
    return _render_line_points(
        spec,
        [d["label"] for d in data],
        [d["value"] for d in data],
    )


def _render_histogram(spec: dict, rows: list[dict]) -> str | None:
    x_key = spec.get("x_axis")
    if not x_key:
        return None

    values = []
    for row in rows:
        v = _parse_numeric(row.get(x_key))
        if v is not None:
            values.append(v)
    if len(values) < 2:
        return None

    unique = len(set(values))
    bin_count = min(20, max(5, unique))
    counts, edges = np.histogram(values, bins=bin_count)
    if not counts.any():
        return None

    labels = [f"{edges[i]:.2g}–{edges[i + 1]:.2g}" for i in range(len(counts))]

    fig, ax = _new_figure()
    ax.bar(range(len(counts)), counts, color=BRAND_COLORS[0], width=0.85, edgecolor="none", zorder=3)
    ax.set_xticks(range(len(labels)))
    truncated = [l[:12] + "..." if len(l) > 12 else l for l in labels]
    ax.set_xticklabels(truncated, rotation=35, ha="right", fontsize=7, color=TEXT_MUTED)
    ax.set_title(spec.get("title", ""), fontsize=10, fontweight="bold", color=TEXT_PRIMARY, pad=10)
    _style_axes(ax, xlabel=_prettify(x_key), ylabel="Count")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))
    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_pareto(spec: dict, rows: list[dict]) -> str | None:
    data = _build_grouped(rows, spec.get("x_axis"), spec.get("y_axis"))
    if not data:
        return None
    data = sorted(data, key=lambda d: d["value"], reverse=True)[:MAX_BAR_CATEGORIES]

    labels = [d["label"] for d in data]
    values = [d["value"] for d in data]
    total = sum(values) or 1.0
    cumulative: list[float] = []
    run = 0.0
    for v in values:
        run += v
        cumulative.append(run / total * 100.0)

    fig, ax1 = _new_figure()
    x_pos = range(len(labels))
    ax1.bar(x_pos, values, color=BRAND_COLORS[0], width=0.6, edgecolor="none", zorder=3)
    ax1.set_xticks(list(x_pos))
    truncated = [l[:14] + "..." if len(l) > 14 else l for l in labels]
    ax1.set_xticklabels(truncated, rotation=35, ha="right", fontsize=7, color=TEXT_MUTED)
    y_label = _prettify(spec.get("y_axis") or "Value")
    ax1.set_title(spec.get("title", ""), fontsize=10, fontweight="bold", color=TEXT_PRIMARY, pad=10)
    _style_axes(ax1, ylabel=y_label)
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))

    ax2 = ax1.twinx()
    ax2.plot(list(x_pos), cumulative, color=BRAND_COLORS[2], linewidth=2, marker="o",
             markersize=4, zorder=4)
    ax2.set_ylabel("Cumulative %", fontsize=9, color=TEXT_MUTED)
    ax2.tick_params(colors=TEXT_MUTED, labelsize=8)
    ax2.set_ylim(0, 105)
    ax2.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    for spine in ax2.spines.values():
        spine.set_visible(False)

    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_pie_points(spec: dict, labels: list[str], values: list[float]) -> str | None:
    if not labels or not values:
        return None

    colors = [BRAND_COLORS[i % len(BRAND_COLORS)] for i in range(len(labels))]

    fig, ax = _new_figure(height=3.4)
    fig.set_facecolor(BG_PAGE)

    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%", startangle=90,
        colors=colors, pctdistance=0.78,
        wedgeprops=dict(width=0.45, edgecolor=BG_PAGE, linewidth=1.5),
    )
    for t in autotexts:
        t.set_fontsize(7)
        t.set_color(TEXT_PRIMARY)
        t.set_fontweight("bold")

    ax.set_title(spec.get("title", ""), fontsize=9, fontweight="bold",
                 color=TEXT_PRIMARY, pad=8)

    truncated_labels = [l[:18] + "..." if len(l) > 18 else l for l in labels]
    ax.legend(wedges, truncated_labels, loc="center left", bbox_to_anchor=(1.02, 0.5),
              fontsize=8, frameon=False, labelcolor=TEXT_MUTED)

    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_pie(spec: dict, rows: list[dict]) -> str | None:
    data = _build_grouped(rows, spec.get("x_axis"), spec.get("y_axis"),
                          count_when_no_y=True)
    if not data:
        return None

    data = [d for d in data if d["value"] > 0]
    if not data:
        return None

    if len(data) > MAX_PIE_SLICES:
        head = data[:MAX_PIE_SLICES - 1]
        others_val = sum(d["value"] for d in data[MAX_PIE_SLICES - 1:])
        head.append({"label": "Others", "value": others_val})
        data = head

    return _render_pie_points(
        spec,
        [d["label"] for d in data],
        [d["value"] for d in data],
    )


def _render_scatter(spec: dict, rows: list[dict]) -> str | None:
    x_key = spec.get("x_axis")
    y_key = spec.get("y_axis")
    if not x_key or not y_key:
        return None

    xs, ys = [], []
    for row in rows:
        x = _parse_numeric(row.get(x_key))
        y = _parse_numeric(row.get(y_key))
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)

    if len(xs) < 3:
        return None

    fig, ax = _new_figure()
    ax.scatter(xs, ys, c=BRAND_COLORS[5], s=20, alpha=0.65, edgecolors="none", zorder=3)

    if len(xs) >= 5:
        try:
            z = np.polyfit(xs, ys, 1)
            p = np.poly1d(z)
            x_sorted = sorted(xs)
            ax.plot(x_sorted, p(x_sorted), color=BRAND_COLORS[0],
                    linewidth=1.5, linestyle="--", alpha=0.7, zorder=4)
        except Exception:
            pass

    x_label = _prettify(x_key)
    y_label = _prettify(y_key)
    ax.set_title(spec.get("title", ""), fontsize=9, fontweight="bold",
                 color=TEXT_PRIMARY, pad=8)
    _style_axes(ax, xlabel=x_label, ylabel=y_label)
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.5, alpha=0.7)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: _compact_number(x)))

    fig.tight_layout()
    return _fig_to_base64(fig)


def _render_kpi_value(spec: dict, total: float) -> str | None:
    target_key = spec.get("y_axis") or (spec.get("columns") or [None])[0] or "Value"

    fig = plt.figure(figsize=(3.5, 2.0))
    fig.set_facecolor(BG_CARD)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_CARD)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.62, _compact_number(total), ha="center", va="center",
            fontsize=28, fontweight="bold", color=BRAND_PRIMARY)
    ax.text(0.5, 0.30, _prettify(target_key), ha="center", va="center",
            fontsize=10, color=TEXT_MUTED)
    ax.text(0.5, 0.14, spec.get("title", ""), ha="center", va="center",
            fontsize=7, color=BRAND_PRIMARY, fontstyle="italic")

    return _fig_to_base64(fig)


def _render_kpi(spec: dict, rows: list[dict]) -> str | None:
    target_key = spec.get("y_axis") or (spec.get("columns") or [None])[0]
    if not target_key:
        return None

    total = 0.0
    count = 0
    for row in rows:
        val = _parse_numeric(row.get(target_key))
        if val is not None:
            total += val
            count += 1

    if count == 0:
        return None

    return _render_kpi_value(spec, total)


def _agg_points(data: list[dict], *, label_keys: tuple[str, ...] = ("label", "name")) -> tuple[list[str], list[float]]:
    labels: list[str] = []
    values: list[float] = []
    for pt in data:
        label = None
        for key in label_keys:
            if pt.get(key) is not None:
                label = str(pt[key])
                break
        val = _parse_numeric(pt.get("value"))
        if label is None or val is None:
            continue
        labels.append(label)
        values.append(val)
    return labels, values


def render_chart_from_agg(chart_spec: dict, agg: dict) -> str | None:
    """
    Render a chart from pre-aggregated data (same shape as aggregate_charts_from_df).
    Uses the full dataset totals — matches the Report page charts.
    """
    raw_type = str(chart_spec.get("chart_type", "")).lower().replace("-", "_")
    data = agg.get("data") or []

    try:
        if raw_type in ("kpi_card", "radial"):
            val = _parse_numeric(data[0].get("value")) if data else None
            if val is None:
                return None
            return _render_kpi_value(chart_spec, val)

        if raw_type in ("pie", "donut", "treemap"):
            labels, values = _agg_points(data, label_keys=("name", "label"))
            positive = [(l, v) for l, v in zip(labels, values) if v > 0]
            if not positive:
                return None
            labels, values = zip(*positive)
            labels, values = list(labels), list(values)
            if len(labels) > MAX_PIE_SLICES:
                head_l = labels[: MAX_PIE_SLICES - 1]
                head_v = values[: MAX_PIE_SLICES - 1]
                others = sum(values[MAX_PIE_SLICES - 1 :])
                labels = head_l + ["Others"]
                values = head_v + [others]
            return _render_pie_points(chart_spec, labels, values)

        if raw_type in ("line", "area"):
            labels, values = _agg_points(data)
            return _render_line_points(chart_spec, labels[:MAX_BAR_CATEGORIES], values[:MAX_BAR_CATEGORIES])

        if raw_type in ("bar", "horizontal_bar", "stacked_bar", "combo", "funnel"):
            labels, values = _agg_points(data)
            return _render_bar_points(chart_spec, labels[:MAX_BAR_CATEGORIES], values[:MAX_BAR_CATEGORIES])

        if raw_type == "pareto":
            labels, values = _agg_points(data)
            if not labels:
                return None
            paired = sorted(zip(labels, values), key=lambda p: p[1], reverse=True)[:MAX_BAR_CATEGORIES]
            labels, values = zip(*paired)
            fake_rows = [{chart_spec.get("x_axis") or "label": l, chart_spec.get("y_axis") or "value": v}
                         for l, v in zip(labels, values)]
            return _render_pareto(chart_spec, fake_rows)

        # Unknown types fall back to row-based rendering if possible
        renderer = _RENDERERS.get(raw_type)
        if renderer and raw_type not in ("kpi_card", "radial"):
            fake_rows = []
            for pt in data:
                x_key = chart_spec.get("x_axis") or "label"
                y_key = chart_spec.get("y_axis") or "value"
                lbl = pt.get("label") or pt.get("name")
                val = pt.get("value")
                if lbl is not None:
                    fake_rows.append({x_key: lbl, y_key: val})
            if fake_rows:
                return renderer(chart_spec, fake_rows)
    except Exception:
        logger.exception(
            "pdf_charts: failed to render aggregated %s chart '%s'",
            raw_type,
            chart_spec.get("title", "?"),
        )
    return None


# ══════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════

_RENDERERS = {
    "bar": _render_bar,
    "horizontal_bar": _render_bar,
    "stacked_bar": _render_bar,
    "combo": _render_bar,
    "line": _render_line,
    "area": _render_line,
    "pie": _render_pie,
    "donut": _render_pie,
    "treemap": _render_pie,
    "funnel": _render_bar,
    "histogram": _render_histogram,
    "pareto": _render_pareto,
    "scatter": _render_scatter,
    "kpi_card": _render_kpi,
    "radial": _render_kpi,
}


# ══════════════════════════════════════════════════════════════
# Report-only renderers (forecast + correlation)
# ══════════════════════════════════════════════════════════════

def render_forecast_chart(forecast_data: dict) -> str | None:
    """Render a saved forecast (projection line + 95% confidence band) to base64 PNG.

    `forecast_data` = {forecast:[{date,value}], prediction_intervals:[{date,lower,upper}],
    historical?:[{date,value}]}. Returns None if there are no forecast points.
    """
    try:
        fd = forecast_data or {}
        forecast = fd.get("forecast") or []
        if not forecast:
            return None
        intervals = fd.get("prediction_intervals") or []
        historical = fd.get("historical") or []

        f_dates = [str(p.get("date", "")) for p in forecast]
        f_vals = [_parse_numeric(p.get("value")) for p in forecast]

        fig, ax = _new_figure(width=CHART_WIDTH, height=2.4)

        # Confidence band (aligned to forecast points by index)
        if intervals:
            lo = [_parse_numeric(iv.get("lower")) for iv in intervals]
            hi = [_parse_numeric(iv.get("upper")) for iv in intervals]
            n = min(len(f_dates), len(lo), len(hi))
            if n and all(v is not None for v in lo[:n] + hi[:n]):
                ax.fill_between(range(n), lo[:n], hi[:n], color=BRAND_PRIMARY,
                                alpha=0.12, linewidth=0, label="95% Confidence")

        # Historical (if present) then forecast, on a shared index
        x_labels = f_dates
        if historical:
            h_vals = [_parse_numeric(p.get("value")) for p in historical]
            h_dates = [str(p.get("date", "")) for p in historical]
            hx = list(range(len(h_vals)))
            fx = list(range(len(h_vals), len(h_vals) + len(f_vals)))
            ax.plot(hx, h_vals, color="#22d3ee", linewidth=1.6, label="Historical")
            ax.plot(fx, f_vals, color=BRAND_PRIMARY, linewidth=1.6,
                    linestyle="--", label="Forecast")
            x_labels = h_dates + f_dates
            tick_idx = hx + fx
        else:
            fx = list(range(len(f_vals)))
            ax.plot(fx, f_vals, color=BRAND_PRIMARY, linewidth=1.6,
                    linestyle="--", label="Forecast")
            tick_idx = fx

        # Thin the x tick labels so they stay legible
        step = max(1, len(tick_idx) // 8)
        ax.set_xticks(tick_idx[::step])
        ax.set_xticklabels([x_labels[i][5:] if len(x_labels[i]) > 5 else x_labels[i]
                            for i in range(0, len(x_labels), step)], rotation=0)
        _style_axes(ax)
        ax.legend(fontsize=6, loc="best", frameon=False)
        return _fig_to_base64(fig)
    except Exception:
        logger.exception("pdf_charts: failed to render forecast chart")
        return None


def render_correlation_heatmap(df, max_cols: int = 12) -> str | None:
    """Render a numeric-correlation heatmap (RdBu_r) to base64 PNG.

    Returns None if fewer than 2 numeric columns. Caps to the `max_cols` highest-
    variance columns so the matrix stays readable.
    """
    try:
        num = df.select_dtypes(include="number")
        # Drop constant / all-null columns (no correlation signal)
        num = num.loc[:, num.nunique(dropna=True) > 1]
        if num.shape[1] < 2:
            return None
        if num.shape[1] > max_cols:
            top = num.var(numeric_only=True).sort_values(ascending=False).head(max_cols).index
            num = num[list(top)]

        corr = num.corr(numeric_only=True)
        labels = [str(c) for c in corr.columns]
        n = len(labels)

        fig, ax = _new_figure(width=CHART_WIDTH, height=CHART_WIDTH * 0.82)
        im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")

        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        short = [(lbl[:14] + "…") if len(lbl) > 15 else lbl for lbl in labels]
        ax.set_xticklabels(short, rotation=45, ha="right", fontsize=6, color=TEXT_MUTED)
        ax.set_yticklabels(short, fontsize=6, color=TEXT_MUTED)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(False)

        # Annotate cells only when the matrix is small enough to stay readable
        if n <= 8:
            for i in range(n):
                for j in range(n):
                    v = corr.values[i, j]
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=5.5, color="#0f172a" if abs(v) < 0.6 else "#ffffff")

        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(labelsize=6, colors=TEXT_MUTED)
        return _fig_to_base64(fig)
    except Exception:
        logger.exception("pdf_charts: failed to render correlation heatmap")
        return None
