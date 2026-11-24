import asyncio
import gzip
import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from filter import MediaFilter, parse_release_title_and_year, normalize_title
from plex_analyzer import plex_oauth_login, PlexLibraryAnalyzer

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

SITE_SCAN = os.getenv("SITE_SCAN")
SITE_SCAN1 = os.getenv("SITE_SCAN1") or SITE_SCAN or "https://thepiratebay.org/search.php?q=top100:48h_207"
SITE_SCAN2 = os.getenv("SITE_SCAN2") or "https://uindex.org/top.php?c=1&t=7d"
SITE_SCAN3 = os.getenv("SITE_SCAN3")
DATA_DIR = os.getenv("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MAX_SYNOPSIS_WORDS = int(os.getenv("MAX_SYNOPSIS_WORDS", "60"))
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "").strip()
IMDB_CACHE_DB = os.path.join(DATA_DIR, "imdb_ratings.db")
os.makedirs(DATA_DIR, exist_ok=True)

# Media filter instance (initialized on demand or during ensure_media_profile)
media_filter: Optional[MediaFilter] = None


def ensure_media_profile(force_sync: bool = False, profile_path: str = "plex_library_profile.json") -> MediaFilter:
    """
    Ensures that a valid media server library profile exists for duplicate checking and genre matching.
    Supports Plex (with automated OAuth fallback), Jellyfin, Emby, or combined ('all').
    """
    global media_filter
    media_server_type = os.getenv("MEDIA_SERVER", "plex").strip().lower()
    profile_file = Path(profile_path)

    # 1. Trigger OAuth login if using Plex and token is missing
    if media_server_type == "plex":
        plex_token = os.getenv("PLEX_TOKEN", "").strip()
        if not plex_token or "your_plex_token" in plex_token:
            logger.info("Plex token is missing in .env. Launching official Plex OAuth login in your browser...")
            if plex_oauth_login():
                load_dotenv(override=True)
            else:
                logger.warning("Plex OAuth login was not completed. Proceeding without Plex library synchronization.")

    # 2. Generate or refresh profile if missing or requested
    if force_sync or not profile_file.exists():
        logger.info(f"Generating media library profile using '{media_server_type}' provider...")
        try:
            from media_servers import get_media_server_provider
            provider = get_media_server_provider(media_server_type)
            if provider.connect():
                profile_data = provider.generate_profile()
                with open(profile_file, "w", encoding="utf-8") as f:
                    json.dump(profile_data, f, indent=2, ensure_ascii=False)
                logger.info(f"Successfully generated and saved library profile from '{media_server_type}' to '{profile_path}'.")
        except Exception as e:
            logger.warning(f"Could not generate media profile from {media_server_type}: {e}")

    media_filter = MediaFilter(profile_path=profile_path)
    return media_filter

# Backward-compatibility alias
ensure_plex_profile = ensure_media_profile

# Cache for IMDb metadata (score, genres) to avoid redundant network calls
imdb_cache: Dict[str, Dict[str, Any]] = {}


def ensure_imdb_ratings_db() -> bool:
    """
    Downloads and caches the official IMDb ratings dataset (title.ratings.tsv.gz)
    directly from IMDb (https://datasets.imdbws.com) into a local SQLite database.
    Updates only once per 24 hours.
    """
    try:
        if os.path.exists(IMDB_CACHE_DB):
            if time.time() - os.path.getmtime(IMDB_CACHE_DB) < 86400:
                return True

        logger.info("Syncing official IMDb ratings dataset from datasets.imdbws.com...")
        url = "https://datasets.imdbws.com/title.ratings.tsv.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        tmp_db = IMDB_CACHE_DB + ".tmp"
        if os.path.exists(tmp_db):
            os.remove(tmp_db)

        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("CREATE TABLE ratings (tconst TEXT PRIMARY KEY, rating REAL, votes INTEGER)")

        with urllib.request.urlopen(req, timeout=30) as resp:
            with gzip.GzipFile(fileobj=resp) as gz:
                next(gz)  # skip header
                batch = []
                for line in gz:
                    parts = line.decode("utf-8").strip().split("\t")
                    if len(parts) >= 3:
                        try:
                            batch.append((parts[0], float(parts[1]), int(parts[2])))
                        except ValueError:
                            continue
                    if len(batch) >= 100000:
                        cur.executemany("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?)", batch)
                        batch = []
                if batch:
                    cur.executemany("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?)", batch)
        conn.commit()
        conn.close()

        if os.path.exists(IMDB_CACHE_DB):
            os.remove(IMDB_CACHE_DB)
        os.replace(tmp_db, IMDB_CACHE_DB)
        logger.info("Official IMDb ratings database successfully indexed and cached.")
        return True
    except Exception as e:
        logger.warning(f"Could not update official IMDb ratings database: {e}")
        return False


