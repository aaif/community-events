#!/usr/bin/env python3
"""Unit tests for the pure logic in sync_crm.py (no network/gws).

The .xlsx fixtures are built here rather than checked in as binaries, from the
same shape the real chapter CRMs have — inline strings, a styled sample row 2,
pre-created empty rows, and the Status data-validation list. A checked-in binary
would silently stop resembling the live workbooks; this can't.
"""
import os, sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_crm
from sync_crm import (Attendees, CRM_HEADERS, DV_STATUS_NEW, DV_STATUS_OLD,
                      SELF_SERVE_MIN, X,
                      apply_ops, cell_ref, clean_text, col_of, crm_fields,
                      fold_email, gate_pipeline_organizers, is_aaif_ops,
                      join_distinct, load_parts, match_chapters,
                      merge_people, patch_status_dropdown, plan_workbook,
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
              shared=False, dv=DV_STATUS_OLD):
    """A two-sheet workbook whose 'Attendees' tab mirrors the shipped template."""
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
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:K%d" /><sheetData>%s</sheetData>'
        '<dataValidations count="1"><dataValidation sqref="D2:D1000" type="list">'
        '<formula1>"%s"</formula1></dataValidation></dataValidations>'
        '</worksheet>' % (2 + blank_rows, "".join(rows), dv))
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
          "Sales", "—", "Community regular"]

# The template's shipped fixture row, which every chapter CRM carries.
DUMMY = ["Sam Taylor", "Non-grata", "No", "Declined",
         "Pitched from the floor. Do not invite.", "sam@example.com", "", "Vendor Inc.",
         "Sales", "—", "Wanted to pitch"]


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

f_acc = crm_fields(both[0], TODAY)
check("accepted organizer -> Organizer", f_acc["Status"], "Organizer")
check("accepted organizer -> Trusted", f_acc["Trusted/Regular"], "Yes")
check("note carries every role, statuses deduped",
      f_acc["Notes (CRM)"], "Intake: Organizer/Speaker · Accepted · 2026-08-06")

f_host = crm_fields(merge_people([person("Bo", "bo@x.io", "B", "Hosts", "Accepted")])[0], TODAY)
check("accepted host -> Host", f_host["Status"], "Host")
check("accepted host is not auto-Trusted", f_host["Trusted/Regular"], "")

# Pipeline (not-yet-accepted) people, per the self-serve policy.
f_pipe = crm_fields(merge_people([person("Cy", "cy@x.io", "B", "Organizers", "Tentative")])[0], TODAY)
check("pipeline organizer -> Prospect", f_pipe["Status"], "Prospect")
check("pipeline organizer is not Trusted", f_pipe["Trusted/Regular"], "")
check("pipeline note carries the intake status",
      f_pipe["Notes (CRM)"], "Intake: Organizer · Tentative · 2026-08-06")
f_pspk = crm_fields(merge_people([person("Dee", "dee@x.io", "B", "Speakers", "New")])[0], TODAY)
check("pipeline speaker -> Speaker (not Prospect)", f_pspk["Status"], "Speaker")
check("pipeline speaker is not Trusted", f_pspk["Trusted/Regular"], "")
# Accepted in one role while still in the pipeline for another: the accepted
# role wins the Status, and the organizer application alone earns no trust.
f_mix = crm_fields(merge_people([
    person("Eve", "eve@x.io", "B", "Organizers", "Interviewing"),
    person("Eve", "eve@x.io", "B", "Speakers", "Accepted")])[0], TODAY)
check("accepted speaker beats pipeline organizer", f_mix["Status"], "Speaker")
check("...and does not become Trusted", f_mix["Trusted/Regular"], "")

# Minimal by construction: the automation must not touch Signal or any of the
# detail columns, so they are absent from the mapping rather than written blank.
check("only the minimal columns are produced",
      sorted(f_acc), sorted(sync_crm.CRM_WRITTEN))
