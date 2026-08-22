#!/usr/bin/env python3
"""Unit tests for migrate_status_prospect.py — plan logic and zip surgery only,
no network/gws. Follows the plain-script test convention (exit 1 on failure).

The .xlsx fixtures are built with zipfile from the same shape the chapter CRMs
have — a Status list validation AND a Signal list validation that legitimately
contains "New". The load-bearing cases:

  * columns are located by HEADER NAME: a workbook whose Status/Signal columns
    are swapped migrates identically, and Signal's unrelated "New" survives in
    both its dropdown and its data cells;
  * only the sheet part being edited changes — every other zip part is repacked
    byte-identically;
  * the intake plan touches column A and nothing else (columns B+ are
    ARRAYFORMULA mirrors), and refuses a tab whose Status column moved.
"""
import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_status_prospect as mig
from migrate_status_prospect import (CF_TOKEN_NEW, CF_TOKEN_OLD, HOWTO_TEXTS,
                                     CrmStatusSheet, cf_refusal, dv_list,
                                     grid_rule_plan, howto_new_text,
                                     intake_status_guard, migrate_cf_formula,
                                     migrate_list, plan_crm, plan_howto,
                                     plan_intake_cells, runs_of, sqref_cols,
                                     tab_actionable, tab_changes)
import sync_crm
from sync_crm import cell_ref, load_parts, save_parts, sheet_part

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
INTAKE_LIST = ["New", "In progress", "Tentative", "Interviewing", "Accepted",
               "Denied", "Inactive", "Duplicate", "Existing (from MLOps)"]
CRM_LIST = ["New", "Prospect", "Attended", "Regular", "Speaker", "Organizer",
            "Volunteer", "Host", "Declined"]
SIGNAL_LIST = ["High", "Low", "Non-grata", "New"]

check("the CRM fixture list IS sync_crm's dropdown (drift here breaks the "
      "Host patch fleet-wide)",
      ",".join(CRM_LIST), sync_crm.DV_STATUS_NEW)
check("and migrating it yields exactly sync_crm's migrated list",
      ",".join(migrate_list(CRM_LIST)), sync_crm.DV_STATUS_NEW_MIGRATED)

check("intake list: New is replaced in place (Prospect not yet offered)",
      migrate_list(INTAKE_LIST),
      ["Prospect", "In progress", "Tentative", "Interviewing", "Accepted",
       "Denied", "Inactive", "Duplicate", "Existing (from MLOps)"])
check("CRM list: the leading New is dropped (Prospect already offered)",
      migrate_list(CRM_LIST), CRM_LIST[1:])
check("a list without New is already in sync", migrate_list(CRM_LIST[1:]), None)
check("order is preserved either way",
      migrate_list(["A", "New", "B"]), ["A", "Prospect", "B"])

check("guard passes when Status is column A",
      intake_status_guard(["Status", "Full name", "Email"]), None)
check("guard strips header whitespace",
      intake_status_guard([" Status ", "Full name"]), None)
check("guard refuses a tab with no Status column",
      "no 'Status' column" in (intake_status_guard(["Full name"]) or ""), True)
check("guard refuses Status anywhere but column A (B+ are ARRAYFORMULAs)",
      "not column A" in (intake_status_guard(["Full name", "Status"]) or ""), True)

check("plan_intake_cells takes only literal New data cells",
      plan_intake_cells(["Status", "New", "", "In progress", "New ", "Accepted"]),
      [2, 5])
check("a header cell is never planned even if it said New",
      plan_intake_cells(["New", "Accepted"]), [])
check("runs_of groups contiguous rows",
      runs_of([2, 3, 4, 7, 9, 10]), [[2, 4], [7, 7], [9, 10]])

check("sqref_cols reads a plain range", sqref_cols("D2:D1000"), {3})
check("sqref_cols reads an ABSOLUTE range ($ broke col_of and made the "
      "Status validation unmatchable)", sqref_cols("$D$2:$D$1000"), {3})
check("sqref_cols reads a mixed-absolute span", sqref_cols("$B2:C$10"), {1, 2})
check("sqref_cols reads multiple refs and spans",
      sqref_cols("B2:C10 F3"), {1, 2, 5})
check("dv_list strips the quotes", dv_list('"A,B,C"'), ["A", "B", "C"])

# The two live rule shapes on the intake role tabs: the whole-row status color
# and the pink 1-week SLA breach (which ORs blank with the old literal).
SLA = '=AND($C2<>"",OR($A2="",$A2="New"),TODAY()-INT($C2)>=7)'
check("the status color rule is renamed",
      migrate_cf_formula('=$A2="New"'), '=$A2="Prospect"')
