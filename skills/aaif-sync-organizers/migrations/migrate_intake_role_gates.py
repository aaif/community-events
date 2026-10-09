#!/usr/bin/env python3
"""One-shot: teach the Intake Ops role tabs the form's per-role Yes/No questions.

Until 2026-10-09 the intake form routed every applicant through ONE choice,
`What brings you here?` — organizer, speaker or venue — and the role tabs are
array-formula views of `Form Responses` filtered on it:
`FILTER(data, ISNUMBER(SEARCH("organizer", brand)))`. That day the choice was
replaced by one Yes/No question per role, so a single submission can apply for
several. Every row filed since leaves `What brings you here?` blank, matches no
SEARCH, and is on NO role tab — nothing errors, the applicant just never
reaches triage, a chapter CRM or a Drive grant. Keep the form closed until
`--write` has verified.

Two edits per formula:

1. **The filter.** A row is kept when EITHER the old answer matches OR the
   role's new question says Yes:

       Organizers!C2          SEARCH("organizer")  +  organize question = "Yes"
       Hosts!C2               SEARCH("venue")      +  venue question    = "Yes"
       Speakers!C2            SEARCH("speaker")    +  speak question    = "Yes"
       Organizers by City!A2  SEARCH("organizer")  +  organize question = "Yes"

   `Collaborators!C2` (a role tab created on 2026-10-09) already filters on its
   question alone — no older answer ever routed to it — so it gets only (2).
   `Organizers by City` is a summary view of accepted organizers, not a role tab.

2. **A guard.** Renaming a form question renames its sheet column, and the
   role tabs end in `IFERROR(...,"")`, so a missing column used to turn the
   whole tab blank — older rows included — with no error anywhere. Each formula
   is wrapped in `IF(ISNA(MATCH(<question>, 'Form Responses'!$1:$1, 0)),
   "MISSING FORM COLUMN: …", <formula>)`, so the tab shows that message in its
   first cell instead. clean.py and sync_crm also abort on a missing question.

The range (`$A$1:$CT$1`) is left alone. Google inserts a new question's column
INSIDE it and the reference grows to cover it (seen: $CO -> $CP -> $CT), and a
reference past the sheet's last column would #REF! the tab.

    python3 migrate_intake_role_gates.py           # report (default)
    python3 migrate_intake_role_gates.py --write   # apply, then verify

Idempotent: a formula already carrying both edits is reported as done; one
that references a question without the full new clause is refused, never
"done". After writing, each tab's row count must equal what Form Responses says
it should show (rows whose old answer or new question qualifies), so a run that
recovers nobody fails instead of reporting ok.

Exit codes: 0 nothing due, a report (whether or not changes are due), or a
--write that applied and verified; 1 a --write whose verification failed;
2 a formula of unexpected shape, a missing question column, or the Collaborate
marketing column out of order (fix by hand — this script will not guess). A gws
failure raises (exit 1 with a traceback); writes go one cell at a time, so some
may have landed, and a re-run finishes the rest.
"""

import argparse
import os
import sys
from collections import namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))
from sync_chapters import INTAKE_ID  # noqa: E402
from sync_crm import COLLABORATE_Q, ORGANIZE_Q, SPEAK_Q, VENUE_Q  # noqa: E402
from aaif_events import gws  # noqa: E402

BRAND = "What brings you here?"
GUARD_MESSAGE = "MISSING FORM COLUMN: "

#: kind "keep": a role tab, FILTERing `data` into `keep`. "mask": Organizers by
#: City, building a boolean `mask` over `fr`. "gated": already filters on its
#: question alone; gets only the guard.
Target = namedtuple("Target", "tab cell src word question kind")
TARGETS = (
    Target("Organizers", "C2", "data", "organizer", ORGANIZE_Q, "keep"),
    Target("Hosts", "C2", "data", "venue", VENUE_Q, "keep"),
    Target("Speakers", "C2", "data", "speaker", SPEAK_Q, "keep"),
    Target("Organizers by City", "A2", "fr", "organizer", ORGANIZE_Q, "mask"),
    Target("Collaborators", "C2", "data", None, COLLABORATE_Q, "gated"),
)
KINDS = ("keep", "mask", "gated")

