#!/usr/bin/env python3
"""Tests for the per-role CRM tab migration. No Drive, no network.

This migration adds SHEETS to ~100 live workbooks holding real people, which is
the highest-risk shape of change in this repo. So the tests are about the two
ways that goes wrong and nothing else:

  * **The package stops being a valid xlsx.** A sheet whose part is not
    declared in [Content_Types].xml, or whose rId collides, or whose sheetId
    repeats, opens as "we found a problem with some content" — and the operator
    finds out after 100 uploads. Every registration is asserted here.
  * **A human's work is destroyed.** `Attendees` carries `Signal`, hand-typed
    notes and corrected spellings that no automation may author. The migration
    is additive and the source tab must come through byte-identical.
"""
import os
import sys
from xml.etree import ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

import migrate_role_tabs as mig  # noqa: E402
import sync_crm as crm  # noqa: E402

# The fixture is built here rather than imported from `test_sync_crm`.
# Importing that module RUNS it — these are plain scripts, not a pytest
# package — and its self-test writes real backup directories under the repo.
# A test that leaves state behind is the thing this migration must not do.
SAMPLE = ["Ravi Menon", "High", "Yes", "Regular",
          "Brought three friends to the Feb event.", "ravi@example.com", "",
          "Vendor Inc.", "Sales", "—", "Community regular", ""]


def _c(ref, text, style):
    if text is None:
        return '<c r="%s" s="%s"/>' % (ref, style)
    return ('<c r="%s" s="%s" t="inlineStr"><is><t>%s</t></is></c>'
            % (ref, style, text))


def make_xlsx(sample_row=None, blank_rows=8, headers=crm.CRM_HEADERS):
    """A two-sheet workbook whose `Attendees` tab mirrors the shipped template."""
    head = "".join(_c(crm.cell_ref(i, 1), h, "2") for i, h in enumerate(headers))
    rows = ['<row r="1" ht="30" customHeight="1" s="20">%s</row>' % head]
    if sample_row:
        cells = "".join(_c(crm.cell_ref(i, 2), v, "3")
                        for i, v in enumerate(sample_row) if v)
        rows.append('<row r="2">%s</row>' % cells)
    for r in range(3, 3 + blank_rows):
        rows.append('<row r="%d">%s</row>'
                    % (r, "".join(_c(crm.cell_ref(i, r), None, "5")
                                  for i in range(len(headers)))))
    status_col = crm.cell_ref(list(headers).index("Status"), 1)[0] \
        if "Status" in headers else "D"
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:L%d" /><sheetData>%s</sheetData>'
        '<dataValidations count="1">'
        '<dataValidation sqref="%s2:%s1000" type="list">'
        '<formula1>"Prospect,Accepted"</formula1></dataValidation>'
        '</dataValidations></worksheet>'
        % (2 + blank_rows, "".join(rows), status_col, status_col))
    parts = {
        "[Content_Types].xml": "<Types />",
        "_rels/.rels": "<Relationships />",
        "xl/workbook.xml":
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Guide" sheetId="2" r:id="rId9" />'
            '<sheet name="Attendees" sheetId="1" r:id="rId7" /></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Target="worksheets/sheet2.xml" />'
            '<Relationship Id="rId7" Target="worksheets/sheet1.xml" /></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
        "xl/worksheets/sheet2.xml": "<worksheet />",
    }
    names = list(parts)
    return names, {n: v.encode() for n, v in parts.items()}

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def fixture(**kw):
    names, parts = make_xlsx(sample_row=SAMPLE, **kw)
    return names, dict(parts)


def tabs(parts):
    return mig.existing_tabs(parts)


# --- the plan ----------------------------------------------------------------
_names, parts = fixture()
check("a one-tab workbook is planned for all three role tabs",
      mig.plan_workbook(parts), (list(mig.ROLE_TABS), None))

# --- applying it --------------------------------------------------------------
_names, parts = fixture()
before_attendees = parts[crm.sheet_part(parts, "Attendees")]
added = mig.migrate_workbook(parts)
check("all three tabs are added", added, list(mig.ROLE_TABS))
check("...and the workbook now lists them",
      [t for t in tabs(parts) if t in mig.ROLE_TABS], list(mig.ROLE_TABS))
