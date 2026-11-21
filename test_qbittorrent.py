"""
Unit tests for qBittorrent downloader integration and settings.
"""
import unittest
from unittest.mock import patch, MagicMock
from retrieve_media import QBittorrentDownloader, DownloadResult, get_downloader
import user_manager
from app import app, get_user_downloader
from fastapi.testclient import TestClient


class TestQBittorrentDownloader(unittest.TestCase):

    def setUp(self):
        self.downloader = QBittorrentDownloader(
            url="http://localhost:8080",
            api_key="qbt_testkey1234567890123456789012",
            category="movies",
            save_path="/downloads/movies",
            start_now=True
        )

    def test_init_properties(self):
        self.assertEqual(self.downloader.name, "qbittorrent")
        self.assertEqual(self.downloader.url, "http://localhost:8080")
        self.assertEqual(self.downloader.api_key, "qbt_testkey1234567890123456789012")
        self.assertEqual(self.downloader.category, "movies")
        self.assertEqual(self.downloader.save_path, "/downloads/movies")
        self.assertTrue(self.downloader.start_now)

    def test_session_headers_with_api_key(self):
        session = self.downloader._get_session()
        self.assertEqual(session.headers.get("Authorization"), "Bearer qbt_testkey1234567890123456789012")

    @patch("requests.Session.get")
    def test_connection_success_api_key(self, mock_get):
        # Mock /api/v2/app/version response
        mock_ver = MagicMock()
        mock_ver.status_code = 200
        mock_ver.text = "v5.2.4"

        # Mock /api/v2/app/webapiVersion response
        mock_webapi = MagicMock()
        mock_webapi.status_code = 200
        mock_webapi.text = "2.15.1"

        mock_get.side_effect = [mock_ver, mock_webapi]

        success, msg = self.downloader.test_connection()
        self.assertTrue(success)
        self.assertIn("v5.2.4", msg)
        self.assertIn("WebAPI 2.15.1", msg)

    @patch("requests.Session.get")
    def test_connection_failure_401(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.headers = {}
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        success, msg = self.downloader.test_connection()
        self.assertFalse(success)
        self.assertIn("authentication failed", msg)

    @patch("requests.Session.post")
    def test_add_torrent_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Ok."
        mock_post.return_value = mock_resp

        magnet = "magnet:?xt=urn:btih:1234567890abcdef&dn=Test.Movie.2026"
        res = self.downloader.add_torrent(magnet, title="Test Movie")
        self.assertTrue(res.success)
        self.assertEqual(res.client, "qbittorrent")
        self.assertIn("Successfully sent download", res.message)

    @patch("requests.Session.post")
    def test_add_torrent_rejection(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Fails."
        mock_post.return_value = mock_resp

        magnet = "magnet:?xt=urn:btih:1234567890abcdef&dn=Test.Movie.2026"
        res = self.downloader.add_torrent(magnet, title="Test Movie")
        self.assertFalse(res.success)
        self.assertIn("rejected", res.message)


class TestQBittorrentWebIntegration(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_default_settings_include_qbittorrent(self):
        defaults = user_manager.get_default_settings("test_qbt_user")
        self.assertIn("qbittorrent_url", defaults)
        self.assertIn("qbittorrent_api_key", defaults)
        self.assertIn("qbittorrent_user", defaults)
        self.assertIn("qbittorrent_password", defaults)
        self.assertIn("qbittorrent_category", defaults)
        self.assertEqual(defaults["qbittorrent_api_key"], "")

    def test_get_user_downloader_qbittorrent(self):
        settings = {
            "download_client": "qbittorrent",
            "qbittorrent_url": "http://192.168.0.4:8080",
            "qbittorrent_api_key": "qbt_abc123",
            "qbittorrent_category": "movies",
            "qbittorrent_start_now": True
        }
        downloader = get_user_downloader(settings)
        self.assertIsInstance(downloader, QBittorrentDownloader)
        self.assertEqual(downloader.url, "http://192.168.0.4:8080")
        self.assertEqual(downloader.api_key, "qbt_abc123")


if __name__ == "__main__":
    unittest.main()
