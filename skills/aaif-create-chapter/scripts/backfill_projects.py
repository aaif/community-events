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

- A roster whose items are not all on `KNOWN` — `PROJECTS` plus the long forms
  of the same projects — is **left alone and reported**. That is a chapter that wrote its own line (a local project, a
  different ordering with commentary), and overwriting it would be a silent
  edit of someone's words. Adding a project to `PROJECTS` therefore never
  clobbers a hand-written line — it skips it and tells you where to look.
- The line is one line by design, and two more names do not fit the box it was
  drawn in. The box is widened to fit, but never past the gutter in front of
  whatever is to its right (on the About slide, the `OPEN / BY DEFAULT` stat), so
  a widened roster cannot collide with the stat column. If even that is too
  narrow, the run is stepped down in point size instead, and a roster that
  still does not fit at the smallest size this script will use is reported as
  **overflowing** rather than quietly written — a roster that is shrunk, or that
  will collide, is a design decision an operator should see.

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

The durable fix is the **TemplateCity edit**; this script is what brings the
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

  # One chapter (matches anywhere in the Drive path, case-insensitive):
  python backfill_projects.py --chapter "New York City" --write

  # Test the XML engine on a local .pptx, no Drive at all:
  python backfill_projects.py --rewrite-local ./Slides.pptx
"""
import argparse, collections, os, re, shutil, sys, tempfile, zipfile
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
#: How far the roster's left edge may differ from the eyebrow's and still count
#: as the same column: 0.02". Google Slides rewrites geometry on every open, so
#: an exact compare reads a rounding nudge as "this deck has no roster".
LEFT_TOLERANCE = 18288
#: Clear space kept in front of whatever sits to the roster's right: 0.2",
#: which is what the six-project line leaves before the About slide's OPEN stat
#: at the template's own geometry. Widening this to a quarter inch pushes that
#: line just past the limit — by a fraction of one estimated character — and so
#: steps the whole estate's roster down a point size for no visible gain; the
#: estimate is wide by ~4% already (see PROP_EM), so the rendered gap is bigger
#: than either number suggests.
GUTTER = 182880
#: Average advance of Instrument Sans Bold as a fraction of the point size,
#: rounded UP from ~0.50 measured on a Slides render. See the docstring.
PROP_EM = 0.52
#: Point sizes (in hundredths) the roster may be stepped down to when even the
#: full width will not hold it, largest first. Below the founding-members line's
#: 900 the roster stops reading as the stronger of the two, so that is the floor
#: — for a roster that starts above it. Nothing steps a roster UP, so a roster
#: already at or below 900 has no candidate but its own size, and one that still
#: overruns is reported rather than resized (see fit).
FALLBACK_SIZES = (1300, 1200, 1100, 1000, 900)

#: The statuses rewrite_slide may return, and the subset that means "this slide's
#: new XML must be kept". Declared once: the "did it change" test is made in four
#: places, and a status added later without updating all four silently drops a
#: rewritten part from the upload while the summary prints "already current".
STATUSES = ("none", "orphan", "current", "custom", "updated", "shrunk")
CHANGED = {"updated", "shrunk"}

SZ_ATTR_RE = re.compile(r'\bsz="\d+"')
SLD_SZ_RE = re.compile(r'<p:sldSz[^>]*\bcx="(\d+)"')
#: Slide width used when the deck does not declare one: 10in, the 16:9 size
#: every deck in the estate is drawn at. Only the right-hand fallback in
#: available_cx() depends on it, and that fallback only applies to a roster with
#: nothing to its right at all.
DEFAULT_SLIDE_CX = 9144000

#: Exit codes. 0 is "nothing left to do"; a plan run that found work is not a
#: failure but must not read as done; anything the scan could not cover — or any
#: file a human has to look at — is the loud one.
EXIT_PENDING, EXIT_COVERAGE = 1, 2
#: The template that carries the About slide. Every chapter folder also holds
#: hero, square-hero and carousel decks, which have no About slide and are
#: rightly reported as having no roster — so they are the wrong denominator for
#: the two "has the matcher stopped matching?" checks below, and the wrong file
#: to judge TemplateCity by.
ROSTER_DECK = "Slides.pptx"
#: Above this share of ROSTER_DECK files showing no roster at all, the matcher
#: is the suspect rather than the estate: they are all clones of one design.
UNRECOGNISED_RATIO = 0.2


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
    with a list of projects.

    The left edge is matched within LEFT_TOLERANCE rather than exactly: Google
    Slides rewrites geometry when anyone opens a deck, and an exact EMU compare
    turns a roster nudged by a rounding error into "this deck has no roster" —
    which the caller would otherwise count as a clean file."""
    label = next((i for i, s in enumerate(shp)
                  if s.box and s.text.upper() == LABEL), None)
    if label is None:
        return None
    lab = shp[label].box
    below = [i for i, s in enumerate(shp)
             if s.text and s.box and abs(s.box.x - lab.x) <= LEFT_TOLERANCE
             and lab.y < s.box.y <= lab.y + MAX_LABEL_GAP]
    return min(below, key=lambda i: shp[i].box.y) if below else None


