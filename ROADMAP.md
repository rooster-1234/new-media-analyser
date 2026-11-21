# Project Roadmap

This document outlines the planned milestones, upcoming features, and technical architecture decisions for **New Media Analyser**.

---

## Current Status (v0.5.0)
- [x] **Unified Web Application (`app.py`)**: Self-contained FastAPI + Uvicorn server running on port 8080.
- [x] **Flat-File User Authentication (`/login`)**: Login and registration with password hashing (SHA-256 + salt) and cookie sessions.
- [x] **Web Configuration Page (`/settings`)**: Live connection testing and library syncing for Plex/Jellyfin/Emby, downloader testing for Radarr/ruTorrent, and taste preference sliders.
- [x] **Personalized Release Dashboard (`/dashboard`)**: **"Scan Releases Now" (GO)** button, multi-source release monitoring, and 1-click download dispatch.

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

## Phase 2: Containerisation & NAS Deployment

Package the entire platform for simple, one-click self-hosting in homelabs and NAS environments.

- [ ] **Docker Packaging**
  - [ ] Write optimized `Dockerfile` (Python + Playwright / Chromium headless dependencies).
  - [ ] Write `docker-compose.yml` with persistent volume mappings:
    - `/config` — For `settings.json`, Plex/downloader credentials, and IMDb cache database.
    - `/output` — For generated reports and logs.
- [ ] **NAS Test Deployment**
  - [ ] Deploy container to local NAS (via Docker / Portainer / Synology Container Manager).
  - [ ] Verify zero-latency local LAN communication with local Plex server (`http://192.168.0.2:32400`).
  - [ ] Test end-to-end download flow from NAS container to remote seedbox (ruTorrent).
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

## Future / Cloud Architecture Considerations

- [ ] **Google Cloud / Hosted Multi-User SaaS Evaluation**
  - Multi-tenancy architecture (Firestore / Cloud SQL).
  - KMS encryption for securing user seedbox and media server tokens.
  - Reverse proxy / tunnel requirements (Tailscale, Cloudflare Tunnels) to traverse residential CGNAT firewalls to reach private home NAS servers.
