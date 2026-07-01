import unittest

from preprocessing.pipeline import _read_csv_with_fallback


class Step3CsvEncodingTests(unittest.TestCase):
    def test_reads_cp1252_encoded_csv(self):
        content = "name,city\nAndré,Cairo\nMina,Alexandria\n"
        raw_bytes = content.encode("cp1252")

        df, encoding = _read_csv_with_fallback(raw_bytes)

        self.assertEqual(df.shape[0], 2)
        self.assertEqual(list(df.columns), ["name", "city"])
        self.assertEqual(str(df.iloc[0]["name"]), "André")
        self.assertIn(encoding, {"cp1252", "latin-1"})


if __name__ == "__main__":
    unittest.main()
