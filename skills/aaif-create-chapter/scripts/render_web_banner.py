#!/usr/bin/env python3
"""Render each chapter's `Web Banner.png` from its own Square Logo deck.

`Web Banner.png` sits at the root of every chapter folder: the AAIF mark over
"AAIF Community Events / <City>". It is an image, so `create_chapter.py`'s
rebrand — which rewrites OOXML text — cannot reach it, and the clone copied
TemplateCity's PNG byte for byte. Found 2026-11-03: dozens of chapters' banners
said "San Francisco", and every renamed chapter's still showed its old name.

The same composition already exists as text, which the rebrand does reach:
slide 4 of `Banners (…)/Square Logo.pptx`. So the banner is not drawn here at
all; it is that slide, rendered by Google's own Slides renderer
(`aaif_events.slides_export.render_slide_png`, never LibreOffice), and so it is
correct whenever the deck is.

Two guards:

  * **The slide must name the chapter.** Rendering a deck that still says
    another city would publish the wrong name with a fresh timestamp on it, so
    a slide whose text lacks the chapter's name is reported and skipped.
  * **A banner records the deck it came from.** The upload stamps
    `appProperties.aaifBannerDeck` with the deck's `md5Checksum`. A re-run
    skips a chapter whose stamp matches, so this is cheap to re-run, and a deck
    that `rename_chapter.py` has rewritten is picked up automatically because
    its checksum moved. (Renders are not byte-stable, so comparing PNG bytes
    would re-upload every banner on every run.)

Read-only by default.

Usage:
  python3 render_web_banner.py                      # report
  python3 render_web_banner.py --write
  python3 render_web_banner.py --chapter Aarhus --write
"""
import argparse
import os
import re
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc               # noqa: E402  (puts lib on the path)
from aaif_events import gws as gwsmod     # noqa: E402
from aaif_events import slides_export     # noqa: E402

BANNER = "Web Banner.png"
DECK = "Square Logo.pptx"
#: The banners subfolder is "Banners (Chapter Specific, Changed Rarely)"; match
#: the stem so a reworded parenthetical does not hide it.
BANNERS_DIR = re.compile(r"^Banners\b")
#: 0-based: slide 4 is the "AAIF Community Events / <City>" square.
SLIDE_INDEX = 3
HEADLINE = "AAIF Community Events"
STAMP = "aaifBannerDeck"
#: The template's deck says the city it is cloned FROM, by design.
DECK_NAME = {"TemplateCity": "San Francisco"}
PNG = "image/png"


class Result(NamedTuple):
    name: str
    #: "current", "render" (would/did render), "absent" (no banner and not
    #: asked to create one), or None when `error` is set.
    action: Optional[str] = None
    error: Optional[str] = None


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


def slide_text(pptx_path, index=SLIDE_INDEX):
    """Every text run on slide `index` (0-based), joined by spaces."""
    with zipfile.ZipFile(pptx_path) as z:
        xml = z.read("ppt/slides/slide%d.xml" % (index + 1)).decode("utf-8", "replace")
    return " ".join(re.findall(r"<a:t(?:\s[^>]*)?>([^<]*)</a:t>", xml))


def names_chapter(text, name):
    """True when the slide is the community-events square and names `name`."""
    t = _norm(text)
    return _norm(HEADLINE) in t and _norm(name) in t


def chapters():
    """[(id, name)] for every chapter folder under Chapters, sorted."""
    return sorted(((c["id"], c["name"]) for c in cc.list_children(cc.CHAPTERS_PARENT)
                   if c["mimeType"] == cc.FOLDER), key=lambda t: t[1])


def _find(children, pred):
    hits = [c for c in children if pred(c)]
    if len(hits) > 1:
        raise RuntimeError("%d matches for %s" % (len(hits), hits[0]["name"]))
    return hits[0] if hits else None


