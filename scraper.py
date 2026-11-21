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
from retrieve_media import get_downloader
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
OUTPUT_DIR = os.getenv("OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
MAX_SYNOPSIS_WORDS = int(os.getenv("MAX_SYNOPSIS_WORDS", "60"))
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "").strip()
IMDB_CACHE_DB = os.path.join(OUTPUT_DIR, "imdb_ratings.db")
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

                # Fallback 1: Query local official IMDb ratings database (from datasets.imdbws.com)
                if score == "N/A":
                    dataset_score = get_imdb_dataset_rating(imdb_id)
                    if dataset_score:
                        score = dataset_score
                        logger.info(f"Retrieved IMDb score {score} for {imdb_id} via official IMDb dataset cache")

                # Fallback 2: Query OMDb API if score or release date is still missing and API key is set
                if (score == "N/A" or not release_date) and OMDB_API_KEY:
                    try:
                        omdb_url = f"https://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
                        def _get_omdb():
                            with urllib.request.urlopen(omdb_url, timeout=4) as omdb_resp:
                                return json.loads(omdb_resp.read().decode("utf-8"))
                        omdb_data = await loop.run_in_executor(None, _get_omdb)
                        raw_omdb = omdb_data.get("imdbRating")
                        if raw_omdb and is_valid_imdb_score(str(raw_omdb)):
                            score = str(raw_omdb)
                            logger.info(f"Retrieved IMDb score {score} for {imdb_id} via OMDb fallback")
                        raw_omdb_rel = omdb_data.get("Released")
                        if raw_omdb_rel and raw_omdb_rel != "N/A" and not release_date:
                            try:
                                parsed_d = datetime.strptime(raw_omdb_rel, "%d %b %Y").date()
                                release_date = parsed_d.isoformat()
                            except Exception:
                                pass
                    except Exception as omdb_err:
                        logger.debug(f"OMDb fallback failed for {imdb_id}: {omdb_err}")

                res = {
                    "score": score,
                    "genres": genres,
                    "synopsis": synopsis,
                    "language": language,
                    "country": country,
                    "release_date": release_date,
                    "year": meta_year
                }
                logger.info(f"Retrieved Cinemeta metadata for {imdb_id}: score={score}, genres={genres}, rel_date={release_date}")
                imdb_cache[imdb_id] = res
                return res
        except Exception as e:
            logger.debug(f"Cinemeta lookup failed for {imdb_id} ({media_type}): {e}")

    fallback = {"score": "N/A", "genres": [], "synopsis": "", "language": None, "country": None, "release_date": None, "year": None}
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

    if imdb_id:
        meta = await fetch_imdb_metadata(imdb_id)
        score = meta.get("score", "N/A")
        genres = meta.get("genres", [])
        synopsis = meta.get("synopsis", "")
        language = meta.get("language")
        country = meta.get("country")
        release_date = meta.get("release_date")
        meta_year = meta.get("year")

    item["imdb_id"] = imdb_id
    item["imdb_url"] = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else "N/A"
    item["imdb_score"] = score
    item["genres"] = genres
    item["synopsis"] = synopsis
    item["language"] = language
    item["country"] = country
    item["release_date"] = release_date
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

            existing["source_details"][source_name] = {
                "raw_title": item.get("title", ""),
                "size": item.get("size", "N/A"),
                "magnet": item.get("magnet", ""),
                "seeders": item.get("seeders", 0),
                "href": item.get("href", "")
            }

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


