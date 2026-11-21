import unittest
import json
from pathlib import Path
from fastapi.testclient import TestClient

import app
import user_manager

class TestFeedbackSystem(unittest.TestCase):
    def setUp(self):
        # Register and authenticate test user
        self.username = "test_feedback_user"
        self.password = "FeedbackPass123!"
        user_manager.register_user(self.username, self.password)
        _, self.token = user_manager.authenticate_user(self.username, self.password)
        
        self.client = TestClient(app.app, cookies={"session_token": self.token})

        # Backup existing feedback if any
        self.feedback_file = Path("data/feedback.json")
        self.original_feedback = None
        if self.feedback_file.exists():
            self.original_feedback = self.feedback_file.read_text(encoding="utf-8")

    def tearDown(self):
        # Restore original feedback
        if self.original_feedback is not None:
            self.feedback_file.write_text(self.original_feedback, encoding="utf-8")
        elif self.feedback_file.exists():
            self.feedback_file.unlink()

    def test_submit_feedback_validation(self):
        """Test submitting empty feedback returns 400."""
        resp = self.client.post("/api/feedback", json={"comments": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_submit_and_get_feedback(self):
        """Test submitting valid feedback and retrieving it."""
        payload = {
            "rating": "😍 Love it",
            "category": "UI & Artwork",
            "comments": "The hover zoom on posters is brilliant! Very snappy.",
            "page": "/dashboard"
        }
        resp = self.client.post("/api/feedback", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))

        # Verify in file
        self.assertTrue(self.feedback_file.exists())
        entries = app.load_feedback_entries()
        self.assertGreaterEqual(len(entries), 1)
        first = entries[0]
        self.assertEqual(first["username"], self.username)
        self.assertEqual(first["rating"], "😍 Love it")
        self.assertIn("hover zoom", first["comments"])

        # Verify via GET endpoint
        get_resp = self.client.get("/api/feedback")
        self.assertEqual(get_resp.status_code, 200)
        get_data = get_resp.json()
        self.assertTrue(get_data["success"])
        self.assertGreaterEqual(len(get_data["entries"]), 1)

    def test_feedback_ui_rendered_on_pages(self):
        """Test that feedback button and modal are rendered on dashboard and settings."""
        # Dashboard
        dash_resp = self.client.get("/dashboard")
        self.assertEqual(dash_resp.status_code, 200)
        self.assertIn("btn-feedback", dash_resp.text)
        self.assertIn("feedbackModal", dash_resp.text)
        self.assertIn("sendWhatsappFeedback", dash_resp.text)

        # Settings
        sett_resp = self.client.get("/settings")
        self.assertEqual(sett_resp.status_code, 200)
        self.assertIn("btn-feedback", sett_resp.text)
        self.assertIn("feedbackModal", sett_resp.text)
        self.assertIn("4. Tester Feedback Log", sett_resp.text)

if __name__ == "__main__":
    unittest.main()
