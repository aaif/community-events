#!/usr/bin/env python3
"""Unit tests for rendering each chapter's Web Banner from its Square Logo deck.

The properties pinned here are the ones that fail quietly:

  (a) **a slide that does not say exactly the chapter's name is never
      rendered** — another city, a qualified spelling, a leftover template city
      beside the new one. Rendering it would publish the wrong name with a
      fresh date on it.
  (b) **the slide checked is the slide rendered**: the fourth in presentation
      order, not the `slide4.xml` part.
  (c) **a banner stamped with this deck AND this name is skipped**, and a moved
      deck or a new name is not — otherwise a re-run re-renders the estate, or
      a rename never reaches the banner.
  (d) **the upload and the stamp land on the banner, never the deck**, and the
      stamp only after the upload.
  (e) **a chapter with no banner gets one created exactly once** (NO_RETRY),
      and only when asked; a sweep never adds one.
  (f) `main()`'s exit codes: 1 on any failure, 2 for report-only with work, 0
      otherwise; ABSENT is not a failure, an unknown outcome is.

Nothing touches Drive: `create_chapter`'s Drive calls and the renderer are
replaced with a fake folder tree.

Run: python3 skills/aaif-create-chapter/scripts/test_render_web_banner.py
"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_web_banner as rwb   # noqa: E402
import create_chapter as cc       # noqa: E402
from aaif_events import gws as gwsmod  # noqa: E402

BANNERS = "Banners (Chapter Specific, Changed Rarely)"


def _pptx(fourth_runs, order=(1, 2, 3, 4, 5), part_runs=None):
    """Bytes of a minimal .pptx. `order` lists slide part numbers in
    presentation order; the part shown FOURTH gets `fourth_runs`, unless
    `part_runs` maps part numbers to runs explicitly."""
    if part_runs is None:
        part_runs = {order[3]: fourth_runs}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        ids = "".join('<p:sldId id="%d" r:id="rId%d"/>' % (255 + n, n) for n in order)
        z.writestr("ppt/presentation.xml",
                   "<p:presentation><p:sldIdLst>%s</p:sldIdLst></p:presentation>" % ids)
        rels = "".join('<Relationship Id="rId%d" Type="x/slide" Target="slides/slide%d.xml"/>'
                       % (n, n) for n in order)
        z.writestr("ppt/_rels/presentation.xml.rels", "<Relationships>%s</Relationships>" % rels)
        for n in order:
            runs = part_runs.get(n, ["AAIF"])
            body = "".join('<a:r><a:t xml:space="preserve">%s</a:t></a:r>' % r
                           if i % 2 else "<a:r><a:t>%s</a:t></a:r>" % r
                           for i, r in enumerate(runs))
            z.writestr("ppt/slides/slide%d.xml" % n, "<p:sld>%s</p:sld>" % body)
    return buf.getvalue()


def _path(data):
    fh = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
    fh.write(data)
    fh.close()
    return fh.name


class FakeDrive:
    """One chapter folder, `boston`, with a Banners folder and a deck."""

    def __init__(self, runs, stamp=None, banner=True, md5="m1", banners=1):
        self.deck = _pptx(runs)
        self.md5 = md5
        self.stamp = stamp
        self.banners = banners if banner else 0
        self.calls = []           # (verb, fileId, retries, body)
        self.renders = []
        self.render_check = None
        self.fail_upload = False

    def list_children(self, fid):
        if fid == "boston":
            kids = [{"id": "bdir", "name": BANNERS, "mimeType": cc.FOLDER}]
            kids += [{"id": "wb" if i == 0 else "wb%d" % i, "name": rwb.BANNER,
                      "mimeType": "image/png"} for i in range(self.banners)]
            return kids
        if fid == "bdir":
            return [{"id": "deck", "name": rwb.DECK, "mimeType": "x"}]
        return []

    def gws_json(self, *verb, params=None, body=None, retries=None):
        fid = (params or {}).get("fileId")
        self.calls.append((verb[-1], fid, retries, body))
        if verb[-1] == "get" and fid == "deck":
            return {"md5Checksum": self.md5} if self.md5 else {}
        if verb[-1] == "get" and fid == "wb":
            return {"appProperties": {rwb.STAMP: self.stamp} if self.stamp else {}}
        if verb[-1] == "create":
            self.banners = 1
            return {"id": "wb"}
        if verb[-1] == "update":
            if fid != "wb":
                raise AssertionError("stamp written to %r, not the banner" % fid)
            self.stamp = body["appProperties"][rwb.STAMP]
            return {}
        raise AssertionError("unexpected call %r %r" % (verb, params))

    def download(self, fid, out):
        assert fid == "deck"
        with open(out, "wb") as fh:
            fh.write(self.deck)

    def upload(self, fid, path, mime):
        if fid != "wb":
            raise AssertionError("PNG uploaded over %r, not the banner" % fid)
        if self.fail_upload:
            raise RuntimeError("upload blew up")
        self.calls.append(("upload", fid, None, mime))

    def render(self, fid, out, slide_index=0, check_text=None, strict_cleanup=False):
        assert (fid, slide_index, strict_cleanup) == ("deck", rwb.SLIDE_INDEX, True)
        self.render_check = check_text
        self.renders.append(fid)
        with open(out, "wb") as fh:
            fh.write(b"\x89PNG fake")
        return out

    def verbs(self):
        return [c[0] for c in self.calls]


def run(fake, write=True, name="Boston", create=False):
    with mock.patch.object(cc, "list_children", fake.list_children), \
            mock.patch.object(cc, "gws_json", fake.gws_json), \
            mock.patch.object(cc, "gws_download", fake.download), \
            mock.patch.object(cc, "gws_upload", fake.upload), \
            mock.patch.object(rwb.slides_export, "render_slide_png", fake.render), \
            tempfile.TemporaryDirectory() as tmp:
        return rwb.sync_chapter("boston", name, write, tmp, create=create)


class TestNameCheck(unittest.TestCase):
    def test_exactly_the_headline_and_the_name_passes(self):
        self.assertIsNone(rwb.name_mismatch("AAIF  Community Events  new  york", "New York"))

    def test_a_trailing_full_stop_is_ignored(self):
        self.assertIsNone(rwb.name_mismatch("AAIF Community Events Japan.", "Japan"))

    def test_another_city_is_refused(self):
        self.assertIn("san francisco",
                      rwb.name_mismatch("AAIF Community Events San Francisco", "Boston"))

    def test_a_qualified_spelling_is_not_a_match(self):
        self.assertIsNotNone(rwb.name_mismatch("AAIF Community Events Portland, OR", "Portland"))
        self.assertIsNotNone(rwb.name_mismatch("AAIF Community Events New York", "York"))

    def test_a_leftover_template_city_beside_the_new_one_is_refused(self):
        self.assertIsNotNone(rwb.name_mismatch(
            "AAIF Community Events Boston San Francisco", "Boston"))

    def test_a_slide_without_the_headline_is_refused(self):
        self.assertIn("does not start", rwb.name_mismatch("Boston", "Boston"))

    def test_templatecity_expects_the_rebrand_source_city(self):
        self.assertEqual(rwb.expected_name("TemplateCity"), cc.SOURCE_NAME)
        self.assertEqual(rwb.expected_name("Boston"), "Boston")


class TestSlideText(unittest.TestCase):
    def test_runs_with_and_without_attributes_are_read(self):
        p = _path(_pptx(["AAIF Community Events", "New York"]))
        self.addCleanup(os.remove, p)
        self.assertEqual(rwb.slide_text(p), "AAIF Community Events New York")

    def test_the_fourth_slide_in_presentation_order_not_the_part_name(self):
        # Reordered deck: part slide4.xml now sits second; slide2.xml is fourth.
        p = _path(_pptx(None, order=(1, 4, 3, 2, 5), part_runs={
            4: ["AAIF Community Events", "San Francisco"],
            2: ["AAIF Community Events", "Boston"]}))
        self.addCleanup(os.remove, p)
        self.assertEqual(rwb.slide_text(p), "AAIF Community Events Boston")

    def test_xml_entities_are_unescaped(self):
        p = _path(_pptx(["AAIF Community Events", "Trinidad &amp; Tobago"]))
        self.addCleanup(os.remove, p)
        self.assertIsNone(rwb.name_mismatch(rwb.slide_text(p), "Trinidad & Tobago"))

    def test_a_short_deck_says_so(self):
        p = _path(_pptx(None, order=(1, 2), part_runs={}))
        self.addCleanup(os.remove, p)
        with self.assertRaisesRegex(RuntimeError, "2 slide"):
            rwb.slide_text(p)


class TestSync(unittest.TestCase):
    OK = ["AAIF Community Events", "Boston"]

    def test_a_deck_naming_another_city_is_never_rendered(self):
        fake = FakeDrive(["AAIF Community Events", "San Francisco"])
        r = run(fake)
        self.assertIn("expected 'Boston'", r.error)
        self.assertEqual(fake.renders, [])
        self.assertNotIn("upload", fake.verbs())

    def test_a_banner_stamped_with_this_deck_and_name_is_skipped(self):
        fake = FakeDrive(self.OK, stamp=rwb.stamp_value("m1", "Boston"))
        self.assertEqual(run(fake), rwb.Result("Boston", rwb.CURRENT))
        self.assertEqual(fake.renders, [])

    def test_a_new_name_with_an_unchanged_deck_is_checked_again(self):
        """A folder renamed by hand: the deck md5 did not move."""
        fake = FakeDrive(["AAIF Community Events", "Scotland"],
                         stamp=rwb.stamp_value("m1", "Scotland"))
        r = run(fake, name="Edinburgh")
        self.assertIn("expected 'Edinburgh'", r.error)
        self.assertEqual(fake.renders, [])

    def test_a_moved_deck_is_re_rendered_onto_the_banner_and_restamped(self):
        fake = FakeDrive(self.OK, stamp=rwb.stamp_value("old", "Boston"), md5="new")
        self.assertEqual(run(fake).action, rwb.RENDER)
        self.assertEqual(fake.renders, ["deck"])
        self.assertEqual([c for c in fake.calls if c[0] == "upload"],
                         [("upload", "wb", None, "image/png")])
        self.assertEqual(fake.stamp, rwb.stamp_value("new", "Boston"))
        self.assertLess(fake.verbs().index("upload"), fake.verbs().index("update"))

    def test_the_renderer_rechecks_the_page_it_draws(self):
        fake = FakeDrive(self.OK)
        run(fake)
        self.assertIsNone(fake.render_check("AAIF Community Events Boston"))
        self.assertIsNotNone(fake.render_check("AAIF Community Events Oslo"))

    def test_report_mode_renders_and_writes_nothing(self):
        fake = FakeDrive(self.OK)
        self.assertEqual(run(fake, write=False).action, rwb.RENDER)
        self.assertEqual(fake.renders, [])
        self.assertFalse({"create", "update", "upload"} & set(fake.verbs()))

    def test_a_failed_upload_is_not_stamped_current(self):
        fake = FakeDrive(self.OK, stamp="old")
        fake.fail_upload = True
        r = run(fake)
        self.assertIn("upload blew up", r.error)
        self.assertEqual(fake.stamp, "old")

    def test_a_deck_with_no_checksum_is_an_error_not_current(self):
        """Otherwise None == an unstamped banner's missing stamp."""
        fake = FakeDrive(self.OK, md5=None)
        r = run(fake)
        self.assertIn("no checksum", r.error)
        self.assertEqual(fake.renders, [])

    def test_duplicate_banners_are_refused_with_their_ids(self):
        fake = FakeDrive(self.OK, banners=2)
        r = run(fake)
        self.assertIn("wb, wb1", r.error)
        self.assertEqual(fake.renders, [])

    def test_a_sweep_never_creates_a_missing_banner(self):
        fake = FakeDrive(self.OK, banner=False)
        self.assertEqual(run(fake).action, rwb.ABSENT)
        self.assertNotIn("create", fake.verbs())
        self.assertEqual(fake.renders, [])

    def test_templatecity_is_checked_against_the_city_it_clones_from(self):
        fake = FakeDrive(["AAIF Community Events", "San Francisco"])
        self.assertEqual(run(fake, name="TemplateCity").action, rwb.RENDER)

    def test_a_missing_banner_is_created_once_without_retries_and_stamped(self):
        fake = FakeDrive(self.OK, banner=False)
        self.assertEqual(run(fake, create=True).action, rwb.RENDER)
        creates = [c for c in fake.calls if c[0] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertIs(creates[0][2], gwsmod.NO_RETRY)
        self.assertEqual(creates[0][3]["parents"], ["boston"])
        self.assertEqual(fake.stamp, rwb.stamp_value("m1", "Boston"))

    def test_a_create_then_failed_upload_names_the_empty_file(self):
        fake = FakeDrive(self.OK, banner=False)
        fake.fail_upload = True
        r = run(fake, create=True)
        self.assertIn("EMPTY", r.error)
        self.assertIn("wb", r.error)
        self.assertIsNone(fake.stamp)

    def test_errors_are_returned_not_raised(self):
        fake = FakeDrive(self.OK)
        fake.list_children = lambda fid: (_ for _ in ()).throw(OSError("drive down"))
        self.assertIn("drive down", run(fake).error)


class TestMain(unittest.TestCase):
    CHAPTERS = [("t", "TemplateCity"), ("a", "Aarhus"), ("b", "Boston")]

    def _main(self, results, argv=()):
        seen = []

        def fake_list(parent):
            return [{"id": i, "name": n, "mimeType": cc.FOLDER} for i, n in self.CHAPTERS]

        def fake_sync(cid, name, write, tmp, create=False):
            seen.append((name, create))
            return results.get(name, rwb.Result(name, rwb.CURRENT))
        with mock.patch.object(cc, "list_children", fake_list), \
                mock.patch.object(rwb, "sync_chapter", fake_sync), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            code = rwb.main(list(argv))
        return code, seen, out.getvalue()

    def test_a_sweep_skips_templatecity_and_never_creates(self):
        code, seen, _ = self._main({})
        self.assertEqual(seen, [("Aarhus", False), ("Boston", False)])
        self.assertEqual(code, 0)

    def test_templatecity_only_by_name_case_insensitive(self):
        _c, seen, _ = self._main({}, ["--chapter", "templatecity"])
        self.assertEqual(seen, [("TemplateCity", False)])

    def test_report_with_work_exits_2(self):
        code, _s, _o = self._main({"Aarhus": rwb.Result("Aarhus", rwb.RENDER)})
        self.assertEqual(code, 2)

    def test_absent_is_listed_not_failed(self):
        code, _s, out = self._main({"Boston": rwb.Result("Boston", rwb.ABSENT)})
        self.assertEqual(code, 0)
        self.assertIn("No Web Banner.png at all", out)

    def test_any_failure_exits_1(self):
        code, _s, _o = self._main({"Boston": rwb.Result("Boston", error="boom")})
        self.assertEqual(code, 1)

    def test_an_unknown_outcome_is_a_failure_not_clean(self):
        code, _s, out = self._main({"Boston": rwb.Result("Boston")})
        self.assertEqual(code, 1)
        self.assertIn("unknown outcome", out)

    def test_no_match_exits_1(self):
        code, _s, _o = self._main({}, ["--chapter", "Atlantis"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
