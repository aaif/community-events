#!/usr/bin/env python3
"""One-shot: split the chapter CRMs' `Status` column into a decision and a role.

Until 2026-08-25 the Attendees tab had ONE `Status` column and sync_crm wrote
the person's ROLE into it, taken from whichever intake role tab they came from.
So a venue host whose intake row still read "Prospect" appeared in their
chapter's CRM as a flat `Host`, and a speaker awaiting triage as `Speaker` — a
chapter organizer scanning the list saw settled people where central triage had
settled nothing. Pipeline ORGANIZERS were the one role spared, because they
were special-cased to "Prospect", which is what made the bug easy to miss.

This script performs the split across every "<City> CRM.xlsx" under the
Chapters Drive folder (the templates included), in five parts:

  1. A new `Interested in` column is APPENDED — at the first free column past
     the last header, which is L on the shipped layout. Appended, not slotted
     in beside Status where it reads best: inserting a column would renumber
     every cell ref in ~1000 rows, every dataValidation sqref, and the Guide
     tab's cross-sheet formulas. Nothing addresses these sheets by letter.

  2. Every occupied row is backfilled from its own `Notes (CRM)` provenance
     string — "Intake: Organizer/Speaker · Accepted · 2026-08-07" — which has
     carried BOTH facts correctly all along. The role half becomes
     `Interested in`; the status half becomes `Status`. Nothing is invented:
     a row whose Status holds a role but whose note cannot be parsed has the
     role RELOCATED and its Status left BLANK, because a role was never a
     decision and this script is not entitled to guess one. Blank is in
     sync_crm.AUTO_STATUS, so the next sync fills it from the live intake.

  3. Both dropdowns are rewritten to sync_crm.DV_EXPECTED: `Status` loses
     "Speaker" / "Organizer" / "Host" (offering them is what let the column
     mean two things at once) and gains the pipeline ladder; the new column
     gets the role list. sync_crm CHECKS these and reports drift; only this
     script writes them.

  4. The Guide tab's formulas are repointed. Two of them are landmines:
       * the dashboard counts `COUNTIF(Attendees!D2:D1000,"Speaker")` and the
         same for "Organizer" — after the split those read ZERO in every
         chapter unless they move to the new column (with wildcards, since a
         cell can say "Organizer/Speaker");
       * the "Live list" FILTER formula already references `Attendees!L2:L`,
         a column that does not exist on the 11-column layout. It has been
         returning a blank column since the last renumbering. It is repointed
         at the columns it plainly meant.
     Cached <v> results next to a rewritten <f> are left stale; Sheets and
     Excel both recalculate on open.

A row a human curated is never touched: only a `Status` holding one of the
three role words is rewritten, and an `Interested in` that already has content
is left exactly as it is. "Attended", "Regular", "Volunteer" and "Declined" say
something the intake does not know and are outside this script's reach.

Idempotent: a workbook that already has the column, the lists and the Guide
formulas plans zero changes.

House rules: the report is the default and writes nothing; --write applies,
then re-downloads and re-reads every workbook and prints a Verified line.
Pre-edit bytes are kept under <repo>/backups/ (gitignored). No member names or
emails on stdout — counts, chapter names and column names only.

Exit codes: 0 nothing due (or a --write that applied and verified cleanly);
2 changes proposed in report mode; 1 failure — a skipped workbook, a failed
verify, or a gap this script cannot close.

Usage:
  python3 migrate_interested_in.py                 # report only, zero writes
  python3 migrate_interested_in.py --city Boston   # scope to one chapter
  python3 migrate_interested_in.py --write         # apply, then verify
"""
import argparse
import os
import re
import sys
import tempfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import download, fold_city, fresh_if_unchanged, upload  # noqa: E402
from sync_crm import (CRM_HEADERS, CRM_LIFECYCLE, CRM_ROLE, CRM_SHEET,  # noqa: E402
                      DV_EXPECTED, NEW_COLUMN, SYNC_STATUSES, X, XLSX,
                      Attendees, backup_root, cell_ref, cleanup_workdir,
                      clean_text, col_of, find_crm, list_chapter_folders,
                      load_parts, save_parts, set_cell, sheet_part)

#: The header set a PRE-split workbook has — everything except the column this
#: script adds. Attendees() would otherwise refuse to open one, which is
#: correct for the sync and useless for the migration that fixes it.
PRE_SPLIT_HEADERS = tuple(h for h in CRM_HEADERS if h != NEW_COLUMN)

#: The three values that used to live in `Status` and are being relocated.
#: Matched case-insensitively — these were typed by hand as often as written.
ROLE_WORDS = {r.casefold(): r for r in CRM_ROLE.values()}

