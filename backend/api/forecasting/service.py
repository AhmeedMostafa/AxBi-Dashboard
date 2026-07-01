"""
Forecasting service v2.

Implements:
- Forecast readiness checks
- Frequency detection + alignment
- Baselines (naive, seasonal naive)
- ETS / Exponential Smoothing (Holt-Winters)
- SARIMAX with auto-ARIMA order selection (optional dependency)
- CatBoost with Fourier features (optional dependency)
- IQR-based outlier capping + log transform detection
- Rolling backtest model competition
- Widening prediction intervals
- Historical data in response for charting
- Unified run_forecast_service entry point
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


MIN_HISTORY_ROWS = 3
DEFAULT_HORIZON = 30
DEFAULT_BACKTEST_FOLDS = 3

# Two-stage tournament: screen every candidate on a single cheap fold, then pay for full
# (adaptive) cross-validation + the holdout fit only on the top survivors. Also-rans never
# pay the expensive folds. Cheap baselines are always kept (needed for baseline_comparison
# and the ensemble blend). Only engaged when there's something worth pruning.
TOURNAMENT_MIN_CANDIDATES = 4
TOURNAMENT_STAGE1_FOLDS = 1
TOURNAMENT_KEEP_TOP = 3
TOURNAMENT_ALWAYS_KEEP = ("naive", "seasonal_naive")
MAX_HORIZON = 365
DEFAULT_MISSING_PERIODS_POLICY = "drop"
PRIMARY_SELECTION_METRIC = "mae"
SELECTION_METRIC_ORDER = ("mae", "rmse", "wape", "mase")
INTERVAL_COVERAGE = 0.80
INTERVAL_RESIDUAL_CLIP_QUANTILES = (0.05, 0.95)
INTERVAL_MIN_PER_STEP_SAMPLES = 6

# 70 / 30 holdout split for final test-set evaluation
TEST_SPLIT_RATIO = 0.30

# Thread timeout for slow model fits (seconds)
SARIMAX_FIT_TIMEOUT_S = 25
AUTO_ARIMA_TIMEOUT_S = 20

MODEL_MIN_HISTORY: dict[str, int] = {
    "naive": 3,
    "seasonal_naive": 6,
    "ets": 10,
    "exp_smoothing": 10,
    "sarimax": 20,
    "catboost": 30,
    "lightgbm": 30,
    "prophet": 20,
}

DEFAULT_MODEL_ORDER = (
    "naive",
    "seasonal_naive",
    "ets",
    "sarimax",
    "catboost",
    "lightgbm",
    "prophet",
)

# Every model id that _build_model() can actually construct. Used to validate
# caller-supplied candidate_models up front so a bad id fails loud instead of
# being silently dropped mid-loop ("Unsupported model: X", folds=0).
SUPPORTED_MODELS = frozenset({
    "naive", "seasonal_naive", "ets", "exp_smoothing",
    "sarimax", "catboost", "lightgbm", "prophet",
})

# When an auto-detected DAILY series has more unique periods than this, downsample
# to WEEKLY before training. Keeps heavy models (SARIMAX/Prophet) fast and the
# backtest tractable on very long histories (e.g. 400k-row multi-entity uploads
# collapse to ~8000 daily points -> ~1140 weekly points).
MAX_DAILY_POINTS_BEFORE_WEEKLY = 1500

# Frequency-agnostic ceiling on the number of modeled series points. Forecast cost
# scales with POINTS, not input rows (all dimensions are summed into one series), and
# the detector never returns sub-daily (it floors at "D"), so the only way to exceed
# this is long daily/weekly history. We coarsen the frequency (D->W->MS->QS) until the
# series is under the cap, then truncate to the most-recent N as a last resort. This is
# what makes a forecast finish fast regardless of how many rows were uploaded.
# Both modes use the SAME point cap so they forecast at the same grain. "fast" gets its
# speed by skipping the slow Prophet/SARIMAX fits (SLOW_MODELS), NOT by coarsening the
# series — an earlier aggressive 400 cap made fast SLOWER (monthly grain pushed points
# into a higher CV-fold tier) and produced a worse forecast. Keep them equal.
MAX_SERIES_POINTS_FAST = 1200       # "fast" mode: cheap models only, sync
MAX_SERIES_POINTS_ACCURATE = 1200   # "accurate" mode: all models, runs async
_COARSER_FREQ = {"D": "W", "W": "MS", "MS": "QS", "QS": None}

# Models skipped in "fast" mode. Fast = cheap statistical models + the single fast tree
# (LightGBM). We drop the slow fitters: SARIMAX (auto_arima grid), Prophet (cmdstan), and
# CatBoost — CatBoost is ~2-3x LightGBM's fit time, and measured tests showed fast running
# BOTH trees was SLOWER than accurate (whose larger pool lets the tournament prune CatBoost).
# Keeping only LightGBM makes fast genuinely faster than accurate at the same grain.
SLOW_MODELS = frozenset({"sarimax", "prophet", "catboost"})
# Skipped even in "accurate" mode: SARIMAX's auto_arima grid is pathologically slow on
# weekly/seasonal data and usually times out (folds=0) — pure wasted runtime. Prophet +
# both trees stay in accurate (the tournament prunes whichever underperforms).
ACCURATE_SKIP_MODELS = frozenset({"sarimax"})

# Forecast execution modes.
FORECAST_MODES = ("fast", "accurate")
DEFAULT_FORECAST_MODE = "fast"

HISTORY_TAIL_FOR_CHART = 90

# Season lengths per frequency.
# Daily: 7 is used for *weekly* seasonality (short-cycle patterns like weekday/weekend).
# For annual seasonality in daily data (temperature, energy) Prophet/ETS handle it
# via their own internal annual Fourier components, so 7 is the correct value here
# for the SeasonalNaive + feature-engineering lags.
FREQUENCY_TO_SEASON_LENGTH = {
    "D": 7,
    "W": 52,
    "MS": 12,
    "QS": 4,
}

# Maximum training rows passed to tree models and SARIMAX during each CV fold.
# For large daily series (> 1000 rows) the models are accurate on a recent window.
# Capping avoids O(n^2) fitting time without significant accuracy loss.
CV_TRAIN_CAP: dict[str, int] = {
    "lightgbm": 1095,  # 3 years: covers full annual lag cycle without O(n^2) fit cost
    "catboost": 1095,
    "prophet": 1095,   # Prophet needs 3 years to estimate annual Fourier seasonality
    "sarimax": 500,    # auto_arima is slow; 500 rows is sufficient for order selection
}


def _get_model_min_history(model_name: str, season_length: int = 1) -> int:
    """Return the minimum history rows required for a given model.

    For seasonal_naive, the minimum is max(base, 2 * season_length)
    so at least two full seasonal cycles are available.
    """
    base = MODEL_MIN_HISTORY.get(model_name, MIN_HISTORY_ROWS)
    if model_name == "seasonal_naive":
        return max(base, 2 * season_length)
    return base


def _should_log_transform(series: pd.Series) -> bool:
    """Detect if the series is multiplicative / exponential.

    Heuristic: all values are positive, and the coefficient of variation
    (std/mean) is high enough to suggest a multiplicative structure.
    Also checks whether log-space residuals have lower variance than
    raw-space residuals after removing a linear trend.
    """
    vals = series.dropna().astype(float)
    if len(vals) < 10 or vals.min() <= 0:
        return False

    cv = float(vals.std() / vals.mean()) if vals.mean() > 0 else 0.0
    if cv < LOG_TRANSFORM_CV_THRESHOLD:
        return False

    x = np.arange(len(vals), dtype=float)
    raw_resid_var = float(np.polyfit(x, vals.values, 1, full=True)[1][0]) if len(vals) > 2 else 1.0
    log_vals = np.log(vals.values)
    log_resid_var = float(np.polyfit(x, log_vals, 1, full=True)[1][0]) if len(vals) > 2 else 1.0

    return log_resid_var < raw_resid_var


def _apply_log_transform(series: np.ndarray) -> np.ndarray:
    return np.log(np.maximum(series, LOG_TRANSFORM_MIN_VALUE))


def _invert_log_transform(series: np.ndarray) -> np.ndarray:
    return np.exp(series)


class ForecastingError(Exception):
    pass


class DependencyUnavailableError(ForecastingError):
    pass


def _run_with_timeout(fn, timeout_seconds: int, *args, **kwargs):
    """Run *fn* in a background thread; raise ForecastingError on timeout."""
    result: list = [None]
    exc: list = [None]

    def _worker():
        try:
            result[0] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            exc[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    if t.is_alive():
        raise ForecastingError(
            f"Operation timed out after {timeout_seconds}s. "
            "Try a simpler model or reduce the dataset."
        )
    if exc[0] is not None:
        raise exc[0]
    return result[0]


LOG_TRANSFORM_CV_THRESHOLD = 0.5
LOG_TRANSFORM_MIN_VALUE = 1e-9


@dataclass
class PreparedSeries:
    frame: pd.DataFrame
    time_column: str
    target_column: str
    feature_columns: list[str]
    frequency: str
    season_length: int
    log_transformed: bool = False
    anomalies: list[dict] = None  # outlier points that were capped

    def __post_init__(self):
        if self.anomalies is None:
            self.anomalies = []


class BaseForecaster:
    model_name = "base"

    def __init__(
        self,
        time_column: str,
        target_column: str,
        feature_columns: list[str],
        frequency: str,
        season_length: int,
    ):
        self.time_column = time_column
        self.target_column = target_column
        self.feature_columns = feature_columns
        self.frequency = frequency
        self.season_length = max(1, int(season_length))

    def fit(self, train_df: pd.DataFrame):
        raise NotImplementedError

    def predict(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
    ) -> np.ndarray:
        raise NotImplementedError

    def predict_with_intervals(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
        alpha: float = 0.05,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        """Return (predictions, lower, upper). Base returns None for intervals."""
        pred = self.predict(horizon=horizon, future_frame=future_frame)
        return pred, None, None


class NaiveForecaster(BaseForecaster):
    model_name = "naive"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_value: float = 0.0

    def fit(self, train_df: pd.DataFrame):
        self.last_value = float(train_df[self.target_column].iloc[-1])

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        return np.repeat(self.last_value, horizon).astype(float)


class SeasonalNaiveForecaster(BaseForecaster):
    model_name = "seasonal_naive"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history: np.ndarray | None = None
        self.last_value: float = 0.0

    def fit(self, train_df: pd.DataFrame):
        values = train_df[self.target_column].astype(float).to_numpy()
        self.history = values
        self.last_value = float(values[-1])

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        if self.history is None or len(self.history) == 0:
            return np.zeros(horizon, dtype=float)
        if len(self.history) < self.season_length:
            return np.repeat(self.last_value, horizon).astype(float)

        season_values = self.history[-self.season_length :]
        out = [float(season_values[i % len(season_values)]) for i in range(horizon)]
        return np.asarray(out, dtype=float)


class ETSForecaster(BaseForecaster):
    """Exponential Smoothing (Holt-Winters). Handles trend + seasonality."""

    model_name = "ets"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None

    def fit(self, train_df: pd.DataFrame):
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "statsmodels is not installed. Install statsmodels to enable ETS."
            ) from exc
        except Exception as exc:
            raise DependencyUnavailableError(
                f"statsmodels failed to import ({exc}). "
                "Upgrade statsmodels (>=0.14.6) for pandas 3.x compatibility."
            ) from exc

        endog = train_df[self.target_column].astype(float).to_numpy()

        trend: str | None = "add" if len(endog) >= 10 else None
        seasonal: str | None = None
        seasonal_periods: int | None = None
        if self.season_length > 1 and len(endog) >= 2 * self.season_length:
            seasonal = "add"
            seasonal_periods = self.season_length

        try:
            ets = ExponentialSmoothing(
                endog=endog,
                trend=trend,
                seasonal=seasonal,
                seasonal_periods=seasonal_periods,
                initialization_method="estimated",
            )
            self.model = ets.fit(optimized=True)
        except Exception:
            # Fallback: no seasonal component
            ets = ExponentialSmoothing(
                endog=endog,
                trend=trend,
                seasonal=None,
                initialization_method="estimated",
            )
            self.model = ets.fit(optimized=True)

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        if self.model is None:
            raise ForecastingError("ETS model is not fitted.")
        return np.asarray(self.model.forecast(horizon), dtype=float)


class SarimaxForecaster(BaseForecaster):
    model_name = "sarimax"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.exog_maps: dict[str, dict[str, int]] = {}
        self.last_exog_row: dict[str, Any] = {}

    def fit(self, train_df: pd.DataFrame):
        try:
            from statsmodels.tsa.statespace.sarimax import SARIMAX
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "statsmodels is not installed. Install statsmodels to enable SARIMAX."
            ) from exc
        except Exception as exc:
            raise DependencyUnavailableError(
                f"statsmodels failed to import ({exc}). "
                "Upgrade statsmodels (>=0.14.6) for pandas 3.x compatibility."
            ) from exc

        endog = train_df[self.target_column].astype(float).to_numpy()
        exog = self._encode_exog_train(train_df, self.feature_columns)

        order, seasonal_order = self._select_orders(endog)

        sarimax = SARIMAX(
            endog=endog,
            exog=exog,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        self.model = _run_with_timeout(
            lambda: sarimax.fit(disp=False),
            SARIMAX_FIT_TIMEOUT_S,
        )
        if self.feature_columns:
            self.last_exog_row = train_df[self.feature_columns].iloc[-1].to_dict()

    def _select_orders(self, endog: np.ndarray):
        """Select ARIMA orders via AIC grid search on pure statsmodels.

        Searches (p,d,q) ∈ {0,1,2}² × d∈{0,1} plus a seasonal term when the
        series is long enough.  Falls back to a simple heuristic if the grid
        search fails entirely.  No pmdarima C-extension required.
        """
        def _run_grid():
            return self._select_orders_aic_grid(endog)

        try:
            return _run_with_timeout(_run_grid, AUTO_ARIMA_TIMEOUT_S)
        except Exception:
            # Last-resort heuristic
            seasonal_order = (0, 0, 0, 0)
            if self.season_length > 1 and len(endog) >= self.season_length * 2:
                seasonal_order = (1, 1, 1, self.season_length)
            return self._select_order_heuristic(endog, len(endog)), seasonal_order

    def _select_orders_aic_grid(self, endog: np.ndarray):
        """Grid search (p,d,q) × seasonal by AIC using pure statsmodels."""
        from statsmodels.tsa.statespace.sarimax import SARIMAX as _SARIMAX
        from statsmodels.tsa.stattools import adfuller
        import warnings

        n = len(endog)

        # Determine d via ADF
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pval = adfuller(endog, maxlag=min(12, n // 3))[1]
            d = 0 if pval < 0.05 else 1
        except Exception:
            d = 1

        use_seasonal = self.season_length > 1 and n >= self.season_length * 2
        D = 1 if use_seasonal else 0
        s = self.season_length if use_seasonal else 0
        seasonal_candidates = [(1, D, 1, s)] if use_seasonal else [(0, 0, 0, 0)]

        best_aic = float("inf")
        best_order = (1, d, 1)
        best_seasonal = seasonal_candidates[0]

        for p in range(3):          # 0,1,2
            for q in range(3):      # 0,1,2
                for seas in seasonal_candidates:
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            m = _SARIMAX(
                                endog,
                                order=(p, d, q),
                                seasonal_order=seas,
                                enforce_stationarity=False,
                                enforce_invertibility=False,
                            )
                            r = m.fit(disp=False, maxiter=50)
                        if r.aic < best_aic:
                            best_aic = r.aic
                            best_order = (p, d, q)
                            best_seasonal = seas
                    except Exception:
                        continue

        return best_order, best_seasonal

    @staticmethod
    def _select_order_heuristic(endog: np.ndarray, n: int) -> tuple[int, int, int]:
        if n < 15:
            return (1, 0, 0)
        try:
            from statsmodels.tsa.stattools import adfuller
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p_value = adfuller(endog, maxlag=min(12, n // 3))[1]
            d = 0 if p_value < 0.05 else 1
        except Exception:
            d = 1
        p = 2 if n >= 40 else 1
        q = 1
        return (p, d, q)

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        if self.model is None:
            raise ForecastingError("SARIMAX model is not fitted.")

        if self.feature_columns:
            if future_frame is None:
                future_frame = _build_future_exog_frame(
                    horizon=horizon,
                    feature_columns=self.feature_columns,
                    last_row=self.last_exog_row,
                )
            exog = self._encode_exog_predict(future_frame, self.feature_columns)
            forecast = self.model.forecast(steps=horizon, exog=exog)
        else:
            forecast = self.model.forecast(steps=horizon)

        return np.asarray(forecast, dtype=float)

    def predict_with_intervals(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
        alpha: float = 0.05,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        """Return native SARIMAX prediction intervals."""
        if self.model is None:
            raise ForecastingError("SARIMAX model is not fitted.")

        exog = None
        if self.feature_columns:
            if future_frame is None:
                future_frame = _build_future_exog_frame(
                    horizon=horizon,
                    feature_columns=self.feature_columns,
                    last_row=self.last_exog_row,
                )
            exog = self._encode_exog_predict(future_frame, self.feature_columns)

        forecast_obj = self.model.get_forecast(steps=horizon, exog=exog)
        pred = np.asarray(forecast_obj.predicted_mean, dtype=float)
        conf = forecast_obj.conf_int(alpha=alpha)
        if hasattr(conf, "iloc"):
            lower = np.asarray(conf.iloc[:, 0], dtype=float)
            upper = np.asarray(conf.iloc[:, 1], dtype=float)
        else:
            lower = np.asarray(conf[:, 0], dtype=float)
            upper = np.asarray(conf[:, 1], dtype=float)
        return pred, lower, upper

    def _encode_exog_train(self, df: pd.DataFrame, cols: list[str]) -> np.ndarray | None:
        if not cols:
            return None
        encoded_cols = []
        for col in cols:
            series = df[col]
            if pd.api.types.is_numeric_dtype(series):
                encoded_cols.append(pd.to_numeric(series, errors="coerce").fillna(0.0).to_numpy())
            else:
                text = series.astype("string").fillna("__missing__")
                categories = list(pd.Index(text.unique()))
                mapping = {str(value): idx for idx, value in enumerate(categories)}
                self.exog_maps[col] = mapping
                encoded = text.map(lambda value: mapping.get(str(value), -1)).astype(float).to_numpy()
                encoded_cols.append(encoded)
        return np.column_stack(encoded_cols)

    def _encode_exog_predict(self, df: pd.DataFrame, cols: list[str]) -> np.ndarray | None:
        if not cols:
            return None
        encoded_cols = []
        for col in cols:
            series = df[col] if col in df.columns else pd.Series([0] * len(df))
            if col in self.exog_maps:
                mapping = self.exog_maps[col]
                text = series.astype("string").fillna("__missing__")
                encoded = text.map(lambda value: mapping.get(str(value), -1)).astype(float).to_numpy()
                encoded_cols.append(encoded)
            else:
                encoded_cols.append(pd.to_numeric(series, errors="coerce").fillna(0.0).to_numpy())
        return np.column_stack(encoded_cols)


def _build_ts_feature_row(
    history: pd.DataFrame,
    current_time: pd.Timestamp,
    current_exog: dict[str, Any],
    *,
    target_column: str,
    feature_columns: list[str],
    season_length: int,
) -> dict:
    """Build a single feature-row dict for tree-based forecasters.

    Shared by CatBoostForecaster and LightGBMForecaster so both use
    identical feature sets and predictions are directly comparable.
    """
    target_history = history[target_column].astype(float)
    n = len(target_history)

    lag_1 = float(target_history.iloc[-1]) if n >= 1 else 0.0
    lag_2 = float(target_history.iloc[-2]) if n >= 2 else 0.0
    lag_3 = float(target_history.iloc[-3]) if n >= 3 else 0.0
    lag_7 = float(target_history.iloc[-7]) if n >= 7 else 0.0
    lag_14 = float(target_history.iloc[-14]) if n >= 14 else 0.0
    lag_21 = float(target_history.iloc[-21]) if n >= 21 else 0.0
    lag_season = float(target_history.iloc[-season_length]) if n >= season_length else 0.0

    diff_1 = lag_1 - lag_2 if n >= 2 else 0.0
    pct_1 = diff_1 / abs(lag_2) if n >= 2 and abs(lag_2) > 1e-9 else 0.0
    pct_7 = (lag_1 - lag_7) / abs(lag_7) if n >= 7 and abs(lag_7) > 1e-9 else 0.0
    pct_14 = (lag_1 - lag_14) / abs(lag_14) if n >= 14 and abs(lag_14) > 1e-9 else 0.0

    tail_3 = target_history.tail(3)
    tail_7 = target_history.tail(7)
    tail_14 = target_history.tail(14)
    roll_3 = float(tail_3.mean()) if n >= 3 else 0.0
    roll_7 = float(tail_7.mean()) if n >= 7 else 0.0
    roll_14 = float(tail_14.mean()) if n >= 14 else 0.0
    roll_30 = float(target_history.tail(30).mean()) if n >= 30 else 0.0
    roll_std_3 = float(tail_3.std()) if n >= 3 else 0.0
    roll_std_7 = float(tail_7.std()) if n >= 7 else 0.0

    ewm_span5 = float(target_history.ewm(span=min(5, n)).mean().iloc[-1]) if n >= 2 else lag_1
    ewm_span12 = float(target_history.ewm(span=min(12, n)).mean().iloc[-1]) if n >= 2 else lag_1
    diff_ewm = ewm_span5 - ewm_span12

    trend_slope = 0.0
    if n >= 7:
        recent = target_history.tail(7).to_numpy()
        x = np.arange(len(recent), dtype=float)
        trend_slope = float(np.polyfit(x, recent, 1)[0])

    row = {
        "lag_1": lag_1, "lag_2": lag_2, "lag_3": lag_3,
        "lag_7": lag_7, "lag_14": lag_14, "lag_21": lag_21,
        "lag_season": lag_season,
        "diff_1": diff_1,
        "pct_change_1": pct_1, "pct_change_7": pct_7, "pct_change_14": pct_14,
        "rolling_mean_3": roll_3, "rolling_mean_7": roll_7,
        "rolling_mean_14": roll_14, "rolling_mean_30": roll_30,
        "rolling_std_3": roll_std_3, "rolling_std_7": roll_std_7,
        "ewm_5": ewm_span5, "ewm_12": ewm_span12, "ewm_diff": diff_ewm,
        "trend_slope_7": trend_slope,
        "month": int(current_time.month),
        "quarter": int(current_time.quarter),
        "weekday": int(current_time.weekday()),
        "is_weekend": int(current_time.weekday() >= 5),
        "is_month_start": int(current_time.is_month_start),
        "is_month_end": int(current_time.is_month_end),
        "day_of_year": int(current_time.dayofyear),
        "sin_yearly": float(np.sin(2 * np.pi * current_time.dayofyear / 365.25)),
        "cos_yearly": float(np.cos(2 * np.pi * current_time.dayofyear / 365.25)),
        "sin_weekly": float(np.sin(2 * np.pi * current_time.weekday() / 7)),
        "cos_weekly": float(np.cos(2 * np.pi * current_time.weekday() / 7)),
    }
    for col in feature_columns:
        row[f"exog__{col}"] = current_exog.get(col)
    return row


def _build_ts_training_matrix(
    train_df: pd.DataFrame,
    *,
    time_column: str,
    target_column: str,
    feature_columns: list[str],
    season_length: int,
) -> tuple[pd.DataFrame, pd.Series, list[str], list[int]]:
    """Build (X, y, feature_names, cat_indices) for tree-based models."""
    min_hist = max(3, min(7, season_length))
    rows, targets = [], []

    for i in range(min_hist, len(train_df)):
        hist = train_df.iloc[:i]
        current = train_df.iloc[i]
        feat = _build_ts_feature_row(
            hist,
            pd.to_datetime(current[time_column]),
            {col: current[col] for col in feature_columns},
            target_column=target_column,
            feature_columns=feature_columns,
            season_length=season_length,
        )
        rows.append(feat)
        targets.append(float(current[target_column]))

    X = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = pd.Series(targets, dtype=float).loc[X.index]
    feature_names = list(X.columns)
    cat_indices = []
    for idx, col in enumerate(feature_names):
        if not pd.api.types.is_numeric_dtype(X[col]):
            X[col] = X[col].astype("string")
            cat_indices.append(idx)
    return X, y, feature_names, cat_indices


class CatBoostForecaster(BaseForecaster):
    """CatBoost gradient-boosted trees with quantile prediction intervals.

    Trains three models on each fit:
      • RMSE model  → point forecast (mean)
      • Quantile 10 → lower bound
      • Quantile 90 → upper bound

    The quantile models use the *median* prediction for autoregressive lag
    features so the intervals stay centred on the point forecast.
    """

    model_name = "catboost"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.model_q10 = None
        self.model_q90 = None
        self.feature_names: list[str] = []
        self.categorical_feature_indices: list[int] = []
        self.training_history: pd.DataFrame | None = None

    def fit(self, train_df: pd.DataFrame):
        try:
            from catboost import CatBoostRegressor
        except Exception as exc:
            raise DependencyUnavailableError(
                "catboost is not installed. Install catboost to enable CatBoost forecasting."
            ) from exc

        X, y, feature_names, cat_indices = _build_ts_training_matrix(
            train_df,
            time_column=self.time_column,
            target_column=self.target_column,
            feature_columns=self.feature_columns,
            season_length=self.season_length,
        )
        if len(X) < 10:
            raise ForecastingError("Not enough rows to train CatBoost after lag feature building.")

        n_samples = len(X)
        iterations = min(500, max(100, n_samples * 5))
        depth = 4 if n_samples < 50 else 6
        lr = 0.08 if n_samples < 50 else 0.05
        l2 = 5.0 if n_samples < 100 else 3.0

        _common = dict(
            iterations=iterations, learning_rate=lr, depth=depth,
            l2_leaf_reg=l2, random_seed=42, verbose=False,
        )

        self.model = CatBoostRegressor(loss_function="RMSE", **_common)
        self.model_q10 = CatBoostRegressor(
            loss_function="Quantile:alpha=0.10",
            iterations=min(300, iterations), **{k: v for k, v in _common.items() if k != "iterations"},
        )
        self.model_q90 = CatBoostRegressor(
            loss_function="Quantile:alpha=0.90",
            iterations=min(300, iterations), **{k: v for k, v in _common.items() if k != "iterations"},
        )

        if n_samples >= 30:
            split_idx = int(n_samples * 0.85)
            X_tr, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
            y_tr, y_val = y.iloc[:split_idx], y.iloc[split_idx:]
            self.model.fit(X_tr, y_tr, cat_features=cat_indices, eval_set=(X_val, y_val),
                           early_stopping_rounds=30)
        else:
            self.model.fit(X, y, cat_features=cat_indices)

        # Quantile models always fit on all data (no early stopping needed)
        self.model_q10.fit(X, y, cat_features=cat_indices)
        self.model_q90.fit(X, y, cat_features=cat_indices)

        self.feature_names = feature_names
        self.categorical_feature_indices = cat_indices
        self.training_history = train_df[
            [self.time_column, self.target_column, *self.feature_columns]
        ].copy()

    def _predict_recursive(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None,
        extra_models: list | None = None,
    ) -> tuple[np.ndarray, ...]:
        """Run the autoregressive prediction loop.

        *extra_models* is a list of additional CatBoost models whose outputs
        are returned alongside the main model's predictions (same shape).
        """
        if self.model is None or self.training_history is None:
            raise ForecastingError("CatBoost model is not fitted.")

        history = self.training_history.copy()
        if future_frame is None:
            future_frame = _build_future_frame(
                train_df=history,
                time_column=self.time_column,
                feature_columns=self.feature_columns,
                frequency=self.frequency,
                horizon=horizon,
            )

        all_preds: list[list[float]] = [[] for _ in range(1 + len(extra_models or []))]

        for i in range(horizon):
            current_time = pd.to_datetime(future_frame.iloc[i][self.time_column])
            current_exog = {
                col: future_frame.iloc[i][col] if col in future_frame.columns else history.iloc[-1][col]
                for col in self.feature_columns
            }
            feat = _build_ts_feature_row(
                history, current_time, current_exog,
                target_column=self.target_column,
                feature_columns=self.feature_columns,
                season_length=self.season_length,
            )
            X_row = pd.DataFrame([feat], columns=self.feature_names)
            for idx in self.categorical_feature_indices:
                X_row[self.feature_names[idx]] = X_row[self.feature_names[idx]].astype("string")

            pred_mid = float(self.model.predict(X_row)[0])
            all_preds[0].append(pred_mid)
            for k, m in enumerate(extra_models or []):
                all_preds[k + 1].append(float(m.predict(X_row)[0]))

            # Append point-forecast to history so lags stay consistent
            row_new = {self.time_column: current_time, self.target_column: pred_mid}
            row_new.update(current_exog)
            history = pd.concat([history, pd.DataFrame([row_new])], ignore_index=True)

        return tuple(np.asarray(p, dtype=float) for p in all_preds)

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        return self._predict_recursive(horizon, future_frame)[0]

    def predict_with_intervals(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
        alpha: float = 0.05,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        if self.model_q10 is None or self.model_q90 is None:
            pred = self.predict(horizon, future_frame)
            return pred, None, None
        pred_mid, pred_lo, pred_hi = self._predict_recursive(
            horizon, future_frame, extra_models=[self.model_q10, self.model_q90]
        )
        return pred_mid, pred_lo, pred_hi

    def get_feature_importance(self, top_n: int = 15) -> list[dict]:
        """Return top-N features by CatBoost PredictionValuesChange importance."""
        if self.model is None or not self.feature_names:
            return []
        try:
            importances = self.model.get_feature_importance()
            indices = np.argsort(importances)[::-1][:top_n]
            total = float(np.sum(importances)) or 1.0
            return [
                {
                    "feature": self.feature_names[i],
                    "importance": float(importances[i]),
                    "importance_pct": round(float(importances[i]) / total * 100, 2),
                }
                for i in indices if importances[i] > 0
            ]
        except Exception:
            return []


# ── LightGBM forecaster ──────────────────────────────────────────────────────

class LightGBMForecaster(BaseForecaster):
    """LightGBM gradient-boosted trees with quantile prediction intervals.

    Uses identical feature engineering to CatBoostForecaster (_build_ts_feature_row)
    so model rankings are directly comparable on the same feature set.

    Trains three objectives:
      • regression_l2  → point forecast
      • quantile 0.10  → lower bound
      • quantile 0.90  → upper bound
    """

    model_name = "lightgbm"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.model_q10 = None
        self.model_q90 = None
        self.feature_names: list[str] = []
        self.training_history: pd.DataFrame | None = None
        self._lgb_label_maps: dict[str, dict] = {}

    def _encode_for_lgb(self, X: pd.DataFrame) -> pd.DataFrame:
        """Label-encode any non-numeric columns so LightGBM receives only float dtypes."""
        X = X.copy()
        for col in X.columns:
            if not pd.api.types.is_numeric_dtype(X[col]):
                mapping = self._lgb_label_maps.get(col, {})
                X[col] = (
                    X[col].astype(str)
                    .map(lambda v, m=mapping: float(m.get(v, -1)))
                )
        return X

    def fit(self, train_df: pd.DataFrame):
        try:
            import lightgbm as lgb
        except Exception as exc:
            raise DependencyUnavailableError(
                "lightgbm is not installed. Install lightgbm to enable LightGBM forecasting."
            ) from exc

        X, y, feature_names, _ = _build_ts_training_matrix(
            train_df,
            time_column=self.time_column,
            target_column=self.target_column,
            feature_columns=self.feature_columns,
            season_length=self.season_length,
        )
        if len(X) < 10:
            raise ForecastingError("Not enough rows to train LightGBM after lag feature building.")

        # Build label maps for any string columns, then encode to float
        self._lgb_label_maps = {}
        for col in X.columns:
            if not pd.api.types.is_numeric_dtype(X[col]):
                codes, uniques = pd.factorize(X[col].astype(str).fillna("__missing__"))
                self._lgb_label_maps[col] = {str(v): float(i) for i, v in enumerate(uniques)}
                X[col] = codes.astype(np.float32)


        n_samples = len(X)
        n_estimators = min(500, max(100, n_samples * 5))
        lr = 0.08 if n_samples < 50 else 0.05
        num_leaves = 15 if n_samples < 50 else 31

        def _train(objective: str, alpha: float | None = None) -> lgb.Booster:
            params: dict = {
                "objective": objective,
                "n_estimators": n_estimators,
                "learning_rate": lr,
                "num_leaves": num_leaves,
                "min_child_samples": max(5, n_samples // 20),
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "verbose": -1,
                "random_state": 42,
            }
            if alpha is not None:
                params["alpha"] = alpha
            ds = lgb.Dataset(X, label=y)
            callbacks = [lgb.log_evaluation(period=-1)]
            if n_samples >= 30:
                split_idx = int(n_samples * 0.85)
                ds_tr = lgb.Dataset(X.iloc[:split_idx], label=y.iloc[:split_idx])
                ds_val = lgb.Dataset(X.iloc[split_idx:], label=y.iloc[split_idx:], reference=ds_tr)
                callbacks.append(lgb.early_stopping(stopping_rounds=30, verbose=False))
                return lgb.train(
                    params, ds_tr, valid_sets=[ds_val],
                    num_boost_round=n_estimators, callbacks=callbacks,
                )
            return lgb.train(params, ds, num_boost_round=n_estimators, callbacks=callbacks)

        self.model = _train("regression_l2")
        self.model_q10 = _train("quantile", alpha=0.10)
        self.model_q90 = _train("quantile", alpha=0.90)

        self.feature_names = feature_names
        self.training_history = train_df[
            [self.time_column, self.target_column, *self.feature_columns]
        ].copy()

    def _predict_recursive(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None,
        extra_models: list | None = None,
    ) -> tuple[np.ndarray, ...]:
        if self.model is None or self.training_history is None:
            raise ForecastingError("LightGBM model is not fitted.")

        history = self.training_history.copy()
        if future_frame is None:
            future_frame = _build_future_frame(
                train_df=history,
                time_column=self.time_column,
                feature_columns=self.feature_columns,
                frequency=self.frequency,
                horizon=horizon,
            )

        all_preds: list[list[float]] = [[] for _ in range(1 + len(extra_models or []))]

        for i in range(horizon):
            current_time = pd.to_datetime(future_frame.iloc[i][self.time_column])
            current_exog = {
                col: future_frame.iloc[i][col] if col in future_frame.columns else history.iloc[-1][col]
                for col in self.feature_columns
            }
            feat = _build_ts_feature_row(
                history, current_time, current_exog,
                target_column=self.target_column,
                feature_columns=self.feature_columns,
                season_length=self.season_length,
            )
            X_row = self._encode_for_lgb(pd.DataFrame([feat], columns=self.feature_names))
            pred_mid = float(self.model.predict(X_row)[0])
            all_preds[0].append(pred_mid)
            for k, m in enumerate(extra_models or []):
                all_preds[k + 1].append(float(m.predict(X_row)[0]))

            row_new = {self.time_column: current_time, self.target_column: pred_mid}
            row_new.update(current_exog)
            history = pd.concat([history, pd.DataFrame([row_new])], ignore_index=True)

        return tuple(np.asarray(p, dtype=float) for p in all_preds)

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        return self._predict_recursive(horizon, future_frame)[0]

    def predict_with_intervals(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
        alpha: float = 0.05,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        if self.model_q10 is None or self.model_q90 is None:
            pred = self.predict(horizon, future_frame)
            return pred, None, None
        pred_mid, pred_lo, pred_hi = self._predict_recursive(
            horizon, future_frame, extra_models=[self.model_q10, self.model_q90]
        )
        return pred_mid, pred_lo, pred_hi

    def get_feature_importance(self, top_n: int = 15) -> list[dict]:
        """Return top-N features by LightGBM gain importance."""
        if self.model is None or not self.feature_names:
            return []
        try:
            importances = self.model.feature_importance(importance_type="gain")
            indices = np.argsort(importances)[::-1][:top_n]
            total = float(np.sum(importances)) or 1.0
            return [
                {
                    "feature": self.feature_names[i],
                    "importance": float(importances[i]),
                    "importance_pct": round(float(importances[i]) / total * 100, 2),
                }
                for i in indices if importances[i] > 0
            ]
        except Exception:
            return []


# ── Prophet forecaster ───────────────────────────────────────────────────────

class ProphetForecaster(BaseForecaster):
    """Facebook Prophet with native uncertainty intervals.

    Handles trend changes, yearly + weekly seasonality, and optional
    numeric exogenous regressors automatically.  Prediction intervals
    come directly from Prophet's built-in posterior sampling.
    """

    model_name = "prophet"

    # Map internal frequency codes to pandas offset aliases Prophet understands
    _PROPHET_FREQ = {"D": "D", "W": "W", "MS": "MS", "QS": "QS"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self._numeric_regressors: list[str] = []
        self._last_train_ds: pd.Timestamp | None = None

    def fit(self, train_df: pd.DataFrame):
        try:
            from prophet import Prophet  # type: ignore
        except Exception as exc:
            raise DependencyUnavailableError(
                "prophet is not installed. Install prophet to enable Prophet forecasting."
            ) from exc

        # Identify numeric regressors first
        num_regs = [
            col for col in self.feature_columns
            if col in train_df.columns and pd.api.types.is_numeric_dtype(train_df[col])
        ]

        # ── Dtype contract ───────────────────────────────────────────────────────
        # pandas 2.x resample/agg returns nullable Float64 (capital-F).
        # Prophet calls np.isnan() on df['y'].values; on a pandas ExtensionArray
        # that raises "ufunc 'isnan' not supported".
        # Fix: build df_p from a plain dict of raw numpy arrays so pandas has
        # no opportunity to infer nullable types.
        ds_raw = pd.to_datetime(train_df[self.time_column], utc=False, errors="coerce")
        if hasattr(ds_raw.dtype, "tz") and ds_raw.dtype.tz is not None:
            ds_raw = ds_raw.dt.tz_localize(None)

        def _to_float64_array(series: pd.Series) -> np.ndarray:
            """Convert any pandas Series (including nullable Float64) to a plain numpy float64 array."""
            return pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)

        raw: dict = {
            "ds": ds_raw.to_numpy(dtype="datetime64[ns]"),
            "y":  _to_float64_array(train_df[self.target_column]),
        }
        for col in num_regs:
            raw[col] = _to_float64_array(train_df[col])

        # Build from dict → guaranteed numpy-backed columns, no ExtensionArray
        df_p = pd.DataFrame(raw)
        df_p = df_p.dropna(subset=["ds", "y"]).reset_index(drop=True)
        self._numeric_regressors = num_regs

        import logging as _logging
        _logging.getLogger("prophet").setLevel(_logging.WARNING)
        _logging.getLogger("cmdstanpy").setLevel(_logging.WARNING)

        m = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=(self.frequency == "D"),
            daily_seasonality=False,
            interval_width=0.80,
            uncertainty_samples=200,
        )
        for col in num_regs:
            m.add_regressor(col)

        try:
            m.fit(df_p)
        except Exception as exc:
            raise ForecastingError(f"Prophet fit failed: {exc}") from exc

        self.model = m
        self._last_train_ds = df_p["ds"].iloc[-1]

    def _make_future(self, horizon: int, future_frame: pd.DataFrame | None) -> pd.DataFrame:
        freq = self._PROPHET_FREQ.get(self.frequency, "D")
        future = self.model.make_future_dataframe(
            periods=horizon, freq=freq, include_history=False
        )
        if future_frame is not None:
            future = future.reset_index(drop=True)
            for col in self._numeric_regressors:
                if col in future_frame.columns:
                    future[col] = future_frame[col].values[:horizon]
                else:
                    future[col] = 0.0
        else:
            for col in self._numeric_regressors:
                future[col] = 0.0
        return future

    def predict(self, horizon: int, future_frame: pd.DataFrame | None = None) -> np.ndarray:
        if self.model is None:
            raise ForecastingError("Prophet model is not fitted.")
        future = self._make_future(horizon, future_frame)
        fc = self.model.predict(future)
        return np.asarray(fc["yhat"].to_numpy(), dtype=float)

    def predict_with_intervals(
        self,
        horizon: int,
        future_frame: pd.DataFrame | None = None,
        alpha: float = 0.05,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        if self.model is None:
            raise ForecastingError("Prophet model is not fitted.")
        future = self._make_future(horizon, future_frame)
        fc = self.model.predict(future)
        return (
            np.asarray(fc["yhat"].to_numpy(), dtype=float),
            np.asarray(fc["yhat_lower"].to_numpy(), dtype=float),
            np.asarray(fc["yhat_upper"].to_numpy(), dtype=float),
        )


def run_forecast_service(
    df: pd.DataFrame,
    *,
    time_column: str,
    target_column: str,
    id_columns: list[str] | None = None,
    feature_columns: list[str] | None = None,
    frequency: str | None = None,
    horizon: int = DEFAULT_HORIZON,
    candidate_models: list[str] | None = None,
    season_length: int | None = None,
    missing_periods_policy: str = DEFAULT_MISSING_PERIODS_POLICY,
    mode: str = DEFAULT_FORECAST_MODE,
) -> dict:
    """
    Unified forecasting entry point.

    mode:
      "fast"     — aggressive point cap (MAX_SERIES_POINTS_FAST) + cheap models only
                   (SLOW_MODELS skipped). Finishes in seconds; runs synchronously.
      "accurate" — higher point cap (MAX_SERIES_POINTS_ACCURATE) + all models. Slower;
                   intended to run async on a Celery worker.
    """
    t_start = time.perf_counter()

    mode = mode if mode in FORECAST_MODES else DEFAULT_FORECAST_MODE
    max_points = MAX_SERIES_POINTS_FAST if mode == "fast" else MAX_SERIES_POINTS_ACCURATE

    id_columns = list(id_columns or [])
    feature_columns = [c for c in (feature_columns or []) if c and c != target_column and c != time_column]
    candidate_models = candidate_models or list(DEFAULT_MODEL_ORDER)

    # Fail loud on unknown model ids instead of silently skipping them later.
    unknown_models = [m for m in candidate_models if m not in SUPPORTED_MODELS]
    if unknown_models:
        raise ForecastingError(
            f"Unknown model(s): {sorted(set(unknown_models))}. "
            f"Supported: {sorted(SUPPORTED_MODELS)}"
        )

    # Gate slow models by mode. fast: drop SLOW_MODELS (sarimax + prophet). accurate:
    # drop only ACCURATE_SKIP_MODELS (sarimax — slow and usually times out). Keep at
    # least one model if a caller passed only skipped ones.
    drop_models = SLOW_MODELS if mode == "fast" else ACCURATE_SKIP_MODELS
    gated = [m for m in candidate_models if m not in drop_models]
    candidate_models = gated or ["ets"]

    horizon = int(max(1, min(int(horizon), MAX_HORIZON)))
    missing_periods_policy = _normalize_missing_periods_policy(missing_periods_policy)

    logger.info(
        "Forecast requested: target=%s, time=%s, features=%s, freq=%s, horizon=%d, "
        "models=%s, input_rows=%d",
        target_column, time_column, feature_columns, frequency, horizon,
        candidate_models, len(df),
    )

    readiness = _run_readiness_checks(
        df=df,
        time_column=time_column,
        target_column=target_column,
        feature_columns=feature_columns,
    )
    if not readiness["forecast_possible"]:
        logger.warning("Readiness check failed: %s", readiness["reasons"])
        return {
            "forecast_possible": False,
            "readiness": readiness,
            "target": target_column,
            "frequency": frequency,
            "horizon": horizon,
            "missing_periods_policy": missing_periods_policy,
            "candidate_models": candidate_models,
            "model_results": [],
            "best_model": None,
            "metrics": {},
            "forecast": [],
            "prediction_intervals": [],
            "duration_ms": int((time.perf_counter() - t_start) * 1000),
        }

    prepared = _prepare_series_frame(
        df=df,
        time_column=time_column,
        target_column=target_column,
        feature_columns=feature_columns,
        frequency=frequency,
        season_length=season_length,
        missing_periods_policy=missing_periods_policy,
        max_points=max_points,
    )

    original_target = prepared.frame[prepared.target_column].astype(float)
    if prepared.log_transformed:
        original_target = _invert_log_transform(original_target.to_numpy())
    target_min = float(np.min(original_target))
    non_negative_target = target_min >= 0.0

    logger.info(
        "Series prepared: rows=%d, freq=%s, season=%d, log_transform=%s, non_negative=%s",
        len(prepared.frame), prepared.frequency, prepared.season_length,
        prepared.log_transformed, non_negative_target,
    )

    total_rows = len(prepared.frame)
    eligible_models: list[str] = []
    skipped_models: list[dict] = []
    for model_name in candidate_models:
        required = _get_model_min_history(model_name, prepared.season_length) + horizon
        if total_rows >= required:
            eligible_models.append(model_name)
        else:
            skipped_models.append({
                "model": model_name,
                "reason": f"needs {required} rows (min_history={required - horizon} + horizon={horizon}), got {total_rows}",
            })

    if skipped_models:
        logger.info("Skipped models: %s", [s["model"] for s in skipped_models])
    logger.info("Eligible models: %s", eligible_models)

    if not eligible_models:
        logger.warning("No eligible models for %d rows + horizon %d", total_rows, horizon)
        elapsed = int((time.perf_counter() - t_start) * 1000)
        return {
            "forecast_possible": False,
            "readiness": {
                "forecast_possible": False,
                "reasons": [
                    f"not_enough_rows_for_any_model: got {total_rows}, "
                    f"cheapest model needs {_get_model_min_history('naive', prepared.season_length) + horizon}",
                ],
            },
            "target": target_column,
            "frequency": prepared.frequency,
            "horizon": horizon,
            "missing_periods_policy": missing_periods_policy,
            "candidate_models": candidate_models,
            "skipped_models": skipped_models,
            "model_results": [],
            "best_model": None,
            "metrics": {},
            "forecast": [],
            "prediction_intervals": [],
            "duration_ms": elapsed,
        }

    t_eval = time.perf_counter()

    # ── Model selection ──────────────────────────────────────────────────────
    # Two-stage tournament when there are enough candidates to prune: screen all on
    # 1 fold, then full CV + holdout only on the top survivors (+ always-kept baselines).
    # Otherwise run the single-stage path on every eligible model.
    if len(eligible_models) > TOURNAMENT_MIN_CANDIDATES:
        stage1 = _evaluate_candidates(
            prepared=prepared,
            horizon=horizon,
            candidate_models=eligible_models,
            non_negative_target=non_negative_target,
            folds_override=TOURNAMENT_STAGE1_FOLDS,
        )
        ranked = sorted(
            [r for r in stage1 if r["status"] == "ok"],
            key=lambda r: r["metrics"]["wape"],
        )
        # Keep the top-K real CONTENDERS (excluding the always-kept cheap baselines, so
        # baselines don't consume contender slots), then add the baselines back. This
        # guarantees KEEP_TOP genuine models survive even if a baseline ranks high on the
        # noisy single screen fold.
        survivors = [
            r["model"] for r in ranked
            if r["model"] not in TOURNAMENT_ALWAYS_KEEP
        ][:TOURNAMENT_KEEP_TOP]
        for b in TOURNAMENT_ALWAYS_KEEP:
            if b in eligible_models and b not in survivors:
                survivors.append(b)
        eliminated = [m for m in eligible_models if m not in survivors]
        logger.info(
            "Tournament: %d candidates -> survivors=%s eliminated=%s",
            len(eligible_models), survivors, eliminated,
        )

        model_results = _evaluate_candidates(
            prepared=prepared,
            horizon=horizon,
            candidate_models=survivors,
            non_negative_target=non_negative_target,
        )
        holdout_test = _evaluate_holdout_test(
            prepared=prepared,
            horizon=horizon,
            candidate_models=survivors,
            non_negative_target=non_negative_target,
        )
        # Surface eliminated models (with their 1-fold screen metrics) as informational
        # rows — they never enter selection but the UI can show they were considered.
        by_name = {r["model"]: r for r in stage1}
        for m in eliminated:
            r = by_name.get(m, {"model": m, "metrics": {}, "folds": 1})
            model_results.append({**r, "status": "eliminated", "eliminated_stage": 1})
    else:
        model_results = _evaluate_candidates(
            prepared=prepared,
            horizon=horizon,
            candidate_models=eligible_models,
            non_negative_target=non_negative_target,
        )
        holdout_test = _evaluate_holdout_test(
            prepared=prepared,
            horizon=horizon,
            candidate_models=eligible_models,
            non_negative_target=non_negative_target,
        )

    eval_ms = int((time.perf_counter() - t_eval) * 1000)

    # Attach test_metrics to each model result (None if holdout failed)
    for mr in model_results:
        mr["test_metrics"] = holdout_test.get(mr["model"])

    for mr in model_results:
        if mr["status"] == "ok":
            tm = mr.get("test_metrics") or {}
            logger.info(
                "  Model %-20s OK   CV_MAE=%.4f  Test_MAE=%.4f  WAPE=%.4f  folds=%d",
                mr["model"], mr["metrics"]["mae"],
                tm.get("mae", float("nan")),
                mr["metrics"]["wape"], mr["folds"],
            )
        elif mr["status"] == "eliminated":
            _wape = (mr.get("metrics") or {}).get("wape")
            logger.info(
                "  Model %-20s ELIM (stage1 wape=%s)",
                mr["model"], f"{_wape:.4f}" if isinstance(_wape, (int, float)) else "n/a",
            )
        else:
            logger.warning(
                "  Model %-20s FAIL folds=%d  error=%s",
                mr["model"], mr["folds"], mr.get("error", "?"),
            )

    successful = [r for r in model_results if r.get("status") == "ok"]
    if not successful:
        elapsed = int((time.perf_counter() - t_start) * 1000)
        logger.warning("All models failed. eval_ms=%d, total_ms=%d", eval_ms, elapsed)
        return {
            "forecast_possible": True,
            "readiness": readiness,
            "target": target_column,
            "frequency": prepared.frequency,
            "horizon": horizon,
            "missing_periods_policy": missing_periods_policy,
            "candidate_models": candidate_models,
            "skipped_models": skipped_models,
            "model_results": model_results,
            "best_model": None,
            "metrics": {},
            "forecast": [],
            "prediction_intervals": [],
            "message": "No candidate model succeeded. Check dependency installation and input quality.",
            "duration_ms": elapsed,
        }

    best = _select_best_model(successful, primary_metric=PRIMARY_SELECTION_METRIC)
    best_model_name = best["model"]
    best_metrics = best["metrics"]
    best_by_metric = _extract_best_models_by_metric(successful)
    baseline_delta = _compute_baseline_delta(
        successful=successful,
        best=best,
        metric=PRIMARY_SELECTION_METRIC,
        baseline_model_name="naive",
    )

    ensemble_partner = _find_ensemble_partner(successful, best, primary_metric=PRIMARY_SELECTION_METRIC)
    use_ensemble = ensemble_partner is not None

    if use_ensemble:
        ensemble_label = f"{best_model_name}+{ensemble_partner['model']}"
        logger.info(
            "Ensemble selected: %s (avg rank winner + runner-up within 25%%)",
            ensemble_label,
        )
    else:
        ensemble_label = best_model_name

    logger.info(
        "Best model by avg-rank: %s (MAE=%.4f, RMSE=%.4f, WAPE=%.4f, MASE=%.4f)",
        ensemble_label,
        best_metrics["mae"], best_metrics["rmse"], best_metrics["wape"], best_metrics["mase"],
    )

    build_kwargs = dict(
        time_column=prepared.time_column,
        target_column=prepared.target_column,
        feature_columns=prepared.feature_columns,
        frequency=prepared.frequency,
        season_length=prepared.season_length,
    )

    best_model = _build_model(model_name=best_model_name, **build_kwargs)
    best_model.fit(prepared.frame)

    # Feature importance (tree models only)
    feature_importance: list[dict] = []
    if hasattr(best_model, "get_feature_importance"):
        feature_importance = best_model.get_feature_importance(top_n=15)

    future_frame = _build_future_frame(
        train_df=prepared.frame,
        time_column=prepared.time_column,
        feature_columns=prepared.feature_columns,
        frequency=prepared.frequency,
        horizon=horizon,
    )

    future_pred, native_lower, native_upper = best_model.predict_with_intervals(
        horizon=horizon, future_frame=future_frame,
    )

    if use_ensemble:
        partner_model = _build_model(model_name=ensemble_partner["model"], **build_kwargs)
        partner_model.fit(prepared.frame)
        partner_pred = partner_model.predict(horizon=horizon, future_frame=future_frame)
        future_pred = (future_pred + partner_pred) / 2.0
        native_lower = None
        native_upper = None
        best_model_name = ensemble_label

    prediction_dates = future_frame[prepared.time_column].tolist()

    if prepared.log_transformed:
        future_pred = _invert_log_transform(future_pred)
        if native_lower is not None:
            native_lower = _invert_log_transform(native_lower)
            native_upper = _invert_log_transform(native_upper)

    if native_lower is not None and native_upper is not None:
        pred_lower = native_lower
        pred_upper = native_upper
    else:
        best_residuals = best.get("residuals", [])
        interval_lo, interval_hi = _build_empirical_intervals(
            best_residuals, horizon, coverage=INTERVAL_COVERAGE
        )
        pred_lower = future_pred + interval_lo
        pred_upper = future_pred + interval_hi

    future_pred = _apply_prediction_constraints(future_pred, non_negative_target=non_negative_target)

    clamp_lower = non_negative_target or bool(np.all(future_pred >= 0))

    forecast = []
    prediction_intervals = []
    non_bracketing_count = 0
    for i, (dt, value) in enumerate(zip(prediction_dates, future_pred)):
        value_f = float(value)
        date_str = pd.to_datetime(dt).date().isoformat()
        lo = float(pred_lower[i])
        hi = float(pred_upper[i])
        if clamp_lower:
            lo = max(0.0, lo)
        if hi < lo:
            hi = lo
        naturally_brackets = lo <= value_f <= hi
        if not naturally_brackets:
            non_bracketing_count += 1
            lo = min(lo, value_f)
            hi = max(hi, value_f)
        forecast.append({"date": date_str, "value": value_f})
        prediction_intervals.append(
            {"date": date_str, "lower": lo, "upper": hi}
        )

    quality_warnings = _detect_forecast_quality_warnings(
        forecast_values=future_pred,
        prediction_intervals=prediction_intervals,
    )
    if non_bracketing_count > 0:
        quality_warnings.append(
            f"{non_bracketing_count} interval(s) were adjusted to bracket the point forecast."
        )

    # Annotate each model with overfit/underfit diagnosis
    for mr in model_results:
        cv_mae  = (mr.get("metrics") or {}).get("mae")
        test_mae = (mr.get("test_metrics") or {}).get("mae")
        if mr.get("status") != "ok" or cv_mae is None or test_mae is None or cv_mae == 0:
            mr["fit_diagnosis"] = None
            mr["fit_ratio"] = None
            continue
        ratio = test_mae / cv_mae
        mr["fit_ratio"] = round(ratio, 3)
        if ratio < 0.70:
            mr["fit_diagnosis"] = "check_leakage"   # suspiciously good on test
        elif ratio <= 1.20:
            mr["fit_diagnosis"] = "healthy"
        elif ratio <= 1.50:
            mr["fit_diagnosis"] = "mild_overfit"
        elif ratio <= 2.00:
            mr["fit_diagnosis"] = "overfit"
        else:
            mr["fit_diagnosis"] = "severe_overfit"

    # Strip internal-only keys from model results sent to frontend
    _strip = {"residuals", "dates", "actuals", "predictions"}
    clean_model_results = [{k: v for k, v in mr.items() if k not in _strip} for mr in model_results]

    # Build test comparison from the winning model's holdout data
    best_holdout = holdout_test.get(best_model_name.split("+")[0]) or {}
    test_comparison = []
    if best_holdout.get("dates"):
        for date, actual, predicted in zip(
            best_holdout["dates"],
            best_holdout["actuals"],
            best_holdout["predictions"],
        ):
            test_comparison.append({"date": date, "actual": actual, "predicted": predicted})

    confidence, confidence_reason = _classify_confidence(
        baseline_delta=baseline_delta, selection_metric=PRIMARY_SELECTION_METRIC
    )

    # Historical tail for frontend charting
    history_tail = prepared.frame.tail(min(HISTORY_TAIL_FOR_CHART, len(prepared.frame)))
    if prepared.log_transformed:
        historical = [
            {
                "date": pd.to_datetime(row[prepared.time_column]).date().isoformat(),
                "value": float(_invert_log_transform(np.array([row[prepared.target_column]]))[0]),
            }
            for _, row in history_tail.iterrows()
        ]
    else:
        historical = [
            {
                "date": pd.to_datetime(row[prepared.time_column]).date().isoformat(),
                "value": float(row[prepared.target_column]),
            }
            for _, row in history_tail.iterrows()
        ]

    elapsed = int((time.perf_counter() - t_start) * 1000)
    logger.info(
        "Forecast complete: best=%s, metric=%s=%.4f, confidence=%s, points=%d, "
        "eval_ms=%d, total_ms=%d",
        best_model_name,
        PRIMARY_SELECTION_METRIC.upper(),
        float(best_metrics.get(PRIMARY_SELECTION_METRIC, float("nan"))),
        confidence,
        len(forecast),
        eval_ms,
        elapsed,
    )

    # ── Series grain ──────────────────────────────────────────────────────
    # Every column that is NOT the time/target/feature is a dimension that got
    # collapsed by the per-period resample (summed). Surface it so the caller/UI
    # MUST know what the forecasted number actually represents (e.g. company-wide
    # total vs per-store), instead of silently aggregating across all entities.
    collapsed_dimensions = [
        c for c in df.columns
        if c not in {time_column, target_column, *feature_columns}
    ]
    series_grain = {
        "time_column": time_column,
        "target_column": target_column,
        "aggregation": "sum",
        "collapsed_dimensions": collapsed_dimensions,
        "is_aggregated": len(df) > len(prepared.frame),
        "input_rows": len(df),
        "series_points": len(prepared.frame),
        "mode": mode,
        "max_points": max_points,
    }

    return {
        "forecast_possible": True,
        "readiness": readiness,
        "target": target_column,
        "mode": mode,
        "frequency": prepared.frequency,
        "frequency_auto_detected": frequency is None,
        "series_grain": series_grain,
        "horizon": horizon,
        "missing_periods_policy": missing_periods_policy,
        "candidate_models": candidate_models,
        "skipped_models": skipped_models,
        "model_results": clean_model_results,
        "best_model": best_model_name,
        "best_model_selection_metric": "avg_rank",
        "ensemble": use_ensemble,
        "best_models_by_metric": best_by_metric,
        "baseline_comparison": baseline_delta,
        "metrics": best_metrics,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "warnings": quality_warnings,
        "forecast": forecast,
        "prediction_intervals": prediction_intervals,
        "historical": historical,
        "training_rows": len(prepared.frame),
        "test_split_ratio": TEST_SPLIT_RATIO,
        "anomalies": prepared.anomalies,
        "feature_importance": feature_importance,
        "test_comparison": test_comparison,
        "duration_ms": elapsed,
    }


def _run_readiness_checks(
    *,
    df: pd.DataFrame,
    time_column: str,
    target_column: str,
    feature_columns: list[str],
) -> dict:
    reasons = []

    if not isinstance(df, pd.DataFrame) or df.empty:
        reasons.append("empty_dataframe")
        return {"forecast_possible": False, "reasons": reasons}

    if time_column not in df.columns:
        reasons.append(f"time_column_not_found:{time_column}")
    if target_column not in df.columns:
        reasons.append(f"target_column_not_found:{target_column}")

    missing_features = [col for col in feature_columns if col not in df.columns]
    if missing_features:
        reasons.append(f"missing_feature_columns:{','.join(missing_features)}")

    if reasons:
        return {"forecast_possible": False, "reasons": reasons}

    parsed_time = pd.to_datetime(df[time_column], errors="coerce")
    valid_time_ratio = float(parsed_time.notna().mean()) if len(df) else 0.0
    if valid_time_ratio < 0.8:
        reasons.append("time_column_not_parseable_enough")

    target_numeric = pd.to_numeric(df[target_column], errors="coerce")
    valid_target_ratio = float(target_numeric.notna().mean()) if len(df) else 0.0
    if valid_target_ratio < 0.6:
        reasons.append("target_not_numeric_enough")

    ready_rows = int((parsed_time.notna() & target_numeric.notna()).sum())
    if ready_rows < MIN_HISTORY_ROWS:
        reasons.append(f"insufficient_history_rows:{ready_rows}")

    return {"forecast_possible": len(reasons) == 0, "reasons": reasons}


def _prepare_series_frame(
    *,
    df: pd.DataFrame,
    time_column: str,
    target_column: str,
    feature_columns: list[str],
    frequency: str | None,
    season_length: int | None,
    missing_periods_policy: str,
    max_points: int | None = None,
) -> PreparedSeries:
    frame = df[[time_column, target_column, *feature_columns]].copy()
    frame[time_column] = pd.to_datetime(frame[time_column], errors="coerce")
    frame[target_column] = pd.to_numeric(frame[target_column], errors="coerce")
    frame = frame.dropna(subset=[time_column, target_column]).sort_values(time_column)
    frame["__obs_count"] = 1.0

    forced = _normalize_frequency(frequency) is not None
    freq = _normalize_frequency(frequency) or _detect_frequency(frame[time_column])
    if freq is None:
        freq = "D"

    # Auto-downsample very long DAILY histories to WEEKLY. Only when frequency was
    # auto-detected (caller did not force one). Avoids O(n^2) heavy-model fits and a
    # huge backtest on series like 400k-row multi-entity uploads (~8000 daily points).
    if freq == "D" and not forced:
        span_days = (frame[time_column].max() - frame[time_column].min()).days
        if span_days > MAX_DAILY_POINTS_BEFORE_WEEKLY:
            logger.info(
                "Long daily history (%d days) -> downsampling to weekly", span_days
            )
            freq = "W"

    agg = {target_column: "sum", "__obs_count": "sum"}
    for col in feature_columns:
        if pd.api.types.is_numeric_dtype(frame[col]):
            agg[col] = "mean"
        else:
            agg[col] = _mode_or_last

    base = frame.set_index(time_column)

    def _resample_at(f: str) -> pd.DataFrame:
        return base.resample(f).agg(agg).reset_index()

    frame = _resample_at(freq)

    # Frequency-agnostic point cap: forecast cost scales with the number of points,
    # not input rows. If the series exceeds the cap, coarsen the frequency one step at
    # a time (D->W->MS->QS) and re-aggregate; if the caller forced a frequency we keep
    # it and instead truncate to the most-recent N points as a last resort.
    if max_points and len(frame) > max_points:
        if not forced:
            while len(frame) > max_points and _COARSER_FREQ.get(freq):
                freq = _COARSER_FREQ[freq]
                frame = _resample_at(freq)
            logger.info(
                "Series point cap (%d): coarsened to freq=%s -> %d points",
                max_points, freq, len(frame),
            )
        if len(frame) > max_points:
            frame = frame.tail(max_points).reset_index(drop=True)
            logger.info(
                "Series point cap (%d): truncated to most-recent %d points",
                max_points, len(frame),
            )
    # pandas 2.x resample/agg produces nullable Float64 (ExtensionType).
    # Downcast all numeric columns to regular numpy float64 to avoid downstream
    # "ufunc 'isnan' not supported" errors (Prophet, etc.).
    for _col in frame.select_dtypes(include="number").columns:
        frame[_col] = frame[_col].to_numpy(dtype=np.float64, na_value=np.nan)

    frame["__obs_count"] = pd.to_numeric(frame["__obs_count"], errors="coerce").fillna(0.0)
    if missing_periods_policy == "drop":
        frame = frame.loc[frame["__obs_count"] > 0].copy()
    else:  # missing_periods_policy == "zero"
        frame.loc[frame["__obs_count"] <= 0, target_column] = 0.0

    frame = frame.drop(columns=["__obs_count"], errors="ignore")
    frame[target_column] = pd.to_numeric(frame[target_column], errors="coerce")
    frame = frame.dropna(subset=[target_column])

    # ── Outlier capping — capture anomalies before & after ──
    pre_cap = frame[[time_column, target_column]].copy()
    frame[target_column] = _cap_outliers_iqr(frame[target_column])
    frame = _cap_outliers(frame, target_column)
    anomalies = _detect_capped_anomalies(
        original=pre_cap,
        capped=frame,
        time_column=time_column,
        target_column=target_column,
    )

    for col in feature_columns:
        if col not in frame.columns:
            continue
        if pd.api.types.is_numeric_dtype(frame[col]):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").ffill().bfill().fillna(0.0)
        else:
            frame[col] = frame[col].astype("string").ffill().bfill().fillna("Unknown")

    if frame.empty:
        raise ForecastingError("No rows remain after frequency alignment.")

    if anomalies:
        logger.info(
            "Outlier capping applied: %d point(s) adjusted", len(anomalies)
        )

    use_log = _should_log_transform(frame[target_column])
    if use_log:
        logger.info("Log-transform applied (multiplicative pattern detected)")
        frame[target_column] = _apply_log_transform(frame[target_column].to_numpy())

    chosen_season = int(season_length) if season_length else FREQUENCY_TO_SEASON_LENGTH.get(freq, 1)
    return PreparedSeries(
        frame=frame,
        time_column=time_column,
        target_column=target_column,
        feature_columns=feature_columns,
        frequency=freq,
        season_length=max(1, chosen_season),
        log_transformed=use_log,
        anomalies=anomalies,
    )


def _cap_outliers_iqr(series: pd.Series, multiplier: float = 3.0) -> pd.Series:
    """Clip extreme outliers using the IQR method."""
    q1 = series.quantile(0.25)
    q3 = series.quantile(0.75)
    iqr = q3 - q1
    if iqr < 1e-9:
        return series
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    return series.clip(lower=lower, upper=upper)


def _detect_frequency(time_series: pd.Series) -> str | None:
    if time_series.empty:
        return None

    unique_sorted = pd.Series(pd.to_datetime(time_series).dropna().sort_values().unique())
    if len(unique_sorted) < 3:
        return "D"

    inferred = pd.infer_freq(unique_sorted)
    norm = _normalize_frequency(inferred)
    if norm:
        return norm

    deltas = unique_sorted.diff().dropna().dt.total_seconds() / (24 * 3600)
    if deltas.empty:
        return "D"
    median_days = float(deltas.median())
    if median_days <= 1.5:
        return "D"
    if median_days <= 8:
        return "W"
    if median_days <= 31:
        return "MS"
    return "QS"


def _normalize_frequency(freq: str | None) -> str | None:
    if not freq:
        return None
    text = str(freq).strip().upper()
    if text.startswith("D"):
        return "D"
    if text.startswith("W"):
        return "W"
    if text.startswith("Q"):
        return "QS"
    if text.startswith("M"):
        return "MS"
    return None


def _mode_or_last(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return "Unknown"
    mode_values = values.mode()
    if not mode_values.empty:
        return mode_values.iloc[0]
    return values.iloc[-1]


def _normalize_missing_periods_policy(policy: str | None) -> str:
    text = str(policy or DEFAULT_MISSING_PERIODS_POLICY).strip().lower()
    if text in {"drop", "zero"}:
        return text
    raise ForecastingError(
        f"Unsupported missing_periods_policy: {policy}. Use 'drop' or 'zero'."
    )


def _build_empirical_intervals(
    residuals: list[np.ndarray],
    horizon: int,
    coverage: float = 0.80,
) -> tuple[np.ndarray, np.ndarray]:
    """Build symmetric prediction-interval half-widths using robust MAD estimator.

    Uses Median Absolute Deviation for robustness against skewed/sparse
    backtest residuals, and widens intervals with horizon step to reflect
    growing uncertainty in recursive forecasting.

    Returns (lower_residual, upper_residual), each length *horizon*.
    """
    _COVERAGE_Z = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960}
    z = _COVERAGE_Z.get(round(coverage, 2), 1.282)
    _MAD_TO_STD = 1.4826
    _HORIZON_POWER = 0.20

    max_len = max((len(r) for r in residuals), default=0)
    if max_len == 0:
        ones = np.ones(horizon, dtype=float)
        return (-ones, ones)

    all_resid = np.concatenate(residuals)

    clip_lo_q, clip_hi_q = INTERVAL_RESIDUAL_CLIP_QUANTILES
    clip_lo = float(np.quantile(all_resid, clip_lo_q))
    clip_hi = float(np.quantile(all_resid, clip_hi_q))
    all_resid = np.clip(all_resid, clip_lo, clip_hi)

    median_resid = float(np.median(all_resid))
    mad = float(np.median(np.abs(all_resid - median_resid)))
    robust_std = max(mad * _MAD_TO_STD, 1e-9)

    steps = np.arange(1, horizon + 1, dtype=float)
    scale = np.power(steps, _HORIZON_POWER)

    hw = z * robust_std * scale
    lower = -hw
    upper = hw

    return lower, upper


def _cap_outliers(frame: pd.DataFrame, target_column: str, iqr_factor: float = 3.0) -> pd.DataFrame:
    """Winsorize extreme outliers using the IQR method to prevent model distortion."""
    vals = frame[target_column].astype(float)
    q1 = float(vals.quantile(0.25))
    q3 = float(vals.quantile(0.75))
    iqr = q3 - q1
    if iqr < 1e-9:
        return frame
    lower_fence = q1 - iqr_factor * iqr
    upper_fence = q3 + iqr_factor * iqr
    frame = frame.copy()
    frame[target_column] = vals.clip(lower=lower_fence, upper=upper_fence)
    return frame


def _apply_prediction_constraints(pred: np.ndarray, *, non_negative_target: bool) -> np.ndarray:
    out = np.asarray(pred, dtype=float)
    if non_negative_target:
        out = np.maximum(0.0, out)
    return out


def _detect_capped_anomalies(
    original: pd.DataFrame,
    capped: pd.DataFrame,
    time_column: str,
    target_column: str,
    threshold: float = 1e-6,
) -> list[dict]:
    """Compare original vs capped target values; return rows that were changed.

    Each anomaly record carries the date, the raw value, and the
    capped value so the frontend can overlay them on the historical chart.
    """
    anomalies: list[dict] = []
    for idx in capped.index:
        if idx not in original.index:
            continue
        orig = float(original.loc[idx, target_column])
        cap = float(capped.loc[idx, target_column])
        if abs(orig - cap) > threshold:
            date_val = capped.loc[idx, time_column]
            anomalies.append(
                {
                    "date": pd.to_datetime(date_val).date().isoformat(),
                    "original_value": round(orig, 4),
                    "capped_value": round(cap, 4),
                    "direction": "upper" if orig > cap else "lower",
                }
            )
    return anomalies


def _evaluate_candidates(
    *,
    prepared: PreparedSeries,
    horizon: int,
    candidate_models: list[str],
    non_negative_target: bool,
    folds_override: int | None = None,
) -> list[dict]:
    frame = prepared.frame

    results = []
    for model_name in candidate_models:
        model_min = _get_model_min_history(model_name, prepared.season_length)
        backtest_horizon, folds = _plan_backtest(
            total_rows=len(frame),
            requested_horizon=horizon,
            min_history=model_min,
        )
        # Stage-1 tournament screen caps folds (cheap single-fold ranking).
        if folds_override is not None and folds > 0:
            folds = max(1, min(folds, folds_override))
        if folds <= 0:
            results.append({
                "model": model_name,
                "status": "failed",
                "metrics": {},
                "folds": 0,
                "backtest_horizon": 0,
                "error": "cannot_plan_backtest_with_available_history",
            })
            continue

        fold_metrics = []
        all_residuals: list[np.ndarray] = []
        failed_error = None

        for split in _iter_rolling_splits(frame=frame, horizon=backtest_horizon, folds=folds):
            train_df, test_df = split
            if len(train_df) < model_min:
                failed_error = "insufficient_train_rows_in_backtest"
                break

            # Cap training rows for slow models on large series.
            # Recent data is more representative; capping avoids O(n^2) fit time.
            _cap = CV_TRAIN_CAP.get(model_name)
            if _cap and len(train_df) > _cap:
                train_df = train_df.iloc[-_cap:].copy()

            try:
                model = _build_model(
                    model_name=model_name,
                    time_column=prepared.time_column,
                    target_column=prepared.target_column,
                    feature_columns=prepared.feature_columns,
                    frequency=prepared.frequency,
                    season_length=prepared.season_length,
                )
                model.fit(train_df)

                future_frame = test_df[[prepared.time_column, *prepared.feature_columns]].copy()
                pred = model.predict(horizon=len(test_df), future_frame=future_frame)
                y_true = test_df[prepared.target_column].astype(float).to_numpy()

                if prepared.log_transformed:
                    pred = _invert_log_transform(pred)
                    y_true = _invert_log_transform(y_true)

                pred = _apply_prediction_constraints(pred, non_negative_target=non_negative_target)

                train_target = train_df[prepared.target_column].astype(float).to_numpy()
                if prepared.log_transformed:
                    train_target = _invert_log_transform(train_target)
                naive_mae = _compute_naive_mae(train_target)

                metrics = _compute_metrics(
                    y_true=y_true, y_pred=pred, naive_mae=naive_mae,
                )
                fold_metrics.append(metrics)
                all_residuals.append(y_true - pred)
            except Exception as exc:
                failed_error = str(exc)
                break

        if fold_metrics and failed_error is None:
            agg = {
                "mae": float(np.mean([m["mae"] for m in fold_metrics])),
                "rmse": float(np.mean([m["rmse"] for m in fold_metrics])),
                "wape": float(np.mean([m["wape"] for m in fold_metrics])),
                "mase": float(np.mean([m["mase"] for m in fold_metrics])),
            }
            if not _metrics_are_sane(agg):
                results.append(
                    {
                        "model": model_name,
                        "status": "failed",
                        "metrics": agg,
                        "folds": len(fold_metrics),
                        "backtest_horizon": backtest_horizon,
                        "error": "divergent_predictions_detected",
                    }
                )
            else:
                results.append(
                    {
                        "model": model_name,
                        "status": "ok",
                        "metrics": agg,
                        "folds": len(fold_metrics),
                        "backtest_horizon": backtest_horizon,
                        "residuals": all_residuals,
                    }
                )
        else:
            results.append(
                {
                    "model": model_name,
                    "status": "failed",
                    "metrics": {},
                    "folds": len(fold_metrics),
                    "backtest_horizon": backtest_horizon,
                    "error": failed_error or "unknown_error",
                }
            )

    return results


def _adaptive_folds(total_points: int) -> int:
    """Cross-validation folds scaled to series length.

    More folds add little accuracy on long series but multiply runtime, so we taper:
    short series get more folds (each is cheap + selection is noisier), long series
    fewer. Accuracy barely changes; runtime drops on the expensive large-series case.
    """
    if total_points < 150:
        return 5
    if total_points <= 600:
        return 3
    if total_points <= 1200:
        return 2
    return 1


def _plan_backtest(
    total_rows: int,
    requested_horizon: int,
    min_history: int = MIN_HISTORY_ROWS,
) -> tuple[int, int]:
    available = total_rows - min_history
    if available <= 0:
        return 0, 0

    desired_folds = max(1, _adaptive_folds(total_rows))
    max_horizon_for_desired = available // desired_folds
    if max_horizon_for_desired >= 1:
        horizon_used = min(requested_horizon, max_horizon_for_desired)
    else:
        horizon_used = min(requested_horizon, available)
    horizon_used = max(1, int(horizon_used))

    folds = available // horizon_used
    folds = max(1, min(desired_folds, folds))
    return horizon_used, folds


def _iter_rolling_splits(
    *,
    frame: pd.DataFrame,
    horizon: int,
    folds: int,
):
    total = len(frame)
    for fold_idx in range(folds):
        remaining = folds - fold_idx
        test_end = total - (remaining - 1) * horizon
        test_start = test_end - horizon
        train_df = frame.iloc[:test_start].copy()
        test_df = frame.iloc[test_start:test_end].copy()
        yield train_df, test_df


def _build_model(
    *,
    model_name: str,
    time_column: str,
    target_column: str,
    feature_columns: list[str],
    frequency: str,
    season_length: int,
) -> BaseForecaster:
    kwargs = {
        "time_column": time_column,
        "target_column": target_column,
        "feature_columns": feature_columns,
        "frequency": frequency,
        "season_length": season_length,
    }
    if model_name == "naive":
        return NaiveForecaster(**kwargs)
    if model_name == "seasonal_naive":
        return SeasonalNaiveForecaster(**kwargs)
    if model_name in ("ets", "exp_smoothing"):
        return ETSForecaster(**kwargs)
    if model_name == "sarimax":
        return SarimaxForecaster(**kwargs)
    if model_name == "catboost":
        return CatBoostForecaster(**kwargs)
    if model_name == "lightgbm":
        return LightGBMForecaster(**kwargs)
    if model_name == "prophet":
        return ProphetForecaster(**kwargs)
    raise ForecastingError(f"Unsupported model: {model_name}")


def _select_best_model(successful: list[dict], primary_metric: str = PRIMARY_SELECTION_METRIC) -> dict:
    """Select best model using average rank across all metrics.

    Each model is ranked 1..N on every metric; the model with the lowest
    average rank wins.  Ties are broken by the primary metric value.
    This prevents a model that dominates one metric but fails on others
    from being selected over a consistently-good model.
    """
    if len(successful) == 1:
        return successful[0]

    metrics_to_rank = list(SELECTION_METRIC_ORDER)
    ranks: dict[str, list[float]] = {row["model"]: [] for row in successful}

    for metric in metrics_to_rank:
        sorted_models = sorted(successful, key=lambda r: float(r["metrics"].get(metric, float("inf"))))
        for rank, row in enumerate(sorted_models, start=1):
            ranks[row["model"]].append(float(rank))

    def _avg_rank_score(row: dict) -> tuple[float, float]:
        avg_rank = float(np.mean(ranks[row["model"]]))
        primary_val = float(row["metrics"].get(primary_metric, float("inf")))
        return (avg_rank, primary_val)

    return min(successful, key=_avg_rank_score)


def _find_ensemble_partner(
    successful: list[dict],
    best: dict,
    primary_metric: str = PRIMARY_SELECTION_METRIC,
    max_gap: float = 0.25,
) -> dict | None:
    """Return the runner-up model if it's within *max_gap* of the best on primary metric.

    Ensembling two close models reduces variance on noisy series.
    Returns None if there's no suitable partner (only 1 model, or gap too large).
    """
    if len(successful) < 2:
        return None

    best_val = float(best["metrics"].get(primary_metric, float("inf")))
    if not np.isfinite(best_val) or best_val < 1e-12:
        return None

    others = [r for r in successful if r["model"] != best["model"]]
    runner = min(others, key=lambda r: float(r["metrics"].get(primary_metric, float("inf"))))
    runner_val = float(runner["metrics"].get(primary_metric, float("inf")))

    if not np.isfinite(runner_val):
        return None

    gap = (runner_val - best_val) / best_val
    if gap <= max_gap:
        return runner
    return None


def _extract_best_models_by_metric(successful: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for metric in SELECTION_METRIC_ORDER:
        out[metric] = min(successful, key=lambda row: float(row["metrics"].get(metric, float("inf"))))["model"]
    return out


def _compute_baseline_delta(
    *,
    successful: list[dict],
    best: dict,
    metric: str,
    baseline_model_name: str = "naive",
) -> dict:
    """Return normalized improvement over baseline on selected metric."""
    baseline = next((row for row in successful if row.get("model") == baseline_model_name), None)
    best_val = float(best.get("metrics", {}).get(metric, float("inf")))
    baseline_val = float(baseline.get("metrics", {}).get(metric, float("inf"))) if baseline else float("inf")
    has_baseline = np.isfinite(baseline_val) and baseline_val > 1e-12
    improvement = ((baseline_val - best_val) / baseline_val) if has_baseline else float("nan")
    return {
        "metric": metric,
        "baseline_model": baseline_model_name,
        "baseline_value": baseline_val if np.isfinite(baseline_val) else None,
        "best_model": best.get("model"),
        "best_value": best_val if np.isfinite(best_val) else None,
        "improvement_ratio": float(improvement) if np.isfinite(improvement) else None,
        "improvement_pct": float(improvement * 100.0) if np.isfinite(improvement) else None,
    }


def _classify_confidence(*, baseline_delta: dict, selection_metric: str) -> tuple[str, str]:
    """Return confidence level + reason based on selected-metric baseline delta."""
    improvement_pct = baseline_delta.get("improvement_pct")
    if improvement_pct is None:
        return "medium", f"Best model selected by {selection_metric.upper()} (naive baseline unavailable)"
    if improvement_pct >= 20:
        return "high", (
            f"Model beats naive baseline by {int(round(improvement_pct))}% "
            f"on {selection_metric.upper()}"
        )
    if improvement_pct >= 0:
        return "medium", (
            f"Model beats naive baseline by {int(round(improvement_pct))}% "
            f"on {selection_metric.upper()}"
        )
    return "low", (
        f"Model is {int(round(abs(improvement_pct)))}% worse than naive baseline "
        f"on {selection_metric.upper()}"
    )


def _detect_forecast_quality_warnings(
    *,
    forecast_values: np.ndarray,
    prediction_intervals: list[dict],
) -> list[str]:
    """Return human-readable warnings for suspicious forecast behavior."""
    warnings: list[str] = []

    values = np.asarray(forecast_values, dtype=float)
    if len(values) >= 10:
        rounded = np.round(values, 2)
        unique_ratio = float(len(np.unique(rounded)) / max(1, len(rounded)))
        if unique_ratio < 0.6:
            warnings.append("Forecast becomes repetitive across horizon; validate seasonality and exogenous signals.")

    if prediction_intervals:
        widths = np.asarray(
            [float(pi["upper"]) - float(pi["lower"]) for pi in prediction_intervals], dtype=float
        )
        mean_width = float(np.mean(np.maximum(widths, 0.0)))
        mean_level = float(np.mean(np.abs(values))) if len(values) else 0.0
        if mean_level > 1e-9 and (mean_width / mean_level) > 1.0:
            warnings.append("Prediction intervals are very wide relative to forecast level; confidence is limited.")

    return warnings


def _metrics_are_sane(metrics: dict) -> bool:
    """Reject results with non-finite or absurdly large error metrics."""
    for key in ("mae", "rmse", "wape"):
        val = metrics.get(key)
        if val is None:
            return False
        if not np.isfinite(val):
            return False
    if metrics["wape"] > 2.0:
        return False
    return True


def _compute_naive_mae(train_series: np.ndarray) -> float:
    """Compute the in-sample MAE of the one-step naive forecast (y_t = y_{t-1}).

    Used as the scaling denominator for MASE.  Returns a small positive
    floor if the series is constant to avoid division by zero.
    """
    s = np.asarray(train_series, dtype=float)
    if len(s) < 2:
        return 1e-9
    naive_errors = np.abs(np.diff(s))
    result = float(np.mean(naive_errors))
    return max(result, 1e-9)


def _compute_metrics(
    *, y_true: np.ndarray, y_pred: np.ndarray, naive_mae: float = 0.0
) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if len(y_true) != len(y_pred):
        raise ForecastingError("Prediction length does not match target length.")
    if len(y_true) == 0:
        raise ForecastingError("Empty target for metric computation.")

    err = y_true - y_pred
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(np.square(err))))
    denom = float(np.sum(np.abs(y_true)))
    wape = float(np.sum(np.abs(err)) / denom) if denom > 1e-9 else 0.0
    mase = mae / naive_mae if naive_mae > 1e-9 else float("inf")
    return {"mae": mae, "rmse": rmse, "wape": wape, "mase": float(mase)}


def _build_future_frame(
    *,
    train_df: pd.DataFrame,
    time_column: str,
    feature_columns: list[str],
    frequency: str,
    horizon: int,
) -> pd.DataFrame:
    last_ts = pd.to_datetime(train_df[time_column].iloc[-1])
    offset = pd.tseries.frequencies.to_offset(frequency)
    future_times = [last_ts + (i + 1) * offset for i in range(horizon)]

    out = pd.DataFrame({time_column: future_times})
    for col in feature_columns:
        last_val = train_df[col].iloc[-1] if col in train_df.columns else None
        out[col] = [last_val] * horizon
    return out


def _build_future_exog_frame(
    *,
    horizon: int,
    feature_columns: list[str],
    last_row: dict[str, Any],
) -> pd.DataFrame:
    frame = pd.DataFrame(index=range(horizon))
    for col in feature_columns:
        frame[col] = [last_row.get(col)] * horizon
    return frame


def _evaluate_holdout_test(
    *,
    prepared: "PreparedSeries",
    horizon: int,
    candidate_models: list[str],
    non_negative_target: bool,
) -> dict[str, dict | None]:
    """Train each model on the first (1 - TEST_SPLIT_RATIO) of the prepared data
    and evaluate on the held-out last TEST_SPLIT_RATIO portion.

    Returns a dict mapping model_name → metrics dict (mae/rmse/wape/mase/n_test)
    or None if the model failed on the holdout split.

    This is separate from the rolling cross-validation used for model selection —
    it provides a single held-out "real-world" test evaluation shown to the user.
    """
    frame = prepared.frame
    n = len(frame)

    # Determine test window: last TEST_SPLIT_RATIO of data, capped to avoid
    # an unreasonably large test set for long series.
    test_size = max(1, int(round(n * TEST_SPLIT_RATIO)))
    train_size = n - test_size

    if train_size < MIN_HISTORY_ROWS:
        return {}

    train_df = frame.iloc[:train_size].copy()
    test_df = frame.iloc[train_size:].copy()

    results: dict[str, dict | None] = {}
    for model_name in candidate_models:
        min_hist = _get_model_min_history(model_name, prepared.season_length)
        if len(train_df) < min_hist:
            results[model_name] = None
            continue

        try:
            model = _build_model(
                model_name=model_name,
                time_column=prepared.time_column,
                target_column=prepared.target_column,
                feature_columns=prepared.feature_columns,
                frequency=prepared.frequency,
                season_length=prepared.season_length,
            )
            _cap = CV_TRAIN_CAP.get(model_name)
            fit_df = train_df.iloc[-_cap:].copy() if _cap and len(train_df) > _cap else train_df
            model.fit(fit_df)

            predict_steps = min(len(test_df), horizon)
            future_frame = (
                test_df[[prepared.time_column, *prepared.feature_columns]]
                .head(predict_steps)
                .copy()
            )
            pred = model.predict(horizon=predict_steps, future_frame=future_frame)
            y_true = (
                test_df[prepared.target_column]
                .astype(float)
                .head(predict_steps)
                .to_numpy()
            )

            if prepared.log_transformed:
                pred = _invert_log_transform(pred)
                y_true = _invert_log_transform(y_true)

            pred = _apply_prediction_constraints(pred, non_negative_target=non_negative_target)

            train_target = train_df[prepared.target_column].astype(float).to_numpy()
            if prepared.log_transformed:
                train_target = _invert_log_transform(train_target)
            naive_mae = _compute_naive_mae(train_target)

            metrics = _compute_metrics(y_true=y_true, y_pred=pred, naive_mae=naive_mae)
            dates = (
                test_df[prepared.time_column]
                .head(predict_steps)
                .apply(lambda x: pd.to_datetime(x).date().isoformat())
                .tolist()
            )
            results[model_name] = {
                **metrics,
                "n_test": int(len(y_true)),
                "dates": dates,
                "actuals": [round(float(v), 6) for v in y_true],
                "predictions": [round(float(v), 6) for v in pred],
            }

        except Exception as exc:
            logger.warning("Holdout test failed for model=%s: %s", model_name, exc)
            results[model_name] = None

    return results
