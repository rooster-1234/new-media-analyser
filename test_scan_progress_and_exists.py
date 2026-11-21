import unittest
from fastapi.testclient import TestClient
from app import app, ensure_user_library_profile
from filter import MediaFilter
import user_manager

class TestScanProgressAndExists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        user_manager.register_user("heart_test@example.com", "Password123!")
        _, cls.token = user_manager.authenticate_user("heart_test@example.com", "Password123!")

    def test_heart_of_the_beast_marked_exists(self):
        # Using a profile containing 'Heart of the Beast'
        profile_path = "data/profiles/test_plex_qbit_library_profile.json"
        
        # Test 1: With exclude_existing = True
        flt_true = MediaFilter(profile_path=profile_path)
        flt_true.exclude_existing = True
        item1 = {"title": "Heart of the Beast 2026 1080p", "year": 2026, "imdb_score": "7.0"}
        res1 = flt_true.evaluate(item1)
        self.assertEqual(res1["filter_status"], "Exists")
        self.assertTrue(res1.get("in_library"))
        self.assertIn("already exists in Plex library", res1["filter_reason"])

        # Test 2: With exclude_existing = False (user turned off the filter)
        flt_false = MediaFilter(profile_path=profile_path)
        flt_false.exclude_existing = False
        item2 = {"title": "Heart of the Beast 2026 1080p", "year": 2026, "imdb_score": "7.0"}
        res2 = flt_false.evaluate(item2)
        self.assertEqual(res2["filter_status"], "Exists")
        self.assertTrue(res2.get("in_library"))
        self.assertIn("already exists in Plex library", res2["filter_reason"])

    def test_ensure_user_library_profile(self):
        # For a new user without a profile, ensure_user_library_profile should create one from existing server profiles
        p = ensure_user_library_profile("heart_test@example.com")
        self.assertTrue(p.exists())
        self.assertGreater(p.stat().st_size, 100)

    def test_scan_status_endpoint(self):
        resp = self.client.get("/api/scan/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("is_scanning", data)
        self.assertIn("found", data)
        self.assertIn("message", data)

if __name__ == "__main__":
    unittest.main()