#: Width for the new column. The shipped `<cols>` runs leave the appended
#: column at the sheet default (8.71), which truncates "Organizer/Speaker" to
#: about "Organiz". widen_column splits whatever run it lands in, so no column
#: letter is assumed here.
NEW_COL_WIDTH = "20"

# The Notes (CRM) provenance string sync_crm writes:
#   "Intake: Organizer/Speaker · Accepted · 2026-08-07"
# Anchored at the start and tolerant of the separator's surrounding spaces. The
# date is matched but unused — it is what makes the third group unambiguous.
NOTE_RE = re.compile(
    r"^\s*Intake:\s*(?P<roles>[^·]+?)\s*·\s*(?P<statuses>[^·]+?)\s*·\s*(?P<date>\S+)")


def parse_note(note):
    """(interested, status) from a Notes (CRM) provenance string, or ("", "").

    Both halves may be "/"-joined for someone who applied in several roles.
    The status follows crm_fields()'s own rule — accepted in ANY role means
    Accepted — so a backfilled row is byte-identical to what the sync would
    write today, and the very next run therefore proposes nothing.
    """
    m = NOTE_RE.match(clean_text(note))
    if not m:
        return "", ""
    roles = [ROLE_WORDS.get(r.strip().casefold())
             for r in m.group("roles").split("/")]
    if not all(roles):
        return "", ""            # a role word we do not recognise: do not guess
    raw = [s.strip() for s in m.group("statuses").split("/") if s.strip()]
    if any(s in SYNC_STATUSES for s in raw):
        status = "Accepted"
    else:
        # CRM_LIFECYCLE covers every value the intake can hold; anything else
        # is a note a human rewrote, and yields no status rather than a wrong one.
        mapped = [CRM_LIFECYCLE[s] for s in raw if s in CRM_LIFECYCLE]
        status = mapped[0] if mapped else ""
    return "/".join(roles), status


def plan_row(interested, status, note):
    """{header: new value} for one Attendees row — empty when nothing is due.

    The three rules, in order:
      * `Interested in` is filled only when EMPTY. Content already there is
        either this migration's own earlier pass or a human's, and either way
        it is not ours to restate.
      * `Status` is rewritten only when it holds one of the three ROLE words.
        Every other value — blank, a pipeline status, or a human's "Declined" —
        is left alone, because only a role in that column is unambiguously the
        bug this script exists to undo.
      * The note is the only source for `Status`. `Interested in` falls back
        to the role word already sitting in `Status` when the note will not
        parse — the role still moves to its proper column, and the status goes
        blank rather than being guessed at; a blank invites the next sync to
        decide, a guess would outrank it.
    """
    note_role, note_status = parse_note(note)
    status_role = ROLE_WORDS.get(clean_text(status).casefold())
    sets = {}
    if not clean_text(interested):
        want = note_role or status_role
        if want:
            sets[NEW_COLUMN] = want
    if status_role:
        sets["Status"] = note_status
    return sets


def plan_workbook(att):
    """[{rownum, sets}] for every occupied row that needs the split applied."""
    ops = []
    for rownum in sorted(att.rows):
        if rownum == 1 or not att.occupied(rownum):
            continue
        sets = plan_row(att.value(rownum, NEW_COLUMN) if NEW_COLUMN in att.headers
                        else "",
                        att.value(rownum, "Status"),
                        att.value(rownum, "Notes (CRM)"))
        if sets:
            ops.append({"rownum": rownum, "sets": sets})
    return ops


# ---------------------------------------------------------------------------
# Sheet surgery — all of it on the ElementTree, none of it on bytes
# ---------------------------------------------------------------------------
# Attendees.serialize() rewrites the sheet part wholesale from the tree, so a
# bytes-level edit made beforehand is silently discarded. That trap cost a
# probe workbook once already (see sync_crm.finalize); doing every edit on the
# tree removes the ordering question instead of documenting it.
def add_column(att):
    """Append the new header, returning its 0-based column index.

    Idempotent: a workbook that already has the column keeps its position,
    wherever a previous pass or a human put it.
    """
    if NEW_COLUMN in att.headers:
        return att.headers[NEW_COLUMN]
    col = max(att.headers.values()) + 1
    head = att.rows[1]
    # Match the header row's own style rather than the sheet default, or the
    # new column renders as plain text beside ten dark-blue banner cells.
    style = next((c.get("s") for c in head.findall(X + "c") if c.get("s")), None)
    set_cell(head, col, NEW_COLUMN, style)
    att.headers[NEW_COLUMN] = col
    return col


