import unittest

import numpy as np
import pandas as pd

from api.forecasting.feature_recommender import recommend_features


class FeatureRecommenderTests(unittest.TestCase):
    def test_strong_numeric_corr_ranks_first(self):
        n = 200
        rng = np.random.default_rng(42)
        target = np.arange(n, dtype=float)
        df = pd.DataFrame({
            "order_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "revenue": target,
            "strong": target * 2.0 + rng.normal(0, 1, n),
            "noise": rng.normal(0, 1, n),
        })
        result = recommend_features(df, target_column="revenue", time_column="order_date")
        recs = result["recommendations"]
        self.assertEqual(recs[0]["feature"], "strong")
        self.assertGreater(recs[0]["score"], recs[-1]["score"])
        self.assertNotIn("order_date", [r["feature"] for r in recs])
        self.assertNotIn("revenue", [r["feature"] for r in recs])

    def test_categorical_feature_uses_mutual_info(self):
        n = 150
        cats = (["a"] * 50) + (["b"] * 50) + (["c"] * 50)
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=n, freq="D"),
            "y": [1.0] * 50 + [5.0] * 50 + [9.0] * 50,
            "segment": cats,
        })
        result = recommend_features(df, target_column="y", time_column="ts")
        recs = result["recommendations"]
        self.assertTrue(any(r["feature"] == "segment" for r in recs))
        seg = next(r for r in recs if r["feature"] == "segment")
        self.assertEqual(seg["method"], "mutual_info")
        self.assertGreater(seg["score"], 0.0)

    def test_invalid_target_returns_empty_with_reason(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        result = recommend_features(df, target_column="missing", time_column="a")
        self.assertEqual(result["recommendations"], [])
        self.assertTrue(result["reason"])

    def test_no_candidates_returns_empty_with_reason(self):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=5, freq="D"),
            "y": [1.0, 2.0, 3.0, 4.0, 5.0],
        })
        result = recommend_features(df, target_column="y", time_column="ts")
        self.assertEqual(result["recommendations"], [])
        self.assertTrue(result["reason"])
