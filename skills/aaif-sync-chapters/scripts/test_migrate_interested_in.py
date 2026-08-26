#!/usr/bin/env python3
"""Unit tests for migrate_interested_in.py (no network/gws).

The fixture is a PRE-split workbook — eleven columns, the old Status dropdown
with its role values, and the Guide tab's real formulas — because that is the
only shape this migration ever runs against. Built here rather than checked in
as a binary, for the reason sync_crm's tests give: a binary would silently stop
resembling the live workbooks, and this cannot.
"""
import os, sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_interested_in as mig
import sync_crm
from sync_crm import (Attendees, DV_EXPECTED, NEW_COLUMN, X, check_dropdowns,
                      cell_ref, col_of, load_parts, save_parts, sheet_part)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


# ---------------------------------------------------------------------------
# Fixture: the workbook as it was BEFORE the split
# ---------------------------------------------------------------------------
PRE = mig.PRE_SPLIT_HEADERS
# The list every chapter carried until 2026-08-25 — roles and lifecycle values
# in one column, which is the conflation being undone.
OLD_DV = "Prospect,Attended,Regular,Speaker,Organizer,Volunteer,Host,Declined"

# The conditional formatting every CRM already carries: Signal (B) and
# Trusted/Regular (C). Nothing tested Status, which is why the split broke none
# of it — and why the new rules have the column to themselves.
SIGNAL_CF = (
    '<conditionalFormatting sqref="B2:B1000">'
    '<cfRule type="cellIs" priority="1" operator="equal" dxfId="0">'
    '<formula>"High"</formula></cfRule></conditionalFormatting>'
    '<conditionalFormatting sqref="A2:A1000">'
    '<cfRule type="expression" priority="4" dxfId="2">'
    '<formula>$B2="Non-grata"</formula></cfRule></conditionalFormatting>')

# The Guide tab's real content, verbatim from the shipped template: two
# dashboard COUNTIFs against the Status column and the live-list FILTER whose
# `Attendees!L2:L` points at a column that does not exist on this layout.
GUIDE = (
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData>'
    '<row r="1"><c r="B1" t="inlineStr"><is><t>'
    '=FILTER({Attendees!A2:A, Attendees!I2:I, Attendees!K2:K, Attendees!L2:L, '
    'Attendees!B2:B, Attendees!E2:E}, (Attendees!C2:C=&quot;Yes&quot;)'
    '+(Attendees!B2:B=&quot;High&quot;), Attendees!B2:B&lt;&gt;&quot;Non-grata&quot;)'
    '</t></is></c></row>'
    '<row r="2"><c r="D2"><f>COUNTIF(Attendees!D2:D1000,"Speaker")</f><v>3</v></c></row>'
    '<row r="3"><c r="D3"><f>COUNTIF(Attendees!D2:D1000,"Organizer")</f><v>5</v></c></row>'
    '<row r="4"><c r="D4"><f>COUNTA(Attendees!A2:A1000)</f><v>9</v></c></row>'
    '</sheetData></worksheet>')


def _c(ref, text, style=None):
    s = ' s="%s"' % style if style else ""
    if text is None:
        return '<c r="%s"%s t="n" />' % (ref, s)
    return '<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>' % (ref, s, text)


#: The three dxf styles every shipped CRM carries — green / amber / red. The
#: count is what the colour rules check before referencing dxfId 0..2.
STYLES = '<styleSheet><dxfs count="3"><dxf /><dxf /><dxf /></dxfs></styleSheet>'


