#!/usr/bin/env python3
"""One-shot: rename the intake/CRM status "New" to "Prospect".

"New" misread as *new organizer*; "Prospect" is the term sync_crm.py already
writes for a pipeline candidate, so the rename unifies the vocabulary. Code
accepts BOTH values during the transition (sync_crm.PIPELINE_STATUSES,
intake.normalize_status); this script is what retires "New" from the sheets:

Phase A — the Intake Ops role tabs (Organizers / Speakers / Hosts):
  * the Status dropdown (ONE_OF_LIST dataValidation on column A) has its "New"
    entry replaced by "Prospect" — reapplied as an EXPLICIT full rule, never a
    partial one: gws drops empty request objects, so a partial rule hoping to
    "clear and keep the rest" would silently no-op;
  * every Status cell literally holding "New" is rewritten to "Prospect" via a
    RAW values write to the exact A-column cells. Status is the ONE hand-edited
    literal column on those tabs; columns B+ are ARRAYFORMULA mirrors and a
    literal written into them #REF!s the whole tab, so this script refuses to
    run against a tab whose Status column is not column A;
  * the hand-made conditional-format rules that TEST the Status literal are
    renamed with it: the blue whole-row color (`=$A2="New"`) and the arm of the
    pink SLA-breach rule (`OR($A2="",$A2="New")`). Renaming only the cells
    unpaints every Prospect row and — the real damage — stops the 1-week SLA
    breach from ever firing again. These rules are hand-made on the sheet;
    clean.py owns only the red error rule and the city/violet rules, so nothing
    else would ever repair them;
  * the "How to use" tab's status prose is migrated by exact whole-sentence
    match — it is what an organizer reads before picking from the dropdown.

Phase B — every chapter CRM workbook (the ~80 "<City> CRM.xlsx" under the
Chapters Drive folder, plus the TemplateCity and TemplateSeries templates):
  * the Status column is located BY HEADER NAME on the Attendees sheet (column
    positions vary per CRM — never assume D);
  * the Status column's dataValidation list loses its leading "New" ("Prospect"
    is already on the list). The Signal column's list also contains a "New" —
    an UNRELATED value that must survive, which is why validations are matched
    to the header-located Status column, never by list content alone;
  * Status data cells holding "New" (the sheet default for hand-added rows) are
    rewritten to "Prospect".
  Edits are zip-part surgery in create_chapter._rewrite_zip's style: only the
  Attendees sheet part is re-serialized; every other part is repacked
  byte-identically. Pre-edit bytes are kept in a mkdtemp backup dir.

House rules: report is the default and writes nothing; --write applies, then
re-downloads / re-reads and verifies, printing a Verified line. No member names
or emails on stdout — counts, tab names and file names only.

Exit codes: 0 everything already in sync; 2 changes proposed (or applied);
1 failure (including a failed verify or a skipped workbook).

Usage:
  python3 migrate_status_prospect.py            # report only, zero writes
  python3 migrate_status_prospect.py --city Boston   # scope Phase B to one CRM
  python3 migrate_status_prospect.py --write    # apply, then verify
"""
import argparse
import copy
import os
import re
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import (INTAKE_ID, download, fold_city, fresh_if_unchanged,  # noqa: E402
                           get_values, gws_json, upload)
from sync_crm import (CRM_SHEET, X, XLSX, cell_ref, cell_text, col_of,  # noqa: E402
                      find_crm,
                      list_chapter_folders, load_parts, register_namespaces,
                      save_parts, set_cell, shared_strings, sheet_part,
                      _XML_DECL)

OLD, NEW = "New", "Prospect"
ROLE_TABS = ("Organizers", "Speakers", "Hosts")

# The template workbooks must migrate too, or every future chapter/series is
# born with the legacy dropdown. Ids as declared by their own creation scripts
# (create_chapter.TEMPLATE_FOLDER / create_series.TEMPLATE_FOLDER — different
# skills, so the ids are restated rather than imported across skill folders).
# TemplateCity is itself a child of the Chapters parent and is deduped by id.
TEMPLATE_FOLDERS = (
    {"id": "1PHvEgqnHo0RrsFyA47O9iRJGaKehC8Eg", "name": "TemplateCity"},
    {"id": "1M15wzKvQqd_jQz5cG16NO_YcbWU3EH1j", "name": "TemplateSeries"},
)

