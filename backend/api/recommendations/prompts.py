RECOMMENDATIONS_PROMPT = """\
You are a senior business analyst advising a {category} team.

Dataset: "{filename}" ({row_count} rows)

Detected signals from this dataset's analytics:
{signals_json}

Generate 3-5 prioritized, actionable recommendations based on the signals above.
Speak in {category} domain language (e.g. "customer" for Sales, "employee" for HR,
"product" for Operations, "campaign" for Marketing).

Signal type guide:
- report_insights   → Use the "insights" array in evidence as your primary content source. Extract specific numbers, trends, and findings from those section summaries to build concrete recommendations.
- missing_forecast  → Recommend running a forecast; explain the business value of knowing future trends for this category.
- missing_segmentation → Recommend running segmentation; explain the value of knowing which groups drive the most value.
- forecast_decline  → Revenue/metric protection and recovery actions.
- forecast_growth   → Capitalise on growth; scaling and investment recommendations.
- growing_at_risk_segment → Retention and win-back campaign actions.
- shrinking_top_segment   → VIP retention and loyalty programme actions.
- high_forecast_error     → Data collection improvement and model retraining actions.
- high_null_columns       → Data quality remediation actions.
- stale_data              → Data freshness and pipeline refresh actions.

For each recommendation return:
- title:        a short, imperative action phrase (max 8 words)
- rationale:    1-2 sentences citing specific numbers or findings from the signal evidence
- priority:     "high" | "medium" | "low"  (critical signals → high, warning → medium, info → low)
- actions:      list of 3-4 concrete next steps the team can take this week
- metrics:      dict of the most important numbers from the evidence (key: numeric value pairs only)
- triggered_by: list of signal type strings that prompted this recommendation

Rules:
- Order by priority (high first)
- Reference actual column names, segment names, and percentages from the signal evidence
- If signals list is empty, return an empty recommendations array — do NOT invent signals
- metrics values MUST be actual numbers or percentages taken directly from the signal evidence (e.g. 40.0, 12.5). NEVER use strings like "Track weekly", "Monitor monthly", "N/A", or any qualitative text as a metric value — put those phrases in the actions list instead
- If metrics cannot be extracted as real numbers from the evidence, return an empty metrics dict {{}}
- Return ONLY valid JSON matching the schema below. No markdown, no commentary.

Schema:
{{
  "recommendations": [
    {{
      "title": "string",
      "rationale": "string",
      "priority": "high|medium|low",
      "actions": ["string", ...],
      "metrics": {{"key": numeric_value, ...}},
      "triggered_by": ["signal_type", ...]
    }}
  ]
}}
"""


def build_prompt(ctx: dict, signals: list) -> str:
    import json

    category  = ctx.get("category") or "business"
    filename  = ctx.get("filename") or "dataset"
    row_count = ctx.get("row_count") or "unknown"

    step8 = ctx.get("step8") or {}
    step8_summary = ""
    if step8:
        sections = step8.get("sections") or []
        if sections:
            step8_summary = " | ".join(
                f"{s.get('title','')}: {str(s.get('content',''))[:200]}"
                for s in sections[:3]
            )
        elif step8.get("report_html"):
            step8_summary = "(report available but not summarised)"

    return RECOMMENDATIONS_PROMPT.format(
        category=category.title(),
        filename=filename,
        row_count=row_count,
        signals_json=json.dumps(signals, indent=2, default=str),
        step8_summary=step8_summary or "Not available",
    )
