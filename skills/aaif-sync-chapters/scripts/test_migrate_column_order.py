#!/usr/bin/env python3
"""Unit tests for migrate_column_order.py (no network/gws).

The fixture is a POST-split workbook — twelve columns with `Interested in`
appended at L, both dropdowns, the Status colour rules and the Guide's
repointed formulas — because that is the only shape this migration runs
against. It is built from migrate_interested_in's own fixture, put through
that migration, so the input here is literally what the previous script
produces rather than a hand-written guess at it.
"""
import os, re, sys, tempfile
from unittest import mock
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
check("a cf rule testing a column that MOVES is rewritten",
      mo.remap_formula('$D2="Accepted"', MAP), '$E2="Accepted"')
check("...and one testing a column that does not is left alone",
      mo.remap_formula('$B2="Non-grata"', MAP), '$B2="Non-grata"')

# REGRESSION (found in review, after this had already run on 83 workbooks).
# The open-ended-range pass ran a SECOND time over a range the first pass had
# already rewritten, because its lookahead `(?![A-Z0-9])` did not exclude `$`:
# `$L$2:$L$1000` -> `$D$2:$D$1000` -> `$D$2:$E$1000`, a range spanning TWO
# columns, inside a live dashboard formula, with no error anywhere. Only
# relative refs appeared in the shipped template, which is the sole reason the
# suite missed it — Sheets writes absolute ranges routinely.
for f, want in (("Attendees!$L$2:$L$1000", "Attendees!$D$2:$D$1000"),
                ("Attendees!$D$2:$D$1000", "Attendees!$E$2:$E$1000"),
                ("Attendees!$B$2:$B$1000", "Attendees!$B$2:$B$1000"),
                ("Attendees!$A$1:$K$1",    "Attendees!$A$1:$L$1"),
                ("Attendees!L2:L1000",     "Attendees!D2:D1000"),
                ("Attendees!L2:L",         "Attendees!D2:D")):
    got = mo.remap_formula(f, MAP, cross_sheet=True)
    check("absolute/relative range %-24r" % f, got, want)
check("no remapped range ever spans two columns",
      [g for g in (mo.remap_formula("Attendees!$%s$2:$%s$1000" % (c, c), MAP,
                                    cross_sheet=True)
                   for c in "ABCDEFGHIJKL")
       if len(set(re.findall(r"\$([A-Z]+)\$", g))) != 1], [])

# The Guide's cross-sheet references, and only those.
GUIDE_FILTER = ("=FILTER({Attendees!A2:A, Attendees!L2:L, Attendees!H2:H, "
                "Attendees!I2:I, Attendees!K2:K, Attendees!B2:B, Attendees!E2:E}, "
                '(Attendees!C2:C="Yes")+(Attendees!B2:B="High"), '
                'Attendees!B2:B<>"Non-grata")')
check("the live list is remapped, open-ended ranges included",
      mo.remap_formula(GUIDE_FILTER, MAP, cross_sheet=True),
      "=FILTER({Attendees!A2:A, Attendees!D2:D, Attendees!I2:I, "
      "Attendees!J2:J, Attendees!L2:L, Attendees!B2:B, Attendees!F2:F}, "
      '(Attendees!C2:C="Yes")+(Attendees!B2:B="High"), '
      'Attendees!B2:B<>"Non-grata")')
check("the dashboard tiles follow the column",
      mo.remap_formula('COUNTIF(Attendees!L2:L1000,"*Speaker*")', MAP,
                       cross_sheet=True),
      'COUNTIF(Attendees!D2:D1000,"*Speaker*")')
check("an unrelated Attendees column is still remapped",
      mo.remap_formula("COUNTA(Attendees!A2:A1000)", MAP, cross_sheet=True),
      "COUNTA(Attendees!A2:A1000)")
# THE trap: the Guide has its own cells, and they must not move with the
# Attendees layout. Without the prefix scope the dashboard rewrites itself.
check("a Guide-LOCAL reference is left alone",
      mo.remap_formula("B5+Attendees!E2", MAP, cross_sheet=True),
      "B5+Attendees!F2")
check("...even when it names a column that moves",
      mo.remap_formula("SUM(D2:D9)", MAP, cross_sheet=True), "SUM(D2:D9)")


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
check("the name-turns-red rule still tests Signal, in the fixture's encoding",
      '$B2="Non-grata"' in d, True)

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


