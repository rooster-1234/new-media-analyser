import unittest
import app

class TestPosterUI(unittest.TestCase):
    def test_poster_rendering_with_imdb(self):
        items = [{
            "title": "Gladiator II (2024)",
            "clean_title": "Gladiator II",
            "year": 2024,
            "imdb_id": "tt2064968",
            "imdb_score": "6.8",
            "genres": ["Action", "Adventure"],
            "synopsis": "After his home is conquered, Lucius enters the Colosseum.",
            "sources": ["TPB", "uIndex"],
            "source_keys": ["tpb", "uindex"],
            "filter_status": "Consider",
            "genre_match": "High"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("media-cell", html)
        self.assertIn("poster-container", html)
        self.assertIn("poster-thumb", html)
        self.assertIn("poster-hover-preview", html)
        self.assertIn("https://images.metahub.space/poster/small/tt2064968/img", html)
        self.assertIn("https://images.metahub.space/poster/medium/tt2064968/img", html)

    def test_poster_rendering_fallback_placeholder(self):
        items = [{
            "title": "Unknown Home Video 2026",
            "clean_title": "Unknown Home Video",
            "year": 2026,
            "imdb_id": "",
            "imdb_score": "N/A",
            "genres": [],
            "synopsis": "Some home video description.",
            "sources": ["TPB"],
            "source_keys": ["tpb"],
            "filter_status": "Consider",
            "genre_match": "Low"
        }]
        html = app.render_dashboard_page("testuser", {}, items)
        self.assertIn("media-cell", html)
        self.assertIn("poster-placeholder", html)
        self.assertIn("🎬", html)

if __name__ == "__main__":
    unittest.main()
