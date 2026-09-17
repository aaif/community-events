#!/usr/bin/env python3
"""Rework the "HOSTED BY / WITH" logo footer in every EXISTING event template.

The footer used to draw each logo as a *button*: a rounded rectangle with a
1px border and a fill, holding centred bold text. Three things were wrong with
it, and this script fixes all three in one pass:

- **The boxes.** A bordered chip reads as a control you can press, and a row of
  them fights the flat, rule-and-type language the rest of the deck is drawn in.
  They go; the logo slot is just its text.
- **The host.** The slot said `HOST VENUE CO.` — the venue was the host and AAIF
  appeared only in the header. AAIF hosts these events, so the slot now carries
  the AAIF lockup, drawn from the mark image the slide *already* embeds for its
  header plus the wordmark set in Space Grotesk bold. No new media is added and
  no relationship is rewritten, so the lockup cannot drift from the header's.
- **The spacing.** Slot widths were sized for the buttons, so with the buttons
  gone the row was strung out across gaps that no longer meant anything. The
  host row is re-packed left on one even gap.

Remaining slots become `LOGO 1`, `LOGO 2`, … in the same muted grey as their
`HOSTED BY` / `WITH` labels: an unfilled slot should read as empty, not as a
member called "MEMBER LOGO". Chips holding a *real* name (the founding-member
grid on the carousel: AWS, Anthropic, Block, …) keep their ink and their
position — only the box comes off. The old `AAIF · SF` chapter badge beside the
host slot is dropped outright, because the lockup now says the same thing.

A chip is found *structurally* — a roundRect with a text shape at exactly the
same geometry — but telling an unfilled slot from a real member name exists
only in the text, because the slots were never given distinguishing shape names
or fills. `PLACEHOLDER_RE` and `BADGE_RE` are therefore a vocabulary fixed **for
this migration**, not a general design rule — three exact strings plus one
deliberate `\bLOGO\b` wildcard, which will demote any member whose name
contains the standalone word "Logo". They are safe here because the whole estate
was cloned from one template and the run has a plan mode; a chip's text is not
echoed, so the per-file chip counts and the NO LOCKUP flag are what an operator
reads.

Scope is **templates**, not the copies organizers have already made for a given
event: every `.pptx` directly in a folder named `Event Templates…` / `Event Name`,
across all chapters, the online series, and the shared Templates folder. That
set includes **TemplateCity**, the folder `create_chapter.py` clones for every
new chapter — so a full sweep is what stops new chapters being minted with the
old footer, and a full-estate run that does not reach it says so and exits
non-zero. A `--chapter` run skips the estate-coverage checks by design. A file
whose footer has already been reworked has no chips left to find, so a re-run is
a no-op and the file is not re-uploaded.

Read-only by default: it prints what each file would lose and gain and changes
nothing. There is no undo beyond Drive's own revision history, so read a plan
run before passing --write.

Usage:
  # Plan (default) — list every template and its footer, write nothing:
  python backfill_host_footer.py

  # Apply to the whole estate:
  python backfill_host_footer.py --write

  # One chapter (matches the Drive folder name, case-insensitive):
  python backfill_host_footer.py --chapter "New York City" --write

  # Test the XML engine on a local .pptx, no Drive at all:
  python backfill_host_footer.py --rework-local ./Event-Hero-Square.pptx
"""
import argparse, os, re, shutil, sys, tempfile, zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc      # Drive plumbing + the shared OOXML primitives
# The estate walk and the shape-level XML helpers, shared with every other
# sweep. They used to live here, which made a script named after a footer the
# home of the whole estate's Drive id; see deck_estate.py's docstring.
from deck_estate import (                                    # noqa: F401 — re-exported
    COMMUNITY_ROOT, CHAPTERS_FOLDER, TEMPLATE_CITY, TEMPLATE_FOLDER_RE, SERIES_FOLDER,
    EMU_PER_PT, OFF_RE, EXT_RE, SHAPE_RE, TEXT_RE, GEOM_RE, SZ_RE, SLIDE_RE,
    Box, Shape, shape_text, shapes, font_size, text_width, move, resize, retext,
    walk_templates, coverage_attention, process)