# A chapter's own data column past the last HEADER is outside move_mapping
# (which is built from headers), so taking the sheet's new extent from the
# mapping narrowed <dimension> and <autoFilter> past that data — and
# Attendees.serialize()'s "never NARROW" guard cannot help, because it
# recomputes the width from the ref this code just shrank.
_n4, _p4 = fx.make_pre_xlsx(rows_data=[fx.row("Bo", "Host",
    "Intake: Host · New · 2026-08-21", "b@x.io")])
_d4 = _p4["xl/worksheets/sheet1.xml"].decode().replace(
    "</sheetData>",
    '<row r="9"><c r="M9" t="inlineStr"><is><t>note</t></is></c></row></sheetData>", 1'[:-4],
    1).replace('<dimension ref="A1:K2"', '<dimension ref="A1:M9"')
_p4["xl/worksheets/sheet1.xml"] = _d4.encode()
_b4 = {"att": Attendees(_p4, sheet_part(_p4, "Attendees"), require=mig.PRE_SPLIT_HEADERS),
       "parts": _p4, "names": _n4, "part": sheet_part(_p4, "Attendees")}
_raw4, _ = mig.apply_plan(_b4, mig.plan(_b4))
_n4, _p4 = load_parts(_raw4)
_a4 = Attendees(_p4, sheet_part(_p4, "Attendees"))
_raw4, _ = mo.apply_move({"att": _a4, "parts": _p4, "names": _n4,
                          "part": sheet_part(_p4, "Attendees")},
                         mo.move_mapping(_a4.headers))
_, _pm = load_parts(_raw4)
_dm = _pm[sheet_part(_pm, "Attendees")].decode()
check("an un-headered data column does not narrow <dimension>",
      re.search(r'<dimension ref="([^"]+)"', _dm).group(1), "A1:M9")
check("...nor the autoFilter", re.search(r'<autoFilter ref="([^"]+)"', _dm).group(1),
      "$A$1:$M$1")
check("...and its data survives", 'r="M9"' in _dm, True)


# ---------------------------------------------------------------------------
# Hyperlinks, and the elements this move refuses to touch
# ---------------------------------------------------------------------------
# REGRESSION (found in review, after this had run on 83 workbooks). Hyperlinks
# carry a ref like everything else. Leaving them behind is silent in the worst
# way: the link stays on the ADDRESS while its column slides out from under it,
# so a LinkedIn URL ends up on the Email cell and clicking someone's address
# opens a profile. snapshot() compares cell VALUES, so the verify cannot see
# it. Dallas and Kampala had 6 such links between them.
names, parts = fx.make_pre_xlsx(rows_data=[fx.row("Ada","Organizer",
    "Intake: Organizer · Accepted · 2026-08-07","ada@x.io")])
_s = parts["xl/worksheets/sheet1.xml"].decode().replace(
    "<dataValidations", '<hyperlinks><hyperlink ref="G2" location="linkedin"/></hyperlinks>'
    "<dataValidations", 1)
parts["xl/worksheets/sheet1.xml"] = _s.encode()
att_h = Attendees(parts, sheet_part(parts, "Attendees"), require=mig.PRE_SPLIT_HEADERS)
check("G is 'LinkedIn URL' before the move",
      mo.target_order(att_h.headers)[6], "LinkedIn URL")
mig.apply_plan({"att": att_h, "parts": parts, "names": names,
                "part": sheet_part(parts, "Attendees")}, mig.plan({"att": att_h, "parts": parts}))
_n, _p = load_parts(save_parts(names, parts))
att_h = Attendees(_p, sheet_part(_p, "Attendees"))
raw_h, _ = mo.apply_move({"att": att_h, "parts": _p, "names": _n,
                          "part": sheet_part(_p, "Attendees")}, mo.move_mapping(att_h.headers))
_, ph = load_parts(raw_h)
post_h = Attendees(ph, sheet_part(ph, "Attendees"))
dh = ph[sheet_part(ph, "Attendees")].decode()
check("the hyperlink followed its column G -> H",
      re.search(r'<hyperlink ref="([^"]+)"', dh).group(1), "H2")
check("...and H is 'LinkedIn URL' after the move",
      mo.target_order(post_h.headers)[7], "LinkedIn URL")
check("...so it is NOT sitting on Email",
      mo.target_order(post_h.headers)[6], "Email")