def make_pre_xlsx(rows_data=(), headers=PRE, dv=OLD_DV, guide=GUIDE, cols=True,
                  styles=STYLES, cf=""):
    """A pre-split workbook. `rows_data` is [[cell, ...]] starting at row 2."""
    head = "".join(_c(cell_ref(i, 1), h, "2") for i, h in enumerate(headers))
    rows = ['<row r="1" ht="30" customHeight="1" s="20">%s</row>' % head]
    for n, vals in enumerate(rows_data, start=2):
        cells = "".join(_c(cell_ref(i, n), v, "3") for i, v in enumerate(vals) if v)
        rows.append('<row r="%d">%s</row>' % (n, cells))
    # The shipped <cols>: one run covering L..S (12..19) at the default width,
    # which is what widen_column has to split rather than narrow wholesale.
    colblock = ('<cols><col width="22" customWidth="1" style="20" min="1" max="1" />'
                '<col width="8.71" customWidth="1" style="20" min="12" max="19" />'
                '</cols>') if cols else ""
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:K%d" />%s<sheetData>%s</sheetData>'
        # conditionalFormatting must sit AFTER sheetData and BEFORE
        # dataValidations — CT_Worksheet fixes the order, and Excel calls the
        # file corrupt if a block lands after pageMargins.
        '%s'
        '<dataValidations count="2">'
        '<dataValidation sqref="B2:B1000" type="list">'
        '<formula1>"High,Low,Non-grata,New"</formula1></dataValidation>'
        '<dataValidation sqref="D2:D1000" type="list">'
        '<formula1>"%s"</formula1></dataValidation>'
        '</dataValidations><pageMargins left="0.75" /></worksheet>'
        % (1 + len(rows_data), colblock, "".join(rows),
           SIGNAL_CF + cf, dv))
    parts = {
        "[Content_Types].xml": "<Types />",
        "_rels/.rels": "<Relationships />",
        "xl/workbook.xml":
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Attendees" sheetId="1" r:id="rId7" />'
            '<sheet name="Guide" sheetId="2" r:id="rId9" /></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7" Target="worksheets/sheet1.xml" />'
            '<Relationship Id="rId9" Target="worksheets/sheet2.xml" /></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
        "xl/worksheets/sheet2.xml": guide,
    }
    if styles:
        parts["xl/styles.xml"] = styles
    names = list(parts)
    return names, {n: v.encode() for n, v in parts.items()}


def row(name, status, note, email):
    """One pre-split Attendees row: A name, D status, E note, F email."""
    return [name, "", "", status, note, email, "", "", "", "", ""]


def opened(**kw):
    names, parts = make_pre_xlsx(**kw)
    att = Attendees(parts, sheet_part(parts, "Attendees"),
                    require=mig.PRE_SPLIT_HEADERS)
    return names, parts, att


# ---------------------------------------------------------------------------
# parse_note — the provenance string is the only source of truth for a backfill
# ---------------------------------------------------------------------------
check("an accepted organizer's note",
      mig.parse_note("Intake: Organizer · Accepted · 2026-08-07"),
      ("Organizer", "Accepted"))
check("a pipeline host's note — the row this whole change is about",
      mig.parse_note("Intake: Host · New · 2026-08-21"),
      ("Host", "Prospect"))
check("the legacy New spelling maps like Prospect",
      mig.parse_note("Intake: Speaker · Prospect · 2026-08-21"),
      ("Speaker", "Prospect"))
check("a multi-role note keeps every role",
      mig.parse_note("Intake: Organizer/Speaker/Host · New · 2026-08-21"),
      ("Organizer/Speaker/Host", "Prospect"))
check("accepted in ANY role wins, matching crm_fields",
      mig.parse_note("Intake: Organizer/Speaker · Tentative/Accepted · 2026-08-07"),
      ("Organizer/Speaker", "Accepted"))
check("'Existing (from MLOps)' is an accepted status",
      mig.parse_note("Intake: Organizer · Existing (from MLOps) · 2026-08-07"),
      ("Organizer", "Accepted"))
check("an in-flight status is carried through verbatim",
      mig.parse_note("Intake: Organizer · Interviewing · 2026-08-07"),
      ("Organizer", "Interviewing"))
for note in ("", "Met at the Feb event; will co-host.",
             "Intake: Sponsor · Accepted · 2026-08-07",
             "Intake: Organizer · Accepted"):
    check("unparsable note %-46r -> nothing" % note[:44], mig.parse_note(note), ("", ""))