check("the SLA rule keeps its date logic and its blank arm",
      migrate_cf_formula(SLA),
      '=AND($C2<>"",OR($A2="",$A2="Prospect"),TODAY()-INT($C2)>=7)')
check("a rule testing another status is left alone",
      migrate_cf_formula('=$A2="Existing (from MLOps)"'), None)
check("the City (New) header is not a Status test",
      migrate_cf_formula('=$J2="City (New)"'), None)
check("an already-migrated rule is in sync (idempotent)",
      migrate_cf_formula('=$A2="Prospect"'), None)
check("the tokens are the column-A Status test",
      (CF_TOKEN_OLD, CF_TOKEN_NEW), ('$A2="New"', '$A2="Prospect"'))

check("a migrated rule is not also reported as a refusal",
      cf_refusal('=$A2="New"'), None)
check("an unrecognised column-A test for New is reported, never guessed at",
      "migrate it by hand" in (cf_refusal('=$A$2 = "New"') or ""), True)
check("EXACT() against column A is reported too",
      "migrate it by hand" in (cf_refusal('=EXACT($A2,"New")') or ""), True)
check("a NEGATED column-A test is reported too (highlight-the-untriaged)",
      "migrate it by hand" in (cf_refusal('=$A2<>"New"') or ""), True)
check("...with spaces around the <> as well",
      "migrate it by hand" in (cf_refusal('=$A2 <> "New"') or ""), True)
check("a rule with no Status test is not a refusal",
      cf_refusal('=$K2<>""'), None)

# ---------------------------------------------------------------------------
# The "How to use" tab's status prose
# ---------------------------------------------------------------------------
check("every curated sentence still says the old status",
      [t for t in HOWTO_TEXTS if "New" not in t], [])
check("the swap takes the FIRST New, which is the status one",
      howto_new_text("Every new submission defaults to New. Then Tentative."),
      "Every new submission defaults to Prospect. Then Tentative.")
check("no curated sentence also mentions the City (New) header "
      "(which the first-occurrence swap would eat)",
      [t for t in HOWTO_TEXTS if "City (New)" in t], [])
check("no curated sentence carries a second status New",
      [t for t in HOWTO_TEXTS if t.count("New") != 1], [])

# The tab as get_values returns it: ragged rows, prose in mixed columns, and
# the mentions that must SURVIVE (a form note, the City (New) header, a flow
# line about a new city).
HOWTO_ROWS = [
    ["STATUS"],
    [],
    ["Tab", "New submissions append here automatically."],
    [HOWTO_TEXTS[0], HOWTO_TEXTS[1]],
    ["\U0001f7e6  Blue", HOWTO_TEXTS[2]],
    ["\U0001fa77  Pink", HOWTO_TEXTS[3]],
    ["CITY COLORS  (on the City (Existing) / City (New) columns)"],
    [HOWTO_TEXTS[4]],
    ["        \u2514 New city / new chapter  \u2192  City (New)"],
]
_plans, _refusals = plan_howto(HOWTO_ROWS)
# Expectations are spelled out as LITERALS, not computed with the function
# under test: howto_new_text(HOWTO_TEXTS[1]) as an expectation would still pass
# if the swap ate "new submission" instead of the status.
check("every status sentence is located, and nothing else is",
      [(a1, new) for a1, _o, new in _plans],
      [("A4", "Prospect \u2192 In progress \u2192 Accepted / Denied"),
       ("B4", "Every new submission defaults to Prospect. Pick from the "
              "dropdown as you work each person: Tentative once their LinkedIn "
              "checks out, Interviewing while the interview is scheduled or "
              "under way, then Accepted / Denied. Use Inactive to park one "
              "without deciding, and Duplicate for a repeat submission from "
              "someone already in the queue."),
       ("B5", "Prospect \u2014 untriaged."),
       ("B6", "Overdue \u2014 still Prospect after 1 week (of a 2-week response "
              "SLA). Act on it to clear."),
       ("A8", "Form submission  \u2192  \U0001f7e6 Prospect")])
check("a clean tab raises no refusals", _refusals, [])

_migrated = [[howto_new_text(c) if c in HOWTO_TEXTS else c for c in row]
             for row in HOWTO_ROWS]
check("an already-migrated tab plans nothing (idempotent)",
      plan_howto(_migrated), ([], []))

_reworded = [[c for c in row if c not in (HOWTO_TEXTS[2],)] for row in HOWTO_ROWS]
_p2, _r2 = plan_howto(_reworded)
check("a reworded sentence is REFUSED, not guessed at", len(_r2), 1)
check("the refusal names the sentence", "untriaged" in _r2[0], True)
check("...and the other four are still planned", len(_p2), 4)

