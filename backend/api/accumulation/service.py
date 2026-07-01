"""
Pure helpers for accumulating multiple same-schema files into one dataset.

No Django/Supabase. Used by the upload and append views.
"""

from __future__ import annotations

import io
import os
import re

import pandas as pd

_ID_NAME_HINTS = ("id", "code", "uuid", "key", "number", "no")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """snake_case column names so schema comparison is apples-to-apples."""
    renamed = {}
    for col in df.columns:
        s = str(col).strip().lower()
        s = re.sub(r"[\s\-]+", "_", s)
        s = re.sub(r"[^0-9a-z_]", "", s)
        s = re.sub(r"_+", "_", s).strip("_")
        renamed[col] = s or "column"
    return df.rename(columns=renamed)


def schemas_match(cols_a, cols_b) -> tuple[bool, str]:
    """Name-set equality, order-independent. Types are tolerated because
    pandas concat coerces compatible numeric types."""
    set_a, set_b = set(cols_a), set(cols_b)
    if set_a == set_b:
        return True, ""
    missing = sorted(set_a - set_b)
    extra = sorted(set_b - set_a)
    parts = []
    if missing:
        parts.append(f"missing columns: {', '.join(missing)}")
    if extra:
        parts.append(f"unexpected columns: {', '.join(extra)}")
    return False, "; ".join(parts)


def detect_key(df: pd.DataFrame) -> list[str] | None:
    """Pick a single all-unique column that looks like an ID. If none,
    return None (caller falls back to exact-row dedup)."""
    n = len(df)
    if n == 0:
        return None
    for col in df.columns:
        name = str(col).lower()
        looks_like_id = any(
            h == name or name.endswith("_" + h) or name.startswith(h + "_")
            for h in _ID_NAME_HINTS
        )
        if looks_like_id and df[col].is_unique and df[col].notna().all():
            return [col]
    return None


def combine(frames: list[pd.DataFrame], key: list[str] | None) -> pd.DataFrame:
    """Concat frames; upsert (last-wins) on key if given, else drop exact
    duplicate rows."""
    combined = pd.concat(frames, ignore_index=True)
    if key:
        return combined.drop_duplicates(subset=key, keep="last").reset_index(drop=True)
    return combined.drop_duplicates(keep="first").reset_index(drop=True)


def read_upload(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Read an uploaded CSV/XLSX into a column-normalized DataFrame."""
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".csv":
        df = pd.read_csv(io.BytesIO(file_bytes))
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(file_bytes))
    else:
        raise ValueError(f"Unsupported file type: {ext or 'unknown'}")
    return normalize_columns(df)


def accumulate_files(
    files: list[tuple[str, bytes]],
    base_df: pd.DataFrame | None = None,
    allow_single: bool = False,
) -> dict:
    """
    Combine uploaded files (+ optional existing base_df) into one frame.

    Schema reference = base_df columns if given, else the first readable file.
    Files whose normalized column-name set doesn't match are rejected
    individually. Raises ValueError if nothing usable remains.

    Returns {dataframe, accepted: [filename], rejected: [{filename, reason}]}.
    """
    accepted: list[str] = []
    rejected: list[dict] = []
    frames: list[pd.DataFrame] = []
    reference_cols: list[str] | None = (
        list(base_df.columns) if base_df is not None else None
    )
    if base_df is not None:
        frames.append(base_df)

    for filename, raw in files:
        try:
            df = read_upload(raw, filename)
        except Exception as exc:  # noqa: BLE001
            rejected.append({"filename": filename, "reason": f"unreadable: {exc}"})
            continue
        if reference_cols is None:
            reference_cols = list(df.columns)
            frames.append(df)
            accepted.append(filename)
            continue
        ok, reason = schemas_match(reference_cols, list(df.columns))
        if not ok:
            rejected.append({"filename": filename, "reason": reason})
            continue
        frames.append(df[reference_cols])
        accepted.append(filename)

    if not accepted:
        raise ValueError("No files matched the expected schema.")
    if not allow_single and base_df is None and len(accepted) == 1 and rejected:
        raise ValueError("No files matched the expected schema.")

    key = detect_key(frames[0])
    combined = combine(frames, key=key)
    return {"dataframe": combined, "accepted": accepted, "rejected": rejected}
