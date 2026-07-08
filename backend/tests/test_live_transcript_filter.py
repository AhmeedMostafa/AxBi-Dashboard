import unittest

from api.live_transcript_filter import user_transcript_allowed


class LiveTranscriptFilterTests(unittest.TestCase):
    def test_rejects_hindi_when_arabic_selected(self):
        self.assertFalse(user_transcript_allowed("तो जज भी ठीक करामाते", "ar-EG"))

    def test_accepts_arabic_when_arabic_selected(self):
        self.assertTrue(user_transcript_allowed("عايز أشوف بيانات نتفلix", "ar-EG"))

    def test_rejects_hindi_when_english_selected(self):
        self.assertFalse(user_transcript_allowed("नमस्ते", "en-US"))

    def test_accepts_english_when_english_selected(self):
        self.assertTrue(user_transcript_allowed("Show me sales trends", "en-US"))

    def test_rejects_arabic_when_english_selected(self):
        self.assertFalse(user_transcript_allowed("عايز أشوف المبيعات", "en-US"))


if __name__ == "__main__":
    unittest.main()
