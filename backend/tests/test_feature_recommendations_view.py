import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import django
from django.conf import settings

if not settings.configured:
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    django.setup()

from rest_framework.test import APIRequestFactory

from api import views


class FeatureRecommendationsViewTests(unittest.TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        n = 120
        self.df = pd.DataFrame({
            "order_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "revenue": np.arange(n, dtype=float),
            "strong": np.arange(n, dtype=float) * 3.0,
            "noise": np.random.default_rng(1).normal(0, 1, n),
        })

    def _call(self, params):
        request = self.factory.get("/api/datasets/abc/feature-recommendations/", params)
        return views.get_feature_recommendations_view(request, "abc")

    @patch("api.views._authenticate_request", return_value=("user-1", None))
    @patch("api.views.get_dataset")
    @patch("api.views.download_file_bytes")
    def test_returns_ranked_recommendations(self, mock_dl, mock_get, mock_auth):
        mock_get.return_value = {
            "user_id": "user-1",
            "processed_path": "user-1/x.parquet",
            "global_context": {},
        }
        import io
        buf = io.BytesIO()
        self.df.to_parquet(buf, index=False)
        mock_dl.return_value = buf.getvalue()

        resp = self._call({"target": "revenue", "time": "order_date"})
        self.assertEqual(resp.status_code, 200)
        recs = resp.data["recommendations"]
        self.assertEqual(recs[0]["feature"], "strong")

    @patch("api.views._authenticate_request", return_value=("user-1", None))
    @patch("api.views.get_dataset", return_value=None)
    def test_404_when_dataset_missing(self, mock_get, mock_auth):
        resp = self._call({"target": "revenue"})
        self.assertEqual(resp.status_code, 404)

    @patch("api.views._authenticate_request", return_value=("user-1", None))
    @patch("api.views.get_dataset")
    def test_403_when_not_owner(self, mock_get, mock_auth):
        mock_get.return_value = {"user_id": "someone-else"}
        resp = self._call({"target": "revenue"})
        self.assertEqual(resp.status_code, 403)

    @patch("api.views._authenticate_request", return_value=("user-1", None))
    @patch("api.views.get_dataset")
    def test_400_when_target_missing_param(self, mock_get, mock_auth):
        mock_get.return_value = {"user_id": "user-1", "processed_path": "p.parquet"}
        resp = self._call({})
        self.assertEqual(resp.status_code, 400)
