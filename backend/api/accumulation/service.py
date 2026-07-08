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
_CSV_ENCODING_FALLBACKS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


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


def _looks_like_html(raw: bytes) -> bool:
    head = raw[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"<head")


def _is_zip_ooxml(raw: bytes) -> bool:
    """XLSX (and other Office Open XML) files are ZIP archives."""
    return len(raw) >= 2 and raw[:2] == b"PK"


def _is_legacy_xls(raw: bytes) -> bool:
    return len(raw) >= 8 and raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _read_excel_bytes(raw: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(raw))


def _read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Read CSV bytes with encoding + delimiter fallbacks (common for web downloads)."""
    if not raw or not raw.strip():
        raise ValueError("File is empty.")
    if _looks_like_html(raw):
        raise ValueError(
            "File looks like a web page, not a CSV. Download the dataset file directly "
            "(Save link as…) instead of saving an HTML login or error page."
        )

    last_err: Exception | None = None
    for encoding in _CSV_ENCODING_FALLBACKS:
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding)
            if df.shape[1] >= 2:
                return df
            # One column often means the wrong delimiter (e.g. European ;-separated CSV).
            first_col = str(df.columns[0]) if len(df.columns) else ""
            for sep in (";", "\t", "|"):
                if sep in first_col or sep in raw[:4096].decode(encoding, errors="ignore"):
                    try:
                        df_alt = pd.read_csv(io.BytesIO(raw), encoding=encoding, sep=sep)
                        if df_alt.shape[1] >= 2:
                            return df_alt
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
            return df
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
        except pd.errors.EmptyDataError:
            raise ValueError("CSV file has no data rows.") from None
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue

    try:
        return pd.read_csv(
            io.BytesIO(raw),
            encoding="utf-8",
            encoding_errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(last_err or exc)) from exc


def _accumulation_failure(accepted: list[str], rejected: list[dict]) -> ValueError:
    """Build a user-facing error from rejected files (avoid generic schema-only text)."""
    if len(rejected) == 1 and not accepted:
        reason = rejected[0]["reason"]
        if reason.startswith("unreadable: "):
            reason = reason[len("unreadable: "):]
        name = rejected[0]["filename"]
        return ValueError(f"Could not read {name}: {reason}")
    if rejected:
        detail = "; ".join(f"{r['filename']}: {r['reason']}" for r in rejected)
        if accepted:
            return ValueError(f"No additional files matched the expected schema. {detail}")
        return ValueError(f"No usable files. {detail}")
    return ValueError("No files matched the expected schema.")


def read_upload(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Read an uploaded CSV/XLSX into a column-normalized DataFrame."""
    ext = os.path.splitext(filename.lower())[1]

    # Content beats extension — downloads are often Excel saved as ".csv".
    if _is_zip_ooxml(file_bytes) or _is_legacy_xls(file_bytes):
        try:
            df = _read_excel_bytes(file_bytes)
        except Exception as exc:  # noqa: BLE001
            hint = "Rename the file to .xlsx and upload again." if ext == ".csv" else ""
            msg = f"File looks like Excel but could not be opened: {exc}"
            if hint:
                msg = f"{msg} {hint}"
            raise ValueError(msg) from exc
    elif ext == ".csv":
        if _looks_like_html(file_bytes):
            raise ValueError(
                "File looks like a web page, not a CSV. Download the dataset file directly "
                "(Save link as…) instead of saving an HTML login or error page."
            )
        df = _read_csv_bytes(file_bytes)
    elif ext in (".xlsx", ".xls"):
        df = _read_excel_bytes(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: {ext or 'unknown'}")
    if df.empty and len(df.columns) == 0:
        raise ValueError("File has no columns.")
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
        raise _accumulation_failure(accepted, rejected)
    if not allow_single and base_df is None and len(accepted) == 1 and rejected:
        raise _accumulation_failure(accepted, rejected)

    key = detect_key(frames[0])
    combined = combine(frames, key=key)
    return {"dataframe": combined, "accepted": accepted, "rejected": rejected}