def has_label(shp):
    """Whether the `THE PROJECTS` eyebrow is on this slide at all. What tells an
    About slide whose roster moved out of reach (report it — the matcher has
    stopped matching) from a slide that simply is not the About slide."""
    return any(s.box and s.text.upper() == LABEL for s in shp)


def roster_items(text):
    """The roster read as a list of names, or [] if it is not a list at all.
    Both the template's "·" and a plain comma count — a deck re-typed by hand
    may use either. A single name IS a list of one: a chapter that trimmed the
    line to "MCP" wrote a roster, not prose, and reporting it as someone's own
    wording sends the operator looking for words that are not there."""
    parts = [p.strip() for p in re.split(r"[·,]", text)]
    return parts if parts and all(parts) else []


def is_stock(text):
    """Whether this roster is the line this script owns, rather than a chapter's
    own wording. See KNOWN and the docstring's first guard."""
    items = roster_items(text)
    return bool(items) and all(i.lower() in KNOWN for i in items)


def available_cx(shp, roster, slide_cx):
    """How WIDE the roster may grow: up to the left edge of the nearest shape
    sharing any part of its horizontal band, less one gutter. Falls back to the
    slide's right edge when the band is clear all the way across. A width, not
    an x — the two differ by the roster's own left edge (`box.x`), and confusing
    them lets a roster grow one whole left-offset past the shape it must clear.

    Shapes with no geometry of their own are invisible here: their position is
    inherited from a layout this script does not resolve. rewrite_slide counts
    them (see de.unmeasured) so a widened roster says what it could not see,
    rather than reporting empty space it never measured."""
    box = shp[roster].box
    right = min((s.box.x for i, s in enumerate(shp)
                 if i != roster and s.box and s.box.x > box.x
                 and s.box.y < box.y + box.cy and box.y < s.box.y + s.box.cy),
                default=slide_cx)
    return right - GUTTER - box.x


def fit(sz, limit_cx):
    """(size, width, overflows) for the roster inside `limit_cx`.

    Its own size when it fits, else the largest fallback that does, else the
    smallest size it tried — the FALLBACK_SIZES floor when the roster started
    above it, and the roster's own size when it started at or below it, because
    nothing steps a roster UP.

    `overflows` is returned rather than left for the caller to infer from "did
    the size change": a roster already at the smallest size changes no size and
    still overruns, which is exactly the case an operator must be told about."""
    cands = (sz,) + tuple(s for s in FALLBACK_SIZES if s < sz)
    for cand in cands:
        w = text_width(ROSTER, cand)
        if w <= limit_cx:
            return cand, w, False
    return cands[-1], w, True


