#!/usr/bin/env python3
"""Unit tests for the pure logic in sync_chapters.py (no network/gws)."""
import sys, os
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_chapters
from sync_chapters import (fold, fold_city, slugify, parse_organizers, build_proposal,
                           col_letter)

# The live feed layout — writes are resolved through it by name.
HEADERS = ["Title", "City", "Country", "Generated Geolocation", "Summary", "Image",
           "CTA", "URL for CTA", "Organizers", "Chapter Luma Link",
           "MLOps Community Organizers"]

def chap(row, city, orgs):
    return {"row": row, "city": city, "organizers_raw": orgs}

def entry(row, name, city):
    return {"row": row, "name": name, "city": city, "status": "Accepted"}

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))

def read_chapters_over(rows):
    """Drive the real read_chapters() over a fixture grid.

    Building LAYOUT by hand would let read_chapters' output shape drift away from
    what every write path consumes while all the tests still passed, so the layout
    under test is always the one production actually produces.
    """
    with mock.patch.object(sync_chapters, "get_values", return_value=rows):
        return sync_chapters.read_chapters()

def aborts(fn):
    """True if fn() calls sys.exit — the script's only refusal mechanism."""
    try:
        fn()
    except SystemExit:
        return True
    return False

ROW2 = ["AAIF Boston Chapter", "Boston", "USA", "42,-71", "blurb", "img",
        "Stay Updated", "u", "Kranthi Manchikanti", "luma", ""]
_chapters, _last, LAYOUT = read_chapters_over([HEADERS, ROW2])
check("read_chapters resolves the real layout", LAYOUT["index"]["Organizers"], 8)
check("read_chapters finds the last City row", _last, 2)

# --- fold: case, whitespace, accents (compare folded, write original) --------
check("fold trims/collapses/casefolds", fold("  Chandana  Srinivasa "), "chandana srinivasa")
check("fold strips accents", fold("Médéric Hurier"), fold("Mederic HURIER"))

# --- slugify: default rule + the Denver exception -----------------------------
check("slug default", slugify("New York"), "newyork")
check("slug accents", slugify("Montréal"), "montreal")
check("slug Denver override", slugify("Denver"), "colorado")

# --- parse_organizers ----------------------------------------------------------
check("parse Organizers cell", parse_organizers(" Gleb Lukicov;  Alex Jones ; "), ["Gleb Lukicov", "Alex Jones"])
check("parse empty Organizers cell", parse_organizers(""), [])

CHAPTERS = [chap(2, "Boston", "Kranthi Manchikanti"),
            chap(3, "Delhi NCR", ""),
            chap(4, "San Francisco", "Rahul Parundekar"),
            chap(5, "Silicon Valley", "")]

# --- merge, don't overwrite: dupe detection is case/space/accent-insensitive ---
adds, new_rows, near = build_proposal(
    [entry(2, "kranthi  manchikanti", "Boston"),      # already present (case/space)
     entry(3, "New Person", "Boston")],
    CHAPTERS, 5)
check("merge appends only missing", adds,
      [{"row": 2, "city": "Boston", "names": ["New Person"],
        "new_value": "Kranthi Manchikanti; New Person"}])
check("merge creates no rows", (new_rows, near), ([], []))

# --- city match is case-insensitive; manual B entries are kept -----------------
adds, _, _ = build_proposal([entry(2, "Ana Ruiz", "  boston ")], CHAPTERS, 5)
check("city matched folded, existing name kept", adds[0]["new_value"],
      "Kranthi Manchikanti; Ana Ruiz")

# --- near-miss reported, never written -----------------------------------------
adds, new_rows, near = build_proposal([entry(2, "Kritika Parmar", "Delhi")], CHAPTERS, 5)
check("near-miss no write", (adds, new_rows), ([], []))
check("near-miss candidates", near,
      [{"city": "Delhi", "names": ["Kritika Parmar"],
        "candidates": [("Delhi NCR", 3)]}])

# --- punctuation-only differences are the SAME city, not a new row ---------------
check("fold_city flattens punctuation", fold_city("Washington, DC"), fold_city("Washington DC"))
DC = [chap(2, "Washington DC", "Sushant Kumar")]
adds, new_rows, near = build_proposal([entry(2, "Donte Small", "Washington, DC")], DC, 2)
check("comma variant merges into the existing row", (adds[0]["row"], adds[0]["new_value"]),
      (2, "Sushant Kumar; Donte Small"))
check("comma variant creates no row", (new_rows, near), ([], []))

# --- shared-token near-miss: 'New Delhi' must not fork 'Delhi NCR' ---------------
adds, new_rows, near = build_proposal([entry(2, "Satyam Soni", "New Delhi")], CHAPTERS, 5)
check("shared-token near-miss writes nothing", (adds, new_rows), ([], []))
check("shared-token near-miss names the candidate",
      [c[0] for c in near[0]["candidates"]], ["Delhi NCR"])