# How far down column A the dropdown is probed. The rule ships on roughly
# A2:A1000; probing past it just reads empty rows.
PROBE_ROWS = 2000


# ----------------------------------------------------------------------------
# Pure plan logic (offline-testable)
# ----------------------------------------------------------------------------
def migrate_list(values):
    """New dropdown list with "New" retired, or None when already in sync.

    "New" becomes "Prospect" in place when Prospect is not on the list (the
    intake dropdown); when Prospect is already offered (the CRM list), "New" is
    simply dropped. Order is otherwise preserved — the dropdown is a workflow,
    and reshuffling it would reorder every status menu an operator uses.
    """
    if OLD not in values:
        return None
    if NEW in values:
        return [v for v in values if v != OLD]
    return [NEW if v == OLD else v for v in values]


# The Status-literal test inside a conditional-format formula. Column A is the
# Status column (intake_status_guard requires it), so the exact `$A2="New"`
# token is the whole match surface: every other mention of the word on these
# tabs — the `City (New)` header, the CRMs' unrelated Signal list — is not a
# column-A equality test and must not be touched.
CF_TOKEN_OLD = '$A2="%s"' % OLD
CF_TOKEN_NEW = '$A2="%s"' % NEW
# A column-A test for "New" written some other way (spacing, EXACT(), $A$2).
# Not migrated silently: reported, so a human fixes it rather than the rule
# quietly going dead the way `=$A2="New"` just did.
CF_SUSPECT = re.compile(r'\$A\$?\d+\s*=\s*"%s"|EXACT\s*\(\s*\$A' % OLD)


def migrate_cf_formula(formula):
    """The rule formula with its Status test renamed, or None when it has none.

    A plain textual swap of the exact token — the rest of the formula (the SLA
    rule's date arithmetic, its blank-status arm) is someone else's logic and
    is preserved verbatim.
    """
    f = formula or ""
    return f.replace(CF_TOKEN_OLD, CF_TOKEN_NEW) if CF_TOKEN_OLD in f else None


def cf_refusal(formula):
    """Why a rule this script did not migrate still looks like it tests the old
    Status, or None. Catches the near-misses of `migrate_cf_formula`."""
    f = formula or ""
    if CF_TOKEN_OLD in f or not CF_SUSPECT.search(f):
        return None
    return ("tests the Status column for %r in a shape this script does not "
            "rewrite (%s) — migrate it by hand" % (OLD, f))


HOWTO_TAB = "How to use"
# The tab organizers actually read before they touch a dropdown: it teaches the
# status flow in prose, so a rename that skips it leaves the sheet documenting
# a value the dropdown no longer offers.
#
# Migrated by EXACT WHOLE-CELL match — never a word swap over the tab. The same
# tab legitimately says "New submissions", "New city" and "City (New)" (a
# column header, not a status), and a regex for the word would have rewritten
# all three. Pinning the entire sentence is also what makes the single
# first-occurrence swap below unambiguous: in each of these five cells the
# first capital-N "New" IS the status. A sentence present in neither spelling
# means the tab was reworded — reported, never guessed at.
HOWTO_TEXTS = (
    "New \u2192 In progress \u2192 Accepted / Denied",
    "Every new submission defaults to New. Pick from the dropdown as you work "
    "each person: Tentative once their LinkedIn checks out, Interviewing while "
    "the interview is scheduled or under way, then Accepted / Denied. Use "
    "Inactive to park one without deciding, and Duplicate for a repeat "
    "submission from someone already in the queue.",
    "New \u2014 untriaged.",
    "Overdue \u2014 still New after 1 week (of a 2-week response SLA). Act on "
    "it to clear.",
    "Form submission  \u2192  \U0001f7e6 New",
)


def howto_new_text(old):
    """The migrated text of one How-to-use sentence: the FIRST "New" only."""
    return old.replace(OLD, NEW, 1)


