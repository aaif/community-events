import os
import sys
import tempfile
import unittest
import xml.dom.minidom

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "lib"))
import make_agent_badge  # noqa: E402
from aaif_events.report_style import find_chrome  # noqa: E402

# CI installs neither Chrome nor cairosvg (see .github/workflows/validate.yml)
# -- this generator's PNG step needs headless Chrome (DESIGN.md's one allowed
# renderer), so tests that call build() must skip gracefully where it's absent.
HAS_CHROME = bool(find_chrome())


class TestSlugify(unittest.TestCase):
    def test_matches_make_badges_slugify(self):
        # Both generators are invoked with the SAME slug by sync_badges.py, but
        # each also has its own standalone CLI -- they must stay in agreement.
        sys.path.insert(0, os.path.dirname(__file__))
        import make_badges
        for name in ("Mexico City", "Delhi NCR", "Washington DC", "Montréal"):
            self.assertEqual(make_agent_badge.slugify(name), make_badges.slugify(name))


class TestSvgContent(unittest.TestCase):
    def test_well_formed_and_shows_city_name(self):
        svg = make_agent_badge._svg("DUBLIN", "Dublin")
        xml.dom.minidom.parseString(svg)  # raises if not well-formed
        self.assertIn(">DUBLIN<", svg)

    def test_deterministic_per_chapter_colour(self):
        # Same chapter name -> same mascot colour every run (agent_art.chapter_scene
        # hashes the name), so re-running the sync doesn't flap between colours.
        svg1 = make_agent_badge._svg("DUBLIN", "Dublin")
        svg2 = make_agent_badge._svg("DUBLIN", "Dublin")
        self.assertEqual(svg1, svg2)

    def test_escapes_untrusted_chapter_names(self):
        # The chapter display name comes from a live Drive folder any of its
        # organizers can rename -- mirrors make_badges.py's escaping fix.
        svg = make_agent_badge._svg("R&amp;D", "R&D")
        xml.dom.minidom.parseString(svg)


@unittest.skipUnless(HAS_CHROME, "headless Chrome not installed")
class TestBuild(unittest.TestCase):
    def test_writes_two_files(self):
        with tempfile.TemporaryDirectory() as d:
            slug, made = make_agent_badge.build("Dublin", d)
            self.assertEqual(slug, "dublin")
            self.assertEqual(len(made), 2)
            for p in made:
                self.assertTrue(os.path.isfile(p))
                self.assertGreater(os.path.getsize(p), 0)


if __name__ == "__main__":
    unittest.main()
