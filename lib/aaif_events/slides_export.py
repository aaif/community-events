"""Render a single slide of a Drive-hosted .pptx to PNG via the Slides API,
instead of a local LibreOffice conversion. LibreOffice substitutes local
system fonts for the deck's actual brand fonts whenever the render machine
doesn't have those fonts installed (true for effectively every machine but
the original designer's), silently producing wrong-looking exports; Google's
own renderer (used here) does not.

Requires the `gws` CLI (Drive + Slides scopes) on PATH and authenticated.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from aaif_events.report_style import redact
from aaif_events.slack import scrubbed_env

_SLIDES_MIME = "application/vnd.google-apps.presentation"

# Mirrors skills/aaif-create-chapter/scripts/create_chapter.py's _gws/gws_json —
# same retry list and empty/non-JSON-output guards, so a transient Drive/Slides
# hiccup here self-heals the same way it does for the rest of the toolkit.
_TRANSIENT = ("timed out", "internalError", "HTTP request failed",
              "Connection", "temporarily", "rateLimit", "userRateLimit",
              "backendError", "503", "500", "502")


def _gws(cmd, retries=5):
    for i in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True, env=scrubbed_env())
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < retries - 1 and any(k in msg for k in _TRANSIENT):
            time.sleep(2 * (i + 1))
            continue
        # Bounded and redacted: gws has printed its auth state on failure, and
        # this message ends up in terminals and bug reports.
        raise RuntimeError("gws failed (%s) for %s: %s"
                          % (r.returncode, " ".join(cmd)[:200], redact(msg.strip())))


def _gws_json(*args, params=None, body=None):
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = _gws(cmd)
    s = "\n".join(l for l in out.split("\n") if "keyring backend" not in l).strip()
    if not s:
        raise RuntimeError("gws produced no JSON output for: %s" % " ".join(args))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise RuntimeError("gws returned non-JSON output for %s: %s"
                           % (" ".join(args), redact(s, 200)))


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
        copy = _gws_json("drive", "files", "copy",
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
