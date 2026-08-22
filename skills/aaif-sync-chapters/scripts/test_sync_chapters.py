#!/usr/bin/env python3
"""Unit tests for the pure logic in sync_chapters.py (no network/gws)."""
import sys, os
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_chapters
from sync_chapters import (fold, fold_city, slugify, parse_organizers, build_proposal,
                           col_letter)

# The live feed layout — writes are resolved through it by name.
HEADERS = ["Title", "City", "Country",
           # The resource map, inserted after Country by migrate_resource_columns.
           "Chapter Folder", "Slack Channel", "Organizer Channel", "Country Channel",
           "Organizer Handles",
           "Generated Geolocation", "Summary", "Image",
           "CTA", "URL for CTA", "Organizers", "Chapter Luma Link",
           "MLOps Community Organizers",
           # Column Q since 2026-08-22: squatted/superseded channel names that
           # provision_channels refuses to create or rename into.
           "Erstwhile Channels"]

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

ROW2 = ["AAIF Boston Chapter", "Boston", "USA",
        "https://drive.google.com/drive/folders/F1", "boston", "boston-organizers", "none",
        "@ada",
        "42,-71", "blurb", "img",
        "Stay Updated", "u", "Wedge Antilles", "luma", "", ""]
_chapters, _last, LAYOUT = read_chapters_over([HEADERS, ROW2])
check("fixture row matches the header width", len(ROW2), len(HEADERS))
check("read_chapters resolves the real layout", LAYOUT["index"]["Organizers"], 13)
check("read_chapters finds the last City row", _last, 2)

# --- fold: case, whitespace, accents (compare folded, write original) --------
check("fold trims/collapses/casefolds", fold("  Chandana  Srinivasa "), "chandana srinivasa")
check("fold strips accents", fold("Padmé Naberrie"), fold("Padme NABERRIE"))

# --- slugify: default rule + the Denver exception -----------------------------
check("slug default", slugify("New York"), "newyork")
check("slug accents", slugify("Montréal"), "montreal")
check("slug Denver override", slugify("Denver"), "colorado")

# --- parse_organizers ----------------------------------------------------------
check("parse Organizers cell", parse_organizers(" Mon Mothma;  Kit Fisto ; "), ["Mon Mothma", "Kit Fisto"])
check("parse empty Organizers cell", parse_organizers(""), [])

CHAPTERS = [chap(2, "Boston", "Wedge Antilles"),
            chap(3, "Delhi NCR", ""),
            chap(4, "San Francisco", "Ben Kenobi"),
            chap(5, "Silicon Valley", "")]

# --- merge, don't overwrite: dupe detection is case/space/accent-insensitive ---
adds, new_rows, near = build_proposal(
    [entry(2, "wedge  antilles", "Boston"),           # already present (case/space)
     entry(3, "New Person", "Boston")],
    CHAPTERS, 5)
check("merge appends only missing", adds,
      [{"row": 2, "city": "Boston", "names": ["New Person"],
        "new_value": "Wedge Antilles; New Person"}])
check("merge creates no rows", (new_rows, near), ([], []))

# --- city match is case-insensitive; manual B entries are kept -----------------
adds, _, _ = build_proposal([entry(2, "Ana Ruiz", "  boston ")], CHAPTERS, 5)
check("city matched folded, existing name kept", adds[0]["new_value"],
      "Wedge Antilles; Ana Ruiz")

# --- near-miss reported, never written -----------------------------------------
adds, new_rows, near = build_proposal([entry(2, "Leia Organa", "Delhi")], CHAPTERS, 5)
check("near-miss no write", (adds, new_rows), ([], []))
check("near-miss candidates", near,
      [{"city": "Delhi", "names": ["Leia Organa"],
        "candidates": [("Delhi NCR", 3)]}])

