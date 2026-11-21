"""
New Media Analyser - Modern Multi-User Web Application
Built with FastAPI. Provides:
1. Welcome page with Login & Registration (flat-file auth in data/users.json).
2. Configuration page (/settings) for Media Servers (Plex/Jellyfin/Emby) & Downloaders (Radarr/ruTorrent).
3. Dashboard (/) with the 'Scan Releases Now' (GO) button and personalized release monitoring.
4. Downloader request API (/api/request) dispatching to the active user's download client.
"""

import asyncio
import html
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import user_manager
from filter import MediaFilter, parse_release_title_and_year, normalize_title
from media_servers import PlexServerProvider, JellyfinServerProvider, EmbyServerProvider, CombinedServerProvider
from retrieve_media import RadarrDownloader, RuTorrentDownloader, QBittorrentDownloader, DownloadResult
import scraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("app")

app = FastAPI(title="New Media Analyser", version="0.4.0")

DATA_DIR = Path("data")
PROFILES_DIR = DATA_DIR / "profiles"
CACHE_FILE = DATA_DIR / "scraped_releases_cache.json"

# In-memory scan status tracking
scan_lock = asyncio.Lock()
scan_state = {
    "is_scanning": False,
    "progress_message": "Idle",
    "last_scan_time": None,
    "total_items": 0
}

# In-memory store for active Plex OAuth PIN sessions (pin_id -> MyPlexPinLogin)
active_plex_pins: Dict[str, Any] = {}


def get_current_user(request: Request) -> Optional[str]:
    """Helper to retrieve authenticated username from session cookie."""
    token = request.cookies.get("session_token")
    return user_manager.get_user_from_token(token)


def require_user(request: Request) -> str:
    """Dependency that raises 401 if user is not authenticated."""
    username = get_current_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return username


def load_cached_raw_releases() -> List[Dict[str, Any]]:
    """Loads cached raw releases from disk if available."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read release cache: {e}")
    return []


def save_cached_raw_releases(items: List[Dict[str, Any]]):
    """Saves raw releases to shared disk cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save release cache: {e}")


def get_user_media_filter(username: str) -> MediaFilter:
    """Instantiates a MediaFilter customized to the user's library and settings."""
    settings = user_manager.get_user_settings(username)
    profile_path = settings.get("profile_file") or str(PROFILES_DIR / f"{username}_library_profile.json")

    flt = MediaFilter(profile_path=profile_path)
    flt.exclude_existing = settings.get("filter_exclude_existing", True)
    flt.timeframe = settings.get("filter_timeframe", "365d")
    flt.only_current_year = settings.get("filter_only_current_year", True) if "filter_only_current_year" in settings else (flt.timeframe != "all")
    flt.target_year = int(settings.get("filter_target_year", 2026))
    flt.min_imdb_score = float(settings.get("filter_min_imdb_score", 5.0))
    flt.require_english = settings.get("filter_require_english", True)

    raw_disliked = settings.get("filter_disliked_genres", [])
    if isinstance(raw_disliked, str):
        flt.disliked_genres = [g.strip().lower() for g in raw_disliked.split(",") if g.strip()]
    else:
        flt.disliked_genres = [str(g).strip().lower() for g in raw_disliked if str(g).strip()]

    return flt


def get_user_downloader(settings: Dict[str, Any]):
    """Instantiates the user's configured download client."""
    client_name = (settings.get("download_client") or "radarr").lower()
    if client_name == "radarr":
        return RadarrDownloader(
            url=settings.get("radarr_url"),
            api_key=settings.get("radarr_api_key"),
            root_folder=settings.get("radarr_root_folder"),
            quality_profile=settings.get("radarr_quality_profile"),
        )
    elif client_name == "rutorrent":
        return RuTorrentDownloader(
            url=settings.get("rutorrent_url"),
            username=settings.get("rutorrent_user"),
            password=settings.get("rutorrent_password"),
            start_now=settings.get("rutorrent_start_now", True),
        )
    elif client_name in ("qbittorrent", "qbit"):
        return QBittorrentDownloader(
            url=settings.get("qbittorrent_url"),
            api_key=settings.get("qbittorrent_api_key"),
            username=settings.get("qbittorrent_user"),
            password=settings.get("qbittorrent_password"),
            proxy_user=settings.get("qbittorrent_proxy_user"),
            proxy_password=settings.get("qbittorrent_proxy_password"),
            save_path=settings.get("qbittorrent_save_path"),
            category=settings.get("qbittorrent_category", "movies"),
            start_now=settings.get("qbittorrent_start_now", True),
        )
    return RadarrDownloader(url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))


# -----------------------------------------------------------------------------
# Request Models
# -----------------------------------------------------------------------------
class AuthModel(BaseModel):
    username: str
    password: str


class SettingsUpdateModel(BaseModel):
    media_server: Optional[str] = "plex"
    plex_url: Optional[str] = "http://localhost:32400"
    plex_token: Optional[str] = ""
    plex_movie_libraries: Optional[str] = ""
    plex_tv_libraries: Optional[str] = ""
    jellyfin_url: Optional[str] = "http://localhost:8096"
    jellyfin_key: Optional[str] = ""
    emby_url: Optional[str] = "http://localhost:8096"
    emby_key: Optional[str] = ""
    download_client: Optional[str] = "radarr"
    radarr_url: Optional[str] = "http://localhost:7878"
    radarr_api_key: Optional[str] = ""
    radarr_root_folder: Optional[str] = ""
    radarr_quality_profile: Optional[str] = ""
    rutorrent_url: Optional[str] = "http://localhost/rutorrent"
    rutorrent_user: Optional[str] = ""
    rutorrent_password: Optional[str] = ""
    rutorrent_start_now: Optional[bool] = True
    qbittorrent_url: Optional[str] = "http://localhost:8080"
    qbittorrent_api_key: Optional[str] = ""
    qbittorrent_user: Optional[str] = ""
    qbittorrent_password: Optional[str] = ""
    qbittorrent_proxy_user: Optional[str] = ""
    qbittorrent_proxy_password: Optional[str] = ""
    qbittorrent_save_path: Optional[str] = ""
    qbittorrent_category: Optional[str] = "movies"
    qbittorrent_start_now: Optional[bool] = True
    site_scan1: Optional[str] = ""
    site_scan2: Optional[str] = ""
    site_scan3: Optional[str] = ""
    filter_exclude_existing: Optional[bool] = True
    filter_timeframe: Optional[str] = "365d"
    filter_only_current_year: Optional[bool] = True
    filter_target_year: Optional[int] = 2026
    filter_min_imdb_score: Optional[float] = 5.0
    filter_require_english: Optional[bool] = True
    filter_disliked_genres: Optional[List[str]] = []


# -----------------------------------------------------------------------------
# Web Page HTML Generators
# -----------------------------------------------------------------------------

def render_login_page(error: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>New Media Analyser | Welcome</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }}
        .auth-card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 32px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }}
        .brand {{
            text-align: center;
            margin-bottom: 24px;
        }}
        .brand h1 {{
            margin: 0;
            font-size: 24px;
            color: #38bdf8;
            font-weight: 700;
        }}
        .brand p {{
            margin: 6px 0 0 0;
            color: #94a3b8;
            font-size: 14px;
        }}
        .tab-buttons {{
            display: flex;
            border-bottom: 1px solid #334155;
            margin-bottom: 20px;
        }}
        .tab-btn {{
            flex: 1;
            padding: 10px;
            background: none;
            border: none;
            color: #94a3b8;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            border-bottom: 2px solid transparent;
        }}
        .tab-btn.active {{
            color: #38bdf8;
            border-bottom: 2px solid #38bdf8;
        }}
        .form-group {{
            margin-bottom: 16px;
        }}
        label {{
            display: block;
            font-size: 13px;
            color: #cbd5e1;
            margin-bottom: 6px;
            font-weight: 500;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 10px 12px;
            border-radius: 6px;
            border: 1px solid #475569;
            background: #0f172a;
            color: #fff;
            font-size: 14px;
            box-sizing: border-box;
            outline: none;
        }}
        input[type="text"]:focus, input[type="password"]:focus {{
            border-color: #38bdf8;
            box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
        }}
        .btn-submit {{
            width: 100%;
            padding: 11px;
            border-radius: 6px;
            border: none;
            background: #0284c7;
            color: white;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            margin-top: 8px;
        }}
        .btn-submit:hover {{
            background: #0369a1;
        }}
        .error-msg {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #f87171;
            padding: 10px;
            border-radius: 6px;
            font-size: 13px;
            margin-bottom: 16px;
            display: none;
        }}
        .info-pill {{
            text-align: center;
            font-size: 12px;
            color: #64748b;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="auth-card">
        <div class="brand">
            <h1>New Media Analyser</h1>
            <p>Smart Media Discovery & Download Monitor</p>
        </div>

        <div class="tab-buttons">
            <button class="tab-btn active" id="tabLogin" onclick="switchTab('login')">Sign In</button>
            <button class="tab-btn" id="tabRegister" onclick="switchTab('register')">Register New Account</button>
        </div>

        <div class="error-msg" id="errorBox"></div>

        <form id="authForm" onsubmit="handleSubmit(event)">
            <div class="form-group">
                <label for="username">Your Email</label>
                <input type="text" id="username" placeholder="name@example.com" required autocomplete="email" autofocus>
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" placeholder="••••••••" required autocomplete="current-password">
            </div>
            <button type="submit" class="btn-submit" id="submitBtn">Sign In</button>
        </form>

        <div class="info-pill">
            Local & Remote Test Deployment
        </div>
    </div>

    <script>
        let currentMode = 'login';
        function switchTab(mode) {{
            currentMode = mode;
            document.getElementById('tabLogin').className = 'tab-btn' + (mode === 'login' ? ' active' : '');
            document.getElementById('tabRegister').className = 'tab-btn' + (mode === 'register' ? ' active' : '');
            document.getElementById('submitBtn').textContent = mode === 'login' ? 'Sign In' : 'Create Account';
            document.getElementById('errorBox').style.display = 'none';
        }}

        async function handleSubmit(e) {{
            e.preventDefault();
            const u = document.getElementById('username').value.trim();
            const p = document.getElementById('password').value;
            const errBox = document.getElementById('errorBox');
            errBox.style.display = 'none';

            const endpoint = currentMode === 'login' ? '/api/auth/login' : '/api/auth/register';
            try {{
                const res = await fetch(endpoint, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ username: u, password: p }})
                }});
                const data = await res.json();
                if (data.success) {{
                    window.location.href = '/dashboard';
                }} else {{
                    errBox.textContent = data.message || 'Authentication failed.';
                    errBox.style.display = 'block';
                }}
            }} catch (err) {{
                errBox.textContent = 'Server connection error. Please try again.';
                errBox.style.display = 'block';
            }}
        }}
    </script>