def widen_column(att, col):
    """Give the new column a readable width, splitting the shipped <cols> run.

    The template declares one <col min="12" max="19" width="8.71">; narrowing
    a shared run to a single column would silently re-width seven others, so
    the run is split rather than edited in place.
    """
    cols = att.root.find(X + "cols")
    if cols is None:
        return
    one = col + 1                                    # <col> is 1-based
    for c in list(cols):
        lo, hi = int(c.get("min")), int(c.get("max"))
        if not lo <= one <= hi:
            continue
        if c.get("width") == NEW_COL_WIDTH and lo == hi == one:
            return                                   # already split out
        at = list(cols).index(c)
        for new_lo, new_hi in ((lo, one - 1), (one, one), (one + 1, hi)):
            if new_lo > new_hi:
                continue
            el = ET.Element(X + "col", dict(c.attrib))
            el.set("min", str(new_lo))
            el.set("max", str(new_hi))
            if new_lo == one:
                el.set("width", NEW_COL_WIDTH)
                el.set("customWidth", "1")
            cols.insert(at, el)
            at += 1
        cols.remove(c)
        return


def dv_plan(att):
    """([(header, current)] due, [header] blocked) for the two dropdowns.

    `blocked` is a column whose validation spans several columns — rewriting it
    would silently re-validate a neighbour (the Signal list's unrelated "New"),
    and there is no safe way to split a merged sqref without knowing what a
    human meant. apply_dropdowns refuses those.

    Splitting the two is not cosmetic. `blocked` used to come back in the same
    list as `due`, so `plan()["any"]` stayed true for a workbook that had
    already received everything it was ever going to: the verify reported
    "1 op still pending", the run exited 1, and every later run re-downloaded,
    re-backed-up and re-uploaded it to change nothing — for ever. The comment
    beside the refusal already said counting one "would pin every later run to
    a permanent failure"; this is what makes that true.
    """
    due, blocked = [], []
    for header, want in DV_EXPECTED.items():
        col = att.headers.get(header)
        if col is None:
            due.append((header, None))
            continue
        if any(_dv_column(dv) is None and _covers(dv, col) for dv in _dv_elements(att)):
            blocked.append(header)
            continue
        got = next((_dv_formula(dv) for dv in _dv_elements(att)
                    if _dv_column(dv) == col), None)
        if got != want:
            due.append((header, got))
    return due, blocked


def _dv_elements(att):
    block = att.root.find(X + "dataValidations")
    return list(block) if block is not None else []


def _dv_column(dv):
    """The single column a dataValidation covers, or None if it spans several."""
    sqref = (dv.get("sqref") or "").strip()
    if not sqref or " " in sqref:
        return None
    cols = {col_of(end) for end in sqref.split(":")}
    # -1 is col_of's "no column letter here". Returning it would let an
    # unparsable sqref match a real column index and be rewritten.
    return cols.pop() if len(cols) == 1 and -1 not in cols else None


def _dv_formula(dv):
    f = dv.find(X + "formula1")
    if f is None or not f.text:
        return None
    return f.text.strip().strip('"')


def apply_dropdowns(att, last_row=1000):
    """Set both columns' lists to DV_EXPECTED, creating the block if needed.

    A validation spanning several columns is left alone and reported by the
    caller: rewriting one would silently re-validate a neighbour (the Signal
    column's list contains an unrelated "New" that must survive), and there is
    no safe way to split a merged sqref without knowing what a human meant.
    """
    block = att.root.find(X + "dataValidations")
    if block is None:
        # NOT ET.SubElement: that appends to the end of <worksheet>, which puts
        # dataValidations after pageMargins/pageSetup. CT_Worksheet is a fixed
        # sequence and Excel calls such a file corrupt — the same trap apply_cf
        # computes an insertion point to avoid. Worse, apply_dropdowns runs
        # FIRST, so a block appended here then becomes the anchor apply_cf
        # searches for and drags the colour rules after pageMargins too.
        block = ET.Element(X + "dataValidations")
        att.root.insert(_insert_at(att, AFTER_DATA_VALIDATIONS), block)
    refused = []
    for header, want in DV_EXPECTED.items():
        col = att.headers[header]
        existing = [dv for dv in list(block) if _dv_column(dv) == col]
        if any(_dv_column(dv) is None and _covers(dv, col) for dv in list(block)):
            refused.append(header)
            continue
        if existing:
            dv = existing[0]
        else:
            dv = ET.SubElement(block, X + "dataValidation", {
                "sqref": "%s:%s" % (cell_ref(col, 2), cell_ref(col, last_row)),
                "type": "list", "allowBlank": "1", "showDropDown": "0",
                "showInputMessage": "0", "showErrorMessage": "0"})
        f = dv.find(X + "formula1")
        if f is None:
            f = ET.SubElement(dv, X + "formula1")
        f.text = '"%s"' % want
    block.set("count", str(len(list(block))))
    return refused