check("a note with an unrecognised STATUS still relocates the role",
      mig.parse_note("Intake: Host · Retired · 2026-08-07"), ("Host", ""))

# Every status the sync can stamp into a note must parse back out, or the
# backfill silently blanks the Status of everyone carrying it.
_unparsable = [st for st in sync_crm.SYNC_STATUSES + sync_crm.PIPELINE_STATUSES
                if st and mig.parse_note("Intake: Host · %s · 2026-08-07" % st)[1] == ""]
check("every intake status round-trips through a note", _unparsable, [])


# ---------------------------------------------------------------------------
# plan_row — what actually gets rewritten
# ---------------------------------------------------------------------------
check("THE bug: a pipeline host reading 'Host' becomes Prospect + Host",
      mig.plan_row("", "Host", "Intake: Host · New · 2026-08-21"),
      {NEW_COLUMN: "Host", "Status": "Prospect"})
check("an accepted organizer keeps their standing, stated properly",
      mig.plan_row("", "Organizer", "Intake: Organizer · Accepted · 2026-08-07"),
      {NEW_COLUMN: "Organizer", "Status": "Accepted"})
check("a pipeline organizer already said Prospect — only the role moves in",
      mig.plan_row("", "Prospect", "Intake: Organizer · New · 2026-08-21"),
      {NEW_COLUMN: "Organizer"})
check("a role with no usable note is RELOCATED, and no status is invented",
      mig.plan_row("", "Speaker", "Met her at the Feb event."),
      {NEW_COLUMN: "Speaker", "Status": ""})

# The curated values. A human who set these said something the intake does not
# know, and none of them is a role, so none is this script's business.
for human in ("Attended", "Regular", "Volunteer", "Declined"):
    check("a human's %r is untouched" % human,
          mig.plan_row("", human, "Intake: Host · New · 2026-08-21"),
          {NEW_COLUMN: "Host"})
check("a blank Status stays blank",
      mig.plan_row("", "", "Met at the Feb event."), {})
check("an already-filled Interested in is never restated",
      mig.plan_row("Organizer", "Organizer",
                   "Intake: Organizer · Accepted · 2026-08-07"),
      {"Status": "Accepted"})
check("a human's note in Interested in survives",
      mig.plan_row("Organizer (co-lead)", "Prospect",
                   "Intake: Organizer · New · 2026-08-21"), {})
check("a hand-typed lowercase role is still recognised",
      mig.plan_row("", "host", "Met at the Feb event."),
      {NEW_COLUMN: "Host", "Status": ""})

# Idempotency at the row level: the output of a pass is a fixed point.
_first = mig.plan_row("", "Host", "Intake: Host · New · 2026-08-21")
check("re-planning a migrated row proposes nothing",
      mig.plan_row(_first[NEW_COLUMN], _first["Status"],
                   "Intake: Host · New · 2026-08-21"), {})


# ---------------------------------------------------------------------------
# The column: added, widened, addressable
# ---------------------------------------------------------------------------
names, parts, att = opened(rows_data=[row("Ada", "Host", "Intake: Host · New · 2026-08-21",
                                          "ada@x.io")])
check("the fixture really is pre-split", NEW_COLUMN in att.headers, False)
col = mig.add_column(att)
check("the new column lands past the last header (L)", col, 11)
check("...and is addressable by name", att.headers[NEW_COLUMN], 11)
check("...and carries the header row's style",
      next(c.get("s") for c in att.rows[1].findall(X + "c")
           if col_of(c.get("r")) == col), "2")
check("add_column is idempotent", mig.add_column(att), 11)

mig.widen_column(att, col)
runs = [(int(c.get("min")), int(c.get("max")), c.get("width"))
        for c in att.root.find(X + "cols")]