check("sentences are located by TEXT, not by a fixed A1 (rows can shift)",
      [a1 for a1, _o, _n in plan_howto([[], []] + HOWTO_ROWS)[0]][:1], ["A6"])

# The flow-diagram head is indented on the live tab; matching strips, but the
# REWRITE must keep the indent or the diagram silently misaligns.
_indented = [["   " + HOWTO_TEXTS[4] + " "] if row and row[0] == HOWTO_TEXTS[4]
             else row for row in HOWTO_ROWS]
check("an indented sentence keeps its own whitespace",
      [new for _a1, _o, new in plan_howto(_indented)[0] if "Form" in new],
      ["   Form submission  \u2192  \U0001f7e6 Prospect "])

_dup = HOWTO_ROWS + [[HOWTO_TEXTS[2]]]
_p3, _r3 = plan_howto(_dup)
check("a duplicated sentence is REFUSED, not half-migrated", len(_r3), 1)
check("the duplicate refusal says how many copies", "appears 2 times" in _r3[0], True)
check("...and the duplicated one is not planned", len(_p3), 4)

try:
    howto_new_text("a sentence with no status in it")
    _raised = None
except ValueError as e:
    _raised = str(e)
check("howto_new_text refuses a sentence with no New (never a self-write)",
      _raised is not None, True)
check("dv_list refuses a non-literal formula", dv_list("=Lists!A1:A9"), None)


# ---------------------------------------------------------------------------
# The one definition of "does this tab have work"
# ---------------------------------------------------------------------------
def _tp(**kw):
    base = {"guard": None, "cell_rows": [], "new_list": None, "cf_plans": []}
    base.update(kw)
    return base

check("tab_changes counts cells + the dropdown + each color rule",
      tab_changes(_tp(cell_rows=[2, 3], new_list=["Prospect"],
                      cf_plans=[("x",), ("y",)])), 5)
check("a guarded tab is never actionable, however much it 'plans'",
      tab_actionable(_tp(guard="Status is not column A", cell_rows=[2])), False)
check("a tab with only color rules IS actionable (the apply gate must not "
      "drop them)", tab_actionable(_tp(cf_plans=[("x",)])), True)
check("an in-sync tab is not actionable", tab_actionable(_tp()), False)


# ---------------------------------------------------------------------------
# The Status dropdown's grid rule: runs, and the three ways the read can lie
# ---------------------------------------------------------------------------
def _dv_row(values):
    if values is None:
        return {}
    return {"values": [{"dataValidation": {
        "condition": {"type": "ONE_OF_LIST",
                      "values": [{"userEnteredValue": v} for v in values]}}}]}

_LEGACY, _DONE = ["New", "Accepted"], ["Prospect", "Accepted"]

_rule, _runs, _ref = grid_rule_plan(
    [_dv_row(None)] + [_dv_row(_LEGACY)] * 4, 0, 100)
check("covered rows become one contiguous run", _runs, [[1, 4]])
check("a clean read raises no refusal", _ref, [])

_rule, _runs, _ref = grid_rule_plan(
    [_dv_row(_LEGACY)] * 2 + [_dv_row(None)] * 3 + [_dv_row(_LEGACY)] * 2, 0, 100)
check("a gap is TWO runs, never the min..max hull (the gap rows carry no "
      "validation and must keep none)", _runs, [[0, 1], [5, 6]])

_rule, _runs, _ref = grid_rule_plan([_dv_row(_DONE)] * 3, 0, 100)
check("an already-migrated tab plans no runs", _runs, [])
check("...and raises no refusal", _ref, [])

_rule, _runs, _ref = grid_rule_plan([{}] * 5, 0, 100)
check("NO dropdown at all is refused — not the same fact as 'already "
      "migrated'", len(_ref), 1)
check("the refusal says the dropdown is missing", "missing" in _ref[0], True)

_rule, _runs, _ref = grid_rule_plan(
    [_dv_row(_LEGACY), _dv_row(["New", "Denied"])], 0, 100)
check("two DIFFERENT lists across covered rows are refused, not flattened",
      any("DIFFERENT lists" in r for r in _ref), True)

_rule, _runs, _ref = grid_rule_plan([_dv_row(_LEGACY)] * 3, 0, 3)
check("a hit at the last probed row is refused (the rule likely runs past "
      "the window every verify also reads through)",
      any("probed row" in r for r in _ref), True)


