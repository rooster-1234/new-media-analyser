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

### 1. Configuration
Copy `.env.template` to `.env` and fill in your Plex server details:

```bash
cp .env.template .env
```

Edit `.env`:
```env
PLEX_URL=http://localhost:32400
PLEX_TOKEN=your_plex_token_here
```

> **How to find your Plex Token:**
> Refer to the official Plex guide: [Finding an Authentication Token (X-Plex-Token)](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### 2. Run the Analyzer
Using the virtual environment:

```powershell
.\.venv\Scripts\python.exe plex_analyzer.py
```

Options:
- `--summary-only`: Prints the summary in the terminal without writing the full JSON.
- `--output <path>`: Specify a custom path for the output JSON profile (defaults to `plex_library_profile.json`).
- `--env-file <path>`: Specify a custom `.env` path.
