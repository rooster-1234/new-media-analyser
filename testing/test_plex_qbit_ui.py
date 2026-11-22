import unittest
from fastapi.testclient import TestClient
from app import app
import user_manager

class TestPlexAndQbitUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        user_manager.register_user("test_ui_user@example.com", "Password123!")
        _, cls.token = user_manager.authenticate_user("test_ui_user@example.com", "Password123!")

    @classmethod
    def tearDownClass(cls):
        user_manager.delete_user("test_ui_user@example.com")

    def test_settings_page_plex_and_qbit_ui(self):
        resp = self.client.get("/settings", cookies={"session_token": self.token})
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Verify Plex UI improvements
        self.assertIn("1-Click Plex Authorization", html)
        self.assertIn("Sign In with Plex", html)
        self.assertIn("Your token does not need to be entered manually", html)
        self.assertIn('id="plex_token"', html)
        self.assertIn("readonly", html)
        self.assertIn("Edit manually", html)

        # Verify qBittorrent documentation link
        self.assertIn("https://github.com/qbittorrent/qBittorrent/wiki/API-Key-Authentication-(%E2%89%A5v5.2.0)", html)
        self.assertIn("how to find or create your API Key", html)

        # Verify Dashboard button styling
        self.assertIn('href="/dashboard" class="btn btn-primary"', html)

    def test_plex_oauth_start_endpoint(self):
        resp = self.client.post("/api/plex/oauth/start", cookies={"session_token": self.token})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("oauth_url", data)
        self.assertIn("pin_id", data)
        self.assertIn("app.plex.tv/auth", data["oauth_url"])

        # Poll with the created pin_id
        poll_resp = self.client.get(f"/api/plex/oauth/poll?pin_id={data['pin_id']}", cookies={"session_token": self.token})
        self.assertEqual(poll_resp.status_code, 200)
        poll_data = poll_resp.json()
        self.assertTrue(poll_data.get("success"))
        self.assertFalse(poll_data.get("authenticated"))

if __name__ == "__main__":
    unittest.main()
