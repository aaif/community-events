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
from migrate_status_prospect import (CF_TOKEN_NEW, CF_TOKEN_OLD, CrmStatusSheet,
                                     cf_refusal, dv_list, intake_status_guard,
                                     migrate_cf_formula, migrate_list, plan_crm,
                                     plan_intake_cells, runs_of, sqref_cols)
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
check("a rule with no Status test is not a refusal",
      cf_refusal('=$K2<>""'), None)
check("dv_list refuses a non-literal formula", dv_list("=Lists!A1:A9"), None)


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
             shared_status_rows=()):
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
            cells += _c(cell_ref(signal_col, n), gv)
        rows.append('<row r="%d">%s</row>' % (n, cells))
    s_let = cell_ref(status_col, 2)[:-1]
    g_let = cell_ref(signal_col, 2)[:-1]
    dvs = ('<dataValidation type="list" sqref="%s">'
           '<formula1>"%s"</formula1></dataValidation>'
           % (dv_sqref or "%s2:%s1000" % (s_let, s_let), ",".join(status_dv)))
    dvs += ('<dataValidation type="list" sqref="%s2:%s1000">'
            '<formula1>"%s"</formula1></dataValidation>'
            % (g_let, g_let, ",".join(signal_dv)))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:K12" /><sheetData>%s</sheetData>'
        '<dataValidations count="2">%s</dataValidations></worksheet>'
        % ("".join(rows), dvs))
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
                        shared_status_rows={4})
sheet = open_sheet(parts)
check("Status is located by header name", sheet.status_col, 3)
check("Signal is located by header name", sheet.signal_col, 1)
plan, refusals = plan_crm(sheet)
check("only Status cells holding New are planned", plan["cells"], [2, 4])
check("exactly one validation (Status's) is planned",
      [(old, new) for _dv, old, new in plan["dvs"]],
      [(CRM_LIST, CRM_LIST[1:])])
check("no refusals on a healthy workbook", refusals, [])

_before_wb = parts["xl/workbook.xml"]
plan, refusals, (rt_names, rt_parts) = migrate_and_reload(names, parts)
rt = open_sheet(rt_parts)
check("every zip part survives the round-trip", rt_names, names)
check("untouched parts are byte-identical",
      rt_parts["xl/workbook.xml"] == _before_wb, True)
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
        "gid": 99, "r0": 1, "r1": 1000, "cf_gid": 99,
        "cf_plans": [(4, '=$A2="New"', _cf_rule)], "cf_refusals": [],
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
_cf_req = _calls[1][2]["requests"][0]["updateConditionalFormatRule"]
check("the color rule is replaced at its own index",
      (_cf_req["sheetId"], _cf_req["index"]), (99, 4))
check("the rule is re-sent WHOLE — its ranges and color survive",
      (_cf_req["rule"]["ranges"], _cf_req["rule"]["booleanRule"]["format"]),
      ([{"sheetId": 99, "startRowIndex": 1}],
       {"backgroundColor": {"blue": 1}}))
_dv_req = _calls[0][2]["requests"][0]["setDataValidation"]
check("the dropdown rule targets column A only",
      (_dv_req["range"]["startColumnIndex"], _dv_req["range"]["endColumnIndex"]),
      (0, 1))
check("the rule is EXPLICIT and full (gws drops empty request objects)",
      [v["userEnteredValue"] for v in _dv_req["rule"]["condition"]["values"]],
      migrate_list(INTAKE_LIST))
check("showCustomUi is restated explicitly",
      _dv_req["rule"]["showCustomUi"], True)
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

print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