check("the shared 12-19 run is SPLIT, not re-widened wholesale",
      runs, [(1, 1, "22"), (12, 12, mig.NEW_COL_WIDTH), (13, 19, "8.71")])
mig.widen_column(att, col)
check("widen_column is idempotent",
      [(int(c.get("min")), int(c.get("max")), c.get("width"))
       for c in att.root.find(X + "cols")], runs)
_, _, att_nc = opened(cols=False)
mig.widen_column(att_nc, mig.add_column(att_nc))
check("a workbook with no <cols> is left alone rather than crashing",
      att_nc.root.find(X + "cols"), None)


# ---------------------------------------------------------------------------
# The dropdowns
# ---------------------------------------------------------------------------
names, parts, att = opened()
check("the pre-split lists are both reported wrong",
      sorted(h for h, _ in mig.dv_plan(att)), sorted(["Status", NEW_COLUMN]))
check("...and the Status one is reported with what it currently holds",
      dict(mig.dv_plan(att))["Status"], OLD_DV)
mig.add_column(att)
check("apply_dropdowns refuses nothing on a normal workbook",
      mig.apply_dropdowns(att), [])
check("both lists are now right", mig.dv_plan(att), [])
check("no role word is left on the Status list",
      [r for r in mig.ROLE_WORDS.values()
       if r in DV_EXPECTED["Status"].split(",")], [])
check("the Signal list — an unrelated 'New' — is untouched",
      [mig._dv_formula(dv) for dv in mig._dv_elements(att)
       if mig._dv_column(dv) == 1], ["High,Low,Non-grata,New"])
check("the count attribute is refreshed",
      att.root.find(X + "dataValidations").get("count"), "3")

# A validation spanning several columns cannot be rewritten without silently
# re-validating its neighbour, so it is refused and reported, never guessed at.
names, parts = make_pre_xlsx()
parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
    b'sqref="D2:D1000"', b'sqref="C2:D1000"')
att_m = Attendees(parts, sheet_part(parts, "Attendees"), require=mig.PRE_SPLIT_HEADERS)
mig.add_column(att_m)
check("a merged sqref is refused, not rewritten",
      mig.apply_dropdowns(att_m), ["Status"])
check("...and the merged rule is left exactly as it was",
      [dv.get("sqref") for dv in mig._dv_elements(att_m)
       if dv.get("sqref") == "C2:D1000"], ["C2:D1000"])


# ---------------------------------------------------------------------------
# Conditional formatting on Status
# ---------------------------------------------------------------------------
names, parts, att = opened()
mig.add_column(att)
check("the colour rules are due on a fresh workbook", mig.cf_plan(att, parts), "due")
mig.apply_cf(att)
check("...and satisfied once applied", mig.cf_plan(att, parts), "ok")
before = [mig._cf_signature(e) for e in mig._cf_blocks(att)][:2]

block = [e for e in mig._cf_blocks(att)
         if e.get("sqref") == "D2:D1000" and len(e) > 1][0]
check("the rules paint the Status column",
      [(r.find(X + "formula").text.strip('"'), r.get("dxfId")) for r in block],
      [("In progress", "1"), ("Interviewing", "1"), ("Tentative", "1"),
       ("Accepted", "0"), ("Regular", "0"), ("Volunteer", "0"), ("Declined", "2")])
check("...in the dropdown's own order",
      [r.find(X + "formula").text.strip('"') for r in block],
      [v for v in DV_EXPECTED["Status"].split(",")
       if v in dict(mig.status_cf_rules())])
check("Prospect and Attended are deliberately NOT painted",
      [v for v in ("Prospect", "Attended") if v in mig.STATUS_DXF], [])
check("a blank Status is not painted (it would light up 990 empty rows)",
      "" in mig.STATUS_DXF, False)
check("every painted value is a real Status dropdown value",
      [v for v in mig.STATUS_DXF if v not in DV_EXPECTED["Status"].split(",")], [])
check("no role word is painted — they are not statuses any more",
      [v for v in mig.STATUS_DXF if v in mig.ROLE_WORDS.values()], [])