def generate_html(data: List[Dict[str, Any]], file_path: str):
    """Generate dark-themed HTML report table with unified sources, filter status column, download size, and request buttons."""
    downloader = get_downloader()
    client_raw = (downloader.name or "radarr").lower()
    downloader_name = {
        "radarr": "Radarr",
        "seerr": "Seerr",
        "overseerr": "Seerr",
        "jellyseerr": "Seerr",
        "sonarr": "Sonarr",
        "rutorrent": "ruTorrent",
    }.get(client_raw, client_raw.capitalize())

    # Detect media server display name
    media_server_type = os.getenv("MEDIA_SERVER", "plex").strip().lower()
    server_display_names = {
        "plex": "Plex",
        "jellyfin": "Jellyfin",
        "emby": "Emby",
        "all": "Library"
    }
    server_name = server_display_names.get(media_server_type, "Media Server")
    in_server_label = f"In {server_name}"

    # Compute summary counts
    counts = {"Consider": 0, "Exists": 0, "Too Old": 0, "Low Score": 0, "Non-English": 0}
    genre_counts = {"High": 0, "Medium": 0, "Low": 0}
    for item in data:
        st = item.get("filter_status", "Consider")
        counts[st] = counts.get(st, 0) + 1
        gm = item.get("genre_match", "Low")
        genre_counts[gm] = genre_counts.get(gm, 0) + 1

    # Compute source counts
    tpb_count = sum(1 for x in data if "tpb" in x.get("source_keys", []))
    uindex_count = sum(1 for x in data if "uindex" in x.get("source_keys", []))

    source_pills_html = f"""
    <div class="source-filter-bar">
        <span class="source-filter-label">Source Filter:</span>
        <button class="source-pill active" data-source="all">All Sources ({len(data)})</button>
        <button class="source-pill pill-tpb" data-source="tpb">Pirate Bay ({tpb_count})</button>
        <button class="source-pill pill-uindex" data-source="uindex">uIndex ({uindex_count})</button>
    </div>
    """

    rows = []
    for item in data:
        imdb_url = item.get("imdb_url", "N/A")
        if imdb_url and imdb_url != "N/A":
            title_html = f"<a href='{imdb_url}' target='_blank' class='title-link'>{item['title']}</a>"
        else:
            title_html = f"<span class='title-main'>{item['title']}</span>"

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

        year_str = str(item.get("year") or "-")
        size_str = str(item.get("size") or "N/A")
        synopsis = item.get("synopsis", "")
        truncated = truncate_synopsis(synopsis, max_words=MAX_SYNOPSIS_WORDS)
        synopsis_html = (
            f"<div class='synopsis-text' title='{html.escape(synopsis)}'>{html.escape(truncated)}</div>"
            if synopsis
            else ""
        )

        imdb_id = item.get("imdb_id") or ""
        escaped_title = item.get("title", "").replace("'", "\\'").replace('"', '&quot;')
        year_val = item.get("year") or 0
        magnet_str = item.get("magnet") or ""

        # Source badge formatting
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

        btn_html = f"<button class='btn-request' data-magnet=\"{html.escape(magnet_str)}\" onclick=\"requestMedia(this, '{imdb_id}', '{escaped_title}', {year_val})\">Request</button>"

        rows.append(f"""
                <tr data-status="{html.escape(status)}" data-genre="{html.escape(genre_match)}" data-sources="{html.escape(source_keys_str)}">
                    <td>
                        <div class='title-main'>{title_html}</div>
                        {synopsis_html}
                    </td>
                    <td>{year_str}</td>
                    <td>{source_badge}</td>
                    <td><span class='size-text'>{html.escape(size_str)}</span></td>
                    <td>
                        <span class='badge {genre_badge_cls}' title='{genre_reason}'>{genre_match}</span>
                        <div class='genre-subtext' title='{genres_display}'>{genres_display}</div>
                    </td>
                    <td><span class='score'>{item['imdb_score']}</span></td>
                    <td><span class='badge {badge_cls}' title='{reason}'>{status}</span></td>
                    <td style='text-align: center;'>{btn_html}</td>
                </tr>""")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Media Release Monitor</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #121212; color: #e0e0e0; padding: 25px; margin: 0; }}
        h1 {{ margin-top: 0; color: #f8fafc; font-size: 24px; }}
        .header-row {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }}
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
        
        /* Source Filter Bar & Pills */
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
        .source-filter-label {{ 
            font-size: 11px; 
            font-weight: 700; 
            color: #94a3b8; 
            text-transform: uppercase; 
            letter-spacing: 0.5px; 
            margin-right: 4px; 
        }}
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
        .source-pill:hover {{ 
            border-color: #64748b; 
            color: #f8fafc; 
        }}
        .source-pill.active {{ 
            background: #2563eb; 
            color: #ffffff; 
            border-color: #3b82f6; 
            box-shadow: 0 0 8px rgba(37, 99, 235, 0.4); 
        }}
        .source-pill.pill-tpb.active {{ 
            background: rgba(245, 158, 11, 0.25); 
            color: #fbbf24; 
            border-color: #f59e0b; 
            box-shadow: 0 0 8px rgba(245, 158, 11, 0.4); 
        }}
        .source-pill.pill-uindex.active {{ 
            background: rgba(6, 182, 212, 0.25); 
            color: #22d3ee; 
            border-color: #06b6d4; 
            box-shadow: 0 0 8px rgba(6, 182, 212, 0.4); 
        }}
        .source-pill.pill-multi.active {{ 
            background: rgba(168, 85, 247, 0.25); 
            color: #c084fc; 
            border-color: #a855f7; 
            box-shadow: 0 0 8px rgba(168, 85, 247, 0.4); 
        }}

        .filter-status-info {{ margin-bottom: 16px; font-size: 13px; color: #94a3b8; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }}
        .filter-status-info strong {{ color: #f8fafc; }}
        .reset-filter-btn {{ display: none; background: #262626; border: 1px solid #444; color: #38bdf8; padding: 2px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }}
        .reset-filter-btn:hover {{ background: #333; }}

        .server-status-pill {{ 
            font-size: 12px; 
            padding: 4px 10px; 
            border-radius: 9999px; 
            background: #1e1e1e; 
            border: 1px solid #333; 
            display: inline-flex; 
            align-items: center; 
            gap: 6px; 
        }}
        .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: #94a3b8; }}
        .dot.online {{ background: #4ade80; box-shadow: 0 0 8px rgba(74, 222, 128, 0.6); }}
        .dot.offline {{ background: #fbbf24; }}

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

        /* Source Badges */
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
            box-shadow: 0 1px 3px rgba(0,0,0,0.3); 
            white-space: nowrap; 
        }}
        .btn-request:hover {{ background: #1d4ed8; transform: translateY(-1px); box-shadow: 0 3px 6px rgba(37,99,235,0.4); }}
        .btn-request:active {{ transform: translateY(0); }}
        .btn-request.loading {{ background: #475569; cursor: wait; }}
        .btn-request.btn-success {{ background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); cursor: default; }}
        .btn-request.btn-failed {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}

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
        }}
        .code-box button {{ 
            background: #2563eb; 
            color: white; 
            border: none; 
            padding: 4px 8px; 
            border-radius: 4px; 
            cursor: pointer; 
            font-size: 11px; 
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
    <div class="header-row">
        <h1>Media Release Monitor</h1>
        <div class="server-status-pill" id="serverPill">
            <span class="dot" id="serverDot"></span>
            <span id="serverText">Downloader: <strong>{downloader_name}</strong></span>
        </div>
    </div>
    <div class="summary-bar">
        <div class="card active" data-filter="all"><div class="card-num">{len(data)}</div><div class="card-label">Total</div></div>
        <div class="card card-consider" data-filter="status:Consider"><div class="card-num">{counts.get('Consider', 0)}</div><div class="card-label">Consider</div></div>
        <div class="card card-exists" data-filter="status:Exists"><div class="card-num">{counts.get('Exists', 0)}</div><div class="card-label">{in_server_label}</div></div>
        <div class="card card-low" data-filter="status:Low Score"><div class="card-num">{counts.get('Low Score', 0)}</div><div class="card-label">Low Score</div></div>
        <div class="card card-non-eng" data-filter="status:Non-English"><div class="card-num">{counts.get('Non-English', 0)}</div><div class="card-label">Non-English</div></div>
        <div class="card card-old" data-filter="status:Too Old"><div class="card-num">{counts.get('Too Old', 0)}</div><div class="card-label">Too Old</div></div>
        <div class="card card-genre-high" data-filter="genre:High"><div class="card-num">{genre_counts.get('High', 0)}</div><div class="card-label">High Genre Match</div></div>
    </div>

    {source_pills_html}

    <div class="filter-status-info" id="filterInfo">
        <div>
            <span>Showing <strong id="visibleCount">{len(data)}</strong> of {len(data)} items</span>
            <button class="reset-filter-btn" id="resetBtn" onclick="resetFilter()">Reset Filter</button>
        </div>
    </div>
    <table>
        <thead>
            <tr>
                <th>Title</th>
                <th>Year</th>
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

    <!-- Modal for Server Offline Fallback -->
    <div class="modal-overlay" id="fallbackModal">
        <div class="modal">
            <h3>1-Click Downloader Not Running</h3>
            <p>To trigger 1-click requests instantly from your browser, start the local background API server:</p>
            <div class="code-box">
                <span id="serveCmd">.\\.venv\\Scripts\\python.exe retrieve_media.py --serve</span>
                <button onclick="copyToClipboard('serveCmd')">Copy</button>
            </div>
            <p>Or send this single request directly from your terminal:</p>
            <div class="code-box">
                <span id="singleCmd">.\\.venv\\Scripts\\python.exe retrieve_media.py</span>
                <button onclick="copyToClipboard('singleCmd')">Copy</button>
            </div>
            <div class="modal-buttons">
                <button class="btn-close" onclick="closeModal()">Close</button>
            </div>
        </div>
    </div>

    <!-- Toast container -->
    <div class="toast-container" id="toastContainer"></div>

    <script>
        const cards = document.querySelectorAll('.card');
        const sourcePills = document.querySelectorAll('.source-pill');
        const rows = document.querySelectorAll('#mediaTableBody tr');
        const visibleCountSpan = document.getElementById('visibleCount');
        const resetBtn = document.getElementById('resetBtn');
        const noMatchesDiv = document.getElementById('noMatches');
        const serverDot = document.getElementById('serverDot');
        const serverText = document.getElementById('serverText');

        let isServerOnline = false;
        let currentFilterType = 'all';
        let currentFilterVal = '';
        let currentSourceFilter = 'all';

        async function checkServerStatus() {{
            try {{
                const res = await fetch('http://127.0.0.1:7890/api/status', {{ method: 'GET' }});
                if (res.ok) {{
                    const data = await res.json();
                    isServerOnline = true;
                    serverDot.className = 'dot online';
                    serverText.innerHTML = 'Downloader: <strong>' + (data.client_display || '{downloader_name}') + '</strong> (1-Click Ready)';
                    return;
                }}
            }} catch (e) {{}}
            isServerOnline = false;
            serverDot.className = 'dot offline';
            serverText.innerHTML = 'Downloader: <strong>{downloader_name}</strong> (Offline)';
        }}

        checkServerStatus();
        setInterval(checkServerStatus, 5000);

        async function requestMedia(btn, imdbId, title, year) {{
            btn.disabled = true;
            btn.textContent = 'Requesting...';
            btn.className = 'btn-request loading';

            try {{
                const magnet = btn.getAttribute('data-magnet') || '';
                let url = 'http://127.0.0.1:7890/api/request?imdb=' + encodeURIComponent(imdbId) + '&title=' + encodeURIComponent(title) + '&year=' + year;
                if (magnet) {{
                    url += '&magnet=' + encodeURIComponent(magnet);
                }}
                const res = await fetch(url, {{ method: 'POST' }});
                const data = await res.json();

                if (data.success) {{
                    btn.textContent = '✓ Requested';
                    btn.className = 'btn-request btn-success';
                    showToast(data.message, 'success');
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
                openModal(imdbId, title, year);
            }}
        }}

        function showToast(msg, type) {{
            const c = document.getElementById('toastContainer');
            const t = document.createElement('div');
            t.className = 'toast ' + (type || 'info');
            t.textContent = msg;
            c.appendChild(t);
            setTimeout(() => {{ t.remove(); }}, 5000);
        }}

        function openModal(imdbId, title, year) {{
            const modal = document.getElementById('fallbackModal');
            const singleCmd = document.getElementById('singleCmd');
            if (imdbId) {{
                singleCmd.textContent = '.venv/Scripts/python.exe retrieve_media.py --imdb ' + imdbId;
            }} else {{
                singleCmd.textContent = '.venv/Scripts/python.exe retrieve_media.py --title "' + title + '" --year ' + year;
            }}
            modal.style.display = 'flex';
        }}

        function closeModal() {{
            document.getElementById('fallbackModal').style.display = 'none';
        }}

        function copyToClipboard(elementId) {{
            const text = document.getElementById(elementId).textContent;
            navigator.clipboard.writeText(text);
            showToast('Copied to clipboard!', 'success');
        }}

        function applyFilters() {{
            let visible = 0;
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

                const show = matchStatus && matchSource;
                r.style.display = show ? '' : 'none';
                if (show) visible++;
            }});

            visibleCountSpan.textContent = visible;
            noMatchesDiv.style.display = (visible === 0) ? 'block' : 'none';
            const isFiltered = (currentFilterType !== 'all') || (currentSourceFilter !== 'all');
            resetBtn.style.display = isFiltered ? 'inline-block' : 'none';
        }}

        function resetFilter() {{
            currentFilterType = 'all';
            currentFilterVal = '';
            currentSourceFilter = 'all';

            cards.forEach(c => c.classList.remove('active'));
            const totalCard = document.querySelector('.card[data-filter="all"]');
            if (totalCard) totalCard.classList.add('active');

            sourcePills.forEach(p => p.classList.remove('active'));
            const allPill = document.querySelector('.source-pill[data-source="all"]');
            if (allPill) allPill.classList.add('active');

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
    </script>
</body>
</html>
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"HTML report generated at {file_path}")


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
        out_file = os.path.join(OUTPUT_DIR, "media_report.html")
        generate_html(sanitized, out_file)

    asyncio.run(main())
