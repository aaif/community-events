#!/usr/bin/env python3
"""Re-place the slide-5 "you-are-here" map dot in every EXISTING chapter deck.

Chapters created before the Gall Stereographic fit shipped (PR #20) got their
dot from `create_chapter.py`'s old placement, which was wrong in two separate
ways — worth keeping apart, because they call for different reasoning:

- The **projection** was a linear lon2x plus a piecewise-linear lat2y, about 9%
  too wide with a ~20 px offset. Tokyo is the clean example: it landed roughly
  21 degrees of longitude east, out in the Pacific.
- Four cities never used that projection at all. Seoul, Sydney, Melbourne and
  Shanghai sat in a hand-tuned `PIXEL_OVERRIDES` table, and `project_city`
  returned the table's pixel and ignored lat/lon entirely. Shanghai's dot landed
  on Honshu because that **override** was wrong, not because the formula was —
  do not cite it as evidence about the projection.

The current fit is correct to 0.64 px and needs no overrides; this script walks
the Chapters Drive and brings the old decks up to it.

It is a *backfill*, not a second implementation: the projection, the OOXML
surgery and the Drive plumbing are all imported from `create_chapter.py`, so
there is exactly one definition of where a dot belongs. Re-running is a no-op on
decks already within --tolerance, which is what makes this the tool to reach for
if the map art is ever refitted.

Coordinates come from the **Chapters & Teams** sheet's `Generated Geolocation`
column, joined to Drive by the folder URL in `Chapter Folder` — not from
geocoding the folder name. The sheet is what the website feed already draws, so
the dot and the site agree by construction; it is also the only source that maps
a folder to its real city, and a folder's name can lag that city (they have been
renamed before). A Drive folder with no sheet row is reported and skipped rather
than guessed at.

Read-only by default: it prints the per-chapter drift and changes nothing. There
is no undo beyond Drive's own revision history, so read a plan run before
passing --write.

Usage:
  # Plan (default) — report every chapter's drift in pixels, write nothing:
  python backfill_map_dots.py

  # Apply to the whole estate:
  python backfill_map_dots.py --write

  # One chapter, with coordinates given rather than read from the sheet:
  python backfill_map_dots.py --city Shanghai --lat 31.2304 --lon 121.4737 --write
"""
import argparse, collections, os, re, shutil, sys, tempfile, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc      # projection, OOXML surgery, Drive plumbing

CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"   # "Chapters List"
CHAPTERS_TAB = "Chapters & Teams"
COL_CITY, COL_FOLDER, COL_GEO = "City", "Chapter Folder", "Generated Geolocation"

DECK_NAME = "Slides.pptx"        # the deck that carries the slide-5 network map
# TemplateCity's dot is the San Francisco anchor the label offsets are measured
# from (see LABEL_DX/LABEL_DY in create_chapter) — moving it would redefine the
# template, not fix a chapter. Guarded by name, and main() says so loudly if no
# folder matches, because a renamed template would silently become a target.
SKIP_FOLDERS = {"TemplateCity"}

# A dot within a pixel of its target is rounding, not misplacement; only move a
# dot actually in the wrong place, so a re-run is a cheap no-op.
DEFAULT_TOLERANCE_PX = 1.0
# Above this, --tolerance stops meaning "ignore rounding" and starts meaning
# "report a broken estate as clean". Warned about, not forbidden.
LOUD_TOLERANCE_PX = 50.0
EMU_PER_PX_X = cc.MAP_EXT[0] / cc.MAP_PX[0]
EMU_PER_PX_Y = cc.MAP_EXT[1] / cc.MAP_PX[1]

FOLDER_ID_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
GEO_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

#: One parsed sheet row. `fid` is None when the row names no Drive folder;
#: `latlon` is None exactly when `why` explains what stopped it being usable.
Row = collections.namedtuple("Row", "city fid latlon why")
#: read_chapter_coords()'s result. Attribute access, not unpacking: widening
#: this from two values to three already broke every call site and test once.
Sheet = collections.namedtuple("Sheet", "by_folder bad_rows unusable")

#: status -> (marker, phrase). One declaration, so adding a status cannot leave
#: a half-updated lookup to KeyError mid-sweep, after N decks are already up.
STATUS_DISPLAY = {"ok": ("=", "already correct"),
                  "moved": ("+", "moved"),
                  "would-move": ("~", "WOULD move")}


