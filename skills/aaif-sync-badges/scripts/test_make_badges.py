import importlib.util
import os
import sys
import tempfile
import unittest
import xml.dom.minidom

sys.path.insert(0, os.path.dirname(__file__))
import make_badges  # noqa: E402

# CI installs only pytest/pyyaml (see .github/workflows/validate.yml) --
# cairosvg is a manual, documented-in-SKILL.md dependency for the PNG render
# step, so anything that calls build() must skip gracefully where it's absent
# rather than fail the whole suite.
HAS_CAIROSVG = importlib.util.find_spec("cairosvg") is not None


class TestSlugify(unittest.TestCase):
    def test_matches_representative_edge_cases(self):
        # Accents, spaces, and abbreviations -- a regression here would
        # mismatch a chapter against whatever badge folder already carries
        # its slug in Drive.
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


class TestSvgEscaping(unittest.TestCase):
    """A chapter's Drive folder display name is untrusted (any of its
    organizers can rename it) and lands in the SVG unescaped otherwise --
    regression coverage for the XML-injection fix."""

    def test_ampersand_and_angle_brackets_are_escaped(self):
        svg = make_badges.colour_svg('R&D <VILLE>')
        xml.dom.minidom.parseString(svg)  # raises if not well-formed
        self.assertIn("R&amp;D &lt;VILLE&gt;", svg)
        self.assertNotIn("<VILLE>", svg)

    def test_plain_name_parses_and_renders_unescaped(self):
        svg = make_badges.colour_svg("DUBLIN")
        xml.dom.minidom.parseString(svg)
        self.assertIn(">DUBLIN<", svg)


class TestColourAndWhiteVariants(unittest.TestCase):
    def test_variants_use_different_palettes(self):
        colour = make_badges.colour_svg("DUBLIN")
        white = make_badges.white_svg("DUBLIN")
        self.assertIn(make_badges.ORANGE, colour)
        self.assertNotIn(make_badges.ORANGE, white)
        self.assertIn("#FFFFFF", white)


@unittest.skipUnless(HAS_CAIROSVG, "cairosvg not installed")
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