def get_imdb_dataset_rating(imdb_id: str) -> Optional[str]:
    """Look up an IMDb score in the local official IMDb dataset cache."""
    if not os.path.exists(IMDB_CACHE_DB):
        ensure_imdb_ratings_db()

    if not os.path.exists(IMDB_CACHE_DB):
        return None

    try:
        conn = sqlite3.connect(IMDB_CACHE_DB)
        cur = conn.cursor()
        cur.execute("SELECT rating FROM ratings WHERE tconst = ?", (imdb_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return str(round(row[0], 1))
    except Exception as e:
        logger.debug(f"Dataset rating lookup failed for {imdb_id}: {e}")
    return None


def is_valid_imdb_score(val: str) -> bool:
    """Validate that score is a numeric value between 1.0 and 10.0."""
    try:
        f = float(val)
        return 1.0 <= f <= 10.0
    except (ValueError, TypeError):
        return False


def extract_imdb_score_from_text(text: str) -> str:
    """
    Fallback extractor for text / NFO release descriptions:
    - Star format: ⭐ 8.4
    - JSON-LD / API ratingValue: "ratingValue": 8.4
    - Text rating: IMDb: 8.4, Rating: 8.4/10, or 8.4/10
    """
    if not text:
        return "N/A"

    # 1. Star pattern (e.g., ⭐ 8.4)
    m = re.search(r"[⭐\u2B50\u2618\u2728]\s*(\d+(?:\.\d+)?)", text)
    if m and is_valid_imdb_score(m.group(1)):
        return m.group(1)

    # 2. JSON-LD ratingValue
    m = re.search(r'"ratingValue":\s*["\']?(\d+(?:\.\d+)?)', text)
    if m and is_valid_imdb_score(m.group(1)):
        return m.group(1)

    # 3. Labelled rating (e.g. IMDb: 8.4, Rating: 8.4/10) or standalone fraction (8.4/10)
    m = re.search(r"(?i)(?:IMDb|Rating|Score)[:\s]+([1-9]\.\d|10(?:\.0)?)(?:\s*/\s*10)?|\b([1-9]\.\d|10(?:\.0)?)\s*/\s*10\b", text)
    if m:
        val = m.group(1) or m.group(2)
        if val and is_valid_imdb_score(val):
            return val

    return "N/A"


async def search_cinemeta_imdb_id(title: str, year: Optional[int] = None) -> Optional[str]:
    """
    Resolves IMDb ID (tt...) for a movie/series title using Cinemeta search catalog.
    Matches closest title and release year.
    """
    if not title:
        return None
    loop = asyncio.get_running_loop()
    clean_query = title.strip()
    encoded = urllib.parse.quote(clean_query)

    for mtype in ("movie", "series"):
        try:
            url = f"https://v3-cinemeta.strem.io/catalog/{mtype}/top/search={encoded}.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

            def _fetch(request=req):
                with urllib.request.urlopen(request, timeout=3) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            data = await loop.run_in_executor(None, _fetch)
            metas = data.get("metas", []) if isinstance(data, dict) else []
            for m in metas:
                m_year = m.get("releaseInfo") or m.get("year")
                if year and m_year:
                    try:
                        if abs(int(str(m_year)[:4]) - int(year)) <= 1:
                            return m.get("id")
                    except Exception:
                        pass
                else:
                    return m.get("id")
            if metas:
                return metas[0].get("id")
        except Exception as e:
            logger.debug(f"Cinemeta search failed for '{title}' ({mtype}): {e}")
    return None


async def fetch_imdb_metadata(imdb_id: str) -> Dict[str, Any]:
    """
    Fetches official IMDb rating, genres, and synopsis for a tt ID using Cinemeta JSON metadata API.
    Fast, direct, and requires no browser or web proxies.
    """
    if not imdb_id:
        return {"score": "N/A", "genres": [], "synopsis": ""}

    if imdb_id in imdb_cache:
        return imdb_cache[imdb_id]

    loop = asyncio.get_running_loop()

    # Query Cinemeta metadata API (movie first, then series)
    for media_type in ("movie", "series"):
        try:
            url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

            def _get(request=req):
                with urllib.request.urlopen(request, timeout=4) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            data = await loop.run_in_executor(None, _get)
            meta = data.get("meta") if isinstance(data, dict) else None
            if meta:
                # Priority 1: Query local official IMDb ratings database (daily export directly from datasets.imdbws.com)
                dataset_score = get_imdb_dataset_rating(imdb_id)
                if dataset_score:
                    score = dataset_score
                else:
                    raw_score = meta.get("imdbRating")
                    score = str(raw_score) if raw_score and is_valid_imdb_score(str(raw_score)) else "N/A"

                genres = meta.get("genres") or meta.get("genre") or []
                if isinstance(genres, str):
                    genres = [g.strip() for g in genres.split(",") if g.strip()]
                synopsis = (meta.get("description") or meta.get("overview") or "").strip()

                language = meta.get("language")
                country = meta.get("country")

                # Extract official US release date (ISO timestamp e.g. "2024-03-01T00:00:00.000Z")
                raw_released = meta.get("released")
                release_date = None
                if raw_released:
                    try:
                        release_date = str(raw_released)[:10]
                    except Exception:
                        pass

                meta_year = None
                raw_year = meta.get("year") or meta.get("releaseInfo")
                if raw_year:
                    try:
                        meta_year = int(str(raw_year)[:4])
                    except Exception:
                        pass
                if not meta_year and release_date:
                    try:
                        meta_year = int(release_date[:4])
                    except Exception:
                        pass

                poster = meta.get("poster") or meta.get("poster_path") or ""

                # Fallback 2: Query OMDb API if score, release date, poster, or language is missing and API key is set
                if (score == "N/A" or not release_date or not poster or not language) and OMDB_API_KEY:
                    try:
                        omdb_url = f"https://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
                        def _get_omdb():
                            with urllib.request.urlopen(omdb_url, timeout=4) as omdb_resp:
                                return json.loads(omdb_resp.read().decode("utf-8"))
                        omdb_data = await loop.run_in_executor(None, _get_omdb)
                        if score == "N/A":
                            raw_omdb = omdb_data.get("imdbRating")
                            if raw_omdb and is_valid_imdb_score(str(raw_omdb)):
                                score = str(raw_omdb)
                                logger.info(f"Retrieved IMDb score {score} for {imdb_id} via OMDb fallback")
                        if not language and omdb_data.get("Language") and omdb_data.get("Language") != "N/A":
                            language = omdb_data["Language"]
                        if not country and omdb_data.get("Country") and omdb_data.get("Country") != "N/A":
                            country = omdb_data["Country"]
                        raw_omdb_rel = omdb_data.get("Released")
                        if raw_omdb_rel and raw_omdb_rel != "N/A" and not release_date:
                            try:
                                parsed_d = datetime.strptime(raw_omdb_rel, "%d %b %Y").date()
                                release_date = parsed_d.isoformat()
                            except Exception:
                                pass
                        if not poster and omdb_data.get("Poster") and omdb_data.get("Poster") != "N/A":
                            poster = omdb_data["Poster"]
                    except Exception as omdb_err:
                        logger.debug(f"OMDb fallback failed for {imdb_id}: {omdb_err}")

                if not poster and imdb_id and imdb_id.startswith("tt"):
                    poster = f"https://images.metahub.space/poster/small/{imdb_id}/img"

                res = {
                    "score": score,
                    "genres": genres,
                    "synopsis": synopsis,
                    "language": language,
                    "country": country,
                    "release_date": release_date,
                    "year": meta_year,
                    "poster": poster
                }
                logger.info(f"Retrieved Cinemeta metadata for {imdb_id}: score={score}, genres={genres}, rel_date={release_date}")
                imdb_cache[imdb_id] = res
                return res
        except Exception as e:
            logger.debug(f"Cinemeta lookup failed for {imdb_id} ({media_type}): {e}")

    fallback_poster = f"https://images.metahub.space/poster/small/{imdb_id}/img" if imdb_id and imdb_id.startswith("tt") else ""
    fallback_score = get_imdb_dataset_rating(imdb_id) or "N/A"
    fallback = {"score": fallback_score, "genres": [], "synopsis": "", "language": None, "country": None, "release_date": None, "year": None, "poster": fallback_poster}
    imdb_cache[imdb_id] = fallback
    return fallback


async def fetch_imdb_score(imdb_id: str) -> str:
    """Helper to fetch IMDb score string."""
    meta = await fetch_imdb_metadata(imdb_id)
    return meta.get("score", "N/A")


async def scrape_tpb(page, url: str) -> List[Dict[str, Any]]:
    """Scrapes release listings from The Pirate Bay."""
    logger.info(f"Navigating to The Pirate Bay: {url}...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_selector("#torrents", timeout=15000)
    except Exception as e:
        logger.warning(f"Could not load TPB #torrents table: {e}")
        return []

    entries = await page.query_selector_all("#torrents li.list-entry")
    results = []
    for entry in entries:
        title_el = await entry.query_selector(".item-title a, a[href^='/description.php?id=']")
        if not title_el:
            continue
        raw_title = (await title_el.inner_text()).strip()
        href = await title_el.get_attribute("href")
        if href and not href.startswith("http"):
            href = urllib.parse.urljoin("https://thepiratebay.org", href)
        size_el = await entry.query_selector(".item-size, span[class*='size']")
        size_text = (await size_el.inner_text()).replace("\xa0", " ").strip() if size_el else "N/A"
        mag_el = await entry.query_selector("a[href^='magnet:']")
        magnet = (await mag_el.get_attribute("href")) if mag_el else ""
        clean_title, year = parse_release_title_and_year(raw_title)

        results.append({
            "source": "TPB",
            "source_key": "tpb",
            "title": raw_title,
            "clean_title": clean_title,
            "year": year,
            "size": size_text,
            "magnet": magnet,
            "href": href,
            "seeders": 0,
        })
    logger.info(f"The Pirate Bay: parsed {len(results)} releases.")
    return results


async def scrape_uindex(page, url: str) -> List[Dict[str, Any]]:
    """Scrapes release listings from uIndex.org."""
    logger.info(f"Navigating to uIndex: {url}...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1000)
        rows = await page.locator("table tr").all()
    except Exception as e:
        logger.warning(f"Could not load uIndex table: {e}")
        return []

    results = []
    for row in rows[1:]:
        cols = await row.locator("td").all()
        if len(cols) < 4:
            continue
        title_el = row.locator("a[href*='details.php']")
        if await title_el.count() == 0:
            continue
        raw_title = (await title_el.first.inner_text()).strip()
        href = await title_el.first.get_attribute("href")
        if href and not href.startswith("http"):
            href = urllib.parse.urljoin("https://uindex.org", href)
        mag_el = row.locator("a[href^='magnet:']")
        magnet = await mag_el.first.get_attribute("href") if await mag_el.count() > 0 else ""
        size_text = (await cols[3].inner_text()).strip() if len(cols) > 3 else "N/A"
        seeders_str = (await cols[5].inner_text()).strip().replace(",", "") if len(cols) > 5 else "0"
        seeders = int(seeders_str) if seeders_str.isdigit() else 0
        clean_title, year = parse_release_title_and_year(raw_title)

        results.append({
            "source": "uIndex",
            "source_key": "uindex",
            "title": raw_title,
            "clean_title": clean_title,
            "year": year,
            "size": size_text,
            "magnet": magnet,
            "href": href,
            "seeders": seeders,
        })
    logger.info(f"uIndex: parsed {len(results)} releases.")
    return results


async def enrich_and_filter_item(item: Dict[str, Any], page=None) -> Dict[str, Any]:
    """
    Enriches release item with IMDb ID, scores, genres, and synopsis,
    then evaluates against media server library and filter rules.
    """
    clean_title = item.get("clean_title") or item.get("title", "")
    year = item.get("year")
    imdb_id = item.get("imdb_id")

    # 1. Resolve IMDb ID via Cinemeta search if not already present
    if not imdb_id or imdb_id == "N/A":
        imdb_id = await search_cinemeta_imdb_id(clean_title, year)

    # 2. If TPB item still has no IMDb ID and page is provided, check detail page as fallback
    if not imdb_id and page and item.get("source") == "TPB" and item.get("href"):
        try:
            detail_url = urllib.parse.urljoin("https://thepiratebay.org", item["href"])
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=12000)
            content = await page.content()
            m = re.search(r'imdb\.com/title/(tt\d+)', content) or re.search(r'(?i)IMDb[:\s]+(tt\d+)', content)
            if m:
                imdb_id = m.group(1)
            # Refine magnet / size if missing
            if not item.get("magnet"):
                mag_detail = await page.query_selector("a[href^='magnet:']")
                if mag_detail:
                    item["magnet"] = await mag_detail.get_attribute("href")
        except Exception:
            pass

    # 3. Retrieve metadata via Cinemeta and fallbacks
    score = "N/A"
    genres = []
    synopsis = ""
    language = None
    country = None
    release_date = None
    meta_year = None
    poster = ""

    if imdb_id:
        meta = await fetch_imdb_metadata(imdb_id)
        score = meta.get("score", "N/A")
        genres = meta.get("genres", [])
        synopsis = meta.get("synopsis", "")
        language = meta.get("language")
        country = meta.get("country")
        release_date = meta.get("release_date")
        meta_year = meta.get("year")
        poster = meta.get("poster") or ""

    if not poster and imdb_id and imdb_id.startswith("tt"):
        poster = f"https://images.metahub.space/poster/small/{imdb_id}/img"

    item["imdb_id"] = imdb_id
    item["imdb_url"] = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else "N/A"
    item["imdb_score"] = score
    item["genres"] = genres
    item["synopsis"] = synopsis
    item["language"] = language
    item["country"] = country
    item["release_date"] = release_date
    item["poster"] = poster
    if meta_year and not item.get("year"):
        item["year"] = meta_year

    # 4. Evaluate against filter criteria (.env: Exists, Low Score, Non-English, Too Old, or Consider)
    if media_filter:
        media_filter.evaluate(item)
    else:
        item["filter_status"] = "Consider"
        item["genre_match"] = "Low"

    return item


def deduplicate_and_merge(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merges duplicate releases across sources (and different resolutions of the same title)
    into a unified list. Combines source badges, preserves the best magnet link and download size.
    """
    merged_map: Dict[str, Dict[str, Any]] = {}

    for item in items:
        imdb_id = item.get("imdb_id")
        clean_title = item.get("clean_title") or item.get("title", "")
        year = item.get("year")

        if imdb_id and imdb_id != "N/A" and str(imdb_id).startswith("tt"):
            key = imdb_id
        else:
            key = f"{normalize_title(clean_title)}_{year or ''}"

        source_name = item.get("source", "Unknown")
        source_key = item.get("source_key", source_name.lower())

        if key not in merged_map:
            item["sources"] = [source_name]
            item["source_keys"] = [source_key]
            item["source_urls"] = {source_name: item.get("href", "")} if item.get("href") else {}
            item["source_details"] = {
                source_name: {
                    "raw_title": item.get("title", ""),
                    "size": item.get("size", "N/A"),
                    "magnet": item.get("magnet", ""),
                    "seeders": item.get("seeders", 0),
                    "href": item.get("href", "")
                }
            }
            merged_map[key] = item
        else:
            existing = merged_map[key]
            if source_name not in existing["sources"]:
                existing["sources"].append(source_name)
            if source_key not in existing["source_keys"]:
                existing["source_keys"].append(source_key)

            if "source_details" not in existing or not isinstance(existing["source_details"], dict):
                existing["source_details"] = {}
            existing["source_details"][source_name] = {
                "raw_title": item.get("title", ""),
                "size": item.get("size", "N/A"),
                "magnet": item.get("magnet", ""),
                "seeders": item.get("seeders", 0),
                "href": item.get("href", "")
            }
            if "source_urls" not in existing or not isinstance(existing["source_urls"], dict):
                existing["source_urls"] = {}
            if item.get("href"):
                existing["source_urls"][source_name] = item["href"]

            # Select best magnet (if existing has no magnet, or new item has more seeders)
            new_seeders = item.get("seeders", 0)
            existing_seeders = existing.get("seeders", 0)
            if not existing.get("magnet") and item.get("magnet"):
                existing["magnet"] = item["magnet"]
            elif item.get("magnet") and new_seeders > existing_seeders:
                existing["magnet"] = item["magnet"]
                existing["seeders"] = new_seeders

            # If new item is 1080p and existing was 720p, prefer 1080p size
            existing_size = existing.get("size", "N/A")
            new_size = item.get("size", "N/A")
            if "1080p" in item.get("title", "") and "1080p" not in existing.get("title", ""):
                existing["size"] = new_size
            elif existing_size in ("N/A", "") and new_size not in ("N/A", ""):
                existing["size"] = new_size

            # Keep richer metadata if missing in existing
            if existing.get("imdb_score") in ("N/A", "") and item.get("imdb_score") not in ("N/A", ""):
                existing["imdb_score"] = item["imdb_score"]
                existing["imdb_id"] = item.get("imdb_id")
                existing["imdb_url"] = item.get("imdb_url")
            if not existing.get("genres") and item.get("genres"):
                existing["genres"] = item["genres"]
            if not existing.get("synopsis") and item.get("synopsis"):
                existing["synopsis"] = item["synopsis"]
            if not existing.get("poster") and item.get("poster"):
                existing["poster"] = item["poster"]
            if not existing.get("language") and item.get("language"):
                existing["language"] = item["language"]
            if not existing.get("country") and item.get("country"):
                existing["country"] = item["country"]
            if not existing.get("release_date") and item.get("release_date"):
                existing["release_date"] = item["release_date"]

    return list(merged_map.values())


async def fetch_media_index(progress_callback: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Scrapes all configured media release sources (The Pirate Bay, uIndex, etc.),
    enriches them with IMDb metadata, deduplicates across sources into a unified list,
    and returns categorized releases.
    """
    ensure_media_profile()

    sources_to_scan = []
    s1 = os.getenv("SITE_SCAN1") or os.getenv("SITE_SCAN")
    if s1:
        sources_to_scan.append({"name": "TPB", "type": "tpb", "url": s1})

    s2 = os.getenv("SITE_SCAN2")
    if s2:
        sources_to_scan.append({"name": "uIndex", "type": "uindex", "url": s2})

    s3 = os.getenv("SITE_SCAN3")
    if s3 and "ext.to" not in s3:  # ext.to is handled if unblocked
        sources_to_scan.append({"name": "ext", "type": "ext", "url": s3})

    if not sources_to_scan:
        logger.error("No valid SITE_SCAN or SITE_SCAN1/SITE_SCAN2 sources configured in .env.")
        return [{"error": "No scan sources configured in .env"}]

    all_raw_items: List[Dict[str, Any]] = []

    if progress_callback:
        progress_callback("Connecting to live indexers...", 0)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        for src in sources_to_scan:
            try:
                if src["type"] == "tpb":
                    if progress_callback:
                        progress_callback(f"Scraping The Pirate Bay... ({len(all_raw_items)} found)", len(all_raw_items))
                    items = await scrape_tpb(page, src["url"])
                    all_raw_items.extend(items)
                    if progress_callback:
                        progress_callback(f"The Pirate Bay: Found {len(all_raw_items)} releases", len(all_raw_items))
                elif src["type"] == "uindex":
                    if progress_callback:
                        progress_callback(f"Scraping uIndex... ({len(all_raw_items)} found)", len(all_raw_items))
                    items = await scrape_uindex(page, src["url"])
                    all_raw_items.extend(items)
                    if progress_callback:
                        progress_callback(f"uIndex: Found {len(all_raw_items)} releases", len(all_raw_items))
            except Exception as e:
                logger.error(f"Error scraping {src['name']} ({src['url']}): {e}")

        logger.info(f"Total raw releases collected across {len(sources_to_scan)} sources: {len(all_raw_items)}")

        # Step 2: Enrich items with IMDb metadata & filter evaluations
        logger.info("Enriching items with IMDb metadata & filter status...")
        enriched_items = []
        total_raw = len(all_raw_items)
        for idx, item in enumerate(all_raw_items):
            if progress_callback and (idx % 3 == 0 or idx == total_raw - 1):
                progress_callback(f"Enriching {idx + 1}/{total_raw} with IMDb metadata ({total_raw} found)...", total_raw)
            await enrich_and_filter_item(item, page=page)
            enriched_items.append(item)

        await browser.close()

    # Step 3: Deduplicate and merge across sources into a unified list
    merged_items = deduplicate_and_merge(enriched_items)
    logger.info(f"Unified into {len(merged_items)} distinct releases after smart deduplication.")
    if progress_callback:
        progress_callback(f"Complete! Found {len(merged_items)} releases", len(merged_items))

    # Save cache for web app and dashboard
    try:
        cache_path = Path("data") / "scraped_releases_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(merged_items, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved release cache to {cache_path}")
    except Exception as e:
        logger.debug(f"Could not save release cache: {e}")

    return merged_items


def truncate_synopsis(text: str, max_words: int = MAX_SYNOPSIS_WORDS) -> str:
    """Truncates synopsis after max_words words and appends '....' if truncated."""
    if not text:
        return ""
    words = text.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "...."
    return text


def sanitize_output(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean titles and standardize output fields."""
    sanitized = []
    for item in raw_data:
        if "error" in item:
            sanitized.append(item)
            continue
        clean_title = item.get("clean_title") or item["title"].replace("Pirate", "Source").replace("Torrent", "File")
        sources = item.get("sources", ["TPB"])
        source_keys = item.get("source_keys", [s.lower() for s in sources])

        if len(sources) > 1:
            source_display = " + ".join(sources)
        elif sources and sources[0] == "TPB":
            source_display = "Pirate Bay"
        elif sources:
            source_display = sources[0]
        else:
            source_display = "Unknown"

        sanitized.append({
            "title": clean_title,
            "raw_title": item.get("title", ""),
            "year": item.get("year"),
            "size": item.get("size", "N/A"),
            "magnet": item.get("magnet", ""),
            "sources": sources,
            "source_keys": source_keys,
            "source_display": source_display,
            "seeders": item.get("seeders", 0),
            "imdb_id": item.get("imdb_id"),
            "imdb_url": item.get("imdb_url", "N/A"),
            "imdb_score": item.get("imdb_score", "N/A"),
            "genres": item.get("genres", []),
            "genre_match": item.get("genre_match", "Low"),
            "genre_match_reason": item.get("genre_match_reason", ""),
            "synopsis": item.get("synopsis", ""),
            "language": item.get("language"),
            "country": item.get("country"),
            "filter_status": item.get("filter_status", "Consider"),
            "filter_reason": item.get("filter_reason", "")
        })
    return sanitized


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape and analyze new media releases against Plex/Jellyfin/Emby library.")
    parser.add_argument("--login", action="store_true", help="Log in to Plex via official browser OAuth")
    parser.add_argument("--server", choices=["plex", "jellyfin", "emby", "all"], help="Choose or override active media server provider")
    parser.add_argument("--sync", "--sync-plex", action="store_true", dest="sync_media", help="Re-scan media server library and refresh profile before scraping")
    args = parser.parse_args()

    if args.server:
        os.environ["MEDIA_SERVER"] = args.server

    if args.login:
        plex_oauth_login()
        load_dotenv(override=True)

    if args.sync_media:
        ensure_media_profile(force_sync=True)

    async def main():
        data = await fetch_media_index()
        sanitized = sanitize_output(data)
        print(f"Scraped and enriched {len(sanitized)} media releases.")
        for item in sanitized[:10]:
            print(f"  [{item.get('filter_status', 'N/A'):<11}] {item.get('title')} ({item.get('year')}) - IMDb: {item.get('imdb_score')}")

    asyncio.run(main())
