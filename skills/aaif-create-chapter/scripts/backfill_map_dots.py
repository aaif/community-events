#!/usr/bin/env python3
"""Re-place the slide-5 "you-are-here" map dot in every EXISTING chapter deck.

Chapters created before 2026-08-12 got their dot from `create_chapter.py`'s old
hand-rolled projection (a linear lon2x plus a piecewise-linear lat2y, patched per
city by a PIXEL_OVERRIDES table). That model was wrong everywhere — ~9% too wide
with a ~20 px offset — so every one of those decks carries a misplaced dot (mean
31 px, worst 60 px: Shanghai landed on Japan, Tokyo in the Pacific). The current
Gall Stereographic fit in `create_chapter.py` is correct to 0.64 px and needs no
overrides; this script walks the Chapters Drive and brings the old decks up to it.

It is a *backfill*, not a second implementation: the projection, the OOXML
surgery and the Drive plumbing are all imported from `create_chapter.py`, so
there is exactly one definition of where a dot belongs. Re-running is safe and
cheap — a deck already within --tolerance of its target is left untouched, which
is also what makes this the tool to reach for if the map art is ever refitted.

Coordinates come from the **Chapters & Teams** sheet's `Generated Geolocation`
column, joined to Drive by the folder URL in `Chapter Folder` — not from
geocoding the folder name. The sheet is what the website feed already draws, so
the dot and the site agree by construction; it is also the only source that knows
a folder still called "Scotland" is the Edinburgh chapter. A Drive folder with no
sheet row is reported and skipped rather than guessed at.

Read-only by default: it prints the per-chapter drift and changes nothing.

Usage:
  # Plan (default) — report every chapter's drift in pixels, write nothing:
  python backfill_map_dots.py

  # Apply to the whole estate:
  python backfill_map_dots.py --write

  # One chapter, with coordinates given rather than read from the sheet:
  python backfill_map_dots.py --city Shanghai --lat 31.2304 --lon 121.4737 --write
"""
import argparse, os, re, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc      # projection, OOXML surgery, Drive plumbing

CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"   # "Chapters List"
CHAPTERS_TAB = "Chapters & Teams"
COL_CITY, COL_FOLDER, COL_GEO = "City", "Chapter Folder", "Generated Geolocation"

DECK_NAME = "Slides.pptx"        # the deck that carries the slide-5 network map
# TemplateCity's dot is the San Francisco anchor the label offsets are measured
# from (see LABEL_DX/LABEL_DY in create_chapter) — moving it would redefine the
# template, not fix a chapter. It has no sheet row either, so this is belt and
# braces: it must never be treated as a chapter.
SKIP_FOLDERS = {"TemplateCity"}

# Sub-pixel drift is invisible on a 1123 px map and is just rounding; only move a
# dot that is actually in the wrong place, so a re-run is a cheap no-op.
DEFAULT_TOLERANCE_PX = 1.0
EMU_PER_PX_X = cc.MAP_EXT[0] / cc.MAP_PX[0]
EMU_PER_PX_Y = cc.MAP_EXT[1] / cc.MAP_PX[1]

FOLDER_ID_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
GEO_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def drift_px(current, target):
    """Distance in map pixels between a deck's current dot and where it belongs."""
    dx = (current[0] - target[0]) / EMU_PER_PX_X
    dy = (current[1] - target[1]) / EMU_PER_PX_Y
    return (dx * dx + dy * dy) ** 0.5