def _covers(dv, col):
    """Whether a validation's range includes `col`, for the refusal check.

    Deliberately INCLUSIVE of what it cannot parse: an unreadable end (-1 from
    col_of) makes this return True, so the column is refused and reported
    rather than rewritten under a rule nobody understood.
    """
    sqref = (dv.get("sqref") or "").strip()
    ends = [col_of(e) for e in re.split(r"[\s:]+", sqref) if e]
    if not ends:
        return False
    if -1 in ends:
        return True
    return min(ends) <= col <= max(ends)


# ---------------------------------------------------------------------------
# Conditional formatting on Status
# ---------------------------------------------------------------------------
# Every CRM ships the same three `dxf` styles, and the Signal column already
# uses all three — so the decision ladder is painted by REFERENCING them, not
# by adding any. Editing styles.xml across 83 workbooks to introduce a fourth
# colour would be real surgery for no gain, and dxfId is a positional index
# into <dxfs>: appending to one workbook and not another silently paints the
# wrong colour in whichever one drifted.
#
#   0  green  bold #1B7A48 on #C6EFCE   "settled, and settled well"
#   1  amber       #9C6500 on #FFEB9C   "in flight — someone must act"
#   2  red    bold #9C0006 on #FFC7CE   "settled no"
DXF_GOOD, DXF_INFLIGHT, DXF_BAD = 0, 1, 2

#: Status value -> dxfId. Deliberately PARTIAL: `Prospect` is the single most
#: common value in the column and `Attended` is a neutral fact, so neither is
#: painted. Colouring every value colours nothing — the point is that the rows
#: needing attention stand out from the rows that do not.
STATUS_DXF = {
    "Accepted": DXF_GOOD, "Regular": DXF_GOOD, "Volunteer": DXF_GOOD,
    "In progress": DXF_INFLIGHT, "Interviewing": DXF_INFLIGHT,
    "Tentative": DXF_INFLIGHT,
    "Declined": DXF_BAD,
}

#: How many <dxf> entries a workbook must have for the ids above to mean what
#: they say. Fewer and the rules would reference a style that does not exist,
#: which renders as arbitrary formatting rather than as an error.
MIN_DXFS = 3

# A blank Status is deliberately NOT painted: the rule covers the whole Status
# column and the template pre-creates 1000 empty rows, so a blank rule lights up
# the entire column on every chapter with fewer than a thousand people — i.e.
# all of them. (_build_cf derives the column from att.headers, so no letter is
# assumed; migrate_column_order.py has since moved Status to E.)
_DXF_COUNT_RE = re.compile(rb'<dxfs\b[^>]*\bcount="(\d+)"')


def dxf_count(parts):
    m = _DXF_COUNT_RE.search(parts.get("xl/styles.xml", b""))
    return int(m.group(1)) if m else 0


def status_cf_rules():
    """[(value, dxfId)] in the order the rules are written.

    Sorted by DV_STATUS_VALUES so the block reads down the column's own
    dropdown, and so two runs cannot produce different orderings of the same
    rules — which would make the idempotency check below fail forever.
    """
    order = {v: i for i, v in enumerate(DV_EXPECTED["Status"].split(","))}
    return sorted(STATUS_DXF.items(), key=lambda kv: order.get(kv[0], 99))


def _cf_blocks(att):
    return [el for el in att.root if el.tag == X + "conditionalFormatting"]


def _cf_signature(el):
    """(sqref, ((value, dxfId), ...)) for one block, for comparison."""
    rules = []
    for r in el:
        f = r.find(X + "formula")
        text = (f.text or "").strip() if f is not None else ""
        rules.append((text.strip('"').replace("&quot;", "").strip('"'),
                      int(r.get("dxfId", -1))))
    return (el.get("sqref"), tuple(rules))


#: The states cf_plan can return. Named, because a magic string that is not
#: "due" is silently skipped by `any` AND silently absent from the gap report —
#: a fifth state added later would make a whole chapter vanish from both.
CF_OK, CF_DUE, CF_FOREIGN, CF_NO_DXFS = "ok", "due", "foreign", "no-dxfs"
CF_STATES = (CF_OK, CF_DUE, CF_FOREIGN, CF_NO_DXFS)
#: Terminal states: reported, never counted as pending work.
CF_BLOCKED = (CF_FOREIGN, CF_NO_DXFS)


