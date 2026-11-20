# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Containerisation of the entire application suite using Docker and Docker Compose.
- Automated periodic background scraping inside the containerized web service.
- Seerr (Overseerr / Jellyseerr) integration for movie and TV media requests.
- Sonarr integration for automated TV series tracking and downloads.

---

## [0.2.0] - 2026-11-20

### Added
- **Plex OAuth Browser Sign-in (`--login`)**: Added official Plex OAuth PIN authentication to `plex_analyzer.py`, allowing users to log in through their web browser without manually extracting `X-Plex-Token`.
- **Plex Server Auto-Discovery**: Automatically discovers connected Plex Media Servers (including local network URIs) and saves both `PLEX_TOKEN` and `PLEX_URL` directly to `.env`.
- **ruTorrent Downloader Integration**: Direct torrent client support in `retrieve_media.py` via HTTP Basic Auth.
- **Direct Magnet Extraction**: Scraper now extracts torrent magnet links directly from source listings and binds them to the interactive UI.
- **Index-Free Magnet Fallback**: Implemented automatic fallback to `apibay.org` lookups in `RuTorrentDownloader` if a magnet link is missing.
- **1-Click Download HTML API Server**: Embedded a lightweight local HTTP server (`retrieve_media.py --serve` on `127.0.0.1:7890`) enabling 1-click downloads directly from `media_report.html`.

### Changed
- Refactored `retrieve_media.py` payload construction to ensure ruTorrent downloads start automatically on addition instead of pausing in a stopped state.
- Removed default label tagging on ruTorrent downloads, providing a clean queue by default with optional custom labels.
- Updated `README.md` with instructions for Plex OAuth login and downloader setup.

### Fixed
- Fixed ruTorrent `torrents_start_stopped` parameter bug where passing `"0"` caused PHP's `isset()` check to load torrents in a stopped state.
- Fixed missing magnet link issue when requesting movies from the HTML report.
- Hardened `.gitignore` to prevent leaking virtual environments (`.venv`, `.venv_linux`), cached SQLite databases, or secrets (`.env`).

---

## [0.1.0] - 2026-11-19

### Added
- **Plex Library Analyzer (`plex_analyzer.py`)**:
  - Full catalog scan of Plex movie and TV libraries via PlexAPI.
  - User preference analytics calculating top genres, sub-genres, directors, watched ratio, and completion stats.
  - Normalized title index and TV season tracking to prevent downloading duplicate media.
  - Structured output profile export to `plex_library_profile.json`.
- **New Release Scraper (`scraper.py`)**:
  - Headless browser automation via Playwright with anti-bot evasion techniques.
  - Top 48-hour release extraction from The Pirate Bay.
  - Cinemeta API and official IMDb dataset scoring correlation.
- **Match and Preference Engine (`filter.py`)**:
  - Dynamic score weighting matching releases against Plex library taste profile (High, Medium, Low).
  - Filtering by release year, minimum IMDb rating, language, and disliked genres.
- **Interactive Media Report (`output/media_report.html`)**:
  - Visual HTML dashboard featuring real-time search, category filtering, release size display, and status badges (Exists, Consider, Too Old, Low Score).
- **Radarr Integration (`retrieve_media.py`)**:
  - Radarr v3 API integration for automated movie lookup, root folder discovery, quality profile matching, and download dispatch.
- **Environment & Secret Management**:
  - Centralized `.env` configuration file with comprehensive `.env.template`.