check("only the three shipped dxf styles are referenced",
      sorted(set(mig.STATUS_DXF.values())), [0, 1, 2])
check("the Signal rules are untouched",
      [mig._cf_signature(e) for e in mig._cf_blocks(att)][:2], before)

# Schema order: conditionalFormatting must precede dataValidations, or Excel
# calls the file corrupt. Appending to the end of the worksheet is the easy
# mistake, and it puts the block after pageMargins.
kids = [k.tag.split("}")[1] for k in att.root]
check("the new block lands before dataValidations",
      kids.index("conditionalFormatting") < kids.index("dataValidations"), True)
check("...and before pageMargins",
      max(i for i, k in enumerate(kids) if k == "conditionalFormatting")
      < kids.index("pageMargins"), True)
check("priorities are unique across every block",
      len({r.get("priority") for e in mig._cf_blocks(att) for r in e}),
      sum(len(e) for e in mig._cf_blocks(att)))

# A workbook whose Status column someone already painted: their rules are not
# ours to replace.
_, fparts, fatt = opened(cf='<conditionalFormatting sqref="D2:D1000">'
                            '<cfRule type="cellIs" priority="9" operator="equal" '
                            'dxfId="1"><formula>"VIP"</formula></cfRule>'
                            '</conditionalFormatting>')
mig.add_column(fatt)
check("a human's Status formatting is reported, not overwritten",
      mig.cf_plan(fatt, fparts), "foreign")

# Referencing dxfId 0..2 in a workbook that has fewer than three styles would
# render as arbitrary formatting rather than as an error.
_, nparts, natt = opened(styles='<styleSheet><dxfs count="1"><dxf /></dxfs></styleSheet>')
mig.add_column(natt)
check("a workbook without the three dxf styles is refused",
      mig.cf_plan(natt, nparts), "no-dxfs")
_, sparts, satt = opened(styles="")
mig.add_column(satt)
check("...as is one with no styles.xml at all", mig.cf_plan(satt, sparts), "no-dxfs")

# The whole block must survive serialize(), which rewrites the sheet part from
# the tree — the same trap that once discarded the dropdown patch.
names, parts, att = opened()
mig.add_column(att)
mig.apply_cf(att)
att.serialize()
rt_names, rt_parts = load_parts(save_parts(names, parts))
rt = Attendees(rt_parts, sheet_part(rt_parts, "Attendees"))
check("the colour rules survive a serialize round-trip",
      mig.cf_plan(rt, rt_parts), "ok")


# ---------------------------------------------------------------------------
# The Guide tab
# ---------------------------------------------------------------------------
names, parts = make_pre_xlsx()
todo, missing = mig.plan_guide(parts, 11)
check("all three Guide formulas are due", len(todo), 3)
check("...and none is unrecognised", missing, [])
mig.apply_guide(parts, todo)
g = parts["xl/worksheets/sheet2.xml"].decode()
check("the Speaker dashboard tile now counts the new column",
      '<f>COUNTIF(Attendees!L2:L1000,"*Speaker*")</f>' in g, True)
check("the Organizer tile too",
      '<f>COUNTIF(Attendees!L2:L1000,"*Organizer*")</f>' in g, True)
check("no dashboard tile still counts Status for a role",
      'COUNTIF(Attendees!D2:D1000,"Speaker")' in g
      or 'COUNTIF(Attendees!D2:D1000,"Organizer")' in g, False)
check("the unrelated COUNTA is untouched",
      '<f>COUNTA(Attendees!A2:A1000)</f>' in g, True)
check("the live list no longer has a dangling reference",
      "Attendees!L2:L," in g and "Attendees!H2:H" in g, True)
check("the live list's filter conditions are unchanged",
      "(Attendees!C2:C=&quot;Yes&quot;)+(Attendees!B2:B=&quot;High&quot;)" in g, True)
check("re-planning a migrated Guide proposes nothing", mig.plan_guide(parts, 11)[0], [])