def plan_howto(rows):
    """([(a1, old, new)], [refusals]) for the How-to-use tab's status prose.

    `rows` is the tab as returned by get_values (ragged rows of strings). Each
    sentence is located by its text, not by a fixed A1 — the tab gets rows
    inserted as it is edited, and a coordinate would silently rewrite the wrong
    cell after that.
    """
    where = {}
    for i, row in enumerate(rows, start=1):
        for j, cell in enumerate(row):
            where.setdefault((cell or "").strip(), cell_ref(j, i))
    plans, refusals = [], []
    for old in HOWTO_TEXTS:
        new = howto_new_text(old)
        if old in where:
            plans.append((where[old], old, new))
        elif new not in where:
            refusals.append("the sentence %r is on the tab in neither "
                            "spelling \u2014 it was reworded; migrate it by hand"
                            % (old[:48] + ("..." if len(old) > 48 else "")))
    return plans, refusals


def intake_status_guard(headers):
    """Why a role tab must NOT be written, or None when it is safe.

    Status is resolved by header name AND required to be column A: columns B+
    are ARRAYFORMULA mirrors, and one literal written into them #REF!s the
    whole tab. A layout where Status moved is a migration this script does not
    know how to do safely, so it refuses rather than guesses.
    """
    heads = [h.strip() for h in headers]
    if "Status" not in heads:
        return "no 'Status' column — headers: %s" % heads[:8]
    if heads.index("Status") != 0:
        return ("'Status' is not column A (found at index %d) — columns B+ are "
                "ARRAYFORMULA mirrors and must never be written; refusing"
                % heads.index("Status"))
    return None


def plan_intake_cells(col_a_values):
    """Sheet row numbers (1-based) of data cells literally holding "New".

    `col_a_values` is column A top to bottom, header included. Only the exact
    legacy value is rewritten — blanks stay blank (code already reads a blank
    as Prospect) and every other status is someone else's decision.
    """
    return [i for i, v in enumerate(col_a_values, start=1)
            if i > 1 and (v or "").strip() == OLD]


def runs_of(rownums):
    """Contiguous [start, end] row runs, for compact range writes."""
    out = []
    for r in sorted(rownums):
        if out and r == out[-1][1] + 1:
            out[-1][1] = r
        else:
            out.append([r, r])
    return out


def sqref_cols(sqref):
    """0-based column indices covered by an sqref like 'D2:D1000 F3'."""
    cols = set()
    for ref in (sqref or "").split():
        parts = ref.split(":")
        a = col_of(parts[0])
        b = col_of(parts[-1])
        cols.update(range(min(a, b), max(a, b) + 1))
    return cols


def dv_list(formula_text):
    """The list a <formula1> literal holds, or None when it isn't a literal."""
    t = (formula_text or "").strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].split(",")
    return None


