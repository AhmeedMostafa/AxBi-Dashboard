import unittest

import numpy as np
import pandas as pd

from api.forecasting import run_forecast_service
from api.forecasting.service import _prepare_series_frame


class ForecastingServiceTests(unittest.TestCase):
    def test_readiness_failure_when_time_column_missing(self):
        df = pd.DataFrame(
            {
                "revenue": [100, 120, 130, 150],
                "discount": [0, 5, 10, 0],
            }
        )
        result = run_forecast_service(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=["discount"],
            horizon=7,
        )

        self.assertFalse(result["forecast_possible"])
        reasons = result["readiness"]["reasons"]
        self.assertTrue(any("time_column_not_found" in r for r in reasons))

    def test_end_to_end_with_baseline_and_optional_models(self):
        rng = pd.date_range("2025-01-01", periods=120, freq="D")
        base = np.linspace(100, 250, num=120)
        season = 10 * np.sin(np.arange(120) * 2 * np.pi / 7)
        noise = np.zeros(120)
        revenue = base + season + noise

        df = pd.DataFrame(
            {
                "order_date": rng,
                "revenue": revenue,
                "discount": np.where(np.arange(120) % 10 == 0, 15, 0),
                "region": np.where(np.arange(120) % 2 == 0, "east", "west"),
            }
        )

        result = run_forecast_service(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=["discount", "region"],
            horizon=14,
            candidate_models=["naive", "seasonal_naive", "sarimax", "catboost"],
        )

        self.assertTrue(result["forecast_possible"])
        self.assertEqual(result["horizon"], 14)
        self.assertEqual(len(result["forecast"]), 14)
        self.assertEqual(len(result["prediction_intervals"]), 14)
        self.assertIsNotNone(result["best_model"])
        self.assertIn(result["best_model"], {"naive", "seasonal_naive", "sarimax", "catboost"})
        self.assertIn("mae", result["metrics"])
        self.assertIn("rmse", result["metrics"])
        self.assertIn("wape", result["metrics"])

    def test_prediction_interval_lower_bound_clamped_for_non_negative_targets(self):
        rng = pd.date_range("2025-01-01", periods=90, freq="D")
        revenue = np.linspace(1.0, 2.0, num=90)
        df = pd.DataFrame({"order_date": rng, "revenue": revenue})

        result = run_forecast_service(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=[],
            horizon=14,
            candidate_models=["naive", "seasonal_naive"],
        )

        self.assertTrue(result["forecast_possible"])
        lowers = [row["lower"] for row in result["prediction_intervals"]]
        self.assertTrue(all(value >= 0 for value in lowers))
        forecast_values = [row["value"] for row in result["forecast"]]
        self.assertTrue(all(value >= 0 for value in forecast_values))

    def test_missing_periods_policy_drop_vs_zero(self):
        df = pd.DataFrame(
            {
                "order_date": ["2025-01-01", "2025-01-03", "2025-01-05"],
                "revenue": [100, 200, 300],
            }
        )

        prepared_drop = _prepare_series_frame(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=[],
            frequency="D",
            season_length=None,
            missing_periods_policy="drop",
        )
        prepared_zero = _prepare_series_frame(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=[],
            frequency="D",
            season_length=None,
            missing_periods_policy="zero",
        )

        self.assertEqual(len(prepared_drop.frame), 3)
        self.assertEqual(len(prepared_zero.frame), 5)
        zero_count = int((prepared_zero.frame["revenue"] == 0).sum())
        self.assertGreaterEqual(zero_count, 2)

    def test_backtest_uses_multiple_folds_by_default(self):
        rng = pd.date_range("2025-01-01", periods=70, freq="D")
        revenue = 100 + np.sin(np.arange(70) * 2 * np.pi / 7) * 5
        df = pd.DataFrame({"order_date": rng, "revenue": revenue})

        result = run_forecast_service(
            df=df,
            time_column="order_date",
            target_column="revenue",
            feature_columns=[],
            horizon=30,
            candidate_models=["naive", "seasonal_naive"],
            missing_periods_policy="drop",
        )

        self.assertTrue(result["forecast_possible"])
        successful = [row for row in result["model_results"] if row["status"] == "ok"]
        self.assertTrue(all(row["folds"] >= 2 for row in successful))
        self.assertTrue(all(row["backtest_horizon"] <= 30 for row in successful))


if __name__ == "__main__":
    unittest.main()
