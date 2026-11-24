# New Media Analyser

An intelligent, self-hosted media release radar and 1-click download dispatcher. It connects to your **Plex, Jellyfin, or Emby** library, learns your favorite genres, automatically detects duplicates, and matches newly released media from multiple sources against your personal taste profile.

---

## 💡 Why I Built This

Media discovery platforms like **Overseerr and Jellyseerr (Seerr)** are great for requesting known movies, but their discovery lists rely on public metadata databases (like TMDb) that often **lag behind real-world release drops**. Days or even weeks can pass before a newly available digital release actually shows up on their radars.

Like many media server owners, I found myself constantly checking "Top 48h" and "New Releases" lists on The Pirate Bay and tracker boards manually—cross-checking whether I already had a title in Plex, verifying IMDb ratings, checking for English audio, and manually copying magnet links.

**New Media Analyser automates that daily ritual**:
1. **Scouts live tracker feeds** directly the moment releases drop.
2. **Cross-references your library** (Plex, Jellyfin, or Emby) to instantly filter out movies you already own.
3. **Applies quality & taste filters** (IMDb score threshold, release year, English audio, and genre weighting).
4. **Dispatches with 1 click** to **Radarr, qBittorrent, or ruTorrent** (or copies clean magnet URIs if no downloader is configured).

---

## ✨ Features

- **🌐 Modern Web Application**: Self-contained FastAPI web app running on port `8080` with zero external web server dependencies (powered by Uvicorn).
- **👥 Multi-User Support**: Simple flat-file user authentication (`data/users.json`) with cryptographic password hashing (SHA-256 + salt) and cookie-based sessions. Perfect for letting friends test without sharing credentials.
- **📺 Multi-Platform Media Servers**: Pluggable provider engine supporting:
  - **Plex Media Server** (with 1-click browser OAuth sign-in and local server auto-discovery).
  - **Jellyfin** (REST API with batch title, season, and genre extraction).
  - **Emby** (REST API with library cataloging and duplicate lookup indices).
  - **Combined Mode** (scan and deduplicate across multiple media servers simultaneously).
- **🔍 Multi-Source Stealth Scraping**:
  - Scrapes **The Pirate Bay** (`SITE_SCAN1`) and **uIndex.org** (`SITE_SCAN2`) concurrently using Playwright with stealth evasions.
  - Direct extraction of magnet URIs, release sizes, and live seeder counts.
  - **Cinemeta Catalog Integration**: Instant, zero-delay IMDb resolution for releases that lack embedded IMDb links.
- **🖼️ Visual Movie Posters & Hover Zoom**:
  - 44×64px poster thumbnails integrated directly alongside movie titles and synopses.
  - Interactive hover zoom expands posters to a high-resolution 160×240px artwork preview with soft drop shadows and animations.
  - Zero-latency automated CDN resolution via Cinemeta and Metahub (`images.metahub.space`) with fallback placeholders.
- **📅 Release Date Filtering & Long Format Display**:
  - Parses official theatrical and streaming release dates, formatted in human-readable long date format (e.g. `6 June 2026`).
  - 1-click filter pills for **Within Last 7 Days**, **Last 30 Days**, **Last 90 Days**, **Last 12 Months**, **Specific Year**, or **Any**.
- **📊 Interactive Column Sorting**:
  - 1-click column heading sorting for **Title** (A-Z / Z-A), **Released** (newest / oldest), **Size** (numeric bytes sort), **IMDb Score** (highest / lowest), and **Filter Status**.
  - Interactive sort direction indicators (`▲`, `▼`, `↕`) with responsive hover styling.
- **🔗 Stacked Tracker Detail Links**:
  - Separate, compact pill buttons for **`PB ↗`** and **`uIndex ↗`** stacked vertically in the Source column.
  - Smart direct linking to original tracker detail records (`description.php` / `details.php`) with fallback keyword searches.
- **⚡ Smart Cross-Source Deduplication**:
  - Automatically merges identical releases and multiple resolution tiers (1080p, 720p) across different tracker sources into a single consolidated row.
  - 1-click interactive source filter pills (`All Sources`, `Pirate Bay`, `uIndex`, `Both Sources`).
- **🎯 Intelligent Match & Filter Engine**:
  - **Status Classification**: Automatically categorizes releases into `Consider`, `In Library` (already owned), `Low Score` (below IMDb threshold), `Non-English`, or `Too Old`.
  - **Taste Weighting**: Calculates High / Medium / Low genre match scores based on your server's actual library statistics.
  - **Avoided Genres**: 1-click toggle pills in Settings automatically flag unwanted genres as `Low` match.
- **🚀 1-Click Media Dispatcher & Plex OAuth**:
  - **1-Click Plex OAuth**: Authorize your Plex account via official browser popup with automatic token retrieval and linking.
  - **Radarr v3**: Automatic movie search, quality profile selection, and root folder assignment.
  - **qBittorrent (WebAPI v2)**: Native integration supporting modern v5.2.0+ API Key (Bearer token) authentication and session cookie fallback, with custom category and path mapping.
  - **ruTorrent**: Direct magnet dispatch via HTTP Basic Auth with automatic start handling.
  - **Manual Magnet Fallback**: If a downloader isn't configured, 1-click copies the direct magnet link to clipboard.
- **💬 In-App Feedback System**:
  - Modal dialog accessible directly from the dashboard header for testers and collaborators to submit reactions and thoughts.
  - Interactive sentiment rating pills (😍 Love it, 👍 Good, 💡 Idea, 🪲 Bug) saved securely in `data/feedback.json`.
- **⭐ Authoritative IMDb Ratings & Daily Dataset**:
  - Local SQLite index of official IMDb daily exports (`datasets.imdbws.com`) queried directly with zero network lag, preventing stale cache discrepancies.
  - Enhanced foreign co-production filtering evaluating primary country of origin and OMDb language tags.