#: The marketing question's title is on Form Responses twice: once in the
#: Collaborate section, once in the venue section (answered by every host).
#: `Collaborators!C2` takes the FIRST match, so the first must sit inside the
#: Collaborate block. These titles are unique and bound that block.
MARKETING_Q = "Will you run any marketing or sales motion after the event?"
COLLAB_BLOCK = ("What kind of partner are you?", "Organizer/Organization/Company name",
                "Organizer/Organization/Company Website", "Tell us about the collaboration",
                "Open-source or spec contributions")

#: Values a formula cell shows when it is broken.
ERROR_VALUES = ("#N/A", "#REF!", "#VALUE!", "#ERROR!", "#NAME?", "#DIV/0!", "#NUM!",
                "#SPILL!", "#NULL!")


class ShapeError(Exception):
    """The formula is not the one this script was written against."""


def clauses(t):
    """(old, new) text of the filter clause that changes, for keep/mask."""
    brand = 'brand,CHOOSECOLS(%s,MATCH("%s",hdr,0)),' % (t.src, BRAND)
    gate = 'gate,CHOOSECOLS(%s,MATCH("%s",hdr,0)),' % (t.src, t.question)
    old_test = 'ISNUMBER(SEARCH("%s",brand))' % t.word
    new_test = '(%s+(gate="Yes"))>0' % old_test
    tail = ("keep,FILTER(%s,%%s)," % t.src) if t.kind == "keep" else "mask,ARRAYFORMULA(%s),"
    return brand + tail % old_test, brand + gate + tail % new_test


def gated_clause(t):
    return 'gate,CHOOSECOLS(%s,MATCH("%s",hdr,0)),keep,FILTER(%s,gate="Yes"),' % (
        t.src, t.question, t.src)


def guard(question):
    """The prefix that turns a missing question column into a visible message."""
    return ('=IF(ISNA(MATCH("%s",\'Form Responses\'!$1:$1,0)),"%s%s — a form question '
            'was renamed; see migrate_intake_role_gates.py",' % (question, GUARD_MESSAGE, question))


def rewrite(formula, t):
    """The migrated formula, or None when it already carries both edits."""
    if t.kind not in KINDS:
        raise ValueError("unknown target kind %r" % t.kind)
    f = formula
    if t.kind == "gated":
        if gated_clause(t) not in f:
            raise ShapeError("expected the clause %s" % gated_clause(t))
    else:
        old, new = clauses(t)
        if new not in f:
            if f.count(old) != 1:
                raise ShapeError("expected the clause exactly once, found it %d time(s): %s"
                                 % (f.count(old), old))
            if "gate," in f:
                raise ShapeError("the formula already defines a LET name `gate`")
            f = f.replace(old, new)
    g = guard(t.question)
    if not f.startswith(g):
        if not f.startswith("=LET("):
            raise ShapeError("expected the formula to start with =LET( or the guard")
        f = g + f[1:] + ")"
    return None if f == formula else f


def expected_rows(t, hdr, rows):
    """How many rows the tab should show, by the formula's own test: SEARCH is
    case-blind and `gate="Yes"` compares case-blind without trimming. None for
    the grouped Organizers by City."""
    if t.kind == "mask":
        return None
    gi = hdr.index(t.question)
    bi = hdr.index(BRAND) if t.word else None
    get = lambda r, i: r[i] if i is not None and i < len(r) else ""
    return sum(1 for r in rows
               if (t.word and t.word in get(r, bi).lower()) or get(r, gi).lower() == "yes")


def marketing_in_collab_block(hdr):
    """True when the first marketing column sits inside the Collaborate block."""
    block = [hdr.index(h) for h in COLLAB_BLOCK if h in hdr]
    return bool(block) and MARKETING_Q in hdr and min(block) <= hdr.index(MARKETING_Q) <= max(block)


