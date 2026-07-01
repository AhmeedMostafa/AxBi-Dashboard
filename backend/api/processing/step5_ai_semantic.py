"""
Step 5 – AI Semantic Column Analysis via Gemini.

Takes the technical profiles produced by Step 4 and asks Gemini
to add human-readable descriptions, semantic types, and flag
primary metrics for dashboard KPIs.
"""

import json
import logging
import os
import re

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Gemini setup (lazy — configured on first call) ───────────
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
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file."
            )
        _client = genai.Client(api_key=api_key)


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def run_step5(
    columns_metadata: list[dict],
    category_hint: str | None = None,
) -> list[dict]:
    """
    Run AI semantic analysis on all columns.

    Args:
        columns_metadata: rows from the columns_metadata table
                          (must include id, column fields from Step 4).
        category_hint:    optional user-provided dataset category.

    Returns:
        List of dicts each with:
            column_id          – UUID to update
            ai_profile         – dict with description, semantic_type, etc.
            is_primary_metric  – bool
    """
    _ensure_gemini()

    BATCH_SIZE = 30
    all_results = []

    for i in range(0, len(columns_metadata), BATCH_SIZE):
        batch = columns_metadata[i : i + BATCH_SIZE]
        prompt = _build_prompt(batch, category_hint)
        raw_response = _call_gemini(prompt)
        try:
            parsed = _parse_response(raw_response, batch)
        except ValueError:
            logger.warning(
                "Step 5: first parse failed for batch %s-%s, retrying Gemini once in strict mode",
                i,
                i + len(batch) - 1,
            )
            raw_response_retry = _call_gemini(prompt, strict_json=True)
            parsed = _parse_response(raw_response_retry, batch)
        all_results.extend(parsed)

    logger.info(
        f"Step 5: AI enriched {len(all_results)}/{len(columns_metadata)} columns"
    )
    return all_results


# ══════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════

def _build_prompt(
    columns_metadata: list[dict],
    category_hint: str | None,
) -> str:
    """Build the Gemini prompt from column technical profiles."""

    columns_summary = []
    for col in columns_metadata:
        stats = col.get("technical_stats", {})
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except json.JSONDecodeError:
                stats = {}

        columns_summary.append(
            {
                "column_name": col.get("clean_name") or col.get("original_name", ""),
                "original_name": col.get("original_name", ""),
                "data_type": col.get("data_type", "unknown"),
                "stats": stats,
            }
        )

    category_line = ""
    if category_hint:
        category_line = (
            f'\nThe user described this dataset as related to: "{category_hint}".\n'
        )

    prompt = (
        "You are a senior data analyst AI. Analyze the following dataset columns "
        "and provide semantic meaning for each one.\n"
        f"{category_line}\n"
        "Here are the columns with their technical profiles from pandas:\n\n"
        f"{json.dumps(columns_summary, indent=2, default=str)}\n\n"
        "For EACH column return a JSON object inside a JSON array. "
        "Each object must have exactly these keys:\n\n"
        '1. "column_name" – the exact column_name string from the input\n'
        '2. "ai_profile" – an object with:\n'
        '   - "description": 1-2 sentence explanation of what this column represents\n'
        '   - "semantic_meaning": A clean, human-friendly name you predict for this column \n '
        '   - "role": Pick exactly ONE match the column role: ["id", "measure", "dimension", "date", "geographic", "descriptive", "boolean", "unknown"]\n'
        '   - "tags": list of short relevant tags (e.g. ["revenue", "financial"])\n'
        '   - "suggested_aggregation": best default aggregation for dashboards — '
        '"sum", "avg", "count", "min", "max", or "none"\n'
        '   - "column_confidence": a float between 0.0 and 1.0 representing how '
        "certain you are about this semantic prediction\n"
        '3. "is_primary_metric" – boolean, true ONLY for the 1-3 most important '
        "numeric KPI columns a dashboard should highlight\n\n"
        "Return ONLY a valid JSON array. No markdown fences, no commentary outside the JSON."
    )

    return prompt


# ══════════════════════════════════════════════════════════════
# GEMINI CALL
# ══════════════════════════════════════════════════════════════

def _call_gemini(prompt: str, strict_json: bool = False) -> str:
    """Send prompt to Gemini with automatic model fallback.

    Tries each model in GEMINI_MODEL_CHAIN in order. If a model
    fails (rate-limit, deprecation, outage, etc.) the next one is
    attempted. Only raises if every model in the chain fails.
    """
    global _client
    if _client is None:
        _ensure_gemini()

    config_kwargs = {
        "temperature": 0.2 if not strict_json else 0.0,
        "max_output_tokens": 4096,
    }
    if strict_json:
        config_kwargs["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**config_kwargs)

    last_error: Exception | None = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            logger.info("Step 5: Gemini call succeeded with model %s", model_name)
            return response.text
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Step 5: model %s failed (%s: %s), trying next fallback",
                model_name,
                type(exc).__name__,
                exc,
            )

    raise ValueError(
        f"All Gemini models failed. Last error: {last_error}"
    )



# ══════════════════════════════════════════════════════════════
# RESPONSE PARSING
# ══════════════════════════════════════════════════════════════

def _parse_response(
    raw_response: str,
    columns_metadata: list[dict],
) -> list[dict]:
    """
    Parse Gemini JSON response and map results back to column IDs.

    Returns:
        List of dicts: { column_id, ai_profile, is_primary_metric }
    """
    cleaned = _clean_response_text(raw_response)
    ai_results = _parse_json_array_resilient(cleaned, raw_response)

    if not isinstance(ai_results, list):
        raise ValueError("Gemini response is not a JSON array")

    # Build lookup: column_name → metadata row (which has the 'id')
    name_to_meta = {}
    for col in columns_metadata:
        name = col.get("clean_name") or col.get("original_name", "")
        name_to_meta[name] = col

    results = []
    for ai_col in ai_results:
        col_name = ai_col.get("column_name", "")

        if col_name not in name_to_meta:
            logger.warning(f"Gemini returned unknown column '{col_name}', skipping")
            continue

        meta = name_to_meta[col_name]
        results.append(
            {
                "column_id": meta["id"],
                "ai_profile": ai_col.get("ai_profile", {}),
                "is_primary_metric": bool(ai_col.get("is_primary_metric", False)),
            }
        )

    logger.info(
        f"Parsed {len(results)}/{len(columns_metadata)} columns from Gemini response"
    )
    return results


def _clean_response_text(raw_response: str) -> str:
    cleaned = (raw_response or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_json_array_resilient(cleaned: str, raw_response: str) -> list:
    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)

    extracted = _extract_json_array_block(cleaned)
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
            if isinstance(parsed, list):
                return parsed
            last_error = ValueError("Gemini response is not a JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e

    if repair_json is not None:
        try:
            repaired = repair_json(raw_response, return_objects=True)
            if isinstance(repaired, list):
                return repaired
            if isinstance(repaired, dict):
                for v in repaired.values():
                    if isinstance(v, list):
                        return v
        except Exception as e:
            logger.debug("json_repair also failed: %s", e)

    logger.error(
        "Gemini returned invalid JSON after repair attempts: %s\nRaw response (first 500 chars): %s",
        last_error,
        raw_response[:500],
    )
    raise ValueError(f"Gemini returned invalid JSON: {last_error}")


def _extract_json_array_block(text: str) -> str:
    if not text:
        return ""
    start = text.find("[")
    end = text.rfind("]")
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
