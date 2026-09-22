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
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):   # gone on success (os.replace consumed it)
            os.remove(tmp)


def render_slide_png(file_id, out_path, slide_index=0, thumbnail_size="WIDTH2000_PX"):
    """Export slide `slide_index` (0-based) of the Drive pptx `file_id` to a PNG
    at `out_path`. Makes a throwaway Google Slides copy to render from (Slides
    thumbnails only work on native Slides files, not stored .pptx blobs) and
    trashes it afterward — the source file is never modified."""
    presentation_id = None
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

        presentation = _gws_json("slides", "presentations", "get",
                                  params={"presentationId": presentation_id, "fields": "slides.objectId"})
        page_object_id = presentation["slides"][slide_index]["objectId"]

        thumb = _gws_json("slides", "presentations", "pages", "getThumbnail",
                           params={"presentationId": presentation_id, "pageObjectId": page_object_id,
                                   "thumbnailProperties.thumbnailSize": thumbnail_size})
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        _download(thumb["contentUrl"], out_path)
        return out_path
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