def cf_plan(att, parts):
    """One of CF_STATES for the Status colour rules.

    "foreign" means a conditionalFormatting block already covers the Status
    column and is not the one this script writes — a human painted that column,
    and their rules are not ours to replace. Reported, never overwritten.
    """
    if dxf_count(parts) < MIN_DXFS:
        return CF_NO_DXFS
    col = att.headers["Status"]
    want = _cf_signature(_build_cf(att, priority=1))
    for el in _cf_blocks(att):
        cols = {col_of(end) for end in (el.get("sqref") or "").split(":")}
        if cols != {col}:
            continue
        # Compare the rules, not the priority — a later block insertion may
        # renumber priorities without changing what the rules mean.
        return CF_OK if _cf_signature(el)[1] == want[1] else CF_FOREIGN
    return CF_DUE


def _build_cf(att, priority, last_row=1000):
    """The <conditionalFormatting> element for the Status column."""
    col = att.headers["Status"]
    el = ET.Element(X + "conditionalFormatting", {
        "sqref": "%s:%s" % (cell_ref(col, 2), cell_ref(col, last_row))})
    for i, (value, dxf) in enumerate(status_cf_rules()):
        rule = ET.SubElement(el, X + "cfRule", {
            "type": "cellIs", "dxfId": str(dxf),
            "priority": str(priority + i), "operator": "equal"})
        ET.SubElement(rule, X + "formula").text = '"%s"' % value
    return el


#: Elements that must follow <dataValidations>, per the CT_Worksheet sequence.
#: A new block is inserted before the first of these that the sheet actually
#: has; if it has none, appending at the end is correct.
AFTER_DATA_VALIDATIONS = ("hyperlinks", "printOptions", "pageMargins", "pageSetup",
                          "headerFooter", "rowBreaks", "colBreaks", "drawing",
                          "legacyDrawing", "tableParts", "extLst")
#: ...and the same for <conditionalFormatting>, which precedes dataValidations.
AFTER_CONDITIONAL_FORMATTING = ("dataValidations",) + AFTER_DATA_VALIDATIONS


def _insert_at(att, after_tags):
    """Index of the first child in `after_tags`, else the end of the element."""
    kids = list(att.root)
    tags = {X + t for t in after_tags}
    return next((i for i, k in enumerate(kids) if k.tag in tags), len(kids))


def apply_cf(att):
    """Insert the Status colour block at a schema-legal position.

    CT_Worksheet fixes the child order, and `conditionalFormatting` must come
    AFTER sheetData and BEFORE dataValidations. Appending to the end puts it
    after pageMargins/pageSetup and Excel reports the file as corrupt — so the
    insertion point is computed, never assumed.
    """
    kids = list(att.root)
    existing = _cf_blocks(att)
    priority = max([int(r.get("priority", 0)) for el in existing for r in el]
                   + [0]) + 1
    el = _build_cf(att, priority)
    at = (kids.index(existing[-1]) + 1 if existing
          else _insert_at(att, AFTER_CONDITIONAL_FORMATTING))
    att.root.insert(at, el)


# ---------------------------------------------------------------------------
# The Guide tab
# ---------------------------------------------------------------------------
def guide_parts(parts):
    """The zip parts that can hold Guide content, in search order.

    Two storage forms are in the estate, and only one of them is the sheet:
    most workbooks write the Guide's prose as INLINE strings in its own part,
    but the older ones (Austin, Dallas, Kampala, London, Tatooine as of
    2026-08-25) put every string in the shared table. Searching only the sheet
    part found their `<f>` formulas and none of their text.
    """
    out = []
    part = sheet_part(parts, "Guide")
    if part:
        out.append(part)
    if "xl/sharedStrings.xml" in parts:
        out.append("xl/sharedStrings.xml")
    return out


def _both_quotings(old, new):
    """One replacement in both spellings a workbook may use for `"`.

    The same trap the dataValidation reader documents: the template writes
    `"…"` and the older workbooks write `&quot;…&quot;`. Matching only the
    first reported five chapters' dashboards as unrecognised while they sat
    there still counting the Status column.
    """
    return [(old, new),
            (old.replace('"', "&quot;"), new.replace('"', "&quot;"))]


def _letter(headers, header):
    """The column letter `header` currently occupies."""
    return re.sub(r"\d+$", "", cell_ref(headers[header], 1))