# --- punctuation-only differences are the SAME city, not a new row ---------------
check("fold_city flattens punctuation", fold_city("Washington, DC"), fold_city("Washington DC"))
DC = [chap(2, "Washington DC", "Han Solo")]
adds, new_rows, near = build_proposal([entry(2, "Lando Calrissian", "Washington, DC")], DC, 2)
check("comma variant merges into the existing row", (adds[0]["row"], adds[0]["new_value"]),
      (2, "Han Solo; Lando Calrissian"))
check("comma variant creates no row", (new_rows, near), ([], []))

# --- shared-token near-miss: 'New Delhi' must not fork 'Delhi NCR' ---------------
adds, new_rows, near = build_proposal([entry(2, "Mace Windu", "New Delhi")], CHAPTERS, 5)
check("shared-token near-miss writes nothing", (adds, new_rows), ([], []))
check("shared-token near-miss names the candidate",
      [c[0] for c in near[0]["candidates"]], ["Delhi NCR"])

# --- SF is NOT mirrored into Silicon Valley -------------------------------------
adds, new_rows, near = build_proposal([entry(2, "Luke Skywalker", "San Francisco")], CHAPTERS, 5)
check("SF row only, SV untouched", [a["row"] for a in adds], [4])

# --- new rows append after last non-empty row, in intake order ------------------
adds, new_rows, _ = build_proposal(
    [entry(2, "Owen Lars", "Pune"), entry(3, "Someone Else", "Pune"),
     entry(4, "Zéno Vélar", "Montréal")],
    CHAPTERS, 5)
check("new rows numbered from last+1",
      [(n["row"], n["city"], n["names"], n["slug"]) for n in new_rows],
      [(6, "Pune", ["Owen Lars", "Someone Else"], "pune"),
       (7, "Montréal", ["Zéno Vélar"], "montreal")])

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
_i_cta = HEADERS.index("CTA")
check("missing derived column aborts",
      aborts(lambda: read_chapters_over(
          [[h for h in HEADERS if h != "CTA"], ROW2[:_i_cta] + ROW2[_i_cta + 1:]])),
      True)
_half = [""] * len(HEADERS)
_half[HEADERS.index("Country")] = "France"
_half[HEADERS.index("Summary")] = "half-written summary"
check("non-empty row below the last City row aborts",
      aborts(lambda: read_chapters_over([HEADERS, ROW2, _half])), True)
check("blank trailing rows are fine",
      read_chapters_over([HEADERS, ROW2, [""] * len(HEADERS)])[1], 2)

# --- col_letter rejects a negative index instead of returning "" ----------------
try:
    col_letter(-1); raised = False
except ValueError:
    raised = True
check("col_letter(-1) raises", raised, True)

# --- public-form text is charset/length checked before it can reach the feed ----
# Skip-and-report, not abort: one hostile row must not hold every other
# chapter's sync hostage, but the flagged value must still never be written.
check("markup in a name is flagged",
      sync_chapters.bad_public_text("name", "<img src=x onerror=1>") is not None, True)
check("control characters are flagged",
      sync_chapters.bad_public_text("city", "Bo\x00ston") is not None, True)
check("over-long text is flagged",
      sync_chapters.bad_public_text("name", "A" * 200) is not None, True)
check("an ordinary accented name passes",
      sync_chapters.bad_public_text("name", "Padmé Naberrie"), None)

# read_intake excludes the offending ROW and reports it; everything else syncs.
INTAKE_HEADERS = ["Status", "Full name", "City (Existing)", "City (New)",
                  "Run events before?", "Why organize / ties"]
def intake_row(status, name, city):
    return [status, name, city, "", "", ""]
with mock.patch.object(sync_chapters, "get_values", return_value=[
        INTAKE_HEADERS,
        intake_row("Accepted", "Ada Lovelace", "Boston"),
        intake_row("Accepted", "<b>Mallory</b>", "Boston"),
        intake_row("Accepted", "Owen Lars", "Pune")]):
    _e, _u, _c, _d, _m = sync_chapters.read_intake()
check("a malformed row is excluded, the rest still sync",
      [e["name"] for e in _e], ["Ada Lovelace", "Owen Lars"])
