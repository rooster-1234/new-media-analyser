"""
Unified Media Server Provider Module
Supports Plex, Jellyfin, and Emby media servers for library catalog analysis,
duplicate detection indexing, and genre preference profile extraction.
"""

import os
import re
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

# Automatically load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy/clean matching against lookup indices."""
    if not title:
        return ""
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


class BaseMediaServer(ABC):
    """Abstract base class for media server integrations."""

    name: str = "base"

    @abstractmethod
    def connect(self) -> bool:
        """Test connection and authentication to the media server."""
        pass

    @abstractmethod
    def generate_profile(self) -> Dict[str, Any]:
        """Scan the media library and return a structured library taste & duplicate profile."""
        pass


class PlexServerProvider(BaseMediaServer):
    """Plex Media Server integration via plexapi."""

    name: str = "plex"

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        self.url = (url or os.getenv("PLEX_URL") or "http://localhost:32400").rstrip("/")
        self.token = token or os.getenv("PLEX_TOKEN") or ""
        self.server = None

    def connect(self) -> bool:
        try:
            from plexapi.server import PlexServer
            self.server = PlexServer(self.url, self.token, timeout=15)
            logger.info(f"Connected to Plex Server '{self.server.friendlyName}' (v{self.server.version})")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Plex Server at {self.url}: {e}")
            return False

    def generate_profile(self) -> Dict[str, Any]:
        from plex_analyzer import PlexLibraryAnalyzer
        analyzer = PlexLibraryAnalyzer(base_url=self.url, token=self.token)
        if not analyzer.connect():
            raise RuntimeError(f"Could not connect to Plex at {self.url}")
        return analyzer.generate_profile()


