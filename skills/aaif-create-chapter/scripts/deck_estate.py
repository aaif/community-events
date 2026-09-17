#!/usr/bin/env python3
"""What every Drive-wide deck sweep needs: the estate walk and the slide-XML
primitives the sweeps edit OOXML with.

Two scripts sweep the estate this way — `backfill_host_footer.py` and
`backfill_projects.py` — and anything that follows them needs the same four
things: the folder vocabulary that says which folders hold a *template*, the
walk that finds them, the per-file download / rewrite / upload contract, and a
handful of shape-level XML helpers. The first sweep owned all of it and the
second imported it from there, which made a migration script named after a
footer the de-facto home of the estate's Drive id. This module is that home
instead, so a sweep can be retired without taking the next one with it.
(`restyle_design_system.py` is a third estate sweep that predates this module
and still keeps its own root id and its own walk; folding it in is a separate
change, so the root id is not yet single-homed in this repo.)

It is **not** `lib/aaif_events/`: a skill script that imports `lib` stops working
when the skill is zipped standalone (see AGENTS.md's two-tier note). This sits
beside the scripts that import it and takes no dependency outside this folder —
only `create_chapter.py` next to it, which every one of these scripts already
needs for Drive plumbing, and which resolves through the importing script's
`sys.path` shim rather than one here.

Nothing here knows what a sweep is *for*. The rules about what to look for and
what to write are each sweep's own; this module only knows how to find the files
and how to read and edit a shape.
"""
import collections, html, os, re, zipfile
from concurrent.futures import ThreadPoolExecutor

import create_chapter as cc      # Drive plumbing + the shared OOXML primitives

COMMUNITY_ROOT = "1Z1M-xk0S16sksS1IBNm9OG6Ia22Yql6f"   # the "Community Events" folder
CHAPTERS_FOLDER = "Chapters"     # its child holding one folder per chapter
# The folder create_chapter.py clones for every new chapter. It is the single
# most important target in any sweep — miss it and every chapter created after
# the migration is minted with the old content — so a caller asserts it was
# reached rather than trusting it to fall out of the walk.
TEMPLATE_CITY = "TemplateCity"
# The folders that hold a *template* rather than one event's copy of it. The
# chapter tree names them "Event Templates (Copy for Each Event)", the online
# series "Event Template", and the shared Templates folder "Event Name".
# Matching on a folder NAME is fragile — that folder has been renamed once
# already — so coverage_attention() reports any chapter that contributed no
# template at all, which is what a future rename would look like.
TEMPLATE_FOLDER_RE = re.compile(r"^(event templates?\b.*|event name)$", re.I)
SERIES_FOLDER = "Online"         # its children are the online series

EMU_PER_PT = 12700

# Reuse create_chapter's offset pattern rather than restating it: its `\s*`
# tolerates a re-saved " />", which Drive emits after anyone opens a deck in
# Google Slides. A private copy of this regex silently drops those shapes.
OFF_RE = cc.OFF_RE
EXT_RE = re.compile(r'<a:ext cx="(\d+)" cy="(\d+)"\s*/>')
# Widened from create_chapter's SP_RE to cover <p:pic> as well; the backreference
# stops the two kinds matching across each other. The `[^>]*` attribute tolerance
# is inherited from SP_RE, not added here — a re-saved slide writes <p:sp ...>.
# Like SP_RE this assumes a flat spTree: a shape nested in a <p:grpSp> would be
# matched with group-relative offsets. These templates have none.
SHAPE_RE = re.compile(r"<p:(sp|pic)\b[^>]*>.*?</p:\1>", re.S)
TEXT_RE = re.compile(r"<a:t>(.*?)</a:t>", re.S)
GEOM_RE = re.compile(r'<a:prstGeom prst="(\w+)"')
SZ_RE = re.compile(r'<a:rPr\b[^>]*\bsz="(\d+)"')
SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml$")
# Control characters a deck's own text may carry. Slide copy is written by ~92
# organizer accounts and printed straight to an operator's terminal, so an ESC
# sequence in a shape could repaint or scroll away the ATTENTION and FAILED
# lines of a 100-file report. Stripped at the source, in shape_text, because
# every sweep reads text through it and only some of them echo it.
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

