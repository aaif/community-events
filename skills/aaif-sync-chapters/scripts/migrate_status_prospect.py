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

NOT in scope: the CRMs' free-text "Notes (CRM)" cells. Every row synced before
the rename carries an audit note reading "Intake: Organizer - New - <date>",
and those stay: the note records what the status WAS when the row was written,
sync_crm never overwrites a non-blank note, and rewriting history in a
human-editable column to match today's vocabulary is not this script's job.

House rules: report is the default and writes nothing; --write applies, then
re-downloads / re-reads and verifies, printing a Verified line. No member names
or emails on stdout — counts, tab names and file names only.

Exit codes: 0 everything already in sync; 2 changes proposed (or applied);
1 failure (including a failed verify or a skipped workbook).

A REFUSAL ("shaped in a way I will not rewrite": a range-backed list, an x14
validation, an EXACT() color rule) is reported as needs-a-human and is NEVER
folded into a verify verdict — it is still true after a perfectly successful
write, so counting it would pin every later run to a permanent failure.

Usage:
  python3 migrate_status_prospect.py            # report only, zero writes
  python3 migrate_status_prospect.py --city Boston   # scope Phase B to one CRM
  python3 migrate_status_prospect.py --write    # apply, then verify
  # --city scopes Phase B ONLY; Phase A is the whole spreadsheet, so with
  # --city it is reported but not written unless --include-intake is passed.
