#!/usr/bin/env python3
"""Bring the "THE PROJECTS" roster on the About slide up to the projects AAIF
actually hosts.

The decks were drawn when the foundation hosted four projects, so every chapter's
About slide reads `MCP · goose · AGENTS.md · agentgateway`. aaif.io/projects now
lists six — Agent2Agent and Agent Router joined — and a roster that is two short
is the kind of stale that an audience checks against the website mid-talk.

The roster is found **structurally**: the `THE PROJECTS` eyebrow, then the text
shape directly below it on the same left edge. Matching the old roster string
instead would miss a deck an organizer had already half-corrected, and matching
"the shape with the project names in it" is what the eyebrow already says.

Two guards, because this rewrites copy a human may have touched:

- A roster whose items are not all names on ROSTER's own list is **left alone
  and reported**. That is a chapter that wrote its own line (a local project, a
  different ordering with commentary), and overwriting it would be a silent
  edit of someone's words. Adding a project to `PROJECTS` therefore never
  clobbers a hand-written line — it skips it and tells you where to look.
- The line is one line by design, and two more names do not fit the box it was
  drawn in. The box is widened to fit, but never past the gutter in front of
  whatever is to its right (on the About slide, the `OPEN / BY DEFAULT` stat), so
  a widened roster cannot collide with the stat column. If even that is too
  narrow, the run is stepped down in point size instead and the file is flagged,
  because a shrunk roster is a design decision an operator should see, not a
  silent one.

Widths are estimated, not measured — there is no font engine here. `PROP_EM` is
a deliberate over-estimate of Instrument Sans Bold's average advance (measured
at ~0.50 em against a Slides render of the six-project line), and erring wide
only ever leaves the line short of the gutter, never through it.

Scope is **templates**, not the copies organizers have already made for a given
event: the `Event Templates…` / `Event Name` folders, found by `deck_estate`'s
walk and reported on by its estate-coverage checks — the same ones
`backfill_host_footer` uses. TemplateCity is in that set and is the one target
that must be hit: miss it and every chapter created afterwards is minted
four-projects-old.

The durable fix is the **TemplateCity edit**; this script is what brings the ~100
copies already out there up to it. Nothing detects drift on its own — when the
foundation takes on another project, edit `PROJECTS` and run a plan sweep.

Read-only by default: it prints the roster each file has and would get, and
changes nothing. There is no undo beyond Drive's own revision history, so read a
plan run before passing --write.

Usage:
  # Plan (default) — show every template's roster, write nothing:
  python backfill_projects.py

  # Apply to the whole estate:
  python backfill_projects.py --write

  # One chapter (matches the Drive folder name, case-insensitive):
  python backfill_projects.py --chapter "New York City" --write

  # Test the XML engine on a local .pptx, no Drive at all:
  python backfill_projects.py --rewrite-local ./Slides.pptx
"""
import argparse, os, re, shutil, sys, tempfile, zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc             # Drive plumbing + the zip repacker
import deck_estate as de                # the estate walk + the slide-XML primitives

#: The hosted projects, in the order aaif.io/projects lists them. Short forms,
#: because the line is a roster and not a catalogue: the deck has said "MCP"
#: rather than "Model Context Protocol" since it was drawn, and "A2A" is how
#: aaif.io itself abbreviates Agent2Agent.
PROJECTS = ("MCP", "goose", "AGENTS.md", "agentgateway", "A2A", "Agent Router")
SEP = " · "
ROSTER = SEP.join(PROJECTS)
#: Names a roster may hold and still count as the stock line this script owns.
#: It is PROJECTS plus the long forms of the same projects — nothing else, so a
#: chapter's own wording is skipped rather than overwritten. See the docstring.
KNOWN = {p.lower() for p in PROJECTS} | {
    "model context protocol", "agent2agent", "agents.md", "agent router"}