- **🎨 Brand Identity ("Radar Lens")**:
  - Vector icon combining search discovery (magnifier) with download stream (downward arrow).
  - Dynamic SVG favicon served directly to browser tabs (`/favicon.svg`).
- **👋 Zero-Friction Onboarding**: New users land directly on the live dashboard and can explore releases immediately; an onboarding banner gently guides them to Settings when ready.

---

## 🚀 Quick Start (Local)

### 1. Prerequisites
- Python 3.10+ (Python 3.12 recommended)
- Google Chrome or Chromium (installed automatically by Playwright)

### 2. Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/rooster-1234/new-media-analyser.git
cd new-media-analyser

# Create and activate virtual environment
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 3. Start the Web Server

```bash
python app.py
```

Open your browser and navigate to:
👉 **http://localhost:8080**

1. Click **"Register New User"** to create an account.
2. In **Settings** (`/settings`), connect your media server (Plex, Jellyfin, or Emby) and test your downloader.
3. On the **Dashboard** (`/dashboard`), click **"Scan Releases Now" (GO)** to pull live releases from all sources!

---

## 🛠️ Configuration & Settings

While all settings can be configured interactively from the web UI (`/settings`), defaults can also be pre-seeded in `.env`:

```ini
# Active Media Server Provider (plex, jellyfin, emby, or all)
MEDIA_SERVER=plex

# Plex Media Server
PLEX_URL=http://localhost:32400
PLEX_TOKEN=your_plex_token_here

# Jellyfin Media Server
JELLYFIN_URL=http://localhost:8096
JELLYFIN_KEY=your_jellyfin_api_key

# Emby Media Server
EMBY_URL=http://localhost:8096
EMBY_KEY=your_emby_api_key

# Downloader Client (radarr, rutorrent)
DOWNLOAD_CLIENT=radarr
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your_radarr_key

# Scraper Sources
SITE_SCAN1=https://thepiratebay.org/search.php?q=top100:48h_207
SITE_SCAN2=https://uindex.org/top.php?c=1&t=7d

# Filter Criteria
FILTER_EXCLUDE_EXISTING=true
FILTER_TIMEFRAME=365d              # 7d, 30d, 90d, 365d, year, or all
FILTER_ONLY_CURRENT_YEAR=true
FILTER_TARGET_YEAR=2026
FILTER_MIN_IMDB_SCORE=5.0
FILTER_REQUIRE_ENGLISH=true
FILTER_DISLIKED_GENRES=Fantasy, Romance
```

*(See [`.env.template`](.env.template) for complete documentation of all parameters).*

---

## 🍓 Raspberry Pi 5 & Linux Deployment

### Option A: Docker Compose (Recommended)

```bash
git clone https://github.com/rooster-1234/new-media-analyser.git
cd new-media-analyser
docker compose up -d
```
The application will automatically build with headless Chromium on ARM64 and start on `http://<your-pi-ip>:8080`.

### Option B: Native 24/7 Systemd Service

We provide an automated setup script that handles the Linux virtual environment, Playwright system shared libraries, and systemd registration:

```bash
git clone https://github.com/rooster-1234/new-media-analyser.git
cd new-media-analyser
chmod +x setup_service.sh
./setup_service.sh
```

Manage the service anytime:
```bash
sudo systemctl status new-media-analyser
sudo systemctl restart new-media-analyser
```

---

## 🌐 Remote Access for Friends

To let a friend test the application from outside your local network:

1. **Router Port Forwarding**:
   - Forward external port `8080` on your home router to your host machine's local IP address (`192.168.0.X`) on port `8080`.
   - Your friend visits `http://<your-public-ip>:8080`.
2. **Cloudflare Tunnel / Tailscale (Zero Port Forwarding)**:
   - Run `cloudflared tunnel --url http://localhost:8080` to generate a temporary, encrypted HTTPS URL (e.g. `https://media-test.trycloudflare.com`) that your friend can open from anywhere.
3. **Data Isolation**:
   - When your friend registers their account, their settings, server tokens, and library indices are stored in their own isolated file (`data/profiles/<friend>_settings.json`). They will never overwrite or see your personal library data!

---

## 📁 Project Architecture

```text
├── app.py                     # FastAPI web server, routes (/login, /settings, /dashboard, /api)
├── user_manager.py            # Flat-file authentication & per-user settings management
├── scraper.py                 # Multi-source scraper (TPB, uIndex) & deduplication engine
├── filter.py                  # MediaFilter: score, year, language, and taste profile matcher
├── media_servers.py           # Pluggable media server providers (Plex, Jellyfin, Emby, Combined)
├── plex_analyzer.py           # Plex API analyzer & official browser OAuth PIN flow
├── retrieve_media.py          # Downloader integration (Radarr v3, qBittorrent WebAPI, ruTorrent)
├── testing/                   # Automated test suite (unit, integration & web tests)
├── data/                      # Persistent storage (users, profiles, scraped cache, IMDb SQLite db)
├── Dockerfile                 # Container packaging with Playwright & Chromium
├── docker-compose.yml         # Compose configuration with persistent data volumes
├── setup_service.sh           # Raspberry Pi / Linux automated systemd service installer
├── requirements.txt           # Python package requirements
├── CHANGELOG.md               # Version history and milestone logs
└── ROADMAP.md                 # Project roadmap, upcoming features, and tasks
```

---

## 📋 Documentation & Tracking

- [CHANGELOG.md](CHANGELOG.md) — Documented release history and version changes.
- [ROADMAP.md](ROADMAP.md) — Planned milestones, containerisation, and feature roadmap.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