def drift_px(current_emu, target_emu):
    """Distance in map pixels between two EMU corner offsets.

    Both arguments are **EMU**, not pixels. Handing this pixel pairs returns a
    plausible small number rather than failing, so every deck would report
    "already correct" and nothing would raise."""
    dx = (current_emu[0] - target_emu[0]) / EMU_PER_PX_X
    dy = (current_emu[1] - target_emu[1]) / EMU_PER_PX_Y
    return (dx * dx + dy * dy) ** 0.5


def parse_geo(cell):
    """Parse a `Generated Geolocation` cell ("37.7749, -122.4194") to
    ((lat, lon), None), or (None, reason) when it cannot be used.

    The reason is worth carrying: "the cell is blank" and "the pair looks
    transposed" call for different fixes by whoever maintains the sheet. A
    swapped pair where BOTH values are inside +/-90 (London, Berlin) still
    cannot be caught here — that one is only visible on the map."""
    text = (cell or "").strip()
    if not text:
        return None, "%s is blank" % COL_GEO
    m = GEO_RE.match(text)
    if not m:
        return None, "%s is not a coordinate pair (%r)" % (COL_GEO, text[:40])
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90):
        # By far the most likely cause, and the one the operator can act on.
        return None, ("%s latitude %.4f is out of range — transposed lat/lon?"
                      % (COL_GEO, lat))
    if not (-180 <= lon <= 180):
        return None, "%s longitude %.4f is out of range" % (COL_GEO, lon)
    if lat == 0 and lon == 0:
        # Null Island is in range and parses cleanly, but it is what a failed
        # geocode writes far more often than it is a real chapter.
        return None, "%s is 0, 0 — a failed geocode, not a city" % COL_GEO
    return (lat, lon), None


def folder_id_from_url(cell):
    m = FOLDER_ID_RE.search(cell or "")
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Sheet
# ----------------------------------------------------------------------------
def read_chapter_coords():
    """Return a Sheet: by_folder {fid: Row}, bad_rows [Row], unusable {fid: Row}.

    `bad_rows` is every row that cannot place a dot; `unusable` is the subset of
    those that DO name a Drive folder, keyed by it, so the sweep can tell "this
    folder's row is blank" from "no row mentions this folder at all". Both are
    derived from one pass rather than appended to separately — they are one
    dataset, and two hand-maintained copies drifting apart is exactly how a
    folder ends up reported as rowless when its row is sitting right there.

    Columns are resolved by header name, never by letter — the tab is a website
    feed and its layout has moved before."""
    res = cc.gws_json("sheets", "spreadsheets", "values", "get", params={
        "spreadsheetId": CHAPTERS_ID, "range": "'%s'!A:AZ" % CHAPTERS_TAB})
    rows = res.get("values", [])
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    missing = [c for c in (COL_CITY, COL_FOLDER, COL_GEO) if c not in headers]
    if missing:
        sys.exit("ABORT: %s is missing column(s) %s — the tab was restructured; "
                 "re-point this script at the new header names."
                 % (CHAPTERS_TAB, ", ".join(map(repr, missing))))
    i_city, i_folder, i_geo = (headers.index(c) for c in (COL_CITY, COL_FOLDER, COL_GEO))

    by_folder, bad_rows, unusable = {}, [], {}
    for row in rows[1:]:
        def cell(i):
            return (row[i] if i < len(row) else "").strip()
        city = cell(i_city)
        if not city:
            continue
        fid = folder_id_from_url(cell(i_folder))
        latlon, why = parse_geo(cell(i_geo))
        if not fid:
            bad_rows.append(Row(city, None, None, "no %s link" % COL_FOLDER))
            continue
        if why:
            r = Row(city, fid, None, why)
            bad_rows.append(r)
            unusable.setdefault(fid, r)
            continue
        if fid in by_folder:
            # Two rows pointing at one folder. Picking one silently would place
            # the dot for whichever row happens to sort later on the sheet.
            bad_rows.append(Row(city, fid, latlon,
                                "duplicate %s — already claimed by %r; NOT used"
                                % (COL_FOLDER, by_folder[fid].city)))
            continue
        by_folder[fid] = Row(city, fid, latlon, None)
    # A usable row wins over an unusable one for the same folder. Stated here
    # rather than left to the order main() happens to check the two dicts in.
    for fid in list(unusable):
        if fid in by_folder:
            del unusable[fid]
    return Sheet(by_folder, bad_rows, unusable)