LABEL = "THE PROJECTS"
#: How far below the eyebrow the roster may sit and still be its roster. The
#: template's gap is 274320 EMU (0.3"); half an inch of slack covers a deck
#: whose spacing was nudged, without reaching the line *below* the roster.
MAX_LABEL_GAP = 457200
#: Clear space kept in front of whatever sits to the roster's right: 0.2",
#: which is what the six-project line leaves before the About slide's OPEN stat
#: at the template's own geometry. Widening this to a quarter inch pushes that
#: line one estimated character over the limit and steps the whole estate's
#: roster down a point size for no visible gain — the estimate is wide by ~4%
#: already (see PROP_EM), so the space actually rendered is far bigger.
GUTTER = 182880
#: Average advance of Instrument Sans Bold as a fraction of the point size,
#: rounded UP from ~0.50 measured on a Slides render. See the docstring.
PROP_EM = 0.52
#: Point sizes (in hundredths) the roster may be stepped down to when even the
#: full width will not hold it, largest first. Below the founding-members line's
#: 900 the roster stops reading as the stronger of the two, so that is the floor.
FALLBACK_SIZES = (1300, 1200, 1100, 1000, 900)

SZ_ATTR_RE = re.compile(r'\bsz="\d+"')
SLD_SZ_RE = re.compile(r'<p:sldSz[^>]*\bcx="(\d+)"')
#: Slide width used when the deck does not declare one: 10in, the 16:9 size
#: every deck in the estate is drawn at. Only the right-hand fallback in
#: available_cx() depends on it, and that fallback only applies to a roster with
#: nothing to its right at all.
DEFAULT_SLIDE_CX = 9144000


# ----------------------------------------------------------------------------
# Slide XML engine (pure, unit-testable — no Drive, no filesystem)
# ----------------------------------------------------------------------------
def text_width(text, sz):
    """Estimated rendered width in EMU of `text` at `sz` (hundredths of a pt),
    in the roster's own face. See PROP_EM."""
    return de.text_width(text, sz, PROP_EM)


def find_roster(shp):
    """The `shapes()` index of the roster line, or None.

    The roster is the nearest text shape *below* the `THE PROJECTS` eyebrow that
    starts on the same left edge. Both conditions matter: "nearest below" alone
    would pick up the founding-members line on a deck whose roster had been
    deleted, and re-rostering that line would replace a sentence about members
    with a list of projects."""
    label = next((i for i, s in enumerate(shp)
                  if s.box and s.text.upper() == LABEL), None)
    if label is None:
        return None
    lab = shp[label].box
    below = [i for i, s in enumerate(shp)
             if s.text and s.box and s.box.x == lab.x
             and lab.y < s.box.y <= lab.y + MAX_LABEL_GAP]
    return min(below, key=lambda i: shp[i].box.y) if below else None


def roster_items(text):
    """The roster read as a list of project names, or None if it is not a
    separated list at all. Both the template's "·" and a plain comma count — a
    deck re-typed by hand may use either."""
    parts = [p.strip() for p in re.split(r"[·,]", text)]
    return parts if all(parts) and len(parts) > 1 else None


def is_stock(text):
    """Whether this roster is the line this script owns, rather than a chapter's
    own wording. See KNOWN and the docstring's first guard."""
    items = roster_items(text)
    return bool(items) and all(i.lower() in KNOWN for i in items)


def available_cx(shp, roster, slide_cx):
    """How WIDE the roster may grow: up to the left edge of the nearest shape
    sharing any part of its horizontal band, less one gutter. Falls back to the
    slide's right edge when the band is clear all the way across. A width, not
    an x — the two differ by the roster's own left inset, and confusing them
    lets a roster grow one inset past the shape it must clear."""
    box = shp[roster].box
    right = min((s.box.x for i, s in enumerate(shp)
                 if i != roster and s.box and s.box.x > box.x
                 and s.box.y < box.y + box.cy and box.y < s.box.y + s.box.cy),
                default=slide_cx)
    return right - GUTTER - box.x


