"""Unit tests for the map-dot backfill (backfill_map_dots.py).

The projection itself is tested in test_create_chapter.py — the backfill imports
it rather than reimplementing it. What is tested here is everything that decides
*which* dot moves *where*: parsing the sheet, joining it to Drive, and the
already-correct/would-move/moved decision that keeps a re-run a no-op.

Four invariants carry the safety of this script, and each is pinned by a named
test rather than by a comment:

  (a) it never writes on a default invocation   -> TestDefaultIsReadOnly
  (b) it never moves a dot on a guessed coord   -> TestMainSkipBranches
  (c) a re-run is a no-op                       -> TestReRunIsANoOp
  (d) a non-template deck is skipped, not moved -> TestProcess + test_create_chapter

Nothing here may touch the network. TestNoNetwork guards that globally: a
regression in an early argument check used to let a "unit" test list the live
Chapters folder and download a production deck.

Run: python3 skills/aaif-create-chapter/scripts/test_backfill_map_dots.py
"""
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import backfill_map_dots as bf  # noqa: E402
import create_chapter as cc  # noqa: E402
from test_create_chapter import DOT_SP, LABEL_SP, make_pptx, slide5  # noqa: E402

SF = (37.7749, -122.4194)
URL = "https://drive.google.com/drive/folders/%s"


def sheet(*rows, headers=None):
    """A fake gws_json values.get response for the Chapters tab."""
    headers = headers or [bf.COL_CITY, bf.COL_FOLDER, bf.COL_GEO]
    return {"values": [headers] + [list(r) for r in rows]}


def row(city, fid, geo):
    """One Chapters-tab row: city, folder link, geolocation cell."""
    return (city, URL % fid if fid else "", geo)


def folder(fid, name):
    return {"id": fid, "name": name, "mimeType": cc.FOLDER}


def stub_download(_fid, out):
    """A minimal but REAL zip: process() rejects an empty or non-pptx download,
    so a stub that writes nothing would be caught by that check rather than
    reaching the code under test."""
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("x", "y")