def guide_edits(headers):
    """[[(old, new), ...]] — one inner list of interchangeable spellings per
    edit, for the layout `headers` describes.

    Every letter in the REPLACEMENT is derived from the live header map, never
    hardcoded. migrate_column_order.py moves `Interested in` from L to D and
    shifts six other columns, so a hardcoded target would stop matching the
    moment that ran — this script would then report three unrecognised Guide
    formulas on all 83 chapters, forever, about a Guide that is perfectly fine.
    The OLD text stays hardcoded because it is history: it is the one spelling
    the shipped template ever had.
    """
    L = _letter(headers, NEW_COLUMN)
    company = _letter(headers, "Company")
    title = _letter(headers, "Role / title")
    what = _letter(headers, "What brings you here?")
    name = _letter(headers, "Full name")
    signal = _letter(headers, "Signal")
    notes = _letter(headers, "Notes (CRM)")
    return [
        # The dashboard counted the Status column, so both tiles read 0 for
        # every chapter the moment roles left it. Wildcards because a cell can
        # hold "Organizer/Speaker".
        _both_quotings('<f>COUNTIF(Attendees!D2:D1000,"Speaker")</f>',
                       '<f>COUNTIF(Attendees!%s2:%s1000,"*Speaker*")</f>' % (L, L)),
        _both_quotings('<f>COUNTIF(Attendees!D2:D1000,"Organizer")</f>',
                       '<f>COUNTIF(Attendees!%s2:%s1000,"*Organizer*")</f>' % (L, L)),
        # The live-list FILTER already pointed at L, which held nothing on the
        # 11-column layout: it has been returning one blank column since the
        # columns were last renumbered. Repointed at what it plainly meant —
        # who they are, what they want, where they work, why they came. No
        # quote spelling to vary: the match stops before the conditions.
        [("=FILTER({Attendees!A2:A, Attendees!I2:I, Attendees!K2:K, Attendees!L2:L, "
          "Attendees!B2:B, Attendees!E2:E}",
          "=FILTER({Attendees!%s2:%s, Attendees!%s2:%s, Attendees!%s2:%s, "
          "Attendees!%s2:%s, Attendees!%s2:%s, Attendees!%s2:%s, Attendees!%s2:%s}"
          % (name, name, L, L, company, company, title, title, what, what,
             signal, signal, notes, notes))],
    ]


def plan_guide(parts, headers):
    """([(part, old, new)] still to apply, [the old text of each edit not found]).

    Exact-match replacement, not a regex: the Guide is prose and seven
    formulas, parsed nowhere, so a workbook whose Guide someone edited reports
    the miss instead of having a pattern rewrite something it did not
    understand.
    """
    raws = {p: parts[p].decode("utf-8", "replace") for p in guide_parts(parts)}
    todo, missing = [], []
    for variants in guide_edits(headers):
        if any(new in raw for _, new in variants for raw in raws.values()):
            continue                                   # already migrated
        hit = next(((p, old, new) for old, new in variants
                    for p, raw in raws.items() if old in raw), None)
        if hit:
            todo.append(hit)
        else:
            missing.append(variants[0][0])
    return todo, missing


def apply_guide(parts, todo):
    for part, old, new in todo:
        parts[part] = parts[part].decode("utf-8", "replace").replace(old, new).encode()


# ---------------------------------------------------------------------------
# Per-workbook driver
# ---------------------------------------------------------------------------
def open_crm(folder, workdir):
    """Download a chapter's CRM and parse its Attendees tab, PRE-split-tolerant.

    Mirrors sync_crm.open_crm, including its everything-is-guarded rule: a
    truncated download raises zipfile.BadZipFile, a missing rels part KeyError,
    and ET.ParseError is a SyntaxError, not a ValueError — catching only
    ValueError once turned one bad workbook into a traceback that replaced the
    whole run's summary.
    """
    crm, why = find_crm(folder["id"])
    if crm is None:
        return None, why
    path = os.path.join(workdir, "%s.xlsx" % re.sub(r"[^\w.-]", "_", folder["name"]))
    try:
        names, parts = load_parts(download(crm["id"], path))
        part = sheet_part(parts, CRM_SHEET)
        if part is None:
            return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
        att = Attendees(parts, part, require=PRE_SPLIT_HEADERS)
    except Exception as e:
        return None, "%s: %s: %s" % (crm["name"], type(e).__name__, e)
    return {"folder": folder, "crm": crm, "names": names, "parts": parts,
            "part": part, "att": att, "path": path}, None


