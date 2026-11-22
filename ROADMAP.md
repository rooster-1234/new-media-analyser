# Project Roadmap

This document outlines the planned milestones, upcoming features, and technical architecture decisions for **New Media Analyser**.

---

## Current Status (v0.6.0)
- [x] **Unified Web Application (`app.py`)**: Self-contained FastAPI + Uvicorn server running on port 8080.
- [x] **Flat-File User Authentication (`/login`)**: Email-based login and registration with password hashing (SHA-256 + salt) and cookie sessions.
- [x] **Web Configuration Page (`/settings`)**: Live connection testing and library syncing for Plex/Jellyfin/Emby, Plex 1-click OAuth PIN, downloader testing for Radarr/ruTorrent/qBittorrent, and taste preference sliders.
- [x] **Personalized Release Dashboard (`/dashboard`)**: **"Scan Releases Now" (GO)** button with live counter, multi-source release monitoring, and 1-click download dispatch.
- [x] **Visual Movie Posters & Hover Zoom**: 44×64px thumbnails in media cell expanding to 160×240px artwork preview on mouse hover.
- [x] **Relative Timeframe Filtering**: Filter by Last 7 Days, Last 30 Days, Last 90 Days, Last 12 Months, Specific Year, or Any.
- [x] **Stacked Dual-Source Links**: Direct site record links to The Pirate Bay (`PB ↗`) and `uIndex ↗` stacked vertically in compact column.
- [x] **Table Column Sorting & Long Date Display**: Click-to-sort by Title, Released, Size, IMDb Score, and Filter Status with human-readable long date formatting (e.g. 6 June 2026).
- [x] **Containerisation & ARM64 Pi 5 Support**: Docker & Docker Compose setup with persistent data volumes running live on Raspberry Pi 5.

---

## Next Milestone: v0.7.0 (Code Architecture & Frontend Decoupling)
Refactor `app.py` from inline prototype HTML/CSS strings into standard FastAPI Jinja2 templates and dedicated static assets.

- [ ] **Template Engine Integration (`templates/`)**
  - [ ] Configure `Jinja2Templates` in `app.py`.
  - [ ] Create `templates/base.html` for shared layout, navbar, session badges, and brand vector logo.
  - [ ] Migrate `render_dashboard_page()` to `templates/dashboard.html`.
  - [ ] Migrate `render_settings_page()` to `templates/settings.html`.
  - [ ] Migrate `render_login_page()` to `templates/login.html`.
- [ ] **Static Assets Extraction (`static/`)**
  - [ ] Mount `/static` route via FastAPI `StaticFiles`.
  - [ ] Extract inlined CSS into `static/css/theme.css` with browser caching headers.
  - [ ] Extract client-side JavaScript into `static/js/dashboard.js` and `static/js/settings.js`.
  - [ ] Move SVG logo to `static/img/logo.svg`.
- [ ] **Docker & Deployment Updates**
  - [ ] Update `docker-compose.yml` and `Dockerfile` to mount `./templates` and `./static`.
  - [ ] Verify test suite passes without regressions.
  - [ ] Slim `app.py` from ~2,550 lines down to ~350 lines of clean routing and API handlers.

---

## Phase 1: Web Application & Configuration UI [Completed - v0.5.0]

Transition from standalone CLI scripts and static HTML reports to a unified, self-contained local web application.

- [x] **Unified Web Backend (FastAPI / Uvicorn)**
  - [x] Serve the interactive media dashboard directly from a single web port (`http://localhost:8080`).
  - [x] Consolidate downloader API routes (`/api/request`, `/api/scan`) into the web server.
- [x] **Welcome & Authentication (`/login`)**
  - [x] Flat-file user management (`data/users.json`) with password hashing and session cookies.
- [x] **Interactive Web Configuration Page (`/settings`)**
  - [x] **Media Server Settings**: Dropdown to select media server type (Plex / Jellyfin / Emby / Combined) with connection test and library synchronization.
  - [x] **Downloader Settings**: Inputs for Radarr and ruTorrent with live connectivity verification buttons.
  - [x] **Filter & Preference Sliders**: Visual controls for minimum IMDb score, release year, English-only toggle, and multi-select tags for disliked genres.
- [x] **Personalized Dashboard & Live Scanner (`/dashboard`)**
  - [x] Header status indicators for logged-in user, active server, and active downloader.
  - [x] **"Scan Releases Now" (GO)** button with live status updates.
  - [x] 1-Click Request dispatcher routed to the user's configured downloader.
- [x] **Per-User Configuration Storage**
  - [x] Persistent JSON config per user in `data/profiles/<username>_settings.json` with `.env` fallback defaults.

---

## Phase 2: Containerisation & Pi 5 / NAS Deployment [Completed - v0.6.0]

Package the entire platform for simple, one-click self-hosting in homelabs, Raspberry Pi 5, and NAS environments.

- [x] **Docker Packaging**
  - [x] Optimized multi-arch `Dockerfile` with Python 3.12 and headless Playwright / Chromium dependencies.
  - [x] `docker-compose.yml` with persistent volume mappings:
    - `./data:/app/data` — For user accounts, library profiles, scraped cache, and SQLite IMDb ratings.
    - Hot-mounted source code files for instantaneous updates without image rebuilds.
