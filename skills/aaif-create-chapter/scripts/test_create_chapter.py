"""Unit tests for the network-map marker placement in create_chapter.py.

Run: python3 skills/aaif-create-chapter/scripts/test_create_chapter.py
"""
import os
import re
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
import create_chapter as cc  # noqa: E402

A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'

# Slide-5 shape templates, mirroring the real deck. The DOT is the square
# 155448x155448 shape and carries an EMPTY <a:t></a:t> (so text presence can NOT
# distinguish it from the label). The LABEL is a wide text box whose <a:t> carries
# xml:space="preserve" the way rebrand_file leaves it. Discrimination is by ext.
DOT_SP = (
    '<p:sp><p:spPr><a:xfrm><a:off x="4074942" y="2650779"/>'
    '<a:ext cx="155448" cy="155448"/></a:xfrm>'
    '<a:solidFill><a:srgbClr val="14964A"/></a:solidFill></p:spPr>'
    '<p:txBody><a:p><a:r><a:t></a:t></a:r></a:p></p:txBody></p:sp>'
)
LABEL_SP = (
    '<p:sp><p:spPr><a:xfrm><a:off x="3649553" y="2875998"/>'
    '<a:ext cx="2000000" cy="300000"/></a:xfrm>'
    '<a:solidFill><a:srgbClr val="14964A"/></a:solidFill></p:spPr>'
    '<p:txBody><a:p><a:r>'
    '<a:t xml:space="preserve">SAN FRANCISCO · TONIGHT</a:t>'
    '</a:r></a:p></p:txBody></p:sp>'
)
# A non-green decorative shape that must never move.
OTHER_SP = (
    '<p:sp><p:spPr><a:xfrm><a:off x="111111" y="222222"/>'
    '<a:ext cx="500000" cy="500000"/></a:xfrm>'
    '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></p:spPr></p:sp>'
)


def green_sp(off, ext, text=None, off_selfclose="/>"):
    """A green shape with a chosen off/ext, optional text run, and control over
    the <a:off .../> self-close spacing (to exercise re-saved ' />' output)."""
    body = ("<p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody>" % text
            if text is not None else "")
    return ('<p:sp><p:spPr><a:xfrm><a:off x="%d" y="%d"%s'
            '<a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:solidFill><a:srgbClr val="14964A"/></a:solidFill></p:spPr>%s</p:sp>'
            % (off[0], off[1], off_selfclose, ext[0], ext[1], body))


def slide5(*shapes):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, "".join(shapes)))