class CrmStatusSheet:
    """The Attendees sheet of one CRM, opened just far enough to migrate Status.

    Deliberately NOT sync_crm.Attendees: that class requires all eleven CRM
    headers, and this migration must also reach the TemplateSeries CRM, whose
    layout it does not otherwise care about. Only 'Status' is required;
    'Signal' is located when present so its unrelated "New" can be proven
    untouched.
    """

    def __init__(self, parts, part_name):
        raw = parts[part_name]
        register_namespaces(raw)
        self.parts, self.part_name, self.raw = parts, part_name, raw
        self.root = ET.fromstring(raw)
        self.sst = shared_strings(parts)
        self.data = self.root.find(X + "sheetData")
        if self.data is None:
            raise ValueError("no <sheetData> in %s" % part_name)
        self.rows = {int(r.get("r")): r for r in self.data.findall(X + "row")}
        head = self.rows.get(1)
        if head is None:
            raise ValueError("no header row")
        self.headers = {}
        for c in head.findall(X + "c"):
            txt = cell_text(c, self.sst).strip()
            if txt and txt not in self.headers:
                self.headers[txt] = col_of(c.get("r"))
        if "Status" not in self.headers:
            raise ValueError("no 'Status' header on row 1 — headers: %s"
                             % sorted(self.headers))
        self.status_col = self.headers["Status"]
        self.signal_col = self.headers.get("Signal")

    def status_cells_holding_old(self):
        """Row numbers whose Status cell literally reads "New"."""
        hit = []
        for rownum in sorted(self.rows):
            if rownum == 1:
                continue
            for c in self.rows[rownum].findall(X + "c"):
                if col_of(c.get("r") or "") == self.status_col:
                    if cell_text(c, self.sst).strip() == OLD:
                        hit.append(rownum)
                    break
        return hit

    def plan_validations(self):
        """([(dv_element, old_list, new_list)], [refusal_reason]).

        Only a validation whose sqref covers EXACTLY the Status column is
        edited. One that also spans the Signal column (whose list legitimately
        contains "New") or any other column is refused loudly — editing it
        would rewrite a list this migration has no business touching. The
        Signal column's own validation is never matched at all: the match is
        the header-located column, not the list's contents.
        """
        plans, refusals = [], []
        for dv in self.root.iter(X + "dataValidation"):
            cols = sqref_cols(dv.get("sqref"))
            if self.status_col not in cols:
                continue
            extra = cols - {self.status_col}
            if extra:
                refusals.append(
                    "the Status validation (sqref %r) also covers other "
                    "column(s) — refusing to edit it" % dv.get("sqref"))
                continue
            f1 = dv.find(X + "formula1")
            values = dv_list(f1.text if f1 is not None else None)
            if values is None:
                continue
            new = migrate_list(values)
            if new is not None:
                plans.append((dv, values, new))
        return plans, refusals

    def apply(self, cell_rows, dv_plans):
        """Rewrite the planned cells and lists, re-serialize this ONE part."""
        for rownum in cell_rows:
            set_cell(self.rows[rownum], self.status_col, NEW)
        for dv, _old, new in dv_plans:
            dv.find(X + "formula1").text = '"' + ",".join(new) + '"'
        # Same serialization discipline as sync_crm.Attendees.serialize():
        # re-register this workbook's own prefixes (the ET registry is global)
        # and refuse a root that did not serialize as <worksheet>.
        register_namespaces(self.raw)
        body = ET.tostring(self.root, encoding="UTF-8")
        at = body.find(b"<worksheet")
        if at < 0:
            raise ValueError("%s: serialized root is not <worksheet> (got %r) "
                             "— refusing to write" % (self.part_name, body[:80]))
        self.parts[self.part_name] = _XML_DECL + body[at:]


# ----------------------------------------------------------------------------
# Local-output safety
# ----------------------------------------------------------------------------
def assert_git_safe(path):
    """Refuse a backup/work path git would ever pick up (this repo is public).

    mkdtemp lands outside any checkout, so the common case is the cheap one; a
    path inside a git work tree must be ignored there. Inlined (like
    aaif-backup's copy) rather than imported from lib/, so this skill keeps
    working without the library checkout.
    """
    probe = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        return  # not inside any git work tree — safe
    chk = subprocess.run(["git", "-C", path, "check-ignore", "-q", path],
                         capture_output=True)
    if chk.returncode != 0:
        sys.exit("ABORT: %s is inside the git work tree %s and not gitignored "
                 "— refusing to write member data there."
                 % (path, probe.stdout.strip()))


# ----------------------------------------------------------------------------
# Phase A — the intake role tabs
# ----------------------------------------------------------------------------
def intake_grid_rule(tab):
    """(sheetId, rule, start_row, end_row) for the Status dropdown on `tab`.

    `rule` is the full dataValidation of the first covered cell; rows are
    0-based [start_row, end_row). (None, None, 0, 0) when column A carries no
    ONE_OF_LIST containing "New" — already migrated, or never had one.
    """
    res = gws_json("sheets", "spreadsheets", "get", params={
        "spreadsheetId": INTAKE_ID,
        "ranges": ["'%s'!A1:A%d" % (tab, PROBE_ROWS)],
        "fields": "sheets(properties(sheetId,title),"
                  "data(startRow,rowData(values(dataValidation))))"})
    sheet = res["sheets"][0]
    gid = sheet["properties"]["sheetId"]
    data = (sheet.get("data") or [{}])[0]
    start = data.get("startRow", 0)
    covered, rule = [], None
    for i, rd in enumerate(data.get("rowData") or []):
        vals = rd.get("values") or [{}]
        dv = vals[0].get("dataValidation") or {}
        cond = dv.get("condition") or {}
        if cond.get("type") != "ONE_OF_LIST":
            continue
        listed = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
        if OLD in listed:
            covered.append(start + i)
            rule = rule or dv
    if not covered:
        return gid, None, 0, 0
    return gid, rule, covered[0], covered[-1] + 1


