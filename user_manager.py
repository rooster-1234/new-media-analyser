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
    """Creates initial default settings based on current system environment."""
    return {
        "username": username,
        # Media Server Settings
        "media_server": os.getenv("MEDIA_SERVER", "plex").lower(),
        "plex_url": os.getenv("PLEX_URL", "http://localhost:32400"),
        "plex_token": os.getenv("PLEX_TOKEN", ""),
        "plex_movie_libraries": os.getenv("PLEX_MOVIE_LIBRARIES", ""),
        "plex_tv_libraries": os.getenv("PLEX_TV_LIBRARIES", ""),
        "jellyfin_url": os.getenv("JELLYFIN_URL", "http://localhost:8096"),
        "jellyfin_key": os.getenv("JELLYFIN_KEY", ""),
        "emby_url": os.getenv("EMBY_URL", "http://localhost:8096"),
        "emby_key": os.getenv("EMBY_KEY", ""),
        # Downloader Settings
        "download_client": os.getenv("DOWNLOAD_CLIENT", "radarr").lower(),
        "radarr_url": os.getenv("RADARR_URL", "http://localhost:7878"),
        "radarr_api_key": os.getenv("RADARR_API_KEY", ""),
        "radarr_root_folder": os.getenv("RADARR_ROOT_FOLDER", ""),
        "radarr_quality_profile": os.getenv("RADARR_QUALITY_PROFILE", ""),
        "rutorrent_url": os.getenv("RUTORRENT_URL", "http://localhost/rutorrent"),
        "rutorrent_user": os.getenv("RUTORRENT_USER", ""),
        "rutorrent_password": os.getenv("RUTORRENT_PASSWORD", ""),
        "rutorrent_start_now": os.getenv("RUTORRENT_START_NOW", "true").lower() in ("true", "1", "yes"),
        # Scraper URLs
        "site_scan1": os.getenv("SITE_SCAN1") or os.getenv("SITE_SCAN") or "https://thepiratebay.org/search.php?q=top100:48h_207",
        "site_scan2": os.getenv("SITE_SCAN2") or "https://uindex.org/top.php?c=1&t=7d",
        "site_scan3": os.getenv("SITE_SCAN3", ""),
        # Filter Criteria
        "filter_exclude_existing": os.getenv("FILTER_EXCLUDE_EXISTING", "true").lower() in ("true", "1", "yes"),
        "filter_only_current_year": os.getenv("FILTER_ONLY_CURRENT_YEAR", "true").lower() in ("true", "1", "yes"),
        "filter_target_year": int(os.getenv("FILTER_TARGET_YEAR", "2026")),
        "filter_min_imdb_score": float(os.getenv("FILTER_MIN_IMDB_SCORE", "5.0")),
        "filter_require_english": os.getenv("FILTER_REQUIRE_ENGLISH", "true").lower() in ("true", "1", "yes"),
        "filter_disliked_genres": [g.strip() for g in os.getenv("FILTER_DISLIKED_GENRES", "Fantasy, Romance").split(",") if g.strip()],
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