def make_pptx(path, slide_xml=None, extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if slide_xml is not None:
            z.writestr(cc.SLIDE5, slide_xml)
        for member_name, data in (extra or {}).items():
            z.writestr(member_name, data)


def offsets_by_shape(path):
    """Return {'dot': (x, y), 'label': (x, y), 'other': (x, y)} from slide 5."""
    with zipfile.ZipFile(path) as z:
        xml = z.read(cc.SLIDE5).decode("utf-8")
    out = {}
    for block in re.findall(r"<p:sp\b[^>]*>.*?</p:sp>", xml, re.S):
        m = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"/>', block)
        xy = (int(m.group(1)), int(m.group(2)))
        if "14964A" not in block:
            out["other"] = xy
        elif cc.DOT_EXT_RE.search(block):
            out["dot"] = xy
        else:
            out["label"] = xy
    return out


class TestTransformText(unittest.TestCase):
    """transform_text() on filename-shaped strings — short, no surrounding
    sentence, unlike the prose paragraphs it's normally exercised against via
    _process_paragraphs. The bare-"SF" case heuristic looks ±30 chars around
    the match for a capitalized neighbor word, which behaves differently on a
    short filename than on a full paragraph."""

    def test_full_city_name_in_filename(self):
        self.assertEqual(
            cc.transform_text("San Francisco CRM.xlsx", "New York", "NEW YORK", "newyork"),
            "New York CRM.xlsx")

    def test_bare_sf_abbreviation_in_filename_title_case(self):
        # "SF" is the only capitalized neighbor in a short filename; the
        # heuristic must still resolve to title case (matching how "AAIF SF
        # Kickoff" reads in prose), not upper-case "NEW YORK".
        self.assertEqual(
            cc.transform_text("SF Kickoff Deck.pptx", "New York", "NEW YORK", "newyork"),
            "New York Kickoff Deck.pptx")

    def test_luma_slug_in_filename(self):
        self.assertEqual(
            cc.transform_text("aaif-sanfrancisco-banner.png", "New York", "NEW YORK", "newyork"),
            "aaif-newyork-banner.png")

    def test_filename_with_no_source_tokens_is_unchanged(self):
        self.assertEqual(
            cc.transform_text("About.docx", "New York", "NEW YORK", "newyork"),
            "About.docx")


class TestProjection(unittest.TestCase):
    def test_sf_calibration_lock(self):
        # Calibration lock for the fitted Gall Stereographic constants: the SF
        # dot offset under the 2026-07-30 coastline fit (mean residual 0.64 px).
        # NOTE this is ~9 px east of the template's hand-placed SF dot — the fit
        # wins. If this fails, the projection constants were edited; recalibrate
        # against the coastlines (see SKILL.md), don't just repin the value.
        dot_off, _ = cc.marker_offsets(37.77, -122.42)
        self.assertEqual(dot_off, (4119719, 2715465))

    def test_label_keeps_template_offset_from_dot(self):
        dot_off, label_off = cc.marker_offsets(40.71, -74.01)
        self.assertEqual(label_off[0] - dot_off[0], cc.LABEL_DX)
        self.assertEqual(label_off[1] - dot_off[1], cc.LABEL_DY)

    # (No literal re-pins of the fitted constants beyond the SF calibration
    # lock above: a second copy of the numbers just makes a legitimate refit
    # touch more magic literals, and invites the repin the lock forbids.)

    def test_lat2y_is_monotonic_north_up(self):
        # y decreases as latitude increases (row 0 is the top of the image).
        lats = [-55, -34, 0, 25, 37.77, 51.5, 64.8]
        ys = [cc.lat2y(lat) for lat in lats]
        self.assertEqual(ys, sorted(ys, reverse=True))

    def test_far_flung_cities_land_on_the_canvas(self):
        # Cities at the map's extremes (including the ones the old model needed
        # per-pixel overrides for) must all project inside the 1123x794 image.
        w, h = cc.MAP_PX
        for city, lat, lon in [("Seoul", 37.57, 126.98), ("Sydney", -33.87, 151.21),
                               ("Melbourne", -37.81, 144.96), ("Shanghai", 31.23, 121.47),
                               ("Reykjavik", 64.15, -21.94), ("Wellington", -41.29, 174.78)]:
            x, y = cc.project_city(lat, lon)
            self.assertTrue(0 <= x <= w, "%s x=%.1f out of 0..%d" % (city, x, w))
            self.assertTrue(0 <= y <= h, "%s y=%.1f out of 0..%d" % (city, y, h))


class TestReposition(unittest.TestCase):
    def test_moves_both_shapes_to_new_city(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, slide5(DOT_SP, LABEL_SP, OTHER_SP))
            moved = cc.reposition_map_marker(p, 40.71, -74.01)
            self.assertEqual(moved, 2)
            self.assertIsNone(zipfile.ZipFile(p).testzip())

            dot_off, label_off = cc.marker_offsets(40.71, -74.01)
            got = offsets_by_shape(p)
            self.assertEqual(got["dot"], dot_off)
            self.assertEqual(got["label"], label_off)      # label detected despite xml:space
            self.assertEqual(got["other"], (111111, 222222))  # untouched

    def test_absent_slide5_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, slide_xml=None)  # no slide 5 at all
            self.assertEqual(cc.reposition_map_marker(p, 40.71, -74.01), 0)

    def test_no_green_shapes_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, slide5(OTHER_SP))  # shapes present, none green
            self.assertEqual(cc.reposition_map_marker(p, 40.71, -74.01), 0)

    def test_guard_raises_when_not_exactly_two_green(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, slide5(DOT_SP, OTHER_SP))  # only 1 green shape
            with self.assertRaises(RuntimeError):
                cc.reposition_map_marker(p, 40.71, -74.01)

    def test_guard_raises_when_both_green_match_dot_ext(self):
        # Identity, not count: two square green shapes -> both classified as dot,
        # zero labels -> must raise even though moved == 2 (would otherwise stack
        # the markers silently).
        two_dots = slide5(green_sp((1, 2), (155448, 155448)),
                          green_sp((3, 4), (155448, 155448), text="X"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, two_dots)
            with self.assertRaises(RuntimeError):
                cc.reposition_map_marker(p, 40.71, -74.01)

    def test_guard_raises_when_neither_green_matches_dot_ext(self):
        # Both green shapes are wide -> both classified as label, zero dots -> raise.
        two_labels = slide5(green_sp((1, 2), (999999, 111111)),
                            green_sp((3, 4), (888888, 222222), text="X"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, two_labels)
            with self.assertRaises(RuntimeError):
                cc.reposition_map_marker(p, 40.71, -74.01)

    def test_off_regex_tolerates_respaced_selfclose(self):
        # A deck re-saved as '<a:off ... />' (space before />) must still move.
        respaced = slide5(green_sp((1, 2), (155448, 155448), off_selfclose=" />"),
                         green_sp((3, 4), (2011680, 201168), text="SF", off_selfclose=" />"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, respaced)
            self.assertEqual(cc.reposition_map_marker(p, 40.71, -74.01), 2)

    def test_rewrite_preserves_other_zip_members(self):
        # A non-slide5 binary member (like image18.png) must round-trip byte-identical.
        blob = bytes(range(256)) * 8
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Slides.pptx")
            make_pptx(p, slide5(DOT_SP, LABEL_SP),
                      extra={"ppt/media/image18.png": blob})
            cc.reposition_map_marker(p, 40.71, -74.01)
            with zipfile.ZipFile(p) as z:
                self.assertEqual(z.read("ppt/media/image18.png"), blob)
                self.assertIn("[Content_Types].xml", z.namelist())


class TestResolveLatlon(unittest.TestCase):
    def test_override_bypasses_geocoding(self):
        # Both values given -> returned verbatim, no network.
        self.assertEqual(cc.resolve_latlon("Anywhere", 12.34, 56.78), (12.34, 56.78))

    def test_lone_value_falls_through_to_geocode(self):
        calls = []
        orig = cc.geocode_city
        cc.geocode_city = lambda name, **kw: calls.append(name) or (1.0, 2.0)
        try:
            self.assertEqual(cc.resolve_latlon("Paris", 48.85, None), (1.0, 2.0))
            self.assertEqual(calls, ["Paris"])
        finally:
            cc.geocode_city = orig

    def test_ungeocodable_returns_none(self):
        orig = cc.geocode_city
        cc.geocode_city = lambda name, **kw: None
        try:
            self.assertIsNone(cc.resolve_latlon("Tatooine", None, None))
        finally:
            cc.geocode_city = orig


class _FakeResp:
    def __init__(self, body):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class TestGeocodeCity(unittest.TestCase):
    """geocode_city with urlopen stubbed — no network. Patches time.sleep so the
    retry path doesn't actually back off."""

    def _patch(self, urlopen):
        self._orig_open = cc.urllib.request.urlopen
        self._orig_sleep = cc.time.sleep
        cc.urllib.request.urlopen = urlopen
        cc.time.sleep = lambda *_a: None
        self.addCleanup(self._restore)

    def _restore(self):
        cc.urllib.request.urlopen = self._orig_open
        cc.time.sleep = self._orig_sleep

    def test_success_parses_lat_lon(self):
        self._patch(lambda req, timeout=0: _FakeResp('[{"lat":"40.71","lon":"-74.01"}]'))
        self.assertEqual(cc.geocode_city("New York"), (40.71, -74.01))

    def test_empty_result_is_none(self):
        self._patch(lambda req, timeout=0: _FakeResp("[]"))
        self.assertIsNone(cc.geocode_city("Nowhereville"))

    def test_network_error_retries_then_none(self):
        import urllib.error
        calls = []
        def boom(req, timeout=0):
            calls.append(1)
            raise urllib.error.URLError("unreachable")
        self._patch(boom)
        self.assertIsNone(cc.geocode_city("Paris", retries=3))
        self.assertEqual(len(calls), 3)  # retried all attempts

    def test_non_json_response_is_none_without_retry(self):
        calls = []
        def html(req, timeout=0):
            calls.append(1)
            return _FakeResp("<html>captcha</html>")
        self._patch(html)
        self.assertIsNone(cc.geocode_city("Paris", retries=3))
        self.assertEqual(len(calls), 1)  # deterministic -> not retried

    def test_missing_lat_lon_fields_is_none(self):
        self._patch(lambda req, timeout=0: _FakeResp('[{"name":"somewhere"}]'))
        self.assertIsNone(cc.geocode_city("Paris"))


class TestRebrandWorksheetInlineStrings(unittest.TestCase):
    """Regression test for the xl/worksheets/sheetN.xml branch of rebrand_part:
    cells can hold an inline string (<is><t>...</t></is>) instead of a
    sharedStrings.xml reference, e.g. the CRM's "Guide" sheet title — those
    were previously left untouched by the rebrand engine."""

    SHEET_XML = (
        '<worksheet><sheetData><row r="2">'
        '<c r="B2" t="inlineStr"><is><t>AAIF SF — Attendee CRM</t></is></c>'
        '</row></sheetData></worksheet>'
    )

    def test_inline_string_cell_is_rebranded(self):
        out = cc.rebrand_part("xl/worksheets/sheet2.xml", self.SHEET_XML.encode("utf-8"),
                              "New York", "NEW YORK", "newyork")
        text = out.decode("utf-8")
        self.assertIn("AAIF New York — Attendee CRM", text)
        self.assertNotIn(">AAIF SF", text)

    def test_rels_lookalike_is_handled_by_the_rels_branch_not_worksheets(self):
        # "sheet2.xml.rels" does NOT match the xl/worksheets/sheet\d+.xml$ regex
        # (it ends in ".rels"), so it's routed to the existing `.rels` elif
        # instead - which does its own (unrelated) Luma-slug substitution. This
        # fixture has no slug for that branch to touch, so the bytes are
        # unchanged, but via the .rels path, not because worksheets/*.rels is
        # inert - rebrand_part has no truly-inert branch for anything under
        # xl/worksheets/, so this only proves the two regexes don't collide.
        out = cc.rebrand_part("xl/worksheets/_rels/sheet2.xml.rels",
                              self.SHEET_XML.encode("utf-8"), "New York", "NEW YORK", "newyork")
        self.assertEqual(out, self.SHEET_XML.encode("utf-8"))

    def test_unrelated_part_type_is_left_untouched(self):
        # A genuinely unhandled OOXML part (falls through every elif to the
        # final `else: return data`) must come back byte-for-byte identical.
        out = cc.rebrand_part("xl/drawings/drawing1.xml",
                              self.SHEET_XML.encode("utf-8"), "New York", "NEW YORK", "newyork")
        self.assertEqual(out, self.SHEET_XML.encode("utf-8"))


def make_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)


class TestResidualTokens(unittest.TestCase):
    """City name / slug residuals are case-insensitive; the bare "SF"
    abbreviation is case-SENSITIVE — lowercase "sf" tokens in theme/font XML
    must not sys.exit a clean clone."""

    def check(self, content, expect_hit):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"xl/theme/theme1.xml": content})
            hits = cc.residual_tokens(p)
            if expect_hit:
                self.assertTrue(hits, "expected a residual hit for %r" % content)
            else:
                self.assertEqual(hits, [], "false positive for %r" % content)

    def test_city_name_is_case_insensitive(self):
        self.check(b"visit san francisco soon", True)
        self.check(b"SAN FRANCISCO tonight", True)

    def test_slug_is_case_insensitive(self):
        self.check(b"https://luma.com/aaif-SF", True)

    def test_uppercase_sf_hits(self):
        self.check(b"AAIF SF CHAPTER", True)

    def test_lowercase_sf_token_does_not_false_positive(self):
        self.check(b'<a:latin typeface="sf pro display"/>', False)

    def test_duplicate_city_pattern_removed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"xl/theme/theme1.xml": b"San Francisco SAN FRANCISCO"})
            # one hit for the (case-insensitive) city pattern, not two duplicates
            self.assertEqual(len(cc.residual_tokens(p)), 1)


