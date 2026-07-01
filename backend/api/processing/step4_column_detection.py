"""
Step 4: Technical Column Profiling

Pure Python + pandas. No Django, no Supabase dependencies.
Takes file bytes in, returns a dict of profiling results out.

Responsibilities:
  - Clean column names (lowercase, underscores)
  - Detect technical data type using Soft Detection (no data modification)
  - Compute technical_stats JSON per column
  - Compute file-level info (row_count, column_count, etc.)

Does NOT:
  - Insert rows into dataset_rows (Step 3)
  - Determine semantic_meaning, column_role, or ai_profile (Step 5)
  - Fill datasets.global_context (Step 6)
"""

import io
import re
import math

import pandas as pd

PHONE_LIKE_HINTS = (
    "phone",
    "mobile",
    "tel",
    "telephone",
    "fax",
    "cell",
    "whatsapp",
    "dial_code",
    "dialcode",
)


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def run_step4(file_bytes: bytes, filename: str) -> dict:
    """
    Main entry point for Step 4 technical profiling.

    Args:
        file_bytes: Raw bytes of the cleaned file (from cleaned_data bucket).
        filename:   Original filename (used to determine CSV vs Excel).

    Returns:
        {
            "file_info": {
                "row_count": int,
                "column_count": int,
                "file_size_bytes": int,
                "encoding": str
            },
            "columns": [
                {
                    "original_name": str,
                    "clean_name": str,
                    "data_type": str,
                    "technical_stats": dict,
                    "ai_profile": None
                },
                ...
            ]
        }
    """
    # ── Read file into DataFrame ──────────────────────────────
    df, encoding = _read_file(file_bytes, filename)

    # ── Build file-level info ─────────────────────────────────
    file_info = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "file_size_bytes": len(file_bytes),
        "encoding": encoding,
    }

    # ── Profile each column ───────────────────────────────────
    columns = []
    for col in df.columns:
        series = df[col]
        original_name = str(col)
        clean_name = _clean_column_name(original_name)
        detected_type = _detect_type(series, clean_name)
        stats = _compute_stats(series, detected_type)

        columns.append({
            "original_name": original_name,
            "clean_name": clean_name,
            "data_type": detected_type,
            "technical_stats": stats,
            "ai_profile": None,  # Step 5 fills this
        })

    return {
        "file_info": file_info,
        "columns": columns,
    }


# ══════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════