"""
import argparse
import copy
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import (INTAKE_ID, download, fold_city, fresh_if_unchanged,  # noqa: E402
                           get_values, gws_json, upload)
from sync_crm import (CRM_SHEET, ROLE_TABS, X, XLSX, cell_ref,  # noqa: E402
                      cell_text, col_of, find_crm,
                      list_chapter_folders, load_parts, register_namespaces,
                      save_parts, set_cell, shared_strings, sheet_part,
                      _XML_DECL)

OLD, NEW = "New", "Prospect"
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))

# The template workbooks must migrate too, or every future chapter/series is
# born with the legacy dropdown. Ids as declared by their own creation scripts
# (create_chapter.TEMPLATE_FOLDER / create_series.TEMPLATE_FOLDER — different
# skills, so the ids are restated rather than imported across skill folders).
# TemplateCity is itself a child of the Chapters parent and is deduped by id.
TEMPLATE_FOLDERS = (
    {"id": "1PHvEgqnHo0RrsFyA47O9iRJGaKehC8Eg", "name": "TemplateCity"},
    {"id": "1M15wzKvQqd_jQz5cG16NO_YcbWU3EH1j", "name": "TemplateSeries"},
)

# Excel stores some validations (lists over 255 chars, cross-sheet refs) in an
# extension block under a DIFFERENT namespace, invisible to a scan of the main
# one. Not migrated — but never skipped silently either: a Status-covering
# extension validation is refused so a human sees it.
X14 = "{http://schemas.microsoft.com/office/spreadsheetml/2009/9/main}"
XM = "{http://schemas.microsoft.com/office/excel/2006/main}"

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
# A column-A test for "New" written some other way: extra spacing, an absolute
# $A$2, a negated <> test ("highlight everything not yet triaged"), or EXACT().
# Not migrated silently: reported, so a human fixes it rather than the rule
# quietly going dead the way `=$A2="New"` just did.
CF_SUSPECT = re.compile(r'\$A\$?\d+\s*(?:=|<>)\s*"%s"|EXACT\s*\(\s*\$A' % OLD)


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
    """The migrated text of one How-to-use sentence: the FIRST "New" only.

    Raises on a sentence that does not contain it: returning the text unchanged
    would make plan_howto plan a self-write that can never converge, and the
    post-write verify would report "not migrated" forever without saying why.
    """
    if OLD not in old:
        raise ValueError("How-to-use sentence does not contain %r: %r"
                         % (OLD, old[:60]))
    return old.replace(OLD, NEW, 1)


def plan_howto(rows):
    """([(a1, raw_old, new)], [refusals]) for the How-to-use tab's status prose.

    `rows` is the tab as returned by get_values (ragged rows of strings). Each
    sentence is located by its text, not by a fixed A1 — the tab gets rows
    inserted as it is edited, and a coordinate would silently rewrite the wrong
    cell after that.

    Matching is on the STRIPPED text but the rewrite is applied to the cell's
    RAW text, so a sentence carrying leading whitespace keeps it: one of these
    lines is the head of an indented ASCII flow diagram, and writing back the
    flush-left canonical form would silently misalign it.

    A sentence found more than once is refused rather than half-migrated: the
    write would fix one copy per run, and the run would report failure on a
    tab that is arguably fine.
    """
    where = {}
    for i, row in enumerate(rows, start=1):
        for j, cell in enumerate(row):
            where.setdefault((cell or "").strip(), []).append(
                (cell_ref(j, i), cell))
    plans, refusals = [], []
    for old in HOWTO_TEXTS:
        new = howto_new_text(old)
        hits = where.get(old, [])
        if len(hits) > 1:
            refusals.append("the sentence %r appears %d times on the tab — "
                            "migrate the duplicates by hand"
                            % (_ellipsis(old), len(hits)))
            continue
        if hits:
            a1, raw = hits[0]
            plans.append((a1, raw, howto_new_text(raw)))
        elif new not in where:
            refusals.append("the sentence %r is on the tab in neither "
                            "spelling — it was reworded; migrate it by hand"
                            % _ellipsis(old))
    return plans, refusals


def _ellipsis(text, width=48):
    """Shorten a sentence for a refusal line."""
    return text[:width] + ("..." if len(text) > width else "")


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
    """0-based column indices covered by an sqref like 'D2:D1000 F3'.

    Absolute refs are normalised first: col_of stops at the first non-alpha
    character, so a leading "$" made it return -1 and the Status validation
    silently unmatchable — the workbook then reported "in sync" while keeping
    the legacy value in its dropdown forever.
    """
    cols = set()
    for ref in (sqref or "").replace("$", "").split():
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

    def signal_dv_text(self):
        """The Signal column's validation list as raw text, or None.

        Read before and after a write so "Signal's unrelated New survives" is
        an assertion the production run makes, not just one the tests make.
        """
        if self.signal_col is None:
            return None
        for dv in self.root.iter(X + "dataValidation"):
            if sqref_cols(dv.get("sqref")) == {self.signal_col}:
                f1 = dv.find(X + "formula1")
                return f1.text if f1 is not None else None
        return None

    def plan_validations(self):
        """([(dv_element, old_list, new_list)], [refusal_reason]).

        Only a validation whose sqref covers EXACTLY the Status column is
        edited. One that also spans the Signal column (whose list legitimately
        contains "New") or any other column is refused loudly — editing it
        would rewrite a list this migration has no business touching. The
        Signal column's own validation is never matched at all: the match is
        the header-located column, not the list's contents.
        """
        plans, refusals, saw = [], [], False
        for dv in self.root.iter(X + "dataValidation"):
            cols = sqref_cols(dv.get("sqref"))
            if self.status_col not in cols:
                continue
            saw = True
            extra = cols - {self.status_col}
            if extra:
                refusals.append(
                    "the Status validation (sqref %r) also covers other "
                    "column(s) — refusing to edit it" % dv.get("sqref"))
                continue
            f1 = dv.find(X + "formula1")
            text = f1.text if f1 is not None else None
            values = dv_list(text)
            if values is None:
                # A list backed by a range or a defined name (=Lists!$A$1:$A$9),
                # or no formula1 at all. Skipping it silently would report the
                # workbook "in sync" while its dropdown still offers the legacy
                # value — and the post-write verify, which re-runs THIS
                # function, would agree. Reported, never guessed at.
                refusals.append(
                    "the Status validation (sqref %r) is not a literal list "
                    "(formula1=%r) — migrate it by hand"
                    % (dv.get("sqref"), text))
                continue
            new = migrate_list(values)
            if new is not None:
                plans.append((dv, values, new))
        x14 = self.x14_refusals()
        if not saw and not x14:
            # sync_crm's Host patch reports this state as "absent" rather than
            # guessing at it; the same discipline applies here, or a workbook
            # with no Status list reads as a migrated one.
            refusals.append("no data validation covers the Status column "
                            "(header column %d) — reported, not guessed at"
                            % self.status_col)
        refusals.extend(x14)
        return plans, refusals

    def x14_refusals(self):
        """Refusals for Status validations hidden in the x14 extension block.

        This script does not rewrite them — the point is that they are SEEN.
        Matched by the same rule as the main-namespace ones: the sqref covers
        the header-located Status column.
        """
        out = []
        for dv in self.root.iter(X14 + "dataValidation"):
            ref = dv.find(XM + "sqref")
            text = ref.text if ref is not None else None
            if self.status_col in sqref_cols(text):
                out.append("an x14 extension validation covers the Status "
                           "column (sqref %r) — this script does not rewrite "
                           "those; migrate it by hand" % text)
        return out

    def apply(self, cell_rows, dv_plans):
        """Rewrite the planned cells and lists, re-serialize this ONE part.

        The plan must have come from THIS instance: dv_plans hold live Element
        handles into self.root, so a plan from another workbook would edit a
        detached tree — no error, no effect, and the run would print "wrote".
        """
        unknown = [r for r in cell_rows if r not in self.rows]
        if unknown:
            raise ValueError("%s: plan names row(s) %s that are not in this "
                             "sheet — plan/instance mismatch"
                             % (self.part_name, unknown[:5]))
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
def _scrubbed_env(**extra):
    """os.environ minus the Slack/Luma secrets (plus `extra`), for every
    subprocess: git needs none of them, and a crash dump must not leak one.
    Local copy — this script stays standalone (gws itself goes through
    sync_chapters, which scrubs the same way)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("AAIF_SLACK_") and k.endswith("_TOKEN")) and k != "LUMA_API_KEY"}
    env.update(extra)
    return env