</body>
</html>"""


def render_settings_page(username: str, settings: Dict[str, Any]) -> str:
    media_server = settings.get("media_server", "plex")
    download_client = settings.get("download_client", "radarr")
    disliked = settings.get("filter_disliked_genres", ["Fantasy", "Romance"])
    disliked_str = ", ".join(disliked) if isinstance(disliked, list) else str(disliked)

    genres_all = [
        "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary",
        "Drama", "Family", "Fantasy", "Film-Noir", "History", "Horror", "Music", "Musical",
        "Mystery", "Reality", "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"
    ]

    pills_html = []
    for g in genres_all:
        is_disliked = g.lower() in [d.lower() for d in disliked]
        cls = "genre-pill active" if is_disliked else "genre-pill"
        pills_html.append(f'<span class="{cls}" onclick="toggleGenre(this, \'{g}\')">{g}</span>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>New Media Analyser | Configuration</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 24px;
        }}
        .navbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1e293b;
            padding: 12px 24px;
            border-radius: 10px;
            border: 1px solid #334155;
            margin-bottom: 24px;
        }}
        .navbar h2 {{ margin: 0; font-size: 18px; color: #38bdf8; }}
        .nav-links {{ display: flex; align-items: center; gap: 16px; font-size: 14px; }}
        .nav-links a {{ color: #cbd5e1; text-decoration: none; }}
        .nav-links a:hover {{ color: #38bdf8; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        .section-card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        .section-card h3 {{
            margin-top: 0;
            color: #f8fafc;
            font-size: 16px;
            border-bottom: 1px solid #334155;
            padding-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .form-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }}
        .form-group {{ margin-bottom: 12px; }}
        .form-group.full-width {{ grid-column: 1 / -1; }}
        label {{ display: block; font-size: 13px; color: #94a3b8; margin-bottom: 6px; font-weight: 500; }}
        input[type="text"], input[type="number"], select {{
            width: 100%;
            padding: 9px 12px;
            background: #0f172a;
            border: 1px solid #475569;
            border-radius: 6px;
            color: #fff;
            font-size: 14px;
            box-sizing: border-box;
            outline: none;
        }}
        input:focus, select:focus {{ border-color: #38bdf8; }}
        .btn {{
            padding: 8px 16px;
            border-radius: 6px;
            border: none;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
        }}
        .btn-primary {{ background: #0284c7; color: white; }}
        .btn-primary:hover {{ background: #0369a1; }}
        .btn-test {{ background: #334155; color: #38bdf8; border: 1px solid #475569; }}
        .btn-test:hover {{ background: #475569; }}
        .btn-save-all {{
            background: #10b981;
            color: white;
            padding: 12px 24px;
            font-size: 15px;
            width: 100%;
            margin-top: 10px;
        }}
        .btn-save-all:hover {{ background: #059669; }}
        .result-box {{
            font-size: 12px;
            padding: 8px 12px;
            border-radius: 6px;
            margin-top: 10px;
            display: none;
        }}
        .result-box.success {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }}
        .result-box.error {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}
        
        .genre-pill-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
        .genre-pill {{
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 500;
            background: #0f172a;
            color: #94a3b8;
            border: 1px solid #334155;
            cursor: pointer;
            user-select: none;
        }}
        .genre-pill.active {{
            background: rgba(239, 68, 68, 0.2);
            color: #f87171;
            border-color: #ef4444;
        }}
        .toast-box {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #10b981;
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            display: none;
            z-index: 9999;
        }}
    </style>
</head>
<body>
    <div class="navbar">
        <h2>New Media Analyser</h2>
        <div class="nav-links">
            <span>👤 Logged in as <strong>{html.escape(username)}</strong></span>
            <a href="/dashboard" class="btn btn-primary" style="text-decoration: none;">📊 Dashboard</a>
            <a href="/api/auth/logout" class="btn btn-test" style="text-decoration: none; color: #f87171; border-color: rgba(248, 113, 113, 0.3);">Sign Out</a>
        </div>
    </div>

    <div class="container">
        <!-- 1. Media Server Section -->
        <div class="section-card">
            <h3>
                <span>1. Media Server Connection</span>
                <button type="button" class="btn btn-test" onclick="testMediaServer()">Test Connection & Sync</button>
            </h3>
            <div class="form-group">
                <label>Active Media Server Type</label>
                <select id="media_server" onchange="toggleServerFields()">
                    <option value="plex" {'selected' if media_server == 'plex' else ''}>Plex Media Server</option>
                    <option value="jellyfin" {'selected' if media_server == 'jellyfin' else ''}>Jellyfin</option>
                    <option value="emby" {'selected' if media_server == 'emby' else ''}>Emby</option>
                    <option value="all" {'selected' if media_server == 'all' else ''}>All Servers Combined</option>
                </select>
            </div>

            <!-- Plex Inputs -->
            <div id="fields_plex" style="display: {'block' if (media_server == 'plex' or media_server == 'all') else 'none'};">
                <div style="background: rgba(229, 160, 13, 0.08); border: 1px solid rgba(229, 160, 13, 0.25); border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                    <div>
                        <div style="font-weight: 600; color: #fbbf24; font-size: 14px; margin-bottom: 2px;">⚡ 1-Click Plex Authorization</div>
                        <div style="font-size: 12px; color: #cbd5e1;">Your token does not need to be entered manually. Click below to sign in with your Plex account to create and link your token automatically.</div>
                    </div>
                    <button type="button" id="btnPlexOAuth" onclick="startPlexOAuth()" style="background: #e5a00d; color: #000; font-weight: 700; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; display: flex; align-items: center; gap: 8px; font-size: 13px; transition: all 0.15s ease;">
                        <span>Sign In with Plex</span>
                    </button>
                </div>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Plex Server URL</label>
                        <input type="text" id="plex_url" value="{html.escape(settings.get('plex_url', ''))}" placeholder="http://192.168.0.2:32400">
                    </div>
                    <div class="form-group">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <label style="margin: 0;">Plex Token (X-Plex-Token)</label>
                            <button type="button" id="btnToggleManualToken" onclick="toggleManualPlexToken()" style="background: transparent; border: none; color: #38bdf8; font-size: 11px; cursor: pointer; text-decoration: underline;">Edit manually</button>
                        </div>
                        <input type="text" id="plex_token" value="{html.escape(settings.get('plex_token', ''))}" placeholder="Generated automatically via Sign In with Plex" readonly style="background: #202020; color: #94a3b8; cursor: not-allowed;">
                    </div>
                </div>
            </div>

            <!-- Jellyfin Inputs -->
            <div id="fields_jellyfin" class="form-grid">
                <div class="form-group">
                    <label>Jellyfin Server URL</label>
                    <input type="text" id="jellyfin_url" value="{html.escape(settings.get('jellyfin_url', ''))}" placeholder="http://raspberrypi2.local:8096">
                </div>
                <div class="form-group">
                    <label>Jellyfin API Key</label>
                    <input type="text" id="jellyfin_key" value="{html.escape(settings.get('jellyfin_key', ''))}" placeholder="Your Jellyfin API Key">
                </div>
            </div>

            <!-- Emby Inputs -->
            <div id="fields_emby" class="form-grid">
                <div class="form-group">
                    <label>Emby Server URL</label>
                    <input type="text" id="emby_url" value="{html.escape(settings.get('emby_url', ''))}" placeholder="http://raspberrypi2.local:8097">
                </div>
                <div class="form-group">
                    <label>Emby API Key</label>
                    <input type="text" id="emby_key" value="{html.escape(settings.get('emby_key', ''))}" placeholder="Your Emby API Key">
                </div>
            </div>

            <div id="serverTestResult" class="result-box"></div>
        </div>

        <!-- 2. Downloader Client Section -->
        <div class="section-card">
            <h3>
                <span>2. Downloader Client (1-Click Requests)</span>
                <button type="button" class="btn btn-test" onclick="testDownloader()">Test Downloader</button>
            </h3>
            <div class="form-group">
                <label>Active Downloader</label>
                <select id="download_client" onchange="toggleDownloaderFields()">
                    <option value="radarr" {'selected' if download_client == 'radarr' else ''}>Radarr (Movies)</option>
                    <option value="rutorrent" {'selected' if download_client == 'rutorrent' else ''}>ruTorrent (Direct Client)</option>
                    <option value="qbittorrent" {'selected' if download_client == 'qbittorrent' else ''}>qBittorrent (WebAPI v2)</option>
                </select>
            </div>

            <!-- Radarr Inputs -->
            <div id="fields_radarr" class="form-grid">
                <div class="form-group">
                    <label>Radarr URL</label>
                    <input type="text" id="radarr_url" value="{html.escape(settings.get('radarr_url', ''))}" placeholder="http://localhost:7878">
                </div>
                <div class="form-group">
                    <label>Radarr API Key</label>
                    <input type="text" id="radarr_api_key" value="{html.escape(settings.get('radarr_api_key', ''))}">
                </div>
                <div class="form-group">
                    <label>Root Folder Path (Optional)</label>
                    <input type="text" id="radarr_root_folder" value="{html.escape(settings.get('radarr_root_folder', ''))}" placeholder="Auto-detects first folder if empty">
                </div>
                <div class="form-group">
                    <label>Quality Profile (Optional)</label>
                    <input type="text" id="radarr_quality_profile" value="{html.escape(settings.get('radarr_quality_profile', ''))}" placeholder="Auto-detects if empty">
                </div>
            </div>

            <!-- ruTorrent Inputs -->
            <div id="fields_rutorrent" class="form-grid">
                <div class="form-group">
                    <label>ruTorrent Web URL</label>
                    <input type="text" id="rutorrent_url" value="{html.escape(settings.get('rutorrent_url', ''))}" placeholder="http://localhost/rutorrent">
                </div>
                <div class="form-group">
                    <label>ruTorrent Username</label>
                    <input type="text" id="rutorrent_user" value="{html.escape(settings.get('rutorrent_user', ''))}">
                </div>
                <div class="form-group">
                    <label>ruTorrent Password</label>
                    <input type="password" id="rutorrent_password" value="{html.escape(settings.get('rutorrent_password', ''))}">
                </div>
                <div class="form-group" style="display: flex; align-items: center; gap: 8px; margin-top: 24px;">
                    <input type="checkbox" id="rutorrent_start_now" {'checked' if settings.get('rutorrent_start_now', True) else ''}>
                    <label for="rutorrent_start_now" style="margin: 0; cursor: pointer;">Auto-start torrents on add</label>
                </div>
            </div>

            <!-- qBittorrent Inputs -->
            <div id="fields_qbittorrent" class="form-grid">
                <div class="form-group">
                    <label>qBittorrent Web URL</label>
                    <input type="text" id="qbittorrent_url" value="{html.escape(settings.get('qbittorrent_url', ''))}" placeholder="http://localhost:8080">
                </div>
                <div class="form-group">
                    <label>API Key (v5.2.0+ Bearer Token)</label>
                    <input type="text" id="qbittorrent_api_key" value="{html.escape(settings.get('qbittorrent_api_key', ''))}" placeholder="qbt_...">
                    <span style="font-size: 11px; color: #94a3b8; margin-top: 5px; display: block; line-height: 1.4;">
                        🔑 Supported in qBittorrent v5.2.0+. Learn <a href="https://github.com/qbittorrent/qBittorrent/wiki/API-Key-Authentication-(%E2%89%A5v5.2.0)" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">how to find or create your API Key</a> in the qBittorrent WebUI.
                    </span>
                </div>
                <div class="form-group">
                    <label>WebUI Username (Optional / Fallback)</label>
                    <input type="text" id="qbittorrent_user" value="{html.escape(settings.get('qbittorrent_user', ''))}" placeholder="rooster">
                </div>
                <div class="form-group">
                    <label>WebUI Password (Optional / Fallback)</label>
                    <input type="password" id="qbittorrent_password" value="{html.escape(settings.get('qbittorrent_password', ''))}">
                </div>
                <div class="form-group">
                    <label>Reverse Proxy User (e.g. Seedbox Basic Auth)</label>
                    <input type="text" id="qbittorrent_proxy_user" value="{html.escape(settings.get('qbittorrent_proxy_user', ''))}" placeholder="Leave blank if direct">
                </div>
                <div class="form-group">
                    <label>Reverse Proxy Password</label>
                    <input type="password" id="qbittorrent_proxy_password" value="{html.escape(settings.get('qbittorrent_proxy_password', ''))}">
                </div>
                <div class="form-group">
                    <label>Target Category</label>
                    <input type="text" id="qbittorrent_category" value="{html.escape(settings.get('qbittorrent_category', 'movies'))}" placeholder="movies">
                </div>
                <div class="form-group">
                    <label>Custom Save Path (Optional)</label>
                    <input type="text" id="qbittorrent_save_path" value="{html.escape(settings.get('qbittorrent_save_path', ''))}" placeholder="Default save path if empty">
                </div>
                <div class="form-group" style="display: flex; align-items: center; gap: 8px; margin-top: 24px;">
                    <input type="checkbox" id="qbittorrent_start_now" {'checked' if settings.get('qbittorrent_start_now', True) else ''}>
                    <label for="qbittorrent_start_now" style="margin: 0; cursor: pointer;">Auto-start torrents on add</label>
                </div>
            </div>

            <div id="downloaderTestResult" class="result-box"></div>
        </div>

        <!-- 3. Filter Criteria & Taste Preferences -->
        <div class="section-card">
            <h3>3. Filtering & Taste Preferences</h3>
            <div class="form-grid">
                <div class="form-group">
                    <label>Minimum IMDb Rating (1.0 - 9.0)</label>
                    <input type="number" step="0.1" min="1.0" max="10.0" id="filter_min_imdb_score" value="{settings.get('filter_min_imdb_score', 5.0)}">
                </div>
                <div class="form-group">
                    <label>US Release Date Timeframe</label>
                    <select id="filter_timeframe" onchange="toggleTargetYearInput()">
                        <option value="7d" {"selected" if settings.get("filter_timeframe") == "7d" else ""}>Within Last 7 Days (Past week)</option>
                        <option value="30d" {"selected" if settings.get("filter_timeframe") == "30d" else ""}>Within Last 30 Days (Past month)</option>
                        <option value="90d" {"selected" if settings.get("filter_timeframe") == "90d" else ""}>Within Last 90 Days (Past 3 months)</option>
                        <option value="365d" {"selected" if settings.get("filter_timeframe", "365d") == "365d" else ""}>Within Last 12 Months (Past year)</option>
                        <option value="year" {"selected" if settings.get("filter_timeframe") == "year" else ""}>Specific Release Year</option>
                        <option value="all" {"selected" if settings.get("filter_timeframe") == "all" else ""}>Any Release Date (No cutoff)</option>
                    </select>
                </div>
                <div class="form-group" id="target_year_group" style="{'display: block;' if settings.get('filter_timeframe') == 'year' else 'display: none;'}">
                    <label>Target Release Year</label>
                    <input type="number" id="filter_target_year" value="{settings.get('filter_target_year', 2026)}">
                </div>
                <div class="form-group" style="display: flex; align-items: center; gap: 8px;">
                    <input type="checkbox" id="filter_exclude_existing" {'checked' if settings.get('filter_exclude_existing', True) else ''}>
                    <label for="filter_exclude_existing" style="margin: 0; cursor: pointer;">Exclude titles already in library</label>
                </div>
                <div class="form-group" style="display: flex; align-items: center; gap: 8px;">
                    <input type="checkbox" id="filter_require_english" {'checked' if settings.get('filter_require_english', True) else ''}>
                    <label for="filter_require_english" style="margin: 0; cursor: pointer;">Require English audio/language</label>
                </div>
            </div>

            <div class="form-group full-width" style="margin-top: 14px;">
                <label>Disliked / Avoided Genres (Click to toggle red - automatically marked Low match):</label>
                <div class="genre-pill-container" id="genreContainer">
                    {''.join(pills_html)}
                </div>
            </div>
        </div>

        <div style="display: flex; gap: 12px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
            <button type="button" class="btn btn-save-all" onclick="saveAllSettings()" style="flex: 2; margin: 0; min-width: 220px;">💾 Save All Preferences</button>
            <a href="/dashboard" class="btn btn-primary" style="flex: 1; padding: 12px 20px; font-size: 15px; text-decoration: none; text-align: center; justify-content: center; min-width: 180px;">📊 Back to Dashboard</a>
        </div>
    </div>

    <div class="toast-box" id="toastBox">Preferences saved successfully!</div>

    <script>
        function toggleServerFields() {{
            const s = document.getElementById('media_server').value;
            document.getElementById('fields_plex').style.display = (s === 'plex' || s === 'all') ? 'block' : 'none';
            document.getElementById('fields_jellyfin').style.display = (s === 'jellyfin' || s === 'all') ? 'grid' : 'none';
            document.getElementById('fields_emby').style.display = (s === 'emby' || s === 'all') ? 'grid' : 'none';
        }}
        toggleServerFields();

        function toggleManualPlexToken() {{
            const input = document.getElementById('plex_token');
            const btn = document.getElementById('btnToggleManualToken');
            if (input.readOnly) {{
                input.readOnly = false;
                input.style.background = '#181818';
                input.style.color = '#f8fafc';
                input.style.cursor = 'text';
                btn.textContent = 'Lock field';
            }} else {{
                input.readOnly = true;
                input.style.background = '#202020';
                input.style.color = '#94a3b8';
                input.style.cursor = 'not-allowed';
                btn.textContent = 'Edit manually';
            }}
        }}

        let plexPollInterval = null;
        async function startPlexOAuth() {{
            const btn = document.getElementById('btnPlexOAuth');
            const originalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span>Opening Plex...</span>';

            try {{
                const res = await fetch('/api/plex/oauth/start', {{ method: 'POST' }});
                const data = await res.json();
                if (!data.success || !data.oauth_url) {{
                    alert('Error starting Plex sign-in: ' + (data.message || 'Unknown error'));
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                    return;
                }}

                // Open Plex OAuth window
                const width = 600, height = 700;
                const left = (window.innerWidth - width) / 2;
                const top = (window.innerHeight - height) / 2;
                const popup = window.open(data.oauth_url, 'PlexSignIn', `width=${{width}},height=${{height}},top=${{top}},left=${{left}}`);

                btn.innerHTML = '<span>Waiting for Plex authorization...</span>';

                if (plexPollInterval) clearInterval(plexPollInterval);
                let pollAttempts = 0;
                plexPollInterval = setInterval(async () => {{
                    pollAttempts++;
                    if (pollAttempts > 90) {{
                        clearInterval(plexPollInterval);
                        btn.disabled = false;
                        btn.innerHTML = originalHtml;
                        return;
                    }}
                    try {{
                        const pollRes = await fetch('/api/plex/oauth/poll?pin_id=' + encodeURIComponent(data.pin_id));
                        const pollData = await pollRes.json();
                        if (pollData.success && pollData.authenticated && pollData.token) {{
                            clearInterval(plexPollInterval);
                            if (popup && !popup.closed) popup.close();
                            document.getElementById('plex_token').value = pollData.token;
                            btn.disabled = false;
                            btn.style.background = '#22c55e';
                            btn.style.color = '#fff';
                            btn.innerHTML = '<span>✓ Plex Authorized!</span>';
                            testMediaServer();
                            setTimeout(() => {{
                                btn.style.background = '#e5a00d';
                                btn.style.color = '#000';
                                btn.innerHTML = originalHtml;
                            }}, 5000);
                        }}
                    }} catch (err) {{
                        console.error('Plex poll error:', err);
                    }}
                }}, 2000);
            }} catch (e) {{
                alert('Connection error starting Plex sign-in.');
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }}
        }}

        function toggleDownloaderFields() {{
            const d = document.getElementById('download_client').value;
            document.getElementById('fields_radarr').style.display = (d === 'radarr') ? 'grid' : 'none';
            document.getElementById('fields_rutorrent').style.display = (d === 'rutorrent') ? 'grid' : 'none';
            document.getElementById('fields_qbittorrent').style.display = (d === 'qbittorrent' || d === 'qbit') ? 'grid' : 'none';
        }}
        toggleDownloaderFields();

        function toggleGenre(el, genre) {{
            el.classList.toggle('active');
        }}

        function getDislikedGenres() {{
            const activePills = document.querySelectorAll('.genre-pill.active');
            const arr = [];
            activePills.forEach(p => arr.push(p.textContent.trim()));
            return arr;
        }}

        async function testMediaServer() {{
            const box = document.getElementById('serverTestResult');
            box.className = 'result-box';
            box.style.display = 'block';
            box.textContent = 'Testing connection and reading catalog...';

            const payload = {{
                media_server: document.getElementById('media_server').value,
                plex_url: document.getElementById('plex_url').value,
                plex_token: document.getElementById('plex_token').value,
                jellyfin_url: document.getElementById('jellyfin_url').value,
                jellyfin_key: document.getElementById('jellyfin_key').value,
                emby_url: document.getElementById('emby_url').value,
                emby_key: document.getElementById('emby_key').value,
            }};

            try {{
                const res = await fetch('/api/test/media-server', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();
                if (data.success) {{
                    box.className = 'result-box success';
                    box.textContent = '✓ ' + data.message;
                }} else {{
                    box.className = 'result-box error';
                    box.textContent = '✗ ' + data.message;
                }}
            }} catch (e) {{
                box.className = 'result-box error';
                box.textContent = 'Network or server error while testing media server.';
            }}
        }}

        async function testDownloader() {{
            const box = document.getElementById('downloaderTestResult');
            box.className = 'result-box';
            box.style.display = 'block';
            box.textContent = 'Verifying downloader reachability...';

            const payload = {{
                download_client: document.getElementById('download_client').value,
                radarr_url: document.getElementById('radarr_url').value,
                radarr_api_key: document.getElementById('radarr_api_key').value,
                rutorrent_url: document.getElementById('rutorrent_url').value,
                rutorrent_user: document.getElementById('rutorrent_user').value,
                rutorrent_password: document.getElementById('rutorrent_password').value,
                qbittorrent_url: document.getElementById('qbittorrent_url').value,
                qbittorrent_api_key: document.getElementById('qbittorrent_api_key').value,
                qbittorrent_user: document.getElementById('qbittorrent_user').value,
                qbittorrent_password: document.getElementById('qbittorrent_password').value,
                qbittorrent_proxy_user: document.getElementById('qbittorrent_proxy_user').value,
                qbittorrent_proxy_password: document.getElementById('qbittorrent_proxy_password').value,
            }};

            try {{
                const res = await fetch('/api/test/downloader', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();
                if (data.success) {{
                    box.className = 'result-box success';
                    box.textContent = '✓ ' + data.message;
                }} else {{
                    box.className = 'result-box error';
                    box.textContent = '✗ ' + data.message;
                }}
            }} catch (e) {{
                box.className = 'result-box error';
                box.textContent = 'Network or server error while testing downloader.';
            }}
        }}

        function toggleTargetYearInput() {{
            const tf = document.getElementById('filter_timeframe').value;
            const container = document.getElementById('target_year_group');
            if (container) {{
                container.style.display = (tf === 'year') ? 'block' : 'none';
            }}
        }}

        async function saveAllSettings() {{
            const tfVal = document.getElementById('filter_timeframe').value;
            const payload = {{
                media_server: document.getElementById('media_server').value,
                plex_url: document.getElementById('plex_url').value,
                plex_token: document.getElementById('plex_token').value,
                jellyfin_url: document.getElementById('jellyfin_url').value,
                jellyfin_key: document.getElementById('jellyfin_key').value,
                emby_url: document.getElementById('emby_url').value,
                emby_key: document.getElementById('emby_key').value,
                download_client: document.getElementById('download_client').value,
                radarr_url: document.getElementById('radarr_url').value,
                radarr_api_key: document.getElementById('radarr_api_key').value,
                radarr_root_folder: document.getElementById('radarr_root_folder').value,
                radarr_quality_profile: document.getElementById('radarr_quality_profile').value,
                rutorrent_url: document.getElementById('rutorrent_url').value,
                rutorrent_user: document.getElementById('rutorrent_user').value,
                rutorrent_password: document.getElementById('rutorrent_password').value,
                rutorrent_start_now: document.getElementById('rutorrent_start_now').checked,
                qbittorrent_url: document.getElementById('qbittorrent_url').value,
                qbittorrent_api_key: document.getElementById('qbittorrent_api_key').value,
                qbittorrent_user: document.getElementById('qbittorrent_user').value,
                qbittorrent_password: document.getElementById('qbittorrent_password').value,
                qbittorrent_proxy_user: document.getElementById('qbittorrent_proxy_user').value,
                qbittorrent_proxy_password: document.getElementById('qbittorrent_proxy_password').value,
                qbittorrent_category: document.getElementById('qbittorrent_category').value,
                qbittorrent_save_path: document.getElementById('qbittorrent_save_path').value,
                qbittorrent_start_now: document.getElementById('qbittorrent_start_now').checked,
                filter_min_imdb_score: parseFloat(document.getElementById('filter_min_imdb_score').value) || 5.0,
                filter_timeframe: tfVal,
                filter_target_year: parseInt(document.getElementById('filter_target_year').value) || 2026,
                filter_exclude_existing: document.getElementById('filter_exclude_existing').checked,
                filter_only_current_year: (tfVal !== 'all'),
                filter_require_english: document.getElementById('filter_require_english').checked,
                filter_disliked_genres: getDislikedGenres(),
            }};

            try {{
                const res = await fetch('/api/settings', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();
                if (data.success) {{
                    const t = document.getElementById('toastBox');
                    t.style.display = 'block';
                    setTimeout(() => {{ t.style.display = 'none'; }}, 3000);
                }} else {{
                    alert('Error saving preferences: ' + data.message);
                }}
            }} catch (e) {{
                alert('Connection error while saving settings.');
            }}
        }}
    </script>
</body>
</html>"""