# ---------------------------------------------------------------------------
# Fixture: a CRM-shaped workbook with Status and Signal validations
# ---------------------------------------------------------------------------
def _c(ref, text, shared_idx=None):
    if text is None:
        return ""
    if shared_idx is not None:
        return '<c r="%s" t="s"><v>%d</v></c>' % (ref, shared_idx)
    return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, text)


def make_crm(status_col, signal_col, status_vals, signal_vals,
             status_dv=CRM_LIST, signal_dv=SIGNAL_LIST, dv_sqref=None,
             shared_status_rows=(), shared_signal_rows=(), status_dv_xml=None,
             x14_sqref=None):
    """Build an .xlsx whose Attendees sheet has Status/Signal at the given
    0-based columns. `shared_status_rows` store their Status value through
    sharedStrings — the legacy storage form, and the reason a bytes-level
    replace of "New" could never be safe (Signal shares the same string)."""
    headers = ["Full name"] + [""] * (max(status_col, signal_col) + 1)
    headers[status_col] = "Status"
    headers[signal_col] = "Signal"
    sst = ["New"]
    rows = ['<row r="1">%s</row>'
            % "".join(_c(cell_ref(i, 1), h) for i, h in enumerate(headers) if h)]
    for n, (sv, gv) in enumerate(zip(status_vals, signal_vals), start=2):
        cells = _c(cell_ref(0, n), "Person %d" % n)
        if sv is not None:
            if n in shared_status_rows and sv == "New":
                cells += _c(cell_ref(status_col, n), sv, shared_idx=0)
            else:
                cells += _c(cell_ref(status_col, n), sv)
        if gv is not None:
            if n in shared_signal_rows and gv == "New":
                # ALIASES the same sst entry as the Status cells: any "fix"
                # that rewrote the shared string in place would corrupt Signal
                # too, and this is what proves the migration does not.
                cells += _c(cell_ref(signal_col, n), gv, shared_idx=0)
            else:
                cells += _c(cell_ref(signal_col, n), gv)
        rows.append('<row r="%d">%s</row>' % (n, cells))
    s_let = cell_ref(status_col, 2)[:-1]
    g_let = cell_ref(signal_col, 2)[:-1]
    dvs = status_dv_xml if status_dv_xml is not None else (
        '<dataValidation type="list" sqref="%s">'
        '<formula1>"%s"</formula1></dataValidation>'
        % (dv_sqref or "%s2:%s1000" % (s_let, s_let), ",".join(status_dv)))
    dvs += ('<dataValidation type="list" sqref="%s2:%s1000">'
            '<formula1>"%s"</formula1></dataValidation>'
            % (g_let, g_let, ",".join(signal_dv)))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:K12" /><sheetData>%s</sheetData>'
        '<dataValidations count="2">%s</dataValidations>%s</worksheet>'
        % ("".join(rows), dvs, _x14(x14_sqref)))
    parts = {
        "[Content_Types].xml": "<Types />",
        "_rels/.rels": "<Relationships />",
        "xl/workbook.xml":
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Attendees" sheetId="1" r:id="rId1" /></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" /></Relationships>',
        "xl/sharedStrings.xml":
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join("<si><t>%s</t></si>" % s for s in sst) + "</sst>",
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in parts.items():
            z.writestr(n, v.encode())
    return load_parts(buf.getvalue())


def _x14(sqref):
    """An extension-namespace validation block, as Excel writes for lists it
    cannot store inline. Invisible to a scan of the main namespace."""
    if not sqref:
        return ""
    return ('<extLst><ext xmlns:x14="http://schemas.microsoft.com/office/'
            'spreadsheetml/2009/9/main"><x14:dataValidations count="1">'
            '<x14:dataValidation type="list"><xm:sqref xmlns:xm='
            '"http://schemas.microsoft.com/office/excel/2006/main">%s'
            '</xm:sqref></x14:dataValidation></x14:dataValidations>'
            '</ext></extLst>' % sqref)


def open_sheet(parts):
    return CrmStatusSheet(parts, sheet_part(parts, "Attendees"))


def migrate_and_reload(names, parts):
    sheet = open_sheet(parts)
    plan, refusals = plan_crm(sheet)
    sheet.apply(plan["cells"], plan["dvs"])
    return plan, refusals, load_parts(save_parts(names, parts))


# ---------------------------------------------------------------------------
# The canonical shape: Signal at B, Status at D; one legacy shared-string cell
# ---------------------------------------------------------------------------
names, parts = make_crm(status_col=3, signal_col=1,
                        status_vals=["New", "Prospect", "New", "Organizer"],
                        signal_vals=["New", "High", None, "New"],
                        shared_status_rows={4}, shared_signal_rows={2})
sheet = open_sheet(parts)
check("Status is located by header name", sheet.status_col, 3)
check("Signal is located by header name", sheet.signal_col, 1)
plan, refusals = plan_crm(sheet)
check("only Status cells holding New are planned", plan["cells"], [2, 4])
check("exactly one validation (Status's) is planned",
      [(old, new) for _dv, old, new in plan["dvs"]],
      [(CRM_LIST, CRM_LIST[1:])])
check("no refusals on a healthy workbook", refusals, [])

_before_all = {n: v for n, v in parts.items()}
plan, refusals, (rt_names, rt_parts) = migrate_and_reload(names, parts)
rt = open_sheet(rt_parts)
check("every zip part survives the round-trip", rt_names, names)
_sheet_part_name = sheet_part(rt_parts, "Attendees")
check("EVERY untouched part is byte-identical, sharedStrings included "
      "(the part a bytes-level 'fix' would corrupt)",
      [n for n, v in rt_parts.items()
       if n != _sheet_part_name and v != _before_all[n]], [])
check("Status cells now read Prospect (incl. the shared-string one)",
      rt.status_cells_holding_old(), [])
raw = rt_parts[sheet_part(rt_parts, "Attendees")].decode()
check("the rewritten Status cells read back as Prospect",
      [mig.cell_text(c, rt.sst)
       for r in rt.rows.values() if int(r.get("r")) > 1
       for c in r.findall(mig.X + "c")
       if mig.col_of(c.get("r")) == rt.status_col],
      ["Prospect", "Prospect", "Prospect", "Organizer"])
check("the Status dropdown lost New",
      '"%s"' % ",".join(CRM_LIST[1:]) in raw, True)
check("Signal's dropdown STILL offers its unrelated New",
      '"%s"' % ",".join(SIGNAL_LIST) in raw, True)
# Signal data cells: row 2 and row 5 held "New" — prove they survived.
sig_cells = [c for r in rt.rows.values() if int(r.get("r")) > 1
             for c in r.findall(mig.X + "c")
             if mig.col_of(c.get("r")) == rt.signal_col]
from sync_crm import cell_text  # noqa: E402
check("Signal data cells still hold their New values",
      sorted(cell_text(c, rt.sst) for c in sig_cells if cell_text(c, rt.sst)),
      ["High", "New", "New"])
plan2, refusals2 = plan_crm(rt)
check("a migrated workbook plans nothing (idempotent / verify contract)",
      (plan2["cells"], plan2["dvs"], refusals2), ([], [], []))


# ---------------------------------------------------------------------------
# Swapped columns: Status at B, Signal at D — header location, not position
# ---------------------------------------------------------------------------
names_s, parts_s = make_crm(status_col=1, signal_col=3,
                            status_vals=["New", "Declined"],
                            signal_vals=["New", "New"])
sheet_s = open_sheet(parts_s)
check("swapped: Status found at column B", sheet_s.status_col, 1)
plan_s, refusals_s, (_n, rt_parts_s) = migrate_and_reload(names_s, parts_s)
rt_s = open_sheet(rt_parts_s)
raw_s = rt_parts_s[sheet_part(rt_parts_s, "Attendees")].decode()
check("swapped: the one New status cell was planned", plan_s["cells"], [2])
check("swapped: Status dropdown (on B) lost New",
      '"%s"' % ",".join(CRM_LIST[1:]) in raw_s, True)
check("swapped: Signal dropdown (on D) keeps New",
      '"%s"' % ",".join(SIGNAL_LIST) in raw_s, True)
check("swapped: both Signal data cells still say New",
      sorted(cell_text(c, rt_s.sst)
             for r in rt_s.rows.values() if int(r.get("r")) > 1
             for c in r.findall(mig.X + "c")
             if mig.col_of(c.get("r")) == rt_s.signal_col),
      ["New", "New"])
check("swapped: a human's Declined is untouched",
      "Declined" in raw_s, True)


# ---------------------------------------------------------------------------
# Refusals: a validation spanning Status AND another column is never edited
# ---------------------------------------------------------------------------
names_r, parts_r = make_crm(status_col=3, signal_col=1,
                            status_vals=["New"], signal_vals=["New"],
                            dv_sqref="B2:D1000")
plan_r, refusals_r = plan_crm(open_sheet(parts_r))
check("a multi-column Status validation is refused, not edited",
      (len(plan_r["dvs"]), len(refusals_r)), (0, 1))
check("the refusal names the sqref", "B2:D1000" in refusals_r[0], True)
check("...but the cell rewrites are still planned", plan_r["cells"], [2])

try:
    make_no_status = make_crm(status_col=3, signal_col=1,
                              status_vals=["New"], signal_vals=["New"])
    make_no_status[1]["xl/worksheets/sheet1.xml"] = \
        make_no_status[1]["xl/worksheets/sheet1.xml"].replace(b"Status", b"Stage")
    open_sheet(make_no_status[1])
    check("a workbook without a Status header is refused", "no error", "ValueError")
except ValueError as e:
    check("a workbook without a Status header is refused",
          "no 'Status' header" in str(e), True)


# ---------------------------------------------------------------------------
# The shapes this script must REFUSE rather than silently skip
# ---------------------------------------------------------------------------
_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["New"], dv_sqref="$D$2:$D$1000")
_plan, _ref = plan_crm(open_sheet(_p))
check("an ABSOLUTE sqref still matches the Status column (it used to miss, "
      "leaving the legacy value in that CRM's dropdown forever)",
      [(old, new) for _dv, old, new in _plan["dvs"]], [(CRM_LIST, CRM_LIST[1:])])