Box = collections.namedtuple("Box", "x y cx cy")
Shape = collections.namedtuple("Shape", "kind body span box text geom")
#: walk_templates' result. A namedtuple, not four bare values: `chapters`,
#: `series` and `with_decks` are all sets of folder names, so positional
#: unpacking lets two of them be swapped at a call site with no error — and the
#: coverage checks below would then intersect chapter names with series names,
#: come up empty, and report a renamed folder as a clean estate. That is the
#: exact failure the sets exist to catch.
#: `unlisted` holds folders whose listing failed, so a partial scan cannot pass
#: itself off as a complete one.
Scan = collections.namedtuple("Scan", "templates chapters series with_decks unlisted")
#: process()'s result. Named for the same reason: `report` and `error` are both
#: truthy-ish and swapping them turns every template into a FAILED line.
Result = collections.namedtuple("Result", "entry report error")
#: One template in the estate. `section` and `owner` are parsed once, here,
#: rather than by each consumer re-splitting `path` on "/".
Entry = collections.namedtuple("Entry", "id name path section owner")


# ----------------------------------------------------------------------------
# Slide XML (pure — no Drive, no filesystem)
# ----------------------------------------------------------------------------
def shape_text(body):
    """The shape's visible text: runs concatenated, entities resolved, control
    characters dropped, internal whitespace collapsed. OOXML runs join with NO
    separator — PowerPoint and Slides split a run mid-phrase for a spell-check
    or a language tag, so joining on " " would turn a split "HOSTED BY" into
    "HOSTED  BY" and defeat every exact-match test a sweep makes. Collapsing
    then absorbs a run that legitimately carries its own trailing space. See
    CTRL_RE for why the control characters go."""
    joined = "".join(html.unescape(t) for t in TEXT_RE.findall(body))
    return re.sub(r"\s+", " ", CTRL_RE.sub("", joined)).strip()


def shapes(xml):
    """Every top-level shape, in document order. `box` is None for a shape with
    no xfrm of its own (it inherits placeholder geometry we must not move, and
    which nothing here can resolve — a sweep that measures clearance should say
    so rather than read the shape as absent; see unmeasured())."""
    out = []
    for m in SHAPE_RE.finditer(xml):
        b = m.group(0)
        off, ext = OFF_RE.search(b), EXT_RE.search(b)
        box = Box(int(off.group(1)), int(off.group(2)),
                  int(ext.group(1)), int(ext.group(2))) if off and ext else None
        geom = GEOM_RE.search(b)
        out.append(Shape(m.group(1), b, m.span(), box, shape_text(b),
                         geom.group(1) if geom else ""))
    return out


def unmeasured(shp):
    """How many text shapes carry no geometry of their own. A sweep that widens
    a shape toward its neighbours cannot see these at all, so the count is what
    it reports instead of claiming the space was clear."""
    return sum(1 for s in shp if s.text and s.box is None)


def font_size(body, default=1100):
    """The shape's first run size, in hundredths of a point. `default` is what
    to report when the run declares none and inherits its size — pass None to
    tell the two apart, which matters for any caller that intends to WRITE a
    size back: there is no sz attribute to rewrite on such a run, so a caller
    that assumes the default silently fails to resize it."""
    m = SZ_RE.search(body)
    return int(m.group(1)) if m else default


def text_width(text, sz, em):
    """Estimated rendered width in EMU of `text` at `sz` (hundredths of a point)
    in a face whose average advance is `em` of the point size. There is no font
    engine here: `em` is each caller's own measurement of its own face, and a
    caller that rounds it UP errs by leaving space, never by overrunning."""
    return int(len(text) * em * sz / 100 * EMU_PER_PT)


def move(body, x):
    return OFF_RE.sub(lambda m: '<a:off x="%d" y="%s"/>' % (x, m.group(2)), body, count=1)


def resize(body, cx):
    return EXT_RE.sub(lambda m: '<a:ext cx="%d" cy="%s"/>' % (cx, m.group(2)), body, count=1)


def retext(body, text):
    """Put `text` in the shape's first run and empty the rest, so the run's
    formatting — and any mid-word run splitting — survives the swap. Callers
    pass text with no leading or trailing space, which is why this does not need
    create_chapter's xml:space="preserve" guard."""
    seen = [False]

    def one(_m):
        if seen[0]:
            return "<a:t></a:t>"
        seen[0] = True
        return "<a:t>%s</a:t>" % html.escape(text)
    return TEXT_RE.sub(one, body)


def splice(xml, shp, edits):
    """`xml` with each `{shapes() index: new body}` in `edits` applied.

    Use this rather than slicing on a Shape's `span` directly. Every span is an
    offset into the ORIGINAL string, so applying one edit invalidates every span
    after it: a caller splicing two shapes in the order it found them writes a
    deck with duplicated or truncated XML — which still zips, still validates,
    still uploads, and breaks only when a human opens the slide. Walking the
    spans in ascending order with a cursor is what makes a multi-shape edit
    safe, and it lives here so no caller has to remember it. An empty body
    deletes the shape."""
    out, cursor = [], 0
    for i in sorted(edits):
        start, end = shp[i].span
        out.append(xml[cursor:start])
        out.append(edits[i])
        cursor = end
    out.append(xml[cursor:])
    return "".join(out)