def render_dashboard_page(username: str, settings: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
    downloader_raw = settings.get("download_client", "radarr")
    downloader_name = {
        "radarr": "Radarr",
        "rutorrent": "ruTorrent",
        "qbittorrent": "qBittorrent"
    }.get(downloader_raw.lower(), downloader_raw.capitalize())

    server_raw = settings.get("media_server", "plex")
    server_name = {
        "plex": "Plex",
        "jellyfin": "Jellyfin",
        "emby": "Emby",
        "all": "All Servers"
    }.get(server_raw.lower(), server_raw.capitalize())

    has_server = bool(settings.get("plex_token") or settings.get("jellyfin_key") or settings.get("emby_key"))
    has_downloader = bool(
        settings.get("radarr_api_key")
        or (settings.get("rutorrent_url") and (settings.get("rutorrent_user") or settings.get("rutorrent_password")))
        or (settings.get("qbittorrent_url") and (settings.get("qbittorrent_api_key") or settings.get("qbittorrent_user")))
    )

    if has_server:
        server_pill_html = f'<span class="user-pill pill-online">Server: <strong>{html.escape(server_name)}</strong></span>'
    else:
        server_pill_html = '<a href="/settings" class="user-pill pill-warning" title="Connect your media server in Settings">Server: ⚠️ Not Connected (Setup →)</a>'

    if has_downloader:
        downloader_pill_html = f'<span class="user-pill pill-online">Downloader: <strong>{html.escape(downloader_name)}</strong></span>'
    else:
        downloader_pill_html = '<a href="/settings" class="user-pill pill-warning" title="Configure Radarr, ruTorrent, or qBittorrent in Settings">Downloader: ⚠️ Not Configured (Setup →)</a>'

    onboarding_banner_html = ""
    if not has_server:
        onboarding_banner_html = f"""
    <div class="onboarding-banner" id="onboardingBanner">
        <div class="onboarding-text">
            <h4>👋 Welcome to New Media Analyser, {html.escape(username)}!</h4>
            <p>You are viewing real-time media releases from <strong>The Pirate Bay</strong> and <strong>uIndex</strong>. To automatically hide movies you already own and unlock personalized genre match scores, connect your <strong>Jellyfin, Plex, or Emby</strong> library!</p>
        </div>
        <div class="onboarding-actions">
            <a href="/settings" class="btn-setup">⚙️ Connect Media Server</a>
            <button type="button" class="btn-dismiss" onclick="document.getElementById('onboardingBanner').style.display='none'">Dismiss</button>
        </div>
    </div>
    """

    counts = {"Consider": 0, "Exists": 0, "Too Old": 0, "Low Score": 0, "Non-English": 0}
    genre_counts = {"High": 0, "Medium": 0, "Low": 0}
    for item in items:
        st = item.get("filter_status", "Consider")
        counts[st] = counts.get(st, 0) + 1
        gm = item.get("genre_match", "Low")
        genre_counts[gm] = genre_counts.get(gm, 0) + 1

    tpb_count = sum(1 for x in items if "tpb" in x.get("source_keys", []))
    uindex_count = sum(1 for x in items if "uindex" in x.get("source_keys", []))

    rows = []
    for item in items:
        imdb_url = item.get("imdb_url", "N/A")
        if imdb_url and imdb_url != "N/A":
            title_html = f"<a href='{imdb_url}' target='_blank' class='title-link'>{html.escape(item['title'])}</a>"
        else:
            title_html = f"<span class='title-main'>{html.escape(item['title'])}</span>"

        status = item.get("filter_status", "Consider")
        reason = item.get("filter_reason", "")
        badge_cls = {
            "Consider": "badge-consider",
            "Exists": "badge-exists",
            "Too Old": "badge-old",
            "Low Score": "badge-low",
            "Non-English": "badge-non-eng",
        }.get(status, "badge-consider")

        genre_match = item.get("genre_match", "Low")
        genre_reason = item.get("genre_match_reason", "")
        genres_list = item.get("genres", [])
        genres_display = ", ".join(genres_list) if genres_list else "Unknown"

        genre_badge_cls = {
            "High": "badge-match-high",
            "Medium": "badge-match-medium",
            "Low": "badge-match-low"
        }.get(genre_match, "badge-match-low")

        release_date = item.get("release_date") or ""
        year_val = item.get("year") or 0
        if release_date:
            date_display = release_date
        elif year_val:
            date_display = str(year_val)
        else:
            date_display = "-"

        size_str = str(item.get("size") or "N/A")
        synopsis = item.get("synopsis", "")
        truncated = scraper.truncate_synopsis(synopsis, max_words=60)
        synopsis_html = (
            f"<div class='synopsis-text' title='{html.escape(synopsis)}'>{html.escape(truncated)}</div>"
            if synopsis else ""
        )

        imdb_id = item.get("imdb_id") or ""
        escaped_title = item.get("title", "").replace("'", "\\'").replace('"', '&quot;')
        magnet_str = item.get("magnet") or ""

        sources = item.get("sources", [])
        source_keys_str = ",".join(item.get("source_keys", ["tpb"]))

        if len(sources) > 1:
            source_badge = f"<span class='badge badge-source-multi' title='Available on {html.escape(', '.join(sources))}'>TPB + uIndex</span>"
        elif "uIndex" in sources or "uindex" in source_keys_str:
            source_badge = "<span class='badge badge-source-uindex'>uIndex</span>"
        elif "TPB" in sources or "tpb" in source_keys_str:
            source_badge = "<span class='badge badge-source-tpb'>TPB</span>"
        else:
            source_badge = f"<span class='badge badge-source-other'>{html.escape(item.get('source_display', 'Unknown'))}</span>"

        btn_html = f"<button class='btn-request' data-magnet=\"{html.escape(magnet_str)}\" onclick=\"requestMedia(this, '{imdb_id}', '{escaped_title}', {year_val})\">Request ({downloader_name})</button>"

        rows.append(f"""
            <tr data-status="{html.escape(status)}" data-genre="{html.escape(genre_match)}" data-sources="{html.escape(source_keys_str)}" data-release-date="{html.escape(release_date)}" data-year="{year_val}">
                <td>
                    <div class='title-main'>{title_html}</div>
                    {synopsis_html}
                </td>
                <td><span style="font-family: monospace; font-size: 13px; color: #cbd5e1;">{html.escape(date_display)}</span></td>
                <td>{source_badge}</td>
                <td><span class='size-text'>{html.escape(size_str)}</span></td>
                <td>
                    <span class='badge {genre_badge_cls}' title='{genre_reason}'>{genre_match}</span>
                    <div class='genre-subtext' title='{html.escape(genres_display)}'>{html.escape(genres_display)}</div>
                </td>
                <td><span class='score'>{item.get('imdb_score', 'N/A')}</span></td>
                <td><span class='badge {badge_cls}' title='{reason}'>{status}</span></td>
                <td style='text-align: center;'>{btn_html}</td>
            </tr>""")

    in_server_label = f"In {server_name}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>New Media Analyser | Dashboard</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #121212;
            color: #e0e0e0;
            padding: 24px;
            margin: 0;
        }}
        .navbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1e1e1e;
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid #333;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .navbar h2 {{ margin: 0; font-size: 18px; color: #38bdf8; }}
        .nav-status-group {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
        .user-pill {{
            font-size: 13px;
            padding: 4px 12px;
            border-radius: 9999px;
            background: #252525;
            border: 1px solid #444;
            color: #f8fafc;
        }}
        .nav-links {{ display: flex; align-items: center; gap: 16px; font-size: 14px; }}
        .nav-links a {{ color: #cbd5e1; text-decoration: none; }}
        .nav-links a:hover {{ color: #38bdf8; }}

        /* Action Scan Banner */
        .scan-banner {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #1a2234;
            border: 1px solid #1e3a8a;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .scan-banner-info h3 {{ margin: 0 0 4px 0; color: #60a5fa; font-size: 16px; }}
        .scan-banner-info p {{ margin: 0; font-size: 13px; color: #94a3b8; }}
        
        .btn-go {{
            background: #2563eb;
            color: #ffffff;
            border: none;
            padding: 10px 24px;
            border-radius: 6px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .btn-go:hover {{ background: #1d4ed8; transform: translateY(-1px); }}
        .btn-go.loading {{ background: #475569; cursor: wait; }}

        /* Summary Cards */
        .summary-bar {{ display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }}
        .card {{
            background: #1e1e1e;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 12px 18px;
            min-width: 110px;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            user-select: none;
        }}
        .card:hover {{ border-color: #64748b; transform: translateY(-2px); }}
        .card.active {{ border-color: #f8fafc; box-shadow: 0 0 10px rgba(255, 255, 255, 0.2); background: #262626; }}
        .card-num {{ font-size: 22px; font-weight: bold; margin-bottom: 4px; }}
        .card-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #94a3b8; }}
        
        .card-consider .card-num {{ color: #4ade80; }}
        .card-consider.active {{ border-color: #4ade80; box-shadow: 0 0 12px rgba(74, 222, 128, 0.35); background: rgba(34, 197, 94, 0.12); }}
        .card-exists .card-num {{ color: #60a5fa; }}
        .card-exists.active {{ border-color: #60a5fa; box-shadow: 0 0 12px rgba(96, 165, 250, 0.35); background: rgba(59, 130, 246, 0.12); }}
        .card-low .card-num {{ color: #f87171; }}
        .card-low.active {{ border-color: #f87171; box-shadow: 0 0 12px rgba(248, 113, 113, 0.35); background: rgba(239, 68, 68, 0.12); }}
        .card-non-eng .card-num {{ color: #f43f5e; }}
        .card-non-eng.active {{ border-color: #f43f5e; box-shadow: 0 0 12px rgba(244, 63, 94, 0.35); background: rgba(244, 63, 94, 0.12); }}
        .card-old .card-num {{ color: #fbbf24; }}
        .card-old.active {{ border-color: #fbbf24; box-shadow: 0 0 12px rgba(251, 191, 36, 0.35); background: rgba(245, 158, 11, 0.12); }}
        .card-genre-high .card-num {{ color: #c084fc; }}
        .card-genre-high.active {{ border-color: #c084fc; box-shadow: 0 0 12px rgba(192, 132, 252, 0.35); background: rgba(168, 85, 247, 0.12); }}

        /* Source Filter Bar */
        .source-filter-bar {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 16px;
            flex-wrap: wrap;
            background: #181818;
            padding: 8px 14px;
            border-radius: 8px;
            border: 1px solid #282828;
        }}
        .source-filter-label {{ font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-right: 4px; }}
        .source-pill {{
            background: #202020;
            border: 1px solid #333;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 13px;
            border-radius: 9999px;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            user-select: none;
        }}
        .source-pill:hover {{ border-color: #64748b; color: #f8fafc; }}
        .source-pill.active {{ background: #2563eb; color: #ffffff; border-color: #3b82f6; box-shadow: 0 0 8px rgba(37, 99, 235, 0.4); }}
        .source-pill.pill-tpb.active {{ background: rgba(245, 158, 11, 0.25); color: #fbbf24; border-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4); }}
        .source-pill.pill-uindex.active {{ background: rgba(6, 182, 212, 0.25); color: #22d3ee; border-color: #06b6d4; box-shadow: 0 0 8px rgba(6, 182, 212, 0.4); }}
        .source-pill.pill-multi.active {{ background: rgba(168, 85, 247, 0.25); color: #c084fc; border-color: #a855f7; box-shadow: 0 0 8px rgba(168, 85, 247, 0.4); }}

        .timeframe-pill {{
            background: #202020;
            border: 1px solid #333;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 13px;
            border-radius: 9999px;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            user-select: none;
        }}
        .timeframe-pill:hover {{ border-color: #64748b; color: #f8fafc; }}
        .timeframe-pill.active {{ background: #059669; color: #ffffff; border-color: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.4); }}
        .filter-controls-container {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }}

        .filter-status-info {{ margin-bottom: 16px; font-size: 13px; color: #94a3b8; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }}
        .filter-status-info strong {{ color: #f8fafc; }}
        .reset-filter-btn {{ display: none; background: #262626; border: 1px solid #444; color: #38bdf8; padding: 2px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }}
        .reset-filter-btn:hover {{ background: #333; }}

        table {{ width: 100%; border-collapse: collapse; background: #181818; border-radius: 8px; overflow: hidden; border: 1px solid #2e2e2e; }}
        th, td {{ border-bottom: 1px solid #282828; padding: 12px 14px; text-align: left; font-size: 14px; vertical-align: top; }}
        th {{ background: #222; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; }}
        tr:hover {{ background: #202020; }}
        .title-link {{ color: #38bdf8; text-decoration: none; font-weight: 600; font-size: 14px; }}
        .title-link:hover {{ text-decoration: underline; color: #7dd3fc; }}
        .title-main {{ font-weight: 600; color: #f8fafc; font-size: 14px; }}
        .synopsis-text {{ font-size: 12px; color: #94a3b8; margin-top: 5px; line-height: 1.45; max-width: 440px; }}
        .size-text {{ font-size: 13px; color: #cbd5e1; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }}
        .score {{ font-weight: 600; color: #f8fafc; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-align: center; white-space: nowrap; }}
        .badge-consider {{ background: rgba(34, 197, 94, 0.18); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); }}
        .badge-exists {{ background: rgba(59, 130, 246, 0.18); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }}
        .badge-old {{ background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
        .badge-low {{ background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}
        .badge-non-eng {{ background: rgba(244, 63, 94, 0.18); color: #f43f5e; border: 1px solid rgba(244, 63, 94, 0.4); }}
        .badge-match-high {{ background: rgba(168, 85, 247, 0.18); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); }}
        .badge-match-medium {{ background: rgba(56, 189, 248, 0.18); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }}
        .badge-match-low {{ background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }}

        .badge-source-tpb {{ background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }}
        .badge-source-uindex {{ background: rgba(6, 182, 212, 0.16); color: #22d3ee; border: 1px solid rgba(6, 182, 212, 0.35); }}
        .badge-source-multi {{ background: rgba(168, 85, 247, 0.18); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.45); font-weight: 700; }}
        .badge-source-other {{ background: rgba(148, 163, 184, 0.16); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.35); }}
        .genre-subtext {{ font-size: 11px; color: #94a3b8; margin-top: 4px; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .no-matches {{ display: none; text-align: center; padding: 30px; color: #94a3b8; font-style: italic; }}

        .btn-request {{
            background: #2563eb;
            color: #ffffff;
            border: none;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            white-space: nowrap;
        }}
        .btn-request:hover {{ background: #1d4ed8; transform: translateY(-1px); }}
        .btn-request.loading {{ background: #475569; cursor: wait; }}
        .btn-request.btn-success {{ background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); cursor: default; }}
        .btn-request.btn-failed {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}

        /* Onboarding Banner */
        .onboarding-banner {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: linear-gradient(135deg, rgba(30, 58, 138, 0.45), rgba(88, 28, 135, 0.4));
            border: 1px solid rgba(147, 51, 234, 0.45);
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 20px;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .onboarding-text h4 {{ margin: 0 0 6px 0; color: #c084fc; font-size: 15px; font-weight: 700; }}
        .onboarding-text p {{ margin: 0; color: #cbd5e1; font-size: 13px; line-height: 1.45; max-width: 680px; }}
        .onboarding-actions {{ display: flex; align-items: center; gap: 10px; }}
        .btn-setup {{
            background: #9333ea;
            color: #fff;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.15s ease-in-out;
            white-space: nowrap;
            display: inline-block;
        }}
        .btn-setup:hover {{ background: #7e22ce; }}
        .btn-dismiss {{
            background: rgba(255, 255, 255, 0.1);
            color: #cbd5e1;
            border: 1px solid #475569;
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
        }}
        .btn-dismiss:hover {{ background: rgba(255, 255, 255, 0.18); }}

        .user-pill.pill-warning {{
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border-color: rgba(245, 158, 11, 0.4);
            text-decoration: none;
            transition: all 0.15s ease-in-out;
        }}
        .user-pill.pill-warning:hover {{
            background: rgba(245, 158, 11, 0.25);
            color: #fef08a;
        }}
        .user-pill.pill-online {{
            background: rgba(34, 197, 94, 0.15);
            color: #4ade80;
            border-color: rgba(34, 197, 94, 0.35);
        }}

        /* Modal Overlay */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }}
        .modal {{
            background: #1e1e1e;
            border: 1px solid #333;
            border-radius: 10px;
            padding: 24px;
            max-width: 520px;
            width: 90%;
            color: #f8fafc;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
        }}
        .modal h3 {{ margin-top: 0; font-size: 18px; color: #f8fafc; }}
        .modal p {{ font-size: 14px; color: #94a3b8; line-height: 1.5; }}
        .code-box {{
            background: #111;
            border: 1px solid #333;
            padding: 10px 12px;
            border-radius: 6px;
            font-family: monospace;
            font-size: 13px;
            color: #38bdf8;
            word-break: break-all;
            margin: 12px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
        }}
        .code-box button {{
            background: #2563eb;
            color: white;
            border: none;
            padding: 5px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
        }}
        .code-box button:hover {{ background: #1d4ed8; }}
        .modal-buttons {{ display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }}
        .btn-close {{ background: #333; color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; }}
        .btn-close:hover {{ background: #444; }}

        /* Toast notifications */
        .toast-container {{ position: fixed; bottom: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; }}
        .toast {{
            background: #1e1e1e;
            color: #fff;
            padding: 12px 16px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            border-left: 4px solid #2563eb;
            font-size: 13px;
            animation: slideIn 0.2s ease-out;
            max-width: 360px;
        }}
        .toast.success {{ border-left-color: #4ade80; }}
        .toast.error {{ border-left-color: #f87171; }}
        @keyframes slideIn {{ from {{ transform: translateX(100%); opacity: 0; }} to {{ transform: translateX(0); opacity: 1; }} }}
    </style>
</head>
<body>
    <div class="navbar">
        <h2>New Media Analyser</h2>
        <div class="nav-status-group">
            <span class="user-pill">👤 <strong>{html.escape(username)}</strong></span>
            {server_pill_html}
            {downloader_pill_html}
        </div>
        <div class="nav-links">
            <a href="/settings">⚙️ Settings</a>
            <a href="/api/auth/logout" style="color: #f87171;">Logout</a>
        </div>
    </div>

    {onboarding_banner_html}

    <!-- Live Scan Banner with GO Button -->
    <div class="scan-banner">
        <div class="scan-banner-info">
            <h3 id="scanTitle">Unified Media Release Feed</h3>
            <p id="scanSub">Scans The Pirate Bay & uIndex.org concurrently and compares against your {server_name} library.</p>
        </div>
        <button class="btn-go" id="btnGo" onclick="startScan()">
            <span id="btnGoIcon">🚀</span>
            <span id="btnGoText">Scan Releases Now</span>
        </button>
    </div>

    <!-- Filter Cards -->
    <div class="summary-bar">
        <div class="card active" data-filter="all"><div class="card-num">{len(items)}</div><div class="card-label">Total</div></div>
        <div class="card card-consider" data-filter="status:Consider"><div class="card-num">{counts.get('Consider', 0)}</div><div class="card-label">Consider</div></div>
        <div class="card card-exists" data-filter="status:Exists"><div class="card-num">{counts.get('Exists', 0)}</div><div class="card-label">{in_server_label}</div></div>
        <div class="card card-low" data-filter="status:Low Score"><div class="card-num">{counts.get('Low Score', 0)}</div><div class="card-label">Low Score</div></div>
        <div class="card card-non-eng" data-filter="status:Non-English"><div class="card-num">{counts.get('Non-English', 0)}</div><div class="card-label">Non-English</div></div>
        <div class="card card-old" data-filter="status:Too Old"><div class="card-num">{counts.get('Too Old', 0)}</div><div class="card-label">Too Old</div></div>
        <div class="card card-genre-high" data-filter="genre:High"><div class="card-num">{genre_counts.get('High', 0)}</div><div class="card-label">High Genre Match</div></div>
    </div>

    <!-- Filter Controls (Source & Timeframe) -->
    <div class="filter-controls-container">
        <div class="source-filter-bar" style="margin-bottom: 0;">
            <span class="source-filter-label">Source:</span>
            <button class="source-pill active" data-source="all">All Sources ({len(items)})</button>
            <button class="source-pill pill-tpb" data-source="tpb">Pirate Bay ({tpb_count})</button>
            <button class="source-pill pill-uindex" data-source="uindex">uIndex ({uindex_count})</button>
        </div>
        <div class="source-filter-bar timeframe-bar" style="margin-bottom: 0;">
            <span class="source-filter-label">US Release:</span>
            <button class="timeframe-pill active" data-timeframe="all">All Time ({len(items)})</button>
            <button class="timeframe-pill" data-timeframe="7d">Last 7 Days</button>
            <button class="timeframe-pill" data-timeframe="30d">Last 30 Days</button>
            <button class="timeframe-pill" data-timeframe="90d">Last 3 Months</button>
            <button class="timeframe-pill" data-timeframe="365d">Last 12 Months</button>
        </div>
    </div>

    <div class="filter-status-info" id="filterInfo">
        <div>
            <span>Showing <strong id="visibleCount">{len(items)}</strong> of {len(items)} items</span>
            <button class="reset-filter-btn" id="resetBtn" onclick="resetFilter()">Reset Filter</button>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th>Title</th>
                <th>US Release</th>
                <th>Source</th>
                <th>Size</th>
                <th>Genre</th>
                <th>IMDb Score</th>
                <th>Filter Status</th>
                <th style="text-align: center;">Request ({downloader_name})</th>
            </tr>
        </thead>
        <tbody id="mediaTableBody">{''.join(rows)}
        </tbody>
    </table>
    <div class="no-matches" id="noMatches">No releases match this filter.</div>

    <!-- Modal for Manual Magnet Copy / Downloader Unconfigured -->
    <div class="modal-overlay" id="fallbackModal">
        <div class="modal">
            <h3 id="modalTitle">Downloader Not Configured</h3>
            <p id="modalDesc">You haven't configured a downloader client (Radarr or ruTorrent) yet in your Settings. You can copy the direct torrent magnet link below to download manually with any BitTorrent client:</p>
            <div class="code-box">
                <span id="modalMagnetText" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:320px; font-size:12px;"></span>
                <button type="button" onclick="copyMagnetFromModal()">Copy Magnet</button>
            </div>
            <div class="modal-buttons" style="display:flex; justify-content:space-between; align-items:center; margin-top:16px;">
                <a href="/settings" class="btn-setup" style="font-size:12px; text-decoration:none; margin-right:auto;">⚙️ Configure Downloader in Settings</a>
                <button type="button" class="btn-close" onclick="closeModal()">Close</button>
            </div>
        </div>
    </div>

    <div class="toast-container" id="toastContainer"></div>

    <script>
        const cards = document.querySelectorAll('.card');
        const sourcePills = document.querySelectorAll('.source-pill');
        const timeframePills = document.querySelectorAll('.timeframe-pill');
        const rows = document.querySelectorAll('#mediaTableBody tr');
        const visibleCountSpan = document.getElementById('visibleCount');
        const resetBtn = document.getElementById('resetBtn');
        const noMatchesDiv = document.getElementById('noMatches');

        let currentFilterType = 'all';
        let currentFilterVal = '';
        let currentSourceFilter = 'all';
        let currentTimeframeFilter = 'all';
        let currentActiveMagnet = '';

        function showToast(msg, type) {{
            const c = document.getElementById('toastContainer');
            const t = document.createElement('div');
            t.className = 'toast ' + (type || 'info');
            t.textContent = msg;
            c.appendChild(t);
            setTimeout(() => {{ t.remove(); }}, 5000);
        }}

        function openModal(title, magnet) {{
            currentActiveMagnet = magnet || '';
            document.getElementById('modalTitle').textContent = 'Request: ' + title;
            document.getElementById('modalMagnetText').textContent = magnet || 'No direct magnet URI available.';
            document.getElementById('fallbackModal').style.display = 'flex';
        }}

        function closeModal() {{
            document.getElementById('fallbackModal').style.display = 'none';
        }}

        function copyMagnetFromModal() {{
            if (!currentActiveMagnet) {{
                showToast('No magnet URI available for this release.', 'error');
                return;
            }}
            navigator.clipboard.writeText(currentActiveMagnet);
            showToast('✓ Magnet link copied to clipboard!', 'success');
        }}

        async function requestMedia(btn, imdbId, title, year) {{
            btn.disabled = true;
            btn.textContent = 'Requesting...';
            btn.className = 'btn-request loading';

            try {{
                const magnet = btn.getAttribute('data-magnet') || '';
                let url = '/api/request?imdb=' + encodeURIComponent(imdbId) + '&title=' + encodeURIComponent(title) + '&year=' + year;
                if (magnet) {{
                    url += '&magnet=' + encodeURIComponent(magnet);
                }}
                const res = await fetch(url, {{ method: 'POST' }});
                const data = await res.json();

                if (data.success) {{
                    btn.textContent = '✓ Requested';
                    btn.className = 'btn-request btn-success';
                    showToast(data.message, 'success');
                }} else if (data.unconfigured) {{
                    btn.disabled = false;
                    btn.textContent = 'Request';
                    btn.className = 'btn-request';
                    openModal(title, data.magnet || magnet);
                }} else {{
                    btn.textContent = 'Failed';
                    btn.className = 'btn-request btn-failed';
                    btn.disabled = false;
                    showToast(data.message, 'error');
                }}
            }} catch (err) {{
                btn.disabled = false;
                btn.textContent = 'Request';
                btn.className = 'btn-request';
                showToast('Failed to reach downloader API.', 'error');
            }}
        }}

        async function startScan() {{
            const btn = document.getElementById('btnGo');
            const txt = document.getElementById('btnGoText');
            btn.disabled = true;
            btn.className = 'btn-go loading';
            txt.textContent = 'Scanning Live Sources...';

            try {{
                const res = await fetch('/api/scan', {{ method: 'POST' }});
                const data = await res.json();
                if (data.success) {{
                    showToast('Scan completed! Refreshing release feed...', 'success');
                    setTimeout(() => {{ window.location.reload(); }}, 1200);
                }} else {{
                    showToast('Scan error: ' + data.message, 'error');
                    btn.disabled = false;
                    btn.className = 'btn-go';
                    txt.textContent = 'Scan Releases Now';
                }}
            }} catch (e) {{
                showToast('Server connection error during scan.', 'error');
                btn.disabled = false;
                btn.className = 'btn-go';
                txt.textContent = 'Scan Releases Now';
            }}
        }}

        function applyFilters() {{
            let visible = 0;
            const now = new Date();
            let cutoff = null;
            if (currentTimeframeFilter === '7d') {{
                cutoff = new Date();
                cutoff.setDate(now.getDate() - 7);
            }} else if (currentTimeframeFilter === '30d') {{
                cutoff = new Date();
                cutoff.setDate(now.getDate() - 30);
            }} else if (currentTimeframeFilter === '90d') {{
                cutoff = new Date();
                cutoff.setDate(now.getDate() - 90);
            }} else if (currentTimeframeFilter === '365d') {{
                cutoff = new Date();
                cutoff.setDate(now.getDate() - 365);
            }}

            rows.forEach(r => {{
                let matchStatus = false;
                if (currentFilterType === 'all') {{
                    matchStatus = true;
                }} else if (currentFilterType === 'status') {{
                    matchStatus = (r.getAttribute('data-status') === currentFilterVal);
                }} else if (currentFilterType === 'genre') {{
                    matchStatus = (r.getAttribute('data-genre') === currentFilterVal);
                }}

                let matchSource = false;
                const rowSources = (r.getAttribute('data-sources') || '').toLowerCase().split(',');
                if (currentSourceFilter === 'all') {{
                    matchSource = true;
                }} else {{
                    matchSource = rowSources.includes(currentSourceFilter.toLowerCase());
                }}

                let matchTimeframe = true;
                if (cutoff !== null) {{
                    const relDateStr = r.getAttribute('data-release-date');
                    const yearStr = r.getAttribute('data-year');
                    if (relDateStr && relDateStr.length >= 10) {{
                        const rowDate = new Date(relDateStr);
                        matchTimeframe = (!isNaN(rowDate.getTime()) && rowDate >= cutoff);
                    }} else if (yearStr && parseInt(yearStr) > 0) {{
                        matchTimeframe = (parseInt(yearStr) >= cutoff.getFullYear());
                    }} else {{
                        matchTimeframe = false;
                    }}
                }}

                const show = matchStatus && matchSource && matchTimeframe;
                r.style.display = show ? '' : 'none';
                if (show) visible++;
            }});

            visibleCountSpan.textContent = visible;
            noMatchesDiv.style.display = (visible === 0) ? 'block' : 'none';
            const isFiltered = (currentFilterType !== 'all') || (currentSourceFilter !== 'all') || (currentTimeframeFilter !== 'all');
            resetBtn.style.display = isFiltered ? 'inline-block' : 'none';
        }}

        function resetFilter() {{
            currentFilterType = 'all';
            currentFilterVal = '';
            currentSourceFilter = 'all';
            currentTimeframeFilter = 'all';

            cards.forEach(c => c.classList.remove('active'));
            const totalCard = document.querySelector('.card[data-filter="all"]');
            if (totalCard) totalCard.classList.add('active');

            sourcePills.forEach(p => p.classList.remove('active'));
            const allPill = document.querySelector('.source-pill[data-source="all"]');
            if (allPill) allPill.classList.add('active');

            timeframePills.forEach(p => p.classList.remove('active'));
            const allTimePill = document.querySelector('.timeframe-pill[data-timeframe="all"]');
            if (allTimePill) allTimePill.classList.add('active');

            applyFilters();
        }}

        cards.forEach(card => {{
            card.addEventListener('click', () => {{
                const filter = card.getAttribute('data-filter') || 'all';
                const isCurrentlyActive = card.classList.contains('active');

                if (isCurrentlyActive && filter !== 'all') {{
                    currentFilterType = 'all';
                    currentFilterVal = '';
                    cards.forEach(c => c.classList.remove('active'));
                    const totalCard = document.querySelector('.card[data-filter="all"]');
                    if (totalCard) totalCard.classList.add('active');
                    applyFilters();
                    return;
                }}

                cards.forEach(c => c.classList.remove('active'));
                card.classList.add('active');

                if (filter === 'all') {{
                    currentFilterType = 'all';
                    currentFilterVal = '';
                }} else {{
                    const [type, val] = filter.split(':');
                    currentFilterType = type;
                    currentFilterVal = val;
                }}
                applyFilters();
            }});
        }});

        sourcePills.forEach(pill => {{
            pill.addEventListener('click', () => {{
                const source = pill.getAttribute('data-source') || 'all';
                const isCurrentlyActive = pill.classList.contains('active');

                if (isCurrentlyActive && source !== 'all') {{
                    currentSourceFilter = 'all';
                    sourcePills.forEach(p => p.classList.remove('active'));
                    const allPill = document.querySelector('.source-pill[data-source="all"]');
                    if (allPill) allPill.classList.add('active');
                    applyFilters();
                    return;
                }}

                sourcePills.forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                currentSourceFilter = source;
                applyFilters();
            }});
        }});

        timeframePills.forEach(pill => {{
            pill.addEventListener('click', () => {{
                const tf = pill.getAttribute('data-timeframe') || 'all';
                const isCurrentlyActive = pill.classList.contains('active');

                if (isCurrentlyActive && tf !== 'all') {{
                    currentTimeframeFilter = 'all';
                    timeframePills.forEach(p => p.classList.remove('active'));
                    const allPill = document.querySelector('.timeframe-pill[data-timeframe="all"]');
                    if (allPill) allPill.classList.add('active');
                    applyFilters();
                    return;
                }}

                timeframePills.forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                currentTimeframeFilter = tf;
                applyFilters();
            }});
        }});
    </script>
</body>
</html>"""


# -----------------------------------------------------------------------------
# Web Routes
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Landing route: redirects to /dashboard if logged in, otherwise /login."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serves the Login / Registration page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return HTMLResponse(render_login_page())


@app.post("/api/auth/login")
async def api_login(auth: AuthModel, response: Response):
    """Authenticates a user and sets an HTTP session cookie."""
    ok, token = user_manager.authenticate_user(auth.username, auth.password)
    if ok and token:
        response = JSONResponse(content={"success": True, "message": "Login successful"})
        response.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
        return response
    return JSONResponse(status_code=401, content={"success": False, "message": "Invalid email or password"})


@app.post("/api/auth/register")
async def api_register(auth: AuthModel, response: Response):
    """Registers a new user and sets session cookie."""
    ok, msg = user_manager.register_user(auth.username, auth.password)
    if ok:
        _, token = user_manager.authenticate_user(auth.username, auth.password)
        resp = JSONResponse(content={"success": True, "message": msg})
        if token:
            resp.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
        return resp
    return JSONResponse(status_code=400, content={"success": False, "message": msg})


@app.get("/api/auth/logout")
async def api_logout(request: Request):
    """Logs out user and clears cookie."""
    token = request.cookies.get("session_token")
    user_manager.logout_token(token)
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session_token")
    return resp


@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    """Serves user configuration page."""
    username = get_current_user(request)
    if not username:
        return RedirectResponse(url="/login", status_code=302)

    settings = user_manager.get_user_settings(username)
    return HTMLResponse(render_settings_page(username, settings))


@app.get("/api/settings")
async def api_get_settings(username: str = Depends(require_user)):
    """Returns current user's settings."""
    return user_manager.get_user_settings(username)


@app.post("/api/settings")
async def api_save_settings(data: SettingsUpdateModel, username: str = Depends(require_user)):
    """Updates user configuration settings."""
    existing = user_manager.get_user_settings(username)
    updated = data.model_dump(exclude_unset=True)
    existing.update(updated)
    user_manager.save_user_settings(username, existing)
    return {"success": True, "message": "Settings saved successfully."}


@app.post("/api/plex/oauth/start")
async def api_plex_oauth_start(username: str = Depends(require_user)):
    """Initiates an official Plex OAuth session for 1-click token creation."""
    try:
        from plexapi.myplex import MyPlexPinLogin
        headers = {
            "X-Plex-Product": "New Media Analyser",
            "X-Plex-Version": "1.0",
            "X-Plex-Device": "Web Browser",
            "X-Plex-Device-Name": "New Media Analyser",
        }
        pin = MyPlexPinLogin(oauth=True, headers=headers)
        pin._getCode()
        pin_id = str(pin._id)
        oauth_url = pin.oauthUrl()
        active_plex_pins[pin_id] = pin
        return {"success": True, "pin_id": pin_id, "oauth_url": oauth_url}
    except Exception as e:
        logger.error(f"Error starting Plex OAuth session: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.get("/api/plex/oauth/poll")
async def api_plex_oauth_poll(pin_id: str, username: str = Depends(require_user)):
    """Polls Plex to verify if the user authorized the login PIN."""
    pin = active_plex_pins.get(pin_id)
    if not pin:
        return JSONResponse(status_code=404, content={"success": False, "message": "Plex PIN session expired or not found"})
    try:
        authenticated = pin._checkLogin()
        if authenticated and pin.token:
            token = pin.token
            del active_plex_pins[pin_id]
            settings = user_manager.get_user_settings(username)
            settings["plex_token"] = token
            user_manager.save_user_settings(username, settings)
            return {"success": True, "authenticated": True, "token": token}
        return {"success": True, "authenticated": False}
    except Exception as e:
        logger.error(f"Error checking Plex PIN status: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/test/media-server")
async def api_test_media_server(data: Dict[str, Any], username: str = Depends(require_user)):
    """Tests connection to the specified media server and synchronizes profile."""
    server_type = (data.get("media_server") or "plex").lower()
    try:
        if server_type == "plex":
            url = data.get("plex_url") or "http://localhost:32400"
            token = data.get("plex_token") or ""
            provider = PlexServerProvider(url=url, token=token)
        elif server_type == "jellyfin":
            url = data.get("jellyfin_url") or "http://localhost:8096"
            key = data.get("jellyfin_key") or ""
            provider = JellyfinServerProvider(url=url, api_key=key)
        elif server_type == "emby":
            url = data.get("emby_url") or "http://localhost:8096"
            key = data.get("emby_key") or ""
            provider = EmbyServerProvider(url=url, api_key=key)
        elif server_type == "all":
            provider = CombinedServerProvider()
        else:
            return {"success": False, "message": f"Unsupported server type '{server_type}'"}

        if not provider.connect():
            return {"success": False, "message": f"Could not connect to {server_type} server. Please verify URL and credentials."}

        # Generate and save user profile
        profile = provider.generate_profile()
        profile_file = PROFILES_DIR / f"{username}_library_profile.json"
        with open(profile_file, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)

        total_mov = profile.get("total_movies", len(profile.get("lookup_indices", {}).get("movies", {})))
        total_shows = profile.get("total_shows", len(profile.get("lookup_indices", {}).get("shows", {})))
        genres_count = len(profile.get("taste_profile", {}).get("top_overall_genres", {}))

        return {
            "success": True,
            "message": f"Connected to {server_type.capitalize()}! Successfully indexed {total_mov} movies, {total_shows} shows, and {genres_count} genres."
        }
    except Exception as e:
        logger.error(f"Error testing media server for {username}: {e}")
        return {"success": False, "message": str(e)}


@app.post("/api/test/downloader")
async def api_test_downloader(data: Dict[str, Any], username: str = Depends(require_user)):
    """Tests connection to the configured downloader."""
    client_name = (data.get("download_client") or "radarr").lower()
    try:
        downloader = get_user_downloader(data)
        ok, msg = downloader.test_connection()
        return {"success": ok, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    """Serves the personalized Dashboard."""
    username = get_current_user(request)
    if not username:
        return RedirectResponse(url="/login", status_code=302)

    settings = user_manager.get_user_settings(username)
    raw_releases = load_cached_raw_releases()

    # If no cached raw releases in data/, check if output/media_report.html or previous run exists
    if not raw_releases:
        # Check if scraper can load from previous run
        pass

    # Evaluate releases against THIS user's specific media server library & filter rules!
    user_filter = get_user_media_filter(username)
    personalized_items = []
    for raw in raw_releases:
        item = dict(raw)
        user_filter.evaluate(item)
        personalized_items.append(item)

    sanitized = scraper.sanitize_output(personalized_items)
    return HTMLResponse(render_dashboard_page(username, settings, sanitized))


@app.post("/api/scan")
async def api_scan(username: str = Depends(require_user)):
    """Triggers an asynchronous scrape across all configured sources."""
    if scan_lock.locked():
        return {"success": False, "message": "A scan is already in progress. Please wait."}

    async with scan_lock:
        scan_state["is_scanning"] = True
        scan_state["progress_message"] = "Scraping releases..."
        try:
            logger.info(f"User '{username}' triggered release scan.")
            releases = await scraper.fetch_media_index()
            save_cached_raw_releases(releases)
            scan_state["total_items"] = len(releases)
            scan_state["is_scanning"] = False
            scan_state["progress_message"] = "Completed"
            return {"success": True, "message": f"Successfully scraped and indexed {len(releases)} releases.", "count": len(releases)}
        except Exception as e:
            scan_state["is_scanning"] = False
            scan_state["progress_message"] = f"Error: {e}"
            logger.error(f"Scan failed: {e}")
            return {"success": False, "message": f"Scan failed: {e}"}


@app.post("/api/request")
async def api_request(
    request: Request,
    imdb: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    magnet: Optional[str] = None,
    username: str = Depends(require_user)
):
    """1-Click request endpoint dispatching to the logged-in user's downloader."""
    # Support both JSON payload and Query Parameters
    try:
        body = await request.json()
        if isinstance(body, dict):
            imdb = body.get("imdb_id") or body.get("imdb") or imdb
            title = body.get("title") or title
            year = body.get("year") or year
            magnet = body.get("magnet") or magnet
    except Exception:
        pass

    settings = user_manager.get_user_settings(username)
    client_name = (settings.get("download_client") or "radarr").lower()

    clean_imdb = (imdb or "").strip()
    if clean_imdb in ("N/A", "None", ""):
        clean_imdb = None

    clean_title = (title or "").strip() or "Movie"
    clean_year = year if year and int(year) > 1900 else None
    clean_magnet = (magnet or "").strip() or None

    # Check if user has actually configured a downloader
    has_downloader = False
    if client_name == "radarr":
        has_downloader = bool(settings.get("radarr_api_key"))
    elif client_name == "rutorrent":
        has_downloader = bool(settings.get("rutorrent_url") and (settings.get("rutorrent_user") or settings.get("rutorrent_password")))
    elif client_name in ("qbittorrent", "qbit"):
        has_downloader = bool(settings.get("qbittorrent_url") and (settings.get("qbittorrent_api_key") or settings.get("qbittorrent_user")))

    if not has_downloader:
        return {
            "success": False,
            "unconfigured": True,
            "magnet": clean_magnet or "",
            "message": "Downloader not configured yet in Settings. Copy the direct magnet URI below to download manually:"
        }

    downloader = get_user_downloader(settings)
    logger.info(f"User '{username}' requesting: '{clean_title}' ({clean_year}) [IMDb: {clean_imdb}]")
    result: DownloadResult = downloader.add_movie(
        imdb_id=clean_imdb,
        title=clean_title,
        year=clean_year,
        magnet=clean_magnet
    )

    return {
        "success": result.success,
        "unconfigured": False,
        "message": result.message,
        "client": result.client,
        "title": result.title
    }


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    print(f"\n🚀 Starting New Media Analyser Web Server on http://{host}:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=False)
