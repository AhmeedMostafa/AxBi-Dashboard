import io
import unittest

import pandas as pd

from api.accumulation.service import read_upload, accumulate_files


def _csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


class AccumulationFilesTests(unittest.TestCase):
    def test_read_upload_csv(self):
        df = pd.DataFrame({"A": [1], "B": [2]})
        out = read_upload(_csv_bytes(df), "x.csv")
        self.assertEqual(list(out.columns), ["a", "b"])

    def test_accumulate_accepts_matching_rejects_mismatch(self):
        base = pd.DataFrame({"order_id": [1, 2], "amount": [10, 20]})
        same = pd.DataFrame({"Order Id": [3], "Amount": [30]})
        bad = pd.DataFrame({"order_id": [4], "totally": [99]})
        files = [
            ("base.csv", _csv_bytes(base)),
            ("same.csv", _csv_bytes(same)),
            ("bad.csv", _csv_bytes(bad)),
        ]
        result = accumulate_files(files)
        self.assertEqual(result["accepted"], ["base.csv", "same.csv"])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(result["rejected"][0]["filename"], "bad.csv")
        self.assertEqual(len(result["dataframe"]), 3)
        self.assertEqual(sorted(result["dataframe"]["order_id"].tolist()), [1, 2, 3])

    def test_accumulate_all_mismatch_raises(self):
        a = pd.DataFrame({"x": [1]})
        b = pd.DataFrame({"y": [2]})
        with self.assertRaises(ValueError):
            accumulate_files([("a.csv", _csv_bytes(a)), ("b.csv", _csv_bytes(b))])
