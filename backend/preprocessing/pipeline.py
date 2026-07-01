#OMAR Mohassab preprocessing code Step 3
# ==========================================================begin=====

import io
import os
import pandas as pd

from api.supabase_client import (
    download_file_bytes,
    upload_cleaned_file_to_bucket,
    RAW_DATA_BUCKET,
)

from preprocessing.cleaning import preprocess_dataframe


CSV_ENCODING_FALLBACKS = (
    "utf-8",
    "utf-8-sig",
    "cp1252",
    "latin-1",
)


def _read_csv_with_fallback(raw_bytes: bytes) -> tuple[pd.DataFrame, str]:
    """
    Read CSV bytes using a sequence of common encodings.

    Returns:
        (dataframe, encoding_used)
    """
    for encoding in CSV_ENCODING_FALLBACKS:
        try:
            df = pd.read_csv(io.BytesIO(raw_bytes), encoding=encoding)
            return df, encoding
        except UnicodeDecodeError:
            continue

    # Last-resort decode replacement so malformed rows don't crash the pipeline.
    df = pd.read_csv(
        io.BytesIO(raw_bytes),
        encoding="utf-8",
        encoding_errors="replace",
    )
    return df, "utf-8-replace"


# Get file from Raw_data and convert it into parquet then store it in Cleaned_data
def process_file_to_parquet(user_id: str, storage_path: str) -> str:
    """
    Reads:  raw_data/{user_id}/<filename>
    Writes: cleaned_data/{user_id}/<filename>_cleaned.parquet
    """
    if not storage_path.startswith(f"{user_id}/"):
        raise ValueError("storage_path does not match user folder")

    raw_bytes = download_file_bytes(RAW_DATA_BUCKET, storage_path)
    _, ext = os.path.splitext(storage_path.lower())

    # Read raw into DataFrame
    if ext == ".csv":
        df, _encoding = _read_csv_with_fallback(raw_bytes)
    elif ext in [".xlsx", ".xls"]:
        df = pd.read_excel(io.BytesIO(raw_bytes))
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Preprocess
    df_clean = preprocess_dataframe(df)

    # Free the raw bytes + pre-clean frame so large uploads don't keep ~2-3x copies
    # in memory through the rest of the step.
    del raw_bytes, df

    # Lossless memory shrink: downcast integer columns to the smallest int dtype that
    # fits. Floats are left untouched to avoid any precision loss on metrics/money.
    for _col in df_clean.select_dtypes(include=["int", "int64"]).columns:
        df_clean[_col] = pd.to_numeric(df_clean[_col], downcast="integer")

    # Convert to parquet
    out_buffer = io.BytesIO()
    df_clean.to_parquet(out_buffer, index=False)  # requires pyarrow

    # Build cleaned path: same base + _cleaned.parquet
    base = os.path.splitext(storage_path)[0]      # "{user_id}/filename"
    cleaned_path = f"{base}_cleaned.parquet"      # "{user_id}/filename_cleaned.parquet"

    # Upload to cleaned_data bucket
    upload_cleaned_file_to_bucket(
        file_data=out_buffer.getvalue(),
        storage_path=cleaned_path,
        content_type="application/octet-stream",
    )

    return cleaned_path

    # ====================end====================