# Elements whose position this move cannot renumber are refused, not ignored —
# a cell formula's cached <v> survives the move, so the verify would pass.
_, _, att_ok = split_book()
check("a normal workbook has nothing unmovable", mo.unmovable(att_ok), [])
for frag, want in (('<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>',
                    ["1 merged cell(s)"]),
                   ('<autoFilter ref="$A$1:$L$1"><filterColumn colId="3"/></autoFilter>',
                    ["1 autoFilter column filter(s)"])):
    _n2, _p2 = fx.make_pre_xlsx()
    _p2["xl/worksheets/sheet1.xml"] = _p2["xl/worksheets/sheet1.xml"].decode().replace(
        "<dataValidations", frag + "<dataValidations", 1).encode()
    _a2 = Attendees(_p2, sheet_part(_p2, "Attendees"), require=mig.PRE_SPLIT_HEADERS)
    check("unmovable detects %s" % want[0], mo.unmovable(_a2), want)
_n3, _p3 = fx.make_pre_xlsx(rows_data=[fx.row("Ada","Organizer","n","a@x.io")])
_d3 = _p3["xl/worksheets/sheet1.xml"].decode()
# A chapter's own helper formula in a spare column, with a CACHED result — the
# cache is why snapshot() cannot see the breakage and the workbook must be
# refused instead.
_d3 = _d3.replace("</sheetData>",
                  '<row r="9"><c r="M9"><f>IF(D2="Accepted",1,0)</f><v>0</v></c>'
                  "</row></sheetData>", 1)
_p3["xl/worksheets/sheet1.xml"] = _d3.encode()
_a3 = Attendees(_p3, sheet_part(_p3, "Attendees"), require=mig.PRE_SPLIT_HEADERS)
check("unmovable detects a chapter's own cell formula",
      mo.unmovable(_a3), ["1 cell formula(s)"])


# ---------------------------------------------------------------------------
# Guide references the remapper cannot rewrite are REPORTED, not skipped
# ---------------------------------------------------------------------------
# remap_formula's prefix pass needs a bare `Attendees!` and a row number on the
# first half. A whole-column ref or a quoted sheet name slips through — and a
# Guide left pointing at the PRE-move layout looks exactly like one correctly
# migrated, because the verify never reads the Guide at all. Unlike
# migrate_interested_in.plan_guide, which reports an unrecognised formula, this
# used to be completely silent.
def _book_with_guide(text):
    _n, _p = fx.make_pre_xlsx(guide=(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="B1" t="inlineStr"><is><t>%s</t></is></c></row>'
        "</sheetData></worksheet>" % text))
    return _p

# The report is the RESIDUE of what remap_formula claims, not a list of shapes
# someone predicted — so a form nobody has thought of is reported too.
for text, hits in (("COUNTA(Attendees!L:L)", 1),          # whole-column ref
                   ("COUNTA(Attendees!$L:$L)", 1),
                   ("SUM('Attendees'!D2:D9)", 1),         # quoted sheet name
                   ("INDIRECT(\"Attendees!\" &amp; x)", 1),   # not predicted anywhere
                   ("SUM(Attendees !D2)", 1),             # space before the bang
                   # ...and every form it DOES handle must stay silent.
                   ('COUNTIF(Attendees!D2:D1000,"x")', 0),
                   ('COUNTIF(Attendees!$D$2:$D$1000,"x")', 0),
                   ("FILTER({Attendees!A2:A, Attendees!L2:L})", 0),
                   ("no references at all", 0)):
    check("guide_misses %-40r -> %d" % (text[:38], hits),
          len(mo.unrewritten_refs(_book_with_guide(text))), hits)
check("a reported miss names the column, so it is actionable",
      mo.unrewritten_refs(_book_with_guide("COUNTA(Attendees!L:L)"))[0].startswith("Attendees!L:L"),
      True)
# A sheet whose NAME merely ends in "Attendees" is not ours.
check("a lookalike sheet name is not reported",
      mo.unrewritten_refs(_book_with_guide("COUNTA(PastAttendees!L:L)")), [])
# The shared-string table is searched too — five live chapters keep their Guide
# text there rather than inline.
_ps = _book_with_guide("clean")
_ps["xl/sharedStrings.xml"] = b"<sst><si><t>COUNTA(Attendees!L:L)</t></si></sst>"
check("...including the shared string table", len(mo.unrewritten_refs(_ps)), 1)
# The invariant the whole design rests on: the two patterns cannot disagree.
check("nothing remap_formula rewrites is ever reported as a miss",
      [t for t in ("Attendees!D2", "Attendees!$D$2", "Attendees!D2:D9",
                   "Attendees!D2:D", "Attendees!$D$2:$D$1000")
       if mo.unrewritten_refs(_book_with_guide("X(%s)" % t))], [])


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
      (mig.dv_plan(post), mig.cf_plan(post, mo_parts)), (([], []), mig.CF_OK))