def quiet(fn, *a, **kw):
    """Run fn, swallowing its report. main() prints a full chapter table."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


class TestNoNetwork(unittest.TestCase):
    """Nothing in this suite may reach Drive or Sheets.

    `test_lat_without_lon_aborts` and its sibling once mocked only sys.argv.
    They passed because main() exits early — but with that guard removed the
    suite listed the real Chapters folder and downloaded a production deck.
    A unit suite that CI runs on every PR must not be one regression away from
    touching the estate, so the network is denied for the whole class and the
    aborts assert their reason rather than merely that *something* exited."""

    def setUp(self):
        for name in ("gws_json", "gws_download", "gws_upload", "list_children"):
            p = mock.patch.object(cc, name,
                                  side_effect=AssertionError("touched the network"))
            p.start()
            self.addCleanup(p.stop)

    def test_lat_without_lon_aborts_before_any_io(self):
        with mock.patch.object(sys, "argv", ["x", "--city", "Tokyo", "--lat", "1"]):
            with self.assertRaises(SystemExit) as cm:
                bf.main()
        self.assertIn("--lat and --lon", str(cm.exception))

    def test_coordinate_override_requires_a_city(self):
        with mock.patch.object(sys, "argv", ["x", "--lat", "1", "--lon", "2"]):
            with self.assertRaises(SystemExit) as cm:
                bf.main()
        self.assertIn("--city", str(cm.exception))

    def test_negative_tolerance_aborts(self):
        """Every deck would read as off target and be rewritten."""
        with mock.patch.object(sys, "argv", ["x", "--tolerance", "-1"]):
            with self.assertRaises(SystemExit) as cm:
                bf.main()
        self.assertIn("negative", str(cm.exception))


class TestParseGeo(unittest.TestCase):
    """The `Generated Geolocation` cell is hand-editable free text on a sheet
    other people maintain — a value that isn't a coordinate pair has to read as
    "no coordinate", never as a coordinate that happens to parse."""

    def geo(self, cell):
        latlon, why = bf.parse_geo(cell)
        return latlon

    def test_parses_a_plain_pair(self):
        self.assertEqual(bf.parse_geo("37.7749, -122.4194"), (SF, None))

    def test_tolerates_surrounding_and_inner_whitespace(self):
        self.assertEqual(self.geo("  37.7749 ,-122.4194  "), SF)

    def test_parses_integers_and_negative_latitude(self):
        self.assertEqual(self.geo("-34, 151"), (-34.0, 151.0))

    def test_none_for_blank_and_missing(self):
        for cell in ("", "   ", None):
            latlon, why = bf.parse_geo(cell)
            self.assertIsNone(latlon, cell)
            self.assertIn("blank", why)

    def test_none_for_prose(self):
        for cell in ("TBD", "see notes", "37.7749", "37.7749, -122.4194, 0"):
            latlon, why = bf.parse_geo(cell)
            self.assertIsNone(latlon, cell)
            self.assertIn("not a coordinate pair", why)

    def test_none_when_out_of_range(self):
        """A swapped pair for a high-latitude city (e.g. "24.9384, 60.1699"
        written the wrong way round) is in range and can't be caught here, but a
        genuinely impossible value must not become a dot."""
        for cell in ("91, 0", "-91, 0", "0, 181", "0, -181"):
            self.assertIsNone(self.geo(cell), cell)

    def test_an_out_of_range_latitude_names_transposition(self):
        """The likeliest cause, and the one the sheet's owner can act on —
        distinct from "the cell is blank", which needs a different fix."""
        latlon, why = bf.parse_geo("121.4737, 31.2304")
        self.assertIsNone(latlon)
        self.assertIn("transposed", why)

    def test_null_island_is_rejected(self):
        """0, 0 is in range and parses cleanly, but it is overwhelmingly a
        failed geocode rather than a chapter in the Atlantic."""
        latlon, why = bf.parse_geo("0, 0")
        self.assertIsNone(latlon)
        self.assertIn("failed geocode", why)


class TestFolderIdFromUrl(unittest.TestCase):
    def test_extracts_the_id(self):
        self.assertEqual(bf.folder_id_from_url(URL % "1AbC-_9"), "1AbC-_9")

    def test_extracts_with_a_query_string(self):
        self.assertEqual(bf.folder_id_from_url(URL % "1AbC" + "?usp=sharing"), "1AbC")

    def test_none_for_blank_or_non_folder_link(self):
        for cell in ("", None, "https://drive.google.com/file/d/1AbC/view", "n/a"):
            self.assertIsNone(bf.folder_id_from_url(cell), cell)


class TestDriftPx(unittest.TestCase):
    def test_the_emu_per_pixel_scale_is_what_we_think(self):
        """Asserted against an independent literal, not derived from the module.

        Every other test here builds its inputs by multiplying EMU_PER_PX_*, so
        a wrong scale would cancel out and pass. It would not cancel out in
        production: doubling it silently doubles the effective --tolerance, and
        genuinely misplaced dots start reporting "already correct"."""
        self.assertAlmostEqual(bf.EMU_PER_PX_X, 4804.06, places=1)
        self.assertAlmostEqual(bf.EMU_PER_PX_Y, 4804.06, places=1)

    def test_zero_when_identical(self):
        self.assertEqual(bf.drift_px((100, 200), (100, 200)), 0.0)

    def test_a_literal_emu_step_reads_as_the_expected_pixels(self):
        """A fixed EMU offset, not one computed from the constant under test."""
        self.assertAlmostEqual(bf.drift_px((48041, 0), (0, 0)), 10.0, places=1)

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
        s = self.read(sheet(row("San Francisco", "fid1", "37.7749, -122.4194"),
                            row("Tokyo", "fid2", "35.6762, 139.6503")))
        self.assertEqual(s.bad_rows, [])
        self.assertEqual(s.by_folder["fid1"].city, "San Francisco")
        self.assertEqual(s.by_folder["fid1"].latlon, SF)
        self.assertEqual(s.by_folder["fid2"].latlon, (35.6762, 139.6503))

    def test_columns_are_found_by_header_name_not_position(self):
        """The tab is a website feed and has been restructured before; a column
        reorder must move the reads with it."""
        s = self.read(sheet(
            ("37.7749, -122.4194", "San Francisco", "ignored", URL % "fid1"),
            headers=[bf.COL_GEO, bf.COL_CITY, "Summary", bf.COL_FOLDER]))
        self.assertEqual(s.by_folder["fid1"].latlon, SF)

    def test_row_without_a_folder_link_is_reported_not_guessed(self):
        s = self.read(sheet(row("Lagos", None, "6.5244, 3.3792")))
        self.assertEqual(s.by_folder, {})
        self.assertEqual([(r.city, r.why) for r in s.bad_rows],
                         [("Lagos", "no %s link" % bf.COL_FOLDER)])
        self.assertEqual(s.unusable, {},
                         "no folder id means no folder can be attributed")

    def test_row_without_usable_coordinates_is_keyed_by_its_folder(self):
        """This is the whole reason `unusable` exists: without it the sweep
        reports "no row links to this folder" about a row sitting right there."""
        s = self.read(sheet(row("Lagos", "fid1", "TBD")))
        self.assertEqual(s.by_folder, {})
        self.assertEqual(s.unusable["fid1"].city, "Lagos")
        self.assertIn("not a coordinate pair", s.unusable["fid1"].why)
        self.assertEqual([r.city for r in s.bad_rows], ["Lagos"])

    def test_duplicate_folder_links_are_reported_not_silently_resolved(self):
        """Two rows claiming one folder would otherwise place the dot for
        whichever sorts later, with nothing in the output saying so."""
        s = self.read(sheet(row("Scotland", "fid1", "55.9533, -3.1883"),
                            row("Edinburgh", "fid1", "51.5074, -0.1278")))
        self.assertEqual(s.by_folder["fid1"].city, "Scotland", "first row wins")
        dupes = [r for r in s.bad_rows if "duplicate" in r.why]
        self.assertEqual([r.city for r in dupes], ["Edinburgh"])

    def test_a_usable_row_beats_an_unusable_one_for_the_same_folder(self):
        s = self.read(sheet(row("Tokyo", "fid1", "TBD"),
                            row("Tokyo", "fid1", "35.6762, 139.6503")))
        self.assertEqual(s.by_folder["fid1"].latlon, (35.6762, 139.6503))
        self.assertNotIn("fid1", s.unusable)

    def test_blank_city_rows_are_ignored_silently(self):
        """Trailing blank rows are normal on a sheet — they are not findings."""
        s = self.read(sheet(("", "", ""), ("  ", "", "")))
        self.assertEqual((s.by_folder, s.bad_rows, s.unusable), ({}, [], {}))

    def test_short_rows_do_not_raise(self):
        """Sheets truncates trailing empty cells, so a row can be shorter than
        the header."""
        s = self.read(sheet(("Lagos",)))
        self.assertEqual(s.by_folder, {})
        self.assertEqual([r.why for r in s.bad_rows],
                         ["no %s link" % bf.COL_FOLDER])

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
    KIDS = [folder("f1", "Tokyo"), folder("f2", "Scotland"),
            folder("f3", "TemplateCity"),
            {"id": "f4", "name": "Intro to AAIF", "mimeType": cc.PPTX}]

    def sheet_for(self):
        return bf.Sheet(
            by_folder={"f2": bf.Row("Edinburgh", "f2", (55.95, -3.19), None)},
            bad_rows=[], unusable={})

    def folders(self, only_city=None, kids=None):
        with mock.patch.object(cc, "list_children",
                               return_value=self.KIDS if kids is None else kids):
            out, _ = quiet(bf.chapter_folders, self.sheet_for(), only_city)
            return out

    def test_skips_the_template_and_non_folders(self):
        self.assertEqual([f["name"] for f in self.folders()], ["Scotland", "Tokyo"])

    def test_city_filter_selects_by_folder_name(self):
        self.assertEqual([f["id"] for f in self.folders("Tokyo")], ["f1"])

    def test_city_filter_selects_by_the_sheets_city(self):
        """A folder's name can lag its chapter's city. Matching only the folder
        name would make `--city Edinburgh` abort on the folder still called
        "Scotland" — the exact mismatch the sheet join exists to bridge."""
        self.assertEqual([f["id"] for f in self.folders("Edinburgh")], ["f2"])

    def test_city_filter_is_case_insensitive(self):
        self.assertEqual([f["id"] for f in self.folders("tokyo")], ["f1"])

    def test_city_filter_aborts_on_an_unknown_name(self):
        with self.assertRaises(SystemExit):
            self.folders("Atlantis")

    def test_city_filter_cannot_select_the_template(self):
        with self.assertRaises(SystemExit):
            self.folders("TemplateCity")

    def test_warns_when_the_template_folder_is_absent(self):
        """A renamed template is no longer protected by name, and rewriting it
        would redefine LABEL_DX/LABEL_DY for every future chapter."""
        kids = [folder("f1", "Tokyo")]
        with mock.patch.object(cc, "list_children", return_value=kids):
            _out, printed = quiet(bf.chapter_folders, self.sheet_for(), None)
        self.assertIn("WARNING", printed)
        self.assertIn("TemplateCity", printed)


class TestFindDeck(unittest.TestCase):
    def find(self, tree):
        with mock.patch.object(cc, "list_children", side_effect=lambda fid: tree[fid]):
            return bf.find_deck("chapter")

    def test_finds_the_deck_one_level_down(self):
        self.assertEqual(self.find({
            "chapter": [{"id": "sub", "name": "Event Templates (Copy for Each Event)",
                         "mimeType": cc.FOLDER}],
            "sub": [{"id": "deck", "name": "Slides.pptx", "mimeType": cc.PPTX}],
        }), ("deck", None))

    def test_finds_the_deck_at_the_top_level(self):
        self.assertEqual(self.find({
            "chapter": [{"id": "deck", "name": "Slides.pptx", "mimeType": cc.PPTX}],
        }), ("deck", None))

    def test_none_when_the_deck_was_never_cloned(self):
        deck, why = self.find({
            "chapter": [{"id": "sub", "name": "Banners", "mimeType": cc.FOLDER}],
            "sub": [{"id": "x", "name": "Square Logo.pptx", "mimeType": cc.PPTX}],
        })
        self.assertIsNone(deck)
        self.assertIn("no Slides.pptx", why)

    def test_ignores_a_google_slides_file_of_the_same_name(self):
        """Only a stored .pptx can be byte-rewritten; a native Slides file of the
        same name is a different thing and must not be treated as the deck."""
        self.assertIsNone(self.find({
            "chapter": [{"id": "g", "name": "Slides.pptx",
                         "mimeType": "application/vnd.google-apps.presentation"}],
        })[0])

    def test_two_copies_are_reported_not_arbitrarily_picked(self):
        """Rewriting one of two decks and reporting success would leave the copy
        people actually present from untouched, with nothing hinting it exists."""
        deck, why = self.find({
            "chapter": [{"id": "d1", "name": "Slides.pptx", "mimeType": cc.PPTX},
                        {"id": "sub", "name": "Event Templates", "mimeType": cc.FOLDER}],
            "sub": [{"id": "d2", "name": "Slides.pptx", "mimeType": cc.PPTX}],
        })
        self.assertIsNone(deck)
        self.assertIn("2 copies", why)


class TestProcess(unittest.TestCase):
    """The move/skip decision, with Drive and the OOXML surgery stubbed out."""

    def setUp(self):
        self.uploads, self.moves = [], []
        dot, label = cc.marker_offsets(*SF)
        self.current = (dot, label)          # default: already correct
        self.tmpdir = None

    def run_process(self, latlon=SF, write=False, tolerance=1.0):
        self.tmpdir = tempfile.mkdtemp()
        seen = {}

        def fake_download(_fid, out):
            with zipfile.ZipFile(out, "w") as z:
                z.writestr("x", "y")         # a real zip: process() checks
            seen["path"] = out

        reader = ((self.current, None) if self.current
                  else (None, "slide 5 has no green marker shapes"))
        with mock.patch.object(cc, "gws_download", fake_download), \
             mock.patch.object(cc, "read_marker_offsets", lambda _p: reader), \
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

    def nudge_dot(self, dx):
        dot, label = self.current
        self.current = ((dot[0] + dx, dot[1]), label)

    def nudge_label(self, dx):
        dot, label = self.current
        self.current = (dot, (label[0] + dx, label[1]))

    def test_already_correct_deck_is_left_alone_even_with_write(self):
        status, drift = self.run_process(write=True)
        self.assertEqual(status, "ok")
        self.assertLess(drift, 1.0)
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_sub_pixel_drift_is_within_tolerance(self):
        """A dot placed from a slightly different geocode rounds to a few EMU —
        invisible, and re-uploading 80 decks for it would be pure churn."""
        self.nudge_dot(40)
        self.assertEqual(self.run_process(write=True)[0], "ok")
        self.assertEqual(self.uploads, [])

    def test_just_inside_the_tolerance_is_left_alone(self):
        self.nudge_dot(round(0.9 * bf.EMU_PER_PX_X))
        self.assertEqual(self.run_process(write=True)[0], "ok")
        self.assertEqual(self.uploads, [])

    def test_just_outside_the_tolerance_is_moved(self):
        """The 1 px boundary is the whole no-op guarantee; pin both sides."""
        self.nudge_dot(round(1.1 * bf.EMU_PER_PX_X))
        self.assertEqual(self.run_process(write=True)[0], "moved")
        self.assertEqual(self.uploads, [("deck1", cc.PPTX)])

    def test_a_dragged_label_alone_is_detected_and_repaired(self):
        """read_marker_offsets returns both offsets and reposition rewrites both,
        so gating on the dot would leave a hand-dragged label uncorrectable —
        and would make a refit of LABEL_DX/LABEL_DY a silent estate-wide no-op."""
        self.nudge_label(10 ** 6)
        status, drift = self.run_process(write=True)
        self.assertEqual(status, "moved")
        self.assertGreater(drift, 1.0)
        self.assertEqual(self.uploads, [("deck1", cc.PPTX)])

    def test_plan_run_never_uploads(self):
        self.nudge_dot(10 ** 6)
        status, drift = self.run_process(write=False)
        self.assertEqual(status, "would-move")
        self.assertGreater(drift, 1.0)
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_write_run_repositions_then_uploads_as_pptx(self):
        self.nudge_dot(10 ** 6)
        self.assertEqual(self.run_process(write=True)[0], "moved")
        self.assertEqual([(la, lo) for _p, la, lo in self.moves], [SF])
        self.assertEqual(self.uploads, [("deck1", cc.PPTX)])

    def test_unrecognised_slide5_is_reported_not_rewritten(self):
        self.current = None
        status, why = self.run_process(write=True)
        self.assertIsNone(status)
        self.assertIn("no green", why)
        self.assertEqual((self.moves, self.uploads), ([], []))

    def test_a_download_that_wrote_nothing_raises_naming_the_download(self):
        """gws_download ignores stdout, so an error body at exit 0 would
        otherwise surface as a BadZipFile blaming the OOXML code."""
        self.tmpdir = tempfile.mkdtemp()
        with mock.patch.object(cc, "gws_download", lambda _f, out: None):
            with self.assertRaises(RuntimeError) as cm:
                bf.process("deck1", SF, self.tmpdir, 1.0, False)
        self.assertIn("wrote no file", str(cm.exception))

    def test_a_download_that_is_not_a_pptx_raises_naming_the_download(self):
        self.tmpdir = tempfile.mkdtemp()

        def html_body(_fid, out):
            with open(out, "wb") as f:
                f.write(b"<html>error</html>")

        with mock.patch.object(cc, "gws_download", html_body):
            with self.assertRaises(RuntimeError) as cm:
                bf.process("deck1", SF, self.tmpdir, 1.0, False)
        self.assertIn("not a .pptx", str(cm.exception))

    def test_the_downloaded_deck_is_removed_afterwards(self):
        """Chapter decks are chapter data; they must not be left on disk."""
        self.run_process(write=True)
        self.assertFalse(os.path.exists(self.seen["path"]))

    def test_the_deck_is_removed_even_when_the_upload_raises(self):
        self.nudge_dot(10 ** 6)
        self.tmpdir = tempfile.mkdtemp()
        seen = {}

        def fake_download(_fid, out):
            with zipfile.ZipFile(out, "w") as z:
                z.writestr("x", "y")
            seen["path"] = out

        def boom(*_a, **_k):
            raise RuntimeError("gws failed")

        with mock.patch.object(cc, "gws_download", fake_download), \
             mock.patch.object(cc, "read_marker_offsets",
                               lambda _p: (self.current, None)), \
             mock.patch.object(cc, "reposition_map_marker", lambda *a: None), \
             mock.patch.object(cc, "gws_upload", boom):
            with self.assertRaises(RuntimeError):
                bf.process("deck1", SF, self.tmpdir, 1.0, True)
        self.assertFalse(os.path.exists(seen["path"]))


class TestReRunIsANoOp(unittest.TestCase):
    """Invariant (c), end to end on a real .pptx.

    TestProcess stubs read_marker_offsets AND reposition_map_marker, so the seam
    between them is never exercised inside process(). This drives the real pair
    through a real fixture deck: after one --write pass, a second pass must
    report "ok" and upload nothing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.deck = os.path.join(self.tmpdir, "fixture.pptx")
        make_pptx(self.deck, slide5(DOT_SP, LABEL_SP))
        self.uploads = []

    def run_once(self, latlon):
        def fake_download(_fid, out):
            shutil.copyfile(self.deck, out)

        def fake_upload(fid, path, mime):
            shutil.copyfile(path, self.deck)      # persist, like Drive would
            self.uploads.append(fid)

        with mock.patch.object(cc, "gws_download", fake_download), \
             mock.patch.object(cc, "gws_upload", fake_upload):
            return bf.process("deck1", latlon, self.tmpdir, 1.0, True)

    def test_second_pass_moves_nothing(self):
        tokyo = (35.6762, 139.6503)
        first, drift = self.run_once(tokyo)
        self.assertEqual(first, "moved")
        self.assertGreater(drift, 1.0)
        self.assertEqual(self.uploads, ["deck1"])

        second, drift2 = self.run_once(tokyo)
        self.assertEqual(second, "ok", "a re-run must be a no-op")
        self.assertLessEqual(drift2, 1.0)
        self.assertEqual(self.uploads, ["deck1"], "nothing re-uploaded")


