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
        with self.assertRaises(ValueError) as ctx:
            accumulate_files([("a.csv", _csv_bytes(a)), ("b.csv", _csv_bytes(b))])
        self.assertIn("No usable files", str(ctx.exception))

    def test_read_semicolon_csv(self):
        raw = b"Order Id;Amount\n1;10\n2;20"
        out = read_upload(raw, "euro.csv")
        self.assertEqual(list(out.columns), ["order_id", "amount"])
        self.assertEqual(len(out), 2)

    def test_reject_html_saved_as_csv(self):
        raw = b"<!DOCTYPE html><html><body>Login required</body></html>"
        with self.assertRaises(ValueError) as ctx:
            read_upload(raw, "fake.csv")
        self.assertIn("web page", str(ctx.exception).lower())

    def test_single_unreadable_shows_real_reason(self):
        with self.assertRaises(ValueError) as ctx:
            accumulate_files([("bad.csv", b"<!DOCTYPE html><html></html>")], allow_single=True)
        self.assertIn("Could not read bad.csv", str(ctx.exception))
        self.assertNotEqual(str(ctx.exception).strip(), "No files matched the expected schema.")

    def test_csv_extension_with_xlsx_content(self):
        buf = io.BytesIO()
        pd.DataFrame({"Employee Id": [1, 2], "Department": ["HR", "HR"]}).to_excel(
            buf, index=False, engine="openpyxl"
        )
        raw = buf.getvalue()
        self.assertTrue(raw[:2] == b"PK")
        out = read_upload(raw, "hr.csv")
        self.assertEqual(list(out.columns), ["employee_id", "department"])
        self.assertEqual(len(out), 2)
