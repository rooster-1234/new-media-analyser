import asyncio
import gzip
import html
import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from filter import MediaFilter
from retrieve_media import get_downloader
from plex_analyzer import plex_oauth_login, PlexLibraryAnalyzer

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

SITE_SCAN = os.getenv("SITE_SCAN")
OUTPUT_DIR = os.getenv("OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
MAX_SYNOPSIS_WORDS = int(os.getenv("MAX_SYNOPSIS_WORDS", "60"))
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "").strip()
IMDB_CACHE_DB = os.path.join(OUTPUT_DIR, "imdb_ratings.db")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Media filter instance (initialized on demand or during ensure_plex_profile)
media_filter: Optional[MediaFilter] = None


def ensure_plex_profile(force_sync: bool = False, profile_path: str = "plex_library_profile.json") -> MediaFilter:
    """
    Ensures that a valid Plex library profile exists for duplicate checking and genre matching.
    If PLEX_TOKEN is missing, launches official Plex OAuth in the user's browser.
    If the profile file does not exist or force_sync is requested, scans Plex and generates it.
    """
    global media_filter
    plex_token = os.getenv("PLEX_TOKEN", "").strip()
    profile_file = Path(profile_path)

    # 1. Trigger OAuth login if token is missing
    if not plex_token or "your_plex_token" in plex_token:
        logger.info("Plex token is missing in .env. Launching official Plex OAuth login in your browser...")
        if plex_oauth_login():
            load_dotenv(override=True)
            plex_token = os.getenv("PLEX_TOKEN", "").strip()
        else:
            logger.warning("Plex OAuth login was not completed. Proceeding without Plex library synchronization.")

    # 2. Generate or refresh profile if missing or requested
    if (force_sync or not profile_file.exists()) and plex_token:
        plex_url = os.getenv("PLEX_URL", "").strip()
        logger.info("Generating Plex library profile from Plex Media Server...")
        try:
            analyzer = PlexLibraryAnalyzer(base_url=plex_url, token=plex_token)
            if analyzer.connect():
                profile_data = analyzer.generate_profile()
                with open(profile_file, "w", encoding="utf-8") as f:
                    json.dump(profile_data, f, indent=2, ensure_ascii=False)
                logger.info(f"Successfully generated and saved Plex profile to '{profile_path}'.")
        except Exception as e:
            logger.warning(f"Could not generate Plex profile: {e}")

    media_filter = MediaFilter(profile_path=profile_path)
    return media_filter

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

                # Fallback 1: Query local official IMDb ratings database (from datasets.imdbws.com)
                if score == "N/A":
                    dataset_score = get_imdb_dataset_rating(imdb_id)
                    if dataset_score:
                        score = dataset_score
                        logger.info(f"Retrieved IMDb score {score} for {imdb_id} via official IMDb dataset cache")

                # Fallback 2: Query OMDb API if score is still missing from Cinemeta and API key is set
                if score == "N/A" and OMDB_API_KEY:
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
                    except Exception as omdb_err:
                        logger.debug(f"OMDb fallback failed for {imdb_id}: {omdb_err}")

                res = {"score": score, "genres": genres, "synopsis": synopsis, "language": language, "country": country}
                logger.info(f"Retrieved Cinemeta metadata for {imdb_id}: score={score}, genres={genres}, lang={language}")
                imdb_cache[imdb_id] = res
                return res
        except Exception as e:
            logger.debug(f"Cinemeta lookup failed for {imdb_id} ({media_type}): {e}")

    fallback = {"score": "N/A", "genres": [], "synopsis": "", "language": None, "country": None}
    imdb_cache[imdb_id] = fallback
    return fallback


async def fetch_imdb_score(imdb_id: str) -> str:
    """Helper to fetch IMDb score string."""
    meta = await fetch_imdb_metadata(imdb_id)
    return meta.get("score", "N/A")


