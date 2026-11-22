import unittest
from fastapi.testclient import TestClient
import app
import user_manager

class TestSortingAndDateFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.username = "test_sort_user@example.com"
        cls.password = "SortPassword123!"
        user_manager.register_user(cls.username, cls.password)
        _, cls.token = user_manager.authenticate_user(cls.username, cls.password)
        cls.client = TestClient(app.app, cookies={"session_token": cls.token})

    def test_format_long_date(self):
        """Verify format_long_date outputs long readable dates."""
        self.assertEqual(app.format_long_date("2026-06-06"), "6 June 2026")
        self.assertEqual(app.format_long_date("2025-08-08"), "8 August 2025")
        self.assertEqual(app.format_long_date("2018-12-14"), "14 December 2018")
        self.assertEqual(app.format_long_date("2026-05-01"), "1 May 2026")
        self.assertEqual(app.format_long_date(None, 2026), "2026")
        self.assertEqual(app.format_long_date("", 2024), "2024")
        self.assertEqual(app.format_long_date(None, None), "-")

    def test_parse_size_to_bytes(self):
        """Verify parse_size_to_bytes converts size strings accurately for numeric sorting."""
        gib_size = app.parse_size_to_bytes("1.17 GiB")
        mib_size = app.parse_size_to_bytes("807.34 MiB")
        self.assertGreater(gib_size, mib_size)
        self.assertEqual(app.parse_size_to_bytes("1 GiB"), 1024 * 1024 * 1024)
        self.assertEqual(app.parse_size_to_bytes("1 MiB"), 1024 * 1024)
        self.assertEqual(app.parse_size_to_bytes("invalid"), 0)
        self.assertEqual(app.parse_size_to_bytes(None), 0)

    def test_dashboard_table_headers_and_sorting(self):
        """Verify dashboard table displays 'Released', sortable headers, and sortTable JS."""
        resp = cls_client = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Header renamed from US Release to Released
        self.assertIn(">Released <span class=\"sort-icon\">↕</span></th>", html)
        self.assertNotIn("<th>US Release</th>", html)

        # Sortable columns present
        self.assertIn("data-sort=\"title\"", html)
        self.assertIn("data-sort=\"date\"", html)
        self.assertIn("data-sort=\"size\"", html)
        self.assertIn("data-sort=\"score\"", html)
        self.assertIn("data-sort=\"status\"", html)

        # JavaScript sortTable function present
        self.assertIn("function sortTable(column)", html)
        self.assertIn("currentSortCol", html)

        # Data attributes on rows
        self.assertIn("data-size-bytes=", html)
        self.assertIn("data-score=", html)
        self.assertIn("data-title=", html)

        # Bold formatting for date-text
        self.assertIn("class='date-text'", html)
        self.assertIn(".date-text { font-size: 13px; color: #cbd5e1; font-weight: 600;", html)

if __name__ == "__main__":
    unittest.main()