check("...and raises no refusal", _ref, [])

_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["High"],
                  status_dv_xml='<dataValidation type="list" sqref="D2:D1000">'
                                '<formula1>Lists!$A$1:$A$9</formula1>'
                                '</dataValidation>')
_plan, _ref = plan_crm(open_sheet(_p))
check("a RANGE-BACKED Status list is refused, not skipped (skipped, the "
      "workbook reports in sync and verify agrees)", len(_ref), 1)
check("the refusal shows the formula it could not read",
      "Lists!$A$1:$A$9" in _ref[0], True)
check("...and the cell rewrites are still planned", _plan["cells"], [2])

_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["High"], status_dv_xml="")
_plan, _ref = plan_crm(open_sheet(_p))
check("a workbook with NO Status validation at all is reported, not read as "
      "already migrated", any("no data validation" in r for r in _ref), True)

_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["High"], x14_sqref="D2:D1000")
_plan, _ref = plan_crm(open_sheet(_p))
check("a Status validation hidden in the x14 extension block is refused "
      "(a scan of the main namespace cannot see it)",
      any("x14 extension" in r for r in _ref), True)

_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["High"], x14_sqref="F2:F1000")
_plan, _ref = plan_crm(open_sheet(_p))
check("an x14 validation on an UNRELATED column is not refused", _ref, [])