def intake_cf_plans(tab):
    """(sheetId, [(index, old_formula, new_rule)], [refusals]) for one tab.

    Rules are addressed by their INDEX within the tab — what
    updateConditionalFormatRule takes — and each is re-sent WHOLE (ranges,
    colors, the untouched parts of the formula). A partial rule would drop the
    format it paints: gws drops empty request objects, and the API replaces the
    rule outright rather than merging it.
    """
    res = gws_json("sheets", "spreadsheets", "get", params={
        "spreadsheetId": INTAKE_ID,
        "fields": "sheets(properties(sheetId,title),conditionalFormats)"})
    sheet = next((sh for sh in res["sheets"]
                  if sh["properties"]["title"] == tab), None)
    if sheet is None:
        return None, [], ["no %r tab on the intake" % tab]
    plans, refusals = [], []
    for i, cf in enumerate(sheet.get("conditionalFormats") or []):
        cond = (cf.get("booleanRule") or {}).get("condition") or {}
        if cond.get("type") != "CUSTOM_FORMULA":
            continue
        vals = cond.get("values") or []
        old = vals[0].get("userEnteredValue", "") if vals else ""
        new = migrate_cf_formula(old)
        # Judged on what the rule WILL say: one holding both the exact token
        # and an unrecognised shape is migrated AND reported, never quietly
        # half-done.
        why = cf_refusal(new if new is not None else old)
        if why:
            refusals.append("conditional-format rule %d %s" % (i, why))
        if new is None:
            continue
        rule = copy.deepcopy(cf)
        rule["booleanRule"]["condition"]["values"][0]["userEnteredValue"] = new
        plans.append((i, old, rule))
    return sheet["properties"]["sheetId"], plans, refusals


def apply_intake_cf(gid, plans):
    """Replace each planned rule in place, in ONE batch (indices are positional,
    and a rule replaced in place never shifts the ones after it)."""
    gws_json("sheets", "spreadsheets", "batchUpdate",
             params={"spreadsheetId": INTAKE_ID},
             body={"requests": [{"updateConditionalFormatRule": {
                 "sheetId": gid, "index": i, "rule": rule}}
                 for i, _old, rule in plans]})


def intake_howto_plans():
    """plan_howto over the live tab. Z200 covers it with room to grow."""
    return plan_howto(get_values(INTAKE_ID, "'%s'!A1:Z200" % HOWTO_TAB))


def apply_howto(plans):
    """RAW writes to the located cells. The affected cells carry no rich-text
    runs, so a value write keeps their formatting intact."""
    gws_json("sheets", "spreadsheets", "values", "batchUpdate",
             params={"spreadsheetId": INTAKE_ID},
             body={"valueInputOption": "RAW",
                   "data": [{"range": "'%s'!%s" % (HOWTO_TAB, a1),
                             "values": [[new]]}
                            for a1, _old, new in plans]})


def plan_intake_tab(tab):
    """One tab's plan: {tab, guard, cell_rows, gid, rule, r0, r1, new_list,
    cf_gid, cf_plans, cf_refusals}."""
    col_a = [r[0] if r else "" for r in
             get_values(INTAKE_ID, "'%s'!A1:A" % tab)]
    headers = get_values(INTAKE_ID, "'%s'!1:1" % tab)
    guard = intake_status_guard(headers[0] if headers else [])
    plan = {"tab": tab, "guard": guard, "cell_rows": [], "gid": None,
            "rule": None, "r0": 0, "r1": 0, "new_list": None,
            "cf_gid": None, "cf_plans": [], "cf_refusals": []}
    if guard:
        return plan
    plan["cf_gid"], plan["cf_plans"], plan["cf_refusals"] = intake_cf_plans(tab)
    plan["cell_rows"] = plan_intake_cells(col_a)
    gid, rule, r0, r1 = intake_grid_rule(tab)
    plan["gid"] = gid
    if rule is not None:
        listed = [v.get("userEnteredValue", "")
                  for v in rule["condition"].get("values", [])]
        plan.update(rule=rule, r0=r0, r1=r1, new_list=migrate_list(listed))
    return plan