def plan(book):
    """Everything due on one workbook, or None when it is already split."""
    att = book["att"]
    dv_due, dv_blocked = dv_plan(att)
    p = {"add_column": NEW_COLUMN not in att.headers,
         "rows": plan_workbook(att),
         "dv": dv_due, "dv_blocked": dv_blocked}
    # The new column's index is only known after add_column would run, and the
    # Guide formulas name it — so predict it the same way add_column picks it.
    p["role_col"] = (att.headers[NEW_COLUMN] if not p["add_column"]
                     else max(att.headers.values()) + 1)
    # The header map AS IT WILL BE once add_column has run — the Guide formulas
    # name the new column, and its index is only known after that.
    p["guide"], p["guide_missing"] = plan_guide(
        book["parts"], dict(att.headers, **{NEW_COLUMN: p["role_col"]}))
    p["cf"] = cf_plan(att, book["parts"])
    if p["cf"] not in CF_STATES:
        raise ValueError("cf_plan returned %r, which is not one of %s — a new "
                         "state must be classified as pending or terminal, or "
                         "the chapter is silently skipped by BOTH the work "
                         "gate and the gap report." % (p["cf"], sorted(CF_STATES)))
    p["any"] = bool(p["add_column"] or p["rows"] or p["dv"] or p["guide"]
                    or p["cf"] == CF_DUE)
    return p


def apply_plan(book, p):
    """Apply `plan` to the workbook and return its new bytes."""
    att = book["att"]
    col = add_column(att)
    widen_column(att, col)
    for op in p["rows"]:
        for header, value in op["sets"].items():
            att.write(op["rownum"], header, value)
    refused = apply_dropdowns(att)
    if p["cf"] == CF_DUE:
        apply_cf(att)
    att.serialize()
    if p["guide"]:
        apply_guide(book["parts"], p["guide"])
    return save_parts(book["names"], book["parts"]), refused


def describe(p):
    """The one-line summary of a plan, for the report."""
    bits = []
    if p["add_column"]:
        bits.append("+%r column" % NEW_COLUMN)
    if p["rows"]:
        # A relocated status and a BLANKED one are different events and used to
        # share one count. Blanking happens when a row's Status held a role but
        # its note could not be parsed: the role moves to its proper column and
        # no decision is invented. That is the documented policy, but an
        # operator approving a fleet-wide write deserves to know how many
        # people's decision state is being cleared.
        moved = sum(1 for o in p["rows"] if o["sets"].get("Status"))
        blanked = sum(1 for o in p["rows"]
                      if "Status" in o["sets"] and not o["sets"]["Status"])
        bits.append("%d row(s) backfilled (%d role(s) moved out of Status)"
                    % (len(p["rows"]), moved + blanked))
        if blanked:
            bits.append("%d Status(es) BLANKED — note unparsable, next sync decides"
                        % blanked)
    if p["dv"]:
        bits.append("dropdown(s): %s" % ", ".join(h for h, _ in p["dv"]))
    if p["guide"]:
        bits.append("%d Guide formula(s)" % len(p["guide"]))
    if p["cf"] == CF_DUE:
        bits.append("Status colour rules")
    return ", ".join(bits)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(args):
    workdir = tempfile.mkdtemp(prefix="aaif-split-")
    try:
        code = _run(args, workdir)
    finally:
        stranded = cleanup_workdir(workdir, keep_backups=False)
    return 1 if stranded else code