check("the Guide tab is left alone", "Guide" in tabs(parts), True)

# The whole safety argument: Attendees is additive-only.
check("the SOURCE tab comes through byte-identical",
      parts[crm.sheet_part(parts, "Attendees")], before_attendees)

# --- the package stays valid ---------------------------------------------------
wb = ET.fromstring(parts["xl/workbook.xml"])
sheet_ids = [s.get("sheetId") for s in wb.iter(crm.X + "sheet")]
check("every sheetId is unique", len(sheet_ids), len(set(sheet_ids)))
rids = [s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        for s in wb.iter(crm.X + "sheet")]
check("every sheet carries an r:id", all(rids), True)
check("...and no two sheets share one", len(rids), len(set(rids)))

rels = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
rel_ids = [r.get("Id") for r in rels]
check("every rel Id is unique", len(rel_ids), len(set(rel_ids)))
check("every sheet's r:id resolves to a relationship",
      sorted(set(rids) - set(rel_ids)), [])
targets = {r.get("Id"): r.get("Target") for r in rels}
for name in mig.ROLE_TABS:
    part = crm.sheet_part(parts, name)
    check("%s resolves to a zip part that exists" % name, part in parts, True)
check("no two role tabs share a zip part",
      len({crm.sheet_part(parts, n) for n in mig.ROLE_TABS}), 3)
check("...and none of them points at the Attendees part",
      crm.sheet_part(parts, "Attendees")
      in {crm.sheet_part(parts, n) for n in mig.ROLE_TABS}, False)
check("every new rel target names a part in the package",
      sorted(t for i, t in targets.items()
             if i in rids and ("xl/" + t) not in parts and t not in parts), [])

# --- the clone is a usable CRM sheet ------------------------------------------
for name in mig.ROLE_TABS:
    part = crm.sheet_part(parts, name)
    att = crm.Attendees(parts, part)      # raises if the headers are wrong
    check("%s carries the full CRM header row" % name,
          sorted(att.headers) == sorted(crm.CRM_HEADERS), True)
    check("%s has no occupied rows — the clone was cleared" % name,
          [r for r in att.rows if r != 1 and att.occupied(r)], [])
    check("%s keeps its blank styled rows to write into" % name,
          next(iter(att.free_rows()), None) is not None, True)
    # Dropdowns come from the clone, which is the reason to clone at all.
    # dv_lists keys on the 0-based COLUMN INDEX, not the header name.
    _status_col = list(crm.CRM_HEADERS).index("Status")
    check("%s inherits the Status dropdown" % name,
          crm.dv_lists(parts[part]).get(_status_col) is not None, True)

# A cleared clone must not carry the sample person across into three new tabs.
for name in mig.ROLE_TABS:
    raw = parts[crm.sheet_part(parts, name)]
    check("%s does not carry the source tab's person" % name,
          SAMPLE[0].encode() in raw, False)

# --- idempotence ---------------------------------------------------------------
check("a migrated workbook plans nothing further",
      mig.plan_workbook(parts), ([], None))
check("...and re-running adds nothing", mig.migrate_workbook(parts), [])
tabs_after = tabs(parts)
mig.migrate_workbook(parts)
check("...twice", tabs(parts), tabs_after)

# --- refusals ------------------------------------------------------------------
_n, p_noattend = fixture()
wb2 = p_noattend["xl/workbook.xml"].replace(b'name="Attendees"', b'name="Roster"')
p_noattend["xl/workbook.xml"] = wb2
_missing, why = mig.plan_workbook(p_noattend)
check("a workbook with no Attendees tab is skipped with a reason",
      bool(why) and "Attendees" in why, True)
check("...and nothing is added to it", mig.migrate_workbook(p_noattend), [])