# A Guide someone edited: report the miss rather than regex something we do not
# understand into a formula.
names, parts = make_pre_xlsx(
    guide=GUIDE.replace('COUNTIF(Attendees!D2:D1000,"Speaker")', 'COUNTIF(Foo!A1:A9,"x")'))
todo, missing = mig.plan_guide(parts, 11)
check("an edited Guide formula is reported, not rewritten", len(missing), 1)
check("...and the ones still recognised are still planned", len(todo), 2)

# The LEGACY storage shape, which five live chapters actually have (Austin,
# Dallas, Kampala, London, Tatooine as of 2026-08-25): the formulas escape
# their quotes as &quot; and every string lives in the SHARED table rather
# than inline in the sheet. Searching only the sheet part for only the `"`
# spelling found none of it, and reported all three edits as unrecognised
# while those dashboards sat there still counting the Status column.
LEGACY_GUIDE = (
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData>'
    '<row r="1"><c r="B1" t="s"><v>0</v></c></row>'
    '<row r="2"><c r="D2"><f>COUNTIF(Attendees!D2:D1000,&quot;Speaker&quot;)</f>'
    '<v>3</v></c></row>'
    '<row r="3"><c r="D3"><f>COUNTIF(Attendees!D2:D1000,&quot;Organizer&quot;)</f>'
    '<v>5</v></c></row>'
    '</sheetData></worksheet>')
LEGACY_SST = (
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>'
    '=FILTER({Attendees!A2:A, Attendees!I2:I, Attendees!K2:K, Attendees!L2:L, '
    'Attendees!B2:B, Attendees!E2:E}, (Attendees!C2:C=&quot;Yes&quot;)'
    '+(Attendees!B2:B=&quot;High&quot;), Attendees!B2:B&lt;&gt;&quot;Non-grata&quot;)'
    '</t></si></sst>')
names, parts = make_pre_xlsx(guide=LEGACY_GUIDE)
parts["xl/sharedStrings.xml"] = LEGACY_SST.encode()
todo, missing = mig.plan_guide(parts, 11)
check("the legacy shape is fully recognised", (len(todo), missing), (3, []))
check("...with the FILTER found in the SHARED table, not the sheet",
      sorted({t[0] for t in todo}),
      ["xl/sharedStrings.xml", "xl/worksheets/sheet2.xml"])
mig.apply_guide(parts, todo)
lg = parts["xl/worksheets/sheet2.xml"].decode()
check("the legacy tiles are repointed, keeping their escaping",
      '<f>COUNTIF(Attendees!L2:L1000,&quot;*Speaker*&quot;)</f>' in lg
      and '<f>COUNTIF(Attendees!L2:L1000,&quot;*Organizer*&quot;)</f>' in lg, True)
check("...and no legacy tile still counts Status",
      'Attendees!D2:D1000,&quot;Speaker&quot;' in lg, False)
check("the shared-table live list is repointed",
      "Attendees!H2:H" in parts["xl/sharedStrings.xml"].decode(), True)
check("re-planning the migrated legacy shape proposes nothing",
      mig.plan_guide(parts, 11)[0], [])


# ---------------------------------------------------------------------------
# End to end: migrate, then hand the result to the sync
# ---------------------------------------------------------------------------
# This is the test that matters. The migration and the sync are two scripts
# that must agree on the exact bytes for every row, or --write never converges:
# the sync re-proposes changes forever and the run reports "ops still pending",
# indistinguishable from a real write failure.
ROWS = [
    row("Ada", "Organizer", "Intake: Organizer · Accepted · 2026-08-07", "ada@x.io"),
    row("Bo", "Host", "Intake: Host · New · 2026-08-21", "bo@x.io"),
    row("Cy", "Speaker", "Intake: Speaker · New · 2026-08-21", "cy@x.io"),
    row("Dee", "Prospect", "Intake: Organizer · Tentative · 2026-08-21", "dee@x.io"),
    row("Eve", "Declined", "Pitched from the floor.", "eve@x.io"),
]
names, parts, att = opened(rows_data=ROWS)
p = mig.plan({"att": att, "parts": parts})
check("the whole workbook is due", p["any"], True)
check("...adding the column", p["add_column"], True)
check("...backfilling four of the five rows", len(p["rows"]), 4)
check("...moving three roles out of Status",
      sum(1 for o in p["rows"] if "Status" in o["sets"]), 3)
