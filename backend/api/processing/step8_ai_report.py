"""
Step 8 – AI-Powered Department Report Generation via Gemini.

Consumes the rich metadata produced by Steps 4-7 and asks Gemini
to write a professional, department-specific business report with
an Executive Summary, Key Insights, and Recommendations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, UTC

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client = None

GEMINI_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)


def _ensure_gemini():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)


# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

CATEGORY_CONFIDENCE_THRESHOLD = 0.75
VALID_CATEGORIES = {"sales", "hr", "operations", "marketing"}


# ══════════════════════════════════════════════════════════════
# LIGHTWEIGHT CATEGORY DETECTION (for existing datasets)
# ══════════════════════════════════════════════════════════════

def detect_category_only(dataset: dict, columns_metadata: list[dict]) -> dict:
    """
    Fast Gemini call that only detects the dataset category + confidence.
    Does NOT regenerate the report. Used to backfill category_detection
    for datasets processed before the full detection was added to Step 8.

    Returns a category_detection dict compatible with global_context.category_detection.
    """
    _ensure_gemini()

    user_category = str(dataset.get("category_hint") or "Business").strip().lower()

    col_names = []
    for col in columns_metadata[:30]:
        ai_profile = _parse_json_maybe(col.get("ai_profile")) or {}
        name = col.get("clean_name") or col.get("original_name") or ""
        role = ai_profile.get("column_role") or ai_profile.get("role") or ""
        meaning = ai_profile.get("semantic_meaning") or ""
        col_names.append(f"{name} ({role})" + (f": {meaning[:60]}" if meaning else ""))

    prompt = (
        "You are a business data analyst.\n"
        "Identify the business domain of this dataset from its column names and roles.\n\n"
        f"Columns:\n" + "\n".join(f"- {c}" for c in col_names) + "\n\n"
        'Return ONLY valid JSON: {"detected_category": "Sales"|"HR"|"Operations"|"Marketing"|"Business", '
        '"category_confidence": 0.0-1.0}'
    )

    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=128,
        response_mime_type="application/json",
    )

    raw = ""
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name, contents=prompt, config=config,
            )
            raw = response.text or ""
            break
        except Exception as exc:
            logger.warning("detect_category_only: model %s failed: %s", model_name, exc)

    detected = None
    confidence = 0.0
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        parsed = json.loads(cleaned)
        raw_det = str(parsed.get("detected_category") or "").strip().lower()
        if raw_det in VALID_CATEGORIES:
            detected = raw_det
        confidence = float(parsed.get("category_confidence") or 0.0)
        confidence = max(0.0, min(1.0, confidence))
    except Exception:
        pass

    overridden = False
    mismatch_warning = False

    if detected and detected != user_category:
        if confidence >= CATEGORY_CONFIDENCE_THRESHOLD:
            resolved_category = detected.title()
            overridden = True
        else:
            resolved_category = user_category.title()
            mismatch_warning = True
    else:
        resolved_category = user_category.title()

    logger.info(
        "detect_category_only: detected=%s confidence=%.2f user=%s resolved=%s overridden=%s",
        detected, confidence, user_category, resolved_category, overridden,
    )

    return {
        "resolved_category":  resolved_category,
        "detected_category":  detected or user_category,
        "user_category":      user_category,
        "confidence":         round(confidence, 3),
        "overridden":         overridden,
        "mismatch_warning":   mismatch_warning,
        "threshold":          CATEGORY_CONFIDENCE_THRESHOLD,
    }


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def run_step8(
    dataset: dict,
    columns_metadata: list[dict],
    step6_context: dict | None = None,
    step7_context: dict | None = None,
) -> dict:
    """
    Generate a department-specific narrative business report.

    Gemini objectively detects the true data domain from column profiles
    (ignoring the user's category_hint) and returns a confidence score.
    If confidence >= CATEGORY_CONFIDENCE_THRESHOLD and the detected category
    differs from what the user selected, the system auto-overrides the category.
    Below the threshold, a mismatch_warning flag is stored so the frontend
    can prompt the user to confirm or change their selection.

    Returns dict with keys:
      status, generated_at, department, report_html, sections,
      detected_category, category_confidence, category_overridden,
      category_mismatch_warning, category_detection
    """
    _ensure_gemini()

    user_category = str(dataset.get("category_hint") or "Business").strip().lower()
    prompt = _build_prompt(dataset, columns_metadata, step6_context, step7_context)

    raw = _call_gemini(prompt)
    try:
        parsed = _parse_report_response(raw)
    except ValueError:
        logger.warning("Step 8: first parse failed, retrying Gemini in strict JSON mode")
        try:
            raw_retry = _call_gemini(prompt, strict_json=True)
            parsed = _parse_report_response(raw_retry)
        except ValueError as e:
            logger.error("Step 8: AI report generation failed after retry: %s", e)
            parsed = _build_fallback_report(user_category.title())

    # ── Category detection ──────────────────────────────────────
    raw_detected  = str(parsed.get("detected_category") or "").strip().lower()
    detected      = raw_detected if raw_detected in VALID_CATEGORIES else None
    confidence    = 0.0
    try:
        confidence = float(parsed.get("category_confidence") or 0.0)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    overridden        = False
    mismatch_warning  = False

    if detected and detected != user_category:
        if confidence >= CATEGORY_CONFIDENCE_THRESHOLD:
            resolved_category = detected.title()
            overridden = True
            logger.info(
                "Step 8: category auto-overridden from '%s' → '%s' (confidence=%.2f)",
                user_category, detected, confidence,
            )
        else:
            resolved_category = user_category.title()
            mismatch_warning = True
            logger.info(
                "Step 8: category mismatch detected '%s' vs user '%s' (confidence=%.2f < %.2f) — warning stored",
                detected, user_category, confidence, CATEGORY_CONFIDENCE_THRESHOLD,
            )
    else:
        resolved_category = user_category.title()

    category_detection = {
        "resolved_category":       resolved_category,
        "detected_category":       detected or user_category,
        "user_category":           user_category,
        "confidence":              round(confidence, 3),
        "overridden":              overridden,
        "mismatch_warning":        mismatch_warning,
        "threshold":               CATEGORY_CONFIDENCE_THRESHOLD,
    }

    report_html = _sections_to_html(parsed.get("sections", []))

    return {
        "status":                   "completed",
        "generated_at":             datetime.now(UTC).isoformat(),
        "department":               resolved_category,
        "report_html":              report_html,
        "sections":                 parsed.get("sections", []),
        "detected_category":        detected or user_category,
        "category_confidence":      round(confidence, 3),
        "category_overridden":      overridden,
        "category_mismatch_warning": mismatch_warning,
        "category_detection":       category_detection,
    }


def _build_fallback_report(department: str) -> dict:
    return {
        "sections": [
            {
                "title": "Executive Summary",
                "content": (
                    f"The {department} dataset has been successfully processed through "
                    "automated data profiling, cleaning, and semantic analysis. "
                    "AI report narrative generation was unavailable during this run. "
                    "Please review the auto-generated dashboard charts for visual insights."
                ),
            },
            {
                "title": "Key Insights",
                "content": (
                    "1. Data profiling and cleaning completed successfully.\n"
                    "2. Column types and semantic roles have been identified by AI.\n"
                    "3. Dashboard charts have been generated based on the data structure."
                ),
            },
            {
                "title": "Recommendations",
                "content": (
                    "1. Review the generated dashboard for visual patterns and trends.\n"
                    "2. Use the AI Insights page for time-series forecasting on key metrics.\n"
                    "3. Re-upload the dataset to attempt report generation again."
                ),
            },
        ]
    }


# ══════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════

def _build_prompt(
    dataset: dict,
    columns_metadata: list[dict],
    step6_context: dict | None,
    step7_context: dict | None,
) -> str:
    file_name = dataset.get("file_name", "unknown")

    file_info = _parse_json_maybe(dataset.get("file_info"))
    row_count = file_info.get("row_count", "unknown") if isinstance(file_info, dict) else "unknown"
    col_count = file_info.get("column_count", len(columns_metadata)) if isinstance(file_info, dict) else len(columns_metadata)

    columns_summary = []
    for col in columns_metadata:
        ai_profile = _parse_json_maybe(col.get("ai_profile"))
        stats = _parse_json_maybe(col.get("technical_stats"))

        entry = {
            "name": col.get("clean_name") or col.get("original_name", ""),
            "type": col.get("data_type", "unknown"),
            "is_primary_metric": bool(col.get("is_primary_metric", False)),
        }
        if isinstance(ai_profile, dict):
            entry["role"] = ai_profile.get("role", "unknown")
            entry["description"] = ai_profile.get("description", "")
            entry["semantic_meaning"] = ai_profile.get("semantic_meaning", "")
        if isinstance(stats, dict):
            for key in ("min", "max", "mean", "null_ratio"):
                if key in stats:
                    entry[key] = stats[key]

        columns_summary.append(entry)

    step6_summary = ""
    if isinstance(step6_context, dict):
        rows_before = step6_context.get("rows_before", "?")
        rows_after = step6_context.get("rows_after", "?")
        step6_summary = (
            f"\nData Quality (Step 6): {rows_before} rows before cleaning, "
            f"{rows_after} rows after smart preprocessing.\n"
        )
        report = step6_context.get("report")
        if isinstance(report, dict):
            step6_summary += f"Transforms applied: {json.dumps(report, default=str)}\n"

    step7_summary = ""
    if isinstance(step7_context, dict):
        charts = step7_context.get("suggested_charts", [])
        if charts:
            chart_descs = []
            for c in charts:
                chart_descs.append(
                    f"- {c.get('chart_type', '?')}: {c.get('title', '?')} "
                    f"({c.get('reason', '')})"
                )
            step7_summary = (
                f"\nDashboard Blueprint (Step 7) suggested these charts:\n"
                + "\n".join(chart_descs) + "\n"
            )

    return (
        "You are an expert Business Data Analyst.\n"
        "You have been given a processed dataset to analyse.\n\n"
        f"Dataset: \"{file_name}\"\n"
        f"Rows: {row_count} | Columns: {col_count}\n"
        f"{step6_summary}"
        f"\nColumn Profiles:\n{json.dumps(columns_summary, indent=2, default=str)}\n"
        f"{step7_summary}\n"
        "TASK A — Identify the true business domain of this dataset.\n"
        'Choose exactly one from: "Sales", "HR", "Operations", "Marketing", "Business".\n'
        "Base your decision ONLY on the column names, roles, and data distributions above "
        "— ignore any filename hints.\n"
        "Assign a confidence score (0.0–1.0) reflecting how certain you are.\n\n"
        "TASK B — Write a professional business report with EXACTLY three sections, "
        "written in the voice of a senior analyst for the domain you identified.\n\n"
        "Return a single JSON object with these keys:\n"
        '- "detected_category": one of "Sales"|"HR"|"Operations"|"Marketing"|"Business"\n'
        '- "category_confidence": float 0.0–1.0\n'
        '- "sections": array of exactly 3 objects, each with "title" and "content"\n\n'
        "The three sections must be:\n"
        '1. "Executive Summary" — 2-3 paragraphs summarizing the dataset scope, '
        "data quality, and overall picture.\n"
        '2. "Key Insights" — 3-5 numbered findings from column profiles, '
        "distributions, and notable patterns. Reference specific column names and stats.\n"
        '3. "Recommendations" — 3-5 actionable recommendations based on the data.\n\n'
        "Write in professional English. Be specific — reference actual column names and statistics.\n"
        "Return ONLY valid JSON. No markdown fences, no commentary outside the JSON."
    )


# ══════════════════════════════════════════════════════════════
# GEMINI CALL
# ══════════════════════════════════════════════════════════════

def _call_gemini(prompt: str, strict_json: bool = False) -> str:
    global _client
    if _client is None:
        _ensure_gemini()

    config_kwargs = {
        "temperature": 0.3 if not strict_json else 0.0,
        "max_output_tokens": 8192,
    }
    if strict_json:
        config_kwargs["response_mime_type"] = "application/json"

    last_error: Exception | None = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            logger.info("Step 8: Gemini call succeeded with model %s", model_name)
            return response.text or ""
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Step 8: model %s failed (%s: %s), trying next fallback",
                model_name,
                type(exc).__name__,
                exc,
            )

    raise ValueError(f"All Gemini models failed. Last error: {last_error}")


# ══════════════════════════════════════════════════════════════
# RESPONSE PARSING
# ══════════════════════════════════════════════════════════════

def _parse_report_response(raw: str) -> dict:
    cleaned = _clean_response_text(raw)
    parsed = _parse_json_object_resilient(cleaned, raw)

    if not isinstance(parsed, dict):
        raise ValueError("Step 8 AI response is not a JSON object")

    sections = parsed.get("sections")
    if not isinstance(sections, list) or len(sections) == 0:
        raise ValueError("Step 8 AI response missing 'sections' array")

    valid_sections = []
    for s in sections:
        if isinstance(s, dict) and s.get("title") and s.get("content"):
            valid_sections.append({
                "title": str(s["title"]).strip(),
                "content": str(s["content"]).strip(),
            })

    if not valid_sections:
        raise ValueError("No valid sections found in AI response")

    result: dict = {"sections": valid_sections}

    # Pass through category detection fields if present
    if "detected_category" in parsed:
        result["detected_category"] = parsed["detected_category"]
    if "category_confidence" in parsed:
        result["category_confidence"] = parsed["category_confidence"]

    return result


def _sections_to_html(sections: list[dict]) -> str:
    parts = []
    for section in sections:
        title = section.get("title", "")
        content = section.get("content", "")
        paragraphs = content.split("\n")
        body = "".join(f"<p>{_escape_html(p)}</p>" for p in paragraphs if p.strip())
        parts.append(f"<h2>{_escape_html(title)}</h2>{body}")
    return "\n".join(parts)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _clean_response_text(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_json_object_resilient(cleaned: str, raw: str) -> dict:
    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)

    extracted = _extract_json_object_block(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)

    repaired = _repair_json_text(cleaned)
    if repaired and repaired != cleaned:
        candidates.append(repaired)

    if extracted:
        repaired_ext = _repair_json_text(extracted)
        if repaired_ext and repaired_ext not in candidates:
            candidates.append(repaired_ext)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            last_error = ValueError("Response is not a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e

    if repair_json is not None:
        try:
            repaired_obj = repair_json(raw or "", return_objects=True)
            if isinstance(repaired_obj, dict):
                return repaired_obj
        except Exception:
            pass

    raise ValueError(f"Step 8 AI returned invalid JSON: {last_error}")


def _extract_json_object_block(text: str) -> str:
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start: end + 1].strip()


def _repair_json_text(text: str) -> str:
    if not text:
        return text
    repaired = text
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"')
    repaired = repaired.replace("\u2018", "'").replace("\u2019", "'")
    repaired = re.sub(r"/\*.*?\*/", "", repaired, flags=re.DOTALL)
    repaired = re.sub(r"(^|\s)//.*?$", r"\1", repaired, flags=re.MULTILINE)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    return repaired.strip()


def _parse_json_maybe(value) -> dict | list | str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    if isinstance(value, (dict, list)):
        return value
    return {}
