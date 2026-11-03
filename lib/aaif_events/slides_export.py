"""Render a single slide of a Drive-hosted .pptx to PNG via the Slides API,
instead of a local LibreOffice conversion. LibreOffice substitutes local
system fonts for the deck's actual brand fonts whenever the render machine
doesn't have those fonts installed (true for effectively every machine but
the original designer's), silently producing wrong-looking exports; Google's
own renderer (used here) does not.

Requires the `gws` CLI (Drive + Slides scopes) on PATH and authenticated.
"""
import os
import sys
import urllib.request

from aaif_events import gws as _gws_mod

_SLIDES_MIME = "application/vnd.google-apps.presentation"

# This module carried its own `_gws`/`_gws_json` pair, under a comment saying it
# mirrored create_chapter.py's. It did, including that copy's bare `"500"`
# substring, which matched a permanent `A500:K500 exceeds grid limits` and burned
# the full backoff before failing anyway. A sibling of the shared client, inside
# the same package, is the one copy with no portability argument behind it at
# all, so there is now no copy: `gws.json_out` is the same three guards (retry
# table, empty stdout, non-JSON body) with the fixes.
_gws = _gws_mod.run
_gws_json = _gws_mod.json_out


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _page_text(page):
    """The text of a Slides API page: text in shapes, recursing into groups
    (tables are not read). Runs within a shape are joined with nothing, since
    one word can be split across runs and each paragraph's last run already
    ends in a newline. Shapes are joined with a space."""
    out = []

    def walk(elements):
        for el in elements or ():
            shape_text = "".join(
                (text_el.get("textRun") or {}).get("content") or ""
                for text_el in ((el.get("shape") or {}).get("text") or {}).get("textElements", ()))
            if shape_text:
                out.append(shape_text)
            walk((el.get("elementGroup") or {}).get("children"))
    walk(page.get("pageElements"))
    return " ".join(out)


def _download(url, out_path, timeout=30):
    """Fetch `url` to `out_path` with a bounded timeout. The bytes land in a
    temp file that is renamed over `out_path` only after the too-small sanity
    check passes, so a failure — network, timeout, or a truncated render — can
    neither be mistaken for a finished PNG nor destroy a pre-existing good one:
    only what THIS call wrote is ever cleaned up."""
    tmp = out_path + ".partial"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, \
                open(tmp, "wb") as fh:
            fh.write(r.read())
        if os.path.getsize(tmp) < 1000:
            raise RuntimeError("rendered thumbnail suspiciously small (%s)" % out_path)
        # Size alone let an HTML interstitial or error page through as a
        # "PNG", and a caller that stamps what it uploads would then mark that
        # page current and never look at it again.
        with open(tmp, "rb") as fh:
            if fh.read(8) != _PNG_MAGIC:
                raise RuntimeError("rendered thumbnail is not a PNG (%s)" % out_path)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):   # gone on success (os.replace consumed it)
            os.remove(tmp)


def render_slide_png(file_id, out_path, slide_index=0, thumbnail_size="WIDTH2000_PX",
                     check_text=None, strict_cleanup=False):
    """Export slide `slide_index` (0-based) of the Drive pptx `file_id` to a PNG
    at `out_path`. Makes a throwaway Google Slides copy to render from (Slides
    thumbnails only work on native Slides files, not stored .pptx blobs) and
    trashes it afterward — the source file is never modified.

    `check_text(text) -> error string or None` is run on the text of the page
    that is about to be rendered, the same page object, so a caller can refuse
    a slide without trusting that `slideN.xml` numbering follows presentation
    order. It is a refusal, not a predicate: anything but None or a str raises
    TypeError, so a `lambda t: want in t` cannot pass every wrong slide.

    A failure to trash the throwaway copy is always printed to stderr.
    `strict_cleanup` also makes it part of the raised error (naming the copy's
    id), whether the render succeeded or failed on its own, for callers that
    report per-file results: a stray copy beside TemplateCity's decks is
    cloned into every later chapter."""
    presentation_id = None
    cleanup_error = None
    body_error = None
    try:
        # NO_RETRY: a `files.copy` that succeeded server-side but answered like
        # a timeout would, on a retry, make a SECOND throwaway copy — and the
        # `finally` below only knows the id of the one it got an answer for. The
        # orphan is exactly the stray "TEMP - render_slide_png" the comment there
        # describes: it has no `parents`, so it lands beside the source, and
        # against TemplateCity it is then cloned into every subsequent chapter.
        copy = _gws_json("drive", "files", "copy", retries=_gws_mod.NO_RETRY,
                          params={"fileId": file_id, "supportsAllDrives": True, "fields": "id"},
                          body={"name": "TEMP - render_slide_png", "mimeType": _SLIDES_MIME})
        presentation_id = copy["id"]

        fields = ("slides(objectId,pageElements(shape/text/textElements/textRun/content,"
                  "elementGroup))") if check_text else "slides.objectId"
        presentation = _gws_json("slides", "presentations", "get",
                                  params={"presentationId": presentation_id, "fields": fields})
        slides = presentation.get("slides") or []
        if slide_index >= len(slides):
            raise RuntimeError("deck %s has %d slide(s); slide %d was requested"
                               % (file_id, len(slides), slide_index + 1))
        page_object_id = slides[slide_index]["objectId"]
        if check_text:
            err = check_text(_page_text(slides[slide_index]))
            if err is not None and not isinstance(err, str):
                raise TypeError("check_text must return an error string or None, got %r"
                                % (err,))
            if err:
                raise RuntimeError(err)

        thumb = _gws_json("slides", "presentations", "pages", "getThumbnail",
                           params={"presentationId": presentation_id, "pageObjectId": page_object_id,
                                   "thumbnailProperties.thumbnailSize": thumbnail_size})
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        if not thumb.get("contentUrl"):
            raise RuntimeError("no thumbnail URL for slide %d of %s" % (slide_index + 1, file_id))
        _download(thumb["contentUrl"], out_path)
    except Exception as e:
        body_error = e
        raise
    finally:
        # The throwaway copy has no `parents` set, so it lands in the SAME
        # folder as the source file - not an out-of-sight scratch location.
        # If trashing repeatedly fails and this is ever run against
        # TemplateCity (aaif-create-chapter's own recalibration step does
        # exactly that), a stray "TEMP - render_slide_png" file would get
        # picked up and cloned into every subsequent chapter. Surface
        # cleanup failures loudly instead of swallowing them silently so a
        # stray copy gets noticed and trashed by hand.
        if presentation_id is not None:
            try:
                _gws_json("drive", "files", "update",
                          params={"fileId": presentation_id, "supportsAllDrives": True},
                          body={"trashed": True})
            except Exception as e:
                print("WARNING: could not trash temp Slides copy %s: %s" % (presentation_id, e),
                      file=sys.stderr)
                cleanup_error = e
                if strict_cleanup and body_error is not None:
                    # Replaces the in-flight error on purpose, carrying it, so
                    # the stray id reaches the caller's failure line.
                    raise RuntimeError(
                        "%s; ALSO the temp Slides copy %s beside the source could not "
                        "be trashed (%s); trash it by hand"
                        % (body_error, presentation_id, e)) from body_error
    if cleanup_error is not None and strict_cleanup:
        raise RuntimeError("rendered, but the temp Slides copy %s beside the source "
                           "could not be trashed (%s); trash it by hand"
                           % (presentation_id, cleanup_error))
    return out_path