# ---- live I/O (the only functions that touch the sheet; tests replace them) ----
def read_formula(tab, cell):
    res = gws.json_out("sheets", "spreadsheets", "values", "get",
                       params={"spreadsheetId": INTAKE_ID, "range": "'%s'!%s" % (tab, cell),
                               "valueRenderOption": "FORMULA"},
                       what="%s!%s formula" % (tab, cell))
    return ((res.get("values") or [[""]])[0] or [""])[0]


def read_form_responses():
    """(header, data rows) of Form Responses, read wide, resolved by name."""
    grid = gws.values(INTAKE_ID, "'Form Responses'!A:EZ")
    return (grid[0], grid[1:]) if grid else ([], [])


def tab_column(t):
    """The tab's displayed column under its formula cell, from row 2 down."""
    col = t.cell.rstrip("0123456789")
    return [r[0] if r else "" for r in gws.values(INTAKE_ID, "'%s'!%s2:%s" % (t.tab, col, col))]


def errors_on(tab):
    return sum(1 for r in gws.values(INTAKE_ID, "'%s'!A1:Z5" % tab)
               for c in r if str(c).strip() in ERROR_VALUES)


def write_formula(t, formula):
    # values.update of one cell is replayable: re-sending it lands the same
    # formula, so the shared retry table is safe here.
    gws.json_out("sheets", "spreadsheets", "values", "update",
                 params={"spreadsheetId": INTAKE_ID, "range": "'%s'!%s" % (t.tab, t.cell),
                         "valueInputOption": "USER_ENTERED"},
                 body={"values": [[formula]]}, what="%s!%s" % (t.tab, t.cell))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    args = ap.parse_args(argv)

    hdr, rows = read_form_responses()
    missing = sorted({t.question for t in TARGETS if t.question not in hdr}
                     | ({BRAND} - set(hdr)))
    if missing:
        print("STOP Form Responses has no column(s): %s" % ", ".join(missing))
        return 2
    if not marketing_in_collab_block(hdr):
        print("STOP the first %r column is not inside the Collaborate block, so "
              "Collaborators!C2 would show the venue answers" % MARKETING_Q)
        return 2

    plan = []
    for t in TARGETS:
        formula = read_formula(t.tab, t.cell)
        try:
            new = rewrite(formula, t)
        except ShapeError as exc:
            print("STOP %s!%s: %s" % (t.tab, t.cell, exc))
            return 2
        exp = expected_rows(t, hdr, rows)
        shows = sum(1 for v in tab_column(t) if v)
        counts = "shows %d" % shows + ("" if exp is None else ", should show %d" % exp)
        if new is None:
            print("done %s!%s (%s)" % (t.tab, t.cell, counts))
            continue
        print("due  %s!%s (%s)" % (t.tab, t.cell, counts))
        plan.append((t, new, shows))

    if not plan:
        print("\nNothing due.")
        return 0
    if not args.write:
        print("\n%d formula(s) would change. Re-run with --write to apply." % len(plan))
        return 0

    for t, new, _shows in plan:
        write_formula(t, new)

    failed = 0
    for t, _new, before in plan:
        got = read_formula(t.tab, t.cell)
        try:
            migrated = rewrite(got, t) is None
        except ShapeError:
            migrated = False
        column = [v for v in tab_column(t) if v]
        guarded = bool(column) and column[0].startswith(GUARD_MESSAGE)
        exp = expected_rows(t, hdr, rows)
        rows_ok = len(column) >= before if exp is None else len(column) == exp
        errs = errors_on(t.tab)
        ok = migrated and not guarded and errs == 0 and rows_ok
        failed += not ok
        print("%s %s!%s  rows %d -> %d%s, errors %d%s"
              % ("ok  " if ok else "FAIL", t.tab, t.cell, before, len(column),
                 "" if exp is None else " (expected %d)" % exp, errs,
                 "" if migrated else ", formula not as written"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
