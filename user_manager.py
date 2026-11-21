"""
User and Profile Configuration Manager for New Media Analyser
Stores user accounts in a flat file (data/users.json) and per-user
settings/profiles in data/profiles/<username>_settings.json.
"""

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# Ensure environment variables are loaded for defaults
load_dotenv()

DATA_DIR = Path("data")
PROFILES_DIR = DATA_DIR / "profiles"
USERS_FILE = DATA_DIR / "users.json"

# In-memory session token store (token -> username)
active_sessions: Dict[str, str] = {}


def ensure_directories():
    """Ensure data and profiles directories exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hashes a password with a cryptographic salt."""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verifies a password against hash and salt."""
    check, _ = hash_password(password, salt)
    return secrets.compare_digest(check, hashed)


def load_users() -> Dict[str, Dict[str, Any]]:
    """Loads all registered users from users.json."""
    ensure_directories()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users: Dict[str, Dict[str, Any]]):
    """Saves user registry to users.json."""
    ensure_directories()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def register_user(username: str, password: str) -> tuple[bool, str]:
    """Registers a new user and creates their default profile."""
    username = username.strip().lower()
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters."
    if not password or len(password) < 4:
        return False, "Password must be at least 4 characters."

    users = load_users()
    if username in users:
        return False, "Username already exists. Please choose another."

    pwd_hash, salt = hash_password(password)
    users[username] = {
        "username": username,
        "password_hash": pwd_hash,
        "salt": salt,
        "created_at": secrets.token_hex(4)
    }
    save_users(users)

    # Initialize user profile with defaults from .env if available
    default_settings = get_default_settings(username)
    save_user_settings(username, default_settings)
    return True, "User registered successfully."


def authenticate_user(username: str, password: str) -> tuple[bool, Optional[str]]:
    """Validates login credentials and returns a session token."""
    username = username.strip().lower()
    users = load_users()
    user = users.get(username)
    if not user:
        return False, None

    if verify_password(password, user["password_hash"], user["salt"]):
        token = secrets.token_hex(24)
        active_sessions[token] = username
        return True, token
    return False, None


def get_user_from_token(token: Optional[str]) -> Optional[str]:
    """Retrieves username from active session token."""
    if not token:
        return None
    return active_sessions.get(token)


def logout_token(token: Optional[str]):
    """Removes a session token."""
    if token and token in active_sessions:
        del active_sessions[token]


def get_default_settings(username: str) -> Dict[str, Any]:
    """Creates blank initial settings for a newly registered user (never exposes server credentials)."""
    return {
        "username": username,
        # Media Server Settings (always blank for new users)
        "media_server": "plex",
        "plex_url": "http://localhost:32400",
        "plex_token": "",
        "plex_movie_libraries": "",
        "plex_tv_libraries": "",
        "jellyfin_url": "http://localhost:8096",
        "jellyfin_key": "",
        "emby_url": "http://localhost:8096",
        "emby_key": "",
        # Downloader Settings (always blank for new users)
        "download_client": "radarr",
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "",
        "radarr_root_folder": "",
        "radarr_quality_profile": "",
        "rutorrent_url": "",
        "rutorrent_user": "",
        "rutorrent_password": "",
        "rutorrent_start_now": True,
        "qbittorrent_url": "http://localhost:8080",
        "qbittorrent_api_key": "",
        "qbittorrent_user": "",
        "qbittorrent_password": "",
        "qbittorrent_proxy_user": "",
        "qbittorrent_proxy_password": "",
        "qbittorrent_save_path": "",
        "qbittorrent_category": "movies",
        "qbittorrent_start_now": True,
        # Scraper Sources
        "site_scan1": os.getenv("SITE_SCAN1") or os.getenv("SITE_SCAN") or "https://thepiratebay.org/search.php?q=top100:48h_207",
        "site_scan2": os.getenv("SITE_SCAN2") or "https://uindex.org/top.php?c=1&t=7d",
        "site_scan3": os.getenv("SITE_SCAN3", ""),
        # Filter Criteria
        "filter_exclude_existing": True,
        "filter_timeframe": "365d",
        "filter_only_current_year": True,
        "filter_target_year": 2026,
        "filter_min_imdb_score": 5.0,
        "filter_require_english": True,
        "filter_disliked_genres": ["Fantasy", "Romance"],
        # Paths
        "profile_file": str(PROFILES_DIR / f"{username}_library_profile.json"),
        "last_scan_time": None
    }


def get_user_settings(username: str) -> Dict[str, Any]:
    """Loads user configuration file."""
    ensure_directories()
    settings_file = PROFILES_DIR / f"{username}_settings.json"
    if not settings_file.exists():
        settings = get_default_settings(username)
        save_user_settings(username, settings)
        return settings

    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure all default keys exist
            defaults = get_default_settings(username)
            defaults.update(data)
            return defaults
    except Exception:
        return get_default_settings(username)


def save_user_settings(username: str, settings: Dict[str, Any]):
    """Saves user configuration file."""
    ensure_directories()
    settings["username"] = username
    settings["profile_file"] = str(PROFILES_DIR / f"{username}_library_profile.json")
    settings_file = PROFILES_DIR / f"{username}_settings.json"
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# Initialize directories on module load
ensure_directories()
