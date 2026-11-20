"""
Plex Library Analyzer Agent
Scans an existing Plex Media Server library, catalogs movies and TV shows,
analyzes user genre and sub-genre preferences, and exports a structured library profile.
"""

import os
import sys
import json
import re
import concurrent.futures
from collections import Counter
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

try:
    from plexapi.server import PlexServer
    from plexapi.exceptions import Unauthorized, NotFound
except ImportError:
    print("Error: 'plexapi' is not installed. Please run: pip install -r requirements.txt")
    sys.exit(1)


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy/clean matching."""
    if not title:
        return ""
    # Lowercase and remove punctuation/special characters
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    # Collapse multiple whitespaces
    return re.sub(r"\s+", " ", cleaned).strip()


class PlexLibraryAnalyzer:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.server = None

    def connect(self) -> bool:
        """Connect to the Plex Media Server."""
        print(f"Connecting to Plex Server at {self.base_url}...")
        try:
            self.server = PlexServer(self.base_url, self.token, timeout=self.timeout)
            print(f"Successfully connected to Plex Server: '{self.server.friendlyName}' (v{self.server.version})")
            return True
        except Unauthorized:
            print("Authentication failed: The provided PLEX_TOKEN is invalid.")
            return False
        except Exception as e:
            print(f"Failed to connect to Plex Server at {self.base_url}: {e}")
            return False

    @staticmethod
    def _fetch_full_item(item):
        """Reload item to fetch full metadata including all genres and sub-genres."""
        try:
            item.reload()
        except Exception:
            pass
        return item

    def get_sections(self, movie_filter=None, tv_filter=None):
        """Discover movie and show sections."""
        movie_sections = []
        tv_sections = []

        all_sections = self.server.library.sections()
        for section in all_sections:
            if section.type == "movie":
                if not movie_filter or section.title.lower() in [f.lower() for f in movie_filter]:
                    movie_sections.append(section)
            elif section.type == "show":
                if not tv_filter or section.title.lower() in [f.lower() for f in tv_filter]:
                    tv_sections.append(section)

        return movie_sections, tv_sections

    def analyze_movies(self, sections):
        """Analyze all movies across the specified sections, including full categories and sub-genres."""
        movies_data = []
        category_counter = Counter()
        primary_genre_counter = Counter()
        subgenre_counter = Counter()
        director_counter = Counter()
        watched_count = 0

        for section in sections:
            print(f"Scanning movie library: '{section.title}'...")
            raw_items = section.all()
            print(f"Found {len(raw_items)} items in '{section.title}'. Loading full metadata and sub-genres...")

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                items = list(executor.map(self._fetch_full_item, raw_items))

            print(f"Successfully loaded {len(items)} items. Processing...")

            for item in items:
                genres = [g.tag for g in getattr(item, "genres", [])]
                primary_genre = genres[0] if genres else "Unknown"
                sub_genres = genres[1:] if len(genres) > 1 else []
                directors = [d.tag for d in getattr(item, "directors", [])]
                is_watched = bool(getattr(item, "isPlayed", False) or (getattr(item, "viewCount", 0) or 0) > 0)
                if is_watched:
                    watched_count += 1

                # Top-level genre counter (1 primary genre per title)
                primary_genre_counter[primary_genre] += 1
                if is_watched:
                    primary_genre_counter[f"{primary_genre} (Watched)"] += 1

                # All categories counter (multiple categories per title)
                for g in genres:
                    category_counter[g] += 1
                    if is_watched:
                        category_counter[f"{g} (Watched)"] += 1

                for sg in sub_genres:
                    subgenre_counter[sg] += 1
                    if is_watched:
                        subgenre_counter[f"{sg} (Watched)"] += 1

                for d in directors:
                    director_counter[d] += 1

                movies_data.append({
                    "title": item.title,
                    "normalized_title": normalize_title(item.title),
                    "year": getattr(item, "year", None),
                    "genres": genres,
                    "categories": genres,
                    "primary_genre": primary_genre,
                    "primary_category": primary_genre,
                    "sub_genres": sub_genres,
                    "sub_categories": sub_genres,
                    "directors": directors,
                    "rating": getattr(item, "rating", None),
                    "audience_rating": getattr(item, "audienceRating", None),
                    "view_count": getattr(item, "viewCount", 0) or 0,
                    "is_watched": is_watched,
                    "duration_mins": round((item.duration or 0) / 60000, 1) if getattr(item, "duration", None) else None,
                    "added_at": str(getattr(item, "addedAt", "")),
                    "library_section": section.title
                })

        return {
            "total_movies": len(movies_data),
            "watched_movies": watched_count,
            "top_level_genres": dict(primary_genre_counter.most_common()),
            "categories": dict(category_counter.most_common()),
            "top_genres": dict(category_counter.most_common()),
            "top_sub_genres": dict(subgenre_counter.most_common()),
            "top_directors": dict(director_counter.most_common(15)),
            "items": movies_data
        }

    def analyze_shows(self, sections):
        """Analyze all TV shows across the specified sections, including full categories and sub-genres."""
        shows_data = []
        category_counter = Counter()
        primary_genre_counter = Counter()
        subgenre_counter = Counter()
        watched_shows_count = 0
        total_episodes_count = 0

        for section in sections:
            print(f"Scanning TV library: '{section.title}'...")
            raw_items = section.all()
            print(f"Found {len(raw_items)} shows in '{section.title}'. Loading full metadata and sub-genres...")

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                items = list(executor.map(self._fetch_full_item, raw_items))

            print(f"Successfully loaded {len(items)} shows. Processing...")

            for show in items:
                genres = [g.tag for g in getattr(show, "genres", [])]
                primary_genre = genres[0] if genres else "Unknown"
                sub_genres = genres[1:] if len(genres) > 1 else []

                # Top-level genre counter (1 primary genre per show)
                primary_genre_counter[primary_genre] += 1

                # All categories counter (multiple categories per show)
                for g in genres:
                    category_counter[g] += 1

                for sg in sub_genres:
                    subgenre_counter[sg] += 1

                # Gather seasons details
                seasons_info = []
                show_episodes = 0
                show_unwatched = 0

                try:
                    for season in show.seasons():
                        if season.seasonNumber == 0:
                            # Specials are season 0; record but keep note
                            pass
                        episodes = season.episodes()
                        ep_count = len(episodes)
                        unwatched_ep = sum(1 for ep in episodes if not ep.isPlayed)
                        show_episodes += ep_count
                        show_unwatched += unwatched_ep

                        seasons_info.append({
                            "season_number": season.seasonNumber,
                            "season_title": season.title,
                            "episode_count": ep_count,
                            "unwatched_episodes": unwatched_ep
                        })
                except Exception as ex:
                    # In case of shallow retrieval or timeout on deep season calls
                    pass

                total_episodes_count += show_episodes
                is_fully_watched = (show_episodes > 0 and show_unwatched == 0)
                if is_fully_watched:
                    watched_shows_count += 1

                shows_data.append({
                    "title": show.title,
                    "normalized_title": normalize_title(show.title),
                    "year": getattr(show, "year", None),
                    "genres": genres,
                    "categories": genres,
                    "primary_genre": primary_genre,
                    "primary_category": primary_genre,
                    "sub_genres": sub_genres,
                    "sub_categories": sub_genres,
                    "studio": getattr(show, "studio", None),
                    "rating": getattr(show, "rating", None),
                    "total_seasons": len(seasons_info),
                    "seasons": seasons_info,
                    "existing_season_numbers": [s["season_number"] for s in seasons_info],
                    "total_episodes": show_episodes,
                    "unwatched_episodes": show_unwatched,
                    "is_fully_watched": is_fully_watched,
                    "added_at": str(getattr(show, "addedAt", "")),
                    "library_section": section.title
                })

        return {
            "total_shows": len(shows_data),
            "total_episodes": total_episodes_count,
            "fully_watched_shows": watched_shows_count,
            "top_level_genres": dict(primary_genre_counter.most_common()),
            "categories": dict(category_counter.most_common()),
            "top_genres": dict(category_counter.most_common()),
            "top_sub_genres": dict(subgenre_counter.most_common()),
            "items": shows_data
        }

    def generate_profile(self, movie_sections=None, tv_sections=None) -> dict:
        """Run complete analysis and return profile dictionary."""
        mov_sec, tv_sec = self.get_sections(movie_sections, tv_sections)

        movies_analysis = self.analyze_movies(mov_sec) if mov_sec else {
            "total_movies": 0, "watched_movies": 0, "top_level_genres": {}, "categories": {}, "top_genres": {}, "top_sub_genres": {}, "items": []
        }
        shows_analysis = self.analyze_shows(tv_sec) if tv_sec else {
            "total_shows": 0, "total_episodes": 0, "top_level_genres": {}, "categories": {}, "top_genres": {}, "top_sub_genres": {}, "items": []
        }

        # Combine top-level genres (primary genre per item)
        combined_top_level = Counter()
        for g, count in movies_analysis.get("top_level_genres", {}).items():
            if not g.endswith("(Watched)"):
                combined_top_level[g] += count
        for g, count in shows_analysis.get("top_level_genres", {}).items():
            combined_top_level[g] += count

        # Combine all categories (items exist in multiple categories)
        combined_categories = Counter()
        for g, count in movies_analysis.get("categories", {}).items():
            if not g.endswith("(Watched)"):
                combined_categories[g] += count
        for g, count in shows_analysis.get("categories", {}).items():
            combined_categories[g] += count

        combined_subgenres = Counter()
        for g, count in movies_analysis.get("top_sub_genres", {}).items():
            if not g.endswith("(Watched)"):
                combined_subgenres[g] += count
        for g, count in shows_analysis.get("top_sub_genres", {}).items():
            combined_subgenres[g] += count

        # Create quick lookup indices for future matching
        movie_lookup = {
            m["normalized_title"]: {
                "year": m["year"],
                "title": m["title"],
                "genres": m["genres"],
                "categories": m["categories"],
                "primary_genre": m.get("primary_genre"),
                "primary_category": m.get("primary_category"),
                "sub_genres": m.get("sub_genres", []),
                "sub_categories": m.get("sub_categories", [])
            }
            for m in movies_analysis["items"]
        }

        tv_lookup = {
            s["normalized_title"]: {
                "year": s["year"],
                "title": s["title"],
                "genres": s["genres"],
                "categories": s["categories"],
                "primary_genre": s.get("primary_genre"),
                "primary_category": s.get("primary_category"),
                "sub_genres": s.get("sub_genres", []),
                "sub_categories": s.get("sub_categories", []),
                "existing_season_numbers": s["existing_season_numbers"]
            }
            for s in shows_analysis["items"]
        }

        profile = {
            "metadata": {
                "server_name": self.server.friendlyName,
                "server_version": self.server.version,
                "generated_at": datetime.now().isoformat(),
            },
            "taste_profile": {
                "top_level_genres": dict(combined_top_level.most_common()),
                "categories": dict(combined_categories.most_common()),
                "top_overall_genres": dict(combined_categories.most_common()),
                "top_overall_sub_genres": dict(combined_subgenres.most_common()),
                "top_overall_categories": dict(combined_categories.most_common()),
                "top_overall_sub_categories": dict(combined_subgenres.most_common()),
                "movie_top_level_genres": movies_analysis.get("top_level_genres", {}),
                "movie_categories": movies_analysis.get("categories", {}),
                "show_top_level_genres": shows_analysis.get("top_level_genres", {}),
                "show_categories": shows_analysis.get("categories", {}),
                "top_movie_genres": movies_analysis.get("categories", {}),
                "top_movie_sub_genres": movies_analysis.get("top_sub_genres", {}),
                "top_show_genres": shows_analysis.get("categories", {}),
                "top_show_sub_genres": shows_analysis.get("top_sub_genres", {}),
                "top_directors": movies_analysis.get("top_directors", {})
            },
            "summary": {
                "total_movies": movies_analysis["total_movies"],
                "watched_movies": movies_analysis["watched_movies"],
                "total_shows": shows_analysis["total_shows"],
                "total_episodes": shows_analysis["total_episodes"],
                "fully_watched_shows": shows_analysis.get("fully_watched_shows", 0)
            },
            "lookup_indices": {
                "movies": movie_lookup,
                "shows": tv_lookup
            },
            "libraries": {
                "movies": movies_analysis["items"],
                "shows": shows_analysis["items"]
            }
        }

        return profile


def print_summary(profile: dict):
    """Print a clean summary of the profile to stdout."""
    summary = profile["summary"]
    taste = profile["taste_profile"]
    server = profile["metadata"]

    top_level = [(k, v) for k, v in taste.get("top_level_genres", {}).items() if not k.endswith("(Watched)")]
    categories = [(k, v) for k, v in taste.get("categories", {}).items() if not k.endswith("(Watched)")]

    print("\n" + "=" * 68)
    print(f" PLEX LIBRARY PROFILE: {server['server_name']}")
    print(f" Generated: {server['generated_at']}")
    print("=" * 68)
    print(f" Movies in Library : {summary['total_movies']} ({summary['watched_movies']} watched)")
    print(f" TV Shows in Library: {summary['total_shows']} ({summary['total_episodes']} total episodes)")
    print("=" * 68)

    # 1. TOP LEVEL GENRES (Primary Genre per item)
    print("\n TOP LEVEL GENRES (Primary genre per title - single count per item):")
    print("-" * 68)
    col_split = (len(top_level) + 1) // 2
    for i in range(col_split):
        g1, cnt1 = top_level[i]
        line = f"  * {g1:<20}: {cnt1:>3} titles"
        if i + col_split < len(top_level):
            g2, cnt2 = top_level[i + col_split]
            line += f"   * {g2:<20}: {cnt2:>3} titles"
        print(line)

    # 2. ALL CATEGORIES (Multiple categories per item)
    print("\n" + "-" * 68)
    print(" ALL CATEGORIES (Items belong to multiple categories - numbers won't tally):")
    print("-" * 68)
    col_split = (len(categories) + 1) // 2
    for i in range(col_split):
        c1, cnt1 = categories[i]
        line = f"  * {c1:<20}: {cnt1:>3} titles"
        if i + col_split < len(categories):
            c2, cnt2 = categories[i + col_split]
            line += f"   * {c2:<20}: {cnt2:>3} titles"
        print(line)

    # 3. TOP DIRECTORS
    if taste.get("top_directors"):
        print("\n" + "-" * 68)
        print(" TOP DIRECTORS:")
        print("-" * 68)
        directors = list(taste["top_directors"].items())[:6]
        col_split = (len(directors) + 1) // 2
        for i in range(col_split):
            d1, cnt1 = directors[i]
            line = f"  * {d1:<24}: {cnt1:>2} movies"
            if i + col_split < len(directors):
                d2, cnt2 = directors[i + col_split]
                line += f"   * {d2:<24}: {cnt2:>2} movies"
            print(line)

    print("=" * 68 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Analyze Plex Media Library and extract user preferences.")
    parser.add_argument("--env-file", default=".env", help="Path to .env configuration file")
    parser.add_argument("--output", default="plex_library_profile.json", help="Path for JSON output profile")
    parser.add_argument("--summary-only", action="store_true", help="Print summary without writing full JSON")
    args = parser.parse_args()

    # Load environment variables
    env_path = Path(args.env_file)
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    plex_url = os.getenv("PLEX_URL")
    plex_token = os.getenv("PLEX_TOKEN")

    if not plex_url or not plex_token or "your_plex_token" in plex_token:
        print("\n[!] Configuration missing.")
        print(f"Please configure your Plex URL and Token in '{args.env_file}' or set PLEX_URL and PLEX_TOKEN environment variables.")
        print("A template is available at '.env.template'.\n")
        sys.exit(1)

    movie_libs = [l.strip() for l in os.getenv("PLEX_MOVIE_LIBRARIES", "").split(",") if l.strip()]
    tv_libs = [l.strip() for l in os.getenv("PLEX_TV_LIBRARIES", "").split(",") if l.strip()]

    analyzer = PlexLibraryAnalyzer(base_url=plex_url, token=plex_token)
    if not analyzer.connect():
        sys.exit(1)

    profile = analyzer.generate_profile(
        movie_sections=movie_libs if movie_libs else None,
        tv_sections=tv_libs if tv_libs else None
    )

    print_summary(profile)

    if not args.summary_only:
        out_path = Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        print(f"Saved complete profile to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