# Signal's own list must be provably untouched in production, not just in tests.
_n, _p = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                  signal_vals=["New"])
_sh = open_sheet(_p)
_sig_before = _sh.signal_dv_text()
check("signal_dv_text reads the Signal list", _sig_before, '"%s"' % ",".join(SIGNAL_LIST))
_pl, _rf = plan_crm(_sh)
_sh.apply(_pl["cells"], _pl["dvs"])
check("...and the migration leaves it byte-identical",
      open_sheet(load_parts(save_parts(_n, _p))[1]).signal_dv_text(), _sig_before)

# A plan from another workbook holds Element handles into a DIFFERENT tree.
_na, _pa = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                    signal_vals=["High"])
_nb, _pb = make_crm(status_col=3, signal_col=1,
                    status_vals=["New", "New", "New"],
                    signal_vals=["High", "High", "High"])
_sheet_a = open_sheet(_pa)
try:
    _sheet_a.apply([2, 3, 4], [])
    _mismatch = None
except ValueError as e:
    _mismatch = str(e)
check("apply refuses a plan naming rows this sheet does not have "
      "(plan/instance mismatch would edit a detached tree, silently)",
      "plan/instance mismatch" in (_mismatch or ""), True)

# The ET prefix registry is process-global and Phase B opens ~84 workbooks in
# a row, so a workbook that declares an ALIASED spreadsheetml prefix must
# neither be written mis-rooted nor poison the next workbook. This is the bug
# sync_crm.Attendees.serialize() carries its own regression test for.
def make_aliased_crm():
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<x:worksheet xmlns:x="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><x:sheetData>'
             '<x:row r="1"><x:c r="A1" t="inlineStr"><x:is><x:t>Status</x:t>'
             '</x:is></x:c></x:row>'
             '<x:row r="2"><x:c r="A2" t="inlineStr"><x:is><x:t>New</x:t>'
             '</x:is></x:c></x:row></x:sheetData>'
             '<x:dataValidations><x:dataValidation type="list" sqref="A2:A100">'
             '<x:formula1>"New,Prospect"</x:formula1></x:dataValidation>'
             '</x:dataValidations></x:worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types />")
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Attendees" sheetId="1" r:id="rId1" />'
                   '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="rId1" '
                   'Target="worksheets/sheet1.xml" /></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return load_parts(buf.getvalue())