check("the malformed row is reported with its row and reason",
      (_m[0]["row"], _m[0]["city"], "angle brackets" in _m[0]["why"]),
      (3, "Boston", True))
check("a malformed row is never an unresolved/dupe entry", (_u, _d), ([], []))

# --- apply_changes: exact ranges, column order, RAW (gws mocked, no network) -----
CITY_COL = [["City"], ["Boston"]]        # row 2 = Boston, row 6 still empty
with mock.patch.object(sync_chapters, "gws_json") as gj, \
     mock.patch.object(sync_chapters, "get_values", return_value=CITY_COL):
    n = sync_chapters.apply_changes(
        [{"row": 2, "city": "Boston", "names": ["New Person"],
          "new_value": "Wedge Antilles; New Person"}],
        [{"row": 6, "city": "Pune", "names": ["Owen Lars"], "slug": "pune"}],
        LAYOUT)
    body = gj.call_args.kwargs["body"]
check("apply_changes writes both changes", n, 2)
check("apply_changes uses RAW (no formula injection)", body["valueInputOption"], "RAW")
# Ranges are derived from LAYOUT, not spelled out: the column letters moved once
# already (the resource block was inserted after Country), and a hardcoded "I2"
# fails as a stale expectation rather than as a real regression. What is being
# asserted is that the write FOLLOWS the header row — so the expectation has to
# be computed the same way, and the per-column values below are what pin the
# order.
_ORG_COL = sync_chapters.col_letter(LAYOUT["index"]["Organizers"])
_LAST_COL = sync_chapters.col_letter(len(HEADERS) - 1)
_expected_new = [""] * len(HEADERS)
for _c, _v in (("Title", "AAIF Pune Chapter"), ("City", "Pune"),
               ("CTA", "Stay Updated"),
               ("URL for CTA", "https://luma.com/aaif-pune"),
               ("Organizers", "Owen Lars"),
               ("Chapter Luma Link", "https://luma.com/aaif-pune")):
    _expected_new[LAYOUT["index"][_c]] = _v
check("apply_changes ranges and column order", body["data"],
      [{"range": "'%s'!%s2" % (sync_chapters.CHAPTERS_TAB, _ORG_COL),
        "values": [["Wedge Antilles; New Person"]]},
       {"range": "'%s'!A6:%s6" % (sync_chapters.CHAPTERS_TAB, _LAST_COL),
        "values": [_expected_new]}])
check("the derived new-row write lands past the resource block",
      _ORG_COL, "N")

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
    {"row": 6, "city": "Pune", "names": ["Owen Lars"], "slug": "pune"}, LAYOUT)
check("new row is full feed width", len(vals), len(HEADERS))
check("editorial columns left blank",
      [vals[LAYOUT["index"][c]] for c in sync_chapters.EDITORIAL_COLUMNS], ["", "", "", ""])
# The resource map is sync_resources.py's job, from Drive and Slack. A new row
# guessing its own folder or channel here would write an unverified claim into
# the column the audit trusts.
check("resource columns left blank on new rows",
      [vals[LAYOUT["index"][c]] for c in sync_chapters.RESOURCE_COLUMNS],
      [""] * len(sync_chapters.RESOURCE_COLUMNS))
check("MLOps history untouched on new rows", vals[LAYOUT["index"]["MLOps Community Organizers"]], "")

# --- gws_json survives U+2028 inside JSON string values (the splitlines() bug) ---
raw = '{"a": "line1\u2028line2"}\n'
with mock.patch.object(sync_chapters.subprocess, "run",
                       return_value=mock.Mock(returncode=0, stdout=raw)):
    check("gws_json keeps U+2028 inside values", sync_chapters.gws_json("sheets", "get"),
          {"a": "line1\u2028line2"})

# --- resolve_city: the one function sync_about and the feed MUST agree on ------
check("City (New) wins over both", sync_chapters.resolve_city("Boston", "Pune"),
      "Pune")