# And the sync is layout-independent by construction — it addresses by header
# name only, so the move must be invisible to it.
check("the sync can open the reordered workbook", check_dropdowns(post), [])
check("...and reads every column back by name",
      sorted(post.headers), sorted(WANTED))


# The REWRITER half of the lookbehind. The lookalike test above uses a
# whole-column ref, which _HANDLED_REF never matches — so removing the
# lookbehind from _HANDLED_REF alone produced ZERO failures while corrupting
# data: `PastAttendees!D2` was renumbered to `PastAttendees!E2`, silently
# repointing a formula on somebody else's tab.
check("a lookalike sheet's own reference is never renumbered",
      mo.remap_formula("PastAttendees!D2+Old_Attendees!$L$2:$L$9", MAP, cross_sheet=True),
      "PastAttendees!D2+Old_Attendees!$L$2:$L$9")
check("...while the real sheet beside it still is",
      mo.remap_formula("PastAttendees!D2+Attendees!D2", MAP, cross_sheet=True),
      "PastAttendees!D2+Attendees!E2")

# Parts the rewriter never edits. A chapter's own tab, or a Sheets-exported
# named range, is rewritten by NOTHING here — so every mention in one is a
# miss, claimable or not. Neither was scanned before; the column moved out from
# under them in silence, and `unmovable` does not cover it (it counts <f> on
# the Attendees sheet alone).
def _book_with_extra_sheet(sheet3_xml, workbook_extra=""):
    _n, _p = fx.make_pre_xlsx(headers=tuple(WANTED))
    _p["xl/workbook.xml"] = _p["xl/workbook.xml"].decode().replace(
        "<sheets>", "%s<sheets>" % workbook_extra).replace(
        "</sheets>", '<sheet name="Stats" sheetId="3" r:id="rId5" /></sheets>').encode()
    _p["xl/_rels/workbook.xml.rels"] = _p["xl/_rels/workbook.xml.rels"].decode().replace(
        "</Relationships>",
        '<Relationship Id="rId5" Target="worksheets/sheet3.xml" /></Relationships>').encode()
    _p["xl/worksheets/sheet3.xml"] = sheet3_xml.encode()
    return _p

check("a formula on a chapter's own tab is reported",
      mo.unrewritten_refs(_book_with_extra_sheet(
          '<worksheet><sheetData><row r="2"><c r="B2">'
          "<f>COUNTA(Attendees!L2:L1000)</f><v>7</v></c></row></sheetData></worksheet>")),
      ["Attendees!L2:L1000"])
check("...even though that shape IS one the rewriter could handle",
      mo.remap_formula("COUNTA(Attendees!L2:L1000)", MAP, cross_sheet=True),
      "COUNTA(Attendees!D2:D1000)")
check("a definedName in workbook.xml is reported",
      mo.unrewritten_refs(_book_with_extra_sheet(
          "<worksheet><sheetData/></worksheet>",
          '<definedName name="live">Attendees!$L$2:$L$1000</definedName>')),
      ["Attendees!$L$2:$L$1000"])

# Prose is not a reference. A fix-by-hand list padded with sentence fragments
# is a list operators stop reading, which would bury every real finding in it.
for text, want in (("See the Attendees! tab for the roster", []),
                   ("Great turnout, loved the Attendees! Ada L, ada@x.com", []),
                   ("COUNTA(Attendees!L:L)", ["Attendees!L:L"]),
                   # A reference BUILT as a string is unanalyzable, so it is
                   # reported — the snippet stops at the quote, which is less
                   # specific than a real ref but still points at the spot.
                   ('INDIRECT("Attendees!" & col)', ["Attendees!"]),
                   ("&apos;Attendees&apos;!D2", ["&apos;Attendees&apos;!D2"]),
                   ("&#39;Attendees&#39;!D2", ["&#39;Attendees&#39;!D2"])):
    check("prose/ref %-42r" % text[:40],
          mo.unrewritten_refs(_book_with_guide(text)), want)

# PII. xl/sharedStrings.xml is the workbook-GLOBAL string table — it holds
# organizer names, emails and free-text survey answers. The snippet used to be
# a raw 24-char window, which printed `Attendees! Ada L, ada@x.` under a
# "Fix by hand" heading from a script that has no --redact flag.
_pii = _book_with_guide("clean")
_pii["xl/sharedStrings.xml"] = (
    b"<sst><si><t>Loved the Attendees! Ada Lovelace ada@x.io joins as co-host</t>"
    b"</si></sst>")
check("a member's name and email can never reach the report",
      [r for r in mo.unrewritten_refs(_pii) if "Ada" in r or "@" in r], [])