def fit(sz, limit_cx):
    """(size, width) for the roster inside `limit_cx`: its own size when it fits,
    else the largest fallback that does, else the floor. Never returns a width
    wider than `limit_cx` unless nothing fits, which the caller reports."""
    cands = (sz,) + tuple(s for s in FALLBACK_SIZES if s < sz)
    for cand in cands:
        w = text_width(ROSTER, cand)
        if w <= limit_cx:
            return cand, w
    return cands[-1], w


def rewrite_slide(xml, slide_cx=DEFAULT_SLIDE_CX):
    """Return (new xml, status, detail).

    status is one of: "none" (no roster on this slide), "current" (already the
    six, already fitting), "custom" (a hand-written roster, left alone),
    "updated", or "shrunk" (updated, but only by stepping the type down)."""
    shp = de.shapes(xml)
    roster = find_roster(shp)
    if roster is None:
        return xml, "none", ""
    old = shp[roster].text
    if not is_stock(old):
        return xml, "custom", old
    box = shp[roster].box
    sz = de.font_size(shp[roster].body)
    limit = available_cx(shp, roster, slide_cx)
    new_sz, width = fit(sz, limit)
    # Never narrow the box: it is the drawn design when the line already fits,
    # and shrinking it to the text would re-wrap a deck whose roster someone
    # later lengthens by a word.
    new_cx = max(box.cx, min(width, max(limit, 0)))
    if old == ROSTER and new_sz == sz and new_cx == box.cx:
        return xml, "current", old

    shrunk = new_sz != sz
    body = de.retext(shp[roster].body, ROSTER)
    if shrunk:
        body = SZ_ATTR_RE.sub('sz="%d"' % new_sz, body)
    if new_cx != box.cx:
        body = de.resize(body, new_cx)
    start, end = shp[roster].span
    detail = "%s -> %s" % (old, ROSTER)
    if shrunk:
        detail += "  (%dpt -> %dpt to fit)" % (sz / 100, new_sz / 100)
    return (xml[:start] + body + xml[end:],
            "shrunk" if shrunk else "updated", detail)


def rewrite_pptx(src, dst, repack=True):
    """Rewrite every slide of `src`, writing `dst` only when something changed.
    Returns {slide part: (status, detail)} for every slide that HAS a roster, so
    a caller can tell "already current" from "no About slide here".

    `repack=False` reports without producing `dst`. A plan run is the one people
    are told to do first, and repacking a deck it will never upload costs a full
    re-deflate of every part — the artwork included, which is most of the file —
    on nearly every template in the estate."""
    report, new_parts = {}, {}
    with zipfile.ZipFile(src) as zin:
        # The real slide width, not the default: available_cx() measures the
        # right margin from it, and a deck authored at another size would get a
        # margin from the wrong edge.
        try:
            m = SLD_SZ_RE.search(zin.read("ppt/presentation.xml").decode("utf-8"))
            slide_cx = int(m.group(1)) if m else DEFAULT_SLIDE_CX
        except KeyError:
            slide_cx = DEFAULT_SLIDE_CX
        for name in zin.namelist():
            if not de.SLIDE_RE.match(name):
                continue
            new, status, detail = rewrite_slide(zin.read(name).decode("utf-8"), slide_cx)
            if status != "none":
                report[name] = (status, detail)
            if status in ("updated", "shrunk"):
                new_parts[name] = new.encode("utf-8")
    if new_parts and repack:
        # create_chapter's repacker: it preserves each member's compression and
        # validates the result with testzip() before replacing the file, so a
        # corrupt repack can never reach gws_upload.
        shutil.copyfile(src, dst)
        cc._rewrite_zip(dst, lambda name, data: new_parts.get(name, data))
    return report


