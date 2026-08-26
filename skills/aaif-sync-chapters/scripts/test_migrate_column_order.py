#!/usr/bin/env python3
"""Unit tests for migrate_column_order.py (no network/gws).

The fixture is a POST-split workbook — twelve columns with `Interested in`
appended at L, both dropdowns, the Status colour rules and the Guide's
repointed formulas — because that is the only shape this migration runs
against. It is built from migrate_interested_in's own fixture, put through
that migration, so the input here is literally what the previous script
produces rather than a hand-written guess at it.
"""
import os, sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_column_order as mo
import migrate_interested_in as mig

# The fixture module is a plain script: importing it RUNS its checks and prints
# its own verdict. Silence that, or this suite's output opens with another
# suite's "All checks passed." and a real failure here reads as coming from
# there. Its exit status still surfaces — it calls sys.exit on failure.
import contextlib, io as _io
with contextlib.redirect_stdout(_io.StringIO()):
    import test_migrate_interested_in as fx
from sync_crm import (Attendees, NEW_COLUMN, X, check_dropdowns,
                      col_of, load_parts, save_parts, sheet_part)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


#: The layout every chapter had after migrate_interested_in — the input here.
APPENDED = ["Full name", "Signal", "Trusted/Regular", "Status", "Notes (CRM)",
            "Email", "LinkedIn URL", "Company", "Role / title",
            "Technical expertise", "What brings you here?", "Interested in"]
#: ...and the one this migration produces.
WANTED = ["Full name", "Signal", "Trusted/Regular", "Interested in", "Status",
          "Notes (CRM)", "Email", "LinkedIn URL", "Company", "Role / title",
          "Technical expertise", "What brings you here?"]

MAP = mo.move_mapping({h: i for i, h in enumerate(APPENDED)})


def split_book(rows_data=()):
    """A workbook in the post-split, pre-reorder shape — produced by actually
    running the previous migration, not by describing its output."""
    names, parts = fx.make_pre_xlsx(rows_data=rows_data)
    att = Attendees(parts, sheet_part(parts, "Attendees"),
                    require=mig.PRE_SPLIT_HEADERS)
    book = {"att": att, "parts": parts, "names": names,
            "part": sheet_part(parts, "Attendees")}
    raw, _ = mig.apply_plan(book, mig.plan(book))
    names, parts = load_parts(raw)
    return names, parts, Attendees(parts, sheet_part(parts, "Attendees"))


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------
check("the fixture starts in the appended layout",
      mo.target_order({h: i for i, h in enumerate(APPENDED)}), APPENDED)
check("the move produces the wanted order",
      mo.target_order({h: MAP[i] for i, h in enumerate(APPENDED)}), WANTED)
check("Interested in lands immediately before Status",
      WANTED.index(NEW_COLUMN) + 1, WANTED.index("Status"))
check("the mapping is a bijection", sorted(MAP.values()), list(range(12)))
check("columns left of the insert point do not move",
      [MAP[i] for i in (0, 1, 2)], [0, 1, 2])
check("everything from Status rightward shifts by one",
      [MAP[i] for i in range(3, 11)], list(range(4, 12)))
check("re-planning an already-ordered workbook is a no-op",
      mo.move_mapping({h: i for i, h in enumerate(WANTED)}), None)

# A chapter that added a column of its own keeps its position on the right.
_extra = dict({h: i for i, h in enumerate(APPENDED)}, **{"Chapter notes": 12})
check("an extra column past the headers stays where it is",
      mo.move_mapping(_extra)[12], 12)
check("...and the wanted order is unaffected by it",
      mo.target_order({h: mo.move_mapping(_extra)[i] for h, i in _extra.items()}),
      WANTED + ["Chapter notes"])


# ---------------------------------------------------------------------------
# Reference rewriting
# ---------------------------------------------------------------------------
for ref, want in (("D2:D1000", "E2:E1000"),     # Status dropdown + colours
                  ("L2:L1000", "D2:D1000"),     # Interested in dropdown
                  ("B2:B1000", "B2:B1000"),     # Signal — untouched
                  ("A2:A1000", "A2:A1000"),
                  ("C2:C1000", "C2:C1000"),
                  ("$A$1:$K$1", "$A$1:$L$1")):  # autoFilter, widened
    check("remap_ref(%-11r)" % ref, mo.remap_ref(ref, MAP), want)
