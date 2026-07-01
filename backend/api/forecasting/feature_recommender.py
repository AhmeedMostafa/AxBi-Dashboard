"""
Correlation-based feature recommendation for the forecasting UI.

Pure module (no Django/Supabase). Given a dataframe, a target column,
and the time column, rank the remaining columns by their association
with the target:
  - numeric feature + numeric target  -> abs Pearson correlation
  - otherwise (categorical involved)  -> mutual information

Scores are normalized to 0..1 and returned sorted descending.
Never raises for caller-facing problems: returns
{"recommendations": [], "reason": "..."} instead.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MAX_ROWS_FOR_SCORING = 20000


def recommend_features(
    df: pd.DataFrame,
    target_column: str,
    time_column: str | None = None,
) -> dict:
    if df is None or df.empty:
        return {"recommendations": [], "reason": "Dataset is empty."}
    if target_column not in df.columns:
        return {"recommendations": [], "reason": f"Target column '{target_column}' not found."}

    work = df
    if len(work) > MAX_ROWS_FOR_SCORING:
        work = work.sample(MAX_ROWS_FOR_SCORING, random_state=42)

    excluded = {target_column}
    if time_column:
        excluded.add(time_column)
    candidates = [c for c in work.columns if c not in excluded]
    if not candidates:
        return {"recommendations": [], "reason": "No candidate feature columns available."}

    target = work[target_column]
    target_numeric = pd.to_numeric(target, errors="coerce")
    target_is_numeric = target_numeric.notna().mean() >= 0.8

    scored: list[dict] = []
    for col in candidates:
        try:
            entry = _score_column(work[col], target, target_numeric, target_is_numeric)
        except Exception as exc:  # noqa: BLE001 - scoring must never break the endpoint
            logger.warning("feature_recommender: failed scoring %s: %s", col, exc)
            entry = None
        if entry is not None:
            scored.append({"feature": col, **entry})

    if not scored:
        return {"recommendations": [], "reason": "Could not compute associations for any column."}

    max_score = max(s["raw"] for s in scored) or 1.0
    recommendations = [
        {
            "feature": s["feature"],
            "score": round(min(1.0, s["raw"] / max_score), 4),
            "method": s["method"],
        }
        for s in sorted(scored, key=lambda s: s["raw"], reverse=True)
    ]
    return {"recommendations": recommendations, "reason": ""}


def _score_column(
    series: pd.Series,
    target_raw: pd.Series,
    target_numeric: pd.Series,
    target_is_numeric: bool,
) -> dict | None:
    feat_numeric = pd.to_numeric(series, errors="coerce")
    feat_is_numeric = feat_numeric.notna().mean() >= 0.8

    if feat_is_numeric and target_is_numeric:
        pair = pd.DataFrame({"f": feat_numeric, "t": target_numeric}).dropna()
        if len(pair) < 3 or pair["f"].nunique() < 2:
            return None
        corr = pair["f"].corr(pair["t"], method="pearson")
        if corr is None or np.isnan(corr):
            return None
        return {"raw": float(abs(corr)), "method": "pearson"}

    return _mutual_info_score(series, target_raw, target_is_numeric)


def _mutual_info_score(
    series: pd.Series,
    target_raw: pd.Series,
    target_is_numeric: bool,
) -> dict | None:
    try:
        from sklearn.feature_selection import (
            mutual_info_classif,
            mutual_info_regression,
        )
    except ImportError:
        return None

    pair = pd.DataFrame({"f": series, "t": target_raw}).dropna()
    if len(pair) < 5 or pair["f"].nunique() < 2:
        return None

    f_encoded = pd.factorize(pair["f"].astype(str))[0].reshape(-1, 1)

    if target_is_numeric:
        t_vals = pd.to_numeric(pair["t"], errors="coerce").to_numpy(dtype=float)
        mask = ~np.isnan(t_vals)
        if mask.sum() < 5:
            return None
        mi = mutual_info_regression(
            f_encoded[mask], t_vals[mask], discrete_features=True, random_state=42
        )
    else:
        t_encoded = pd.factorize(pair["t"].astype(str))[0]
        mi = mutual_info_classif(
            f_encoded, t_encoded, discrete_features=True, random_state=42
        )

    score = float(mi[0]) if len(mi) else 0.0
    if score <= 0.0:
        return None
    return {"raw": score, "method": "mutual_info"}