# --- SF is NOT mirrored into Silicon Valley -------------------------------------
adds, new_rows, near = build_proposal([entry(2, "Leo Walker", "San Francisco")], CHAPTERS, 5)
check("SF row only, SV untouched", [a["row"] for a in adds], [4])

# --- new rows append after last non-empty row, in intake order ------------------
adds, new_rows, _ = build_proposal(
    [entry(2, "Imran Bagwan", "Pune"), entry(3, "Someone Else", "Pune"),
     entry(4, "Jaime Vélez", "Montréal")],
    CHAPTERS, 5)
check("new rows numbered from last+1",
      [(n["row"], n["city"], n["names"], n["slug"]) for n in new_rows],
      [(6, "Pune", ["Imran Bagwan", "Someone Else"], "pune"),
       (7, "Montréal", ["Jaime Vélez"], "montreal")])

# --- empty Organizers everywhere (first-ever run) ----------------------------------------
adds, _, _ = build_proposal([entry(2, "A B", "Delhi NCR")],
                            [chap(2, "Delhi NCR", "")], 2)
check("empty Organizers populated", adds[0]["new_value"], "A B")

# --- col_letter: writes are addressed by index, not by hand ----------------------
check("col_letter A/I/K", [col_letter(0), col_letter(8), col_letter(10)], ["A", "I", "K"])
check("col_letter past Z", [col_letter(25), col_letter(26)], ["Z", "AA"])

# --- non-Latin city names must survive folding --------------------------------
# An ASCII allowlist folded these to "", which collides every non-Latin city onto
# one key and merges organizers into the wrong row.
check("fold_city keeps non-latin text", bool(fold_city("Москва")) and bool(fold_city("東京")), True)
check("fold_city keeps non-latin cities distinct", fold_city("Москва") == fold_city("東京"), False)
adds, new_rows, near = build_proposal(
    [entry(2, "New Person", "Pune")], [chap(2, "Boston", "K"), chap(3, "東京", "")], 3)
check("a non-latin chapter row does not block new cities",
      ([n["city"] for n in new_rows], near), (["Pune"], []))
adds, _, _ = build_proposal([entry(2, "X Y", "東京")],
                            [chap(2, "東京", ""), chap(3, "Москва", "")], 3)
check("non-latin city merges into its OWN row", [(a["row"], a["city"]) for a in adds],
      [(2, "東京")])

# --- generic tokens must not block a legitimate new city ------------------------
# A near-miss is never written and has no override, so over-matching here blocks
# the city permanently rather than costing one confirmation.
GENERIC = [chap(2, "San Francisco", ""), chap(3, "New York", ""), chap(4, "Mexico City", "")]
for city in ["San Diego", "New Orleans", "Kansas City", "Salt Lake City"]:
    _, nr, nm = build_proposal([entry(2, "P Q", city)], GENERIC, 4)
    check("%r is added, not blocked by a generic token" % city,
          ([n["city"] for n in nr], nm), ([city], []))
_, nr, nm = build_proposal([entry(2, "P Q", "New Delhi")], [chap(2, "Delhi NCR", "")], 2)
check("a discriminating shared token still blocks", ([n["city"] for n in nr],
      [c[0] for c in nm[0]["candidates"]]), ([], ["Delhi NCR"]))

# --- empty slug must abort, never publish https://luma.com/aaif- ----------------
check("city with no ascii aborts rather than writing an empty slug",
      aborts(lambda: build_proposal([entry(2, "X Y", "東京")], [chap(2, "Boston", "")], 2)), True)

# --- read_chapters refuses layouts it cannot address safely ---------------------
check("duplicate header aborts",
      aborts(lambda: read_chapters_over([HEADERS + ["Organizers"], ROW2 + [""]])), True)
check("missing derived column aborts",
      aborts(lambda: read_chapters_over([[h for h in HEADERS if h != "CTA"], ROW2[:6] + ROW2[7:]])),
      True)
check("non-empty row below the last City row aborts",
      aborts(lambda: read_chapters_over(
          [HEADERS, ROW2, ["", "", "France", "", "half-written summary", "", "", "", "", "", ""]])),
      True)
check("blank trailing rows are fine", read_chapters_over([HEADERS, ROW2, [""] * 11])[1], 2)

# --- col_letter rejects a negative index instead of returning "" ----------------
try:
    col_letter(-1); raised = False
except ValueError:
    raised = True
check("col_letter(-1) raises", raised, True)

# --- public-form text is charset/length checked before it can reach the feed ----
check("markup in a name aborts",
      aborts(lambda: sync_chapters.check_public_text("name", "<img src=x onerror=1>", 5)), True)