_na, _pa = make_aliased_crm()
_sa = open_sheet(_pa)
_pla, _rfa = plan_crm(_sa)
check("an aliased workbook is still read by header name", _pla["cells"], [2])
_sa.apply(_pla["cells"], _pla["dvs"])
check("an aliased workbook serializes back as <worksheet>, not <x:worksheet>",
      b"<worksheet" in _pa[sheet_part(_pa, "Attendees")], True)

# ...and the NEXT workbook, opened after it, must be unaffected.
_nb, _pb = make_crm(status_col=3, signal_col=1, status_vals=["New"],
                    signal_vals=["High"])
_sb = open_sheet(_pb)
_plb, _rfb = plan_crm(_sb)
_sb.apply(_plb["cells"], _plb["dvs"])
check("the workbook opened AFTER it is not poisoned by the global ET registry",
      b"<worksheet" in _pb[sheet_part(_pb, "Attendees")], True)


# ---------------------------------------------------------------------------
# Intake apply: column A and nothing else, explicit full rule
# ---------------------------------------------------------------------------
_calls = []
def _fake_gws_json(*args, params=None, body=None):
    _calls.append((args, params, body))
    return {}

_saved = mig.gws_json
mig.gws_json = _fake_gws_json
try:
    _cf_rule = {"ranges": [{"sheetId": 99, "startRowIndex": 1}],
                "booleanRule": {"condition": {
                    "type": "CUSTOM_FORMULA",
                    "values": [{"userEnteredValue": '=$A2="Prospect"'}]},
                    "format": {"backgroundColor": {"blue": 1}}}}
    mig.apply_intake_tab({
        "tab": "Organizers", "guard": None, "cell_rows": [5, 6, 7, 12],
        "gid": 99, "cf_gid": 99, "dv_runs": [[1, 499], [899, 999]],
        "cf_plans": [(4, '=$A2="New"', _cf_rule)], "cf_refusals": [],
        "dv_refusals": [],
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": v}
                                          for v in INTAKE_LIST]},
                 "showCustomUi": True},
        "new_list": migrate_list(INTAKE_LIST)})
finally:
    mig.gws_json = _saved

check("apply issues the dropdown, then the color rules, then the cell writes",
      [a[:4] for a, _p, _b in _calls],
      [("sheets", "spreadsheets", "batchUpdate"),
       ("sheets", "spreadsheets", "batchUpdate"),
       ("sheets", "spreadsheets", "values", "batchUpdate")])
_dv_reqs = [r["setDataValidation"] for r in _calls[0][2]["requests"]]
check("the dropdown is written per contiguous RUN, never over the hull "
      "(the gap rows carry a different validation, or none)",
      [(r["range"]["startRowIndex"], r["range"]["endRowIndex"]) for r in _dv_reqs],
      [(1, 500), (899, 1000)])
check("every dropdown write targets column A only",
      {(r["range"]["startColumnIndex"], r["range"]["endColumnIndex"])
       for r in _dv_reqs}, {(0, 1)})
check("the rule is EXPLICIT and full (gws drops empty request objects)",
      [v["userEnteredValue"] for v in _dv_reqs[0]["rule"]["condition"]["values"]],
      migrate_list(INTAKE_LIST))
_cf_req = _calls[1][2]["requests"][0]["updateConditionalFormatRule"]
check("the color rule is replaced at its own index",
      (_cf_req["sheetId"], _cf_req["index"]), (99, 4))
check("the rule is re-sent WHOLE — its ranges and color survive",
      (_cf_req["rule"]["ranges"], _cf_req["rule"]["booleanRule"]["format"]),
      ([{"sheetId": 99, "startRowIndex": 1}], {"backgroundColor": {"blue": 1}}))
_cell_body = _calls[2][2]
check("cell writes are RAW", _cell_body["valueInputOption"], "RAW")
check("every cell write targets column A, never B+",
      all(re.fullmatch(r"'Organizers'!A\d+:A\d+", d["range"])
          for d in _cell_body["data"]), True)
check("contiguous rows are grouped into runs",
      [d["range"] for d in _cell_body["data"]],
      ["'Organizers'!A5:A7", "'Organizers'!A12:A12"])
check("run lengths match their value payloads",
      [len(d["values"]) for d in _cell_body["data"]], [3, 1])
check("the written literal is Prospect",
      {v[0] for d in _cell_body["data"] for v in d["values"]}, {"Prospect"})

