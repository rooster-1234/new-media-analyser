# New Media Analyser

An intelligent media analyzer system that inspects your existing Plex library, identifies your favorite genres and tracked series, and will help match new releases against your preferences.

## Components

1. **Plex Library Analyzer (`plex_analyzer.py`)** [Active]
   - Connects to your Plex Media Server via the official Plex API.
   - Discovers all movie and TV show libraries.
   - Extracts complete genre and sub-genre distributions, top directors, watched status, and episode completion.
   - Generates quick lookup indices (normalized titles and season lists) for duplicate detection.
   - Exports a structured `plex_library_profile.json` for downstream agents.

2. **Match and Filter Agent** [Next Stage]
   - Filters candidate releases by genre preferences, existing library duplicates, quality, and seeders.

3. **Fetcher & UI Dashboard (`scraper.py`)** [Active]
   - Scrapes new media releases and correlates with IMDb scores and genres.
   - Generates an interactive HTML report (`output/media_report.html`) with dynamic filtering.

4. **Media Downloader Integration (`retrieve_media.py`)** [Active]
   - Dispatches download requests to your preferred download application (Radarr, Seerr, Sonarr, or ruTorrent).
   - Automatically looks up metadata and triggers download searches via API.


---

## Setup & Running the Plex Analyzer

### 1. Configuration & 1-Click Plex Sign-In
Copy `.env.template` to `.env`:

```bash
cp .env.template .env
```

You can automatically authenticate using official Plex OAuth in your browser:

```powershell
.\.venv\Scripts\python.exe plex_analyzer.py --login
```

This will:
- Open your default browser with the official Plex sign-in screen.
- Automatically retrieve your Plex authentication token.
- Discover your Plex Media Server (e.g. NAS) and its connection URL.
- Automatically save `PLEX_TOKEN` and `PLEX_URL` into `.env` for you!

*(Manual token setup via [Finding an Authentication Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) is also supported).*

### 2. Run the Analyzer
Using the virtual environment:

```powershell
.\.venv\Scripts\python.exe plex_analyzer.py
```

Options:
- `--summary-only`: Prints the summary in the terminal without writing the full JSON.
- `--output <path>`: Specify a custom path for the output JSON profile (defaults to `plex_library_profile.json`).
- `--env-file <path>`: Specify a custom `.env` path.

---

## Documentation & Project Tracking

- [CHANGELOG.md](CHANGELOG.md) — Documented release history and changes.
- [ROADMAP.md](ROADMAP.md) — Planned milestones, containerisation, and feature to-do list.