class JellyfinServerProvider(BaseMediaServer):
    """Jellyfin Media Server integration via REST API."""

    name: str = "jellyfin"

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("JELLYFIN_URL") or os.getenv("JELLTFIN_URL") or "http://localhost:8096").rstrip("/")
        self.api_key = api_key or os.getenv("JELLYFIN_KEY") or os.getenv("JELLYFIN_API_KEY") or ""
        self.server_name = "Jellyfin"
        self.server_version = ""

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f'MediaBrowser Token="{self.api_key}"',
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
        }

    def connect(self) -> bool:
        if not self.url or not self.api_key:
            logger.error("JELLYFIN_URL or JELLYFIN_KEY is not set.")
            return False
        try:
            resp = requests.get(f"{self.url}/System/Info", headers=self.headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                self.server_name = data.get("ServerName", "Jellyfin")
                self.server_version = data.get("Version", "")
                logger.info(f"Connected to Jellyfin Server '{self.server_name}' (v{self.server_version})")
                return True
            logger.error(f"Jellyfin connection failed: HTTP {resp.status_code} ({resp.text[:100]})")
            return False
        except Exception as e:
            logger.error(f"Cannot connect to Jellyfin at {self.url}: {e}")
            return False

    def generate_profile(self) -> Dict[str, Any]:
        logger.info(f"Scanning Jellyfin library at {self.url}...")

        # 1. Fetch Movies
        mov_resp = requests.get(
            f"{self.url}/Items?IncludeItemTypes=Movie&Recursive=true&Fields=Genres,ProductionYear,UserData,People,CommunityRating",
            headers=self.headers,
            timeout=30,
        )
        mov_resp.raise_for_status()
        movies_raw = mov_resp.json().get("Items", [])

        # 2. Fetch Series
        series_resp = requests.get(
            f"{self.url}/Items?IncludeItemTypes=Series&Recursive=true&Fields=Genres,ProductionYear,UserData,CommunityRating",
            headers=self.headers,
            timeout=30,
        )
        series_resp.raise_for_status()
        series_raw = series_resp.json().get("Items", [])

        # 3. Fetch all seasons in 1 fast batch request
        seasons_map = _fetch_all_seasons_map(self.url, self.headers)

        # Build analysis data
        return _build_generic_profile(
            server_type="jellyfin",
            server_name=self.server_name,
            movies_raw=movies_raw,
            series_raw=series_raw,
            seasons_map=seasons_map,
        )


class EmbyServerProvider(BaseMediaServer):
    """Emby Media Server integration via REST API."""

    name: str = "emby"

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("EMBY_URL") or "http://localhost:8096").rstrip("/")
        self.api_key = api_key or os.getenv("EMBY_KEY") or os.getenv("EMBY_API_KEY") or ""
        self.server_name = "Emby"
        self.server_version = ""

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
        }

    def connect(self) -> bool:
        if not self.url or not self.api_key:
            logger.error("EMBY_URL or EMBY_KEY is not set.")
            return False
        try:
            resp = requests.get(f"{self.url}/System/Info", headers=self.headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                self.server_name = data.get("ServerName", "Emby")
                self.server_version = data.get("Version", "")
                logger.info(f"Connected to Emby Server '{self.server_name}' (v{self.server_version})")
                return True
            logger.error(f"Emby connection failed: HTTP {resp.status_code} ({resp.text[:100]})")
            return False
        except Exception as e:
            logger.error(f"Cannot connect to Emby at {self.url}: {e}")
            return False

    def generate_profile(self) -> Dict[str, Any]:
        logger.info(f"Scanning Emby library at {self.url}...")

        # 1. Fetch Movies
        mov_resp = requests.get(
            f"{self.url}/Items?IncludeItemTypes=Movie&Recursive=true&Fields=Genres,ProductionYear,UserData,People,CommunityRating",
            headers=self.headers,
            timeout=30,
        )
        mov_resp.raise_for_status()
        movies_raw = mov_resp.json().get("Items", [])

        # 2. Fetch Series
        series_resp = requests.get(
            f"{self.url}/Items?IncludeItemTypes=Series&Recursive=true&Fields=Genres,ProductionYear,UserData,CommunityRating",
            headers=self.headers,
            timeout=30,
        )
        series_resp.raise_for_status()
        series_raw = series_resp.json().get("Items", [])

        # 3. Fetch all seasons in 1 fast batch request
        seasons_map = _fetch_all_seasons_map(self.url, self.headers)

        # Build analysis data
        return _build_generic_profile(
            server_type="emby",
            server_name=self.server_name,
            movies_raw=movies_raw,
            series_raw=series_raw,
            seasons_map=seasons_map,
        )


class CombinedServerProvider(BaseMediaServer):
    """Combines libraries from all configured servers (Plex, Jellyfin, Emby)."""

    name: str = "all"

    def __init__(self):
        self.providers: List[BaseMediaServer] = []
        if os.getenv("PLEX_TOKEN"):
            self.providers.append(PlexServerProvider())
        if os.getenv("JELLYFIN_KEY"):
            self.providers.append(JellyfinServerProvider())
        if os.getenv("EMBY_KEY"):
            self.providers.append(EmbyServerProvider())

    def connect(self) -> bool:
        connected_any = False
        for p in self.providers:
            if p.connect():
                connected_any = True
        return connected_any

    def generate_profile(self) -> Dict[str, Any]:
        combined_movies: Dict[str, Any] = {}
        combined_shows: Dict[str, Any] = {}
        combined_categories: Counter = Counter()
        combined_top_level: Counter = Counter()
        combined_directors: Counter = Counter()

        total_movies = 0
        total_shows = 0
        connected_names = []

        for p in self.providers:
            try:
                if p.connect():
                    prof = p.generate_profile()
                    connected_names.append(f"{p.name.capitalize()} ({prof.get('metadata', {}).get('server_name', p.name)})")
                    indices = prof.get("lookup_indices", {})

                    # Merge movies
                    for k, m in indices.get("movies", {}).items():
                        if k not in combined_movies:
                            combined_movies[k] = m
                            total_movies += 1

                    # Merge shows
                    for k, s in indices.get("shows", {}).items():
                        if k not in combined_shows:
                            combined_shows[k] = s
                            total_shows += 1
                        else:
                            # Merge existing season numbers
                            existing = set(combined_shows[k].get("existing_season_numbers", []))
                            new_seasons = set(s.get("existing_season_numbers", []))
                            combined_shows[k]["existing_season_numbers"] = sorted(list(existing | new_seasons))

                    # Merge categories
                    taste = prof.get("taste_profile", {})
                    for cat, count in (taste.get("categories") or {}).items():
                        combined_categories[cat] += count
                    for g, count in (taste.get("top_level_genres") or {}).items():
                        combined_top_level[g] += count
                    for d, count in (taste.get("top_directors") or {}).items():
                        combined_directors[d] += count
            except Exception as e:
                logger.warning(f"Failed to scan {p.name}: {e}")

        return {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "server_type": "all",
                "server_name": " + ".join(connected_names) or "Combined Media Servers",
            },
            "summary": {
                "total_movies": total_movies,
                "total_shows": total_shows,
            },
            "taste_profile": {
                "top_level_genres": dict(combined_top_level.most_common(15)),
                "categories": dict(combined_categories.most_common(30)),
                "top_directors": dict(combined_directors.most_common(15)),
            },
            "lookup_indices": {
                "movies": combined_movies,
                "shows": combined_shows,
            },
        }