class TestMainSkipBranches(unittest.TestCase):
    """Invariant (b): a folder the sheet cannot place is skipped, never guessed.

    Replacing either branch with a fallback coordinate used to pass the whole
    suite — the promise lived only in a docstring. These drive main() and assert
    that the write path is never reached."""

    def drive(self, sheet_obj, argv=("backfill_map_dots.py",)):
        never = mock.patch.object(
            cc, "reposition_map_marker",
            side_effect=AssertionError("moved a dot without a sheet coordinate"))
        never_up = mock.patch.object(
            cc, "gws_upload", side_effect=AssertionError("uploaded a deck"))
        with mock.patch.object(sys, "argv", list(argv)), \
             mock.patch.object(bf, "read_chapter_coords", return_value=sheet_obj), \
             mock.patch.object(bf, "chapter_folders",
                               return_value=[folder("f1", "Tokyo")]), \
             mock.patch.object(bf, "find_deck", return_value=("deck1", None)), \
             mock.patch.object(cc, "gws_download", stub_download), \
             never, never_up:
            return quiet(bf.main)

    def test_a_folder_with_no_sheet_row_is_skipped(self):
        _out, printed = self.drive(bf.Sheet({}, [], {}))
        self.assertIn("no sheet row", printed)

    def test_a_folder_whose_row_has_no_coordinates_is_skipped(self):
        """And is named for what it is — not reported as having no row at all,
        which sends the reader looking for a row that is sitting right there."""
        bad = bf.Row("Tokyo", "f1", None, "%s is blank" % bf.COL_GEO)
        _out, printed = self.drive(bf.Sheet({}, [bad], {"f1": bad}))
        self.assertIn("is blank", printed)
        self.assertNotIn("no sheet row", printed)