# The two patterns must agree on where a reference begins. They do NOT always
# start at the same character — an unbalanced leading quote puts the mention
# one earlier — so the residue anchors on the `!`, which both must contain.
check("an unbalanced quote is rewritten and NOT double-reported",
      (mo.remap_formula("X('Attendees!D2)", MAP, cross_sheet=True),
       mo.unrewritten_refs(_book_with_guide("X('Attendees!D2)"))),
      ("X('Attendees!E2)", []))

# The handled <-> reported invariant, over the cross product rather than five
# hand-picked strings.
_shapes = ["Attendees!%s%s%s" % (a, c, r)
           for a in ("", "$") for c in ("A", "D", "L", "AAA") for r in ("2", "$2")]
_shapes += ["Attendees!%s2:%s9" % (c, c) for c in ("A", "D", "L")]
_shapes += ["Attendees!%s2:%s" % (c, c) for c in ("A", "D", "L")]
check("handled <=> not reported, across %d reference shapes" % len(_shapes),
      [t for t in _shapes if bool(mo.unrewritten_refs(_book_with_guide("X(%s)" % t)))], [])


# ---------------------------------------------------------------------------
# _run's report path — the layer that let a type change through untested
# ---------------------------------------------------------------------------
# guide_misses' return type changed from [(part, ref)] to [ref], and the caller
# in _run kept unpacking pairs: `sorted({r for _, r in misses})`. Every direct
# guide_misses test still passed, because none of them went through _run — so
# the whole reporting path was dead, and would have raised ValueError on the
# first real chapter carrying exactly the reference this PR exists to catch.
# These drive _run itself with Drive stubbed.
import contextlib as _ctx  # noqa: E402
import io as _io  # noqa: E402


def _run_report(guide_text):
    """Run _run() in report mode over one stubbed chapter; return (code, stdout).

    Headers are the POST-move order, so open_crm's strict reader succeeds and
    move_mapping returns None — the chapter is already ordered and the ONLY
    thing left to exercise is the gap report. `download` is patched in
    migrate_column_order's namespace, not sync_chapters': open_crm calls the
    name bound at import, so patching the source module leaves the real one in
    place and every chapter reports as SKIPPED on a 404.
    """
    _n, _p = fx.make_pre_xlsx(
        headers=tuple(WANTED),
        guide=('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<sheetData><row r="1"><c r="B1" t="inlineStr"><is><t>%s</t></is></c>'
               "</row></sheetData></worksheet>" % guide_text))
    raw = save_parts(_n, _p)
    buf = _io.StringIO()
    # A raise inside _run is a FAILURE, not a reason to abort the suite: the
    # tuple-unpack bug this harness exists to catch propagated out of _run and
    # killed the whole script, which hid every check after it. Turn it into a
    # value the assertions can inspect.
    try:
        with mock.patch.object(mo, "list_chapter_folders",
                               lambda: [{"id": "f1", "name": "Boston"}]), \
             mock.patch.object(mo, "find_crm",
                               lambda fid: ({"id": "c1", "name": "Boston CRM.xlsx"}, None)), \
             mock.patch.object(mo, "download", lambda fid, path: raw), \
             _ctx.redirect_stdout(buf):
            code = mo._run(mock.Mock(city=None, write=False),
                           tempfile.mkdtemp(prefix="t-"))
    except Exception as e:
        return "RAISED %s: %s" % (type(e).__name__, e), buf.getvalue()
    return code, buf.getvalue()


_code, _out = _run_report("COUNTA(Attendees!L:L)")
check("_run does not raise on a workbook WITH a gap",
      str(_code).startswith("RAISED"), False)
check("the stub opens cleanly — no chapter is SKIPPED", "SKIPPED" in _out, False)
check("_run survives a workbook WITH a miss (the ValueError regression)",
      "Traceback" not in _out, True)
check("...and names the unrewritable reference in the report",
      "Attendees!L:L" in _out, True)
check("...and says which chapter", "Boston" in _out, True)
check("...and does not print a clean verdict over it",
      "Nothing to do —" in _out, False)
check("...and the gap reaches the exit code", _code, 1)

_code2, _out2 = _run_report('COUNTIF(Attendees!D2:D1000,"x")')
check("a workbook with NO miss is silent and exits 0",
      (_code2, "cannot rewrite" in _out2, "Nothing to do —" in _out2),
      (0, False, True))


print()
if fails:
    print("FAILED %d check(s)" % fails)
    sys.exit(1)
print("All checks passed.")
