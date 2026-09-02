import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import make_badges  # noqa: E402


class TestSlugify(unittest.TestCase):
    def test_matches_existing_badge_folder_names(self):
        # These are the slugs already live under the chapter-badges Drive folder;
        # a drift here would mismatch every existing folder.
        cases = {
            "Mexico City": "mexico_city",
            "Delhi NCR": "delhi_ncr",
            "Silicon Valley": "silicon_valley",
            "Washington DC": "washington_dc",
            "Buenos Aires": "buenos_aires",
            "Montréal": "montreal",
            "Cape Town": "cape_town",
        }
        for name, expected in cases.items():
            self.assertEqual(make_badges.slugify(name), expected)


class TestBuild(unittest.TestCase):
    def test_writes_four_files_per_chapter(self):
        with tempfile.TemporaryDirectory() as d:
            slug, made = make_badges.build("Dublin", d)
            self.assertEqual(slug, "dublin")
            self.assertEqual(len(made), 4)
            for p in made:
                self.assertTrue(os.path.isfile(p))
                self.assertGreater(os.path.getsize(p), 0)


if __name__ == "__main__":
    unittest.main()