# ----------------------------------------------------------------------------
# Drive walk
# ----------------------------------------------------------------------------
def find_deck(folder_id):
    """Return (deck_id, reason): exactly one deck -> (id, None), otherwise
    (None, reason).

    Looks at the chapter folder and then one level down, rather than hard-coding
    "Event Templates (Copy for Each Event)": that folder has been renamed once
    already, and a chapter whose deck simply was never cloned has to read as
    missing rather than as an error.

    Ambiguity is reported, never resolved: a chapter holding two copies would
    otherwise get an arbitrary one rewritten and reported as a success, and the
    copy people actually present from might be the other one."""
    children = cc.list_children(folder_id)
    hits = [c["id"] for c in children
            if c["name"] == DECK_NAME and c["mimeType"] == cc.PPTX]
    for c in children:
        if c["mimeType"] != cc.FOLDER:
            continue
        hits += [g["id"] for g in cc.list_children(c["id"])
                 if g["name"] == DECK_NAME and g["mimeType"] == cc.PPTX]
    if not hits:
        return None, "no %s in the chapter folder" % DECK_NAME
    if len(hits) > 1:
        return None, ("%d copies of %s in this chapter folder — resolve by hand"
                      % (len(hits), DECK_NAME))
    return hits[0], None


def chapter_folders(sheet, only_city=None):
    """Chapter folders under the Chapters parent, minus the template.

    `only_city` matches the Drive folder NAME or the city the sheet gives that
    folder. Matching the folder name alone would make `--city Edinburgh` abort
    on a folder still called "Scotland" — the very mismatch this script joins
    through the sheet to avoid everywhere else."""
    kids = cc.list_children(cc.CHAPTERS_PARENT)
    folders = [k for k in kids if k["mimeType"] == cc.FOLDER]
    if not any(k["name"] in SKIP_FOLDERS for k in folders):
        print("  WARNING: no template folder (%s) under the Chapters parent — "
              "it may have been renamed, and is no longer protected.\n"
              % "/".join(sorted(SKIP_FOLDERS)))
    out = [k for k in folders if k["name"] not in SKIP_FOLDERS]
    if only_city:
        want = only_city.strip().casefold()

        def matches(k):
            row = sheet.by_folder.get(k["id"]) or sheet.unusable.get(k["id"])
            return (k["name"].casefold() == want
                    or (row is not None and row.city.casefold() == want))
        out = [k for k in out if matches(k)]
        if not out:
            sys.exit("ABORT: no chapter folder named %r, and no row on %s gives "
                     "that city to a chapter folder." % (only_city, CHAPTERS_TAB))
    return sorted(out, key=lambda k: k["name"])


