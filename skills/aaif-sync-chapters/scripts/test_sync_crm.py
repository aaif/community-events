#!/usr/bin/env python3
"""Unit tests for the pure logic in sync_crm.py (no network/gws).

The .xlsx fixtures are built here rather than checked in as binaries, from the
same shape the real chapter CRMs have — inline strings, a styled sample row 2,
pre-created empty rows, and the Status data-validation list. A checked-in binary
would silently stop resembling the live workbooks; this can't.
"""
import os, sys, tempfile
from unittest import mock
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_chapters
import sync_crm
from sync_crm import (Attendees, CRM_HEADERS, DV_EXPECTED, NEW_COLUMN,
                      SELF_SERVE_MIN, X,
                      apply_ops, cell_ref, check_dropdowns, clean_text, col_of,
                      crm_fields, dv_lists, fold_email, gate_pipeline_organizers,
                      is_aaif_ops, is_auto_role, join_distinct, load_parts,
                      match_chapters, merge_people, plan_workbook,
                      save_parts, sheet_part, valid_email)

TODAY = "2026-08-06"

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


# ---------------------------------------------------------------------------
# Fixture: a workbook shaped like a real chapter CRM
# ---------------------------------------------------------------------------
def _c(ref, text, style=None):
    s = ' s="%s"' % style if style else ""
    if text is None:
        return '<c r="%s"%s t="n" />' % (ref, s)
    return ('<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>' % (ref, s, text))


def make_xlsx(sample_row=None, blank_rows=8, headers=CRM_HEADERS,
              shared=False, dv=None, dv_role=None):
    """A two-sheet workbook whose 'Attendees' tab mirrors the shipped template.

    `dv` / `dv_role` override the Status and `Interested in` dropdown lists;
    both default to what a migrated workbook holds, so a fixture built with no
    arguments is the post-2026-08-25 shape the sync expects to find.
    """
    dv = DV_EXPECTED["Status"] if dv is None else dv
    dv_role = DV_EXPECTED[NEW_COLUMN] if dv_role is None else dv_role
    head = "".join(_c(cell_ref(i, 1), h, "2") for i, h in enumerate(headers))
    rows = ['<row r="1" ht="30" customHeight="1" s="20">%s</row>' % head]
    if sample_row:
        cells = "".join(_c(cell_ref(i, 2), v, "4" if i == 4 else "3")
                        for i, v in enumerate(sample_row) if v)
        rows.append('<row r="2">%s</row>' % cells)
    # Rows 3.. ship with only the columns the template pre-materialises.
    # (One dataValidation is declared below, on Status/column D.)
    for r in range(3, 3 + blank_rows):
        rows.append('<row r="%d">%s</row>'
                    % (r, "".join(_c(cell_ref(i, r), None, "5") for i in (1, 2, 3))))
    # Status sits at D and `Interested in` at L, exactly as the migrated
    # workbooks carry them — the dropdown checker resolves both by column
    # index, so a fixture that put them anywhere else would not exercise it.
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:L%d" /><sheetData>%s</sheetData>'
        '<dataValidations count="2">'
        '<dataValidation sqref="D2:D1000" type="list">'
        '<formula1>"%s"</formula1></dataValidation>'
        '<dataValidation sqref="L2:L1000" type="list">'
        '<formula1>"%s"</formula1></dataValidation>'
        '</dataValidations>'
        '</worksheet>' % (2 + blank_rows, "".join(rows), dv, dv_role))
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
    if shared:
        parts["xl/sharedStrings.xml"] = (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join("<si><t>%s</t></si>" % h for h in headers) + "</sst>")
        # Same header row, stored as shared-string indices — the legacy variant.
        idx = "".join('<c r="%s" s="2" t="s"><v>%d</v></c>' % (cell_ref(i, 1), i)
                      for i in range(len(headers)))
        parts["xl/worksheets/sheet1.xml"] = sheet.replace(head, idx)
    names = list(parts)
    return names, {n: v.encode() for n, v in parts.items()}


# The fixture row a human is presumed to have typed — a REAL-looking address, so
# it must survive every run untouched. Kept distinct from DUMMY below: making the
# general-purpose sample an @example.com address would mean the "never clobber a
# human" tests were quietly running against a row the cleaner deletes.
SAMPLE = ["Ravi Menon", "High", "Yes", "Regular",
          "Brought three friends to the Feb event.", "ravi@vendor.co", "", "Vendor Inc.",
          "Sales", "—", "Community regular", ""]

# The template's shipped fixture row, which every chapter CRM carries.
DUMMY = ["Sam Taylor", "Non-grata", "No", "Declined",
         "Pitched from the floor. Do not invite.", "sam@example.com", "", "Vendor Inc.",
         "Sales", "—", "Wanted to pitch", "Speaker"]


def book(**kw):
    names, parts = make_xlsx(**kw)
    return names, parts, Attendees(parts, sheet_part(parts, "Attendees"))