for col in ("Signal", "LinkedIn URL", "Company", "Role / title", "Technical expertise"):
    check("%r is never written" % col, col in f_acc, False)


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
          ["Tess", "Dan", "Zed", "Bea"])
    pp, rr, _fb = sync_crm.read_role_tab("Organizers", {}, include_pipeline=True)
    check("pipeline mode admits in-flight statuses",
          sorted(p["name"] for p in pp), ["Ada", "Bea", "Tess"])
    check("a blank status is normalized to New on the person",
          [p["status"] for p in pp if p["name"] == "Bea"], ["New"])
    check("Denied and unknown statuses fail closed even in pipeline mode",
          sorted(r["name"] for r in rr
                 if "declined, parked, or not a recognised" in r["why"]),
          ["Dan", "Zed"])
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
check("the survey's detail columns are not written",
      [c for c in ("Technical expertise", "LinkedIn URL", "Company") if c in ops[0]["sets"]], [])

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
for real in ("ravi@vendor.co", "rahul@aihero.studio", "a@examples.com",
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
# Speaker/Host -> Organizer is the case the AUTO_STATUS rule exists for ("a
# person's role is corrected after re-triage") and was the one it never covered.
for before, want in (("", "Organizer"), ("New", "Organizer"), ("Prospect", "Organizer"),
                     ("Speaker", "Organizer"), ("Host", "Organizer"),
                     ("Organizer", None),
                     ("Attended", None), ("Declined", None), ("Regular", None),
                     ("Volunteer", None)):
    _, _, a = book(sample_row=SAMPLE)
    a.write(3, "Email", "ada@x.io")
    if before:
        a.write(3, "Status", before)
    ops_s = plan_workbook(a, people, TODAY)
    # Assert an op exists at all: for the protected values the expected result
    # is None, which an EMPTY ops list also yields — so four of these checks
    # would pass vacuously if planning stopped emitting ops entirely.
    check("Status %-10r -> op kinds" % before, [o["kind"] for o in ops_s], ["fill"])
    got = ops_s[0]["sets"].get("Status") if ops_s else None
    check("Status %-10r -> %r" % (before, want), got, want)

# The self-serve lifecycle end-to-end: a Prospect synced while in the pipeline
# is upgraded in place once the chapter accepts them — Status is in AUTO_STATUS
# and the blank Trusted/Regular cell fills.
_, _, alc = book(sample_row=SAMPLE)
pipe = merge_people([person("Ada", "ada@x.io", "Boston", "Organizers", "Tentative",
                            linkedin="https://li/ada", expertise="Agents, MCP")])
apply_ops(alc, plan_workbook(alc, pipe, TODAY))
check("a prospect lands untrusted",
      [alc.value(3, "Status"), alc.value(3, "Trusted/Regular")], ["Prospect", ""])
up = plan_workbook(alc, people, TODAY)
check("acceptance upgrades the prospect in place",
      (up[0]["kind"], up[0]["sets"].get("Status"), up[0]["sets"].get("Trusted/Regular")),
      ("fill", "Organizer", "Yes"))


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
      "A1:K10")
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
# The Status dropdown patch
# ---------------------------------------------------------------------------
names, parts, att = book(sample_row=SAMPLE)
part = sheet_part(parts, "Attendees")
check("dropdown patch reports a change", patch_status_dropdown(parts, part), "patched")
check('dropdown now offers "Host"', DV_STATUS_NEW.encode() in parts[part], True)
check("dropdown patch is idempotent", patch_status_dropdown(parts, part), "already")
names, parts, _ = book(sample_row=SAMPLE, dv=DV_STATUS_NEW)
check("an already-patched workbook is left alone",
      patch_status_dropdown(parts, sheet_part(parts, "Attendees")), "already")
check('"Host" is the only difference from the shipped list',
      DV_STATUS_NEW.replace("Host,", ""), DV_STATUS_OLD)

# The legacy CRMs escape the formula quotes; matching only `"…"` left every one
# of them un-patched while the run still reported success.
names, parts, _ = book(sample_row=SAMPLE)
part = sheet_part(parts, "Attendees")
parts[part] = parts[part].replace(('"%s"' % DV_STATUS_OLD).encode(),
                                  ("&quot;%s&quot;" % DV_STATUS_OLD).encode())
check("patches the &quot;-escaped legacy spelling",
      patch_status_dropdown(parts, part), "patched")
check("legacy patch keeps the escaping",
      ("&quot;%s&quot;" % DV_STATUS_NEW).encode() in parts[part], True)
check("legacy patch is idempotent", patch_status_dropdown(parts, part), "already")

names, parts, _ = book(sample_row=SAMPLE, dv="Yes,No")
check("a workbook with no Status list is reported, not guessed at",
      patch_status_dropdown(parts, sheet_part(parts, "Attendees")), "absent")

# Regression: serialize() rewrites the sheet part from the element tree, so a
# dropdown patch applied BEFORE it is silently discarded — the run reports the
# patch as applied and the uploaded workbook still has the eight-value list.
# finalize() is the only supported write path precisely to fix the ordering.
names, parts, att = book(sample_row=SAMPLE)
part = sheet_part(parts, "Attendees")
patch_status_dropdown(parts, part)
att.serialize()
check("serialize-then-patch is the bug this guards",
      DV_STATUS_NEW.encode() in parts[part], False)

names, parts, att = book(sample_row=SAMPLE)
b = sync_crm.Book(folder={"name": "Boston"}, crm={"id": "x", "name": "Boston CRM.xlsx"},
                  names=names, parts=parts, part=sheet_part(parts, "Attendees"),
                  att=att, path="/tmp/x.xlsx")
raw = sync_crm.finalize(b, plan_workbook(att, people, TODAY))
fin_names, fin_parts = load_parts(raw)
fin = Attendees(fin_parts, sheet_part(fin_parts, "Attendees"))
check("finalize keeps the dropdown patch",
      DV_STATUS_NEW.encode() in fin_parts[sheet_part(fin_parts, "Attendees")], True)
check("finalize keeps the row writes", fin.value(3, "Email"), "ada@x.io")
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
for col in ("Signal", "LinkedIn URL", "Company", "Role / title", "Technical expertise"):
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

# An alias bound to the spreadsheetml namespace must not displace the default
# binding — that is what made the root serialize as <x:worksheet>.
sync_crm.register_namespaces(
    ('<worksheet xmlns="%s" xmlns:x="%s" />' % (sync_crm._XLNS, sync_crm._XLNS)).encode())
_, _, atta = book(sample_row=SAMPLE)
atta.serialize()
check("an aliased spreadsheetml prefix does not break serialization",
      atta.parts[atta.part_name].count(b"<worksheet"), 1)

print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