check("the $ anchors survive", mo.remap_ref("$D$2", MAP), "$E$2")
check("a cf rule that tests another column is rewritten",
      mo.remap_formula('$B2="Non-grata"', MAP), '$B2="Non-grata"')
check("...and one testing a column that DOES move",
      mo.remap_formula('$D2="Accepted"', MAP), '$E2="Accepted"')

# The Guide's cross-sheet references, and only those.
GUIDE_FILTER = ("=FILTER({Attendees!A2:A, Attendees!L2:L, Attendees!H2:H, "
                "Attendees!I2:I, Attendees!K2:K, Attendees!B2:B, Attendees!E2:E}, "
                '(Attendees!C2:C="Yes")+(Attendees!B2:B="High"), '
                'Attendees!B2:B<>"Non-grata")')
check("the live list is remapped, open-ended ranges included",
      mo.remap_formula(GUIDE_FILTER, MAP, prefix="Attendees!"),
      "=FILTER({Attendees!A2:A, Attendees!D2:D, Attendees!I2:I, "
      "Attendees!J2:J, Attendees!L2:L, Attendees!B2:B, Attendees!F2:F}, "
      '(Attendees!C2:C="Yes")+(Attendees!B2:B="High"), '
      'Attendees!B2:B<>"Non-grata")')
check("the dashboard tiles follow the column",
      mo.remap_formula('COUNTIF(Attendees!L2:L1000,"*Speaker*")', MAP,
                       prefix="Attendees!"),
      'COUNTIF(Attendees!D2:D1000,"*Speaker*")')
check("an unrelated Attendees column is still remapped",
      mo.remap_formula("COUNTA(Attendees!A2:A1000)", MAP, prefix="Attendees!"),
      "COUNTA(Attendees!A2:A1000)")
# THE trap: the Guide has its own cells, and they must not move with the
# Attendees layout. Without the prefix scope the dashboard rewrites itself.
check("a Guide-LOCAL reference is left alone",
      mo.remap_formula("B5+Attendees!E2", MAP, prefix="Attendees!"),
      "B5+Attendees!F2")
check("...even when it names a column that moves",
      mo.remap_formula("SUM(D2:D9)", MAP, prefix="Attendees!"), "SUM(D2:D9)")


# ---------------------------------------------------------------------------
# The move, end to end
# ---------------------------------------------------------------------------
ROWS = [
    fx.row("Ada", "Organizer", "Intake: Organizer · Accepted · 2026-08-07", "ada@x.io"),
    fx.row("Bo", "Host", "Intake: Host · New · 2026-08-21", "bo@x.io"),
    fx.row("Eve", "Declined", "Pitched from the floor.", "eve@x.io"),
]
names, parts, att = split_book(ROWS)
check("the input really is the appended layout", mo.target_order(att.headers), APPENDED)
before = mo.snapshot(att)
check("the snapshot captured the rows", len(before), 3)

mapping = mo.move_mapping(att.headers)
raw, snap = mo.apply_move({"att": att, "parts": parts, "names": names,
                           "part": sheet_part(parts, "Attendees")}, mapping)
mo_names, mo_parts = load_parts(raw)
post = Attendees(mo_parts, sheet_part(mo_parts, "Attendees"))

check("every zip part survives", sorted(mo_names), sorted(names))
check("the columns are in the wanted order", mo.target_order(post.headers), WANTED)
check("re-planning the moved workbook is a no-op", mo.move_mapping(post.headers), None)

# THE check that matters. Compared BY HEADER, so a workbook whose refs were
# renumbered inconsistently fails here even though it opens cleanly and still
# has twelve columns.
check("every row holds exactly the values it held before", mo.snapshot(post), before)
check("...spot-checked on the row the split was about",
      [post.value(3, h) for h in ("Full name", "Status", NEW_COLUMN, "Email")],
      ["Bo", "Prospect", "Host", "bo@x.io"])