def person(name, email, city, tab="Organizers", status="Accepted", **kw):
    base = {"row": 7, "tab": tab, "status": status, "name": name, "email": email,
            "city": city, "linkedin": "", "company": "", "title": "",
            "expertise": "", "interest": "I want to be an organizer/volunteer"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Text + key helpers
# ---------------------------------------------------------------------------
check("clean_text collapses newlines", clean_text("a\r\nb\tc"), "a b c")
check("clean_text strips control chars", clean_text("a\x00\x07b"), "ab")
check("clean_text caps length", len(clean_text("x" * 5000)), sync_crm.MAX_CELL_TEXT)
check("fold_email is case-insensitive", fold_email(" A@B.io "), fold_email("a@b.IO"))
check("fold_email keeps dots/plus", fold_email("a.b+x@c.io"), "a.b+x@c.io")
check("valid_email accepts", valid_email("a.b+x@c.co.uk"), True)
check("valid_email rejects blank", valid_email(""), False)
check("valid_email rejects no-tld", valid_email("a@b"), False)
check("valid_email rejects free text", valid_email("ask me on LinkedIn"), False)
check("join_distinct dedupes case-insensitively",
      join_distinct(["Agents", "agents", "MCP", ""]), "Agents · MCP")
check("col_of round-trips", [col_of(cell_ref(i, 3)) for i in (0, 10, 26, 701)],
      [0, 10, 26, 701])


# ---------------------------------------------------------------------------
# merge_people: one row per person per chapter
# ---------------------------------------------------------------------------
both = merge_people([
    person("Ada L", "ada@x.io", "Boston", "Speakers", "Accepted",
           interest="I want to be a speaker", title="Staff Eng", expertise="Agents"),
    person("Ada Lovelace", "ADA@x.io", "boston", "Organizers", "Accepted",
           interest="I want to be an organizer/volunteer", expertise="Agents"),
])
check("merge collapses one person to one row", len(both), 1)
check("merge takes the highest-priority role first", both[0]["tabs"], ["Organizers", "Speakers"])
check("merge keeps every tab's intake status", both[0]["statuses"], ["Accepted", "Accepted"])
check("merge keeps the speaker's title", both[0]["title"], "Staff Eng")
check("merge unions the interests", both[0]["interest"],
      "I want to be an organizer/volunteer · I want to be a speaker")
check("merge dedupes identical expertise", both[0]["expertise"], "Agents")
check("merge keeps different cities apart",
      len(merge_people([person("A", "a@x.io", "Boston"), person("A", "a@x.io", "Berlin")])), 2)

# Security guard: a NOT-yet-accepted row never merges into an all-accepted
# person — the public form + email-keyed merge would otherwise let a stranger
# writing under an accepted organizer's address fill their CRM row.
_blk = []
guarded = merge_people(
    [person("Ada", "ada@x.io", "Boston", "Organizers", "Accepted"),
     person("Mallory", "ada@x.io", "Boston", "Speakers", "New",
            interest="totally a real talk")], blocked=_blk)
check("a pipeline row does not merge into an all-accepted person",
      guarded[0]["tabs"], ["Organizers"])
check("...its content does not reach the person's record",
      "totally a real talk" in guarded[0]["interest"], False)
check("...and the refusal is reported in rejection shape",
      (len(_blk), sorted(_blk[0]), "refusing to merge" in _blk[0]["why"]),
      (1, ["name", "row", "tab", "why"], True))
check("without a blocked list the row is still refused",
      merge_people([person("Ada", "ada@x.io", "B", "Organizers", "Accepted"),
                    person("M", "ada@x.io", "B", "Speakers", "New")])[0]["tabs"],
      ["Organizers"])

# --- the Status / Interested-in split (2026-08-25) --------------------------
# Every check below exists because ONE column used to answer both questions.
# The invariant, stated once: `Interested in` is a fact about the APPLICATION
# and never moves; `Status` is the DECISION and never names a role.
f_acc = crm_fields(both[0], TODAY)
check("accepted organizer -> Status Accepted", f_acc["Status"], "Accepted")
check("...and Interested in carries every role applied for",
      f_acc[NEW_COLUMN], "Organizer/Speaker")
check("accepted organizer -> Trusted", f_acc["Trusted/Regular"], "Yes")
check("note carries every role, statuses deduped",
      f_acc["Notes (CRM)"], "Intake: Organizer/Speaker · Accepted · 2026-08-06")

f_host = crm_fields(merge_people([person("Bo", "bo@x.io", "B", "Hosts", "Accepted")])[0], TODAY)
check("accepted host -> Status Accepted", f_host["Status"], "Accepted")
check("...and Interested in Host", f_host[NEW_COLUMN], "Host")
check("accepted host is not auto-Trusted", f_host["Trusted/Regular"], "")

# Pipeline (not-yet-accepted) people. Status mirrors the intake verbatim.
f_pipe = crm_fields(merge_people([person("Cy", "cy@x.io", "B", "Organizers", "Tentative")])[0], TODAY)
check("pipeline organizer keeps its real intake status", f_pipe["Status"], "Tentative")
check("...and still reads Interested in Organizer", f_pipe[NEW_COLUMN], "Organizer")
check("pipeline organizer is not Trusted", f_pipe["Trusted/Regular"], "")
check("pipeline note carries the intake status",
      f_pipe["Notes (CRM)"], "Intake: Organizer · Tentative · 2026-08-06")

# THE REGRESSION. A speaker/host sitting at Prospect used to be written as a
# flat `Speaker`/`Host` in Status — the CRM announced a settled speaker where
# triage had settled nothing, and only organizers were spared. Both halves are
# asserted: the decision is Prospect AND the application is still visible.
for tab, role in (("Speakers", "Speaker"), ("Hosts", "Host")):
    f_p = crm_fields(merge_people(
        [person("Dee", "dee@x.io", "B", tab, "Prospect")])[0], TODAY)
    check("pipeline %s -> Status Prospect, not %r" % (role.lower(), role),
          f_p["Status"], "Prospect")
    check("...and Interested in %r" % role, f_p[NEW_COLUMN], role)
    check("...and not Trusted", f_p["Trusted/Regular"], "")

check("no role word can ever reach the Status column",
      sorted(set(sync_crm.CRM_LIFECYCLE.values()) & set(sync_crm.CRM_ROLE.values())),
      [])
check("...nor be offered by the Status dropdown",
      [r for r in sync_crm.CRM_ROLE.values() if r in sync_crm.DV_STATUS_VALUES],
      [])

# Every intake status the sync accepts must map to a CRM status, or the first
# person carrying it crashes the run mid-sweep.
check("every syncable intake status has a lifecycle mapping",
      [st for st in sync_crm.SYNC_STATUSES + sync_crm.PIPELINE_STATUSES
       if st not in sync_crm.CRM_LIFECYCLE], [])
check("both accepted-ish statuses land on Accepted",
      sorted({sync_crm.CRM_LIFECYCLE[st] for st in sync_crm.SYNC_STATUSES}),
      ["Accepted"])

# The 2026-08-22 rename transition: an intake row still saying the legacy "New"
# behaves identically to one saying "Prospect".
check("both the legacy and current spellings are pipeline statuses",
      [s for s in ("New", "Prospect") if s in sync_crm.PIPELINE_STATUSES],
      ["New", "Prospect"])
f_leg = crm_fields(merge_people([person("Lee", "lee@x.io", "B", "Organizers", "New")])[0], TODAY)
f_cur = crm_fields(merge_people([person("Lee", "lee@x.io", "B", "Organizers", "Prospect")])[0], TODAY)
check("a legacy-New organizer lands exactly like a Prospect one",
      (f_leg["Status"], f_leg[NEW_COLUMN], f_leg["Trusted/Regular"]),
      (f_cur["Status"], f_cur[NEW_COLUMN], f_cur["Trusted/Regular"]))
check("...and that Status is Prospect", f_cur["Status"], "Prospect")

# Accepted in one role while still in the pipeline for another: acceptance
# anywhere decides the Status, both applications stay visible, and an organizer
# application alone earns no trust.
f_mix = crm_fields(merge_people([
    person("Eve", "eve@x.io", "B", "Organizers", "Interviewing"),
    person("Eve", "eve@x.io", "B", "Speakers", "Accepted")])[0], TODAY)
check("accepted in any role -> Accepted", f_mix["Status"], "Accepted")
check("...and BOTH applications are still named", f_mix[NEW_COLUMN], "Organizer/Speaker")
check("...and does not become Trusted", f_mix["Trusted/Regular"], "")

# The detail columns the intake collects. They were parsed, merged and then
# dropped on the floor for months, leaving the sheet's most useful columns
# blank in every chapter.
f_det = crm_fields(merge_people([person(
    "Ann", "ann@x.io", "B", "Speakers", "Accepted", linkedin="https://li/ann",
    company="Acme", title="Staff Eng", expertise="Agents, MCP")])[0], TODAY)
check("the survey detail reaches the CRM",
      [f_det["LinkedIn URL"], f_det["Company"], f_det["Role / title"],
       f_det["Technical expertise"]],
      ["https://li/ann", "Acme", "Staff Eng", "Agents, MCP"])
check("an unanswered detail question writes nothing",
      crm_fields(merge_people([person("Zed", "z@x.io", "B")])[0], TODAY)["Company"], "")

# is_auto_role: what the sync may rewrite in `Interested in`.
for value, want in (("", True), ("Host", True), ("Organizer/Speaker", True),
                    ("organizer/host", True), ("Organizer / Speaker", True),
                    ("Organizer/Speaker/Host", True),
                    ("Sponsor", False), ("Organizer (co-lead)", False),
                    ("Host — offered the Acme office", False)):
    check("is_auto_role(%r)" % value, is_auto_role(value), want)
check("every value the sync writes is one it may later rewrite",
      [v for v in sync_crm.DV_INTERESTED_VALUES if not is_auto_role(v)], [])

# `Signal` is the chapter's own private judgement of a person and the one
# column no form answer can supply, so it is absent from the mapping rather
# than written blank. Every OTHER column is now filled from the intake.
check("exactly the writable columns are produced",
      sorted(f_acc), sorted(sync_crm.CRM_WRITTEN))
check("'Signal' is never written", "Signal" in f_acc, False)
check("'Signal' is the only CRM column the automation leaves alone",
      [h for h in CRM_HEADERS if h not in sync_crm.CRM_WRITTEN], ["Signal"])


# ---------------------------------------------------------------------------
# The self-serve gate: pipeline organizers need a 4-organizer chapter
# ---------------------------------------------------------------------------
# Synthetic roster throughout this section (CLAUDE.md: no real names in
# tests). is_aaif_ops reads the module global at call time, so patching
# AAIF_OPS_FOLDED exercises the exact production code path; the shipped
# roster's shape is asserted without repeating its contents.
check("the shipped roster folds one entry per name",
      len(sync_crm.AAIF_OPS_FOLDED), len(sync_crm.AAIF_OPS_NAMES))
_saved_ops = sync_crm.AAIF_OPS_FOLDED
sync_crm.AAIF_OPS_FOLDED = frozenset({sync_crm.fold("Ops Person")})
check("is_aaif_ops matches an ops person's exact name", is_aaif_ops("Ops Person"), True)
check("is_aaif_ops folds case", is_aaif_ops("ops PERSON"), True)
check("is_aaif_ops never matches on a name fragment", is_aaif_ops("Ops Person Jr"), False)

def team(city, n):
    return [person("Org %s %d" % (city, i), "org-%s-%d@x.io" % (city.lower(), i),
                   city, "Organizers", "Accepted") for i in range(n)]

cand = person("Nia", "nia@x.io", "Big", "Organizers", "Tentative")
small_cand = person("Kim", "kim@x.io", "Small", "Organizers", "Interviewing")
kept, held = gate_pipeline_organizers(
    team("Big", SELF_SERVE_MIN) + [cand] + team("Small", SELF_SERVE_MIN - 1) + [small_cand])
check("a self-serve chapter keeps its pipeline organizer", cand in kept, True)
check("a small chapter's pipeline organizer is held",
      [p["name"] for p in held], ["Kim"])
check("accepted people always pass the gate",
      len([p for p in kept if sync_crm.is_accepted(p)]), 2 * SELF_SERVE_MIN - 1)

# AAIF ops people never count toward the threshold: 3 locals + Rahul is 4 rows
# but still a centrally-approved chapter.
_, held = gate_pipeline_organizers(
    team("Bern", SELF_SERVE_MIN - 1)
    + [person("Ops Person", "o@x.io", "Bern", "Organizers", "Accepted"),
       dict(small_cand, city="Bern")])
check("an ops person does not make a chapter self-serve",
      [p["name"] for p in held], ["Kim"])
check("a held person carries the reason beside the rule",
      "fewer than %d accepted organizers" % SELF_SERVE_MIN in held[0]["why"], True)

# The count is distinct emails, so a duplicated accepted row cannot tip it.
dup = team("Twin", SELF_SERVE_MIN - 1)
_, held = gate_pipeline_organizers(
    dup + [dict(dup[0], row=99)] + [dict(cand, city="Twin")])
check("a duplicated accepted row does not tip the threshold",
      [p["name"] for p in held], ["Nia"])

# Pipeline hosts/speakers pass regardless of their chapter's size.
pipe_host = person("Vee", "vee@x.io", "Small", "Hosts", "New")
kept, held = gate_pipeline_organizers([pipe_host])
check("a pipeline host is never gated", (kept, held), ([pipe_host], []))
sync_crm.AAIF_OPS_FOLDED = _saved_ops


# ---------------------------------------------------------------------------
# read_role_tab: the status filter that decides who enters the flow at all
# ---------------------------------------------------------------------------
# Mocked grid, no gws. These pin the two contracts nothing else enforces:
# the accepted-only DEFAULT (sync_access turns this roster into Drive grants)
# and the fail-closed rejection of Denied/unknown statuses even in pipeline
# mode.
_GRID = [
    ["Status", "Full name", "Email", "Chapter", "City (Existing)", "City (New)"],
    ["Accepted", "Ada", "ada@x.io", "Boston", "", ""],
    ["Tentative", "Tess", "tess@x.io", "Boston", "", ""],
    ["Denied", "Dan", "dan@x.io", "Boston", "", ""],
    ["Zebra", "Zed", "zed@x.io", "Boston", "", ""],
    ["New", "Newt", "newt@x.io", "Boston", "", ""],       # legacy spelling
    ["Prospect", "Pia", "pia@x.io", "Boston", "", ""],    # current spelling
    ["", "Bea", "bea@x.io", "Boston", "", ""],
]
_saved_gv = sync_crm.get_values
sync_crm.get_values = lambda *_a, **_k: _GRID
try:
    pp, rr, _fb = sync_crm.read_role_tab("Organizers", {})
    check("the DEFAULT is accepted-only — the Drive-grant contract",
          [p["name"] for p in pp], ["Ada"])
    check("everyone else is rejected as not-accepted by default",
          [r["name"] for r in rr if "not accepted yet" in r["why"]],
          ["Tess", "Dan", "Zed", "Newt", "Pia", "Bea"])
    pp, rr, _fb = sync_crm.read_role_tab("Organizers", {}, include_pipeline=True)
    check("pipeline mode admits in-flight statuses — legacy New included",
          sorted(p["name"] for p in pp), ["Ada", "Bea", "Newt", "Pia", "Tess"])
    check("a blank status is normalized to Prospect on the person",
          [p["status"] for p in pp if p["name"] == "Bea"], ["Prospect"])
    check("a raw legacy New survives on the person (the note shows the truth)",
          [p["status"] for p in pp if p["name"] == "Newt"], ["New"])
    check("Denied and unknown statuses fail closed even in pipeline mode",
          sorted(r["name"] for r in rr
                 if "declined, parked, or not a recognised" in r["why"]),
          ["Dan", "Zed"])
finally:
    sync_crm.get_values = _saved_gv

# The injection-safety property, enforced on the CRM path itself: read_role_tab
# does NOT go through sync_chapters.read_intake, so it runs the same
# bad_public_text check on the public-facing fields (name, city) — without it,
# a hostile form submission walked straight into a private workbook (and, via
# sync_access's roster read, toward a Drive grant).
_BAD_GRID = [
    ["Status", "Full name", "Email", "Chapter", "City (Existing)", "City (New)"],
    ["Accepted", "Ada", "ada@x.io", "Boston", "", ""],
    ["Accepted", "<script>x</script>", "evil@x.io", "Boston", "", ""],   # markup name
    ["Accepted", "Cy", "cy@x.io", "Bo\x07ston", "", ""],                 # control-char city
    ["Accepted", "Dee " * 40, "dee@x.io", "Boston", "", ""],             # absurd length
]
sync_crm.get_values = lambda *_a, **_k: _BAD_GRID
try:
    pp, rr, _fb = sync_crm.read_role_tab("Organizers", {})
    check("malformed public text never becomes a CRM person",
          [p["name"] for p in pp], ["Ada"])
    check("each malformed row lands in the skip report with its reason",
          sorted(r["row"] for r in rr
                 if "must never reach the public feed" in r["why"]
                 or "characters (max" in r["why"]),
          [3, 4, 5])
finally:
    sync_crm.get_values = _saved_gv


# ---------------------------------------------------------------------------
# Attendees: parsing both storage forms
# ---------------------------------------------------------------------------
_, _, att = book(sample_row=SAMPLE)
check("headers resolve by name", att.headers["What brings you here?"], 10)
check("reads an inline string", att.value(2, "Email"), "ravi@vendor.co")
check("indexes existing people by email", att.index_by_email(), {"ravi@vendor.co": 2})
check("row 2 styles are captured for new rows", att.sample[4], "4")
check("first free row is 3", next(att.free_rows()), 3)
check("a cleared row becomes free again", next(att.free_rows(also_free={2})), 2)

_, _, shared_att = book(sample_row=None, shared=True)
check("reads the legacy shared-string header row",
      sorted(shared_att.headers) == sorted(CRM_HEADERS), True)

try:
    book(headers=[h for h in CRM_HEADERS if h != "Email"])
    check("aborts on a missing column", "no error", "ValueError")
except ValueError as e:
    check("aborts on a missing column", "Email" in str(e), True)


# ---------------------------------------------------------------------------
# plan_workbook: append, fill blanks, never clobber
# ---------------------------------------------------------------------------
names, parts, att = book(sample_row=SAMPLE)
people = merge_people([person("Ada", "ada@x.io", "Boston", "Organizers", "Accepted",
                              linkedin="https://li/ada", expertise="Agents, MCP")])
ops = plan_workbook(att, people, TODAY)
check("a new person is one op", len(ops), 1)
check("new person lands on the first free row", ops[0]["rownum"], 3)
check("new person is flagged as an add", ops[0]["kind"], "add")
check("the interest field reaches the CRM cell",
      ops[0]["sets"]["What brings you here?"], "I want to be an organizer/volunteer")
check("the survey's detail columns reach the CRM",
      [ops[0]["sets"].get(c) for c in ("Technical expertise", "LinkedIn URL")],
      ["Agents, MCP", "https://li/ada"])
check("...and an unanswered one is not written blank", "Company" in ops[0]["sets"], False)

apply_ops(att, ops)
check("write landed in the sheet", att.value(3, "Email"), "ada@x.io")
check("write reused the row-2 column style",
      att.rows[3].findall(X + "c")[0].get("s"), "3")
check("existing dropdown cells keep their own style",
      [c.get("s") for c in att.rows[3].findall(X + "c") if col_of(c.get("r")) == 1], ["5"])
check("re-planning the same people is a no-op", plan_workbook(att, people, TODAY), [])

# Two new people must not be planned into the same row.
_, _, att2 = book(sample_row=SAMPLE)
two = plan_workbook(att2, merge_people([person("A", "a@x.io", "B"),
                                        person("C", "c@x.io", "B")]), TODAY)
check("two new people get two rows",
      sorted(o["rownum"] for o in two if o["kind"] == "add"), [3, 4])

# A human's edits survive; only genuinely blank cells are filled.
_, _, att3 = book(sample_row=SAMPLE)
att3.write(3, "Email", "ada@x.io")
att3.write(3, "Full name", "Ada (call her Addy)")
att3.write(3, "Notes (CRM)", "Met at the Feb event; will co-host.")
ops3 = plan_workbook(att3, people, TODAY)
check("existing person is an update, not an append", ops3[0]["kind"], "fill")
check("the human's name spelling is kept", "Full name" in ops3[0]["sets"], False)
check("the human's note is kept", "Notes (CRM)" in ops3[0]["sets"], False)
check("blank cells are still filled", ops3[0]["sets"]["What brings you here?"],
      "I want to be an organizer/volunteer")


# ---------------------------------------------------------------------------
# Clearing fixture rows — ONLY the reserved example domains
# ---------------------------------------------------------------------------
check("is_dummy catches the shipped sample", sync_crm.is_dummy("sam@example.com"), True)
check("is_dummy is case-insensitive", sync_crm.is_dummy("Sam@Example.COM"), True)
# The suffix match is anchored on "@" so look-alike domains are never caught.
for real in ("ravi@vendor.co", "a@vendor.studio", "a@examples.com",
             "a@notexample.com", "x@example.company", ""):
    check("is_dummy leaves %r alone" % real, sync_crm.is_dummy(real), False)

_, _, attd = book(sample_row=DUMMY)
clr = plan_workbook(attd, [], TODAY)
check("a chapter with no people still clears its dummy row",
      [(o["kind"], o["rownum"], o["name"]) for o in clr], [("clear", 2, "Sam Taylor")])
apply_ops(attd, clr)
check("the dummy row is blank afterwards",
      [attd.value(2, h) for h in CRM_HEADERS], [""] * len(CRM_HEADERS))
check("clearing is idempotent", plan_workbook(attd, [], TODAY), [])

# The freed row is reused, so a chapter's first real organizer lands at the top.
_, _, attr = book(sample_row=DUMMY)
mixed = plan_workbook(attr, people, TODAY)
check("clear is planned before the add", [o["kind"] for o in mixed], ["clear", "add"])
check("the real person reuses the dummy's row", mixed[1]["rownum"], 2)
apply_ops(attr, mixed)
check("the reused row holds the real person", attr.value(2, "Email"), "ada@x.io")
check("no stale dummy text survives the reuse", attr.value(2, "Notes (CRM)"),
      "Intake: Organizer · Accepted · 2026-08-06")
check("a wiped-then-reused row keeps nothing from the dummy",
      attr.value(2, "Full name"), "Ada")

# A real-looking row is never cleared, and is reported instead.
_, _, attk = book(sample_row=SAMPLE)
check("a real-looking row is not cleared",
      [o for o in plan_workbook(attk, [], TODAY) if o["kind"] == "clear"], [])
check("a real-looking row is reported as pre-existing",
      sync_crm.preexisting(attk, []), [{"row": 2, "name": "Ravi Menon",
                                        "email": "ravi@vendor.co"}])
check("a row this run touches is not reported as pre-existing",
      sync_crm.preexisting(attk, [{"rownum": 2}]), [])

# An intake person really can have an example-domain address — valid_email
# accepts it. Clearing their row as fixture data while also re-adding them made
# the plan non-empty forever, so --write's re-verify could never converge and
# the chapter reported "ops still pending" permanently.
_, _, attc = book(sample_row=None)
churn = merge_people([person("Sam Taylor", "sam@example.com", "Boston")])
attc.write(2, "Email", "sam@example.com")
attc.write(2, "Full name", "Sam Taylor")
first = plan_workbook(attc, churn, TODAY)
check("an intake person is not cleared as fixture data",
      [o["kind"] for o in first], ["fill"])
apply_ops(attc, first)
check("...and the plan converges on the second run",
      plan_workbook(attc, churn, TODAY), [])

# A fixture row NOT owned by anyone in the plan is still cleared.
_, _, attu = book(sample_row=DUMMY)
check("an unowned fixture row is still cleared",
      [o["kind"] for o in plan_workbook(attu, [], TODAY)], ["clear"])

# A settled CRM must stay settled when the date rolls over — Notes (CRM) embeds
# `today`, so a loosened leave-populated-alone rule would rewrite every note in
# every workbook, every day, and never converge.
_, _, attd2 = book(sample_row=SAMPLE)
apply_ops(attd2, plan_workbook(attd2, people, TODAY))
check("a later date does not re-plan a settled CRM",
      plan_workbook(attd2, people, "2027-01-01"), [])
# On a settled CRM there are no ops, so without the `people` filter every row the
# sync itself wrote would be reported back as an unrecognised address to review.
_, _, atts = book(sample_row=SAMPLE)
atts.write(3, "Email", "ada@x.io")
atts.write(3, "Full name", "Ada")
check("a person the intake expects is not flagged as unrecognised",
      sync_crm.preexisting(atts, [], people), [{"row": 2, "name": "Ravi Menon",
                                                "email": "ravi@vendor.co"}])
check("the expected-person filter is email-based, not row-based",
      [r["row"] for r in sync_crm.preexisting(atts, [], [])], [2, 3])

# Status: upgrade what the automation wrote, never what a human wrote.
# The three ROLE values are the pre-split leftovers sitting in ~62 workbooks;
# they must upgrade like any other automation value, or the migration is the
# only thing that could ever repair them.
for before, want in (("", "Accepted"), ("New", "Accepted"), ("Prospect", "Accepted"),
                     ("In progress", "Accepted"), ("Interviewing", "Accepted"),
                     ("Tentative", "Accepted"),
                     ("Speaker", "Accepted"), ("Host", "Accepted"),
                     ("Organizer", "Accepted"),
                     ("Accepted", None),
                     ("Attended", None), ("Declined", None), ("Regular", None),
                     ("Volunteer", None)):
    _, _, a = book(sample_row=SAMPLE)
    a.write(3, "Email", "ada@x.io")
    if before:
        a.write(3, "Status", before)
    ops_s = plan_workbook(a, people, TODAY)
    # Assert an op exists at all: for the protected values the expected result
    # is None, which an EMPTY ops list also yields — so those checks would pass
    # vacuously if planning stopped emitting ops entirely.
    check("Status %-12r -> op kinds" % before, [o["kind"] for o in ops_s], ["fill"])
    got = ops_s[0]["sets"].get("Status") if ops_s else None
    check("Status %-12r -> %r" % (before, want), got, want)

# The same rule on the new column, and the reason it needs its own predicate:
# "Organizer/Speaker" is not a member of any value set, so a frozenset test
# would freeze every multi-role person at whatever was written first.
for before, want in (("", "Organizer"), ("Speaker", "Organizer"),
                     ("Speaker/Host", "Organizer"),
                     ("Organizer", None),
                     ("Sponsor", None), ("Organizer (co-lead)", None)):
    _, _, a = book(sample_row=SAMPLE)
    a.write(3, "Email", "ada@x.io")
    if before:
        a.write(3, NEW_COLUMN, before)
    ops_r = plan_workbook(a, people, TODAY)
    check("%s %-22r -> op kinds" % (NEW_COLUMN, before),
          [o["kind"] for o in ops_r], ["fill"])
    check("%s %-22r -> %r" % (NEW_COLUMN, before, want),
          ops_r[0]["sets"].get(NEW_COLUMN) if ops_r else None, want)

# The self-serve lifecycle end-to-end: a Prospect synced while in the pipeline
# is upgraded in place once the chapter accepts them — Status is in AUTO_STATUS
# and the blank Trusted/Regular cell fills.
_, _, alc = book(sample_row=SAMPLE)
pipe = merge_people([person("Ada", "ada@x.io", "Boston", "Organizers", "Tentative",
                            linkedin="https://li/ada", expertise="Agents, MCP")])
apply_ops(alc, plan_workbook(alc, pipe, TODAY))
check("a prospect lands with its real intake status, untrusted",
      [alc.value(3, "Status"), alc.value(3, NEW_COLUMN),
       alc.value(3, "Trusted/Regular")],
      ["Tentative", "Organizer", ""])
up = plan_workbook(alc, people, TODAY)
check("acceptance upgrades the prospect in place",
      (up[0]["kind"], up[0]["sets"].get("Status"), up[0]["sets"].get("Trusted/Regular")),
      ("fill", "Accepted", "Yes"))
check("...and does not restate the unchanged Interested in",
      NEW_COLUMN in up[0]["sets"], False)


# ---------------------------------------------------------------------------
# Round-trip: the edited workbook is still a readable .xlsx
# ---------------------------------------------------------------------------
names, parts, att = book(sample_row=SAMPLE)
apply_ops(att, plan_workbook(att, people, TODAY))
att.serialize()
raw = save_parts(names, parts)
rt_names, rt_parts = load_parts(raw)
check("every zip part survives the round-trip", rt_names, names)
rt = Attendees(rt_parts, sheet_part(rt_parts, "Attendees"))
check("the new person reads back", rt.value(3, "Full name"), "Ada")
check("the sample row is untouched", rt.value(2, "Notes (CRM)"), SAMPLE[4])
check("dimension covers the written rows",
      ET.fromstring(rt_parts["xl/worksheets/sheet1.xml"]).find(X + "dimension").get("ref"),
      "A1:L10")
check("rows stay in ascending order",
      [int(r.get("r")) for r in rt.data.findall(X + "row")], list(range(1, 11)))
check("re-serialized XML keeps the default namespace",
      rt_parts["xl/worksheets/sheet1.xml"].startswith(
          b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet'), True)

# A person appended past the end of the pre-created grid.
names, parts, att = book(sample_row=SAMPLE, blank_rows=1)
crowd = merge_people([person("P%d" % i, "p%d@x.io" % i, "B") for i in range(4)])
apply_ops(att, plan_workbook(att, crowd, TODAY))
att.serialize()
_, rt_parts = load_parts(save_parts(names, parts))
rt = Attendees(rt_parts, sheet_part(rt_parts, "Attendees"))
check("rows are created past the end of the grid",
      [rt.value(r, "Email") for r in (3, 4, 5, 6)],
      ["p0@x.io", "p1@x.io", "p2@x.io", "p3@x.io"])


# ---------------------------------------------------------------------------
# The dropdowns — CHECKED here, written by migrate_interested_in.py
# ---------------------------------------------------------------------------
names, parts, att = book(sample_row=SAMPLE)
check("a migrated workbook reports no stale dropdowns", check_dropdowns(att), [])
check("the lists are found at the right columns",
      {c: v for c, v in dv_lists(parts[sheet_part(parts, "Attendees")]).items()},
      {3: DV_EXPECTED["Status"], 11: DV_EXPECTED[NEW_COLUMN]})

# The pre-split list, which is what the whole fleet carried until 2026-08-25.
# Its role values are exactly the conflation the split removed, so a workbook
# still offering them must be reported every run.
LEGACY_STATUS_DV = "Prospect,Attended,Regular,Speaker,Organizer,Volunteer,Host,Declined"
_, _, a_old = book(sample_row=SAMPLE, dv=LEGACY_STATUS_DV)
check("a pre-split Status list is reported stale", check_dropdowns(a_old), ["Status"])
_, _, a_nr = book(sample_row=SAMPLE, dv_role="Yes,No")
check("a wrong Interested-in list is reported stale",
      check_dropdowns(a_nr), [NEW_COLUMN])
_, _, a_both = book(sample_row=SAMPLE, dv=LEGACY_STATUS_DV, dv_role="Yes,No")
check("both are reported together",
      sorted(check_dropdowns(a_both)), sorted(["Status", NEW_COLUMN]))

# The legacy CRMs escape the formula quotes. Matching only `"…"` is what once
# left every one of them silently unpatched while the run reported success, so
# the reader handles both encodings — and a checker that only saw one would
# report the whole legacy estate as stale forever.
names, parts, _ = book(sample_row=SAMPLE)
part = sheet_part(parts, "Attendees")
parts[part] = parts[part].replace(
    ('"%s"' % DV_EXPECTED["Status"]).encode(),
    ("&quot;%s&quot;" % DV_EXPECTED["Status"]).encode())
check("the &quot;-escaped spelling reads back identically",
      dv_lists(parts[part])[3], DV_EXPECTED["Status"])
check("...so a &quot;-escaped migrated workbook is not reported stale",
      check_dropdowns(Attendees(parts, part)), [])

# A validation spanning several columns cannot be attributed to one header, so
# it is skipped rather than guessed at — guessing would report a correct
# workbook as broken, or hide a genuinely stale list behind a neighbour's.
names, parts, _ = book(sample_row=SAMPLE)
part = sheet_part(parts, "Attendees")
parts[part] = parts[part].replace(b'sqref="D2:D1000"', b'sqref="C2:D1000"')
check("a multi-column sqref is not attributed to a header",
      3 in dv_lists(parts[part]), False)
check("...and the column then reads as having no list",
      check_dropdowns(Attendees(parts, part)), ["Status"])

# Pin the wire format: both lists are DERIVED from CRM_LIFECYCLE / CRM_ROLE, so
# an edit to either would otherwise change what lands in ~62 workbooks silently.
check("the derived dropdown literals",
      (DV_EXPECTED["Status"], DV_EXPECTED[NEW_COLUMN]),
      ("Prospect,In progress,Interviewing,Tentative,Accepted,Attended,Regular,"
       "Volunteer,Declined",
       "Organizer,Speaker,Host,Organizer/Speaker,Organizer/Host,Speaker/Host,"
       "Organizer/Speaker/Host"))
check("the Interested-in list can express everything crm_fields writes",
      sorted(sync_crm.DV_INTERESTED_VALUES),
      sorted({crm_fields(m, TODAY)[NEW_COLUMN] for m in [
          merge_people([person("a", "a@x.io", "B", t)])[0] for t in sync_crm.ROLE_TABS]
          } | {"Organizer/Speaker", "Organizer/Host", "Speaker/Host",
               "Organizer/Speaker/Host"}))

# A workbook that predates the split has no `Interested in` column at all.
# Refusing to open it is correct — nothing is written by column letter — but
# the message has to name the migration, not read as a corrupt workbook.
try:
    _, pre_parts = make_xlsx(sample_row=SAMPLE[:-1],
                             headers=tuple(h for h in CRM_HEADERS if h != NEW_COLUMN))
    Attendees(pre_parts, sheet_part(pre_parts, "Attendees"))
    check("a pre-split workbook is refused", "opened", "refused")
except ValueError as e:
    check("a pre-split workbook is refused, naming the migration",
          ("migrate_interested_in" in str(e), NEW_COLUMN in str(e)), (True, True))

names, parts, att = book(sample_row=SAMPLE)
b = sync_crm.Book(folder={"name": "Boston"}, crm={"id": "x", "name": "Boston CRM.xlsx"},
                  names=names, parts=parts, part=sheet_part(parts, "Attendees"),
                  att=att, path="/tmp/x.xlsx")
raw = sync_crm.finalize(b, plan_workbook(att, people, TODAY))
fin_names, fin_parts = load_parts(raw)
fin = Attendees(fin_parts, sheet_part(fin_parts, "Attendees"))
check("finalize keeps the row writes", fin.value(3, "Email"), "ada@x.io")
check("finalize leaves the dropdowns alone", check_dropdowns(fin), [])
check("finalize returns a complete zip", sorted(fin_names), sorted(names))


# ---------------------------------------------------------------------------
# match_chapters
# ---------------------------------------------------------------------------
FOLDERS = [{"id": "f1", "name": "Boston"}, {"id": "f2", "name": "Delhi NCR"},
           {"id": "f3", "name": "San Francisco"}, {"id": "f4", "name": "Washington DC"},
           {"id": "f5", "name": "Montréal"}, {"id": "t", "name": "TemplateCity"}]

by_folder, orphans, near = match_chapters(
    [person("A", "a@x.io", "boston"), person("B", "b@x.io", "Washington, DC"),
     person("C", "c@x.io", "Montreal"), person("D", "d@x.io", "New Delhi"),
     person("E", "e@x.io", "San Diego"), person("F", "f@x.io", "Reykjavik"),
     person("G", "g@x.io", "TemplateCity")], FOLDERS)
check("exact match, case-folded", [p["name"] for p in by_folder["f1"]], ["A"])
check("punctuation-folded match", [p["name"] for p in by_folder["f4"]], ["B"])
check("accent-folded match", [p["name"] for p in by_folder["f5"]], ["C"])
check("TemplateCity never receives people", "t" in by_folder, False)
check("near-miss on a shared discriminating token",
      [(m["city"], m["candidates"]) for m in near], [("New Delhi", ["Delhi NCR"])])
check("generic tokens do not create a near-miss (San Diego / San Francisco)",
      sorted(o["city"] for o in orphans), ["Reykjavik", "San Diego", "TemplateCity"])


# ---------------------------------------------------------------------------
# Write guards: the promises that are now structural rather than documented
# ---------------------------------------------------------------------------
_, _, attw = book(sample_row=SAMPLE)
for col in ("Signal",):
    try:
        attw.write(3, col, "x")
        check("write(%r) is refused" % col, "no error", "ValueError")
    except ValueError:
        check("write(%r) is refused" % col, True, True)
check("write() still accepts a CRM_WRITTEN column",
      attw.write(3, "Full name", "Ada") or attw.value(3, "Full name"), "Ada")
# clear() legitimately touches all eleven, including the guarded five.
attw.clear(3)
check("clear() may blank the guarded columns",
      [attw.value(3, h) for h in CRM_HEADERS], [""] * len(CRM_HEADERS))

# A cleared cell must be truly blank, not a zero-length inline string: the
# latter makes ISBLANK false and COUNTA count it, on row 2 of every workbook.
_, _, attb = book(sample_row=DUMMY)
apply_ops(attb, plan_workbook(attb, [], TODAY))
row2 = [c for c in attb.rows[2].findall(X + "c")]
check("a cleared cell carries no type attribute",
      [c.get("t") for c in row2], [None] * len(row2))
check("a cleared cell has no value child", [len(list(c)) for c in row2], [0] * len(row2))
check("a cleared cell keeps its style", [c.get("s") for c in row2][0], "3")

# apply_ops must not treat an unrecognised kind as a write.
_, _, atty = book(sample_row=SAMPLE)
try:
    apply_ops(atty, [{"kind": "delete", "rownum": 3, "sets": {"Full name": "X"}}])
    check("an unknown op kind is refused", "no error", "ValueError")
except ValueError:
    check("an unknown op kind is refused", True, True)
check("...and nothing was written", atty.value(3, "Full name"), "")

# occupied() must consider the columns the automation never writes, or a
# half-cleaned row hands the next person a stranger's LinkedIn and employer.
_, _, atto = book(sample_row=SAMPLE)
atto._write(4, "Company", "Ex-Employer Inc.")
check("a row with only an unwritten column set is still occupied",
      atto.occupied(4), True)
check("...so it is not offered as a free row", next(atto.free_rows()), 3)

# serialize() must refuse a root it cannot find, rather than slicing to the
# last byte and uploading a 57-byte 'worksheet'.
_, _, atts2 = book(sample_row=SAMPLE)
atts2.root.tag = "{http://example.invalid/other}worksheet"
try:
    atts2.serialize()
    check("serialize refuses a non-<worksheet> root", "no error", "ValueError")
except ValueError as e:
    check("serialize refuses a non-<worksheet> root", "refusing to write" in str(e), True)

# ---------------------------------------------------------------------------
# write_workbooks: a workbook edited during the plan window is never reverted
# ---------------------------------------------------------------------------
# Planning spans minutes over ~80 workbooks plus the approval pause. The bytes
# the plan was built on still sit at book.path; the pre-upload re-download must
# be COMPARED against them, and a mismatch skipped loudly — uploading anyway
# silently reverts whatever a human typed in the window.
with tempfile.TemporaryDirectory() as _wd:
    def _touched(tag):
        names_, parts_, att_ = book(sample_row=SAMPLE)
        path = os.path.join(_wd, "%s.xlsx" % tag)
        raw_ = save_parts(names_, parts_)          # the plan-time bytes
        with open(path, "wb") as fh:
            fh.write(raw_)
        bk = sync_crm.Book(folder={"name": tag},
                           crm={"id": "id-" + tag, "name": "%s CRM.xlsx" % tag},
                           names=names_, parts=parts_,
                           part=sheet_part(parts_, "Attendees"), att=att_, path=path)
        return {"book": bk, "ops": plan_workbook(att_, people, TODAY), "dv": None}, raw_

    t_same, raw_same = _touched("Boston")
    t_edit, raw_edit = _touched("Pune")
    _remote = {"id-Boston": raw_same,             # untouched since planning
               "id-Pune": raw_edit + b"human-edit"}  # changed in the window
    _uploads = []

    def _fake_download(fid, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(_remote[fid])
        return _remote[fid]

    _backup = os.path.join(_wd, "before")
    os.makedirs(_backup)
    # The freshness compare goes through sync_chapters.fresh_if_unchanged, so
    # the download to intercept lives in THAT module's namespace.
    with mock.patch.object(sync_chapters, "download", _fake_download), \
         mock.patch.object(sync_crm, "upload",
                           lambda fid, path, raw_b, ct: _uploads.append((fid, ct))):
        _written, _changed, _failed = sync_crm.write_workbooks(
            [t_same, t_edit], _wd, _backup)
    check("an unchanged workbook is written", _written, ["Boston"])
    check("a changed workbook is skipped, not reverted", _changed, ["Pune"])
    check("the skip is not a failure entry", _failed, [])
    check("only the unchanged workbook was uploaded",
          [u[0] for u in _uploads], [("id-Boston")])
    check("uploads carry the xlsx content type", [u[1] for u in _uploads], [sync_crm.XLSX])
    with open(os.path.join(_backup, "Pune.xlsx"), "rb") as fh:
        check("the changed workbook's FRESH bytes are what got backed up",
              fh.read(), raw_edit + b"human-edit")


# An alias bound to the spreadsheetml namespace must not displace the default
# binding — that is what made the root serialize as <x:worksheet>.
sync_crm.register_namespaces(
    ('<worksheet xmlns="%s" xmlns:x="%s" />' % (sync_crm._XLNS, sync_crm._XLNS)).encode())
_, _, atta = book(sample_row=SAMPLE)
atta.serialize()
check("an aliased spreadsheetml prefix does not break serialization",
      atta.parts[atta.part_name].count(b"<worksheet"), 1)

# ---------------------------------------------------------------------------
# --redact: stdout masking (default on under CI)
# ---------------------------------------------------------------------------
sync_crm.REDACT = False
check("redaction off: email passes through", sync_crm.redact_email("ada@x.com"), "ada@x.com")
check("redaction off: name passes through", sync_crm.redact_name("Ada Lovelace"), "Ada Lovelace")
sync_crm.REDACT = True
try:
    check("redacted email keeps one char + TLD only", sync_crm.redact_email("ada@x.com"), "a***@***.com")
    check("redacted name is a first initial", sync_crm.redact_name("ada lovelace"), "A.")
    check("a non-email is left alone", sync_crm.redact_email("Boston"), "Boston")
    check("empty values survive", (sync_crm.redact_email(""), sync_crm.redact_name("")), ("", ""))
finally:
    sync_crm.REDACT = False

sync_crm.REDACT = True
try:
    check("redact_sets shows column names but masks every value except role/status-like ones",
          sync_crm.redact_sets({"Email": "ada@x.com", "Full name": "Ada Lovelace",
                                "Company": "Acme", "Status": "Accepted",
                                "Role / title": "CTO", "What brings you here?": "my friend Ada"}),
          {"Email": "…", "Full name": "…", "Company": "…", "Status": "Accepted",
           "Role / title": "CTO", "What brings you here?": "…"})
finally:
    sync_crm.REDACT = False

# ---------------------------------------------------------------------------
# --write leaves nothing behind but before/, and before/ is gitignored
# ---------------------------------------------------------------------------
# The workdir holds a downloaded copy of every CRM; after the write only the
# pre-edit copies have any value and they live under <repo>/backups/.
_wd = tempfile.mkdtemp(prefix="aaif-crm-test-")
os.makedirs(os.path.join(_wd, "verify"))
os.makedirs(os.path.join(_wd, "before"))
for name in ("Boston.xlsx", "reread.xlsx", "verify/Boston.xlsx", "before/Boston.xlsx"):
    open(os.path.join(_wd, name), "wb").write(b"x")
check("cleanup with keep_backups reports nothing stranded",
      sync_crm.cleanup_workdir(_wd, keep_backups=True), False)
check("cleanup with keep_backups leaves exactly before/",
      sorted(os.listdir(_wd)), ["before"])
check("cleanup without keep_backups reports nothing stranded",
      sync_crm.cleanup_workdir(_wd, keep_backups=False), False)
check("cleanup without keep_backups removes the workdir itself",
      os.path.exists(_wd), False)
with mock.patch.object(sync_crm.shutil, "rmtree", lambda *a, **k: None), \
     mock.patch.object(sync_crm, "_unlink_quietly", lambda p: None):
    _wd2 = tempfile.mkdtemp(prefix="aaif-crm-test-")
    open(os.path.join(_wd2, "Boston.xlsx"), "wb").write(b"x")
    check("a file that will not delete is reported as stranded",
          sync_crm.cleanup_workdir(_wd2, keep_backups=False), True)
    with mock.patch.object(sync_crm, "_run", return_value=0), \
         mock.patch.object(sync_crm.tempfile, "mkdtemp", return_value=_wd2):
        check("run() exits non-zero when member data was stranded",
              sync_crm.run(mock.Mock()), 1)
import shutil as _shutil  # noqa: E402
_shutil.rmtree(_wd2)

import subprocess  # noqa: E402
_root = sync_crm.backup_root("crm-before-selftest")
try:
    check("backup_root lands under <repo>/backups/",
          os.path.dirname(_root), os.path.join(sync_crm.REPO, "backups"))
    check("backup_root is gitignored (check-ignore on a child path)",
          subprocess.run(["git", "-C", sync_crm.REPO, "check-ignore", "-q",
                          os.path.join(_root, "before", "x.xlsx")]).returncode, 0)
    check("backup_root is private to the operator", os.stat(_root).st_mode & 0o777, 0o700)
finally:
    os.rmdir(_root)


def _backup_root_with(results, raise_missing=False):
    """backup_root with subprocess.run scripted; returns (exit message or None, created path)."""
    calls = iter(results)
    def _fake_run(argv, **kw):
        if raise_missing:
            raise FileNotFoundError("git")
        rc, out, err = next(calls)
        return mock.Mock(returncode=rc, stdout=out, stderr=err)
    made = []
    with mock.patch.object(sync_crm.subprocess, "run", _fake_run), \
         mock.patch.object(sync_crm.os, "makedirs", lambda p, **k: made.append(p)):
        try:
            return None, sync_crm.backup_root("crm-before-selftest")
        except SystemExit as e:
            return str(e), None


_msg, _p = _backup_root_with([], raise_missing=True)
check("git missing aborts explicitly", "git is not installed" in (_msg or ""), True)
_msg, _p = _backup_root_with([(128, "", "fatal: not a git repository (or any parent)")])
check("outside any repo is allowed (nothing to leak into)", (_msg, _p is not None), (None, True))
_msg, _p = _backup_root_with([(128, "", "fatal: detected dubious ownership in repository at '/x'")])
check("any other git failure aborts quoting git",
      ("cannot verify" in (_msg or ""), "dubious ownership" in (_msg or "")), (True, True))
_msg, _p = _backup_root_with([(0, "/repo\n", ""), (1, "", "")])
check("a backup root git would pick up ABORTS", "not gitignored" in (_msg or ""), True)
_msg, _p = _backup_root_with([(0, "/repo\n", ""), (0, "", "")])
check("inside the repo and ignored is fine", (_msg, _p is not None), (None, True))


# --- the CI default is a real boolean, and masking announces itself ------------
import io as _io  # noqa: E402
import contextlib as _ctx  # noqa: E402
check("the CI default is the strict 1/true/yes parse of $CI", sync_crm.CI_REDACT_DEFAULT,
      os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sync_crm.set_redaction(True)
check("turning redaction on prints exactly one stderr line",
      (_err.getvalue().count("\n"), "redaction ON" in _err.getvalue()), (1, True))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sync_crm.set_redaction(False)
check("turning redaction off is silent", _err.getvalue(), "")
check("set_redaction(False) leaves REDACT off", sync_crm.REDACT, False)
print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