raw, refused = mig.apply_plan(
    {"att": att, "parts": parts, "names": names, "part": sheet_part(parts, "Attendees")}, p)
check("nothing was refused", refused, [])

# Re-open through the SYNC's own reader, which demands the full header set.
mig_names, mig_parts = load_parts(raw)
post = Attendees(mig_parts, sheet_part(mig_parts, "Attendees"))
check("every zip part survives", sorted(mig_names), sorted(names))
check("the sync can now open the workbook", NEW_COLUMN in post.headers, True)
check("the sync sees no stale dropdowns", check_dropdowns(post), [])
check("the split, row by row",
      [(post.value(r, "Full name"), post.value(r, "Status"), post.value(r, NEW_COLUMN))
       for r in range(2, 7)],
      [("Ada", "Accepted", "Organizer"),
       ("Bo", "Prospect", "Host"),            # <- was a flat "Host"
       ("Cy", "Prospect", "Speaker"),         # <- was a flat "Speaker"
       ("Dee", "Prospect", "Organizer"),
       ("Eve", "Declined", "")])              # <- a human's call, untouched
check("no Notes (CRM) cell was rewritten",
      post.value(3, "Notes (CRM)"), "Intake: Host · New · 2026-08-21")
check("the dimension covers the new column",
      ET.fromstring(mig_parts["xl/worksheets/sheet1.xml"]).find(X + "dimension").get("ref"),
      "A1:L6")

# Idempotency at the workbook level, through a real round-trip.
check("a second migration pass proposes nothing",
      mig.plan({"att": post, "parts": mig_parts})["any"], False)

# And the sync agrees with what the migration wrote: for the people still on
# the intake it proposes nothing, which is what makes --write converge.
def _person(name, email, tab, status):
    return {"row": 2, "tab": tab, "status": status, "name": name, "email": email,
            "city": "Boston", "linkedin": "", "company": "", "title": "",
            "expertise": "", "interest": ""}

live = sync_crm.merge_people([
    _person("Ada", "ada@x.io", "Organizers", "Accepted"),
    _person("Bo", "bo@x.io", "Hosts", "Prospect"),
    _person("Cy", "cy@x.io", "Speakers", "Prospect")])
left = sync_crm.plan_workbook(post, live, "2026-08-26")
check("the sync proposes no Status or role change over a migrated workbook",
      [(o["rownum"], k) for o in left for k in o["sets"]
       if k in ("Status", NEW_COLUMN)], [])

# The sync must still be able to UPGRADE what the migration wrote — a blank
# Status left by an unparsable note, and a Prospect that has since been
# accepted. Both are in AUTO_STATUS; if either were not, the migration would
# have frozen those people permanently.
check("a migration-written Prospect is still auto-owned",
      [v for v in ("", "Prospect", "Accepted", "Interviewing", "Tentative",
                   "In progress") if v not in sync_crm.AUTO_STATUS], [])
up = sync_crm.plan_workbook(post, sync_crm.merge_people(
    [_person("Bo", "bo@x.io", "Hosts", "Accepted")]), "2026-08-26")
check("acceptance upgrades a migrated row in place",
      (up[0]["rownum"], up[0]["sets"].get("Status")), (3, "Accepted"))
check("...without disturbing what they applied for",
      NEW_COLUMN in up[0]["sets"], False)


print()
if fails:
    print("FAILED %d check(s)" % fails)
    sys.exit(1)
print("All checks passed.")
