# Project Roadmap

This document outlines the planned milestones, upcoming features, and technical architecture decisions for **New Media Analyser**.

---

## Current Status (v0.2.0)
- [x] **Plex Library Analyzer**: Complete catalog analysis, genre preference extraction, director tracking, and title indexing.
- [x] **Plex OAuth PIN Flow**: 1-click browser sign-in and automatic local server discovery (`NAS`).
- [x] **Playwright Media Scraper**: Stealth scraping of top new releases with Cinemeta and IMDb datasets cache.
- [x] **Match & Filter Engine**: Intelligent preference weighting (High/Medium/Low), duplicate suppression, score & year thresholds.
- [x] **Interactive Dashboard**: Static HTML report (`media_report.html`) with live filtering, search, and release size display.
- [x] **Media Download Dispatcher**: 
  - [x] Radarr v3 integration (movie lookup, quality profile selection, root folder discovery).
  - [x] ruTorrent integration (direct magnet dispatch via HTTP Basic Auth, automatic start handling).
  - [x] Local 1-click API server bridge (`127.0.0.1:7890`) for report actions.

---

## Phase 1: Web Application & Configuration UI

Transition from standalone CLI scripts and static HTML reports to a unified, self-contained local web application.

- [ ] **Unified Web Backend (FastAPI / Flask)**
  - [ ] Serve the interactive media dashboard directly from a single web port (e.g. `http://localhost:8080`).
  - [ ] Consolidate the downloader API routes (`/api/request`, `/api/scan`) into the web server.
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

## Phase 3: Multi-Platform Media Server Support (Jellyfin & Emby)

Extend library analysis and duplicate detection beyond Plex to the open-source media server ecosystem.

- [ ] **Abstract Media Server Interface (`BaseMediaServer`)**
  - [ ] Standardize core contract: `connect()`, `get_libraries()`, `get_profile()`, `is_duplicate()`.
- [ ] **Jellyfin Integration**
  - [ ] Connect via Jellyfin REST API using API tokens or Quick Connect PIN login.
  - [ ] Catalog movies and TV shows, extract genre distributions, and build duplicate lookup indices.
- [ ] **Emby Integration**
  - [ ] Connect via Emby REST API using user tokens or Emby Connect.
  - [ ] Catalog media library and synchronize preference profile.

---

## Phase 4: Additional Indexers & Torrent Sources

Broaden the release scanning coverage and implement robust proxy handling for protected sources.

- [ ] **uIndex.org Integration**
  - [ ] Build parser / API scraper for uIndex release lists.
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
