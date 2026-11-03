#!/usr/bin/env python3
"""Render each chapter's `Web Banner.png` from its own Square Logo deck.

`Web Banner.png` sits at the root of every chapter folder: the AAIF mark over
"AAIF Community Events / <City>". It is an image, so `create_chapter.py`'s
rebrand — which rewrites OOXML text — cannot reach it, and the clone copied
TemplateCity's PNG byte for byte. Found 2026-11-03: every chapter cloned before
then (37 of them) had a "San Francisco" banner, and every renamed chapter's
still showed its old name.

The same composition already exists as text, which the rebrand does reach:
slide 4 of `Banners (…)/Square Logo.pptx`, a 2000 x 2000 square like the PNG it
replaces. So the banner is not drawn here at all; it is that slide, rendered by
Google's own Slides renderer (`aaif_events.slides_export.render_slide_png`,
never LibreOffice), and so it is correct whenever the deck is.

Guards:

  * **The slide must say exactly "AAIF Community Events" and the chapter's
    name.** Rendering a deck that names another city would publish the wrong
    name with a fresh timestamp on it. The check is exact, not a substring
    test, so "Portland, OR" does not pass for Portland and a slide still
    carrying a leftover city fails. It runs twice: on the deck's own slide 4
    (resolved through `presentation.xml`'s slide order, not the `slideN.xml`
    part name) before anything is written, and again by the renderer on the
    page it is about to draw.
  * **A banner records what it was rendered from.** The upload stamps
    `appProperties.aaifBannerDeck` with the deck's `md5Checksum` AND the
    chapter name. A re-run skips a chapter whose stamp matches, so it is cheap
    to re-run; a deck that `rename_chapter.py` rewrote, or a folder renamed by
    hand, no longer matches and is checked again. (Renders are not byte-stable,
    so comparing PNG bytes would re-upload every banner on every run.) Drive
    scopes `appProperties` to the OAuth client that wrote them, so an operator
    on a different `gws` client sees no stamps and re-renders everything once:
    slower, never wrong.
  * **A stray temp copy is a failure.** The renderer's throwaway Slides copy
    lands beside the deck; if it cannot be trashed, that chapter FAILS and the
    copy's id is printed.

A sweep skips TemplateCity (its deck says San Francisco by design, and a stray
temp copy there would be cloned into every later chapter) unless it is named
with `--chapter`, and never creates a banner in a folder that has none.

Read-only by default. Exit codes: 0 all current/rendered, 2 report-only with
banners to render, 1 any failure.

Usage:
  python3 render_web_banner.py                      # report
  python3 render_web_banner.py --write
  python3 render_web_banner.py --chapter Aarhus --write
"""
import argparse
import html
import os
import posixpath
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
#: 0-based: the fourth slide in presentation order is the "AAIF Community
#: Events / <City>" square.
SLIDE_INDEX = 3
HEADLINE = "AAIF Community Events"
STAMP = "aaifBannerDeck"
PNG = "image/png"
TEMPLATE = "TemplateCity"
#: Folder name -> the name its deck is expected to show, where they differ.
#: TemplateCity's deck carries the rebrand source city by design.
DECK_NAME = {TEMPLATE: cc.SOURCE_NAME}
#: Absolute, so the printed fallback command works from an installed plugin.
SCRIPT = os.path.abspath(__file__)

CURRENT, RENDER, ABSENT = "current", "render", "absent"


class Result(NamedTuple):
    """One chapter's outcome. Exactly one of `action` / `error` is set."""
    name: str
    #: CURRENT (stamp matched, nothing done), RENDER (would render / rendered),
    #: or ABSENT (no banner, and not asked to create one).
    action: Optional[str] = None
    error: Optional[str] = None


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


def fallback_command(name):
    return "python3 %s --chapter %r --write" % (SCRIPT, name)


def expected_name(folder):
    return DECK_NAME.get(folder, folder)


def stamp_value(md5, want):
    return "%s|%s" % (md5, _norm(want))


def name_mismatch(text, want):
    """None when `text` is exactly the headline plus `want`, else the reason.

    Exact rather than "contains": a slide saying "Portland, OR" must not pass
    for Portland, nor one still carrying the template city beside the new one.
    Trailing punctuation is ignored (the decks' display style puts a full stop
    after the city in some layouts)."""
    t, head = _norm(text), _norm(HEADLINE)
    if not t.startswith(head):
        return "slide %d does not start with %r (it says %r)" % (SLIDE_INDEX + 1, HEADLINE, text[:80])
    rest = re.sub(r"^[\W_]+|[\W_]+$", "", t[len(head):])
    if rest != _norm(want):
        return ("slide %d says %r, expected %r — fix the deck, or map the folder in "
                "DECK_NAME if the deck is right" % (SLIDE_INDEX + 1, rest, want))
    return None


def slide_part(z, index=SLIDE_INDEX):
    """The zip part of the `index`-th slide (0-based) in presentation order.

    Order lives in `presentation.xml`'s `<p:sldIdLst>`, not in the part names:
    a reordered deck keeps `slide4.xml` wherever the slide now sits, while the
    renderer draws the fourth slide in order."""
    pres_xml = z.read("ppt/presentation.xml").decode("utf-8", "replace")
    rids = re.findall(r'<p:sldId\b[^>]*\br:id="([^"]+)"', pres_xml)
    if index >= len(rids):
        raise RuntimeError("deck has %d slide(s); slide %d is needed" % (len(rids), index + 1))
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "replace")
    for rel in re.findall(r"<Relationship\b[^>]*>", rels):
        if re.search(r'\bId="%s"' % re.escape(rids[index]), rel):
            target = re.search(r'\bTarget="([^"]+)"', rel).group(1)
            if target.startswith("/"):          # package-absolute
                return target[1:]
            return posixpath.normpath(posixpath.join("ppt", target))
    raise RuntimeError("slide %d's relationship %s is missing" % (index + 1, rids[index]))


