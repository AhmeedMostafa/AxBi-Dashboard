import unittest

from api.processing import step7_dashboard_blueprint as s7


class Step7ChartVarietyTests(unittest.TestCase):
    def _index(self):
        cols = [
            {"clean_name": "price", "original_name": "price", "data_type": "numeric",
             "is_primary_metric": True, "ai_profile": {"role": "metric"},
             "technical_stats": {"min": 1, "max": 100, "mean": 40}},
            {"clean_name": "qty", "original_name": "qty", "data_type": "numeric",
             "is_primary_metric": False, "ai_profile": {"role": "metric"},
             "technical_stats": {"min": 0, "max": 5, "mean": 2}},
            {"clean_name": "region", "original_name": "region", "data_type": "text",
             "is_primary_metric": False, "ai_profile": {"role": "dimension"},
             "technical_stats": {"top_5_samples": ["a", "b"]}},
        ]
        return cols, s7._build_columns_index(cols)

    def test_histogram_and_pareto_allowed(self):
        self.assertIn("histogram", s7.ALLOWED_CHART_TYPES)
        self.assertIn("pareto", s7.ALLOWED_CHART_TYPES)

    def test_histogram_requires_numeric_x(self):
        _, idx = self._index()
        ok = s7._normalize_chart(
            {"chart_type": "histogram", "title": "Price dist", "x_axis": "price",
             "y_axis": None, "columns": ["price"]}, idx, [])
        self.assertIsNotNone(ok)
        self.assertEqual(ok["chart_type"], "histogram")
        bad = s7._normalize_chart(
            {"chart_type": "histogram", "title": "Bad", "x_axis": "region",
             "y_axis": None, "columns": ["region"]}, idx, [])
        self.assertIsNone(bad)

    def test_pareto_requires_dimension_and_metric(self):
        _, idx = self._index()
        ok = s7._normalize_chart(
            {"chart_type": "pareto", "title": "Top regions", "x_axis": "region",
             "y_axis": "price", "columns": ["region", "price"]}, idx, [])
        self.assertIsNotNone(ok)
        bad = s7._normalize_chart(
            {"chart_type": "pareto", "title": "Bad", "x_axis": "region",
             "y_axis": None, "columns": ["region"]}, idx, [])
        self.assertIsNone(bad)

    def test_prompt_includes_shape_signals(self):
        cols, _ = self._index()
        dataset = {"id": "1", "file_name": "f.csv", "category_hint": "Sales"}
        prompt = s7._build_prompt(dataset, cols, None)
        self.assertIn("dataset_shape", prompt)
        self.assertIn("numeric_count", prompt)