def sync_chapter(chapter_id, name, write, tmpdir, create=False):
    """Bring one chapter's banner up to date. Returns a `Result`, never raises:
    this runs under a thread pool, and one chapter's failure must not end the
    others.

    A chapter with no banner at all is created one only when `create` is set
    (the create/rename hooks). A sweep reports it instead: the folders without
    one predate the template that carries it (the Tatooine test chapter), and
    a sweep has no business adding files to them."""
    try:
        kids = cc.list_children(chapter_id)
        bdir = _find(kids, lambda c: c["mimeType"] == cc.FOLDER
                     and BANNERS_DIR.match(c["name"]))
        if bdir is None:
            return Result(name, error="no Banners folder")
        deck = _find(cc.list_children(bdir["id"]), lambda c: c["name"] == DECK)
        if deck is None:
            return Result(name, error="no %s in %s" % (DECK, bdir["name"]))
        meta = cc.gws_json("drive", "files", "get", params={
            "fileId": deck["id"], "supportsAllDrives": True, "fields": "md5Checksum"})
        md5 = meta.get("md5Checksum")
        if not md5:
            return Result(name, error="%s has no checksum (not a stored .pptx?)" % DECK)

        banner = _find(kids, lambda c: c["name"] == BANNER)
        if banner is None and not create:
            return Result(name, "absent")
        if banner is not None:
            bmeta = cc.gws_json("drive", "files", "get", params={
                "fileId": banner["id"], "supportsAllDrives": True,
                "fields": "appProperties"})
            if (bmeta.get("appProperties") or {}).get(STAMP) == md5:
                return Result(name, "current")

        local = os.path.join(tmpdir, "%s.pptx" % chapter_id)
        cc.gws_download(deck["id"], local)
        try:
            text = slide_text(local)
        finally:
            os.remove(local)
        want = DECK_NAME.get(name, name)
        if not names_chapter(text, want):
            return Result(name, error="slide %d of %s does not say %r with %r — fix "
                          "the deck first" % (SLIDE_INDEX + 1, DECK, HEADLINE, want))
        if not write:
            return Result(name, "render")

        png = os.path.join(tmpdir, "%s.png" % chapter_id)
        slides_export.render_slide_png(deck["id"], png, slide_index=SLIDE_INDEX)
        try:
            if banner is None:
                # NO_RETRY: a create that landed but answered like a timeout
                # would, re-sent, leave two banners in the folder.
                banner = cc.gws_json(
                    "drive", "files", "create", retries=gwsmod.NO_RETRY,
                    params={"supportsAllDrives": True, "fields": "id"},
                    body={"name": BANNER, "parents": [chapter_id], "mimeType": PNG})
            cc.gws_upload(banner["id"], png, PNG)
            # Stamp last: a banner whose upload failed must not be marked current.
            cc.gws_json("drive", "files", "update",
                        params={"fileId": banner["id"], "supportsAllDrives": True},
                        body={"appProperties": {STAMP: md5}})
        finally:
            if os.path.exists(png):
                os.remove(png)
        return Result(name, "render")
    except Exception as e:
        return Result(name, error="%s: %s" % (type(e).__name__, str(e)[:200]))


def sync_one(chapter_id, name):
    """Render one chapter's banner now (used by create/rename after a write).
    Returns an error string, or None on success."""
    with tempfile.TemporaryDirectory() as tmp:
        r = sync_chapter(chapter_id, name, True, tmp, create=True)
    return r.error


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="render and upload (default: report only)")
    ap.add_argument("--chapter", help="only this chapter (exact name, case-insensitive)")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    todo = chapters()
    if a.chapter:
        todo = [(i, n) for i, n in todo if n.lower() == a.chapter.lower()]
    if not todo:
        print("no chapters matched.")
        return 1
    print("%d chapter(s).%s\n" % (len(todo), "" if a.write
                                  else "  REPORT ONLY — nothing will be written."))
    pending, current, failed, absent = [], 0, [], []
    with tempfile.TemporaryDirectory() as tmp, \
            ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for r in pool.map(lambda t: sync_chapter(t[0], t[1], a.write, tmp), todo):
            if r.error is not None:
                failed.append(r)
                print("  FAILED    %-26s %s" % (r.name, r.error))
            elif r.action == "absent":
                absent.append(r.name)
            elif r.action == "render":
                pending.append(r.name)
                print("  %s %s" % ("rendered " if a.write else "would render", r.name))
            else:
                current += 1
    print("\n%d %s, %d already current, %d failed."
          % (len(pending), "rendered" if a.write else "to render", current, len(failed)))
    if absent:
        print("No %s at all (left alone; a sweep never adds one): %s"
              % (BANNER, ", ".join(sorted(absent))))
    if failed:
        return 1
    if pending and not a.write:
        print("Re-run with --write to apply.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
