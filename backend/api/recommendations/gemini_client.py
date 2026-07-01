from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

GEMINI_MODEL_CHAIN = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
)

_client = None


def _ensure_client():
    global _client
    if _client is not None:
        return
    if genai is None:
        raise ValueError("google-genai is not installed.")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")
    _client = genai.Client(api_key=api_key)


def call_gemini(prompt: str) -> list:
    """
    Send a recommendations prompt to Gemini with model fallback.
    Returns the parsed recommendations list, or [] on failure.
    """
    _ensure_client()

    config_kwargs = {
        "temperature": 0.4,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }

    last_error = None
    for model_name in GEMINI_MODEL_CHAIN:
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
            logger.info("Recommendations: Gemini call succeeded with model %s", model_name)
            raw = response.text or ""
            return _parse_recommendations(raw)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Recommendations: model %s failed (%s: %s), trying next",
                model_name, type(exc).__name__, exc,
            )

    logger.error("Recommendations: all Gemini models failed. Last error: %s", last_error)
    return []


def _parse_recommendations(raw: str) -> list:
    """Extract the recommendations list from Gemini's JSON response."""
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return parsed.get("recommendations", [])
    except Exception as exc:
        logger.error("Recommendations: failed to parse Gemini JSON: %s — raw: %.300s", exc, raw)
    return []