def rewrite_slide(xml, slide_cx=DEFAULT_SLIDE_CX):
    """Return (new xml, status, detail). `status` is one of STATUSES:

    - "none"    — not the About slide: no `THE PROJECTS` eyebrow on it.
    - "orphan"  — the eyebrow is here but no roster was found under it. The
                  matcher has stopped matching, which looks identical to a clean
                  file unless it is reported, so the caller must surface it.
    - "current" — already the six, already fitting.
    - "custom"  — a hand-written roster, left alone.
    - "updated" / "shrunk" — rewritten, the second only by stepping the type
                  down. Both keep the new XML (see CHANGED)."""
    shp = de.shapes(xml)
    roster = find_roster(shp)
    if roster is None:
        return xml, ("orphan" if has_label(shp) else "none"), ""
    old = shp[roster].text
    if not is_stock(old):
        return xml, "custom", old
    box = shp[roster].box
    # default=None, not the 1100 fallback: a run that declares no size inherits
    # one, and there is no sz attribute to rewrite on it — so a step-down would
    # report a size change that never happened. Such a roster is measured at the
    # fallback but never resized; see the `resizable` guard below.
    sz = de.font_size(shp[roster].body, default=None)
    resizable = sz is not None
    sz = sz if resizable else de.font_size(shp[roster].body)
    limit = available_cx(shp, roster, slide_cx)
    new_sz, width, overflows = fit(sz, limit)
    if not resizable:
        # No sz attribute to rewrite, so the ladder is not available: the line
        # is measured at the size it actually inherits, and whether THAT fits
        # is the only question left.
        new_sz = sz
        width = text_width(ROSTER, sz)
        overflows = width > limit
    # Never narrow the box: it is the drawn design when the line already fits,
    # and shrinking it to the text would re-wrap a deck whose roster someone
    # later lengthens by a word.
    new_cx = max(box.cx, min(width, max(limit, 0)))
    shrunk = new_sz != sz
    if old == ROSTER and not shrunk and new_cx == box.cx and not overflows:
        return xml, "current", old

    body = de.retext(shp[roster].body, ROSTER)
    if shrunk:
        body = SZ_ATTR_RE.sub('sz="%d"' % new_sz, body)
    if new_cx != box.cx:
        body = de.resize(body, new_cx)

    if old == ROSTER:
        detail = "%s (roster unchanged; the box was)" % ROSTER
    else:
        detail = "%s -> %s" % (old, ROSTER)
    if shrunk:
        detail += "  (%dpt -> %dpt to fit)" % (sz / 100, new_sz / 100)
    if overflows:
        detail += ("  OVERFLOWS its band by %d EMU at %dpt — it will collide with "
                   "the shape to its right%s"
                   % (width - limit, new_sz / 100,
                      "" if resizable else "; its run declares no size, so this "
                                           "script cannot step it down"))
    blind = de.unmeasured(shp)
    if blind and new_cx != box.cx:
        detail += ("  (widened past %d shape(s) whose geometry is inherited and "
                   "could not be measured)" % blind)
    return (de.splice(xml, shp, {roster: body}),
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
        slide_cx, why = DEFAULT_SLIDE_CX, ""
        try:
            m = SLD_SZ_RE.search(zin.read("ppt/presentation.xml").decode("utf-8"))
            if m:
                slide_cx = int(m.group(1))
            else:
                why = "ppt/presentation.xml declares no <p:sldSz cx=...>"
        except KeyError:
            why = "the package has no ppt/presentation.xml"
        if why:
            # Reported, not just defaulted: a 4:3 deck measured against a 10in
            # slide is granted 2.5in of room past its own right edge, and the
            # roster runs off the slide with nothing in the output to say so.
            report["<presentation>"] = ("assumed-width",
                "%s — the right margin was measured from the default %d EMU (10in); "
                "if this deck is not that wide the roster may have been widened past "
                "its slide edge" % (why, DEFAULT_SLIDE_CX))
        for name in zin.namelist():
            if not de.SLIDE_RE.match(name):
                continue
            new, status, detail = rewrite_slide(zin.read(name).decode("utf-8"), slide_cx)
            if status != "none":
                report[name] = (status, detail)
            if status in CHANGED:
                new_parts[name] = new.encode("utf-8")
    if new_parts and repack:
        # create_chapter's repacker: it preserves each member's compression and
        # validates the result with testzip() before replacing the file, so a
        # corrupt repack can never reach gws_upload.
        shutil.copyfile(src, dst)
        cc._rewrite_zip(dst, lambda name, data: new_parts.get(name, data))
    return report


def classify(report):
    """What one file's report means, as one word: "changed", "custom", "orphan",
    "current" or "clean". Every consumer of a status goes through here, so a
    status added to STATUSES without a rule lands in "unknown" and is printed,
    rather than falling into "already current" and disappearing."""
    statuses = {s for s, _d in report.values()}
    unknown = statuses - set(STATUSES) - {"assumed-width"}
    if unknown:
        return "unknown"
    if statuses & CHANGED:
        return "changed"
    for kind in ("custom", "orphan"):
        if kind in statuses:
            return kind
    return "current" if statuses else "clean"


def notices(report):
    """The (status, detail) pairs a human has to read even on a good run."""
    return [(s, d) for s, d in report.values()
            if s in ("custom", "orphan", "assumed-width") or "OVERFLOWS" in d
            or "to fit)" in d]


def estate_attention(counts, city):
    """The lines that say the *matcher* — not the estate — may be what changed.

    `coverage_attention` checks which folders the scan reached; these two check
    what it found once it got there, which is the half that looks identical to a
    clean run: a deck whose eyebrow was retyped reports "no roster here", and a
    hundred of them report a finished sweep."""
    out = []
    if city["scanned"] and not city["roster"]:
        out.append("%s's %s was scanned but no roster was recognised in it — the "
                   "eyebrow text or the shape geometry has changed, and new chapters "
                   "will still be minted with the old roster"
                   % (de.TEMPLATE_CITY, ROSTER_DECK))
    if counts["decks"] and counts["deck_clean"] > UNRECOGNISED_RATIO * counts["decks"]:
        out.append("%d of %d %s showed no About roster at all — above the %d%% this "
                   "one design should ever produce; has the eyebrow or the layout "
                   "changed?" % (counts["deck_clean"], counts["decks"], ROSTER_DECK,
                                 UNRECOGNISED_RATIO * 100))
    return out


def rewrite_local(path):
    """--rewrite-local: run the XML engine on one file, no Drive access at all."""
    dst = re.sub(r"\.pptx$", "", path) + "-projects.pptx"
    report = rewrite_pptx(path, dst)
    if not report:
        print("%s: no THE PROJECTS roster found — nothing to rewrite" % path)
        return 0
    if any(st in CHANGED for st, _d in report.values()):
        print("%s -> %s" % (path, dst))
    for part, (status, detail) in sorted(report.items()):
        print("   %s: %s%s" % (part, status, "  " + detail if detail else ""))
    # Non-zero for anything a human has to look at, not only a custom roster:
    # this is the path an author runs before a sweep, so an overflow or an
    # orphaned eyebrow has to fail here too, or the sweep is the first to know.
    return 1 if notices(report) else 0


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
    if args.jobs < 1:
        sys.exit("ABORT: --jobs must be at least 1 (got %d)." % args.jobs)

    print("Scanning the Community Events tree for event templates...")
    scan = de.walk_templates(de.COMMUNITY_ROOT, max(args.jobs, 8))
    entries = scan.templates
    if args.chapter:
        needle = args.chapter.lower()
        entries = [e for e in entries if needle in e.path.lower()]
    if not entries:
        print("No templates matched." if args.chapter else
              "No templates found — has the Community Events tree moved?")
        return EXIT_COVERAGE
    print("Found %d template file(s).%s\n"
          % (len(entries), "" if args.write else
             "  PLAN ONLY — nothing will be written, and the repack step is not "
             "exercised (only --write repacks)."))
    print("Roster: %s\n" % ROSTER)

    counts = collections.Counter()
    # `city` counts how many of TemplateCity's own copies of the roster deck
    # were scanned and how many held a roster, so "its roster moved" is told
    # from "it was never reached" (coverage_attention's job) and from "that
    # deck has no About slide by design".
    flagged, city = [], collections.Counter()
    with tempfile.TemporaryDirectory() as tmpdir, \
            ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = pool.map(
            lambda e: de.process(e, tmpdir, args.write,
                                 lambda src, dst: rewrite_pptx(src, dst, args.write)),
            entries)
        try:
            for entry, report, err in results:
                report = report or {}
                if err is not None:
                    counts["failed"] += 1
                    print("  FAILED  %s\n            %s" % (entry.path, err))
                    continue
                kind = classify(report)
                counts[kind] += 1
                for status, detail in notices(report):
                    flagged.append((entry.path, status, detail))
                if entry.name == ROSTER_DECK:
                    counts["decks"] += 1
                    counts["deck_clean"] += kind == "clean"
                    if entry.owner == de.TEMPLATE_CITY:
                        city["scanned"] += 1
                        city["roster"] += kind != "clean"
                if kind == "changed":
                    print("  %s  %s  (%s)"
                          % ("REWRITTEN" if args.write else "would rewrite", entry.path,
                             "; ".join("%s: %s" % (p.rsplit("/", 1)[-1], d)
                                       for p, (st, d) in sorted(report.items())
                                       if st in CHANGED)))
                elif kind in ("custom", "orphan", "unknown"):
                    print("  %-9s %s  (%s)"
                          % (kind.upper(), entry.path,
                             "; ".join("%s: %s" % (st, d) for st, d in report.values())))
        finally:
            # Even a run that dies mid-sweep has already uploaded files, and the
            # summary and ATTENTION block are the only record of how far it got,
            # so they are printed before the exception leaves this function.
            print("\n%d rewritten, %d already current, %d without an About roster, "
                  "%d with their own roster, %d orphaned, %d failed."
                  % (counts["changed"], counts["current"], counts["clean"],
                     counts["custom"], counts["orphan"], counts["failed"]))

    attention = ["%s: %s — %s" % (path, status, detail)
                 for path, status, detail in flagged]
    attention += estate_attention(counts, city)
    if not args.chapter:
        # A folder-name match that stops matching looks exactly like a clean
        # estate, so name what the scan could not see rather than letting it
        # read as done.
        attention += de.coverage_attention(
            scan, "new chapters would still be minted with the old roster")
    if attention:
        print("\nATTENTION — read before calling this done:")
        for line in attention:
            print("  - %s" % line)
    if counts["changed"] and not args.write:
        print("Re-run with --write to apply.")

    # Three outcomes, three codes, so a wrapper can tell them apart: a sweep
    # that could not cover the estate is not the same as one with work left to
    # do, and neither is the same as an advisory an operator has already read.
    if counts["failed"] or attention:
        return EXIT_COVERAGE
    if counts["changed"] and not args.write:
        return EXIT_PENDING
    return 0


if __name__ == "__main__":
    sys.exit(main())