check("City (Existing) used when New is blank",
      sync_chapters.resolve_city("Boston", ""), "Boston")
check("an Other placeholder resolves to nothing",
      sync_chapters.resolve_city("Other (please specify)", ""), "")
check("both blank -> unresolved", sync_chapters.resolve_city("", ""), "")

# --- report-mode exit codes: the contract nightly.py consumes ------------------
# 0 = in sync, 2 = drift. Tested through main() with compute() mocked, because
# the return statement IS the feature — a wrapper reading these codes must not
# see 0 on a drifted report.
def exit_code(adds, new_rows, argv):
    state = mock.Mock(adds=adds, new_rows=new_rows)
    with mock.patch.object(sync_chapters, "compute", return_value=state), \
         mock.patch.object(sync_chapters, "print_report", lambda s: None), \
         mock.patch.object(sys, "argv", ["sync_chapters.py"] + argv):
        return sync_chapters.main()


check("report mode, in sync -> exit 0", exit_code([], [], []), 0)
check("report mode, drift -> exit 2",
      exit_code([{"row": 2}], [], []), 2)
check("write mode with nothing to do -> exit 0",
      exit_code([], [], ["--write"]), 0)

# --- partition_new_rows: a city with no live Luma page holds back, not aborts ---
# One pending page used to sys.exit the whole write, freezing every OTHER
# chapter's sync behind a manual step that can lag by weeks.
def nrow(row, city, luma=None):
    d = {"row": row, "city": city, "names": ["A"], "slug": fold_city(city)}
    if luma is not None:
        d["luma"] = luma
    return d

_rows = [nrow(82, "Pune", "absent"), nrow(83, "Boston", "live"),
         nrow(84, "Oslo", "unknown"), nrow(85, "Kyoto", "live")]
_write, _held = sync_chapters.partition_new_rows(_rows, 81, False)
check("only live rows are written",
      [n["city"] for n in _write], ["Boston", "Kyoto"])
check("absent and unknown are both held",
      [n["city"] for n in _held], ["Pune", "Oslo"])
check("written rows are renumbered compactly after last_row (no blank gap)",
      [n["row"] for n in _write], [82, 83])
check("the caller's proposal is not mutated",
      [n["row"] for n in _rows], [82, 83, 84, 85])
check("a row that never got a luma status is held, not written",
      sync_chapters.partition_new_rows([nrow(82, "Pune")], 81, False),
      ([], [nrow(82, "Pune")]))
_write, _held = sync_chapters.partition_new_rows(_rows, 81, True)
check("--allow-missing-luma writes everything",
      ([n["city"] for n in _write], _held),
      (["Pune", "Boston", "Oslo", "Kyoto"], []))
check("--allow-missing-luma still renumbers from last_row",
      [n["row"] for n in _write], [82, 83, 84, 85])


# --- --redact: stdout masking (default on under CI) ----------------------------
sync_chapters.REDACT = False
check("redaction off: email passes through", sync_chapters.redact_email("ada@x.com"), "ada@x.com")
check("redaction off: name passes through", sync_chapters.redact_name("Ada Lovelace"), "Ada Lovelace")
sync_chapters.REDACT = True
try:
    check("redacted email keeps one char + domain", sync_chapters.redact_email("ada@x.com"), "a***@x.com")
    check("redacted name is initials", sync_chapters.redact_name("ada lovelace"), "A. L.")
    check("a non-email is left alone", sync_chapters.redact_email("Boston"), "Boston")
    check("empty values survive", (sync_chapters.redact_email(""), sync_chapters.redact_name("")), ("", ""))
finally:
    sync_chapters.REDACT = False

sync_chapters.REDACT = True
try:
    check("the Organizers cell is masked name by name",
          sync_chapters.redact_names_cell("Ada Lovelace; Grace Hopper"), "A. L.; G. H.")
finally:
    sync_chapters.REDACT = False

print()
sys.exit("FAIL: %d test(s) failed" % fails if fails else None)
