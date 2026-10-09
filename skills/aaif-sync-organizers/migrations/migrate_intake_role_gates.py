#!/usr/bin/env python3
"""One-shot: teach the Intake Ops role tabs the form's per-role Yes/No questions.

Until 2026-10-09 the intake form routed every applicant through ONE choice,
`What brings you here?` — organizer, speaker or venue — and the role tabs are
array-formula views of `Form Responses` filtered on it:
`FILTER(data, ISNUMBER(SEARCH("organizer", brand)))`. That day the choice was
replaced by one Yes/No question per role, so a single submission can apply for
several. Every row filed since leaves `What brings you here?` blank, matches no
SEARCH, and is on NO role tab — nothing errors, the applicant just never
reaches triage, a chapter CRM or a Drive grant. The form stays closed until
this has run.

This rewrites exactly one clause in four formulas so a row is kept when EITHER
the old answer matches OR the role's new question says Yes:

    Organizers!C2          SEARCH("organizer")  +  organize question = "Yes"
    Hosts!C2               SEARCH("venue")      +  venue question    = "Yes"
    Speakers!C2            SEARCH("speaker")    +  speak question    = "Yes"
    Organizers by City!A2  SEARCH("organizer")  +  organize question = "Yes"

Rows from before the switch still match on the old column, which stays on the
sheet; nothing else in the formulas changes. The Collaborate question has no
role tab and gets none here — it never had one.

No IFNA around the new MATCH, on purpose: a renamed question must break the tab
LOUDLY (#N/A where the table was) rather than quietly drop everyone who answered
it, which is the failure this script exists to fix. Renaming a form question
renames its column on the sheet.

The range (`$A$1:$CT$1`) is left alone. Google inserts a new question's column
INSIDE it and the reference grows to cover it (seen: $CO -> $CP -> $CT), and a
reference past the sheet's last column would #REF! the tab.

    python3 migrate_intake_role_gates.py           # report (default)
    python3 migrate_intake_role_gates.py --write   # apply, then verify

Idempotent: a formula that already reads its question is reported as done and
not rewritten. Exit codes: 0 nothing due / applied and verified; 1 a --write
whose verification failed; 2 a formula that no longer has the expected shape
(fix by hand — this script will not guess).
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))
from sync_chapters import INTAKE_ID  # noqa: E402
from aaif_events import gws  # noqa: E402

BRAND = "What brings you here?"
ORGANIZE = "Would you like to help organize local AAIF events?"
SPEAK = "Would you like to speak at an AAIF event?"
VENUE = "Would you like to offer a venue for AAIF events?"

#: (tab, cell, LET name of the data range, SEARCH word, question, LET name of the test)
TARGETS = (
    ("Organizers", "C2", "data", "organizer", ORGANIZE, "keep"),
    ("Hosts", "C2", "data", "venue", VENUE, "keep"),
    ("Speakers", "C2", "data", "speaker", SPEAK, "keep"),
    ("Organizers by City", "A2", "fr", "organizer", ORGANIZE, "mask"),
)


class ShapeError(Exception):
    """The formula is not the one this script was written against."""


def clauses(src, word, question, kind):
    """(old, new) text of the one clause that changes."""
    brand = 'brand,CHOOSECOLS(%s,MATCH("%s",hdr,0)),' % (src, BRAND)
    gate = 'gate,CHOOSECOLS(%s,MATCH("%s",hdr,0)),' % (src, question)
    old_test = 'ISNUMBER(SEARCH("%s",brand))' % word
    new_test = '(%s+(gate="Yes"))>0' % old_test
    if kind == "keep":
        tail = "keep,FILTER(%s,%%s)," % src
    else:
        tail = "mask,ARRAYFORMULA(%s),"
    return brand + tail % old_test, brand + gate + tail % new_test


def rewrite(formula, src, word, question, kind):
    """The migrated formula, or None when it is already migrated."""
    if 'MATCH("%s",hdr,0)' % question in formula:
        return None
    old, new = clauses(src, word, question, kind)
    if formula.count(old) != 1:
        raise ShapeError("expected the clause exactly once, found it %d time(s): %s"
                         % (formula.count(old), old))
    if "gate," in formula:
        raise ShapeError("the formula already defines a LET name `gate`")
    return formula.replace(old, new)


def read_formula(tab, cell):
    res = gws.json_out("sheets", "spreadsheets", "values", "get",
                       params={"spreadsheetId": INTAKE_ID, "range": "'%s'!%s" % (tab, cell),
                               "valueRenderOption": "FORMULA"},
                       what="%s!%s formula" % (tab, cell))
    return ((res.get("values") or [[""]])[0] or [""])[0]


def rows_on(tab):
    """Data rows a role tab currently shows (column C/A non-empty)."""
    col = "A" if tab == "Organizers by City" else "C"
    return sum(1 for r in gws.values(INTAKE_ID, "'%s'!%s2:%s" % (tab, col, col)) if r and r[0])


def errors_on(tab):
    return sum(1 for r in gws.values(INTAKE_ID, "'%s'!A1:Z5" % tab)
               for c in r if str(c).startswith("#"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    args = ap.parse_args()

    plan = []
    for tab, cell, src, word, question, kind in TARGETS:
        formula = read_formula(tab, cell)
        try:
            new = rewrite(formula, src, word, question, kind)
        except ShapeError as exc:
            print("STOP %s!%s: %s" % (tab, cell, exc))
            return 2
        if new is None:
            print("done %s!%s already reads %r" % (tab, cell, question))
            continue
        old_c, new_c = clauses(src, word, question, kind)
        print("due  %s!%s\n       - %s\n       + %s" % (tab, cell, old_c, new_c))
        plan.append((tab, cell, new))

    if not plan:
        print("\nNothing due.")
        return 0
    if not args.write:
        print("\n%d formula(s) would change. Re-run with --write to apply." % len(plan))
        return 0

    before = {tab: rows_on(tab) for tab, _c, _n in plan}
    for tab, cell, new in plan:
        # values.update of one cell is replayable: re-sending it lands the same
        # formula, so the shared retry table is safe here.
        gws.json_out("sheets", "spreadsheets", "values", "update",
                     params={"spreadsheetId": INTAKE_ID, "range": "'%s'!%s" % (tab, cell),
                             "valueInputOption": "USER_ENTERED"},
                     body={"values": [[new]]}, what="%s!%s" % (tab, cell))

    failed = 0
    for tab, cell, new in plan:
        got, rows, errs = read_formula(tab, cell), rows_on(tab), errors_on(tab)
        ok = got == new and errs == 0 and rows >= before[tab]
        failed += not ok
        print("%s %s!%s  rows %d -> %d, errors %d"
              % ("ok  " if ok else "FAIL", tab, cell, before[tab], rows, errs))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