def _read_file(file_bytes: bytes, filename: str) -> tuple:
    """
    Read file bytes into a pandas DataFrame.

    Returns:
        (DataFrame, encoding_used: str)
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "parquet":
        df = pd.read_parquet(io.BytesIO(file_bytes))
        return df, "parquet"

    if ext in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(file_bytes))
        return df, "binary"

    # CSV: try utf-8 first, fall back to latin-1
    for enc in ("utf-8", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            return df, enc
        except UnicodeDecodeError:
            continue

    # Last resort: ignore errors
    df = pd.read_csv(io.BytesIO(file_bytes), encoding="utf-8", errors="ignore")
    return df, "utf-8-lossy"


def _clean_column_name(name: str) -> str:
    """
    Normalize a column name:
      - lowercase
      - replace spaces and special chars with underscores
      - collapse multiple underscores
      - strip leading/trailing underscores

    Examples:
        "Business Date"  -> "business_date"
        "Total (Price)"  -> "total_price"
        "  ID # "        -> "id"
    """
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name or "unnamed"


def _detect_type(series: pd.Series, col_name: str) -> str:
    """
    Soft Detection: detect the likely type WITHOUT modifying the data.

    Strategy:
      1. If ALL values are null, guess from column name (pandas defaults
         all-null to float64, which is misleading).
      2. If pandas already inferred numeric/datetime/bool, trust it
         (with extra checks for phone-number-like numerics).
      3. For 'object' columns, try parsing a sample as datetime then numeric.
      4. Fall back to 'text'.

    Returns one of: "numeric", "datetime", "text", "boolean"
    """
    # ── Handle all-null columns FIRST ─────────────────────────
    # pandas stores all-null as float64, which tricks is_numeric_dtype.
    # Use column name to guess, otherwise default to "text".
    if series.dropna().empty:
        if any(kw in col_name for kw in ("_at", "date", "time")):
            return "datetime"
        return "text"

    # Hard guard: phone/dial-like fields should be text identifiers,
    # not numeric measures, even if values are digit-only.
    if _is_phone_like_column(col_name):
        return "text"

    # ── Trust pandas' own inference ───────────────────────────
    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    if pd.api.types.is_numeric_dtype(series):
        # Extra guard: detect phone-number-like columns that pandas
        # auto-parsed (e.g. "+966501234567" becomes 966501234567.0).
        # Heuristic: if the column name contains "phone", "mobile",
        # "fax", or "tel", treat as text despite being numeric.
        phone_hints = ("phone", "mobile", "fax", "tel", "cell", "whatsapp")
        if any(hint in col_name for hint in phone_hints):
            return "text"
        return "numeric"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    # ── For object (text) columns, try soft detection on a sample ─
    non_null = series.dropna()
    sample = non_null.head(20)

    # Try datetime — but only if values look like date strings
    # (contain separators like /, -, or spaces). Pure integers
    # like 1001 should not be parsed as year 1001 AD.
    sample_strs = sample.astype(str)
    has_date_separators = sample_strs.str.contains(r'[/\-\s:]').any()
    if has_date_separators:
        try:
            pd.to_datetime(sample, format="mixed")
            return "datetime"
        except (ValueError, TypeError):
            pass

    # Try numeric
    try:
        pd.to_numeric(sample)
        return "numeric"
    except (ValueError, TypeError):
        pass

    return "text"


def _is_phone_like_column(col_name: str) -> bool:
    col = str(col_name or "").lower()
    return any(hint in col for hint in PHONE_LIKE_HINTS)


def _compute_stats(series: pd.Series, detected_type: str) -> dict:
    """
    Compute technical_stats JSON for one column.

    Always includes:
      - null_ratio, unique_ratio, top_5_samples

    Type-specific extras:
      - numeric:  min, max, mean, std_dev
      - datetime: min_date, max_date
      - text:     avg_char_length
    """
    total = len(series)

    stats = {
        "null_ratio": round(float(series.isnull().mean()), 4),
        "unique_ratio": round(
            series.nunique() / max(total, 1), 4
        ),
        "top_5_samples": _top_5_samples(series),
    }

    if detected_type == "numeric":
        # Temporarily coerce for stats — does not modify the actual data
        numeric = pd.to_numeric(series, errors="coerce")
        stats["min"] = _safe_float(numeric.min())
        stats["max"] = _safe_float(numeric.max())
        stats["mean"] = _safe_float(numeric.mean(), decimals=2)
        stats["std_dev"] = _safe_float(numeric.std(), decimals=2)

    elif detected_type == "datetime":
        dt = pd.to_datetime(series, errors="coerce", format="mixed")
        valid = dt.dropna()
        if not valid.empty:
            stats["min_date"] = str(valid.min())
            stats["max_date"] = str(valid.max())
        else:
            stats["min_date"] = None
            stats["max_date"] = None

    elif detected_type == "text":
        non_null_str = series.dropna().astype(str)
        if not non_null_str.empty:
            stats["avg_char_length"] = round(
                float(non_null_str.str.len().mean()), 1
            )
        else:
            stats["avg_char_length"] = 0.0

    return stats


# ── Tiny utilities ────────────────────────────────────────────

def _top_5_samples(series: pd.Series) -> list:
    """Return up to 5 most frequent non-null values as plain Python types."""
    non_null = series.dropna()
    if non_null.empty:
        return []
    top = non_null.value_counts().head(5).index.tolist()
    # Convert numpy types to native Python for JSON serialization
    return [_to_python(v) for v in top]


def _to_python(val):
    """Convert numpy/pandas scalar to JSON-safe Python types."""
    if hasattr(val, "item"):  # numpy scalar -> native python
        val = val.item()

    if pd.isna(val):
        return None

    if isinstance(val, pd.Timestamp):
        return val.isoformat()

    if isinstance(val, (int, float, str, bool)):
        return val

    return str(val)



def _safe_float(val, decimals: int = None) -> float | None:
    """Convert to float safely, returning None for NaN/inf."""
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        if decimals is not None:
            return round(f, decimals)
        return f
    except (TypeError, ValueError):
        return None