def process(deck_id, latlon, tmpdir, tolerance, write):
    """Inspect one chapter's deck; move its markers when --write and off target.

    Returns (status, drift_px) with status in STATUS_DISPLAY, or (None, reason)
    when slide 5 cannot be read — a deck that has drifted from the template is
    reported, never rewritten on a guess. None is the same can't-evaluate
    sentinel find_deck() and read_marker_offsets() already use, rather than a
    fourth status the caller would immediately have to re-split out.

    Drift is the WORSE of the dot and the label. Both are rewritten together, so
    gating on the dot alone would leave a hand-dragged label uncorrectable and
    would make a refit of LABEL_DX/LABEL_DY a silent estate-wide no-op."""
    tmp = os.path.join(tmpdir, "d" + deck_id + ".pptx")
    try:
        cc.gws_download(deck_id, tmp)
        # gws_download discards stdout, so an error body written at exit 0 would
        # otherwise surface as a BadZipFile blaming the OOXML code.
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("download of deck %s wrote no file" % deck_id)
        if not zipfile.is_zipfile(tmp):
            raise RuntimeError("download of deck %s is not a .pptx (%d bytes) — "
                               "an error body, not the deck"
                               % (deck_id, os.path.getsize(tmp)))
        current, why = cc.read_marker_offsets(tmp)
        if current is None:
            return None, why
        dot_target, label_target = cc.marker_offsets(*latlon)
        d = max(drift_px(current[0], dot_target),
                drift_px(current[1], label_target))
        if d <= tolerance:
            return "ok", d
        if not write:
            return "would-move", d
        cc.reposition_map_marker(tmp, *latlon)
        cc.gws_upload(deck_id, tmp, cc.PPTX)
        return "moved", d
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="Actually move the dots (default: report only)")
    ap.add_argument("--city", help="Only this chapter: its Drive folder NAME, or "
                                   "the city the sheet gives that folder "
                                   "(default: every chapter)")
    ap.add_argument("--lat", type=float, help="Latitude override (requires --city and --lon)")
    ap.add_argument("--lon", type=float, help="Longitude override (requires --city and --lat)")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PX,
                    help="Leave a dot alone if it is within this many map pixels "
                         "of its target (default: %(default)s)")
    args = ap.parse_args()

    if (args.lat is None) != (args.lon is None):
        sys.exit("ABORT: --lat and --lon must be given together.")
    if args.lat is not None and not args.city:
        sys.exit("ABORT: --lat/--lon apply to one chapter; pass --city too.")
    if args.tolerance < 0:
        sys.exit("ABORT: --tolerance must not be negative (got %r) — every deck "
                 "would be rewritten." % args.tolerance)
    if args.tolerance > LOUD_TOLERANCE_PX:
        print("  WARNING: --tolerance %.1f px exceeds any real drift; a "
              "misplaced estate would report as already correct.\n"
              % args.tolerance)

    override = (args.lat, args.lon) if args.lat is not None else None
    # The sheet is still read under --lat/--lon: --city resolves through it, and
    # its rows are what name the chapter in the report.
    sheet = read_chapter_coords()
    folders = chapter_folders(sheet, args.city)
    print("%s %d chapter folder(s) against %d sheet row(s), tolerance %.1f px.\n"
          % ("Moving dots in" if args.write else "Checking",
             len(folders), len(sheet.by_folder), args.tolerance))

    results, skipped, attention, failed = [], [], [], []
    tmpdir = tempfile.mkdtemp(prefix="aaif-map-dots-")
    try:
        for f in folders:
            row = sheet.by_folder.get(f["id"])
            # Report under the sheet's city, not the folder name: a folder's
            # name can lag the chapter's real city.
            name = row.city if row else f["name"]
            if override:
                latlon = override
            elif row:
                latlon = row.latlon
            elif f["id"] in sheet.unusable:
                bad = sheet.unusable[f["id"]]
                skipped.append((bad.city, "%s on %s" % (bad.why, CHAPTERS_TAB)))
                print("  ?  %-16s %s — dot left as-is" % (bad.city, bad.why))
                continue
            else:
                skipped.append((f["name"], "no row on %s links to this folder"
                                % CHAPTERS_TAB))
                print("  ?  %-16s no sheet row — dot left as-is" % f["name"])
                continue
            deck_id, why = find_deck(f["id"])
            if deck_id is None:
                skipped.append((name, why))
                print("  ?  %-16s %s" % (name, why))
                continue
            # One bad deck must not abort the sweep: without this the summary
            # and the skip list never print, and the operator loses the record
            # of what was already moved. Named exceptions only — a bare except
            # would hide coding errors behind a per-chapter "FAILED" line.
            try:
                status, d = process(deck_id, latlon, tmpdir, args.tolerance,
                                    args.write)
            except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                failed.append((name, str(exc)[:200]))
                print("  X  %-16s FAILED: %s" % (name, str(exc)[:110]))
                continue
            if status is None:
                attention.append((name, d))
                print("  !  %-16s %s — skipped" % (name, d))
                continue
            results.append((name, status, d))
            mark, note = STATUS_DISPLAY[status]
            print("  %s  %-16s %-15s (%5.1f px off)" % (mark, name, note, d))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    off = [r for r in results if r[1] != "ok"]
    print("\n%d chapter(s) checked: %d already correct, %d %s, %d skipped, "
          "%d need attention, %d failed."
          % (len(results), len(results) - len(off), len(off),
             "moved" if args.write else "off (not moved)", len(skipped),
             len(attention), len(failed)))
    if off:
        worst = max(off, key=lambda r: r[2])
        print("Worst drift: %s at %.1f px." % (worst[0], worst[2]))
    for name, why in skipped:
        print("  skipped: %s — %s" % (name, why))
    for name, why in attention:
        print("  NEEDS ATTENTION: %s — %s" % (name, why))
    for name, why in failed:
        print("  FAILED: %s — %s" % (name, why))
    for r in sheet.bad_rows:
        print("  sheet row unusable: %s — %s" % (r.city, r.why))
    if off and not args.write:
        print("\nRe-run with --write to move them.")

    # A run that could not evaluate part of the estate must not exit 0: a
    # wrapper, a cron job, or a glance at $? would read it as a finished
    # backfill. Benign skips (no row, no deck) are not failures; an unreadable
    # deck or a hard error is.
    if failed or attention:
        sys.exit("\n%d deck(s) failed and %d need attention — those chapters "
                 "were NOT backfilled." % (len(failed), len(attention)))


if __name__ == "__main__":
    main()
