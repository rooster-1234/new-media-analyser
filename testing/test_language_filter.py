import unittest
from filter import MediaFilter

class TestLanguageFilter(unittest.TestCase):
    def setUp(self):
        self.flt = MediaFilter()
        self.flt.exclude_existing = False
        self.flt.require_english = True
        self.flt.min_imdb_score = 1.0

    def test_explicit_korean_language(self):
        item = {
            "title": "Ganeunghan sarang (2026)",
            "clean_title": "Ganeunghan sarang",
            "year": 2026,
            "imdb_id": "tt37803364",
            "language": "Korean",
            "country": "South Korea, United States",
            "imdb_score": "7.6",
            "genres": ["Drama"]
        }
        res = self.flt.evaluate(item)
        self.assertEqual(res["filter_status"], "Non-English")
        self.assertIn("Korean", res["filter_reason"])

    def test_country_co_production_primary_foreign(self):
        # Even without explicit language metadata, primary country South Korea flags as non-English
        item = {
            "title": "Ganeunghan sarang (2026)",
            "clean_title": "Ganeunghan sarang",
            "year": 2026,
            "imdb_id": "tt37803364",
            "language": None,
            "country": "South Korea, United States",
            "imdb_score": "7.6",
            "genres": ["Drama"]
        }
        res = self.flt.evaluate(item)
        self.assertEqual(res["filter_status"], "Non-English")
        self.assertIn("South Korea", res["filter_reason"])

    def test_english_co_production_primary_english(self):
        item = {
            "title": "American Indie (2026)",
            "clean_title": "American Indie",
            "year": 2026,
            "imdb_id": "tt12345678",
            "language": None,
            "country": "United States, France",
            "imdb_score": "7.6",
            "genres": ["Drama"]
        }
        res = self.flt.evaluate(item)
        self.assertEqual(res["filter_status"], "Consider")

if __name__ == "__main__":
    unittest.main()
