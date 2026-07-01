import unittest
from unittest.mock import patch

from api.processing import step7_dashboard_blueprint as step7


class Step7FallbackTests(unittest.TestCase):
    @patch.object(step7, "repair_json", None)
    @patch.object(step7, "_ensure_gemini")
    @patch.object(step7, "_call_gemini")
    def test_step7_uses_fallback_blueprint_when_ai_json_invalid(self, mock_call_gemini, _mock_ensure):
        mock_call_gemini.side_effect = [
            '{"dataset_type":"Sales","suggested_charts":[',
            '{"dataset_type":"Sales","suggested_charts":[',
        ]

        dataset = {
            "id": "dataset-1",
            "file_name": "sales.csv",
            "category_hint": "sales",
            "file_info": "{}",
            "global_context": "{}",
        }
        columns_metadata = [
            {
                "clean_name": "order_date",
                "original_name": "order_date",
                "data_type": "datetime",
                "is_primary_metric": False,
                "ai_profile": '{"role":"date"}',
                "technical_stats": "{}",
            },
            {
                "clean_name": "revenue",
                "original_name": "revenue",
                "data_type": "numeric",
                "is_primary_metric": True,
                "ai_profile": '{"role":"measure"}',
                "technical_stats": "{}",
            },
            {
                "clean_name": "region",
                "original_name": "region",
                "data_type": "text",
                "is_primary_metric": False,
                "ai_profile": '{"role":"dimension"}',
                "technical_stats": "{}",
            },
        ]

        result = step7.run_step7(dataset, columns_metadata, step6_context={})

        self.assertEqual(result["status"], "completed")
        self.assertGreaterEqual(len(result["suggested_charts"]), 3)
        self.assertTrue(
            any(w.startswith("ai_json_invalid_used_fallback_blueprint:") for w in result["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
