"""
Pure computation strategies for data segmentation.

Each function takes a DataFrame + column names and returns a per-entity
DataFrame with segment assignments and scores.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _to_float64(series: pd.Series) -> pd.Series:
    """
    Convert any pandas Series (including nullable Float64/Int64 ExtensionArrays)
    to a plain float64 Series. Same pattern used in the forecasting service
    to fix pandas 2.x nullable dtype incompatibility with numpy ufuncs.
    """
    return pd.Series(
        pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan),
        index=series.index,
        name=series.name,
    )


# ══════════════════════════════════════════════════════════════
# RFM SEGMENTATION
# ══════════════════════════════════════════════════════════════

RFM_SEGMENT_MAP = {
    (5, 5, 5): "Champions",
    (5, 5, 4): "Champions",
    (5, 4, 5): "Champions",
    (5, 4, 4): "Champions",
    (4, 5, 5): "Loyal Customers",
    (4, 5, 4): "Loyal Customers",
    (4, 4, 5): "Loyal Customers",
    (4, 4, 4): "Loyal Customers",
    (5, 3, 5): "Loyal Customers",
    (5, 3, 4): "Loyal Customers",
    (3, 5, 5): "Loyal Customers",
    (5, 5, 3): "Potential Loyalists",
    (5, 4, 3): "Potential Loyalists",
    (4, 5, 3): "Potential Loyalists",
    (4, 4, 3): "Potential Loyalists",
    (5, 5, 2): "Potential Loyalists",
    (5, 4, 2): "Potential Loyalists",
    (5, 5, 1): "New Customers",
    (5, 4, 1): "New Customers",
    (5, 3, 1): "New Customers",
    (4, 5, 1): "New Customers",
    (4, 4, 1): "New Customers",
    (5, 3, 2): "Promising",
    (4, 3, 3): "Promising",
    (4, 3, 2): "Promising",
    (3, 3, 3): "Need Attention",
    (3, 3, 4): "Need Attention",
    (3, 4, 4): "Need Attention",
    (3, 4, 3): "Need Attention",
    (3, 3, 5): "Need Attention",
    (2, 3, 3): "About to Sleep",
    (2, 3, 4): "About to Sleep",
    (2, 2, 3): "About to Sleep",
    (2, 2, 4): "About to Sleep",
    (3, 2, 3): "About to Sleep",
    (1, 3, 3): "At Risk",
    (1, 3, 4): "At Risk",
    (1, 3, 5): "At Risk",
    (1, 4, 4): "At Risk",
    (1, 4, 5): "At Risk",
    (1, 5, 5): "At Risk",
    (2, 5, 5): "At Risk",
    (2, 4, 5): "At Risk",
    (2, 5, 4): "At Risk",
    (1, 5, 4): "At Risk",
    (1, 5, 3): "Can't Lose Them",
    (1, 4, 3): "Can't Lose Them",
    (2, 5, 3): "Can't Lose Them",
    (1, 5, 2): "Hibernating",
    (1, 4, 2): "Hibernating",
    (1, 3, 2): "Hibernating",
    (2, 3, 2): "Hibernating",
    (2, 4, 2): "Hibernating",
    (1, 2, 2): "Lost",
    (1, 1, 1): "Lost",
    (1, 1, 2): "Lost",
    (1, 2, 1): "Lost",
    (2, 1, 1): "Lost",
    (2, 2, 1): "Lost",
    (2, 2, 2): "Lost",
    (1, 1, 3): "Lost",
    (1, 2, 3): "Lost",
    (2, 1, 2): "Lost",
    (2, 1, 3): "Lost",
}


def _rfm_label(r: int, f: int, m: int) -> str:
    """Look up the human-readable segment name for an (R, F, M) score tuple."""
    label = RFM_SEGMENT_MAP.get((r, f, m))
    if label:
        return label
    avg = (r + f + m) / 3
    if avg >= 4:
        return "Loyal Customers"
    if avg >= 3:
        return "Need Attention"
    if avg >= 2:
        return "At Risk"
    return "Lost"


def _safe_qcut(series: pd.Series, q: int, target_labels: list) -> pd.Series:
    """
    pd.qcut that adapts to the actual number of bins after dropping duplicates.
    Always converts to plain float64 first to avoid pandas 2.x nullable dtype issues.
    Falls back to integer bin indices remapped to target_labels range.
    """
    clean = _to_float64(series)
    mid = target_labels[len(target_labels) // 2]
    try:
        result = pd.qcut(clean, q=q, labels=target_labels, duplicates="drop")
        # astype on Categorical with integer labels: go through float to avoid nullable Int64
        return result.astype(float).astype(int)
    except ValueError:
        # Fewer bins than labels — use raw integer indices then remap proportionally
        try:
            raw = pd.qcut(clean, q=q, labels=False, duplicates="drop").astype(float)
        except ValueError:
            return pd.Series([mid] * len(series), index=series.index, dtype=int)
        n_bins = int(raw.max()) + 1 if raw.notna().any() else 1
        if n_bins <= 1:
            return pd.Series([mid] * len(series), index=series.index, dtype=int)
        step = (len(target_labels) - 1) / (n_bins - 1)
        idx_map = {i: target_labels[round(i * step)] for i in range(n_bins)}
        return raw.map(idx_map).fillna(target_labels[0]).astype(int)


def rfm_segmentation(
    df: pd.DataFrame,
    entity_col: str,
    date_col: str,
    monetary_col: str,
) -> pd.DataFrame:
    """
    Build an RFM table and assign segments.

    Returns a DataFrame with columns:
        entity, recency, frequency, monetary, r_score, f_score, m_score, segment
    """
    work = df[[entity_col, date_col, monetary_col]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work[monetary_col] = pd.to_numeric(work[monetary_col], errors="coerce")
    work.dropna(subset=[entity_col, date_col, monetary_col], inplace=True)

    if work.empty:
        raise ValueError("No valid rows after cleaning for RFM segmentation")

    reference_date = work[date_col].max() + pd.Timedelta(days=1)

    rfm = (
        work.groupby(entity_col)
        .agg(
            recency=(date_col, lambda x: (reference_date - x.max()).days),
            frequency=(date_col, "count"),
            monetary=(monetary_col, "sum"),
        )
        .reset_index()
    )
    rfm.rename(columns={entity_col: "entity"}, inplace=True)

    # Cast agg results to plain float64 — groupby in pandas 2.x can produce
    # nullable Float64/Int64 ExtensionArrays that break numpy ufuncs (same issue
    # as the forecasting service's Prophet fix).
    rfm["recency"] = _to_float64(rfm["recency"])
    rfm["frequency"] = _to_float64(rfm["frequency"])
    rfm["monetary"] = _to_float64(rfm["monetary"])

    # Recency: lower is better → reverse labels (5 = most recent)
    rfm["r_score"] = _safe_qcut(rfm["recency"], q=5, target_labels=[5, 4, 3, 2, 1])
    rfm["f_score"] = _safe_qcut(rfm["frequency"].rank(method="first"), q=5, target_labels=[1, 2, 3, 4, 5])
    rfm["m_score"] = _safe_qcut(rfm["monetary"].rank(method="first"), q=5, target_labels=[1, 2, 3, 4, 5])

    rfm["segment"] = rfm.apply(
        lambda row: _rfm_label(row["r_score"], row["f_score"], row["m_score"]),
        axis=1,
    )

    return rfm


# ══════════════════════════════════════════════════════════════
# ABC / PARETO ANALYSIS
# ══════════════════════════════════════════════════════════════

def abc_analysis(
    df: pd.DataFrame,
    entity_col: str,
    value_col: str,
) -> pd.DataFrame:
    """
    Classify entities into A/B/C based on cumulative contribution.

    Returns DataFrame with: entity, total_value, cumulative_pct, segment
    """
    work = df[[entity_col, value_col]].copy()
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
    work.dropna(subset=[entity_col, value_col], inplace=True)

    if work.empty:
        raise ValueError("No valid rows after cleaning for ABC analysis")

    abc = (
        work.groupby(entity_col)[value_col]
        .sum()
        .reset_index()
        .rename(columns={entity_col: "entity", value_col: "total_value"})
    )
    # Cast to plain float64 to avoid nullable ExtensionArray issues
    abc["total_value"] = _to_float64(abc["total_value"])
    abc.sort_values("total_value", ascending=False, inplace=True)
    abc["cumulative_pct"] = (abc["total_value"].cumsum() / abc["total_value"].sum() * 100).round(2)

    abc["segment"] = np.where(
        abc["cumulative_pct"] <= 80,
        "A - Top Performers",
        np.where(abc["cumulative_pct"] <= 95, "B - Moderate", "C - Low Impact"),
    )

    return abc.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# K-MEANS CLUSTERING
# ══════════════════════════════════════════════════════════════

def kmeans_segmentation(
    df: pd.DataFrame,
    entity_col: str | None,
    numeric_cols: list[str],
    max_k: int = 6,
) -> tuple[pd.DataFrame, dict]:
    """
    Run K-Means clustering on numeric features.

    Returns:
        (result_df, meta)
        result_df has: entity (or index), cluster, pca_x, pca_y, + original numeric cols
        meta has: best_k, silhouette_score, feature_importances
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    work = df.copy()
    for col in numeric_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    if entity_col and entity_col in work.columns:
        entities = work[entity_col].copy()
        work_numeric = work[numeric_cols].copy()
    else:
        entities = pd.Series(range(len(work)), name="row_index")
        work_numeric = work[numeric_cols].copy()

    work_numeric.dropna(inplace=True)
    if len(work_numeric) < 10:
        raise ValueError(f"Need at least 10 rows for clustering, got {len(work_numeric)}")

    entities = entities.loc[work_numeric.index]

    scaler = StandardScaler()
    scaled = scaler.fit_transform(work_numeric)

    min_k = 3
    max_k = min(max_k, len(work_numeric) - 1)
    if max_k < min_k:
        max_k = min_k

    best_k = min_k
    best_score = -1
    best_labels = None

    for k in range(min_k, max_k + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=42, max_iter=300)
        labels = km.fit_predict(scaled)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(scaled, labels, sample_size=min(5000, len(scaled)))
        if score > best_score:
            best_score = score
            best_k = k
            best_labels = labels

    if best_labels is None:
        km = KMeans(n_clusters=min_k, n_init=10, random_state=42)
        best_labels = km.fit_predict(scaled)
        best_score = 0.0

    # PCA for 2D scatter visualization
    pca_x = np.zeros(len(scaled))
    pca_y = np.zeros(len(scaled))
    if scaled.shape[1] >= 2:
        try:
            from sklearn.decomposition import PCA
            pca = PCA(n_components=2, random_state=42)
            coords = pca.fit_transform(scaled)
            pca_x = coords[:, 0]
            pca_y = coords[:, 1]
        except Exception:
            pass

    result = work_numeric.copy()
    result["entity"] = entities.values
    result["cluster"] = best_labels
    result["pca_x"] = pca_x
    result["pca_y"] = pca_y

    # Feature importance via cluster center distances
    km_final = KMeans(n_clusters=best_k, n_init=10, random_state=42)
    km_final.fit(scaled)
    center_spread = np.std(km_final.cluster_centers_, axis=0)
    importance = dict(zip(numeric_cols, (center_spread / (center_spread.sum() + 1e-9)).round(4).tolist()))

    meta = {
        "best_k": int(best_k),
        "silhouette_score": round(float(best_score), 4),
        "feature_importances": importance,
    }

    return result, meta