def _fetch_all_seasons_map(url: str, headers: dict) -> Dict[str, List[int]]:
    """Fetch all TV seasons across the entire library in a single batch request."""
    seasons_by_show: Dict[str, List[int]] = {}
    try:
        resp = requests.get(
            f"{url}/Items?IncludeItemTypes=Season&Recursive=true&Fields=SeriesId,IndexNumber",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("Items", []):
                series_id = item.get("SeriesId")
                index_num = item.get("IndexNumber")
                if series_id and index_num is not None:
                    if series_id not in seasons_by_show:
                        seasons_by_show[series_id] = []
                    seasons_by_show[series_id].append(index_num)
    except Exception as e:
        logger.debug(f"Batch season fetch failed: {e}")
    return seasons_by_show


def _build_generic_profile(
    server_type: str,
    server_name: str,
    movies_raw: List[dict],
    series_raw: List[dict],
    seasons_map: Optional[Dict[str, List[int]]] = None,
) -> Dict[str, Any]:
    """Common parser for Jellyfin & Emby REST responses into the standard profile structure."""
    if seasons_map is None:
        seasons_map = {}
    movie_lookup: Dict[str, dict] = {}
    show_lookup: Dict[str, dict] = {}
    categories_counter: Counter = Counter()
    top_level_counter: Counter = Counter()
    directors_counter: Counter = Counter()

    watched_movies = 0

    # Process Movies
    for item in movies_raw:
        title = item.get("Name", "").strip()
        year = item.get("ProductionYear")
        genres = item.get("Genres") or []
        norm = normalize_title(title)
        if not norm:
            continue

        user_data = item.get("UserData") or {}
        is_watched = user_data.get("Played", False)
        if is_watched:
            watched_movies += 1

        primary_genre = genres[0] if genres else "Unknown"
        sub_genres = genres[1:] if len(genres) > 1 else []

        for g in genres:
            categories_counter[g] += 1
        if genres:
            top_level_counter[primary_genre] += 1

        # Extract directors if present
        people = item.get("People") or []
        for p in people:
            if p.get("Type") == "Director":
                d_name = p.get("Name")
                if d_name:
                    directors_counter[d_name] += 1

        movie_lookup[norm] = {
            "year": year,
            "title": title,
            "genres": genres,
            "categories": genres,
            "primary_genre": primary_genre,
            "primary_category": primary_genre,
            "sub_genres": sub_genres,
            "sub_categories": sub_genres,
            "watched": is_watched,
        }

    # Process TV Series
    for item in series_raw:
        title = item.get("Name", "").strip()
        year = item.get("ProductionYear")
        genres = item.get("Genres") or []
        series_id = item.get("Id")
        norm = normalize_title(title)
        if not norm:
            continue

        primary_genre = genres[0] if genres else "Unknown"
        sub_genres = genres[1:] if len(genres) > 1 else []

        for g in genres:
            categories_counter[g] += 1
        if genres:
            top_level_counter[primary_genre] += 1

        seasons = seasons_map.get(series_id, [])

        show_lookup[norm] = {
            "year": year,
            "title": title,
            "genres": genres,
            "categories": genres,
            "primary_genre": primary_genre,
            "primary_category": primary_genre,
            "sub_genres": sub_genres,
            "sub_categories": sub_genres,
            "existing_season_numbers": seasons,
        }

    return {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "server_type": server_type,
            "server_name": server_name,
        },
        "summary": {
            "total_movies": len(movie_lookup),
            "watched_movies": watched_movies,
            "total_shows": len(show_lookup),
        },
        "taste_profile": {
            "top_level_genres": dict(top_level_counter.most_common(15)),
            "categories": dict(categories_counter.most_common(30)),
            "top_directors": dict(directors_counter.most_common(15)),
        },
        "lookup_indices": {
            "movies": movie_lookup,
            "shows": show_lookup,
        },
    }


def get_media_server_provider(server_type: Optional[str] = None) -> BaseMediaServer:
    """
    Factory function returning the configured media server provider.
    Reads MEDIA_SERVER from environment (plex, jellyfin, emby, all). Defaults to plex.
    """
    stype = (server_type or os.getenv("MEDIA_SERVER") or "plex").strip().lower()

    if stype == "jellyfin":
        return JellyfinServerProvider()
    elif stype == "emby":
        return EmbyServerProvider()
    elif stype in ("all", "combined"):
        return CombinedServerProvider()
    else:
        return PlexServerProvider()