# A pre-split workbook (no `Interested in`) must be refused, not cloned: the
# clone would propagate the old schema into three new tabs.
_n, p_old = fixture(headers=tuple(h for h in crm.CRM_HEADERS if h != "Interested in"))
_missing, why = mig.plan_workbook(p_old)
check("a pre-split workbook is refused rather than cloned", bool(why), True)
check("...and the reason names the sheet, so the operator knows where to look",
      "Attendees" in (why or ""), True)

# Only the "not a CRM sheet" refusal (a ValueError from Attendees) is a skip; an
# internal error is a bug and must surface, not read as one more skipped chapter.
from unittest import mock  # noqa: E402
_n, p_bug = fixture()
with mock.patch.object(crm, "Attendees", side_effect=RuntimeError("boom")):
    try:
        mig.plan_workbook(p_bug)
        _raised = False
    except RuntimeError:
        _raised = True
check("an internal error in the CRM reader propagates instead of skipping", _raised, True)


# --- main(): a skipped chapter is not a clean exit -----------------------------
def run_main(argv, open_result):
    """main() with Drive mocked to one folder; returns the exit code."""
    with mock.patch.object(crm, "list_chapter_folders",
                           lambda: [{"name": "Boston", "id": "f1"}]), \
         mock.patch.object(crm, "open_crm", lambda f, w: open_result), \
         mock.patch.object(sys, "argv", ["migrate_role_tabs.py"] + argv):
        return mig.main()


_n, p_done = fixture()
mig.migrate_workbook(p_done)
_book = crm.Book(folder={"name": "Boston", "id": "f1"}, crm={"id": "x"}, names=_n,
                 parts=p_done, part=crm.sheet_part(p_done, "Attendees"), att=None,
                 path="/dev/null")
check("main(): every chapter migrated exits 0", run_main([], (_book, None)), 0)
check("main(): a chapter it could not open exits 2, not 0",
      run_main([], (None, "no CRM in the folder")), 2)

# --- part numbering ------------------------------------------------------------
check("a free sheet part skips the numbers already in use",
      mig.next_free_sheet_part({"xl/worksheets/sheet1.xml": b"",
                                "xl/worksheets/sheet2.xml": b""}),
      "xl/worksheets/sheet3.xml")
check("...and fills a hole rather than always appending",
      mig.next_free_sheet_part({"xl/worksheets/sheet2.xml": b""}),
      "xl/worksheets/sheet1.xml")

# --- content types --------------------------------------------------------------
_n, p_ct = fixture()
p_ct["[Content_Types].xml"] = (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/xl/workbook.xml" ContentType="x"/></Types>').encode()
mig.migrate_workbook(p_ct)
ct = ET.fromstring(p_ct["[Content_Types].xml"])
declared = {o.get("PartName") for o in ct}
for name in mig.ROLE_TABS:
    part = crm.sheet_part(p_ct, name)
    check("%s's part is declared in [Content_Types].xml" % name,
          ("/" + part) in declared, True)

# calcChain indexes formula cells by sheet and goes stale when sheets are added;
# a stale one is what makes Excel "repair" a workbook on open.
_n, p_cc = fixture()
p_cc["xl/calcChain.xml"] = b"<calcChain/>"
mig.migrate_workbook(p_cc)
check("a stale calcChain is dropped so the app rebuilds it",
      "xl/calcChain.xml" in p_cc, False)

# --- the package still round-trips through the zip writer ----------------------
names, parts_rt = fixture()
mig.migrate_workbook(parts_rt)
for name in mig.ROLE_TABS:
    part = crm.sheet_part(parts_rt, name)
    if part not in names:
        names.append(part)
raw = crm.save_parts(names, parts_rt)
names2, parts2 = crm.load_parts(raw)
check("the migrated workbook survives a zip round-trip",
      sorted(t for t in tabs(parts2) if t in mig.ROLE_TABS), sorted(mig.ROLE_TABS))
for name in mig.ROLE_TABS:
    crm.Attendees(parts2, crm.sheet_part(parts2, name))
check("...and every role tab still parses as a CRM sheet afterwards", True, True)

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("migrate_role_tabs: all checks passed")
