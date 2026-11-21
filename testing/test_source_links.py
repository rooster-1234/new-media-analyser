import unittest
import app

class TestSourceLinks(unittest.TestCase):
    def test_dual_source_pills_and_links(self):
        items = [{
            "title": "Gladiator II (2024)",
            "clean_title": "Gladiator II",
            "year": 2024,
            "imdb_id": "tt2064968",
            "sources": ["TPB", "uIndex"],
            "source_keys": ["tpb", "uindex"],
            "source_details": {
                "TPB": {"href": "https://thepiratebay.org/description.php?id=79011936"},
                "uIndex": {"href": "https://uindex.org/details.php?id=88312"}
            },
            "filter_status": "Consider",
            "genre_match": "High"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("badge-source-tpb", html)
        self.assertIn("badge-source-uindex", html)
        self.assertIn("PB ↗", html)
        self.assertIn("uIndex ↗", html)
        self.assertIn("https://thepiratebay.org/description.php?id=79011936", html)
        self.assertIn("https://uindex.org/details.php?id=88312", html)
        self.assertIn('target="_blank"', html)

    def test_single_source_fallback_link(self):
        items = [{
            "title": "Alien Romulus (2024)",
            "clean_title": "Alien Romulus",
            "year": 2024,
            "imdb_id": "tt18412256",
            "sources": ["TPB"],
            "source_keys": ["tpb"],
            "filter_status": "Consider",
            "genre_match": "Medium"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("PB ↗", html)
        self.assertNotIn("uIndex ↗", html)
        self.assertIn("https://thepiratebay.org/search.php?q=Alien+Romulus", html)

    def test_uindex_only_fallback_link(self):
        items = [{
            "title": "Dune Part Two (2024)",
            "clean_title": "Dune Part Two",
            "year": 2024,
            "imdb_id": "tt15239678",
            "sources": ["uIndex"],
            "source_keys": ["uindex"],
            "filter_status": "Consider",
            "genre_match": "High"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("uIndex ↗", html)
        self.assertNotIn("PB ↗", html)
        self.assertIn("https://uindex.org/search.php?search=Dune+Part+Two", html)

    def test_stacked_source_badges_and_logo(self):
        items = [{
            "title": "Gladiator II (2024)",
            "clean_title": "Gladiator II",
            "year": 2024,
            "imdb_id": "tt2064968",
            "sources": ["TPB", "uIndex"],
            "source_keys": ["tpb", "uindex"],
            "filter_status": "Consider",
            "genre_match": "High"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("flex-direction: column", html)
        self.assertIn("lensGrad", html)
        self.assertIn("favicon.svg", html)

if __name__ == "__main__":
    unittest.main()
