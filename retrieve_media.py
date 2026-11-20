"""
retrieve_media.py - Downloader Integration Module

Dispatches download requests to the user's preferred media downloader:
- Radarr (Movies)
- Seerr / Overseerr / Jellyseerr (Requests)
- Sonarr (TV Shows)
- ruTorrent (Torrent Client)

Selection is configured via DOWNLOAD_CLIENT in .env or auto-detected based on available API keys.
"""

import argparse
import json
import logging
import os
import sys
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

import requests
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("retrieve_media")

# Load environment configuration
load_dotenv()


@dataclass
class DownloadResult:
    """Result returned by downloader implementations."""
    success: bool
    client: str
    title: str
    message: str
    media_id: Optional[Any] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "client": self.client,
            "title": self.title,
            "message": self.message,
            "media_id": self.media_id,
            "details": self.details,
        }


class BaseDownloader(ABC):
    """Abstract base class for all media download clients."""

    name: str = "base"

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """Test API reachability and authentication."""
        pass

    @abstractmethod
    def add_movie(
        self,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
        search_now: bool = True,
        **kwargs,
    ) -> DownloadResult:
        """Submit a movie download / monitor request."""
        pass

    @abstractmethod
    def add_tv(
        self,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
        search_now: bool = True,
        **kwargs,
    ) -> DownloadResult:
        """Submit a TV series download / monitor request."""
        pass


