"""Unit tests for the map-dot backfill (backfill_map_dots.py).

The projection itself is tested in test_create_chapter.py — the backfill imports
it rather than reimplementing it. What is tested here is everything that decides
*which* dot moves *where*: parsing the sheet, joining it to Drive, and the
already-correct/would-move/moved decision that keeps a re-run a no-op.

Run: python3 skills/aaif-create-chapter/scripts/test_backfill_map_dots.py
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import backfill_map_dots as bf  # noqa: E402
import create_chapter as cc  # noqa: E402

SF = (37.7749, -122.4194)
URL = "https://drive.google.com/drive/folders/%s"


def sheet(*rows, headers=None):
    """A fake gws_json values.get response for the Chapters tab."""
    headers = headers or [bf.COL_CITY, bf.COL_FOLDER, bf.COL_GEO]
    return {"values": [headers] + [list(r) for r in rows]}


class TestParseGeo(unittest.TestCase):
    """The `Generated Geolocation` cell is hand-editable free text on a sheet
    other people maintain — a value that isn't a coordinate pair has to read as
    "no coordinate", never as a coordinate that happens to parse."""

    def test_parses_a_plain_pair(self):
        self.assertEqual(bf.parse_geo("37.7749, -122.4194"), SF)

    def test_tolerates_surrounding_and_inner_whitespace(self):
        self.assertEqual(bf.parse_geo("  37.7749 ,-122.4194  "), SF)

    def test_parses_integers_and_negative_latitude(self):
        self.assertEqual(bf.parse_geo("-34, 151"), (-34.0, 151.0))

    def test_none_for_blank_and_missing(self):
        for cell in ("", "   ", None):
            self.assertIsNone(bf.parse_geo(cell), cell)

    def test_none_for_prose(self):
        for cell in ("TBD", "see notes", "37.7749", "37.7749, -122.4194, 0"):
            self.assertIsNone(bf.parse_geo(cell), cell)

    def test_none_when_out_of_range(self):
        """A swapped pair for a high-latitude city (e.g. "24.9384, 60.1699"
        written the wrong way round) is in range and can't be caught here, but a
        genuinely impossible value must not become a dot."""
        for cell in ("91, 0", "-91, 0", "0, 181", "0, -181"):
            self.assertIsNone(bf.parse_geo(cell), cell)


class TestFolderIdFromUrl(unittest.TestCase):
    def test_extracts_the_id(self):
        self.assertEqual(bf.folder_id_from_url(URL % "1AbC-_9"), "1AbC-_9")

    def test_extracts_with_a_query_string(self):
        self.assertEqual(bf.folder_id_from_url(URL % "1AbC" + "?usp=sharing"), "1AbC")

    def test_none_for_blank_or_non_folder_link(self):
        for cell in ("", None, "https://drive.google.com/file/d/1AbC/view", "n/a"):
            self.assertIsNone(bf.folder_id_from_url(cell), cell)


class TestDriftPx(unittest.TestCase):
    def test_zero_when_identical(self):
        self.assertEqual(bf.drift_px((100, 200), (100, 200)), 0.0)

    def test_one_pixel_step_reads_as_one_pixel(self):
        target = cc.marker_offsets(*SF)[0]
        moved = (target[0] + round(bf.EMU_PER_PX_X), target[1])
        self.assertAlmostEqual(bf.drift_px(moved, target), 1.0, places=3)

    def test_combines_both_axes(self):
        target = (0, 0)
        off = (round(3 * bf.EMU_PER_PX_X), round(4 * bf.EMU_PER_PX_Y))
        self.assertAlmostEqual(bf.drift_px(off, target), 5.0, places=3)


class TestReadChapterCoords(unittest.TestCase):
    def read(self, response):
        with mock.patch.object(cc, "gws_json", return_value=response):
            return bf.read_chapter_coords()

    def test_keys_by_drive_folder_id(self):
        coords, bad, _ = self.read(sheet(
            ("San Francisco", URL % "fid1", "37.7749, -122.4194"),
            ("Tokyo", URL % "fid2", "35.6762, 139.6503")))
        self.assertEqual(bad, [])
        self.assertEqual(coords["fid1"], ("San Francisco", 37.7749, -122.4194))
        self.assertEqual(coords["fid2"], ("Tokyo", 35.6762, 139.6503))

    def test_columns_are_found_by_header_name_not_position(self):
        """The tab is a website feed and has been restructured before; a column
        reorder must move the reads with it."""
        coords, _, _ = self.read(sheet(
            ("37.7749, -122.4194", "San Francisco", "ignored", URL % "fid1"),
            headers=[bf.COL_GEO, bf.COL_CITY, "Summary", bf.COL_FOLDER]))
        self.assertEqual(coords["fid1"], ("San Francisco", 37.7749, -122.4194))

    def test_row_without_a_folder_link_is_reported_not_guessed(self):
        coords, bad, _ = self.read(sheet(("Lagos", "", "6.5244, 3.3792")))
        self.assertEqual(coords, {})
        self.assertEqual(bad, [("Lagos", "no %s link" % bf.COL_FOLDER)])

    def test_row_without_usable_coordinates_is_reported(self):
        coords, bad, unusable = self.read(sheet(("Lagos", URL % "fid1", "TBD")))
        self.assertEqual(coords, {})
        self.assertEqual(bad, [("Lagos", "no usable %s" % bf.COL_GEO)])
        # Keyed by folder id so the sweep can tell this folder apart from one
        # the sheet genuinely says nothing about.
        self.assertEqual(unusable,
                         {"fid1": ("Lagos", "no usable %s" % bf.COL_GEO)})

    def test_row_without_a_folder_link_is_not_in_unusable(self):
        """No folder id means there is nothing to key on — the row is reported,
        but no Drive folder can be attributed to it."""
        _coords, bad, unusable = self.read(sheet(("Lagos", "", "6.5244, 3.3792")))
        self.assertEqual(bad, [("Lagos", "no %s link" % bf.COL_FOLDER)])
        self.assertEqual(unusable, {})

    def test_blank_city_rows_are_ignored_silently(self):
        """Trailing blank rows are normal on a sheet — they are not findings."""
        coords, bad, unusable = self.read(sheet(("", "", ""), ("  ", "", "")))
        self.assertEqual((coords, bad, unusable), ({}, [], {}))

    def test_short_rows_do_not_raise(self):
        """Sheets truncates trailing empty cells, so a row can be shorter than
        the header."""
        coords, bad, _ = self.read(sheet(("Lagos",)))
        self.assertEqual(coords, {})
        self.assertEqual(bad, [("Lagos", "no %s link" % bf.COL_FOLDER)])

    def test_aborts_when_a_column_is_missing(self):
        with self.assertRaises(SystemExit) as cm:
            self.read(sheet(headers=[bf.COL_CITY, bf.COL_FOLDER]))
        self.assertIn(bf.COL_GEO, str(cm.exception))

    def test_aborts_on_an_empty_tab(self):
        """An empty read is a failure, not "no chapters" — treating it as the
        latter would make the whole backfill silently do nothing."""
        with self.assertRaises(SystemExit):
            self.read({"values": []})


class TestChapterFolders(unittest.TestCase):
    KIDS = [
        {"id": "f1", "name": "Tokyo", "mimeType": cc.FOLDER},
        {"id": "f2", "name": "Amsterdam", "mimeType": cc.FOLDER},
        {"id": "f3", "name": "TemplateCity", "mimeType": cc.FOLDER},
        {"id": "f4", "name": "Intro to AAIF", "mimeType": cc.PPTX},
    ]

    def folders(self, only_city=None):
        with mock.patch.object(cc, "list_children", return_value=self.KIDS):
            return bf.chapter_folders(only_city)

    def test_skips_the_template_and_non_folders(self):
        self.assertEqual([f["name"] for f in self.folders()], ["Amsterdam", "Tokyo"])

    def test_city_filter_selects_one(self):
        self.assertEqual([f["id"] for f in self.folders("Tokyo")], ["f1"])

    def test_city_filter_aborts_on_an_unknown_name(self):
        with self.assertRaises(SystemExit):
            self.folders("Atlantis")

    def test_city_filter_cannot_select_the_template(self):
        with self.assertRaises(SystemExit):
            self.folders("TemplateCity")


class TestFindDeck(unittest.TestCase):
    def find(self, tree):
        with mock.patch.object(cc, "list_children", side_effect=lambda fid: tree[fid]):
            return bf.find_deck("chapter")

    def test_finds_the_deck_one_level_down(self):
        self.assertEqual(self.find({
            "chapter": [{"id": "sub", "name": "Event Templates (Copy for Each Event)",
                         "mimeType": cc.FOLDER}],
            "sub": [{"id": "deck", "name": "Slides.pptx", "mimeType": cc.PPTX}],
        }), "deck")

    def test_finds_the_deck_at_the_top_level(self):
        self.assertEqual(self.find({
            "chapter": [{"id": "deck", "name": "Slides.pptx", "mimeType": cc.PPTX}],
        }), "deck")

    def test_none_when_the_deck_was_never_cloned(self):
        self.assertIsNone(self.find({
            "chapter": [{"id": "sub", "name": "Banners", "mimeType": cc.FOLDER}],
            "sub": [{"id": "x", "name": "Square Logo.pptx", "mimeType": cc.PPTX}],
        }))

    def test_ignores_a_google_slides_file_of_the_same_name(self):
        """Only a stored .pptx can be byte-rewritten; a native Slides file of the
        same name is a different thing and must not be treated as the deck."""
        self.assertIsNone(self.find({
            "chapter": [{"id": "g", "name": "Slides.pptx",
                         "mimeType": "application/vnd.google-apps.presentation"}],
        }))


class TestProcess(unittest.TestCase):
    """The move/skip decision, with Drive and the OOXML surgery stubbed out."""

    def setUp(self):
        self.uploads, self.moves = [], []
        self.current = cc.marker_offsets(*SF)[0]     # default: already correct
        self.tmpdir = None

    def run_process(self, latlon=SF, write=False, tolerance=1.0):
        self.tmpdir = tempfile.mkdtemp()
        seen = {}

        def fake_download(_fid, out):
            with open(out, "wb") as f:
                f.write(b"deck")
            seen["path"] = out

        with mock.patch.object(cc, "gws_download", fake_download), \
             mock.patch.object(cc, "read_marker_offsets",
                               lambda _p: (self.current, (0, 0)) if self.current else None), \
             mock.patch.object(cc, "reposition_map_marker",
                               lambda p, la, lo: self.moves.append((p, la, lo))), \
             mock.patch.object(cc, "gws_upload",
                               lambda fid, p, mime: self.uploads.append((fid, mime))):
            out = bf.process("deck1", latlon, self.tmpdir, tolerance, write)
        self.seen = seen
        return out

    def tearDown(self):
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_already_correct_deck_is_left_alone_even_with_write(self):
        status, drift = self.run_process(write=True)
        self.assertEqual(status, "ok")
        self.assertLess(drift, 1.0)
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_sub_pixel_drift_is_within_tolerance(self):
        """A dot placed from a slightly different geocode rounds to a few EMU —
        invisible, and re-uploading 80 decks for it would be pure churn."""
        self.current = (self.current[0] + 40, self.current[1] + 40)
        self.assertEqual(self.run_process(write=True)[0], "ok")
        self.assertEqual(self.uploads, [])

    def test_plan_run_never_uploads(self):
        self.current = (self.current[0] + 10 ** 6, self.current[1])
        status, drift = self.run_process(write=False)
        self.assertEqual(status, "would-move")
        self.assertGreater(drift, 1.0)
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_write_run_repositions_then_uploads_as_pptx(self):
        self.current = (self.current[0] + 10 ** 6, self.current[1])
        self.assertEqual(self.run_process(write=True)[0], "moved")
        self.assertEqual([(la, lo) for _p, la, lo in self.moves], [SF])
        self.assertEqual(self.uploads, [("deck1", cc.PPTX)])

    def test_unrecognised_slide5_is_reported_not_rewritten(self):
        self.current = None
        self.assertEqual(self.run_process(write=True), ("unreadable", None))
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_the_downloaded_deck_is_removed_afterwards(self):
        """Chapter decks are chapter data; they must not be left on disk."""
        self.run_process(write=True)
        self.assertFalse(os.path.exists(self.seen["path"]))

    def test_the_deck_is_removed_even_when_the_upload_raises(self):
        self.current = (self.current[0] + 10 ** 6, self.current[1])
        self.tmpdir = tempfile.mkdtemp()
        seen = {}

        def fake_download(_fid, out):
            with open(out, "wb") as f:
                f.write(b"deck")
            seen["path"] = out

        def boom(*_a, **_k):
            raise RuntimeError("gws failed")

        with mock.patch.object(cc, "gws_download", fake_download), \
             mock.patch.object(cc, "read_marker_offsets", lambda _p: (self.current, (0, 0))), \
             mock.patch.object(cc, "reposition_map_marker", lambda *a: None), \
             mock.patch.object(cc, "gws_upload", boom):
            with self.assertRaises(RuntimeError):
                bf.process("deck1", SF, self.tmpdir, 1.0, True)
        self.assertFalse(os.path.exists(seen["path"]))


class TestDefaultIsReadOnly(unittest.TestCase):
    def test_write_defaults_to_false(self):
        """Repo rule: a script that writes on its default invocation is a bug.

        Driven through a chapter that genuinely IS off target, so the run has
        something to write and the assertion means something."""
        off = cc.marker_offsets(*SF)[0]
        far = (off[0] + 10 ** 6, off[1])
        with mock.patch.object(sys, "argv", ["backfill_map_dots.py"]), \
             mock.patch.object(bf, "read_chapter_coords",
                               return_value=({"f1": ("San Francisco",) + SF}, [], {})), \
             mock.patch.object(bf, "chapter_folders",
                               return_value=[{"id": "f1", "name": "San Francisco",
                                              "mimeType": cc.FOLDER}]), \
             mock.patch.object(bf, "find_deck", return_value="deck1"), \
             mock.patch.object(cc, "gws_download",
                               lambda _fid, out: open(out, "wb").close()), \
             mock.patch.object(cc, "read_marker_offsets", lambda _p: (far, (0, 0))), \
             mock.patch.object(cc, "reposition_map_marker",
                               side_effect=AssertionError("rewrote a deck!")), \
             mock.patch.object(cc, "gws_upload", side_effect=AssertionError("wrote!")):
            bf.main()

    def test_lat_without_lon_aborts(self):
        with mock.patch.object(sys, "argv", ["x", "--city", "Tokyo", "--lat", "1"]):
            with self.assertRaises(SystemExit):
                bf.main()

    def test_coordinate_override_requires_a_city(self):
        with mock.patch.object(sys, "argv", ["x", "--lat", "1", "--lon", "2"]):
            with self.assertRaises(SystemExit):
                bf.main()


if __name__ == "__main__":
    unittest.main()