# Deck constants sampled from the templates themselves, NOT design-system tokens:
# MUTED is the grey the decks' own HOSTED BY / WITH label runs use (it is not
# design/aaif-tokens.css's --ink-4), and Space Grotesk is the face the templates
# embed for the wordmark, not DESIGN.md's Instrument Sans. Retuning either to a
# token would desync the footer from the labels beside it.
MUTED = "9A978F"
INK = "0A0A0A"
# Advance width as a fraction of the point size. JetBrains Mono is monospaced so
# 0.60 is exact; Space Grotesk Bold is proportional and 0.62 is a deliberate
# over-estimate — erring wide only ever opens a gap, never collides two shapes.
MONO_EM, PROP_EM = 0.60, 0.62
# Fractions of the footer band's height. Together these set the lockup's
# proportions and the one gap the row is packed on.
# MARK_H is applied to both axes: the mark is drawn into a SQUARE box, which is
# also what find_mark falls back on to identify it.
MARK_H, MARK_GAP, ROW_GAP = 0.78, 0.13, 0.55
LOCKUP_PT_DIVISOR = 3.2          # band height -> wordmark point size

EMBED_RE = re.compile(r'<a:blip r:embed="([^"]+)"')
ID_RE = re.compile(r'<p:cNvPr[^>]*id="(\d+)"')
LOCKUP_NAME = "AAIF Lockup"          # the name given to the two shapes below
# The header mark's picture carries its source filename in descr (or its name),
# e.g. descr="assets/logo_black.png" — that is what identifies it as the mark.
MARK_NAME_RE = re.compile(r'<p:cNvPr[^>]*(?:descr|name)="[^"]*logo[^"]*"', re.I)
BOLD_RE = re.compile(r'(<a:rPr\b[^>]*?)\bb="1"')
INK_FILL_RE = re.compile(r'<a:srgbClr val="%s"\s*/>' % INK, re.I)
CENTRE_RE = re.compile(r'(<a:pPr\b[^>]*?)algn="ctr"')

# See the docstring: a closed vocabulary for this migration, not a design rule.
PLACEHOLDER_RE = re.compile(r"^(.*\bLOGO\b.*|HOST VENUE CO\.?|VENUE NAME|SPONSOR)$", re.I)
BADGE_RE = re.compile(r"^AAIF\s*[·.\-]\s*\S+$", re.I)


# ----------------------------------------------------------------------------
# Slide XML engine (pure, unit-testable — no Drive, no filesystem)
# ----------------------------------------------------------------------------
def width(text, sz, em=MONO_EM):
    """deck_estate.text_width with this sweep's default face. The footer measures
    mono label runs far more often than the proportional wordmark, so the default
    is MONO_EM and the two lockup calls pass PROP_EM explicitly."""
    return text_width(text, sz, em)