# ----------------------------------------------------------------------------
# Drive
# ----------------------------------------------------------------------------
def walk_templates(root, jobs=8):
    """Scan the estate below `root`. Returns a Scan.

    The name sets are how a caller tells "this chapter has nothing left to do"
    from "this chapter's template folder was renamed and never scanned" — a
    distinction the file counts alone cannot make, since both look like a clean
    estate. Dated per-event folders are not counted: those copies are
    deliberately out of scope, so reporting them would bury the real signal in
    noise. `with_decks` holds (section, owner) pairs rather than bare names so
    that a chapter and an online series sharing a name cannot be mistaken for
    each other — the estate already has near-duplicate names across sections.

    A folder whose listing fails is recorded in `unlisted` rather than aborting
    the walk: one transient listing error on one of ~500 folders should cost the
    caller that subtree, loudly, not the whole run."""
    found, chapters, series, with_decks, unlisted = [], set(), set(), set(), []
    level, seen = [(root, "Community Events")], set()

    def listing(target):
        fid, path = target
        try:
            return path, cc.list_children(fid), None
        except Exception as e:                   # one folder must not stop the walk
            return path, [], "%s: %s" % (type(e).__name__, str(e)[:200])

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while level:
            level = [(f, p) for f, p in level if f not in seen]
            seen.update(f for f, _ in level)
            nxt = []
            for path, kids, err in pool.map(listing, level):
                if err is not None:
                    unlisted.append((path, err))
                    continue
                parts = path.split("/")
                leaf = parts[-1]
                is_template = bool(TEMPLATE_FOLDER_RE.match(leaf))
                section = parts[1] if len(parts) > 1 else None
                owner = parts[2] if len(parts) > 2 else None
                for k in kids:
                    if k["mimeType"] == cc.FOLDER:
                        nxt.append((k["id"], path + "/" + k["name"]))
                        if leaf == CHAPTERS_FOLDER:
                            chapters.add(k["name"])
                    elif k["mimeType"] == cc.PPTX:
                        # Remember which chapter/series owns ANY .pptx, at any
                        # depth. A renamed template folder still leaves decks
                        # behind; a folder that simply has no decks (a podcast
                        # series, say) is not a rename and must not cry wolf.
                        if owner:
                            with_decks.add((section, owner))
                        if is_template:
                            found.append(Entry(k["id"], k["name"],
                                               path + "/" + k["name"], section, owner))
                if leaf == SERIES_FOLDER:
                    series.update(k["name"] for k in kids if k["mimeType"] == cc.FOLDER)
            level = nxt
    # Dedup by Drive id: a file reachable under two parents would otherwise be
    # handed to two workers that share a scratch filename and race.
    seen_ids, unique = set(), []
    for f in found:
        if f.id not in seen_ids:
            seen_ids.add(f.id)
            unique.append(f)
    return Scan(unique, chapters, series, with_decks, unlisted)


def owners(entries, section):
    """The chapter / series names the scan actually reached in `section`."""
    return {e.owner for e in entries if e.section == section and e.owner}


def coverage_attention(scan, template_city_note):
    """The lines a full-estate sweep must print before it may read as finished.

    A folder-name match that stops matching looks exactly like a clean estate,
    so each of these names something the scan could NOT see. `template_city_note`
    is the one sweep-specific sentence: what an unswept TemplateCity means for
    the chapters minted after this run. An empty list means the scan covered
    everything it knows how to check."""
    out = ["%s could not be listed — everything below it was skipped: %s"
           % (path, err) for path, err in scan.unlisted]
    reached = owners(scan.templates, CHAPTERS_FOLDER)
    for missing in sorted(c for c in scan.chapters - reached
                          if (CHAPTERS_FOLDER, c) in scan.with_decks):
        out.append("chapter %r holds decks but contributed no template — "
                   "template folder renamed?" % missing)
    reached_series = owners(scan.templates, SERIES_FOLDER)
    for missing in sorted(s for s in scan.series - reached_series
                          if (SERIES_FOLDER, s) in scan.with_decks):
        out.append("online series %r holds decks but contributed no template — "
                   "template folder renamed?" % missing)
    if not scan.chapters:
        out.append("no chapter folders found under %r — has it been renamed? "
                   "the per-chapter coverage check is disabled without it"
                   % CHAPTERS_FOLDER)
    # An exact owner match, not a substring of the path: a folder named
    # "TemplateCity Archive" would satisfy `"/TemplateCity/" in path` and
    # suppress the one check this module calls the most important in any sweep.
    if not any(e.owner == TEMPLATE_CITY for e in scan.templates):
        out.append("%s was never reached — %s" % (TEMPLATE_CITY, template_city_note))
    return out