class TestRewriteZipHardening(unittest.TestCase):
    def test_mid_loop_failure_cleans_temp_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"a.xml": b"one", "b.xml": b"two"})

            def boom(name, data):
                if name == "b.xml":
                    raise RuntimeError("transform failed")
                return data
            with self.assertRaises(RuntimeError):
                cc._rewrite_zip(p, boom)
            self.assertFalse(os.path.exists(p + ".new"))   # no leftover temp
            with zipfile.ZipFile(p) as z:                  # original intact
                self.assertEqual(z.read("a.xml"), b"one")


class FakeDrive:
    """In-memory Drive: {folder_id: [child dicts]}. Records creates/copies."""

    def __init__(self, folders):
        self.folders = {k: list(v) for k, v in folders.items()}
        self.created, self.copied, self.next = [], [], 0

    def list_children(self, fid):
        return list(self.folders.get(fid, []))

    def create_folder(self, name, parent):
        self.next += 1
        fid = "fld%d" % self.next
        self.folders.setdefault(parent, []).append(
            {"id": fid, "name": name, "mimeType": cc.FOLDER})
        self.folders[fid] = []
        self.created.append(name)
        return fid

    def copy_file(self, src, name, parent):
        self.next += 1
        fid = "cp%d" % self.next
        self.folders.setdefault(parent, []).append(
            {"id": fid, "name": name, "mimeType": "application/x-copied"})
        self.copied.append(name)
        return fid


