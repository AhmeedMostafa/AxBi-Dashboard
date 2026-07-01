# OMAR Mohassab preprocessing code Step 3
# ==========================================================begin=====

import re
import pandas as pd
import numpy as np

NULL_LIKE_PATTERNS = [
    r"^\s*$",
    r"^\s*-\s*$",
    r"^\s*_\s*$",
    r"^\s*(?:n/?a)\s*$",
    r"^\s*null\s*$",
    r"^\s*none\s*$",
]

MAX_NULL_RATIO_TO_KEEP = 0.95


def _to_snake_case(name: str) -> str:
    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def _matches_any_keyword(column_name: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in column_name for keyword in keywords)


def _column_tokens(column_name: str) -> set[str]:
    return set(token for token in column_name.split("_") if token)


def _has_any_token(column_name: str, keywords: tuple[str, ...]) -> bool:
    tokens = _column_tokens(column_name)
    return any(keyword in tokens for keyword in keywords)


def _is_age_like_column(column_name: str) -> bool:
    return "age" in _column_tokens(column_name)


def _is_boolean_like_column(column_name: str) -> bool:
    return (
        column_name.startswith("is_")
        or column_name.startswith("has_")
        or column_name.endswith("_flag")
        or column_name.endswith("_enabled")
        or column_name.endswith("_active")
    )


def _normalize_gender(val):
    if pd.isna(val):
        return "Unknown"
    v = str(val).strip().lower()
    if v in {"m", "male"}:
        return "Male"
    if v in {"f", "female"}:
        return "Female"
    return "Unknown"


def _clean_age(val):
    if pd.isna(val):
        return np.nan

    number_words = {
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "twenty one": 21,
        "twenty two": 22,
        "twenty three": 23,
        "twenty four": 24,
        "twenty five": 25,
        "thirty": 30,
    }

    v = str(val).strip().lower()
    if v.isdigit():
        return int(v)
    return number_words.get(v, np.nan)


def _clean_money(val):
    if pd.isna(val):
        return np.nan

    v = str(val).strip().lower()
    v = v.replace("$", "")
    v = re.sub(r"\b(?:usd|egp|sar|aed|eur|gbp|dollar|riyal|pound)s?\b", "", v)
    v = v.replace(",", "").replace("_", "").strip()

    if v.endswith("k"):
        try:
            return float(v[:-1]) * 1000
        except ValueError:
            return np.nan

    try:
        return float(v)
    except ValueError:
        return np.nan


def _normalize_bool(val):
    if pd.isna(val):
        return np.nan
    v = str(val).strip().lower()
    if v in {"yes", "true", "1", "active", "enabled", "y", "t"}:
        return True
    if v in {"no", "false", "0", "inactive", "disabled", "n", "f"}:
        return False
    return np.nan


def _drop_low_information_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop columns that are mostly empty or not informative.
    - all-null columns
    - columns with null_ratio >= MAX_NULL_RATIO_TO_KEEP
    - columns with <= 1 unique non-null value
    """
    cols_to_drop = []
    row_count = len(df)

    for col in df.columns:
        series = df[col]
        null_ratio = float(series.isna().mean()) if row_count else 1.0
        unique_non_null = int(series.dropna().nunique())

        if null_ratio == 1.0:
            cols_to_drop.append(col)
            continue

        if null_ratio >= MAX_NULL_RATIO_TO_KEEP:
            cols_to_drop.append(col)
            continue

        if unique_non_null <= 1:
            cols_to_drop.append(col)

    return df.drop(columns=cols_to_drop, errors="ignore")


def preprocess_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Standardize column names once so matching logic can work with many variants.
    df.columns = [_to_snake_case(col) for col in df.columns]

    # Normalize null-like placeholders -> NaN
    df = df.replace(NULL_LIKE_PATTERNS, np.nan, regex=True)
    # Trim object columns
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].astype("string").str.strip()

    # Generic email normalization
    for col in df.columns:
        if _matches_any_keyword(col, ("email", "e_mail")):
            df[col] = df[col].astype("string").str.lower()

    # Generic gender normalization
    for col in df.columns:
        if _matches_any_keyword(col, ("gender", "sex")):
            df[col] = df[col].apply(_normalize_gender)

    # Generic age cleaning
    for col in df.columns:
        if _is_age_like_column(col):
            df[col] = df[col].apply(_clean_age)

    # Generic money/salary cleaning
    money_keywords = ("salary", "wage", "income", "pay", "amount", "price", "cost")
    for col in df.columns:
        if _has_any_token(col, money_keywords):
            df[col] = df[col].apply(_clean_money)

    # Generic datetime parsing
    datetime_keywords = ("date", "time", "timestamp")
    for col in df.columns:
        if _has_any_token(col, datetime_keywords) or col.endswith("_at"):
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")

    # Generic boolean normalization (only on likely flag columns)
    for col in df.columns:
        if _is_boolean_like_column(col):
            df[col] = df[col].apply(_normalize_bool)

    # Force consistency for numeric-leaning columns
    for col in df.columns:
        if _has_any_token(col, ("salary", "amount")) or _is_age_like_column(col):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Truncate extremely long text cells while preserving nulls.
    for col in df.select_dtypes(include=["string", "object"]).columns:
        str_values = df[col].astype("string").str.slice(0, 5000)
        df[col] = str_values.where(df[col].notna(), np.nan)

    # Drop low-information columns and fully empty rows.
    # Keep real NaN values in remaining columns for Step 4 statistics.
    df = _drop_low_information_columns(df)
    df = df.dropna(axis=0, how="all")
    df = df.drop_duplicates()

    return df


# ====================end====================