def apply_intake_tab(plan):
    """Apply one tab's plan: the dropdown rule, the color rules, then the cell
    rewrites."""
    tab = plan["tab"]
    if plan["new_list"]:
        # An EXPLICIT full rule over the rule's own observed row span. Never a
        # partial/empty rule: gws drops empty request objects, so anything less
        # than the complete replacement rule can silently no-op.
        rule = dict(plan["rule"])
        rule["condition"] = {"type": "ONE_OF_LIST",
                             "values": [{"userEnteredValue": v}
                                        for v in plan["new_list"]]}
        rule.setdefault("showCustomUi", True)
        gws_json("sheets", "spreadsheets", "batchUpdate",
                 params={"spreadsheetId": INTAKE_ID},
                 body={"requests": [{"setDataValidation": {
                     "range": {"sheetId": plan["gid"],
                               "startRowIndex": plan["r0"],
                               "endRowIndex": plan["r1"],
                               "startColumnIndex": 0, "endColumnIndex": 1},
                     "rule": rule}}]})
    if plan["cf_plans"]:
        apply_intake_cf(plan["cf_gid"], plan["cf_plans"])
    if plan["cell_rows"]:
        data = [{"range": "'%s'!A%d:A%d" % (tab, a, b),
                 "values": [[NEW]] * (b - a + 1)}
                for a, b in runs_of(plan["cell_rows"])]
        gws_json("sheets", "spreadsheets", "values", "batchUpdate",
                 params={"spreadsheetId": INTAKE_ID},
                 body={"valueInputOption": "RAW", "data": data})


def verify_intake_tab(tab):
    """Re-read; return a failure string or None."""
    col_a = [r[0] if r else "" for r in
             get_values(INTAKE_ID, "'%s'!A1:A" % tab)]
    left = plan_intake_cells(col_a)
    if left:
        return "%s: %d Status cell(s) still read %r" % (tab, len(left), OLD)
    _gid, rule, _r0, _r1 = intake_grid_rule(tab)
    if rule is not None:
        return "%s: the Status dropdown still offers %r" % (tab, OLD)
    _cfgid, cf_plans, cf_refusals = intake_cf_plans(tab)
    if cf_plans or cf_refusals:
        return ("%s: %d conditional-format rule(s) still test %r"
                % (tab, len(cf_plans) + len(cf_refusals), OLD))
    return None


# ----------------------------------------------------------------------------
# Phase B — the chapter CRMs
# ----------------------------------------------------------------------------
def crm_folders(city=None):
    """Chapter folders plus the two template folders, deduped by id."""
    folders = list(list_chapter_folders())
    seen = {f["id"] for f in folders}
    folders += [t for t in TEMPLATE_FOLDERS if t["id"] not in seen]
    if city:
        want = fold_city(city)
        folders = [f for f in folders if fold_city(f["name"]) == want]
        if not folders:
            sys.exit("ABORT: no chapter folder matches %r." % city)
    return sorted(folders, key=lambda f: f["name"])


def open_crm_sheet(folder, workdir):
    """((crm_meta, names, parts, sheet, path), None) or (None, why)."""
    crm, why = find_crm(folder["id"])
    if crm is None:
        return None, why
    path = os.path.join(workdir, "%s.xlsx"
                        % "".join(ch if ch.isalnum() or ch in "._-" else "_"
                                  for ch in folder["name"]))
    try:
        names, parts = load_parts(download(crm["id"], path))
        part = sheet_part(parts, CRM_SHEET)
        if part is None:
            return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
        sheet = CrmStatusSheet(parts, part)
    except Exception as e:   # BadZipFile / KeyError / ParseError / ValueError
        return None, "%s: %s: %s" % (crm["name"], type(e).__name__, e)
    return (crm, names, parts, sheet, path), None