def _trim(e, head=120, tail=200):
    """An exception's message, keeping BOTH ends. gws leads with keyring and
    auth noise and puts the Google reason (insufficientFilePermissions,
    storageQuotaExceeded, rateLimitExceeded) last, so a head-only cut removes
    the only actionable part."""
    s = str(e)
    if len(s) <= head + tail + 3:
        return s
    return s[:head] + " … " + s[-tail:]


def process(entry, tmpdir, write, rewrite):
    """Download one template, run `rewrite(src, dst)` over it, and upload the
    result when `write` and the rewrite actually produced a file. Returns a
    Result — one bad file must not stop a sweep, so every failure comes back as
    `error` rather than raised.

    The three phases are caught separately because they call for different
    responses: a download failure is an environment problem and re-running is
    safe; a rewrite failure is a bug in the sweep that will hit every file
    identically; an upload failure leaves this file's state in Drive UNKNOWN.
    One combined message made all three read like a Drive outage."""
    # Named by Drive id, not by path: paths are truncated to stay inside the
    # filename limit, and two truncated paths that collide would have concurrent
    # workers overwriting each other's download.
    src = os.path.join(tmpdir, "in-%s.pptx" % entry.id)
    dst = os.path.join(tmpdir, "out-%s.pptx" % entry.id)
    try:
        try:
            cc.gws_download(entry.id, src)
            # gws writes the response body to --output even when the API
            # returned an error at exit 0, so a JSON error page would reach
            # zipfile and surface as a BadZipFile blaming the OOXML engine.
            if not os.path.exists(src) or os.path.getsize(src) == 0:
                raise RuntimeError("download wrote no file")
            if not zipfile.is_zipfile(src):
                raise RuntimeError("download is not a .pptx (%d bytes) — an error "
                                   "body, not the template" % os.path.getsize(src))
        except Exception as e:
            # Never return a bare str(): an exception whose message is empty
            # (MemoryError, a bare raise) would be falsy and the caller would
            # count the file as already clean, printing nothing at all.
            return Result(entry, None, "DOWNLOAD %s: %s" % (type(e).__name__, _trim(e)))

        try:
            report = rewrite(src, dst)
        except Exception as e:
            return Result(entry, None, "REWRITE %s: %s  (a bug in this script, not "
                                       "Drive — re-running will not help)"
                          % (type(e).__name__, _trim(e)))

        try:
            # The file, not the report: a rewrite may report work it did not
            # write (a plan run that skips the repack), and uploading on a
            # truthy report would push a file that was never rewritten.
            if write and os.path.exists(dst):
                upload(entry.id, dst)
        except Exception as e:
            # `report`, not None: the rewrite DID succeed, and losing that makes
            # the failure read as if nothing had been attempted.
            return Result(entry, report, "UPLOAD %s: %s  (this file's state in Drive "
                                         "is UNKNOWN — check it before re-running)"
                          % (type(e).__name__, _trim(e)))
        return Result(entry, report, None)
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            except OSError as e:
                # Never raise from `finally`: it would replace the return value
                # that has already been computed and escape through pool.map,
                # aborting a --write sweep with no summary and no record of
                # which files were already uploaded.
                print("  warning: could not clean up %s: %s" % (p, e))


def upload(file_id, path):
    """Upload `path` over the Drive file `file_id`, and verify Drive took it.

    The download side already refuses to trust gws's exit code, because gws
    writes the API's response body to its output and exits 0 on an error. The
    upload side needs the same distrust for a worse failure: a 403 on one file
    in a locked-down folder tree would otherwise print REWRITTEN, exit 0, and
    leave the deck stale — a sweep that reports success over an untouched file
    is one nobody ever runs again."""
    before = cc.gws_json("drive", "files", "get",
                         params={"fileId": file_id, "fields": "modifiedTime,size",
                                 "supportsAllDrives": True})
    cc.gws_upload(file_id, path, cc.PPTX)
    after = cc.gws_json("drive", "files", "get",
                        params={"fileId": file_id, "fields": "modifiedTime,size",
                                "supportsAllDrives": True})
    if after.get("modifiedTime") == before.get("modifiedTime"):
        raise RuntimeError("upload reported success but Drive still shows the file "
                           "unchanged (modified %s, %s bytes) — did gws exit 0 on an "
                           "API error?" % (after.get("modifiedTime"), after.get("size")))