def slide_text(pptx_path, index=SLIDE_INDEX):
    """Every text run on the `index`-th slide (presentation order), unescaped
    and joined by spaces."""
    with zipfile.ZipFile(pptx_path) as z:
        xml = z.read(slide_part(z, index)).decode("utf-8", "replace")
    return " ".join(html.unescape(r)
                    for r in re.findall(r"<a:t(?:\s[^>]*)?>([^<]*)</a:t>", xml))


def chapters(include_template=False):
    """[(id, name)] for every chapter folder under Chapters, sorted."""
    return sorted(((c["id"], c["name"]) for c in cc.list_children(cc.CHAPTERS_PARENT)
                   if c["mimeType"] == cc.FOLDER
                   and (include_template or c["name"] != TEMPLATE)),
                  key=lambda t: t[1])


def _find(children, pred, what):
    hits = [c for c in children if pred(c)]
    if len(hits) > 1:
        raise RuntimeError("%d copies of %s (ids %s) — trash the extra one(s)"
                           % (len(hits), what, ", ".join(h["id"] for h in hits)))
    return hits[0] if hits else None


def sync_chapter(chapter_id, name, write, tmpdir, create=False):
    """Bring one chapter's banner up to date. Returns a `Result`; never raises
    an Exception, because this runs under a thread pool and one chapter's
    failure must not end the others.

    A banner is created in a folder that has none only when `create` is set
    (the create hook). A sweep reports such a folder instead: the ones without
    a banner predate the template that carries it (the Tatooine test chapter),
    and a sweep has no business adding files to them."""
    created = None
    try:
        want = expected_name(name)
        kids = cc.list_children(chapter_id)
        bdir = _find(kids, lambda c: c["mimeType"] == cc.FOLDER
                     and BANNERS_DIR.match(c["name"]), "the Banners folder")
        if bdir is None:
            return Result(name, error="no Banners folder")
        deck = _find(cc.list_children(bdir["id"]), lambda c: c["name"] == DECK, DECK)
        if deck is None:
            return Result(name, error="no %s in %s" % (DECK, bdir["name"]))
        md5 = cc.gws_json("drive", "files", "get", params={
            "fileId": deck["id"], "supportsAllDrives": True,
            "fields": "md5Checksum"}).get("md5Checksum")
        if not md5:
            # Without this, a None checksum would compare equal to an unstamped
            # banner's missing stamp and report it current forever.
            return Result(name, error="%s has no checksum (not a stored .pptx?)" % DECK)
        stamp = stamp_value(md5, want)

        banner = _find(kids, lambda c: c["name"] == BANNER, BANNER)
        if banner is None and not create:
            return Result(name, ABSENT)
        if banner is not None:
            props = cc.gws_json("drive", "files", "get", params={
                "fileId": banner["id"], "supportsAllDrives": True,
                "fields": "appProperties"}).get("appProperties") or {}
            if props.get(STAMP) == stamp:
                return Result(name, CURRENT)

        local = os.path.join(tmpdir, "%s.pptx" % chapter_id)
        cc.gws_download(deck["id"], local)
        try:
            why = name_mismatch(slide_text(local), want)
        finally:
            os.remove(local)
        if why:
            return Result(name, error=why)
        if not write:
            return Result(name, RENDER)

        png = os.path.join(tmpdir, "%s.png" % chapter_id)
        try:
            slides_export.render_slide_png(
                deck["id"], png, slide_index=SLIDE_INDEX, strict_cleanup=True,
                check_text=lambda text: name_mismatch(text, want))
            if banner is None:
                # NO_RETRY: a create that landed but answered like a timeout
                # would, re-sent, leave two banners in the folder.
                banner = created = cc.gws_json(
                    "drive", "files", "create", retries=gwsmod.NO_RETRY,
                    params={"supportsAllDrives": True, "fields": "id"},
                    body={"name": BANNER, "parents": [chapter_id], "mimeType": PNG})
            cc.gws_upload(banner["id"], png, PNG)
            # Stamp last: a banner whose upload failed must not be marked current.
            cc.gws_json("drive", "files", "update",
                        params={"fileId": banner["id"], "supportsAllDrives": True},
                        body={"appProperties": {STAMP: stamp}})
        finally:
            if os.path.exists(png):
                os.remove(png)
        return Result(name, RENDER)
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:400])
        if created is not None:
            msg = ("created an EMPTY %s (id %s) but did not finish: %s — re-run to "
                   "fill it" % (BANNER, created["id"], msg))
        return Result(name, error=msg)


def sync_one(chapter_id, name, create):
    """Render one chapter's banner now (the create/rename hooks). Returns the
    `Result`, so a caller can tell a real render from an already-current one."""
    with tempfile.TemporaryDirectory() as tmp:
        return sync_chapter(chapter_id, name, True, tmp, create=create)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="render and upload (default: report only)")
    ap.add_argument("--chapter", help="only this chapter (exact name, case-insensitive; "
                    "the only way to include TemplateCity)")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)

    todo = chapters(include_template=bool(a.chapter))
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
            elif r.action == RENDER:
                pending.append(r.name)
                print("  %s %s" % ("rendered " if a.write else "would render", r.name))
            elif r.action == ABSENT:
                absent.append(r.name)
            elif r.action == CURRENT:
                current += 1
            else:
                # An outcome this loop does not know is not "clean".
                failed.append(r)
                print("  FAILED    %-26s unknown outcome %r" % (r.name, r.action))
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
