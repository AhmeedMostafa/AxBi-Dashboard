import unittest

from api.processing.step7_dashboard_blueprint import _parse_json_object


class Step7JsonParsingTests(unittest.TestCase):
    def test_parses_markdown_fenced_json_object(self):
        raw = """```json
{
  "dataset_type": "Sales",
  "global_confidence": 0.91,
  "suggested_title": "Sales Overview",
  "suggested_charts": []
}
```"""
        parsed = _parse_json_object(raw)
        self.assertEqual(parsed["dataset_type"], "Sales")
        self.assertIn("suggested_charts", parsed)

    def test_repairs_common_json_issues(self):
        raw = """
        {
          dataset_type: "Marketing",
          global_confidence: 0.88,
          suggested_title: "Marketing Overview",
          suggested_charts: [],
        }
        """
        parsed = _parse_json_object(raw)
        self.assertEqual(parsed["dataset_type"], "Marketing")
        self.assertEqual(parsed["global_confidence"], 0.88)


if __name__ == "__main__":
    unittest.main()
