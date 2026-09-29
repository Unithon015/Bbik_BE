import unittest

from src.application.content.preprocessor import preprocess_text


class PreprocessTextTest(unittest.TestCase):
    def test_pii_finding_excerpt_is_original_match(self):
        text = "연락 010-1234-5678 또는 010-9999-0000, 메일 a@b.com"
        result = preprocess_text(text)

        excerpts = {f.signal_type: f.excerpt for f in result.pii_findings}
        self.assertEqual(excerpts, {"개인 전화번호": "010-1234-5678", "개인 이메일 주소": "a@b.com"})
        self.assertTrue(all(e in text for e in excerpts.values()))
        self.assertEqual(result.pii_counts, {"phone": 2, "email": 1})
