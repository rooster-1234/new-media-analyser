import unittest
from datetime import datetime, timedelta
from filter import MediaFilter

class TestFilterTimeframe(unittest.TestCase):
    def setUp(self):
        self.flt = MediaFilter()
        self.flt.exclude_existing = False
        self.flt.require_english = False
        self.flt.min_imdb_score = 1.0

    def test_timeframe_all(self):
        self.flt.timeframe = "all"
        item = {"title": "Classic Movie 1994", "year": 1994, "release_date": "1994-09-23", "imdb_score": "8.5"}
        res = self.flt.evaluate(item)
        self.assertEqual(res["filter_status"], "Consider")

    def test_timeframe_7d(self):
        self.flt.timeframe = "7d"
        today = datetime.now().date()
        recent_date = (today - timedelta(days=2)).isoformat()
        old_date = (today - timedelta(days=15)).isoformat()

        # Item released 2 days ago -> passes
        item_recent = {"title": "Fresh Movie", "release_date": recent_date, "year": today.year, "imdb_score": "7.0"}
        res_recent = self.flt.evaluate(item_recent)
        self.assertEqual(res_recent["filter_status"], "Consider")

        # Item released 15 days ago -> Too Old
        item_old = {"title": "Two Weeks Old Movie", "release_date": old_date, "year": today.year, "imdb_score": "7.0"}
        res_old = self.flt.evaluate(item_old)
        self.assertEqual(res_old["filter_status"], "Too Old")
        self.assertIn("older than last 7 days", res_old["filter_reason"])

    def test_timeframe_30d(self):
        self.flt.timeframe = "30d"
        today = datetime.now().date()
        recent_date = (today - timedelta(days=10)).isoformat()
        old_date = (today - timedelta(days=45)).isoformat()

        item_recent = {"title": "Recent Month Movie", "release_date": recent_date, "year": today.year, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_recent)["filter_status"], "Consider")

        item_old = {"title": "Older Movie", "release_date": old_date, "year": today.year, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_old)["filter_status"], "Too Old")

    def test_timeframe_365d(self):
        self.flt.timeframe = "365d"
        today = datetime.now().date()
        recent_date = (today - timedelta(days=120)).isoformat()
        old_date = (today - timedelta(days=450)).isoformat()

        item_recent = {"title": "This Year Film", "release_date": recent_date, "year": today.year, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_recent)["filter_status"], "Consider")

        item_old = {"title": "Last Year Film", "release_date": old_date, "year": today.year - 2, "imdb_score": "7.0"}
        res_old = self.flt.evaluate(item_old)
        self.assertEqual(res_old["filter_status"], "Too Old")

    def test_timeframe_fallback_to_year(self):
        self.flt.timeframe = "365d"
        today = datetime.now().date()

        # Item with only year, year is current year -> passes
        item_year_current = {"title": "Current Year Movie", "year": today.year, "release_date": None, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_year_current)["filter_status"], "Consider")

        # Item with only year, year is 3 years ago -> Too Old
        item_year_old = {"title": "Old Year Movie", "year": today.year - 3, "release_date": None, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_year_old)["filter_status"], "Too Old")

    def test_timeframe_specific_year(self):
        self.flt.timeframe = "year"
        self.flt.target_year = 2026

        item_2026 = {"title": "Future 2026 Film", "year": 2026, "imdb_score": "7.0"}
        self.assertEqual(self.flt.evaluate(item_2026)["filter_status"], "Consider")

        item_2024 = {"title": "Past 2024 Film", "year": 2024, "imdb_score": "7.0"}
        res = self.flt.evaluate(item_2024)
        self.assertEqual(res["filter_status"], "Too Old")
        self.assertIn("prior to target year 2026", res["filter_reason"])

if __name__ == "__main__":
    unittest.main()