def plan_crm(sheet):
    """({cells, dvs}, [refusals]) for one opened workbook."""
    dvs, refusals = sheet.plan_validations()
    return {"cells": sheet.status_cells_holding_old(), "dvs": dvs}, refusals


def write_crm(folder, book, plan, workdir, backup_dir):
    """Apply + upload one workbook. Returns a failure string or None."""
    crm, names, parts, sheet, path = book
    with open(path, "rb") as fh:
        planned = fh.read()
    fresh, drifted = fresh_if_unchanged(
        crm["id"], os.path.join(workdir, "reread.xlsx"), planned)
    with open(os.path.join(backup_dir, os.path.basename(path)), "wb") as fh:
        fh.write(fresh)
    if drifted:
        return ("%s changed since the plan was built — NOT written, re-run"
                % crm["name"])
    sheet.apply(plan["cells"], plan["dvs"])
    upload(crm["id"], path, save_parts(names, parts), XLSX)
    # Verify: a fresh download must propose nothing.
    book2, why = open_crm_sheet(folder, os.path.join(workdir, "verify"))
    if book2 is None:
        return "%s: could not re-open after write: %s" % (crm["name"], why)
    plan2, refusals2 = plan_crm(book2[3])
    if plan2["cells"] or plan2["dvs"] or refusals2:
        return ("%s: %d cell(s) / %d validation(s) still pending after write"
                % (crm["name"], len(plan2["cells"]), len(plan2["dvs"])))
    return None


# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------
def run(args):
    workdir = tempfile.mkdtemp(prefix="aaif-status-migrate-")
    assert_git_safe(workdir)
    try:
        return _run(args, workdir)
    finally:
        if not args.write:   # --write keeps its before/ backups for recovery
            shutil.rmtree(workdir, ignore_errors=True)