class RadarrDownloader(BaseDownloader):
    """
    Radarr v3 REST API implementation for managing movie downloads.
    Docs: https://radarr.video/docs/api/
    """

    name: str = "radarr"

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        root_folder: Optional[str] = None,
        quality_profile: Optional[str] = None,
    ):
        self.url = (url or os.getenv("RADARR_URL") or "http://localhost:7878").rstrip("/")
        self.api_key = (api_key or os.getenv("RADARR_API_KEY") or "").strip()
        self.root_folder = (root_folder or os.getenv("RADARR_ROOT_FOLDER") or "").strip()
        self.quality_profile = (quality_profile or os.getenv("RADARR_QUALITY_PROFILE") or "").strip()

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> requests.Response:
        full_url = f"{self.url}/api/v3/{endpoint.lstrip('/')}"
        return requests.get(full_url, headers=self.headers, params=params, timeout=timeout)

    def _api_post(self, endpoint: str, data: Dict[str, Any], timeout: int = 15) -> requests.Response:
        full_url = f"{self.url}/api/v3/{endpoint.lstrip('/')}"
        return requests.post(full_url, headers=self.headers, json=data, timeout=timeout)

    def test_connection(self) -> Tuple[bool, str]:
        """Verify Radarr URL and API key by calling /api/v3/system/status."""
        if not self.api_key:
            return False, "RADARR_API_KEY is not configured in .env"

        try:
            resp = self._api_get("system/status", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                version = data.get("version", "unknown")
                app_name = data.get("appName", "Radarr")
                return True, f"Successfully connected to {app_name} (version {version}) at {self.url}"
            elif resp.status_code == 401:
                return False, f"Radarr authentication failed (HTTP 401). Check RADARR_API_KEY."
            else:
                return False, f"Radarr responded with unexpected HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to Radarr at {self.url}. Ensure the server is running."
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def get_root_folders(self) -> List[Dict[str, Any]]:
        """Retrieve accessible root media folders configured in Radarr."""
        try:
            resp = self._api_get("rootfolder")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Failed to query Radarr root folders: {e}")
        return []

    def get_quality_profiles(self) -> List[Dict[str, Any]]:
        """Retrieve quality profiles configured in Radarr."""
        try:
            resp = self._api_get("qualityprofile")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Failed to query Radarr quality profiles: {e}")
        return []

    def lookup_movie(
        self,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Search Radarr for movie metadata via IMDb ID or Title."""
        term = None
        if imdb_id:
            term = f"imdb:{imdb_id}"
        elif title:
            term = title if not year else f"{title} {year}"

        if not term:
            return None

        try:
            resp = self._api_get("movie/lookup", params={"term": term})
            if resp.status_code == 200:
                movies = resp.json()
                if movies and isinstance(movies, list):
                    # If imdb_id was used, return exact match or first result
                    if imdb_id:
                        for m in movies:
                            if m.get("imdbId") == imdb_id:
                                return m
                    return movies[0]
        except Exception as e:
            logger.warning(f"Radarr movie lookup failed for term '{term}': {e}")
        return None

    def add_movie(
        self,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
        search_now: bool = True,
        **kwargs,
    ) -> DownloadResult:
        """Add movie to Radarr library and optionally initiate an automatic download search."""
        if not self.api_key:
            return DownloadResult(
                success=False,
                client=self.name,
                title=title or imdb_id or "Unknown",
                message="RADARR_API_KEY is not configured.",
            )

        # 1. Lookup movie metadata in Radarr
        movie = self.lookup_movie(imdb_id=imdb_id, title=title, year=year)
        if not movie:
            target_str = imdb_id or f"{title} ({year})" if year else (title or "Unknown")
            return DownloadResult(
                success=False,
                client=self.name,
                title=title or target_str,
                message=f"Movie not found in Radarr lookup for '{target_str}'.",
            )

        movie_title = movie.get("title", title or "Unknown")
        movie_year = movie.get("year", year)

        # 2. Check if movie already exists in Radarr library
        existing_id = movie.get("id")
        if existing_id and existing_id > 0:
            return DownloadResult(
                success=True,
                client=self.name,
                title=movie_title,
                message=f"Movie '{movie_title} ({movie_year})' is already in Radarr (ID: {existing_id}).",
                media_id=existing_id,
                details=movie,
            )

        # 3. Determine Root Folder Path
        root_path = self.root_folder
        if not root_path:
            root_folders = self.get_root_folders()
            if root_folders:
                # Prefer first accessible folder
                accessible = [f["path"] for f in root_folders if f.get("accessible", True)]
                root_path = accessible[0] if accessible else root_folders[0].get("path")

        if not root_path:
            return DownloadResult(
                success=False,
                client=self.name,
                title=movie_title,
                message="No root folder configured in Radarr. Set RADARR_ROOT_FOLDER in .env or configure Radarr.",
            )

        # 4. Determine Quality Profile ID
        profile_id = None
        profiles = self.get_quality_profiles()

        if self.quality_profile and profiles:
            # Match by name or ID
            for p in profiles:
                if str(p.get("id")) == self.quality_profile or p.get("name", "").lower() == self.quality_profile.lower():
                    profile_id = p.get("id")
                    break

        if not profile_id and profiles:
            # Fall back to HD-1080p, Any, or the first available profile
            preferred_names = ["hd-1080p", "hd", "any", "standard"]
            for pref in preferred_names:
                match = next((p for p in profiles if pref in p.get("name", "").lower()), None)
                if match:
                    profile_id = match.get("id")
                    break
            if not profile_id:
                profile_id = profiles[0].get("id")

        if not profile_id:
            profile_id = 1  # Default Radarr profile ID fallback

        # 5. Build Add Payload
        payload = {
            "title": movie.get("title"),
            "qualityProfileId": profile_id,
            "titleSlug": movie.get("titleSlug"),
            "images": movie.get("images", []),
            "tmdbId": movie.get("tmdbId"),
            "year": movie.get("year"),
            "rootFolderPath": root_path,
            "monitored": True,
            "minimumAvailability": "announced",
            "addOptions": {
                "searchForMovie": search_now,
            },
        }
        if movie.get("imdbId"):
            payload["imdbId"] = movie.get("imdbId")

        # 6. POST to Radarr /api/v3/movie
        try:
            resp = self._api_post("movie", payload)
            if resp.status_code in (200, 201):
                created = resp.json()
                new_id = created.get("id")
                search_note = " (automatic search triggered)" if search_now else ""
                return DownloadResult(
                    success=True,
                    client=self.name,
                    title=f"{movie_title} ({movie_year})",
                    message=f"Added '{movie_title} ({movie_year})' to Radarr successfully{search_note}.",
                    media_id=new_id,
                    details=created,
                )
            elif resp.status_code == 400:
                # Often occurs if movie was already added or path conflict
                err_text = resp.text
                try:
                    errors = resp.json()
                    if isinstance(errors, list) and errors:
                        err_text = errors[0].get("errorMessage", err_text)
                except Exception:
                    pass
                return DownloadResult(
                    success=False,
                    client=self.name,
                    title=movie_title,
                    message=f"Radarr rejected add request: {err_text}",
                )
            else:
                return DownloadResult(
                    success=False,
                    client=self.name,
                    title=movie_title,
                    message=f"Radarr error HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except Exception as e:
            return DownloadResult(
                success=False,
                client=self.name,
                title=movie_title,
                message=f"Failed to post movie to Radarr: {str(e)}",
            )

    def add_tv(self, *args, **kwargs) -> DownloadResult:
        """Radarr only manages movies."""
        return DownloadResult(
            success=False,
            client=self.name,
            title=kwargs.get("title", "Unknown"),
            message="Radarr only handles movies. Use Sonarr or Seerr for TV series.",
        )


class SeerrDownloader(BaseDownloader):
    """Placeholder for Overseerr / Jellyseerr integration (Movies & TV)."""
    name: str = "seerr"

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("SEERR_URL") or "http://localhost:5055").rstrip("/")
        self.api_key = (api_key or os.getenv("SEERR_API_KEY") or "").strip()

    def test_connection(self) -> Tuple[bool, str]:
        return False, "Seerr integration is not yet implemented. Use Radarr."

    def add_movie(self, *args, **kwargs) -> DownloadResult:
        return DownloadResult(
            success=False,
            client=self.name,
            title=kwargs.get("title", "Unknown"),
            message="Seerr integration is coming soon.",
        )

    def add_tv(self, *args, **kwargs) -> DownloadResult:
        return DownloadResult(
            success=False,
            client=self.name,
            title=kwargs.get("title", "Unknown"),
            message="Seerr integration is coming soon.",
        )


class SonarrDownloader(BaseDownloader):
    """Placeholder for Sonarr integration (TV Shows)."""
    name: str = "sonarr"

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("SONARR_URL") or "http://localhost:8989").rstrip("/")
        self.api_key = (api_key or os.getenv("SONARR_API_KEY") or "").strip()

    def test_connection(self) -> Tuple[bool, str]:
        return False, "Sonarr integration is not yet implemented. Use Radarr for movies."

    def add_movie(self, *args, **kwargs) -> DownloadResult:
        return DownloadResult(
            success=False,
            client=self.name,
            title=kwargs.get("title", "Unknown"),
            message="Sonarr only manages TV shows. Use Radarr for movies.",
        )

    def add_tv(self, *args, **kwargs) -> DownloadResult:
        return DownloadResult(
            success=False,
            client=self.name,
            title=kwargs.get("title", "Unknown"),
            message="Sonarr integration is coming soon.",
        )


class RuTorrentDownloader(BaseDownloader):
    """
    ruTorrent HTTP web & RPC implementation for managing torrent downloads.
    Communicates via HTTP Basic Auth to ruTorrent endpoints.
    """
    name: str = "rutorrent"

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        download_dir: Optional[str] = None,
        start_now: Optional[bool] = None,
    ):
        self.url = (url or os.getenv("RUTORRENT_URL") or "http://localhost/rutorrent").rstrip("/")
        self.username = (username or os.getenv("RUTORRENT_USER") or "").strip()
        self.password = (password or os.getenv("RUTORRENT_PASSWORD") or "").strip()
        self.download_dir = (download_dir or os.getenv("RUTORRENT_DOWNLOAD_DIR") or "").strip()
        start_now_env = os.getenv("RUTORRENT_START_NOW", "true").lower() in ("true", "1", "yes")
        self.start_now = start_now if start_now is not None else start_now_env

    @property
    def auth(self) -> Optional[requests.auth.HTTPBasicAuth]:
        if self.username and self.password:
            return requests.auth.HTTPBasicAuth(self.username, self.password)
        return None

    def test_connection(self) -> Tuple[bool, str]:
        """Test reachability and HTTP Basic Auth to ruTorrent."""
        if not self.url:
            return False, "RUTORRENT_URL is not configured in .env"

        try:
            resp = requests.get(self.url, auth=self.auth, timeout=10)
            if resp.status_code == 200:
                content = resp.text
                if "rutorrent" in content.lower() or "rtorrent" in content.lower():
                    return True, f"Successfully connected to ruTorrent at {self.url} (User: {self.username or 'anonymous'})"
                return True, f"Successfully connected to server at {self.url} (HTTP 200)"
            elif resp.status_code == 401:
                return False, "ruTorrent authentication failed (HTTP 401 Unauthorized). Check RUTORRENT_USER and RUTORRENT_PASSWORD."
            else:
                return False, f"ruTorrent server returned HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to ruTorrent at {self.url}. Ensure server is running and accessible."
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def add_torrent(
        self,
        torrent_url_or_magnet: str,
        title: Optional[str] = None,
        label: Optional[str] = None,
        dir_edit: Optional[str] = None,
        start_now: bool = True,
    ) -> DownloadResult:
        """Add a magnet link or .torrent URL to ruTorrent via /php/addtorrent.php."""
        if not torrent_url_or_magnet:
            return DownloadResult(
                success=False,
                client=self.name,
                title=title or "Unknown",
                message="No torrent URL or magnet link provided.",
            )

        endpoint = f"{self.url}/php/addtorrent.php"
        data = {
            "url": torrent_url_or_magnet,
        }
        if label:
            data["label"] = label
        # ruTorrent's php/addtorrent.php checks `!isset($_REQUEST['torrents_start_stopped'])`.
        # If the parameter is present in POST (even as "0"), PHP's isset() returns true,
        # which causes ruTorrent to load the torrent in a STOPPED state.
        # To auto-start, we omit the key completely.
        if not start_now:
            data["torrents_start_stopped"] = "1"
        dest_dir = dir_edit or self.download_dir
        if dest_dir:
            data["dir_edit"] = dest_dir

        try:
            resp = requests.post(endpoint, data=data, auth=self.auth, timeout=15)
            if resp.status_code == 200:
                resp_text = resp.text
                if "Failed" in resp_text or "Error" in resp_text:
                    return DownloadResult(
                        success=False,
                        client=self.name,
                        title=title or "Unknown",
                        message=f"ruTorrent rejected torrent: {resp_text[:200]}",
                    )
                return DownloadResult(
                    success=True,
                    client=self.name,
                    title=title or "Torrent",
                    message=f"Successfully sent download to ruTorrent ({title or 'Torrent'}).",
                    details={"response": resp_text[:200]},
                )
            else:
                return DownloadResult(
                    success=False,
                    client=self.name,
                    title=title or "Unknown",
                    message=f"ruTorrent error HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except Exception as e:
            return DownloadResult(
                success=False,
                client=self.name,
                title=title or "Unknown",
                message=f"Failed to submit torrent to ruTorrent: {str(e)}",
            )

    def lookup_magnet(self, imdb_id: Optional[str] = None, title: Optional[str] = None) -> Optional[str]:
        """Auto-search indexer for magnet link if not provided directly."""
        query = imdb_id or title
        if not query:
            return None
        url = f"https://apibay.org/q.php?q={quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if isinstance(data, list) and data:
                    valid = [t for t in data if t.get("id") != "0" and t.get("info_hash")]
                    if valid:
                        best = max(valid, key=lambda x: int(x.get("seeders", 0)))
                        hash_val = best["info_hash"]
                        name = best.get("name", "release")
                        trackers = "&tr=udp%3A%2F%2Ftracker.coppersurfer.tk%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A6969%2Fannounce"
                        return f"magnet:?xt=urn:btih:{hash_val}&dn={quote(name)}{trackers}"
        except Exception as e:
            logger.warning(f"Failed to lookup magnet for '{query}': {e}")
        return None

    def add_movie(self, *args, **kwargs) -> DownloadResult:
        magnet = kwargs.get("magnet") or kwargs.get("url")
        title = kwargs.get("title") or kwargs.get("imdb_id") or "Movie"
        if not magnet:
            logger.info(f"ruTorrent: Looking up magnet for '{title}'...")
            magnet = self.lookup_magnet(imdb_id=kwargs.get("imdb_id"), title=kwargs.get("title"))

        if magnet:
            return self.add_torrent(
                magnet,
                title=title,
                label=kwargs.get("label"),
                start_now=self.start_now,
            )
        return DownloadResult(
            success=False,
            client=self.name,
            title=title,
            message=f"Could not find a torrent magnet link for '{title}'.",
        )

    def add_tv(self, *args, **kwargs) -> DownloadResult:
        magnet = kwargs.get("magnet") or kwargs.get("url")
        title = kwargs.get("title") or kwargs.get("imdb_id") or "Series"
        if not magnet:
            logger.info(f"ruTorrent: Looking up magnet for '{title}'...")
            magnet = self.lookup_magnet(imdb_id=kwargs.get("imdb_id"), title=kwargs.get("title"))

        if magnet:
            return self.add_torrent(
                magnet,
                title=title,
                label=kwargs.get("label"),
                start_now=self.start_now,
            )
        return DownloadResult(
            success=False,
            client=self.name,
            title=title,
            message=f"Could not find a torrent magnet link for '{title}'.",
        )


# Available client registry
CLIENT_REGISTRY = {
    "radarr": RadarrDownloader,
    "seerr": SeerrDownloader,
    "overseerr": SeerrDownloader,
    "jellyseerr": SeerrDownloader,
    "sonarr": SonarrDownloader,
    "rutorrent": RuTorrentDownloader,
}


def get_downloader(client_name: Optional[str] = None) -> BaseDownloader:
    """
    Factory function returning the configured downloader instance.
    Determines client based on:
    1. Explicit client_name parameter
    2. DOWNLOAD_CLIENT env var
    3. Auto-detected based on which API key is set (RADARR_API_KEY, SEERR_API_KEY, etc.)
    """
    if not client_name:
        client_name = os.getenv("DOWNLOAD_CLIENT")

    if not client_name:
        # Auto-detect based on configured keys
        if os.getenv("RADARR_API_KEY"):
            client_name = "radarr"
        elif os.getenv("SEERR_API_KEY"):
            client_name = "seerr"
        elif os.getenv("SONARR_API_KEY"):
            client_name = "sonarr"
        elif os.getenv("RUTORRENT_URL"):
            client_name = "rutorrent"
        else:
            client_name = "radarr"  # Default

    client_key = client_name.strip().lower()
    downloader_cls = CLIENT_REGISTRY.get(client_key)
    if not downloader_cls:
        logger.warning(f"Unknown download client '{client_name}', defaulting to 'radarr'.")
        downloader_cls = RadarrDownloader

    return downloader_cls()


class DownloaderRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests from the media report dashboard."""
    downloader: Optional[BaseDownloader] = None

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/status":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            downloader = self.downloader or get_downloader()
            ok, msg = downloader.test_connection()
            resp_data = {
                "status": "ok" if ok else "error",
                "client": downloader.name,
                "client_display": {"radarr": "Radarr", "seerr": "Seerr", "sonarr": "Sonarr", "rutorrent": "ruTorrent"}.get(downloader.name.lower(), downloader.name.capitalize()),
                "connected": ok,
                "message": msg
            }
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        elif path == "/api/request":
            self._handle_request(query)
            return

        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            try:
                body = self.rfile.read(content_len).decode("utf-8")
                body_data = json.loads(body)
                for k, v in body_data.items():
                    query[k] = [str(v)]
            except Exception:
                pass

        if path == "/api/request":
            self._handle_request(query)
            return

        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()

    def _handle_request(self, query: Dict[str, List[str]]):
        imdb_id = query.get("imdb", [None])[0] or query.get("imdb_id", [None])[0]
        title = query.get("title", [None])[0]
        year_str = query.get("year", [None])[0]
        year = int(year_str) if year_str and year_str.isdigit() else None
        media_type = query.get("type", ["movie"])[0]
        magnet = query.get("magnet", [None])[0]

        downloader = self.downloader or get_downloader()
        if media_type == "tv":
            res = downloader.add_tv(imdb_id=imdb_id, title=title, year=year, magnet=magnet)
        else:
            res = downloader.add_movie(imdb_id=imdb_id, title=title, year=year, magnet=magnet)

        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(res.to_dict()).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"Downloader API: {format % args}")


def run_server(port: int = 7890, client_name: Optional[str] = None):
    """Run lightweight local downloader webhook server."""
    downloader = get_downloader(client_name)
    DownloaderRequestHandler.downloader = downloader

    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, DownloaderRequestHandler)
    client_display = {"radarr": "Radarr", "seerr": "Seerr", "sonarr": "Sonarr", "rutorrent": "ruTorrent"}.get(downloader.name.lower(), downloader.name.capitalize())
    logger.info(f"Downloader 1-click API server listening on http://127.0.0.1:{port} (Client: {client_display})")
    logger.info("Keep this running to enable 1-click downloads directly from your media report.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping downloader API server.")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="Media Downloader CLI Interface")
    parser.add_argument("--client", choices=["radarr", "seerr", "sonarr", "rutorrent"], help="Override download client")
    parser.add_argument("--test", action="store_true", help="Test connection to configured client")
    parser.add_argument("--serve", action="store_true", help="Run local API server for 1-click report downloads (port 7890)")
    parser.add_argument("--port", type=int, default=7890, help="Server port (default: 7890)")
    parser.add_argument("--imdb", help="IMDb title ID to download (e.g. tt1234567)")
    parser.add_argument("--title", help="Movie or show title")
    parser.add_argument("--year", type=int, help="Release year")
    parser.add_argument("--magnet", help="Torrent magnet URI or .torrent file URL (for ruTorrent)")
    parser.add_argument("--type", choices=["movie", "tv"], default="movie", help="Media type (default: movie)")
    parser.add_argument("--no-search", action="store_true", help="Monitor only without immediate search")

    args = parser.parse_args()
    downloader = get_downloader(args.client)

    if args.serve:
        run_server(port=args.port, client_name=args.client)
        sys.exit(0)

    if args.test:
        logger.info(f"Testing connection for client '{downloader.name}'...")
        ok, msg = downloader.test_connection()
        if ok:
            logger.info(f"SUCCESS: {msg}")
            sys.exit(0)
        else:
            logger.error(f"FAILURE: {msg}")
            sys.exit(1)

    if not args.imdb and not args.title and not args.magnet:
        parser.print_help()
        print("\nError: Please provide --test or media identifiers (--imdb, --title, or --magnet).")
        sys.exit(1)

    search_now = not args.no_search
    if args.type == "tv":
        res = downloader.add_tv(imdb_id=args.imdb, title=args.title, year=args.year, search_now=search_now, magnet=args.magnet)
    else:
        res = downloader.add_movie(imdb_id=args.imdb, title=args.title, year=args.year, search_now=search_now, magnet=args.magnet)

    if res.success:
        logger.info(f"SUCCESS [{res.client}]: {res.message}")
        sys.exit(0)
    else:
        logger.error(f"FAILED [{res.client}]: {res.message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