- [x] **Raspberry Pi 5 Test Deployment**
  - [x] Deployed and verified container on Raspberry Pi 5 (`raspberrypi2.local:8080`).
  - [x] Verified LAN communication with local Plex server and remote downloaders.
- [ ] **Automated Background Scheduler**
  - [ ] Implement an internal cron/scheduler inside the container to auto-scan for new releases periodically (e.g. every 6, 12, or 24 hours).

---

## Phase 3: Multi-Platform Media Server Support (Jellyfin & Emby) [Completed - v0.3.0]

Extend library analysis and duplicate detection beyond Plex to the open-source media server ecosystem.

- [x] **Abstract Media Server Interface (`BaseMediaServer`)**
  - [x] Standardize core contract: `connect()`, `generate_profile()`.
- [x] **Jellyfin Integration**
  - [x] Connect via Jellyfin REST API using API tokens (`X-Emby-Token` / `Authorization`).
  - [x] Catalog movies and TV shows, extract genre distributions, and build duplicate lookup indices.
- [x] **Emby Integration**
  - [x] Connect via Emby REST API using user tokens (`X-Emby-Token`).
  - [x] Catalog media library and synchronize preference profile.
- [x] **Combined Multi-Server Support (`MEDIA_SERVER=all`)**
  - [x] Scan and deduplicate library items across Plex, Jellyfin, and Emby simultaneously.

---

## Phase 4: Additional Indexers & Torrent Sources [In Progress - v0.4.0]

Broaden the release scanning coverage, merge multi-source feeds, and implement robust proxy handling for protected sources.

- [x] **uIndex.org Integration**
  - [x] Build Playwright stealth table parser for uIndex top movie release lists.
  - [x] Extract direct magnets, clean title/year, file sizes, and live seeders.
  - [x] Cinemeta search API integration for instant IMDb ID resolution on unlinked releases.
  - [x] Unified deduplication and multi-source filtering in `media_report.html`.
- [ ] **ext.to Integration (Cloudflare Bypass)**
  - [ ] Evaluate and implement Cloudflare challenge handling (e.g. FlareSolverr proxy container or `curl_cffi` browser fingerprinting).
- [ ] **Prowlarr / Jackett Multi-Indexer Integration** *(Recommended)*
  - [ ] Add direct API support for Prowlarr / Jackett indexer managers.
  - [ ] Allow users to query 500+ private and public trackers simultaneously without maintaining separate custom scrapers.

---

## Phase 5: Additional Downloader Clients

- [x] **qBittorrent (WebAPI v2)** [Completed - v0.5.1]
  - [x] Stateless API Key (Bearer token) support for qBittorrent v5.2.0+.
  - [x] Cookie-based session authentication fallback (`/api/v2/auth/login`).
  - [x] Reverse proxy HTTP Basic Auth support (for password-protected seedbox routes like PulsedMedia).
  - [x] Category and custom download path mapping with auto-start controls.
- [ ] **Seerr (Overseerr / Jellyseerr)**
  - [ ] Submit media requests directly to Overseerr/Jellyseerr for automated approval and workflow routing.
- [ ] **Sonarr Integration**
  - [ ] Full TV show tracking, season monitoring, and episode matching.

---

## Phase 6: Codebase Architecture & Frontend Decoupling [Planned - v0.7.0]

Decouple `app.py` by separating presentation concerns (HTML templates, CSS stylesheets, client JS) from application and API routing.

- [ ] **Jinja2 Template Integration (`templates/`)**
  - [ ] Configure `Jinja2Templates` and create shared base layout (`templates/base.html`) for brand header, navbar, and session status.
  - [ ] Migrate `render_dashboard_page()` to `templates/dashboard.html`.
  - [ ] Migrate `render_settings_page()` to `templates/settings.html`.
  - [ ] Migrate `render_login_page()` to `templates/login.html`.
- [ ] **Static Assets Separation (`static/`)**
  - [ ] Mount `/static` route via FastAPI `StaticFiles`.
  - [ ] Extract dark theme styles, animations, and modal rules into `static/css/theme.css`.
  - [ ] Extract client-side JavaScript into dedicated files (`static/js/dashboard.js`, `static/js/settings.js`).
  - [ ] Save standalone vector logo to `static/img/logo.svg`.
- [ ] **Deployment & Maintenance Optimization**
  - [ ] Update `docker-compose.yml` to mount `./templates` and `./static`.
  - [ ] Reduce `app.py` from ~2,550 lines down to ~350 lines of clean routing and API handlers.
  - [ ] Enable browser-side caching for stylesheets and static assets.

---

## Future / Cloud Architecture Considerations

- [ ] **Google Cloud / Hosted Multi-User SaaS Evaluation**
  - Multi-tenancy architecture (Firestore / Cloud SQL).
  - KMS encryption for securing user seedbox and media server tokens.
  - Reverse proxy / tunnel requirements (Tailscale, Cloudflare Tunnels) to traverse residential CGNAT firewalls to reach private home NAS servers.