check("a human's Declined still sits with the right person",
      [post.value(4, h) for h in ("Full name", "Status", "Email")],
      ["Eve", "Declined", "eve@x.io"])

# The dropdowns and colour rules must have FOLLOWED their columns, not stayed
# at the letters they were written for.
check("both dropdowns followed their columns", check_dropdowns(post), [])
check("the Status colour rules follow Status",
      mig.cf_plan(post, mo_parts), "ok")
d = mo_parts[sheet_part(mo_parts, "Attendees")].decode()
check("the colour block now names column E",
      'sqref="E2:E1000"' in d and 'sqref="D2:D1000"' in d, True)
check("the Signal rules and dropdown did not move",
      d.count('sqref="B2:B1000"'), 2)   # 1 cf block + 1 dropdown
check("the name-turns-red rule still tests Signal",
      '$B2="Non-grata"' in d or "$B2=&quot;Non-grata&quot;" in d, True)

# Structure.
check("the dimension covers the last column",
      ET.fromstring(mo_parts["xl/worksheets/sheet1.xml"]).find(X + "dimension").get("ref"),
      "A1:L4")
check("the autoFilter was widened to cover every column",
      ET.fromstring(mo_parts["xl/worksheets/sheet1.xml"]).find(X + "autoFilter").get("ref"),
      "$A$1:$L$1")
for row in post.rows.values():
    cols = [col_of(c.get("r")) for c in row]
    check("row %s cells stay in ascending column order" % row.get("r"),
          cols, sorted(cols))

# The moved column keeps the width the previous migration gave it, and its
# neighbours keep theirs — <col> entries are RANGES, so a naive rewrite
# re-widens whatever shared a run with the moved column.
widths = {}
for c in post.root.find(X + "cols"):
    for i in range(int(c.get("min")) - 1, int(c.get("max")) - 1 + 1):
        widths[i] = c.get("width")
check("the moved column kept its width", widths.get(3), mig.NEW_COL_WIDTH)
check("column A kept its width", widths.get(0), "22")
check("no neighbour inherited the moved column's width",
      [i for i, w in widths.items() if w == mig.NEW_COL_WIDTH], [3])

# The Guide followed too.
g = mo_parts[sheet_part(mo_parts, "Guide")].decode()
check("the dashboard tiles point at the moved column",
      'COUNTIF(Attendees!D2:D1000,"*Speaker*")' in g, True)
check("no tile still points at the old position",
      'Attendees!L2:L1000' in g, False)
check("the unrelated COUNTA is untouched",
      "COUNTA(Attendees!A2:A1000)" in g, True)

# A round trip through the real reader, which is what the verify does.
rt_names, rt_parts = load_parts(save_parts(mo_names, mo_parts))
rt = Attendees(rt_parts, sheet_part(rt_parts, "Attendees"))
check("the workbook survives a save/load round-trip",
      mo.target_order(rt.headers), WANTED)
check("...with the data still intact", mo.snapshot(rt), before)


# ---------------------------------------------------------------------------
# The PREVIOUS migration must stay truthful about a reordered workbook
# ---------------------------------------------------------------------------
# migrate_interested_in derives the Guide's target formulas from the live header
# map. If it hardcoded the letters, then the moment this reorder ran it would
# report three unrecognised Guide formulas on all 83 chapters, forever, about a
# Guide that is perfectly correct.
post_headers = dict(post.headers)
check("the split migration sees a reordered workbook as fully migrated",
      mig.plan({"att": post, "parts": mo_parts})["any"], False)
check("...with no Guide formulas reported unrecognised",
      mig.plan_guide(mo_parts, post_headers)[1], [])
check("...and its dropdown/colour checks still pass",
      (mig.dv_plan(post), mig.cf_plan(post, mo_parts)), ([], "ok"))

# And the sync is layout-independent by construction — it addresses by header
# name only, so the move must be invisible to it.
check("the sync can open the reordered workbook", check_dropdowns(post), [])
check("...and reads every column back by name",
      sorted(post.headers), sorted(WANTED))


print()
if fails:
    print("FAILED %d check(s)" % fails)
    sys.exit(1)
print("All checks passed.")
