"""
Light, compact, print-friendly AxBi PDF report HTML builder.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

# Narrative / insight text caps keep page count down for printing.
_NARRATIVE_PARA_MAX = 520
_INSIGHT_BODY_MAX = 380


def _escape(text: str) -> str:
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _parse_json_maybe(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _strip_markdown_bold(text: str) -> str:
    return re.sub(r'\*\*(.+?)\*\*', r'\1', str(text))


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r'\s+', ' ', str(text).strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(' ', 1)[0] + '…'


def _format_segment_insight(insight) -> str:
    if isinstance(insight, dict):
        title = str(insight.get('title') or '').strip()
        content = _truncate(str(insight.get('content') or '').strip(), _INSIGHT_BODY_MAX)
        if title and content:
            return f'{title}: {content}'
        return title or content
    return _truncate(str(insight), _INSIGHT_BODY_MAX)


def _segment_key_metric(seg: dict) -> str:
    avg = seg.get('avg_metrics') or {}
    if isinstance(avg, str):
        avg = _parse_json_maybe(avg) or {}
    for key in ('monetary', 'total_value', 'revenue', 'value', 'value_share', 'score'):
        raw = avg.get(key) if isinstance(avg, dict) else None
        if raw is None:
            raw = seg.get(key)
        if raw is None:
            continue
        try:
            return f'{float(raw):,.0f}'
        except (TypeError, ValueError):
            return str(raw)
    return ''


def _load_brand_logo_b64(max_height: int = 72) -> str:
    """Load AxBi logo as base64 PNG, downscaled for PDF embedding."""
    import io

    logo_path = Path(__file__).resolve().parent / 'assets' / 'axbi-logo.png'
    if not logo_path.is_file():
        return ''
    try:
        from PIL import Image

        img = Image.open(logo_path).convert('RGBA')
        w, h = img.size
        if h > max_height:
            new_w = max(1, int(w * max_height / h))
            img = img.resize((new_w, max_height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        raw = logo_path.read_bytes()
        if len(raw) > 500_000:
            return ''
        return base64.b64encode(raw).decode('ascii')


def build_pdf_html(
    *,
    title: str,
    department: str,
    formatted_date: str,
    file_name: str,
    row_count: str,
    column_count: str,
    kpi_cards: list,
    non_kpi_chart_items: list,
    sections: list,
    columns_meta: list,
    segmentation: dict | None = None,
    forecast_summary: dict | None = None,
    forecast_b64: str | None = None,
    correlation_b64: str | None = None,
) -> str:
    from .pdf_charts import render_chart_from_agg

    C_PRIMARY = '#5A5AF6'
    C_SLATE = '#0f172a'
    C_BODY = '#334155'
    C_MUTED = '#64748b'
    C_BORDER = '#e2e8f0'
    C_STRIPE = '#f8fafc'
    C_WHITE = '#ffffff'
    C_CARD = '#f8fafc'

    logo_b64 = _load_brand_logo_b64()
    logo_img = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="AxBi" '
        f'style="height:28pt;width:auto;display:block;" />'
        if logo_b64 else
        f'<span style="font-size:16pt;font-weight:bold;color:{C_SLATE};">'
        f'Ax<span style="color:{C_PRIMARY};">Bi</span></span>'
    )

    css = f"""
    @page {{
        size: A4;
        margin: 0.75cm 1.1cm 1.2cm 1.1cm;
        @bottom-right {{
            content: "AxBi · p." counter(page);
            font-size: 7pt;
            color: #94a3b8;
            font-family: Helvetica, Arial, sans-serif;
        }}
    }}
    body {{
        font-family: Helvetica, Arial, sans-serif;
        font-size: 8.5pt;
        color: {C_BODY};
        background: {C_WHITE};
        margin: 0;
        padding: 0;
        line-height: 1.42;
    }}
    p {{ margin: 0 0 3pt 0; }}
    table {{ border-collapse: collapse; }}
    """

    sec = [0]

    def next_sec() -> int:
        sec[0] += 1
        return sec[0]

    def _band(num: int, label: str) -> str:
        n = str(num).zfill(2)
        return (
            f'<table style="width:100%;margin:6pt 0 3pt 0;">'
            f'<tr><td style="border-left:3pt solid {C_PRIMARY};padding:0 0 0 8pt;">'
            f'<span style="font-size:7pt;font-weight:bold;color:{C_PRIMARY};">{n}&nbsp;</span>'
            f'<span style="font-size:9.5pt;font-weight:bold;color:{C_SLATE};">'
            f'{_escape(label)}</span></td></tr></table>'
        )

    def _chart_card(title: str, b64: str) -> str:
        return (
            f'<td style="width:50%;vertical-align:top;padding:2pt;">'
            f'<div style="border:1pt solid {C_BORDER};padding:4pt 5pt;">'
            f'<p style="font-size:7pt;font-weight:bold;color:{C_SLATE};margin:0 0 2pt 0;">'
            f'{_escape(title)}</p>'
            f'<img src="data:image/png;base64,{b64}" style="width:100%;max-height:118pt;" '
            f'alt="{_escape(title)}"/>'
            f'</div></td>'
        )

    def _charts_grid(items: list[dict]) -> str:
        if not items:
            return ''
        rows = ''
        for i in range(0, len(items), 2):
            chunk = items[i:i + 2]
            cells = ''.join(_chart_card(c.get('title', ''), c.get('base64', '')) for c in chunk)
            if len(chunk) == 1:
                cells += '<td style="width:50%;padding:2pt;"></td>'
            rows += f'<tr>{cells}</tr>'
        return f'<table style="width:100%;margin-bottom:4pt;">{rows}</table>'

    # ── Compact letterhead (no dedicated cover page) ──
    letterhead = (
        f'<table style="width:100%;border-bottom:1.5pt solid {C_PRIMARY};'
        f'margin-bottom:6pt;padding-bottom:6pt;">'
        f'<tr>'
        f'<td style="width:32%;vertical-align:middle;">{logo_img}</td>'
        f'<td style="width:68%;vertical-align:middle;text-align:right;">'
        f'<p style="font-size:13pt;font-weight:bold;color:{C_SLATE};margin:0 0 2pt 0;">'
        f'{_escape(title)}</p>'
        f'<p style="font-size:7.5pt;color:{C_MUTED};margin:0;">'
        f'<span style="background:{C_PRIMARY};color:{C_WHITE};padding:1pt 6pt;'
        f'font-weight:bold;margin-right:6pt;">{_escape(department)}</span>'
        f'{_escape(file_name)} &nbsp;·&nbsp; {_escape(formatted_date)}'
        f'</p></td></tr></table>'
    )

    # ── Overview + KPI side-by-side ──
    ov_pairs = [
        ('Rows', row_count),
        ('Columns', column_count),
        ('Dept', department),
    ]
    ov_cells = ''
    for lbl, val in ov_pairs:
        ov_cells += (
            f'<td style="padding:3pt 6pt;border:1pt solid {C_BORDER};background:{C_STRIPE};'
            f'font-size:7pt;color:{C_MUTED};width:14%;">{_escape(lbl)}</td>'
            f'<td style="padding:3pt 6pt;border:1pt solid {C_BORDER};font-size:7.5pt;'
            f'color:{C_SLATE};width:19%;">{_escape(val)}</td>'
        )

    kpi_block = ''
    if kpi_cards:
        k = kpi_cards[0]
        kpi_block = (
            f'<div style="border:1pt solid {C_BORDER};border-top:2pt solid {C_PRIMARY};'
            f'background:{C_CARD};padding:6pt 8pt;text-align:center;">'
            f'<p style="font-size:6.5pt;text-transform:uppercase;color:{C_MUTED};margin:0;">'
            f'{_escape(k.get("metric", ""))}</p>'
            f'<p style="font-size:15pt;font-weight:bold;color:{C_PRIMARY};margin:2pt 0;">'
            f'{_escape(k.get("value", ""))}</p>'
            f'<p style="font-size:6.5pt;color:{C_MUTED};margin:0;">'
            f'{_escape(k.get("title", ""))}</p></div>'
        )
        for extra in kpi_cards[1:4]:
            kpi_block += (
                f'<div style="border:1pt solid {C_BORDER};background:{C_CARD};'
                f'padding:4pt 6pt;margin-top:3pt;text-align:center;">'
                f'<span style="font-size:6.5pt;color:{C_MUTED};">{_escape(extra.get("metric", ""))}: </span>'
                f'<span style="font-size:10pt;font-weight:bold;color:{C_PRIMARY};">'
                f'{_escape(extra.get("value", ""))}</span></div>'
            )

    summary_row = (
        f'<div style="margin-bottom:5pt;">'
        f'{_band(next_sec(), "SUMMARY")}'
        f'<table style="width:100%;"><tr>'
        f'<td style="width:62%;vertical-align:top;padding-right:4pt;">'
        f'<table style="width:100%;"><tr>{ov_cells}</tr></table>'
        f'</td>'
        f'<td style="width:38%;vertical-align:top;">{kpi_block}</td>'
        f'</tr></table></div>'
    )

    chart_html = ''
    if non_kpi_chart_items:
        chart_html = (
            f'<div style="margin-bottom:5pt;">'
            f'{_band(next_sec(), "VISUALIZATIONS")}'
            f'{_charts_grid(non_kpi_chart_items)}'
            f'</div>'
        )

    narrative_html = f'<div style="margin-bottom:5pt;">{_band(next_sec(), "AI ANALYSIS")}'
    for section in sections:
        s_title = _escape(section.get('title', ''))
        content = section.get('content', '')
        paras = ''.join(
            f'<p style="font-size:8pt;color:{C_BODY};margin:0 0 2pt 0;">'
            f'{_escape(_truncate(_strip_markdown_bold(p), _NARRATIVE_PARA_MAX))}</p>'
            for p in content.split('\n') if p.strip()
        )
        narrative_html += (
            f'<div style="margin-bottom:4pt;padding:5pt 7pt;border:1pt solid {C_BORDER};'
            f'border-left:2pt solid {C_PRIMARY};background:{C_STRIPE};">'
            f'<p style="font-size:8.5pt;font-weight:bold;color:{C_PRIMARY};'
            f'margin:0 0 3pt 0;">{s_title}</p>{paras}</div>'
        )
    narrative_html += '</div>'

    seg_html = ''
    if segmentation and isinstance(segmentation, dict):
        method = str(segmentation.get('method', 'analysis')).upper()
        seg_list = segmentation.get('segments') or []
        insights = segmentation.get('insights') or []
        seg_charts = segmentation.get('charts') or []

        seg_chart_items = []
        for chart in seg_charts[:4]:
            b64 = render_chart_from_agg(
                {
                    'title': chart.get('title', ''),
                    'chart_type': chart.get('chart_type', 'bar'),
                    'x_axis': None,
                    'y_axis': None,
                },
                {'data': chart.get('data') or []},
            )
            if b64:
                seg_chart_items.append({'title': chart.get('title', ''), 'base64': b64})

        seg_html = f'<div style="margin-bottom:5pt;">{_band(next_sec(), f"SEGMENTATION — {method}")}'
        if seg_chart_items:
            seg_html += _charts_grid(seg_chart_items)

        if insights:
            seg_html += '<div style="margin-top:3pt;">'
            for ins in insights[:4]:
                if isinstance(ins, dict) and ins.get('title'):
                    body = _truncate(str(ins.get('content', '')), _INSIGHT_BODY_MAX)
                    seg_html += (
                        f'<p style="font-size:7.5pt;color:{C_BODY};margin:0 0 3pt 0;">'
                        f'<strong style="color:{C_SLATE};">{_escape(str(ins.get("title", "")))}: </strong>'
                        f'{_escape(body)}</p>'
                    )
                else:
                    seg_html += (
                        f'<p style="font-size:7.5pt;color:{C_BODY};margin:0 0 3pt 0;">'
                        f'{_escape(_format_segment_insight(ins))}</p>'
                    )
            seg_html += '</div>'

        if seg_list:
            seg_rows = ''
            for i, seg in enumerate(seg_list[:20]):
                row_bg = C_STRIPE if i % 2 == 0 else C_WHITE
                seg_name = _escape(str(seg.get('segment') or seg.get('name') or f'Segment {i + 1}'))
                seg_count = _escape(str(seg.get('count', seg.get('size', ''))))
                raw_pct = seg.get('percentage') or seg.get('size_pct') or seg.get('pct') or ''
                try:
                    seg_pct = f'{float(raw_pct):.0f}%'
                except (TypeError, ValueError):
                    seg_pct = str(raw_pct)
                metric_val = _escape(_segment_key_metric(seg))
                seg_rows += (
                    f'<tr>'
                    f'<td style="padding:3pt 5pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                    f'font-size:7.5pt;font-weight:bold;">{seg_name}</td>'
                    f'<td style="padding:3pt 5pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                    f'font-size:7.5pt;text-align:right;">{seg_count}</td>'
                    f'<td style="padding:3pt 5pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                    f'font-size:7.5pt;text-align:right;">{seg_pct}</td>'
                    f'<td style="padding:3pt 5pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                    f'font-size:7.5pt;text-align:right;">{metric_val}</td>'
                    f'</tr>'
                )
            th = f'background:{C_PRIMARY};color:{C_WHITE};padding:3pt 5pt;font-size:7pt;font-weight:bold;'
            seg_html += (
                f'<table style="width:100%;border:1pt solid {C_BORDER};margin-top:4pt;">'
                f'<thead><tr>'
                f'<th style="{th}text-align:left;">Segment</th>'
                f'<th style="{th}text-align:right;">N</th>'
                f'<th style="{th}text-align:right;">%</th>'
                f'<th style="{th}text-align:right;">Avg $</th>'
                f'</tr></thead><tbody>{seg_rows}</tbody></table>'
            )
        seg_html += '</div>'

    col_table_html = ''
    if columns_meta:
        col_rows = ''
        for i, col in enumerate(columns_meta[:30]):
            row_bg = C_STRIPE if i % 2 == 0 else C_WHITE
            col_name = _escape(col.get('clean_name') or col.get('original_name', ''))
            col_type = _escape(col.get('data_type', 'unknown'))
            stats = _parse_json_maybe(col.get('technical_stats'))
            ai = _parse_json_maybe(col.get('ai_profile'))
            role = _escape(str((ai or {}).get('role', '') or (ai or {}).get('column_role', '')))
            null_ratio = col_min = col_max = ''
            if isinstance(stats, dict):
                nr = stats.get('null_ratio')
                if nr is not None:
                    try:
                        null_ratio = f'{float(nr) * 100:.0f}%'
                    except (ValueError, TypeError):
                        null_ratio = str(nr)
                col_min = str(stats['min']) if stats.get('min') is not None else ''
                col_max = str(stats['max']) if stats.get('max') is not None else ''
            col_rows += (
                f'<tr>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;font-weight:bold;">{col_name}</td>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;">{col_type}</td>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;color:{C_PRIMARY};">{role}</td>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;text-align:right;">{null_ratio}</td>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;text-align:right;">{col_min}</td>'
                f'<td style="padding:2pt 4pt;background:{row_bg};border-bottom:1pt solid {C_BORDER};'
                f'font-size:7pt;text-align:right;">{col_max}</td>'
                f'</tr>'
            )
        th = f'background:{C_SLATE};color:{C_WHITE};padding:3pt 4pt;font-size:6.5pt;font-weight:bold;'
        col_table_html = (
            f'<div style="margin-top:4pt;">{_band(next_sec(), "COLUMNS")}'
            f'<table style="width:100%;border:1pt solid {C_BORDER};">'
            f'<thead><tr>'
            f'<th style="{th}text-align:left;">Name</th>'
            f'<th style="{th}text-align:left;">Type</th>'
            f'<th style="{th}text-align:left;">Role</th>'
            f'<th style="{th}text-align:right;">Null</th>'
            f'<th style="{th}text-align:right;">Min</th>'
            f'<th style="{th}text-align:right;">Max</th>'
            f'</tr></thead><tbody>{col_rows}</tbody></table></div>'
        )

    # ── Forecast band (latest saved forecast: summary + projection chart) ──
    forecast_html = ''
    if forecast_b64:
        fs = forecast_summary or {}
        summary_bits = []
        if fs.get('target'):
            summary_bits.append(f'<strong style="color:{C_SLATE};">Target:</strong> {_escape(str(fs["target"]))}')
        if fs.get('best_model'):
            summary_bits.append(f'<strong style="color:{C_SLATE};">Model:</strong> {_escape(str(fs["best_model"]))}')
        if fs.get('accuracy') is not None:
            try:
                summary_bits.append(f'<strong style="color:{C_SLATE};">Accuracy:</strong> {float(fs["accuracy"]):.1f}%')
            except (TypeError, ValueError):
                pass
        if fs.get('horizon'):
            summary_bits.append(f'<strong style="color:{C_SLATE};">Horizon:</strong> {_escape(str(fs["horizon"]))}')
        summary_line = (
            f'<p style="font-size:7.5pt;color:{C_BODY};margin:0 0 3pt 0;">'
            f'{" &nbsp;·&nbsp; ".join(summary_bits)}</p>'
            if summary_bits else ''
        )
        forecast_html = (
            f'<div style="margin-bottom:5pt;">{_band(next_sec(), "FORECAST")}'
            f'{summary_line}'
            f'<div style="border:1pt solid {C_BORDER};padding:4pt 5pt;">'
            f'<img src="data:image/png;base64,{forecast_b64}" style="width:100%;max-height:210pt;" alt="Forecast"/>'
            f'</div></div>'
        )

    # ── Column relationships band (numeric correlation heatmap) ──
    correlation_html = ''
    if correlation_b64:
        correlation_html = (
            f'<div style="margin-bottom:5pt;">{_band(next_sec(), "COLUMN RELATIONSHIPS")}'
            f'<p style="font-size:7.5pt;color:{C_MUTED};margin:0 0 3pt 0;">'
            f'Correlation strength between numeric fields — red = positive, blue = negative.</p>'
            f'<div style="border:1pt solid {C_BORDER};padding:4pt 5pt;">'
            f'<img src="data:image/png;base64,{correlation_b64}" style="width:100%;max-height:300pt;" alt="Correlation heatmap"/>'
            f'</div></div>'
        )

    footer = (
        f'<p style="font-size:6.5pt;color:{C_MUTED};text-align:center;margin-top:6pt;'
        f'padding-top:4pt;border-top:1pt solid {C_BORDER};">'
        f'AxBi · {_escape(formatted_date)} · Automated report — validate before acting.</p>'
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<style>{css}</style>
</head>
<body>
{letterhead}
{summary_row}
{chart_html}
{narrative_html}
{seg_html}
{forecast_html}
{col_table_html}
{correlation_html}
{footer}
</body>
</html>"""
