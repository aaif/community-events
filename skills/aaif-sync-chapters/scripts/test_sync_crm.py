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
from sync_crm import (Attendees, CRM_HEADERS, DV_STATUS_NEW, DV_STATUS_OLD, X,
                      apply_ops, cell_ref, clean_text, col_of, crm_fields,
                      fold_email, join_distinct, load_parts, match_chapters,
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
    # Rows 3.. ship with only the three dropdown columns materialised.
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
    person("Ada L", "ada@x.io", "Boston", "Speakers", "New", interest="I want to be a speaker",
           title="Staff Eng", expertise="Agents"),
    person("Ada Lovelace", "ADA@x.io", "boston", "Organizers", "Accepted",
           interest="I want to be an organizer/volunteer", expertise="Agents"),
])
check("merge collapses one person to one row", len(both), 1)
check("merge takes the highest-priority role first", both[0]["tabs"], ["Organizers", "Speakers"])
check("merge keeps the acceptance", both[0]["status"], "Accepted")
check("merge keeps the speaker's title", both[0]["title"], "Staff Eng")
check("merge unions the interests", both[0]["interest"],
      "I want to be an organizer/volunteer · I want to be a speaker")
check("merge dedupes identical expertise", both[0]["expertise"], "Agents")
check("merge keeps different cities apart",
      len(merge_people([person("A", "a@x.io", "Boston"), person("A", "a@x.io", "Berlin")])), 2)

f_acc = crm_fields(both[0], TODAY)
check("accepted organizer -> Organizer", f_acc["Status"], "Organizer")
check("accepted organizer -> Trusted", f_acc["Trusted/Regular"], "Yes")
check("note carries role, status and date",
      f_acc["Notes (CRM)"], "Intake: Organizer/Speaker · Accepted · 2026-08-06")

f_host = crm_fields(merge_people([person("Bo", "bo@x.io", "B", "Hosts", "Accepted")])[0], TODAY)
check("accepted host -> Host", f_host["Status"], "Host")
check("accepted host is not auto-Trusted", f_host["Trusted/Regular"], "")

# Minimal by construction: the automation must not touch Signal or any of the
# detail columns, so they are absent from the mapping rather than written blank.
check("only the minimal columns are produced",
      sorted(f_acc), sorted(sync_crm.CRM_WRITTEN))
for col in ("Signal", "LinkedIn URL", "Company", "Role / title", "Technical expertise"):
    check("%r is never written" % col, col in f_acc, False)


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
check("interest comes from the survey",
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
for before, want in (("", "Organizer"), ("New", "Organizer"), ("Prospect", "Organizer"),
                     ("Attended", None), ("Declined", None), ("Regular", None),
                     ("Volunteer", None)):
    _, _, a = book(sample_row=SAMPLE)
    a.write(3, "Email", "ada@x.io")
    if before:
        a.write(3, "Status", before)
    got = plan_workbook(a, people, TODAY)
    got = got[0]["sets"].get("Status") if got else None
    check("Status %-10r -> %r" % (before, want), got, want)


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


print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