# showCustomUi: the fixture above already carries it, so asserting on that run
# proves nothing. Sheets OMITS the key when the chip is off — pin what the
# setdefault actually does to a rule that lacks it.
_calls.clear()
mig.gws_json = _fake_gws_json
try:
    mig.apply_intake_tab({
        "tab": "Organizers", "guard": None, "cell_rows": [], "gid": 7,
        "cf_gid": 7, "dv_runs": [[1, 99]], "cf_plans": [], "cf_refusals": [],
        "dv_refusals": [],
        "rule": {"condition": {"type": "ONE_OF_LIST", "values": []}},
        "new_list": ["Prospect"]})
finally:
    mig.gws_json = _saved
check("a rule with NO showCustomUi gets it restated as True",
      _calls[0][2]["requests"][0]["setDataValidation"]["rule"]["showCustomUi"],
      True)


# ---------------------------------------------------------------------------
# intake_cf_plans: what it migrates, and what it refuses rather than skips
# ---------------------------------------------------------------------------
def _cf(kind, values, extra=None):
    rule = {"ranges": [{"sheetId": 1}],
            "booleanRule": {"condition": {
                "type": kind,
                "values": [{"userEnteredValue": v} for v in values]}}}
    if extra:
        rule["booleanRule"].update(extra)
    return rule

def _fake_cf_get(formats):
    def _fake(*args, params=None, body=None):
        return {"sheets": [{"properties": {"sheetId": 42, "title": "Organizers"},
                            "conditionalFormats": formats}]}
    return _fake

def _cf_plans(formats):
    mig.gws_json = _fake_cf_get(formats)
    try:
        return mig.intake_cf_plans("Organizers")
    finally:
        mig.gws_json = _saved

_gid, _plans4, _ref4 = _cf_plans([
    _cf("CUSTOM_FORMULA", ['=$A2="New"']),
    _cf("CUSTOM_FORMULA", ['=$A2="Existing (from MLOps)"']),
    _cf("TEXT_EQ", ["New"]),
    _cf("TEXT_EQ", ["Accepted"]),
    {"ranges": [{"sheetId": 1}], "gradientRule": {"minpoint": {}}},
])
check("the sheetId is returned for the named tab", _gid, 42)
check("only the CUSTOM_FORMULA Status rule is planned",
      [(i, old) for i, old, _r in _plans4], [(0, '=$A2="New"')])
check("a TEXT_EQ 'New' rule is REFUSED, not silently skipped (it is how the "
      "Sheets UI makes a status color, and verify re-runs this function)",
      len(_ref4), 1)
check("the refusal names the condition type", "TEXT_EQ" in _ref4[0], True)
check("a TEXT_EQ on another status, and a gradient rule, are neither planned "
      "nor refused", [r for r in _ref4 if "Accepted" in r], [])

_gid, _plans5, _ref5 = _cf_plans([_cf("CUSTOM_FORMULA", ['=$A2="Prospect"'])])
check("an already-migrated tab plans and refuses nothing",
      (_plans5, _ref5), ([], []))

_gid, _plans6, _ref6 = _cf_plans(
    [_cf("CUSTOM_FORMULA", ['=OR($A2="New",EXACT($A2,"New"))'])])
check("a rule that is BOTH migrated and partly unrecognised is planned AND "
      "reported", (len(_plans6), len(_ref6)), (1, 1))


# ---------------------------------------------------------------------------
# assert_git_safe — the PII guard (ported from test_backup.py's cases)
# ---------------------------------------------------------------------------
def _git_safe_with(results):
    """Run assert_git_safe with subprocess.run stubbed; return SystemExit msg."""
    calls = iter(results)
    class _P:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    def _fake_run(argv, **kw):
        return _P(*next(calls))
    saved = mig.subprocess.run
    mig.subprocess.run = _fake_run
    try:
        mig.assert_git_safe("/tmp/whatever")
        return None
    except SystemExit as e:
        return str(e)
    finally:
        mig.subprocess.run = saved

check("outside any repo is safe",
      _git_safe_with([(128, "", "fatal: not a git repository (or any of the "
                              "parent directories): .git")]), None)
check("a git failure that is NOT 'outside a repo' ABORTS — mapping it to safe "
      "silently disengages the PII guard",
      "cannot verify" in (_git_safe_with(
          [(128, "", "fatal: detected dubious ownership in repository at '/x'")])
          or ""), True)
check("inside a repo, ignored and untracked is safe",
      _git_safe_with([(0, "/repo", ""), (0,), (1,)]), None)
check("inside a repo but NOT ignored aborts",
      "not gitignored" in (_git_safe_with([(0, "/repo", ""), (1,), (1,)]) or ""),
      True)
check("an ignored-but-TRACKED path aborts (.gitignore does not un-track)",
      "TRACKED" in (_git_safe_with([(0, "/repo", ""), (0,), (0,)]) or ""), True)

print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
