"""
Media Release Filter Module
Filters scraped media releases against user preferences and library state:
1. Excludes items that already exist in the Plex library (lookup index).
2. Filters out items older than the target release year (e.g. current year).
3. Filters out items with an IMDb score below the configured threshold (e.g. 5.0).
Flags surviving candidate releases as 'Consider'.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ENGLISH_COUNTRIES = {"united states", "united kingdom", "canada", "australia", "new zealand", "ireland"}

FOREIGN_LANGUAGES = [
    "tagalog", "filipino", "hindi", "korean", "japanese", "chinese",
    "german", "french", "spanish", "italian", "russian", "tamil",
    "telugu", "malayalam", "kannada", "thai", "vietnamese", "polish",
    "turkish", "dutch", "portuguese", "swedish", "danish", "norwegian", "arabic"
]


def normalize_title(title: str) -> str:
    """Normalize a title for clean matching against Plex lookup indices."""
    if not title:
        return ""
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_release_title_and_year(raw: str) -> Tuple[str, Optional[int]]:
    """
    Parses a release filename/title to extract the clean title and 4-digit year.
    Examples:
        'The Odyssey 2026 1080p AMZN WEB-DL...' -> ('The Odyssey', 2026)
        'The Infiltrator (2016) English...'     -> ('The Infiltrator', 2016)
        'Deep Impact (1998) DS4K...'            -> ('Deep Impact', 1998)
    """
    if not raw:
        return "", None

    # Strip file extensions
    name = re.sub(r"\.(mkv|mp4|avi)$", "", raw, flags=re.IGNORECASE)

    # Search for 4-digit year (19xx or 20xx) with delimiters
    m = re.search(r"^(.*?)[.\s\(\[]((?:19\d\d|20\d\d))[.\s\)\]]", name)
    if m:
        clean = re.sub(r"[._]", " ", m.group(1)).strip()
        clean = re.sub(r"[\s\-]+$", "", clean).strip()
        return clean, int(m.group(2))

    # Fallback: year at end of title
    m2 = re.search(r"^(.*?)[.\s\(\[]((?:19\d\d|20\d\d))$", name)
    if m2:
        clean = re.sub(r"[._]", " ", m2.group(1)).strip()
        return clean, int(m2.group(2))

    clean = re.sub(r"[._]", " ", name).strip()
    return clean, None


class MediaFilter:
    """
    Evaluates new release items against Plex profile and environment filter criteria.
    """

    def __init__(
        self,
        profile_path: str = "plex_library_profile.json",
        env_file: Optional[str] = ".env",
    ):
        if env_file and os.path.exists(env_file):
            load_dotenv(dotenv_path=env_file)
        else:
            load_dotenv()

        # Filter criteria configuration from environment
        self.exclude_existing = os.getenv("FILTER_EXCLUDE_EXISTING", "true").lower() in ("true", "1", "yes")
        self.only_current_year = os.getenv("FILTER_ONLY_CURRENT_YEAR", "true").lower() in ("true", "1", "yes")
        raw_tf = os.getenv("FILTER_TIMEFRAME")
        if raw_tf:
            self.timeframe = raw_tf.strip().lower()
        elif not self.only_current_year:
            self.timeframe = "all"
        else:
            self.timeframe = "365d"
        self.require_english = os.getenv("FILTER_REQUIRE_ENGLISH", "true").lower() in ("true", "1", "yes")
        self.target_year = int(os.getenv("FILTER_TARGET_YEAR", str(datetime.now().year)))
        self.min_imdb_score = float(os.getenv("FILTER_MIN_IMDB_SCORE", "5.0"))
        self.genre_high_threshold = float(os.getenv("GENRE_MATCH_HIGH_THRESHOLD", "75.0"))
        self.genre_medium_threshold = float(os.getenv("GENRE_MATCH_MEDIUM_THRESHOLD", "40.0"))
        raw_disliked = os.getenv("FILTER_DISLIKED_GENRES", "")
        self.disliked_genres = [g.strip().lower() for g in raw_disliked.split(",") if g.strip()]

        self.profile_path = Path(profile_path)
        self.existing_movies: Dict[str, dict] = {}
        self.existing_shows: Dict[str, dict] = {}
        self.category_counts: Dict[str, int] = {}

        self.load_plex_profile()

    def load_plex_profile(self):
        """Load Plex lookup index and category preference counts."""
        if not self.profile_path.exists():
            logger.warning(f"Plex profile '{self.profile_path}' not found. Duplicate checks and genre matching disabled.")
            return

        try:
            with open(self.profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                indices = data.get("lookup_indices", {})
                self.existing_movies = indices.get("movies", {})
                self.existing_shows = indices.get("shows", {})

                # Load user category/sub-genre preference counts
                taste = data.get("taste_profile", {})
                raw_cats = taste.get("categories") or taste.get("top_overall_genres") or {}
                self.category_counts = {}
                for cat, count in raw_cats.items():
                    if not str(cat).endswith("(Watched)"):
                        self.category_counts[str(cat).lower().strip()] = int(count)

                logger.info(
                    f"Loaded Plex lookup indices: {len(self.existing_movies)} movies, "
                    f"{len(self.existing_shows)} shows, and {len(self.category_counts)} category preferences."
                )
        except Exception as e:
            logger.error(f"Failed to load Plex profile '{self.profile_path}': {e}")

    def get_category_count(self, genre_name: str) -> int:
        """Get library count for a genre, handling common alias variations."""
        if not genre_name:
            return 0
        norm = genre_name.lower().strip()
        aliases = {
            "sci-fi": "science fiction",
            "scifi": "science fiction",
            "science fiction": "science fiction",
            "action/adventure": "action",
            "action & adventure": "action",
            "sci-fi & fantasy": "science fiction",
            "war & politics": "war",
            "reality-tv": "reality",
            "music": "music",
            "musical": "musical",
        }
        target = aliases.get(norm, norm)
        return self.category_counts.get(target, self.category_counts.get(norm, 0))

    def calculate_genre_match(self, genres: List[str]) -> Tuple[str, float, str]:
        """
        Calculates genre preference match level (High, Medium, Low) based on Plex category counts.
        Considers weakest link (min genre count) and heavily penalizes bottom-tier or disliked genres.
        """
        if not genres:
            return "Low", 0.0, "No genres provided"

        # Check for explicitly disliked genres from environment
        for g in genres:
            if g.lower().strip() in self.disliked_genres:
                return "Low", 0.0, f"Contains disliked genre: '{g}'"

        counts = [self.get_category_count(g) for g in genres]
        min_c = min(counts) if counts else 0
        avg_c = (sum(counts) / len(counts)) if counts else 0

        # Balanced score factors in the weakest link (min count) and the average
        # Low affinity genres severely drag down the score
        balanced_score = round(0.5 * avg_c + 0.5 * min_c, 1)

        breakdown = ", ".join(f"{g} ({self.get_category_count(g)})" for g in genres)

        # Check if release contains bottom-tier genres (< 30 library items)
        has_bottom_tier = any(self.get_category_count(g) < 30 for g in genres)

        # High requires both a strong balanced score (>= threshold) and no bottom-tier genres
        if balanced_score >= self.genre_high_threshold and not has_bottom_tier:
            return "High", balanced_score, breakdown
        elif balanced_score >= self.genre_medium_threshold:
            return "Medium", balanced_score, breakdown
        else:
            return "Low", balanced_score, breakdown

    def is_in_plex(self, clean_title: str) -> bool:
        """Check if title exists in Plex library."""
        norm = normalize_title(clean_title)
        if not norm:
            return False
        return norm in self.existing_movies or norm in self.existing_shows

    def is_non_english(self, item: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determines whether a media item is Non-English based on metadata, title, and page text.
        Returns: (is_non_eng, detected_language)
        """
        meta_lang = item.get("language")
        meta_country = item.get("country")
        raw_title = item.get("raw_title") or item.get("title", "")
        page_content = item.get("page_content", "")

        # 1. Cinemeta language field
        if meta_lang:
            langs = [l.strip().lower() for l in re.split(r"[,/|]", meta_lang) if l.strip()]
            has_english = any("english" in l for l in langs)
            has_foreign = any(any(fl in l for fl in FOREIGN_LANGUAGES) for l in langs)
            if has_foreign and not has_english:
                return True, meta_lang
            if has_english:
                return False, "English"

        # 2. Release title tags (e.g. Tagalog, Hindi, Telugu, Korean, etc.)
        title_lower = raw_title.lower()
        for fl in FOREIGN_LANGUAGES:
            if re.search(rf"\b{fl}\b", title_lower):
                if "english" not in title_lower and "eng" not in title_lower:
                    return True, fl.capitalize()

        # 3. Page description language tag
        if page_content:
            m = re.search(r"(?:Language|Audio|Lang)\s*[:|-]\s*([^\n\r<]+)", page_content, re.IGNORECASE)
            if m:
                desc_lang = m.group(1).lower().strip()
                if any(fl in desc_lang for fl in FOREIGN_LANGUAGES) and "english" not in desc_lang:
                    return True, m.group(1).strip()

        # 4. Metadata country (if primary/first country of origin is non-English)
        if meta_country and not meta_lang:
            countries = [c.strip().lower() for c in meta_country.split(",") if c.strip()]
            if countries and countries[0] not in ENGLISH_COUNTRIES:
                return True, countries[0].title()
            has_eng_country = any(c in ENGLISH_COUNTRIES for c in countries)
            if not has_eng_country:
                return True, countries[0].title()

        return False, "English"

    def check_release_date_cutoff(self, release_date_str: Optional[str], year: Optional[int]) -> Tuple[bool, str]:
        """
        Evaluates whether an item passes the release timeframe filter.
        Returns (is_too_old: bool, reason: str).
        """
        tf = getattr(self, "timeframe", "365d").lower()
        if tf in ("all", "any"):
            return False, ""
        if not getattr(self, "only_current_year", True) and tf == "year":
            return False, ""

        today = datetime.now().date()

        if tf == "7d":
            cutoff = today - timedelta(days=7)
            label = "last 7 days"
        elif tf == "30d":
            cutoff = today - timedelta(days=30)
            label = "last 30 days"
        elif tf == "90d":
            cutoff = today - timedelta(days=90)
            label = "last 90 days"
        elif tf in ("365d", "12m"):
            cutoff = today - timedelta(days=365)
            label = "last 12 months"
        elif tf == "year":
            if year is not None and year < self.target_year:
                return True, f"Released in {year} (prior to target year {self.target_year})"
            return False, ""
        else:
            if year is not None and year < self.target_year:
                return True, f"Released in {year} (prior to target year {self.target_year})"
            return False, ""

        # Check exact US release date (YYYY-MM-DD) if available
        if release_date_str:
            try:
                rel_date = datetime.strptime(str(release_date_str)[:10], "%Y-%m-%d").date()
                if rel_date < cutoff:
                    return True, f"US release date {rel_date} is older than {label} (cutoff: {cutoff})"
                return False, ""
            except Exception:
                pass

        # Fallback to year comparison if specific release date is not known
        if year is not None:
            if year < cutoff.year:
                return True, f"Released in {year} (prior to {label} cutoff year {cutoff.year})"
            return False, ""

        return False, ""

    def evaluate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a single release item and assign a filter status:
        - 'Exists'     : Item already in Plex library
        - 'Low Score'  : IMDb rating is below configured threshold
        - 'Non-English': Foreign language release
        - 'Too Old'    : Released before the target timeframe
        - 'Consider'   : Passes all filter criteria
        """
        raw_title = item.get("title", "")
        clean_title, parsed_year = parse_release_title_and_year(raw_title)

        year = item.get("year") or parsed_year
        score_str = item.get("imdb_score", "N/A")

        status = None
        reason = ""

        # 1. Check if item already exists in Plex
        in_library = self.is_in_plex(clean_title)
        item["in_library"] = in_library
        if in_library:
            status = "Exists"
            reason = f"'{clean_title}' already exists in Plex library"

        # 2. Check IMDb score threshold (checked before Too Old so low scoring films are properly flagged)
        elif score_str and score_str != "N/A":
            try:
                score_num = float(score_str)
                if score_num < self.min_imdb_score:
                    status = "Low Score"
                    reason = f"IMDb score {score_num} is below threshold ({self.min_imdb_score})"
            except (ValueError, TypeError):
                pass

        # 3. Check for Non-English Language
        if status is None and self.require_english:
            is_non_eng, detected_lang = self.is_non_english(item)
            if is_non_eng:
                status = "Non-English"
                reason = f"Language detected as {detected_lang} (Non-English)"

        # 4. Check release timeframe / US release date
        if status is None:
            rel_date_str = item.get("release_date")
            is_old, old_reason = self.check_release_date_cutoff(rel_date_str, year)
            if is_old:
                status = "Too Old"
                reason = old_reason

        # 5. Default: Candidate to consider
        if status is None:
            status = "Consider"
            reason = "Meets all criteria"

        # 6. Evaluate Genre Match against Plex taste profile categories
        genres = item.get("genres") or []
        if isinstance(genres, str):
            genres = [g.strip() for g in re.split(r"[,/|]", genres) if g.strip()]
        match_level, genre_score, breakdown = self.calculate_genre_match(genres)

        item["clean_title"] = clean_title
        item["year"] = year
        item["filter_status"] = status
        item["filter_reason"] = reason
        item["genres"] = genres
        item["genre_match"] = match_level
        item["genre_score"] = genre_score
        item["genre_match_reason"] = breakdown

        return item

    def filter_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Evaluate a list of releases."""
        return [self.evaluate(item) for item in items]

    def get_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """Calculate counts for each filter status."""
        counts = {"Consider": 0, "Exists": 0, "Too Old": 0, "Low Score": 0, "Non-English": 0, "Total": len(items)}
        for item in items:
            status = item.get("filter_status", "Consider")
            counts[status] = counts.get(status, 0) + 1
        return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    flt = MediaFilter()
    print("\n--- Filter Criteria Settings ---")
    print(f"Exclude Existing in Plex : {flt.exclude_existing}")
    print(f"Only Current Year        : {flt.only_current_year} (Target: {flt.target_year})")
    print(f"Minimum IMDb Score       : {flt.min_imdb_score}")
    print("-" * 35)

    test_titles = [
        {"title": "The Odyssey 2026 1080p AMZN WEB-DL DUAL DDP5 1 H 264-KyoGo", "imdb_score": "8.4", "genres": ["Action", "Adventure", "Drama"]},
        {"title": "The Infiltrator (2016) English (1080p BD-Rip AC3 5.1 10bit HEVC - SP1EG3L)", "imdb_score": "7.0", "genres": ["Biography", "Crime", "Drama"]},
        {"title": "Musk (2026) WEB-DL", "imdb_score": "N/A", "genres": ["Documentary"]},
        {"title": "Forgotten Island (2026) ENG-HIN DS4K 1080p WEBRip", "imdb_score": "7.7", "genres": ["Animation", "Action", "Adventure"]},
        {"title": "Kids Cartoon 2026 1080p WEBRip", "imdb_score": "6.0", "genres": ["Animation", "Family"]},
    ]

    print("\nTest Evaluations:")
    for res in flt.filter_items(test_titles):
        print(f"[{res['filter_status']:<9}] Genre:[{res['genre_match']:<6}] {res['clean_title']} ({res['year']}) - IMDb: {res['imdb_score']} | Genres: {res['genres']} -> {res['genre_match_reason']}")
