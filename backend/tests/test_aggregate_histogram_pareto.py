import io
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


class AggregateHistogramParetoTests(unittest.TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.df = pd.DataFrame({
            "price": np.concatenate([np.full(50, 10.0), np.full(50, 90.0)]),
            "region": (["north"] * 80) + (["south"] * 15) + (["west"] * 5),
        })

    def _post(self, charts):
        req = self.factory.post(
            "/api/datasets/d/aggregate/", {"charts": charts}, format="json")
        return views.aggregate_charts_view(req, "d")

    @patch("api.views._authenticate_request", return_value=("u", None))
    @patch("api.views.get_dataset")
    @patch("api.views.download_file_bytes")
    def test_histogram_and_pareto(self, mock_dl, mock_get, mock_auth):
        mock_get.return_value = {"user_id": "u", "processed_path": "u/x.parquet",
                                 "global_context": {}}
        buf = io.BytesIO()
        self.df.to_parquet(buf, index=False)
        mock_dl.return_value = buf.getvalue()

        resp = self._post([
            {"chart_type": "histogram", "x_axis": "price", "y_axis": None},
            {"chart_type": "pareto", "x_axis": "region", "y_axis": "price"},
        ])
        self.assertEqual(resp.status_code, 200)
        hist, pareto = resp.data["results"]
        self.assertEqual(hist["chart_type"], "histogram")
        self.assertTrue(len(hist["data"]) >= 1)
        self.assertIn("label", hist["data"][0])
        self.assertIn("value", hist["data"][0])
        self.assertEqual(pareto["chart_type"], "pareto")
        self.assertIn("cumulative", pareto["data"][0])