def _run(args, workdir):
    folders = list_chapter_folders()
    if args.city:
        want = fold_city(args.city)
        folders = [f for f in folders if fold_city(f["name"]) == want]
        if not folders:
            sys.exit("ABORT: no chapter folder matches %r." % args.city)
    print("Chapters: %d folder(s) in scope.\n" % len(folders))

    touched, skipped, guide_gaps, cf_gaps, dv_gaps = [], [], [], [], []
    for folder in folders:
        book, why = open_crm(folder, workdir)
        if book is None:
            skipped.append((folder["name"], why))
            print("  %-18s SKIPPED — %s" % (folder["name"], why))
            continue
        p = plan(book)
        if p["guide_missing"]:
            guide_gaps.append((folder["name"], len(p["guide_missing"])))
        if p["cf"] in CF_BLOCKED:
            cf_gaps.append((folder["name"], p["cf"]))
        if p["dv_blocked"]:
            dv_gaps.append((folder["name"], p["dv_blocked"]))
        if not p["any"]:
            continue
        print("  %-18s %s" % (folder["name"], describe(p)))
        touched.append({"book": book, "plan": p})

    if dv_gaps:
        print("\nDropdown(s) left alone — one validation spans several columns, so "
              "rewriting it would re-validate a neighbour. Split the range by hand:")
        for name, cols in dv_gaps:
            print("  %-28s %s" % (name, ", ".join(cols)))
    if cf_gaps:
        print("\nStatus colour rules NOT written:")
        for name, why in cf_gaps:
            # .get, not [why]: an unclassified state must not crash the report
            # halfway through a fleet sweep, after other chapters have printed.
            print("  %-28s %s" % (name, {
                CF_FOREIGN: "the Status column already carries conditional "
                            "formatting that does not match the rules this "
                            "script writes — left alone (if an earlier version "
                            "of this script painted it, the rule set has since "
                            "changed)",
                CF_NO_DXFS: "fewer than %d <dxf> styles, so the rules would "
                            "reference a style that does not exist" % MIN_DXFS,
            }.get(why, why)))
    if guide_gaps:
        print("\nGuide tab not in the shipped shape — the formula(s) below were "
              "not found and are NOT rewritten; fix by hand or they keep counting "
              "the old column:")
        for name, n in guide_gaps:
            print("  %-28s %d formula(s) unrecognised" % (name, n))
    if skipped:
        print("\nChapters SKIPPED — not split, fix the workbook and re-run:")
        for name, why in skipped:
            print("  %-28s %s" % (name, why))

    # The gap lists are NOT pending work — nothing this script can do will
    # clear them — but they are also not nothing, and the last line printed is
    # what an operator and a CI log actually read. Saying "every chapter CRM
    # already has the split" directly under a list of chapters whose dashboards
    # still count the wrong column is how a known problem becomes an unknown one.
    gaps = len(guide_gaps) + len(cf_gaps) + len(dv_gaps)
    if not touched:
        if skipped or gaps:
            print("\nNo column, row, dropdown or colour work is due — but %d "
                  "chapter(s) above are NOT fully migrated (%d skipped, %d with "
                  "a gap this script cannot close). Nothing here will fix them."
                  % (len(skipped) + gaps, len(skipped), gaps))
            return 1
        print("\nNothing to do — every chapter CRM already has the split.")
        return 0
    if not args.write:
        print("\n%d workbook(s) would change. Re-run with --write to apply."
              % len(touched))
        return 2

    print("\nWriting %d workbook(s)..." % len(touched))
    backup_dir = backup_root("crm-split-before")
    written, changed, failed, refusals = [], [], [], []
    for t in touched:
        book, name = t["book"], t["book"]["folder"]["name"]
        try:
            with open(book["path"], "rb") as fh:
                planned = fh.read()
            current, drifted = fresh_if_unchanged(
                book["crm"]["id"], os.path.join(workdir, "reread.xlsx"), planned)
            with open(os.path.join(backup_dir, os.path.basename(book["path"])),
                      "wb") as fh:
                fh.write(current)
            if drifted:
                changed.append(name)
                print("  SKIPPED %s — workbook changed since the plan was built; "
                      "NOT written, re-run" % name, file=sys.stderr)
                continue
            raw, refused = apply_plan(book, t["plan"])
            if refused:
                refusals.append((name, refused))
            upload(book["crm"]["id"], book["path"], raw, XLSX)
            written.append(name)
            print("  wrote %s (%s)" % (name, book["crm"]["name"]))
        except Exception as e:            # one bad workbook must not abandon
            failed.append((name, str(e)))  # the other eighty
            print("  FAILED %s — %s" % (name, e), file=sys.stderr)
    print("Wrote %d workbook(s); pre-edit copies kept in %s (gitignored; delete "
          "once the write is confirmed good)" % (len(written), backup_dir))
    if refusals:
        # A REFUSAL is still true after a perfectly successful write, so it is
        # reported beside the verdict and never folded into it — counting it
        # would pin every later run to a permanent failure.
        print("\nDropdown(s) left alone — one validation spans several columns, "
              "so rewriting it would re-validate a neighbour. Fix by hand:")
        for name, cols in refusals:
            print("  %-28s %s" % (name, ", ".join(cols)))
    if changed:
        print("\n%d workbook(s) changed since the plan was built and were NOT "
              "written — re-run:\n  %s" % (len(changed), ", ".join(changed)))

    print("\nRe-verifying...")
    stale = []
    for t in touched:
        folder = t["book"]["folder"]
        if folder["name"] not in written:
            continue
        book, why = open_crm(folder, os.path.join(workdir, "verify"))
        if book is None:
            stale.append((folder["name"], "could not re-open: %s" % why))
            continue
        left = plan(book)
        if left["any"]:
            stale.append((folder["name"], describe(left) + " still pending"))
    if failed or stale or changed:
        if failed or stale:
            print("VERIFY FAILED:")
            for name, why in failed + stale:
                print("  %s — %s" % (name, why))
        return 1
    if gaps:
        print("Verified: a fresh read of every written workbook proposes zero "
              "changes — but %d chapter(s) carry a gap listed above that was "
              "NOT written and is therefore UNVERIFIED." % gaps)
    else:
        print("Verified: a fresh read of every written workbook proposes zero changes.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Split the chapter CRMs' Status column into Status + "
                    "'%s'." % NEW_COLUMN)
    ap.add_argument("--write", action="store_true",
                    help="apply the migration (default: report only)")
    ap.add_argument("--city", help="limit to one chapter folder")
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
