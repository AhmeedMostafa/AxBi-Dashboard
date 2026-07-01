import unittest

import pandas as pd

from api.accumulation.service import (
    normalize_columns,
    schemas_match,
    detect_key,
    combine,
)


class AccumulationServiceTests(unittest.TestCase):
    def test_normalize_columns_snake_cases(self):
        df = pd.DataFrame({"Order Date": [1], "Total-Revenue": [2]})
        out = normalize_columns(df)
        self.assertEqual(list(out.columns), ["order_date", "total_revenue"])

    def test_schemas_match_name_set_order_independent(self):
        a = ["order_date", "revenue"]
        b = ["revenue", "order_date"]
        ok, reason = schemas_match(a, b)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_schemas_mismatch_reports_difference(self):
        ok, reason = schemas_match(["a", "b"], ["a", "c"])
        self.assertFalse(ok)
        self.assertIn("c", reason)

    def test_detect_key_picks_unique_id_column(self):
        df = pd.DataFrame({"order_id": [1, 2, 3], "amount": [9, 9, 9]})
        self.assertEqual(detect_key(df), ["order_id"])

    def test_detect_key_returns_none_when_no_unique(self):
        df = pd.DataFrame({"region": ["a", "a"], "amount": [1, 1]})
        self.assertIsNone(detect_key(df))

    def test_combine_upsert_on_key_keeps_last(self):
        a = pd.DataFrame({"id": [1, 2], "v": [10, 20]})
        b = pd.DataFrame({"id": [2, 3], "v": [99, 30]})
        out = combine([a, b], key=["id"])
        self.assertEqual(sorted(out["id"].tolist()), [1, 2, 3])
        self.assertEqual(out.loc[out["id"] == 2, "v"].iloc[0], 99)

    def test_combine_without_key_drops_exact_duplicates(self):
        a = pd.DataFrame({"x": [1, 2]})
        b = pd.DataFrame({"x": [2, 3]})
        out = combine([a, b], key=None)
        self.assertEqual(sorted(out["x"].tolist()), [1, 2, 3])