def _run(args, workdir):
    proposed, failures = 0, []

    print("Phase A — Intake Ops role tabs (%s):" % ", ".join(ROLE_TABS))
    tab_plans = []
    for tab in ROLE_TABS:
        plan = plan_intake_tab(tab)
        tab_plans.append(plan)
        if plan["guard"]:
            failures.append("%s: %s" % (tab, plan["guard"]))
            print("  %-10s REFUSED — %s" % (tab, plan["guard"]))
            continue
        for r in plan["cf_refusals"]:
            failures.append("%s: %s" % (tab, r))
            print("  %-10s REFUSED — %s" % (tab, r))
        bits = []
        if plan["cell_rows"]:
            bits.append("%d Status cell(s) %r -> %r (column A only)"
                        % (len(plan["cell_rows"]), OLD, NEW))
        if plan["new_list"]:
            bits.append("dropdown list %r -> %r" % (OLD, NEW))
        if plan["cf_plans"]:
            bits.append("%d conditional-format rule(s) %s -> %s"
                        % (len(plan["cf_plans"]), CF_TOKEN_OLD, CF_TOKEN_NEW))
        print("  %-10s %s" % (tab, "; ".join(bits) or "in sync"))
        proposed += (len(plan["cell_rows"]) + (1 if plan["new_list"] else 0)
                     + len(plan["cf_plans"]))

    howto_plans, howto_refusals = intake_howto_plans()
    for r in howto_refusals:
        failures.append("%s: %s" % (HOWTO_TAB, r))
        print("  %-10s REFUSED — %s" % (HOWTO_TAB, r))
    print("  %-10s %s" % (HOWTO_TAB,
                          ("%d status sentence(s) %r -> %r"
                           % (len(howto_plans), OLD, NEW))
                          if howto_plans else "in sync"))
    proposed += len(howto_plans)

    print("\nPhase B — chapter CRMs:")
    backup_dir = os.path.join(workdir, "before")
    os.makedirs(backup_dir, exist_ok=True)
    crm_plans = []
    for folder in crm_folders(args.city):
        book, why = open_crm_sheet(folder, workdir)
        if book is None:
            failures.append("%s: %s" % (folder["name"], why))
            print("  %-20s SKIPPED — %s" % (folder["name"], why))
            continue
        plan, refusals = plan_crm(book[3])
        for r in refusals:
            failures.append("%s: %s" % (book[0]["name"], r))
            print("  %-20s REFUSED — %s" % (folder["name"], r))
        n_cells, n_dvs = len(plan["cells"]), len(plan["dvs"])
        if not n_cells and not n_dvs:
            print("  %-20s in sync" % folder["name"])
            continue
        bits = ([("%d Status cell(s) %r -> %r" % (n_cells, OLD, NEW))]
                if n_cells else []) \
            + ([('Status dropdown -%r' % OLD)] if n_dvs else [])
        print("  %-20s %s (%s)" % (folder["name"], ", ".join(bits),
                                   book[0]["name"]))
        proposed += n_cells + n_dvs
        crm_plans.append((folder, book, plan))

    if not proposed:
        if failures:
            print("\n%d tab(s)/workbook(s) could not be planned:" % len(failures))
            for f in failures:
                print("  %s" % f)
            return 1
        print("\nNothing to do — %r is gone from every dropdown, cell, color "
              "rule and How-to-use sentence." % OLD)
        return 0

    if not args.write:
        print("\n%d change(s) proposed across %d tab(s) and %d workbook(s). "
              "Re-run with --write to apply."
              % (proposed, sum(1 for p in tab_plans
                               if p["cell_rows"] or p["new_list"]
                               or p["cf_plans"]) + (1 if howto_plans else 0),
                 len(crm_plans)))
        return 1 if failures else 2

    print("\nApplying...")
    for plan in tab_plans:
        if plan["guard"] or not (plan["cell_rows"] or plan["new_list"]
                                 or plan["cf_plans"]):
            continue
        try:
            apply_intake_tab(plan)
            print("  wrote %s" % plan["tab"])
        except Exception as e:
            failures.append("%s: %s" % (plan["tab"], e))
            print("  FAILED %s — %s" % (plan["tab"], e), file=sys.stderr)
    if howto_plans:
        try:
            apply_howto(howto_plans)
            print("  wrote %s (%d sentence(s))" % (HOWTO_TAB, len(howto_plans)))
        except Exception as e:
            failures.append("%s: %s" % (HOWTO_TAB, e))
            print("  FAILED %s — %s" % (HOWTO_TAB, e), file=sys.stderr)
    for folder, book, plan in crm_plans:
        try:
            why = write_crm(folder, book, plan, workdir, backup_dir)
        except Exception as e:   # one bad workbook must not abandon the rest
            why = "%s: %s" % (type(e).__name__, e)
        if why:
            failures.append(why)
            print("  FAILED %s — %s" % (folder["name"], why), file=sys.stderr)
        else:
            print("  wrote %s" % folder["name"])
    print("Pre-edit workbook copies kept in %s" % backup_dir)

    print("\nRe-verifying the intake tabs...")
    for plan in tab_plans:
        if plan["guard"] or not (plan["cell_rows"] or plan["new_list"]
                                 or plan["cf_plans"]):
            continue
        bad = verify_intake_tab(plan["tab"])
        if bad:
            failures.append(bad)
    if howto_plans:
        left, still_refused = intake_howto_plans()
        if left or still_refused:
            failures.append("%s: %d status sentence(s) not migrated"
                            % (HOWTO_TAB, len(left) + len(still_refused)))

    if failures:
        print("VERIFY FAILED / INCOMPLETE:")
        for f in failures:
            print("  %s" % f)
        return 1
    print("Verified: fresh reads of every written tab and workbook propose "
          "zero changes.")
    return 2   # changes were proposed AND applied — the shared 0/2/1 contract


def main():
    ap = argparse.ArgumentParser(
        description='Retire the legacy intake status "New" in favor of "Prospect".')
    ap.add_argument("--write", action="store_true",
                    help="apply the proposed changes (default: report only)")
    ap.add_argument("--city", help="limit Phase B to one chapter folder")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
