"""
Step 6: Smart preprocessing based on Step 5 AI semantics.

This module is pure processing logic: it does not read/write Supabase.
It takes a DataFrame plus columns metadata and returns:
  1) transformed DataFrame
  2) an audit report describing applied/skipped actions
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd


RISKY_ROLES = {"measure", "date", "boolean"}
ROLE_ALIASES = {
    "time": "date",
    "geo": "geographic",
    "category": "dimension",
}

ROLE_TYPE_COMPATIBILITY = {
    "measure": {"numeric", "text"},
    "date": {"datetime", "text"},
    "boolean": {"boolean", "text"},
    "id": {"text", "numeric"},
    "dimension": {"text", "numeric", "datetime", "boolean"},
    "descriptive": {"text", "numeric", "datetime", "boolean"},
    "geographic": {"text", "numeric"},
}
PROMOTABLE_TO_MEASURE_ROLES = {"dimension", "descriptive", "unknown"}
NUMERIC_PROMOTION_MIN_NON_NULL = 50
NUMERIC_PROMOTION_MAX_UNIQUE_RATIO = 0.995
IDENTIFIER_HINTS = {
    "id",
    "identifier",
    "uuid",
    "guid",
    "phone",
    "mobile",
    "tel",
    "fax",
    "zip",
    "postal",
    "ssn",
    "passport",
}
CONFORMANCE_SAMPLE_LIMIT = 5


def run_step6(
    df: pd.DataFrame,
    columns_metadata: list[dict],
    *,
    min_confidence: float = 0.75,
    min_conversion_success: float = 0.90,
    numeric_promotion_success: float = 0.90,
    max_fill_null_ratio: float = 0.30,
    text_numeric_mismatch_max_ratio: float = 0.15,
) -> tuple[pd.DataFrame, dict]:
    """
    Apply deterministic smart preprocessing guided by Step 5 AI labels.

    Args:
        df: DataFrame from Step 3 cleaned output.
        columns_metadata: rows from columns_metadata (must include ai_profile).
        min_confidence: minimum AI confidence for risky transforms.
        min_conversion_success: minimum successful parsing ratio for risky casts.
        numeric_promotion_success: threshold to auto-promote mislabeled columns
                                  from text-like roles to measure when values are
                                  strongly numeric.
        max_fill_null_ratio: max null ratio allowed for imputation.
        text_numeric_mismatch_max_ratio: for text final types, replace numeric-like
                                  outliers with "Unknown" only when mismatch ratio is
                                  below this threshold.

    Returns:
        (df_smart, report)
    """
    out = df.copy()
    actions: list[dict[str, Any]] = []

    for meta in columns_metadata:
        column_name = _resolve_dataframe_column(meta, out.columns)
        ai_profile = _parse_ai_profile(meta.get("ai_profile"))
        ai_role = _normalize_role(ai_profile.get("role"))
        confidence = _safe_float(ai_profile.get("column_confidence"), default=0.0)
        original_type = str(meta.get("data_type") or "unknown").strip().lower()

        effective_role, promoted, promotion_reason, promotion_success = _maybe_promote_to_measure(
            role=ai_role,
            series=out[column_name] if column_name else None,
            column_name=column_name or "",
            ai_profile=ai_profile,
            min_success=numeric_promotion_success,
        )
        action = _base_action(
            column_name=column_name,
            ai_role=ai_role,
            role=effective_role,
            confidence=confidence,
            original_type=original_type,
            promoted=promoted,
            promotion_reason=promotion_reason,
            promotion_numeric_success=promotion_success,
        )

        if not column_name:
            action["reason"] = "column_not_found_in_dataframe"
            actions.append(action)
            continue

        original = out[column_name]
        working = original
        core_applied = False

        if effective_role in RISKY_ROLES and confidence < min_confidence and not promoted:
            action["reason"] = f"low_confidence:{confidence:.3f}<required:{min_confidence:.3f}"
        elif not _role_type_compatible(effective_role, original_type):
            action["reason"] = f"incompatible_role_and_type:{effective_role}/{original_type}"
        else:
            if effective_role == "measure":
                result = _apply_measure(
                    original,
                    ai_profile=ai_profile,
                    min_conversion_success=min_conversion_success,
                    max_fill_null_ratio=max_fill_null_ratio,
                )
            elif effective_role == "date":
                result = _apply_date(
                    original,
                    min_conversion_success=min_conversion_success,
                )
            elif effective_role == "boolean":
                result = _apply_boolean(
                    original,
                    min_conversion_success=0.85,
                    max_fill_null_ratio=0.20,
                )
            elif effective_role == "id":
                result = _apply_id(original)
            elif effective_role in {"dimension", "descriptive", "geographic"}:
                result = _apply_dimension(
                    original,
                    max_fill_null_ratio=0.10,
                )
            else:
                result = {
                    "applied": False,
                    "reason": f"unsupported_role:{effective_role}",
                    "transforms": [],
                    "imputation": None,
                    "conversion_success": None,
                }

            action["transforms"] = list(result["transforms"])
            action["imputation"] = result["imputation"]
            action["conversion_success"] = result["conversion_success"]
            if result["applied"]:
                core_applied = True
                working = result["series"]
                action["final_type"] = _infer_series_type(working)
            else:
                action["reason"] = result["reason"]

        target_type = _normalize_target_type(action["final_type"], working)
        conformance = _enforce_type_conformance(
            working,
            target_type=target_type,
            role=effective_role,
            text_numeric_mismatch_max_ratio=text_numeric_mismatch_max_ratio,
        )
        if conformance["applied"]:
            working = conformance["series"]
            action["transforms"].extend(conformance["transforms"])

        action["type_conformance_applied"] = bool(conformance["applied"])
        action["type_conformance_target_type"] = target_type
        action["type_conformance_action"] = conformance["action"]
        action["type_conformance_invalid_count"] = conformance["invalid_count"]
        action["type_conformance_invalid_ratio"] = conformance["invalid_ratio"]
        action["type_conformance_samples"] = conformance["sample_invalid_values"]

        out[column_name] = working
        action["final_type"] = _infer_series_type(working)
        action["values_modified"] = _count_modified(original, working)
        action["applied"] = bool(core_applied or conformance["applied"])

        actions.append(action)

    transformed_count = sum(1 for a in actions if a["applied"])
    report = {
        "rules_version": "step6.v2",
        "min_confidence": min_confidence,
        "min_conversion_success": min_conversion_success,
        "numeric_promotion_success": numeric_promotion_success,
        "max_fill_null_ratio": max_fill_null_ratio,
        "text_numeric_mismatch_max_ratio": text_numeric_mismatch_max_ratio,
        "columns_total": len(actions),
        "columns_transformed": transformed_count,
        "columns_skipped": len(actions) - transformed_count,
        "column_actions": actions,
    }
    return out, report


def _base_action(
    column_name: str | None,
    ai_role: str,
    role: str,
    confidence: float,
    original_type: str,
    promoted: bool,
    promotion_reason: str,
    promotion_numeric_success: float | None,
) -> dict[str, Any]:
    return {
        "column_name": column_name or "",
        "ai_role": ai_role,
        "role": role,
        "confidence": round(confidence, 4),
        "detected_type": original_type,  # Backward compatibility.
        "original_type": original_type,
        "final_type": original_type,
        "role_promoted_by_data": promoted,
        "promotion_reason": promotion_reason,
        "promotion_numeric_success": promotion_numeric_success,
        "applied": False,
        "reason": "",
        "transforms": [],
        "imputation": None,
        "conversion_success": None,
        "values_modified": 0,
        "type_conformance_applied": False,
        "type_conformance_target_type": original_type,
        "type_conformance_action": "none",
        "type_conformance_invalid_count": 0,
        "type_conformance_invalid_ratio": 0.0,
        "type_conformance_samples": [],
    }


def _resolve_dataframe_column(meta: dict, available_columns: pd.Index) -> str | None:
    candidates = [meta.get("clean_name"), meta.get("original_name")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in available_columns:
            return candidate
    return None


def _parse_ai_profile(ai_profile: Any) -> dict:
    if isinstance(ai_profile, dict):
        return ai_profile
    if isinstance(ai_profile, str) and ai_profile.strip():
        try:
            parsed = json.loads(ai_profile)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_role(role: Any) -> str:
    normalized = str(role or "unknown").strip().lower()
    return ROLE_ALIASES.get(normalized, normalized)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _role_type_compatible(role: str, detected_type: str) -> bool:
    if role not in ROLE_TYPE_COMPATIBILITY:
        return False
    return detected_type in ROLE_TYPE_COMPATIBILITY[role]


def _maybe_promote_to_measure(
    *,
    role: str,
    series: pd.Series | None,
    column_name: str,
    ai_profile: dict,
    min_success: float,
) -> tuple[str, bool, str, float | None]:
    """
    Promote weak text-like AI roles to `measure` when data values are clearly numeric.
    This protects against abbreviated/ambiguous column names while keeping ID-like
    columns from being miscast as measures.
    """
    if role not in PROMOTABLE_TO_MEASURE_ROLES:
        return role, False, "", None
    if series is None:
        return role, False, "", None
    if _looks_identifier_like(column_name, ai_profile):
        return role, False, "identifier_like_column", None

    parsed_plain = pd.to_numeric(series, errors="coerce")
    success_plain = _conversion_success(series, parsed_plain)
    parsed_financial = _parse_financial_numeric(series)
    success_financial = _conversion_success(series, parsed_financial)
    best_success = max(success_plain, success_financial)

    if best_success < min_success:
        return role, False, "numeric_dominance_below_threshold", round(best_success, 4)

    non_null = int(series.notna().sum())
    if non_null >= NUMERIC_PROMOTION_MIN_NON_NULL:
        unique_ratio = float(series.nunique(dropna=True)) / max(non_null, 1)
        if unique_ratio > NUMERIC_PROMOTION_MAX_UNIQUE_RATIO:
            return role, False, "identifier_like_high_uniqueness", round(best_success, 4)

    return "measure", True, "numeric_dominance_promotion", round(best_success, 4)


def _looks_identifier_like(column_name: str, ai_profile: dict) -> bool:
    semantic_meaning = str(ai_profile.get("semantic_meaning") or "").lower()
    description = str(ai_profile.get("description") or "").lower()
    tags = ai_profile.get("tags") or []
    tags_text = " ".join(str(t).lower() for t in tags)
    haystack = " ".join([str(column_name or "").lower(), semantic_meaning, description, tags_text])
    tokens = set(token for token in re.split(r"[^a-z0-9]+", haystack) if token)
    return any(hint in tokens for hint in IDENTIFIER_HINTS)


def _apply_measure(
    series: pd.Series,
    *,
    ai_profile: dict,
    min_conversion_success: float,
    max_fill_null_ratio: float,
) -> dict[str, Any]:
    financial = _is_financial_measure(ai_profile, series.name)
    transforms = ["financial_numeric_parse"] if financial else ["numeric_parse"]

    if pd.api.types.is_numeric_dtype(series):
        # Step 3 already typed this column numeric; the string/regex financial parse
        # would reproduce the same values at ~5x the cost. Cast straight to Float64.
        parsed = series.astype("Float64")
    elif financial:
        parsed = _parse_financial_numeric(series)
        # Use float-capable dtype for measures to avoid Int64 + decimal fill failures.
        parsed = pd.to_numeric(parsed, errors="coerce").astype("Float64")
    else:
        parsed = pd.to_numeric(series, errors="coerce").astype("Float64")

    success = _conversion_success(series, parsed)
    if success < min_conversion_success:
        return {
            "applied": False,
            "reason": (
                f"conversion_success_too_low:{success:.3f}"
                f"<required:{min_conversion_success:.3f}"
            ),
            "transforms": transforms,
            "imputation": None,
            "conversion_success": round(success, 4),
        }

    result = parsed
    imputation = None
    if result.isna().mean() <= max_fill_null_ratio and result.notna().any():
        median_value = float(result.median())
        result = result.fillna(median_value)
        transforms.append("fill_median")
        imputation = f"median:{round(median_value, 6)}"

    return {
        "applied": True,
        "series": result,
        "reason": "",
        "transforms": transforms,
        "imputation": imputation,
        "conversion_success": round(success, 4),
    }


def _apply_date(
    series: pd.Series,
    *,
    min_conversion_success: float,
) -> dict[str, Any]:
    if pd.api.types.is_datetime64_any_dtype(series):
        # Already a datetime column out of Step 3 — re-parsing is a no-op.
        parsed = series
    else:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    success = _conversion_success(series, parsed)
    transforms = ["datetime_parse"]

    if success < min_conversion_success:
        return {
            "applied": False,
            "reason": (
                f"conversion_success_too_low:{success:.3f}"
                f"<required:{min_conversion_success:.3f}"
            ),
            "transforms": transforms,
            "imputation": None,
            "conversion_success": round(success, 4),
        }

    imputation = None
    if parsed.isna().mean() <= 0.10 and parsed.notna().any():
        parsed = parsed.ffill()
        transforms.append("fill_forward")
        imputation = "ffill"

    return {
        "applied": True,
        "series": parsed,
        "reason": "",
        "transforms": transforms,
        "imputation": imputation,
        "conversion_success": round(success, 4),
    }


def _apply_boolean(
    series: pd.Series,
    *,
    min_conversion_success: float,
    max_fill_null_ratio: float,
) -> dict[str, Any]:
    parsed = series.apply(_to_bool)
    success = _conversion_success(series, parsed)
    transforms = ["boolean_map"]

    if success < min_conversion_success:
        return {
            "applied": False,
            "reason": (
                f"conversion_success_too_low:{success:.3f}"
                f"<required:{min_conversion_success:.3f}"
            ),
            "transforms": transforms,
            "imputation": None,
            "conversion_success": round(success, 4),
        }

    result = parsed.astype("boolean")
    imputation = None
    if result.isna().mean() <= max_fill_null_ratio and result.notna().any():
        mode_values = result.mode(dropna=True)
        if not mode_values.empty:
            fill_value = bool(mode_values.iloc[0])
            result = result.fillna(fill_value)
            transforms.append("fill_mode")
            imputation = f"mode:{fill_value}"

    return {
        "applied": True,
        "series": result,
        "reason": "",
        "transforms": transforms,
        "imputation": imputation,
        "conversion_success": round(success, 4),
    }


def _apply_id(series: pd.Series) -> dict[str, Any]:
    result = series.astype("string").str.strip()
    result = result.where(result != "", pd.NA)
    # Common spreadsheet artifact for IDs that were cast to float.
    result = result.str.replace(r"^(-?\d+)\.0$", r"\1", regex=True)
    return {
        "applied": True,
        "series": result,
        "reason": "",
        "transforms": ["normalize_id_string"],
        "imputation": None,
        "conversion_success": None,
    }


def _apply_dimension(
    series: pd.Series,
    *,
    max_fill_null_ratio: float,
) -> dict[str, Any]:
    result = series.astype("string").str.strip()
    result = result.str.replace(r"\s+", " ", regex=True)
    result = result.where(result != "", pd.NA)
    transforms = ["normalize_text"]
    imputation = None

    if result.isna().mean() <= max_fill_null_ratio:
        result = result.fillna("Unknown")
        transforms.append("fill_unknown")
        imputation = "constant:Unknown"

    return {
        "applied": True,
        "series": result,
        "reason": "",
        "transforms": transforms,
        "imputation": imputation,
        "conversion_success": None,
    }


def _to_bool(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "active", "enabled"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "inactive", "disabled"}:
        return False
    return pd.NA


def _parse_financial_numeric(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip()
    # (123.45) -> -123.45
    values = values.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    # Remove common currency symbols and separators.
    values = values.str.replace(r"[\$,€£,_\s]", "", regex=True)
    values = values.str.replace(r"(?i)\b(usd|egp|sar|aed|eur|gbp)\b", "", regex=True)
    return pd.to_numeric(values, errors="coerce").astype("Float64")


def _is_financial_measure(ai_profile: dict, column_name: Any) -> bool:
    semantic_meaning = str(ai_profile.get("semantic_meaning") or "").lower()
    description = str(ai_profile.get("description") or "").lower()
    tags = ai_profile.get("tags") or []
    tags_text = " ".join(str(t).lower() for t in tags)
    col = str(column_name or "").lower()

    haystack = " ".join([semantic_meaning, description, tags_text, col])
    finance_hints = {
        "revenue",
        "sales",
        "price",
        "amount",
        "cost",
        "income",
        "profit",
        "spend",
        "expense",
    }
    return any(hint in haystack for hint in finance_hints)


def _normalize_target_type(target_type: str, series: pd.Series) -> str:
    normalized = str(target_type or "").strip().lower()
    if normalized in {"numeric", "datetime", "boolean", "text"}:
        return normalized
    inferred = _infer_series_type(series)
    return inferred if inferred in {"numeric", "datetime", "boolean", "text"} else "text"


def _enforce_type_conformance(
    series: pd.Series,
    *,
    target_type: str,
    role: str,
    text_numeric_mismatch_max_ratio: float,
) -> dict[str, Any]:
    original = series
    transformed = series
    transforms: list[str] = []
    invalid_mask = pd.Series(False, index=series.index)
    action = "none"

    if target_type == "numeric":
        if pd.api.types.is_numeric_dtype(series):
            transformed = series.astype("Float64")
        else:
            transformed = _parse_best_numeric(series).astype("Float64")
        invalid_mask = _non_empty_mask(series) & transformed.isna()
        action = "set_invalid_numeric_to_null" if int(invalid_mask.sum()) > 0 else "none"
        transforms = ["enforce_numeric_type"]
    elif target_type == "datetime":
        if pd.api.types.is_datetime64_any_dtype(series):
            transformed = series
        else:
            transformed = pd.to_datetime(series, errors="coerce", format="mixed")
        invalid_mask = _non_empty_mask(series) & transformed.isna()
        action = "set_invalid_datetime_to_null" if int(invalid_mask.sum()) > 0 else "none"
        transforms = ["enforce_datetime_type"]
    elif target_type == "boolean":
        transformed = series.apply(_to_bool).astype("boolean")
        invalid_mask = _non_empty_mask(series) & transformed.isna()
        action = "set_invalid_boolean_to_null" if int(invalid_mask.sum()) > 0 else "none"
        transforms = ["enforce_boolean_type"]
    else:
        transformed = series.astype("string").str.strip()
        transformed = transformed.str.replace(r"\s+", " ", regex=True)
        transformed = transformed.where(transformed != "", pd.NA)

        numeric_like = _numeric_like_mask(transformed)
        invalid_mask = numeric_like
        invalid_count = int(invalid_mask.sum())
        non_empty = int(_non_empty_mask(transformed).sum())
        invalid_ratio = (invalid_count / non_empty) if non_empty else 0.0

        if role == "id":
            action = "id_role_numeric_text_preserved"
        elif invalid_count > 0 and invalid_ratio <= text_numeric_mismatch_max_ratio:
            transformed = transformed.mask(numeric_like, "Unknown")
            action = "replace_numeric_text_outliers_with_unknown"
            transforms = ["text_numeric_outlier_to_unknown"]
        elif invalid_count > 0:
            action = f"text_numeric_mismatch_above_threshold:{invalid_ratio:.4f}"
        else:
            action = "none"

        transforms = ["enforce_text_type", *transforms]

    invalid_count = int(invalid_mask.sum())
    non_empty_total = int(_non_empty_mask(original).sum())
    invalid_ratio = (invalid_count / non_empty_total) if non_empty_total else 0.0
    changed_count = _count_modified(original, transformed)
    applied = changed_count > 0

    return {
        "applied": applied,
        "series": transformed,
        "transforms": transforms if applied else [],
        "invalid_count": invalid_count,
        "invalid_ratio": round(invalid_ratio, 4),
        "action": action,
        "sample_invalid_values": _sample_invalid_values(original, invalid_mask),
    }


def _non_empty_mask(series: pd.Series) -> pd.Series:
    # For non-text dtypes a value is "non-empty" iff it is not null — no string cast
    # needed (numbers/dates/bools never stringify to "").
    if (
        pd.api.types.is_numeric_dtype(series)
        or pd.api.types.is_datetime64_any_dtype(series)
        or pd.api.types.is_bool_dtype(series)
    ):
        return series.notna()
    text = series.astype("string").str.strip()
    return text.notna() & (text != "")


def _numeric_like_mask(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    present = text.notna() & (text != "")
    plain = pd.to_numeric(text, errors="coerce")
    financial = _parse_financial_numeric(text)
    return present & (plain.notna() | financial.notna())


def _parse_best_numeric(series: pd.Series) -> pd.Series:
    plain = pd.to_numeric(series, errors="coerce")
    financial = _parse_financial_numeric(series)
    return financial if int(financial.notna().sum()) > int(plain.notna().sum()) else plain


def _sample_invalid_values(series: pd.Series, invalid_mask: pd.Series) -> list[str]:
    if invalid_mask.empty:
        return []
    samples = series[invalid_mask].astype("string").dropna().unique().tolist()
    return [str(v) for v in samples[:CONFORMANCE_SAMPLE_LIMIT]]


def _conversion_success(original: pd.Series, parsed: pd.Series) -> float:
    original_non_null = int(original.notna().sum())
    if original_non_null == 0:
        return 1.0
    parsed_non_null = int(parsed.notna().sum())
    return parsed_non_null / original_non_null


def _count_modified(before: pd.Series, after: pd.Series) -> int:
    before_text = before.astype("string")
    after_text = after.astype("string")
    changed = (before_text != after_text).fillna(False)
    return int(changed.sum())


def _infer_series_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "text"