def assert_git_safe(path):
    """Refuse a backup/work path git would ever pick up (this repo is public).

    mkdtemp lands outside any checkout, so the common case is the cheap one; a
    path inside a git work tree must be ignored there AND untracked. Inlined
    (like aaif-backup's assert_dest_git_safe, whose three checks this mirrors)
    rather than imported from lib/, so this skill keeps working without the
    library checkout.

    Only git's own "not a git repository" answer means outside-a-repo. Any
    other failure — dubious ownership (exit 128), a corrupt .git, git missing
    from PATH — ABORTS: mapping it to "safe" would silently disengage the PII
    guard on a directory about to hold ~84 workbooks of real names and emails.
    """
    try:
        probe = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            env=_scrubbed_env(LC_ALL="C", LANG="C"))
    except FileNotFoundError:
        sys.exit("ABORT: git is not installed, so this cannot verify that %s "
                 "is outside the repo. The working copies hold every "
                 "organizer's name and email." % path)
    if probe.returncode != 0:
        stderr = (probe.stderr or "").strip()
        if "not a git repository" in stderr.lower():
            return                       # outside any repo — nothing to leak into
        sys.exit("ABORT: `git rev-parse` failed in %s (exit %d: %s), so this "
                 "cannot verify the path is safe. The working copies hold "
                 "every organizer's name and email."
                 % (path, probe.returncode, stderr[:200]))
    root = probe.stdout.strip()
    # Probe through a CHILD: check-ignore answers differently for a bare
    # directory name, and this works before the child exists.
    ignored = subprocess.run(
        ["git", "-C", root, "check-ignore", "-q", os.path.join(path, "probe")],
        capture_output=True, env=_scrubbed_env()).returncode == 0
    # .gitignore has no effect on already-tracked files, so a path committed
    # before the rules landed still rides along on `git add -A` while
    # check-ignore reports it ignored.
    tracked = subprocess.run(
        ["git", "-C", root, "ls-files", "--error-unmatch", path],
        capture_output=True, env=_scrubbed_env()).returncode == 0
    if tracked:
        sys.exit("ABORT: %s already holds files TRACKED by the repo at %s — "
                 "git rm --cached them first. The working copies hold every "
                 "organizer's name and email." % (path, root))
    if not ignored:
        sys.exit("ABORT: %s is inside the git work tree %s and not gitignored "
                 "— refusing to write member data there." % (path, root))


# ----------------------------------------------------------------------------
# Phase A — the intake role tabs
# ----------------------------------------------------------------------------
def grid_rule_plan(rows, start, probe_rows):
    """Pure planner for the Status dropdown: (rule, runs, refusals).

    `rows` is the probed rowData for column A (each entry a dict, possibly
    empty). `runs` are 0-based [first, last+1] spans of the rows that actually
    carry a ONE_OF_LIST offering the legacy value — CONTIGUOUS RUNS, never the
    min..max hull: the rows between two covered blocks may carry a different
    validation, or deliberately none, and blanketing the hull would overwrite
    them with the Status rule.

    Refusals cover the three ways this read can be lying:
      * a covered row sits at the last probed row — the rule very likely runs
        past the window, and every verify re-reads through the SAME window, so
        the leftover would never be seen;
      * the covered rows carry more than one distinct list — one of them would
        silently win for all of them;
      * column A carries no ONE_OF_LIST at all, which is NOT the same fact as
        "already migrated": a deleted Status dropdown must not read as success.
    """
    covered, lists, rule = [], [], None
    saw_list = False
    for i, rd in enumerate(rows):
        vals = rd.get("values") or [{}]
        dv = vals[0].get("dataValidation") or {}
        cond = dv.get("condition") or {}
        if cond.get("type") != "ONE_OF_LIST":
            continue
        saw_list = True
        listed = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
        if OLD in listed:
            covered.append(start + i)
            if listed not in lists:
                lists.append(listed)
            rule = rule or dv
    refusals = []
    if not saw_list:
        refusals.append("column A carries no ONE_OF_LIST validation at all in "
                        "the first %d rows — the Status dropdown is missing, "
                        "not migrated" % probe_rows)
    if len(lists) > 1:
        refusals.append("the Status dropdown offers %d DIFFERENT lists across "
                        "the covered rows — refusing to flatten them to one"
                        % len(lists))
    if covered and covered[-1] >= start + probe_rows - 1:
        refusals.append("the Status dropdown still offers %r at the last "
                        "probed row (%d) — it very likely runs past the probe "
                        "window, which every verify also reads through"
                        % (OLD, covered[-1] + 1))
    return rule, runs_of(covered), refusals


