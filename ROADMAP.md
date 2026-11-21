# Project Roadmap

This document outlines the planned milestones, upcoming features, and technical architecture decisions for **New Media Analyser**.

---

## Current Status (v0.4.0)
- [x] **Plex Library Analyzer**: Complete catalog analysis, genre preference extraction, director tracking, and title indexing.
- [x] **Plex OAuth PIN Flow**: 1-click browser sign-in and automatic local server discovery (`NAS`).
- [x] **Multi-Server Provider Engine**: Pluggable providers for **Plex**, **Jellyfin**, and **Emby** (`media_servers.py`) with single-server and combined modes.
- [x] **Multi-Source Scraping Pipeline**: Concurrent stealth scraping of **The Pirate Bay** (`SITE_SCAN1`) and **uIndex.org** (`SITE_SCAN2`) with fast Cinemeta catalog lookup.
- [x] **Smart Cross-Source Deduplication**: Merges multi-source entries and multi-resolution releases into a unified candidate list (199 raw releases -> 125 distinct items).
- [x] **Unified Interactive Dashboard**: Consolidated table layout in `media_report.html` with source badges (`TPB`, `uIndex`, `TPB + uIndex`), 1-click source filter pills, dynamic library badges (`In Plex`/`In Jellyfin`/`In Emby`), and 1-click downloader request buttons.
- [x] **Match & Filter Engine**: Intelligent preference weighting (High/Medium/Low), duplicate suppression, score & year thresholds.
- [x] **Media Download Dispatcher**: 
  - [x] Radarr v3 integration (movie lookup, quality profile selection, root folder discovery).
  - [x] ruTorrent integration (direct magnet dispatch via HTTP Basic Auth, automatic start handling).
  - [x] Local 1-click API server bridge (`127.0.0.1:7890`) for report actions.

---

## Phase 1: Web Application & Configuration UI

Transition from standalone CLI scripts and static HTML reports to a unified, self-contained local web application.

- [ ] **Unified Web Backend (FastAPI / Flask)**
  - [ ] Serve the interactive media dashboard directly from a single web port (e.g. `http://localhost:8080`).
  - [x] Consolidate downloader API routes (`/api/request`, `/api/status`) into a local server bridge (`retrieve_media.py --serve` on `127.0.0.1:7890`).
- [ ] **Interactive Web Configuration Page (`/settings`)**
  - [ ] **Media Server Settings**: Dropdown to select media server type (Plex / Jellyfin / Emby), connection test button, and 1-click OAuth login.
  - [ ] **Downloader Settings**: Form for Radarr, Sonarr, ruTorrent, and Seerr API keys, paths, and connection verification buttons.
  - [ ] **Filter & Preference Sliders**: Visual controls for minimum IMDb score, release year, English-only toggle, and multi-select tags for disliked genres.
  - [ ] **Scraper Controls**: Manual "Scan Now" trigger and periodic background schedule settings.
- [ ] **Configuration Storage**
  - [ ] Support saving and loading settings from a persistent JSON/YAML config (`/config/settings.json`) with `.env` fallback.

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
