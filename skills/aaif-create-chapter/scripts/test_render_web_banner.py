#!/usr/bin/env python3
"""Unit tests for rendering each chapter's Web Banner from its Square Logo deck.

The properties pinned here are the ones that fail quietly:

  (a) **a deck that names another city is never rendered.** Rendering it would
      publish the wrong name with a fresh date on it, which is worse than the
      stale banner it replaces.
  (b) **a banner stamped with the deck's checksum is skipped**, and one whose
      deck moved is not — otherwise a re-run re-renders the whole estate, or a
      rename never reaches the banner.
  (c) **the stamp is written only after the upload**, so a failed upload is
      not recorded as current.
  (d) **a chapter with no banner gets one created exactly once** (NO_RETRY).

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


def _pptx(slide4_runs):
    """Bytes of a minimal .pptx whose slide 4 holds `slide4_runs`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(1, 6):
            runs = slide4_runs if i == 4 else ["AAIF"]
            body = "".join('<a:r><a:t xml:space="preserve">%s</a:t></a:r>' % r
                           if n % 2 else "<a:r><a:t>%s</a:t></a:r>" % r
                           for n, r in enumerate(runs))
            z.writestr("ppt/slides/slide%d.xml" % i, "<p:sld>%s</p:sld>" % body)
    return buf.getvalue()


class FakeDrive:
    """One chapter folder, `boston`, with a Banners folder and a deck."""

    def __init__(self, runs, banner_stamp=None, banner=True, md5="m1"):
        self.deck = _pptx(runs)
        self.md5 = md5
        self.stamp = banner_stamp
        self.banner = banner
        self.calls = []           # (verb, fileId-or-None)
        self.renders = 0
        self.fail_upload = False

    def list_children(self, fid):
        if fid == "boston":
            kids = [{"id": "bdir", "name": BANNERS, "mimeType": cc.FOLDER}]
            if self.banner:
                kids.append({"id": "wb", "name": rwb.BANNER, "mimeType": "image/png"})
            return kids
        if fid == "bdir":
            return [{"id": "deck", "name": rwb.DECK, "mimeType": "x"}]
        return []

    def gws_json(self, *verb, params=None, body=None, retries=None):
        fid = (params or {}).get("fileId")
        self.calls.append((verb[-1], fid, retries, body))
        if verb[-1] == "get" and fid == "deck":
            return {"md5Checksum": self.md5}
        if verb[-1] == "get" and fid == "wb":
            return {"appProperties": {rwb.STAMP: self.stamp} if self.stamp else {}}
        if verb[-1] == "create":
            self.banner = True
            return {"id": "wb"}
        if verb[-1] == "update":
            self.stamp = body["appProperties"][rwb.STAMP]
            return {}
        raise AssertionError("unexpected call %r %r" % (verb, params))

    def download(self, fid, out):
        assert fid == "deck"
        with open(out, "wb") as fh:
            fh.write(self.deck)

    def upload(self, fid, path, mime):
        if self.fail_upload:
            raise RuntimeError("upload blew up")
        self.calls.append(("upload", fid, None, None))

    def render(self, fid, out, slide_index=0):
        assert (fid, slide_index) == ("deck", rwb.SLIDE_INDEX)
        self.renders += 1
        with open(out, "wb") as fh:
            fh.write(b"\x89PNG fake")
        return out


def run(fake, write=True, name="Boston", create=False):
    with mock.patch.object(cc, "list_children", fake.list_children), \
            mock.patch.object(cc, "gws_json", fake.gws_json), \
            mock.patch.object(cc, "gws_download", fake.download), \
            mock.patch.object(cc, "gws_upload", fake.upload), \
            mock.patch.object(rwb.slides_export, "render_slide_png", fake.render), \
            tempfile.TemporaryDirectory() as tmp:
        return rwb.sync_chapter("boston", name, write, tmp, create=create)


class TestSlideText(unittest.TestCase):
    def test_runs_with_and_without_attributes_are_read(self):
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as fh:
            fh.write(_pptx(["AAIF Community Events", "New York"]))
        self.addCleanup(os.remove, fh.name)
        self.assertEqual(rwb.slide_text(fh.name), "AAIF Community Events New York")

    def test_names_chapter_is_case_and_space_blind(self):
        self.assertTrue(rwb.names_chapter("AAIF  Community Events  new  york", "New York"))

    def test_another_city_is_refused(self):
        self.assertFalse(rwb.names_chapter("AAIF Community Events San Francisco", "Boston"))

    def test_a_slide_without_the_headline_is_refused(self):
        self.assertFalse(rwb.names_chapter("Boston", "Boston"))


class TestSync(unittest.TestCase):
    def test_a_deck_naming_another_city_is_never_rendered(self):
        fake = FakeDrive(["AAIF Community Events", "San Francisco"])
        r = run(fake)
        self.assertIsNotNone(r.error)
        self.assertEqual(fake.renders, 0)
        self.assertNotIn("upload", [c[0] for c in fake.calls])

    def test_a_banner_stamped_with_the_current_deck_is_skipped(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"], banner_stamp="m1")
        self.assertEqual(run(fake), rwb.Result("Boston", "current"))
        self.assertEqual(fake.renders, 0)

    def test_a_moved_deck_is_re_rendered_and_restamped(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"], banner_stamp="old", md5="new")
        self.assertEqual(run(fake).action, "render")
        self.assertEqual(fake.renders, 1)
        self.assertEqual(fake.stamp, "new")

    def test_report_mode_renders_and_writes_nothing(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"])
        self.assertEqual(run(fake, write=False).action, "render")
        self.assertEqual(fake.renders, 0)
        self.assertEqual([c[0] for c in fake.calls], ["get", "get"])

    def test_a_failed_upload_is_not_stamped_current(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"], banner_stamp="old")
        fake.fail_upload = True
        r = run(fake)
        self.assertIn("upload blew up", r.error)
        self.assertEqual(fake.stamp, "old")

    def test_a_sweep_never_creates_a_missing_banner(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"], banner=False)
        self.assertEqual(run(fake).action, "absent")
        self.assertNotIn("create", [c[0] for c in fake.calls])
        self.assertEqual(fake.renders, 0)

    def test_templatecity_is_checked_against_the_city_it_clones_from(self):
        fake = FakeDrive(["AAIF Community Events", "San Francisco"])
        self.assertEqual(run(fake, name="TemplateCity").action, "render")

    def test_a_missing_banner_is_created_once_without_retries(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"], banner=False)
        self.assertEqual(run(fake, create=True).action, "render")
        creates = [c for c in fake.calls if c[0] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertIs(creates[0][2], gwsmod.NO_RETRY)
        self.assertEqual(creates[0][3]["parents"], ["boston"])

    def test_errors_are_returned_not_raised(self):
        fake = FakeDrive(["AAIF Community Events", "Boston"])
        fake.list_children = lambda fid: (_ for _ in ()).throw(OSError("drive down"))
        self.assertIn("drive down", run(fake).error)


if __name__ == "__main__":
    unittest.main()