def fake_download(_file_id, out):
    """Every downloaded Office file is a minimal xlsx whose sharedStrings carries
    the source city — the real rebrand engine then runs on it, offline."""
    make_zip(out, {"xl/sharedStrings.xml":
                   "<sst><si><t>San Francisco</t></si></sst>"})


CLONE_TEMPLATE = {
    "tpl": [
        {"id": "f1", "name": "San Francisco CRM.xlsx", "mimeType": "application/x"},
        {"id": "f2", "name": "About.txt", "mimeType": "application/x"},
        {"id": "sub", "name": "Event Template", "mimeType": cc.FOLDER},
    ],
    "sub": [
        {"id": "f3", "name": "notes.txt", "mimeType": "application/x"},
    ],
}


class TestCloneResume(unittest.TestCase):
    """clone_and_rebrand with the Drive layer faked — covers the --resume
    skip-by-name decision (network paths excluded by design)."""

    def run_clone(self, drive, existing_id=None):
        from unittest import mock
        ctx = {"name": "New York", "upper": "NEW YORK", "slug": "newyork",
               "residuals": [], "latlon": None}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(cc, "list_children", drive.list_children), \
                mock.patch.object(cc, "create_folder", drive.create_folder), \
                mock.patch.object(cc, "copy_file", drive.copy_file), \
                mock.patch.object(cc, "gws_download", fake_download), \
                mock.patch.object(cc, "gws_upload", lambda *a, **k: None):
            ctx["tmp"] = d
            return cc.clone_and_rebrand("tpl", "parent", "New York", ctx,
                                        existing_id=existing_id), ctx

    def test_fresh_clone_renames_children(self):
        drive = FakeDrive(CLONE_TEMPLATE)
        self.run_clone(drive)
        self.assertEqual(sorted(drive.copied),
                         ["About.txt", "New York CRM.xlsx", "notes.txt"])
        self.assertEqual(sorted(drive.created), ["Event Template", "New York"])

    def test_resume_skips_existing_and_clones_missing(self):
        drive = FakeDrive(dict(CLONE_TEMPLATE, **{
            "ex": [{"id": "e1", "name": "New York CRM.xlsx",
                    "mimeType": "application/x-copied"}],
        }))
        self.run_clone(drive, existing_id="ex")
        self.assertEqual(sorted(drive.copied), ["About.txt", "notes.txt"])
        self.assertEqual(drive.created, ["Event Template"])

    def test_resume_into_fully_cloned_folder_is_a_noop(self):
        drive = FakeDrive(dict(CLONE_TEMPLATE, **{
            "ex": [
                {"id": "e1", "name": "New York CRM.xlsx", "mimeType": "application/x-copied"},
                {"id": "e2", "name": "About.txt", "mimeType": "application/x-copied"},
                {"id": "esub", "name": "Event Template", "mimeType": cc.FOLDER},
            ],
            "esub": [{"id": "e3", "name": "notes.txt", "mimeType": "application/x-copied"}],
        }))
        new_id, _ = self.run_clone(drive, existing_id="ex")
        self.assertEqual(new_id, "ex")
        self.assertEqual(drive.copied, [])
        self.assertEqual(drive.created, [])

    def test_resume_recurses_into_partial_subfolder(self):
        drive = FakeDrive(dict(CLONE_TEMPLATE, **{
            "ex": [
                {"id": "e1", "name": "New York CRM.xlsx", "mimeType": "application/x-copied"},
                {"id": "e2", "name": "About.txt", "mimeType": "application/x-copied"},
                {"id": "esub", "name": "Event Template", "mimeType": cc.FOLDER},
            ],
            "esub": [],
        }))
        self.run_clone(drive, existing_id="ex")
        self.assertEqual(drive.copied, ["notes.txt"])
        self.assertEqual(drive.created, [])


if __name__ == "__main__":
    unittest.main()