def intake_grid_rule(tab):
    """(sheetId, rule, runs, refusals) for the Status dropdown on `tab`.

    `rule` is the full dataValidation of the first covered cell; `runs` are
    0-based contiguous [first, last] row spans. `rule` is None (and `runs`
    empty) when no covered row was found — see grid_rule_plan for why that
    alone is not proof of a migrated tab. The sheetId is ALWAYS returned.
    """
    res = gws_json("sheets", "spreadsheets", "get", params={
        "spreadsheetId": INTAKE_ID,
        "ranges": ["'%s'!A1:A%d" % (tab, PROBE_ROWS)],
        "fields": "sheets(properties(sheetId,title),"
                  "data(startRow,rowData(values(dataValidation))))"})
    sheet = res["sheets"][0]
    data = (sheet.get("data") or [{}])[0]
    rule, runs, refusals = grid_rule_plan(data.get("rowData") or [],
                                          data.get("startRow", 0), PROBE_ROWS)
    return sheet["properties"]["sheetId"], rule, runs, refusals


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
        vals_any = [v.get("userEnteredValue", "") for v in
                    (cond.get("values") or [])]
        if cond.get("type") != "CUSTOM_FORMULA":
            # A TEXT_EQ "New" rule is the natural way to hand-make a status
            # color in the Sheets UI and is indistinguishable from the
            # custom-formula one on screen. This script only rewrites formulas,
            # so such a rule is REPORTED rather than skipped: skipped, it would
            # keep testing the retired value while verify (which re-runs this
            # function) called the tab clean.
            if OLD in vals_any:
                refusals.append(
                    "conditional-format rule %d is a %s condition on %r — this "
                    "script only rewrites CUSTOM_FORMULA rules; migrate it by "
                    "hand" % (i, cond.get("type"), OLD))
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
    """One tab's plan.

    Keys: tab, guard, cell_rows, gid, rule, dv_runs, new_list, cf_gid,
    cf_plans, cf_refusals, dv_refusals, col_a. `col_a` and the cf/dv plans are
    the snapshot the drift gate re-compares against before any write.
    Use tab_changes()/tab_actionable() rather than testing the keys by hand.
    """
    col_a = [r[0] if r else "" for r in
             get_values(INTAKE_ID, "'%s'!A1:A" % tab)]
    headers = get_values(INTAKE_ID, "'%s'!1:1" % tab)
    guard = intake_status_guard(headers[0] if headers else [])
    plan = {"tab": tab, "guard": guard, "cell_rows": [], "gid": None,
            "rule": None, "dv_runs": [], "new_list": None, "cf_gid": None,
            "cf_plans": [], "cf_refusals": [], "dv_refusals": [],
            "col_a": col_a}
    if guard:
        return plan
    plan["cf_gid"], plan["cf_plans"], plan["cf_refusals"] = intake_cf_plans(tab)
    plan["cell_rows"] = plan_intake_cells(col_a)
    gid, rule, runs, dv_refusals = intake_grid_rule(tab)
    plan["gid"] = gid
    plan["dv_refusals"] = dv_refusals
    if rule is not None:
        listed = [v.get("userEnteredValue", "")
                  for v in rule["condition"].get("values", [])]
        plan.update(rule=rule, dv_runs=runs, new_list=migrate_list(listed))
    return plan


def tab_changes(plan):
    """How many changes this tab's plan would apply."""
    return (len(plan["cell_rows"]) + (1 if plan["new_list"] else 0)
            + len(plan["cf_plans"]))


def tab_actionable(plan):
    """Is there writable work here? ONE definition, used by the report count,
    the apply gate and the re-verify gate alike — three hand-copied copies of
    this expression would drift, and the one that drifts silently is the apply
    gate: the change is reported, never written, and the run still says
    "Verified"."""
    return not plan["guard"] and tab_changes(plan) > 0


def intake_tab_drifted(plan):
    """Why this tab must not be written NOW, or None.

    Phase A's plan is built before ~84 Drive round-trips, so minutes pass and a
    human edit in that window is normal — the same reasoning that put
    fresh_if_unchanged in front of every Phase B write. It matters more here,
    not less: the cell writes address ABSOLUTE ROW NUMBERS on a tab whose B+
    columns are ARRAYFORMULA mirrors, and the color rules address POSITIONAL
    INDEXES. A row inserted meanwhile stamps Prospect over a human's Accepted,
    and a rule added meanwhile makes updateConditionalFormatRule replace some
    other rule wholesale — and verify, which only asks whether anything still
    says "New", calls both clean.
    """
    tab = plan["tab"]
    col_a = [r[0] if r else "" for r in
             get_values(INTAKE_ID, "'%s'!A1:A" % tab)]
    if col_a != plan["col_a"]:
        return ("column A changed since the plan was built — NOT written, "
                "re-run")
    _gid, cf_now, _refusals = intake_cf_plans(tab)
    if [(i, old) for i, old, _r in cf_now] != [(i, old) for i, old, _r
                                               in plan["cf_plans"]]:
        return ("the conditional-format rules changed since the plan was "
                "built — NOT written, re-run")
    return None


