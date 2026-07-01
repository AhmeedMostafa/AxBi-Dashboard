import unittest

from api.processing import step7_dashboard_blueprint as s7


class Step7BusinessCategoryTests(unittest.TestCase):
    def _cols(self):
        return [
            {"clean_name": "amount", "original_name": "amount", "data_type": "numeric",
             "is_primary_metric": True, "ai_profile": {"role": "metric"},
             "technical_stats": {"min": 1, "max": 9, "mean": 5}},
            {"clean_name": "label", "original_name": "label", "data_type": "text",
             "is_primary_metric": False, "ai_profile": {"role": "dimension"},
             "technical_stats": {"top_5_samples": ["x", "y"]}},
        ]

    def test_normalize_dataset_type_accepts_business(self):
        self.assertEqual(s7._normalize_dataset_type("business"), "Business")
        self.assertEqual(s7._normalize_dataset_type("general"), "Business")

    def test_invalid_type_defaults_to_business_not_sales(self):
        normalized, warnings = s7._normalize_blueprint(
            {"dataset_type": "weather_data", "suggested_charts": []}, self._cols()
        )
        self.assertEqual(normalized["dataset_type"], "Business")
        self.assertIn("dataset_type_defaulted_to_business", warnings)

    def test_business_has_fallback_chart_menu(self):
        self.assertIn("Business", s7.CATEGORY_CHART_MENU)
        charts = s7._build_category_fallback_charts("Business", s7._build_columns_index(self._cols()))
        self.assertTrue(len(charts) >= 1)