async def fetch_media_index() -> List[Dict[str, str]]:
    """
    Scrapes the media index site, extracts release links, and retrieves IMDb scores.
    """
    ensure_plex_profile()

    if not SITE_SCAN:
        logger.error("SITE_SCAN environment variable is not set.")
        return [{"error": "SITE_SCAN variable missing in .env"}]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            logger.info(f"Navigating to {SITE_SCAN}...")
            await page.goto(SITE_SCAN, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle")

            logger.info(f"Page Title: {await page.title()}")

            # Wait for primary results table
            try:
                await page.wait_for_selector("#torrents", timeout=20000)
            except Exception as e:
                logger.warning("Timeout reached for #torrents. Saving debug page content.")
                content = await page.content()
                with open(os.path.join(OUTPUT_DIR, "debug_page_content_final.html"), "w", encoding="utf-8") as f:
                    f.write(content)
                raise e

            await asyncio.sleep(1)

            # Extract release links and download sizes from search results table
            entries = await page.query_selector_all("#torrents li.list-entry")
            results = []
            if entries:
                for entry in entries:
                    title_el = await entry.query_selector(".item-title a, a[href^='/description.php?id=']")
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                    href = await title_el.get_attribute("href")
                    size_el = await entry.query_selector(".item-size, span[class*='size']")
                    size_text = (await size_el.inner_text()).replace("\xa0", " ").strip() if size_el else "N/A"
                    mag_el = await entry.query_selector("a[href^='magnet:']")
                    magnet = (await mag_el.get_attribute("href")) if mag_el else ""
                    if title and href:
                        results.append({"title": title, "href": href, "size": size_text, "magnet": magnet})
            else:
                links = await page.query_selector_all("#torrents a[href^='/description.php?id=']")
                for link in links:
                    title = await link.inner_text()
                    href = await link.get_attribute("href")
                    if title.strip() and href:
                        results.append({"title": title.strip(), "href": href, "size": "N/A", "magnet": ""})

            logger.info(f"Found {len(results)} potential result links.")

            if not results:
                logger.warning("No results were extracted using the primary link selector.")
                return results

            # Deep scrape each release for its IMDb link and score
            logger.info(f"Starting deep scrape for {len(results)} items...")
            for item in results:
                detail_url = urllib.parse.urljoin(SITE_SCAN, item["href"])

                try:
                    await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000, referer=SITE_SCAN)
                    page_content = await page.content()

                    # Fallback/refine size from detail page if missing
                    if not item.get("size") or item.get("size") == "N/A":
                        size_detail_el = await page.query_selector("#size")
                        if size_detail_el:
                            raw_size = (await size_detail_el.inner_text()).replace("\xa0", " ").strip()
                            m_size = re.search(r'([\d.]+\s*(?:GiB|MiB|KiB|GB|MB|KB))', raw_size, re.IGNORECASE)
                            if m_size:
                                item["size"] = m_size.group(1).strip()

                    # Fallback/refine magnet from detail page if missing
                    if not item.get("magnet"):
                        detail_mag = await page.query_selector("a[href^='magnet:']")
                        if detail_mag:
                            item["magnet"] = await detail_mag.get_attribute("href")

                    # Find IMDb ID (e.g., tt1234567) from link or text
                    imdb_match = re.search(r'imdb\.com/title/(tt\d+)', page_content) or re.search(r"(?i)IMDb[:\s]+(tt\d+)", page_content)
                    imdb_id = imdb_match.group(1) if imdb_match else None

                    # Score, Genre & Synopsis resolution: 1) Fast Cinemeta API, 2) Fallback to text in description
                    score = "N/A"
                    genres = []
                    synopsis = ""
                    language = None
                    country = None
                    if imdb_id:
                        meta = await fetch_imdb_metadata(imdb_id)
                        score = meta.get("score", "N/A")
                        genres = meta.get("genres", [])
                        synopsis = meta.get("synopsis", "")
                        language = meta.get("language")
                        country = meta.get("country")

                    if score == "N/A":
                        score = extract_imdb_score_from_text(page_content)

                    # Text fallback for genres if missing from Cinemeta
                    if not genres:
                        genre_match = re.search(r'(?:Genre|Genres|Category)\s*[:|-]\s*([^\n\r<]+)', page_content, re.IGNORECASE)
                        if genre_match:
                            raw_genres = genre_match.group(1).strip()
                            extracted = [g.strip() for g in re.split(r"[,/|]", raw_genres) if g.strip()]
                            genres = [g for g in extracted if len(g) < 25 and not any(ch in g for ch in "<>{}[]")]

                    # Text fallback for synopsis if missing from Cinemeta
                    if not synopsis:
                        plot_match = re.search(r'(?:Plot|Synopsis|Storyline|Description)\s*[:|-]\s*([^\n\r<]+)', page_content, re.IGNORECASE)
                        if plot_match:
                            candidate = plot_match.group(1).strip()
                            if len(candidate) > 20 and not any(x in candidate for x in ["http", "www", "<html>"]):
                                synopsis = candidate

                    item["imdb_id"] = imdb_id
                    item["imdb_url"] = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else "N/A"
                    item["imdb_score"] = score
                    item["genres"] = genres
                    item["synopsis"] = synopsis
                    item["language"] = language
                    item["country"] = country
                    item["page_content"] = page_content

                    # Apply filter criteria (.env: Exists, Low Score, Non-English, Too Old, or Consider)
                    media_filter.evaluate(item)

                    logger.info(
                        f"Scraped: {item.get('clean_title', item['title'])} ({item.get('year')}) | "
                        f"IMDb: {item['imdb_score']} | Genre Match: {item.get('genre_match')} | Status: {item['filter_status']}"
                    )

                except Exception as e:
                    logger.warning(f"Failed to scrape details for {item['title']}: {e}")
                    item["imdb_id"] = None
                    item["imdb_url"] = "N/A"
                    item["imdb_score"] = "N/A"
                    item["genres"] = []
                    item["synopsis"] = ""
                    item["language"] = None
                    item["country"] = None
                    media_filter.evaluate(item)

            logger.info(f"Successfully completed deep scrape of {len(results)} items.")
            return results

        except Exception as e:
            logger.error(f"Error during scraping: {str(e)}")
            return [{"error": str(e)}]

        finally:
            await browser.close()


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
        sanitized.append({
            "title": clean_title,
            "raw_title": item.get("title", ""),
            "year": item.get("year"),
            "size": item.get("size", "N/A"),
            "magnet": item.get("magnet", ""),
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
    """Generate dark-themed HTML report table with filter status column, download size, and request buttons."""
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

    # Compute summary counts
    counts = {"Consider": 0, "Exists": 0, "Too Old": 0, "Low Score": 0, "Non-English": 0}
    genre_counts = {"High": 0, "Medium": 0, "Low": 0}
    for item in data:
        st = item.get("filter_status", "Consider")
        counts[st] = counts.get(st, 0) + 1
        gm = item.get("genre_match", "Low")
        genre_counts[gm] = genre_counts.get(gm, 0) + 1

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

        btn_html = f"<button class='btn-request' data-magnet=\"{html.escape(magnet_str)}\" onclick=\"requestMedia(this, '{imdb_id}', '{escaped_title}', {year_val})\">Request</button>"

        rows.append(f"""
                <tr data-status="{html.escape(status)}" data-genre="{html.escape(genre_match)}">
                    <td>
                        <div class='title-main'>{title_html}</div>
                        {synopsis_html}
                    </td>
                    <td>{year_str}</td>
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
        .summary-bar {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
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
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-align: center; }}
        .badge-consider {{ background: rgba(34, 197, 94, 0.18); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); }}
        .badge-exists {{ background: rgba(59, 130, 246, 0.18); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }}
        .badge-old {{ background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
        .badge-low {{ background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }}
        .badge-non-eng {{ background: rgba(244, 63, 94, 0.18); color: #f43f5e; border: 1px solid rgba(244, 63, 94, 0.4); }}
        .badge-match-high {{ background: rgba(168, 85, 247, 0.18); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); }}
        .badge-match-medium {{ background: rgba(56, 189, 248, 0.18); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }}
        .badge-match-low {{ background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }}
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
        <div class="card card-exists" data-filter="status:Exists"><div class="card-num">{counts.get('Exists', 0)}</div><div class="card-label">In Plex</div></div>
        <div class="card card-low" data-filter="status:Low Score"><div class="card-num">{counts.get('Low Score', 0)}</div><div class="card-label">Low Score</div></div>
        <div class="card card-non-eng" data-filter="status:Non-English"><div class="card-num">{counts.get('Non-English', 0)}</div><div class="card-label">Non-English</div></div>
        <div class="card card-old" data-filter="status:Too Old"><div class="card-num">{counts.get('Too Old', 0)}</div><div class="card-label">Too Old</div></div>
        <div class="card card-genre-high" data-filter="genre:High"><div class="card-num">{genre_counts.get('High', 0)}</div><div class="card-label">High Genre Match</div></div>
    </div>
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
        const rows = document.querySelectorAll('#mediaTableBody tr');
        const visibleCountSpan = document.getElementById('visibleCount');
        const resetBtn = document.getElementById('resetBtn');
        const noMatchesDiv = document.getElementById('noMatches');
        const serverDot = document.getElementById('serverDot');
        const serverText = document.getElementById('serverText');

        let isServerOnline = false;

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

        function setFilter(filterType, filterVal, activeCard) {{
            cards.forEach(c => c.classList.remove('active'));
            activeCard.classList.add('active');

            let visible = 0;
            rows.forEach(r => {{
                let show = false;
                if (filterType === 'all') {{
                    show = true;
                }} else if (filterType === 'status') {{
                    show = (r.getAttribute('data-status') === filterVal);
                }} else if (filterType === 'genre') {{
                    show = (r.getAttribute('data-genre') === filterVal);
                }}
                r.style.display = show ? '' : 'none';
                if (show) visible++;
            }});

            visibleCountSpan.textContent = visible;
            noMatchesDiv.style.display = (visible === 0) ? 'block' : 'none';
            resetBtn.style.display = (filterType === 'all') ? 'none' : 'inline-block';
        }}

        function resetFilter() {{
            const totalCard = document.querySelector('.card[data-filter="all"]');
            if (totalCard) {{
                setFilter('all', '', totalCard);
            }}
        }}

        cards.forEach(card => {{
            card.addEventListener('click', () => {{
                const filter = card.getAttribute('data-filter') || 'all';
                const isCurrentlyActive = card.classList.contains('active');

                if (isCurrentlyActive && filter !== 'all') {{
                    resetFilter();
                    return;
                }}

                if (filter === 'all') {{
                    setFilter('all', '', card);
                }} else {{
                    const [type, val] = filter.split(':');
                    setFilter(type, val, card);
                }}
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
    parser = argparse.ArgumentParser(description="Scrape and analyze new media releases against Plex library.")
    parser.add_argument("--login", action="store_true", help="Log in to Plex via official browser OAuth")
    parser.add_argument("--sync-plex", action="store_true", help="Re-scan Plex library and refresh profile before scraping")
    args = parser.parse_args()

    if args.login:
        plex_oauth_login()
        load_dotenv(override=True)

    if args.sync_plex:
        ensure_plex_profile(force_sync=True)

    async def main():
        data = await fetch_media_index()
        sanitized = sanitize_output(data)
        out_file = os.path.join(OUTPUT_DIR, "media_report.html")
        generate_html(sanitized, out_file)

    asyncio.run(main())