check("control characters abort",
      aborts(lambda: sync_chapters.check_public_text("city", "Bo\x00ston", 5)), True)
check("over-long text aborts",
      aborts(lambda: sync_chapters.check_public_text("name", "A" * 200, 5)), True)
check("an ordinary accented name passes",
      aborts(lambda: sync_chapters.check_public_text("name", "Médéric Hurier", 5)), False)

# --- apply_changes: exact ranges, column order, RAW (gws mocked, no network) -----
CITY_COL = [["City"], ["Boston"]]        # row 2 = Boston, row 6 still empty
with mock.patch.object(sync_chapters, "gws_json") as gj, \
     mock.patch.object(sync_chapters, "get_values", return_value=CITY_COL):
    n = sync_chapters.apply_changes(
        [{"row": 2, "city": "Boston", "names": ["New Person"],
          "new_value": "Kranthi Manchikanti; New Person"}],
        [{"row": 6, "city": "Pune", "names": ["Imran Bagwan"], "slug": "pune"}],
        LAYOUT)
    body = gj.call_args.kwargs["body"]
check("apply_changes writes both changes", n, 2)
check("apply_changes uses RAW (no formula injection)", body["valueInputOption"], "RAW")
check("apply_changes ranges and column order", body["data"],
      [{"range": "'%s'!I2" % sync_chapters.CHAPTERS_TAB,
        "values": [["Kranthi Manchikanti; New Person"]]},
       {"range": "'%s'!A6:K6" % sync_chapters.CHAPTERS_TAB,
        "values": [["AAIF Pune Chapter", "Pune", "", "", "", "", "Stay Updated",
                    "https://luma.com/aaif-pune", "Imran Bagwan",
                    "https://luma.com/aaif-pune", ""]]}])

# --- the sheet moving under us must abort BEFORE the write ----------------------
with mock.patch.object(sync_chapters, "gws_json") as gj, \
     mock.patch.object(sync_chapters, "get_values", return_value=[["City"], ["Lisbon"]]):
    shifted = aborts(lambda: sync_chapters.apply_changes(
        [{"row": 2, "city": "Boston", "names": ["X"], "new_value": "X"}], [], LAYOUT))
check("a shifted row aborts", shifted, True)
check("a shifted row writes nothing", gj.called, False)

with mock.patch.object(sync_chapters, "gws_json") as gj, \
     mock.patch.object(sync_chapters, "get_values",
                       return_value=[["City"], ["Boston"], ["Someone Else"]]):
    taken = aborts(lambda: sync_chapters.apply_changes(
        [], [{"row": 3, "city": "Pune", "names": ["I B"], "slug": "pune"}], LAYOUT))
check("an occupied target row aborts", taken, True)
check("an occupied target row writes nothing", gj.called, False)

# --- a column reorder must move the writes, not corrupt neighbours ---------------
SWAPPED = ["City", "Organizers", "MLOps Community Organizers", "Chapter Luma Link"]
SWAPPED_LAYOUT = {"headers": SWAPPED, "index": {h: i for i, h in enumerate(SWAPPED)}}
with mock.patch.object(sync_chapters, "gws_json") as gj, \
     mock.patch.object(sync_chapters, "get_values", return_value=[["City"], ["Boston"]]):
    sync_chapters.apply_changes(
        [{"row": 2, "city": "Boston", "names": ["X"], "new_value": "X"}], [], SWAPPED_LAYOUT)
    check("adds follow the Organizers header", gj.call_args.kwargs["body"]["data"][0]["range"],
          "'%s'!B2" % sync_chapters.CHAPTERS_TAB)

# --- new rows never write into the editorial columns -----------------------------
vals = sync_chapters.new_row_values(
    {"row": 6, "city": "Pune", "names": ["Imran Bagwan"], "slug": "pune"}, LAYOUT)
check("new row is full feed width", len(vals), len(HEADERS))
check("editorial columns left blank",
      [vals[LAYOUT["index"][c]] for c in sync_chapters.EDITORIAL_COLUMNS], ["", "", "", ""])
check("MLOps history untouched on new rows", vals[LAYOUT["index"]["MLOps Community Organizers"]], "")

# --- gws_json survives U+2028 inside JSON string values (the splitlines() bug) ---
raw = '{"a": "line1\u2028line2"}\n'
with mock.patch.object(sync_chapters.subprocess, "run",
                       return_value=mock.Mock(returncode=0, stdout=raw)):
    check("gws_json keeps U+2028 inside values", sync_chapters.gws_json("sheets", "get"),
          {"a": "line1\u2028line2"})

print()
sys.exit("FAIL: %d test(s) failed" % fails if fails else None)