def lockup(x, y, cy, embed, first_id):
    """The AAIF mark + wordmark drawn from (x, y) at height cy. Returns
    (markup, total width). `embed` is a relationship id already present in the
    slide — the header mark's — so no media and no rels entry is added."""
    mark = int(cy * MARK_H)
    gap = int(cy * MARK_GAP)
    sz = max(700, int(round(cy / EMU_PER_PT / LOCKUP_PT_DIVISOR * 100)))
    word_cx = max(width("Agentic AI", sz, PROP_EM),
                  width("Foundation", sz, PROP_EM))
    run = ('<a:r><a:rPr b="1" i="0" lang="en-US" sz="%d" u="none" cap="none" strike="noStrike">'
           '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
           '<a:latin typeface="Space Grotesk"/><a:ea typeface="Space Grotesk"/>'
           '<a:cs typeface="Space Grotesk"/><a:sym typeface="Space Grotesk"/></a:rPr>'
           '<a:t>%%s</a:t></a:r>' % (sz, INK))
    para = ('<a:p><a:pPr indent="0" lvl="0" marL="0" marR="0" rtl="0" algn="l">'
            '<a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft>'
            '<a:buNone/></a:pPr>%s</a:p>')
    pic = ('<p:pic><p:nvPicPr><p:cNvPr descr="AAIF logo" id="%d" name="AAIF Lockup Mark"/>'
           '<p:cNvPicPr preferRelativeResize="0"/><p:nvPr/></p:nvPicPr>'
           '<p:blipFill rotWithShape="1"><a:blip r:embed="%s"><a:alphaModFix/></a:blip>'
           '<a:srcRect b="0" l="0" r="0" t="0"/><a:stretch/></p:blipFill>'
           '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
           '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln>'
           '</p:spPr></p:pic>'
           % (first_id, embed, x, y + (cy - mark) // 2, mark, mark))
    wordmark = ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="AAIF Lockup Wordmark"/><p:cNvSpPr/>'
                '<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/>'
                '<a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
                '</a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody>'
                '<a:bodyPr anchorCtr="0" anchor="ctr" bIns="0" lIns="0" spcFirstLastPara="1" '
                'rIns="0" wrap="square" tIns="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>%s%s'
                '</p:txBody></p:sp>'
                % (first_id + 1, x + mark + gap, y, word_cx, cy,
                   para % (run % "Agentic AI"), para % (run % "Foundation")))
    return pic + wordmark, mark + gap + word_cx


def unbox(body, label=None):
    """Strip a chip's button styling: regular weight, left-aligned. `label` mutes
    the chip and relabels it — pass it for a slot nobody has filled yet, and
    leave it None for a chip holding a real name, which keeps text and ink."""
    body = BOLD_RE.sub(r'\1b="0"', body)
    body = CENTRE_RE.sub(r'\1algn="l"', body)
    if label:
        # A regex, not a literal replace: Google Slides re-serializes this as
        # `<a:srgbClr val="0A0A0A" />`, and a literal match would silently leave
        # every relabelled slot in full ink — the exact outcome the muting exists
        # to prevent, and invisible in the run's own output.
        body = INK_FILL_RE.sub('<a:srgbClr val="%s"/>' % MUTED, body)
        body = retext(body, label)
    return body


def button_boxes(shp):
    """{geometry: [indices]} for every empty roundRect — the button plate a chip
    was drawn on. A list, not a single index: two plates stacked at identical
    geometry both have to go, and keying one out would leave the other behind.
    Both returned index sets have a non-None `box`, which is what lets callers
    dereference `shp[i].box` unguarded."""
    boxes = {}
    for i, s in enumerate(shp):
        if s.kind == "sp" and s.geom == "roundRect" and s.box and not s.text:
            boxes.setdefault(s.box, []).append(i)
    return boxes


def find_chips(shp, boxes=None):
    """[(box index, text index)] for every logo chip — a roundRect plate with a
    text shape at exactly the same geometry sitting on top of it. The plate is
    matched on geometry and emptiness, not fill. "Empty" relies on shape_text
    stripping: the real plates carry a whitespace run, so dropping that strip
    would make every chip undiscoverable — silently, as a clean estate. `boxes` only
    holds shapes with no text and the second pass only takes shapes with text,
    so a shape can never be both halves of a pair."""
    boxes = button_boxes(shp) if boxes is None else boxes
    return [(b, i) for i, s in enumerate(shp)
            if s.kind == "sp" and s.text and s.box in boxes
            for b in boxes[s.box]]


def find_mark(shp):
    """The relationship id of the AAIF mark the slide already embeds for its
    header, or None. Identified, not guessed: the first picture in DOCUMENT
    order is whatever the deck happens to draw first — a background plate, a
    speaker photo, a partner logo — and promoting that to the footer would
    render it at mark size beside the AAIF wordmark. Match on the image's own
    name/descr, then fall back to a SQUARE picture (the mark's defining shape,
    since it is drawn into a square box), and give up rather than guess."""
    pics = [s for s in shp if s.kind == "pic" and EMBED_RE.search(s.body)]
    named = [s for s in pics if MARK_NAME_RE.search(s.body)]
    if not named:
        named = [s for s in pics if s.box and s.box.cx == s.box.cy]
    return EMBED_RE.search(named[0].body).group(1) if named else None


def find_host(shp, chips):
    """The `shapes()` index of the *text* shape whose slot should become the
    AAIF lockup — not an index into `chips`. It is the slot the HOSTED BY
    label introduces. Prefer the nearest chip to the label's right *in its own
    band* — the hero layout, where label and chips share a row. Fall back to
    document order for the deck and carousel layouts, where the label is stacked
    ABOVE its chips and so shares a band with none of them."""
    label = next((i for i, s in enumerate(shp) if s.text.upper() == "HOSTED BY"), None)
    if label is None:
        return None
    lab = shp[label].box
    if lab:
        band = [t for _b, t in chips
                if shp[t].box and shp[t].box.y == lab.y
                and shp[t].box.cy == lab.cy and shp[t].box.x > lab.x]
        if band:
            return min(band, key=lambda i: shp[i].box.x)
    return next((t for _b, t in chips if t > label), None)


def rework_slide(xml):
    """Return (new xml, chips found, whether the host slot became the lockup)."""
    shp = shapes(xml)
    boxes = button_boxes(shp)
    chips = find_chips(shp, boxes)
    # A zero-width plate is never a design element: it is residue from an
    # earlier sweep that left an unpaired plate in the row and then resized it
    # to the width of its own empty text. Clean it whether or not this slide
    # still has chips, otherwise an already-migrated file keeps its hairline
    # forever — a re-run finds no chips and returns before reaching the row.
    strays = [i for plates in boxes.values() for i in plates if shp[i].box.cx == 0]
    if not chips and not strays:
        return xml, 0, False
    # An already-drawn lockup counts: a repair-only pass must not report the
    # file as having lost its host slot.
    had_lockup = LOCKUP_NAME in xml

    host = find_host(shp, chips)
    embed = find_mark(shp)
    if embed is None:
        # No identifiable mark: draw nothing rather than promote whatever image
        # happened to be first. The chips still lose their boxes; the host slot
        # is skipped below and keeps its own text.
        host = None
    # `default` matters: a slide can legitimately carry no numeric cNvPr id, and
    # max() would raise ValueError -- surfacing as an unactionable FAILED line.
    first_id = max((int(m) for m in ID_RE.findall(xml)), default=1000) + 1

    edits, slot = {i: "" for i in strays}, 0
    for box_i, text_i in chips:
        edits[box_i] = ""                       # the button box goes away
        chip_text = shp[text_i].text
        if text_i == host:
            continue                            # the reflow below builds the lockup
        if BADGE_RE.match(chip_text):
            edits[text_i] = ""
        elif PLACEHOLDER_RE.match(chip_text):
            slot += 1
            edits[text_i] = unbox(shp[text_i].body, "LOGO %d" % slot)
        else:
            edits[text_i] = unbox(shp[text_i].body)

    # Pack the host's row left on one even gap. Only that row: the founding-
    # member grid elsewhere on the carousel keeps its columns.
    if host is not None:
        band = shp[host].box
        # A plate whose text shape was authored separately (or deleted) has no
        # chip partner, so the loop above never blanked it. Left in place it
        # would join the row below and be resized to the width of its own empty
        # text -- a zero-width bordered rect, which renders as a stray hairline.
        # It is a button plate in the footer band: it goes, like the others.
        for plates in boxes.values():
            for i in plates:
                if shp[i].box.y == band.y and shp[i].box.cy == band.cy:
                    edits.setdefault(i, "")
        row = sorted((i for i, s in enumerate(shp)
                      if s.box and s.box.y == band.y and s.box.cy == band.cy
                      and s.geom != "roundRect" and edits.get(i, s.body) != ""),
                     key=lambda i: shp[i].box.x)
        gap = int(band.cy * ROW_GAP)
        x = min(shp[i].box.x for i in row)
        for i in row:
            if i == host:
                body, w = lockup(x, band.y, band.cy, embed, first_id)
            else:
                # Width comes from the text as it will finally read, so a chip
                # renamed to "LOGO n" is measured at its new length.
                body = edits.get(i, shp[i].body)
                w = width(shape_text(body), font_size(body))
                body = resize(move(body, x), w)
            edits[i] = body
            x += w + gap

    out, cursor = [], 0
    for i in sorted(edits):
        start, end = shp[i].span
        out.append(xml[cursor:start])
        out.append(edits[i])
        cursor = end
    out.append(xml[cursor:])
    return "".join(out), len(chips) + len(strays), host is not None or had_lockup


def rework_pptx(src, dst):
    """Rework every slide of `src`, writing `dst` only when there is something to
    upload. Returns {slide part: (chip count, lockup drawn)} — empty when the
    file has no footer, in which case `dst` is never created and no deflate is
    paid, so a re-run over an already-reworked estate costs one download each.
    A plan run over a file it WOULD change still repacks locally and throws the
    result away: `--write` gates the upload, not the repack."""
    report, new_parts = {}, {}
    with zipfile.ZipFile(src) as zin:
        for name in zin.namelist():
            if not SLIDE_RE.match(name):
                continue
            new, n, host = rework_slide(zin.read(name).decode("utf-8"))
            if n:
                # Carry `host`, don't discard it: a slide whose boxes came off
                # but whose lockup was never drawn is half-migrated, and without
                # this it reports identically to a perfect one.
                report[name] = (n, host)
                new_parts[name] = new.encode("utf-8")
    if report:
        # create_chapter's repacker: it preserves each member's compression and
        # attributes and validates the result with testzip() before replacing
        # the file, so a corrupt repack can never reach gws_upload.
        shutil.copyfile(src, dst)
        cc._rewrite_zip(dst, lambda name, data: new_parts.get(name, data))
    return report


# ----------------------------------------------------------------------------
# Drive
# ----------------------------------------------------------------------------
def rework_local(path):
    """--rework-local: run the XML engine on one file, no Drive access at all."""
    dst = re.sub(r"\.pptx$", "", path) + "-reworked.pptx"
    report = rework_pptx(path, dst)
    if not report:
        print("%s: no logo footer found — nothing to rework" % path)
        return 0
    print("%s -> %s" % (path, dst))
    for part, (n, host) in sorted(report.items()):
        print("   %s: %d chips, %s"
              % (part, n, "lockup drawn" if host else "NO LOCKUP DRAWN"))
    return 0 if all(host for _n, host in report.values()) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="Actually upload the reworked templates (default: plan only)")
    ap.add_argument("--chapter", help="Only templates whose Drive path contains this "
                                      "(case-insensitive), e.g. 'New York City'")
    ap.add_argument("--rework-local", metavar="PPTX",
                    help="Rework a local .pptx to <name>-reworked.pptx; no Drive access")
    ap.add_argument("--jobs", type=int, default=6,
                    help="Concurrent Drive transfers, and folder listings during the "
                         "scan (default: 6)")
    args = ap.parse_args()

    if args.rework_local:
        return rework_local(args.rework_local)

    print("Scanning the Community Events tree for event templates...")
    entries, chapters, series, with_decks = walk_templates(COMMUNITY_ROOT,
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

    changed = clean = failed = 0
    no_lockup = []
    with tempfile.TemporaryDirectory() as tmpdir, \
            ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for entry, report, err in pool.map(
                lambda e: process(e, tmpdir, args.write, rework_pptx), entries):
            if err is not None:
                failed += 1
                print("  FAILED  %s\n            %s" % (entry["path"], err))
            elif report:
                changed += 1
                missing = sorted(p for p, (_n, host) in report.items() if not host)
                if missing:
                    no_lockup.append((entry["path"], missing))
                print("  %s  %s  (%s)%s"
                      % ("REWORKED" if args.write else "would rework", entry["path"],
                         ", ".join("%s:%d chips %s" % (p.rsplit("/", 1)[-1], n,
                                                       "+lockup" if host else "NO LOCKUP")
                                   for p, (n, host) in sorted(report.items())),
                         "   <-- NO LOCKUP" if missing else ""))
            else:
                clean += 1

    print("\n%d reworked, %d already clean or footerless, %d failed."
          % (changed, clean, failed))

    # A folder-name match that stops matching looks exactly like a clean estate,
    # so name what the scan could not see instead of letting it read as done.
    attention = ["%s: boxes removed but no lockup drawn on %s"
                 % (path, ", ".join(s.rsplit("/", 1)[-1] for s in slides))
                 for path, slides in no_lockup]
    if not args.chapter:
        attention += coverage_attention(
            entries, chapters, series, with_decks,
            "new chapters would still be cloned from the OLD footer")
    if attention:
        print("\nATTENTION — the sweep did not cover the whole estate:")
        for line in attention:
            print("  - %s" % line)
    if changed and not args.write:
        print("Re-run with --write to apply.")
    return 1 if (failed or attention) else 0


if __name__ == "__main__":
    sys.exit(main())