def rewrite_local(path):
    """--rewrite-local: run the XML engine on one file, no Drive access at all."""
    dst = re.sub(r"\.pptx$", "", path) + "-projects.pptx"
    report = rewrite_pptx(path, dst)
    if not report:
        print("%s: no THE PROJECTS roster found — nothing to rewrite" % path)
        return 0
    if any(s in ("updated", "shrunk") for s, _d in report.values()):
        print("%s -> %s" % (path, dst))
    for part, (status, detail) in sorted(report.items()):
        print("   %s: %s%s" % (part, status, "  " + detail if detail else ""))
    return 1 if any(s == "custom" for s, _d in report.values()) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="Actually upload the rewritten templates (default: plan only)")
    ap.add_argument("--chapter", help="Only templates whose Drive path contains this "
                                      "(case-insensitive), e.g. 'New York City'")
    ap.add_argument("--rewrite-local", metavar="PPTX",
                    help="Rewrite a local .pptx to <name>-projects.pptx; no Drive access")
    ap.add_argument("--jobs", type=int, default=6,
                    help="Concurrent Drive transfers, and folder listings during the "
                         "scan (default: 6)")
    args = ap.parse_args()

    if args.rewrite_local:
        return rewrite_local(args.rewrite_local)

    print("Scanning the Community Events tree for event templates...")
    entries, chapters, series, with_decks = de.walk_templates(de.COMMUNITY_ROOT,
                                                              max(args.jobs, 8))
    if args.chapter:
        needle = args.chapter.lower()
        entries = [e for e in entries if needle in e["path"].lower()]
    if not entries:
        print("No templates matched." if args.chapter else
              "No templates found — has the Community Events tree moved?")
        return 1
    print("Found %d template file(s).%s\n"
          % (len(entries), "" if args.write else "  PLAN ONLY — nothing will be written."))
    print("Roster: %s\n" % ROSTER)

    changed = current = clean = failed = 0
    custom, shrunk = [], []
    with tempfile.TemporaryDirectory() as tmpdir, \
            ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for entry, report, err in pool.map(
                lambda e: de.process(e, tmpdir, args.write,
                                     lambda src, dst: rewrite_pptx(src, dst, args.write)),
                entries):
            if err is not None:
                failed += 1
                print("  FAILED  %s\n            %s" % (entry["path"], err))
                continue
            statuses = {s for s, _d in report.values()}
            for part, (status, detail) in sorted(report.items()):
                if status == "custom":
                    custom.append((entry["path"], part, detail))
                elif status == "shrunk":
                    shrunk.append((entry["path"], part, detail))
            if statuses & {"updated", "shrunk"}:
                changed += 1
                print("  %s  %s  (%s)"
                      % ("REWRITTEN" if args.write else "would rewrite", entry["path"],
                         "; ".join("%s: %s" % (p.rsplit("/", 1)[-1], d)
                                   for p, (s, d) in sorted(report.items())
                                   if s in ("updated", "shrunk"))))
            elif "custom" in statuses:
                print("  SKIPPED   %s  (own roster: %s)"
                      % (entry["path"], "; ".join(d for s, d in report.values()
                                                  if s == "custom")))
            elif report:
                current += 1
            else:
                clean += 1

    print("\n%d rewritten, %d already current, %d without an About roster, %d failed."
          % (changed, current, clean, failed))

    # A folder-name match that stops matching looks exactly like a clean estate,
    # so name what the scan could not see instead of letting it read as done.
    attention = ["%s (%s): left alone — %s" % (p, part.rsplit("/", 1)[-1], d)
                 for p, part, d in custom]
    attention += ["%s (%s): %s" % (p, part.rsplit("/", 1)[-1], d)
                  for p, part, d in shrunk]
    if not args.chapter:
        attention += de.coverage_attention(
            entries, chapters, series, with_decks,
            "new chapters would still be minted with the old roster")
    if attention:
        print("\nATTENTION — read before calling this done:")
        for line in attention:
            print("  - %s" % line)
    if changed and not args.write:
        print("Re-run with --write to apply.")
    return 1 if (failed or attention) else 0


if __name__ == "__main__":
    sys.exit(main())