class TestDefaultIsReadOnly(unittest.TestCase):
    OFF = None      # set in setUp: a dot genuinely far from target

    def setUp(self):
        dot, label = cc.marker_offsets(*SF)
        self.OFF = ((dot[0] + 10 ** 6, dot[1]), label)
        self.sheet = bf.Sheet({"f1": bf.Row("San Francisco", "f1", SF, None)},
                              [], {})

    def drive(self, argv, on_upload):
        with mock.patch.object(sys, "argv", list(argv)), \
             mock.patch.object(bf, "read_chapter_coords", return_value=self.sheet), \
             mock.patch.object(bf, "chapter_folders",
                               return_value=[folder("f1", "San Francisco")]), \
             mock.patch.object(bf, "find_deck", return_value=("deck1", None)), \
             mock.patch.object(cc, "gws_download", stub_download), \
             mock.patch.object(cc, "read_marker_offsets", lambda _p: (self.OFF, None)), \
             mock.patch.object(cc, "reposition_map_marker", lambda *a: None), \
             mock.patch.object(cc, "gws_upload", on_upload):
            return quiet(bf.main)

    def test_write_defaults_to_false(self):
        """Repo rule: a script that writes on its default invocation is a bug.

        Driven through a chapter that genuinely IS off target, so the run has
        something to write and the assertion means something."""
        boom = mock.Mock(side_effect=AssertionError("wrote without --write!"))
        _out, printed = self.drive(["backfill_map_dots.py"], boom)
        self.assertIn("WOULD move", printed)

    def test_write_actually_writes(self):
        """The mirror of the above. Without it, hardcoding the flag to False
        makes --write a silent estate-wide no-op that the summary still reports
        as a successful backfill — and the whole suite still passes."""
        uploads = []
        _out, printed = self.drive(["backfill_map_dots.py", "--write"],
                                   lambda fid, p, mime: uploads.append(fid))
        self.assertEqual(uploads, ["deck1"])
        self.assertIn("moved", printed)