def parse_geo(cell):
    """Parse a `Generated Geolocation` cell ("37.7749, -122.4194") to (lat, lon),
    or None if it is blank or not a coordinate pair. Range-checked: a garbled
    pair would otherwise place a dot confidently in the wrong place."""
    m = GEO_RE.match(cell or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def folder_id_from_url(cell):
    m = FOLDER_ID_RE.search(cell or "")
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Sheet
# ----------------------------------------------------------------------------
def read_chapter_coords():
    """Return ({folder_id: (city, lat, lon)}, [(city, why)], {folder_id: (city, why)}).

    The third value holds the rows that DO name a Drive folder but whose
    coordinates are unusable. Without it a folder whose row exists but has no
    geolocation is indistinguishable from one with no row at all, and the sweep
    reports "no row on the sheet links to this folder" about a row that is
    sitting right there.

    Columns are resolved by header name, never by letter — the tab is a website
    feed and its layout has moved before. A row missing its folder link or its
    geolocation is left out of the map and reported, so the corresponding Drive
    folder reads as unresolved rather than having its dot moved to a coordinate
    the sheet never gave."""
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

    coords, bad, unusable = {}, [], {}
    for row in rows[1:]:
        def cell(i):
            return (row[i] if i < len(row) else "").strip()
        city = cell(i_city)
        if not city:
            continue
        fid, geo = folder_id_from_url(cell(i_folder)), parse_geo(cell(i_geo))
        if not fid or not geo:
            why = ("no %s link" % COL_FOLDER if not fid
                   else "no usable %s" % COL_GEO)
            bad.append((city, why))
            if fid:
                unusable[fid] = (city, why)
            continue
        coords[fid] = (city, geo[0], geo[1])
    return coords, bad, unusable


# ----------------------------------------------------------------------------
# Drive walk
# ----------------------------------------------------------------------------
def find_deck(folder_id):
    """Return the id of the chapter's Slides.pptx, or None.

    Looks at the chapter folder and then one level down, rather than hard-coding
    "Event Templates (Copy for Each Event)": that folder has been renamed once
    already, and a chapter whose deck simply was never cloned has to read as
    missing rather than as an error."""
    children = cc.list_children(folder_id)
    for c in children:
        if c["name"] == DECK_NAME and c["mimeType"] == cc.PPTX:
            return c["id"]
    for c in children:
        if c["mimeType"] != cc.FOLDER:
            continue
        for g in cc.list_children(c["id"]):
            if g["name"] == DECK_NAME and g["mimeType"] == cc.PPTX:
                return g["id"]
    return None


def chapter_folders(only_city=None):
    kids = cc.list_children(cc.CHAPTERS_PARENT)
    out = [k for k in kids
           if k["mimeType"] == cc.FOLDER and k["name"] not in SKIP_FOLDERS]
    if only_city:
        out = [k for k in out if k["name"] == only_city]
        if not out:
            sys.exit("ABORT: no chapter folder named %r under the Chapters folder."
                     % only_city)
    return sorted(out, key=lambda k: k["name"])


def process(deck_id, latlon, tmpdir, tolerance, write):
    """Inspect one chapter's deck; move its dot when --write and it is off target.

    Returns (status, drift_in_px) where status is ok / moved / would-move, or
    ("unreadable", None) when slide 5 does not hold exactly one green dot and one
    green label — a deck that has drifted from the template is reported, never
    rewritten on a guess."""
    tmp = os.path.join(tmpdir, "d" + deck_id + ".pptx")
    try:
        cc.gws_download(deck_id, tmp)
        current = cc.read_marker_offsets(tmp)
        if current is None:
            return "unreadable", None
        target, _label = cc.marker_offsets(*latlon)
        d = drift_px(current[0], target)
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
    ap.add_argument("--city", help="Only the chapter folder with this NAME "
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

    override = (args.lat, args.lon) if args.lat is not None else None
    coords, bad_rows, unusable = ({}, [], {}) if override else read_chapter_coords()
    folders = chapter_folders(args.city)
    print("%s %d chapter folder(s) against %d sheet row(s), tolerance %.1f px.\n"
          % ("Moving dots in" if args.write else "Checking",
             len(folders), len(coords), args.tolerance))

    results, skipped = [], []
    tmpdir = tempfile.mkdtemp(prefix="aaif-map-dots-")
    try:
        for f in folders:
            row = coords.get(f["id"])
            # Report under the sheet's city, not the folder name: the folder
            # still called "Scotland" is the Edinburgh chapter.
            name = row[0] if row else f["name"]
            if override:
                latlon = override
            elif row:
                latlon = row[1:]
            elif f["id"] in unusable:
                city, why = unusable[f["id"]]
                skipped.append((city, "%s on %s" % (why, CHAPTERS_TAB)))
                print("  ?  %-16s %s — dot left as-is" % (city, why))
                continue
            else:
                skipped.append((f["name"], "no row on %s links to this folder"
                                % CHAPTERS_TAB))
                print("  ?  %-16s no sheet row — dot left as-is" % f["name"])
                continue
            deck_id = find_deck(f["id"])
            if deck_id is None:
                skipped.append((name, "no %s in the chapter folder" % DECK_NAME))
                print("  ?  %-16s no %s — nothing to fix" % (name, DECK_NAME))
                continue
            status, d = process(deck_id, latlon, tmpdir, args.tolerance, args.write)
            if status == "unreadable":
                skipped.append((name, "slide 5 has no single green dot + label"))
                print("  !  %-16s slide 5 markers not recognised — skipped" % name)
                continue
            results.append((name, status, d))
            mark = {"ok": "=", "moved": "+", "would-move": "~"}[status]
            note = {"ok": "already correct", "moved": "moved",
                    "would-move": "WOULD move"}[status]
            print("  %s  %-16s %-15s (%5.1f px off)" % (mark, name, note, d))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    off = [r for r in results if r[1] != "ok"]
    print("\n%d chapter(s) checked: %d already correct, %d %s, %d skipped."
          % (len(results), len(results) - len(off), len(off),
             "moved" if args.write else "off (not moved)", len(skipped)))
    if off:
        worst = max(off, key=lambda r: r[2])
        print("Worst drift: %s at %.1f px." % (worst[0], worst[2]))
    for name, why in skipped:
        print("  skipped: %s — %s" % (name, why))
    for city, why in bad_rows:
        print("  sheet row unusable: %s — %s" % (city, why))
    if off and not args.write:
        print("\nRe-run with --write to move them.")


if __name__ == "__main__":
    main()
