#!/usr/bin/env python3
"""What every Drive-wide deck sweep needs: the estate walk and the slide-XML
primitives the sweeps edit OOXML with.

Three scripts now sweep the same estate — `backfill_host_footer.py`,
`backfill_projects.py`, and anything that follows them — and they all need the
same four things: the folder vocabulary that says which folders hold a
*template*, the walk that finds them, the per-file download / rewrite / upload
contract, and a handful of shape-level XML helpers. The first sweep owned all of
it and the second imported it from there, which made a migration script named
after a footer the de-facto home of the estate's Drive id. This module is that
home instead, so a sweep can be retired without taking the next one with it.

It is **not** `lib/aaif_events/`: a skill script that imports `lib` stops working
when the skill is zipped standalone (see AGENTS.md's two-tier note). This sits
beside the scripts that import it, inside the one skill that owns chapter decks,
and takes no dependency outside that folder except `create_chapter.py` — which
every one of these scripts already needs for Drive plumbing.

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

Box = collections.namedtuple("Box", "x y cx cy")
Shape = collections.namedtuple("Shape", "kind body span box text geom")


# ----------------------------------------------------------------------------
# Slide XML (pure — no Drive, no filesystem)
# ----------------------------------------------------------------------------
def shape_text(body):
    """The shape's visible text: runs concatenated, entities resolved, internal
    whitespace collapsed. OOXML runs join with NO separator — PowerPoint and
    Slides split a run mid-phrase for a spell-check or a language tag, so
    joining on " " would turn a split "HOSTED BY" into "HOSTED  BY" and defeat
    every exact-match test a sweep makes. Collapsing then absorbs a run that
    legitimately carries its own trailing space."""
    joined = "".join(html.unescape(t) for t in TEXT_RE.findall(body))
    return re.sub(r"\s+", " ", joined).strip()


def shapes(xml):
    """Every top-level shape, in document order. `box` is None for a shape with
    no xfrm of its own (it inherits placeholder geometry we must not move)."""
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


def font_size(body, default=1100):
    """The shape's first run size, in hundredths of a point."""
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


# ----------------------------------------------------------------------------
# Drive
# ----------------------------------------------------------------------------
def walk_templates(root, jobs=8):
    """(templates, chapter names, online-series names, names owning any .pptx).
    The name sets are how a caller tells "this chapter has nothing left to do"
    from "this chapter's template folder was renamed and never scanned" — a
    distinction the file counts alone cannot make, since both look like a clean
    estate. Dated per-event folders are not counted: those copies are
    deliberately out of scope, so reporting them would bury the real signal in
    noise."""
    found, chapters, series, with_decks = [], set(), set(), set()
    level, seen = [(root, "Community Events")], set()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while level:
            level = [(f, p) for f, p in level if f not in seen]
            seen.update(f for f, _ in level)
            nxt = []
            for path, kids in pool.map(lambda t: (t[1], cc.list_children(t[0])), level):
                leaf = path.rsplit("/", 1)[-1]
                is_template = bool(TEMPLATE_FOLDER_RE.match(leaf))
                owner = path.split("/")[2] if len(path.split("/")) > 2 else None
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
                            with_decks.add(owner)
                        if is_template:
                            found.append({"id": k["id"], "name": k["name"],
                                          "path": path + "/" + k["name"]})
                if leaf == SERIES_FOLDER:
                    series.update(k["name"] for k in kids if k["mimeType"] == cc.FOLDER)
            level = nxt
    # Dedup by Drive id: a file reachable under two parents would otherwise be
    # handed to two workers that share a scratch filename and race.
    seen_ids, unique = set(), []
    for f in found:
        if f["id"] not in seen_ids:
            seen_ids.add(f["id"])
            unique.append(f)
    return unique, chapters, series, with_decks


def owners(entries, folder):
    """The chapter / series names the scan actually reached under `folder`."""
    return {p[2] for p in (e["path"].split("/") for e in entries)
            if len(p) > 2 and p[1] == folder}


def coverage_attention(scanned, chapters, series, with_decks, template_city_note):
    """The lines a full-estate sweep must print before it may read as finished.

    A folder-name match that stops matching looks exactly like a clean estate,
    so each of these names something the scan could NOT see. `template_city_note`
    is the one sweep-specific sentence: what an unswept TemplateCity means for
    the chapters minted after this run. An empty list means the scan covered
    everything it knows how to check."""
    out = []
    for missing in sorted((chapters - owners(scanned, CHAPTERS_FOLDER)) & with_decks):
        out.append("chapter %r holds decks but contributed no template — "
                   "template folder renamed?" % missing)
    for missing in sorted((series - owners(scanned, SERIES_FOLDER)) & with_decks):
        out.append("online series %r holds decks but contributed no template — "
                   "template folder renamed?" % missing)
    if not chapters:
        out.append("no chapter folders found under %r — has it been renamed? "
                   "the per-chapter coverage check is disabled without it"
                   % CHAPTERS_FOLDER)
    if not any("/%s/" % TEMPLATE_CITY in e["path"] for e in scanned):
        out.append("%s was never reached — %s" % (TEMPLATE_CITY, template_city_note))
    return out


def process(entry, tmpdir, write, rewrite):
    """Download one template, run `rewrite(src, dst)` over it, and upload the
    result when `write` and the rewrite actually produced a file. Returns
    (entry, whatever `rewrite` reported, error or None) — one bad file must not
    stop a sweep, so every failure comes back as that third value."""
    # Named by Drive id, not by path: paths are truncated to stay inside the
    # filename limit, and two truncated paths that collide would have concurrent
    # workers overwriting each other's download.
    src = os.path.join(tmpdir, "in-%s.pptx" % entry["id"])
    dst = os.path.join(tmpdir, "out-%s.pptx" % entry["id"])
    try:
        cc.gws_download(entry["id"], src)
        # gws writes the response body to --output even when the API returned an
        # error at exit 0, so a JSON error page would reach zipfile and surface
        # as a BadZipFile blaming the OOXML engine.
        if not os.path.exists(src) or os.path.getsize(src) == 0:
            raise RuntimeError("download wrote no file")
        if not zipfile.is_zipfile(src):
            raise RuntimeError("download is not a .pptx (%d bytes) — an error body, "
                               "not the template" % os.path.getsize(src))
        report = rewrite(src, dst)
        # The file, not the report: a plan run reports what it WOULD change
        # without writing `dst`, and uploading on a truthy report would push a
        # file that was never rewritten.
        if write and os.path.exists(dst):
            cc.gws_upload(entry["id"], dst, cc.PPTX)
        return entry, report, None
    except Exception as e:                       # one bad file must not stop the run
        # Prefix the class: this catch spans the XML engine as well as the two
        # transfers, and a bare message makes a ValueError in the rewrite read as
        # a network blip. Never return a bare str() — an exception whose str() is
        # empty (MemoryError, a bare raise) would be falsy and the caller would
        # count the file as already clean, printing nothing at all.
        return entry, {}, "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        for p in (src, dst):
            if os.path.exists(p):
                os.remove(p)