class TestExitStatus(unittest.TestCase):
    """A run that could not evaluate part of the estate must not exit 0 — a
    cron, a wrapper, or a glance at $? would read it as a finished backfill."""

    def drive(self, reader):
        sheet_obj = bf.Sheet({"f1": bf.Row("Tokyo", "f1", SF, None)}, [], {})
        with mock.patch.object(sys, "argv", ["backfill_map_dots.py"]), \
             mock.patch.object(bf, "read_chapter_coords", return_value=sheet_obj), \
             mock.patch.object(bf, "chapter_folders",
                               return_value=[folder("f1", "Tokyo")]), \
             mock.patch.object(bf, "find_deck", return_value=("deck1", None)), \
             mock.patch.object(cc, "gws_download", stub_download), \
             mock.patch.object(cc, "read_marker_offsets", reader):
            return quiet(bf.main)

    def test_an_unreadable_deck_exits_non_zero(self):
        with self.assertRaises(SystemExit) as cm:
            self.drive(lambda _p: (None, "slide 5 has TWO green dots"))
        self.assertIn("NOT backfilled", str(cm.exception))

    def test_a_clean_run_exits_zero(self):
        dot, label = cc.marker_offsets(*SF)
        _out, printed = self.drive(lambda _p: ((dot, label), None))
        self.assertIn("already correct", printed)


class TestMarkerFill(unittest.TestCase):
    """The marker fill is shared state across three places — this module, the
    chapter cloner, and ooxml_style's colour rule. If they disagree, the dot
    silently stops being findable and every backfill reports "no marker shapes"
    on a deck that plainly has one."""

    def test_the_marker_fill_is_the_design_systems_spectrum_teal(self):
        tokens = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "design", "aaif-tokens.css")
        with open(tokens, encoding="utf-8") as fh:
            css = fh.read()
        m = re.search(r"--spec-3:\s*#([0-9A-Fa-f]{6})", css)
        self.assertIsNotNone(m, "design system no longer defines --spec-3")
        self.assertEqual(cc.GREEN, m.group(1).upper())

    def test_the_legacy_fill_is_still_recognised(self):
        """The estate is restyled by a separate sweep, so between runs a deck may
        carry either fill. Both must be found; only the new one is written."""
        for fill in cc.MARKER_FILLS:
            self.assertTrue(cc.is_marker('<a:srgbClr val="%s"/>' % fill), fill)
        self.assertFalse(cc.is_marker('<a:srgbClr val="ABCDEF"/>'))


if __name__ == "__main__":
    unittest.main()