def intake_tab_requests(plan):
    """The ONE batchUpdate body for a tab: dropdown rule, color rules, then
    the Status cell rewrites as updateCells (RAW strings).

    A single spreadsheets.batchUpdate is atomic on the sheet: no row can be
    inserted between the rule swap and the cell stamp, which is what the old
    three-call sequence (two batchUpdates + a values.batchUpdate) left open —
    intake_tab_drifted() had been checked minutes before the third call, and
    a row inserted in that window would shift every absolute row number and
    stamp Prospect over a human's Accepted. Pure: returns the request list.
    """
    reqs = []
    if plan["new_list"]:
        # An EXPLICIT full rule, over the CONTIGUOUS RUNS that actually carry
        # the legacy list — never the min..max hull, which would blanket the
        # Status rule over rows that deliberately carry a different validation
        # or none. Never a partial/empty rule either: gws drops empty request
        # objects, so anything less than the complete replacement can silently
        # no-op.
        rule = dict(plan["rule"])
        rule["condition"] = {"type": "ONE_OF_LIST",
                             "values": [{"userEnteredValue": v}
                                        for v in plan["new_list"]]}
        rule.setdefault("showCustomUi", True)
        reqs += [{"setDataValidation": {
            "range": {"sheetId": plan["gid"],
                      "startRowIndex": r0, "endRowIndex": r1 + 1,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "rule": rule}} for r0, r1 in plan["dv_runs"]]
    # Rules are replaced in place, by positional index — a rule replaced in
    # place never shifts the ones after it.
    reqs += [{"updateConditionalFormatRule": {
        "sheetId": plan["cf_gid"], "index": i, "rule": rule}}
        for i, _old, rule in plan["cf_plans"]]
    # updateCells with `fields: userEnteredValue` is the RAW-string write:
    # no formula parsing, and formatting/validation on the cell untouched.
    # Column A only (startColumnIndex 0..1) — B+ are ARRAYFORMULA mirrors.
    reqs += [{"updateCells": {
        "range": {"sheetId": plan["gid"],
                  "startRowIndex": a - 1, "endRowIndex": b,
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "rows": [{"values": [{"userEnteredValue": {"stringValue": NEW}}]}]
                * (b - a + 1),
        "fields": "userEnteredValue"}}
        for a, b in runs_of(plan["cell_rows"])]
    return reqs


def apply_intake_tab(plan):
    """Apply one tab's plan in ONE spreadsheets.batchUpdate (see
    intake_tab_requests). Caller must have cleared intake_tab_drifted() first."""
    reqs = intake_tab_requests(plan)
    if reqs:
        gws_json("sheets", "spreadsheets", "batchUpdate",
                 params={"spreadsheetId": INTAKE_ID},
                 body={"requests": reqs})


def verify_intake_tab(plan):
    """Re-read and prove the PLANNED work landed. Returns a failure or None.

    Positive, not merely absence-of-OLD: every planned cell must now read
    exactly NEW (a blanked or mis-addressed cell also stops saying "New"), and
    the re-read dropdown must offer exactly new_list (a clobbered or vanished
    validation also stops offering "New").

    Refusals are deliberately NOT part of this verdict. A refusal is a
    permanent won't-do — a rule shaped in a way this script never rewrites —
    so counting it here would pin every future run to exit 1 with a message
    claiming outstanding work that no run will ever do. They are reported at
    plan time instead.
    """
    tab = plan["tab"]
    col_a = [r[0] if r else "" for r in
             get_values(INTAKE_ID, "'%s'!A1:A" % tab)]

    def at(row):
        return (col_a[row - 1] if row - 1 < len(col_a) else "").strip()

    left = plan_intake_cells(col_a)
    if left:
        return "%s: %d Status cell(s) still read %r" % (tab, len(left), OLD)
    missed = [r for r in plan["cell_rows"] if at(r) != NEW]
    if missed:
        return ("%s: %d planned cell(s) do not read %r after the write (rows "
                "%s)" % (tab, len(missed), NEW, missed[:5]))
    _gid, rule, _runs, _refusals = intake_grid_rule(tab)
    if rule is not None:
        return "%s: the Status dropdown still offers %r" % (tab, OLD)
    if plan["new_list"]:
        got = intake_dropdown_values(tab)
        if got != plan["new_list"]:
            return ("%s: the Status dropdown reads %r after the write, not the "
                    "migrated list" % (tab, got))
    _cfgid, cf_plans, _cf_refusals = intake_cf_plans(tab)
    if cf_plans:
        return ("%s: %d conditional-format rule(s) still test %r"
                % (tab, len(cf_plans), OLD))
    return None


def intake_dropdown_values(tab):
    """The Status dropdown's list as it now reads, or None when it has none."""
    res = gws_json("sheets", "spreadsheets", "get", params={
        "spreadsheetId": INTAKE_ID,
        "ranges": ["'%s'!A2:A2" % tab],
        "fields": "sheets(data(rowData(values(dataValidation))))"})
    rows = ((res["sheets"][0].get("data") or [{}])[0].get("rowData") or [{}])
    dv = ((rows[0].get("values") or [{}])[0].get("dataValidation") or {})
    cond = dv.get("condition") or {}
    if cond.get("type") != "ONE_OF_LIST":
        return None
    return [v.get("userEnteredValue", "") for v in cond.get("values", [])]


# ----------------------------------------------------------------------------
# Phase B — the chapter CRMs
# ----------------------------------------------------------------------------
def crm_folders(city=None):
    """Chapter folders plus the two template folders, deduped by id.

    `city` filters AFTER the templates are added, so --city drops TemplateCity
    and TemplateSeries from the run too — scoping to one chapter means exactly
    one workbook, templates included.
    """
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
    """(book, None) or (None, why), where book is a dict with keys
    crm/names/parts/sheet/path — named rather than a positional tuple, whose
    members are only valid as a set and were being indexed as book[3] a hundred
    lines from where they are built."""
    crm, why = find_crm(folder["id"])
    if crm is None:
        return None, why
    # The Drive FILE ID is part of the basename: sanitizing the folder name
    # alone collides ("Washington DC" and "Washington, DC" both fold to
    # Washington_DC), and the second backup would overwrite the first
    # workbook's only pre-edit copy — after that workbook had been mutated.
    path = os.path.join(workdir, "%s-%s.xlsx"
                        % ("".join(ch if ch.isalnum() or ch in "._-" else "_"
                                   for ch in folder["name"]), crm["id"]))
    try:
        names, parts = load_parts(download(crm["id"], path))
        part = sheet_part(parts, CRM_SHEET)
        if part is None:
            return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
        sheet = CrmStatusSheet(parts, part)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as e:
        # STRUCTURAL failures only — a workbook this script cannot read. A
        # transport failure (the RuntimeError gws raises once its five retries
        # are spent, OSError, ...) must PROPAGATE: reported here it would be
        # indistinguishable from a corrupt workbook, when the right answer is
        # "the write may have landed, re-run to confirm" — this migration is
        # idempotent, so a re-run is always the correct remedy.
        return None, "%s: %s: %s" % (crm["name"], type(e).__name__, e)
    return {"crm": crm, "names": names, "parts": parts, "sheet": sheet,
            "path": path}, None


def plan_crm(sheet):
    """({cells, dvs}, [refusals]) for one opened workbook."""
    dvs, refusals = sheet.plan_validations()
    return {"cells": sheet.status_cells_holding_old(), "dvs": dvs}, refusals


def write_crm(folder, book, plan, workdir, backup_dir):
    """Apply + upload one workbook. Returns a failure string or None."""
    crm, path = book["crm"], book["path"]
    with open(path, "rb") as fh:
        planned = fh.read()
    fresh, drifted = fresh_if_unchanged(
        crm["id"], os.path.join(workdir, "reread.xlsx"), planned)
    with open(os.path.join(backup_dir, os.path.basename(path)), "wb") as fh:
        fh.write(fresh)
    if drifted:
        return ("%s changed since the plan was built — NOT written, re-run"
                % crm["name"])
    signal_before = book["sheet"].signal_dv_text()
    book["sheet"].apply(plan["cells"], plan["dvs"])
    upload(crm["id"], path, save_parts(book["names"], book["parts"]), XLSX)
    # Verify: a fresh download must show the PLANNED work done. Refusals are
    # excluded on purpose — a refusal is a permanent won't-do (a validation
    # spanning extra columns is refused by design and is still there after a
    # perfectly successful write), so counting it here would fail every future
    # run with a message claiming work no run will ever do.
    book2, why = open_crm_sheet(folder, os.path.join(workdir, "verify"))
    if book2 is None:
        return "%s: could not re-open after write: %s" % (crm["name"], why)
    plan2, _refusals2 = plan_crm(book2["sheet"])
    if plan2["cells"] or plan2["dvs"]:
        return ("%s: %d cell(s) / %d validation(s) still pending after write"
                % (crm["name"], len(plan2["cells"]), len(plan2["dvs"])))
    # The Signal column's own "New" is unrelated and must survive. The class
    # locates that column precisely so this can be PROVEN in production, not
    # only asserted in the tests.
    signal_after = book2["sheet"].signal_dv_text()
    if signal_after != signal_before:
        return ("%s: the Signal validation changed during the write (%r -> %r)"
                % (crm["name"], signal_before, signal_after))
    return None


# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------
def run(args):
    workdir = tempfile.mkdtemp(prefix="aaif-status-migrate-")
    assert_git_safe(workdir)
    try:
        code = _run(args, workdir)
    finally:
        # before/ lives under backup_root(), not here — the workdir itself
        # never survives, in either mode.
        stranded = cleanup_workdir(workdir, keep_backups=False)
    # A stranded working copy is member data on disk with nobody told; the
    # WARNING alone is invisible to a wrapper reading exit codes.
    return 1 if stranded else code


def backup_root():
    """`<repo>/backups/crm-status-before-<UTC stamp>/` — gitignored by
    `**/backups/*`, and assert_git_safe proves it for this checkout before
    a single workbook of member data lands there. Repo-local rather than
    $TMPDIR so the recovery copy is where an operator looks and survives a
    reboot; the operator deletes it once the write is confirmed good."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path = os.path.join(REPO, "backups", "crm-status-before-%s" % stamp)
    os.makedirs(path, mode=0o700, exist_ok=True)
    assert_git_safe(path)
    return path


def cleanup_workdir(workdir, keep_backups):
    """Delete the working copies; keep only the pre-edit backups on --write.

    What lands in workdir is not just before/: a downloaded working copy per
    chapter, reread.xlsx, and the whole verify/ subtree re-downloaded after
    each upload — three to four full copies of every CRM, i.e. the names and
    emails of the entire organizer base. Only before/ has recovery value, so
    only before/ survives, and a failed delete is REPORTED rather than
    swallowed: silence would leave member data on disk with nobody aware.
    """
    left = []
    for name in sorted(os.listdir(workdir)):
        if keep_backups and name == "before":
            continue
        target = os.path.join(workdir, name)
        shutil.rmtree(target, ignore_errors=True) if os.path.isdir(target) \
            else _unlink_quietly(target)
        if os.path.exists(target):
            left.append(target)
    if not keep_backups and not left:
        shutil.rmtree(workdir, ignore_errors=True)
        if not os.path.exists(workdir):
            return False
        left.append(workdir)
    if left:
        print("WARNING: could not delete %d path(s) holding member data — "
              "remove by hand: %s" % (len(left), ", ".join(left[:5])),
              file=sys.stderr)
    return bool(left)


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _run(args, workdir):
    proposed, failures, refused = 0, [], []

    def refuse(where, why, width):
        """A won't-do: reported once, here, and never folded into a verify
        verdict — it is still true after a perfectly successful write."""
        refused.append("%s: %s" % (where, why))
        print("  %-*s REFUSED — %s" % (width, where, why))

    print("Phase A — Intake Ops role tabs (%s):" % ", ".join(ROLE_TABS))
    tab_plans = []
    for tab in ROLE_TABS:
        plan = plan_intake_tab(tab)
        tab_plans.append(plan)
        if plan["guard"]:
            failures.append("%s: %s" % (tab, plan["guard"]))
            print("  %-10s REFUSED — %s" % (tab, plan["guard"]))
            continue
        for r in plan["cf_refusals"] + plan["dv_refusals"]:
            refuse(tab, r, 10)
        bits = []
        if plan["cell_rows"]:
            bits.append("%d Status cell(s) %r -> %r (column A only)"
                        % (len(plan["cell_rows"]), OLD, NEW))
        if plan["new_list"]:
            bits.append("dropdown list %r -> %r over row run(s) %s"
                        % (OLD, NEW, [[a + 1, b + 1] for a, b
                                      in plan["dv_runs"]]))
        for _i, old, _rule in plan["cf_plans"]:
            bits.append("color rule %s" % old)
        print("  %-10s %s" % (tab, "; ".join(bits) or "in sync"))
        proposed += tab_changes(plan)

    howto_plans, howto_refusals = intake_howto_plans()
    for r in howto_refusals:
        refuse(HOWTO_TAB, r, 10)
    print("  %-10s %s" % (HOWTO_TAB,
                          ("%d status sentence(s) %r -> %r at %s"
                           % (len(howto_plans), OLD, NEW,
                              ", ".join(a1 for a1, _o, _n in howto_plans)))
                          if howto_plans else "in sync"))
    proposed += len(howto_plans)

    print("\nPhase B — chapter CRMs:")
    backup_dir = (os.path.join(backup_root(), "before") if args.write
                  else os.path.join(workdir, "before"))
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    crm_plans = []
    for folder in crm_folders(args.city):
        book, why = open_crm_sheet(folder, workdir)
        if book is None:
            failures.append("%s: %s" % (folder["name"], why))
            print("  %-20s SKIPPED — %s" % (folder["name"], why))
            continue
        plan, refusals = plan_crm(book["sheet"])
        for r in refusals:
            refuse(folder["name"], r, 20)
        n_cells, n_dvs = len(plan["cells"]), len(plan["dvs"])
        if not n_cells and not n_dvs:
            print("  %-20s in sync" % folder["name"])
            continue
        bits = ([("%d Status cell(s) %r -> %r" % (n_cells, OLD, NEW))]
                if n_cells else []) \
            + ([('Status dropdown -%r' % OLD)] if n_dvs else [])
        print("  %-20s %s (%s)" % (folder["name"], ", ".join(bits),
                                   book["crm"]["name"]))
        proposed += n_cells + n_dvs
        crm_plans.append((folder, book, plan))

    # Phase A is the WHOLE intake spreadsheet, so --city (whose entire purpose
    # is limiting blast radius) must not drag it along silently: with --city,
    # Phase A is reported but written only on an explicit --include-intake.
    write_intake = args.write and (not args.city or args.include_intake)
    if args.write and args.city and not args.include_intake and (
            howto_plans or any(tab_actionable(p) for p in tab_plans)):
        print("\nNOTE: --city scopes Phase B only. The intake-wide Phase A "
              "changes above are REPORTED, not written — re-run with "
              "--include-intake to apply them.")

    if not proposed:
        print()
        for r in refused:
            print("  needs a human: %s" % r)
        if failures:
            print("%d tab(s)/workbook(s) could not be planned:" % len(failures))
            for f in failures:
                print("  %s" % f)
            return 1
        print("Nothing to do — %r is gone from every dropdown, cell, color "
              "rule and How-to-use sentence this script can read%s."
              % (OLD, " (see the refusals above)" if refused else ""))
        return 0 if not refused else 1

    if not args.write:
        print("\n%d change(s) proposed across %d tab(s) and %d workbook(s). "
              "Re-run with --write to apply."
              % (proposed,
                 sum(1 for p in tab_plans if tab_actionable(p))
                 + (1 if howto_plans else 0),
                 len(crm_plans)))
        for r in refused:
            print("  needs a human: %s" % r)
        return 1 if failures else 2

    print("\nApplying...")
    written_tabs = []
    for plan in tab_plans:
        if not (write_intake and tab_actionable(plan)):
            continue
        try:
            drift = intake_tab_drifted(plan)
            if drift:
                failures.append("%s: %s" % (plan["tab"], drift))
                print("  SKIPPED %s — %s" % (plan["tab"], drift),
                      file=sys.stderr)
                continue
            apply_intake_tab(plan)
            written_tabs.append(plan)
            left = [r for r in plan["cf_refusals"] + plan["dv_refusals"]]
            print("  %s %s%s"
                  % ("PARTIAL" if left else "wrote", plan["tab"],
                     " — applied %d change(s), %d item(s) still need a human"
                     % (tab_changes(plan), len(left)) if left else ""))
        except Exception as e:
            failures.append("%s: %s" % (plan["tab"], e))
            print("  FAILED %s — %s" % (plan["tab"], e), file=sys.stderr)
    if howto_plans and write_intake:
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
    print("Pre-edit workbook copies kept in %s (gitignored; delete once the "
          "write is confirmed good)" % backup_dir)

    print("\nRe-verifying the intake tabs...")
    for plan in written_tabs:
        bad = verify_intake_tab(plan)
        if bad:
            failures.append(bad)
    if howto_plans and write_intake:
        left, _still_refused = intake_howto_plans()
        if left:
            failures.append("%s: %d status sentence(s) not migrated"
                            % (HOWTO_TAB, len(left)))

    for r in refused:
        print("  needs a human: %s" % r)
    if failures:
        print("VERIFY FAILED / INCOMPLETE:")
        for f in failures:
            print("  %s" % f)
        return 1
    print("Verified: every planned change landed — fresh reads of each written "
          "tab and workbook propose zero further changes.")
    return 2   # changes were proposed AND applied — the shared 0/2/1 contract


def main():
    ap = argparse.ArgumentParser(
        description='Retire the legacy intake status "New" in favor of "Prospect".')
    ap.add_argument("--write", action="store_true",
                    help="apply the proposed changes (default: report only)")
    ap.add_argument("--city",
                    help="limit Phase B to one chapter folder; Phase A is then "
                         "reported but not written (see --include-intake)")
    ap.add_argument("--include-intake", action="store_true",
                    help="with --city --write, also apply the intake-wide "
                         "Phase A changes")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
