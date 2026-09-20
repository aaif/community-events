#!/usr/bin/env python3
"""install_ops_notes' placement. Plain script; exit 1 on failure. No network.

The one thing that must hold: the header goes AFTER the last existing column,
never into a gap and never onto an existing header — a tab whose spill
formula occupies the middle columns has literal ops columns to the right,
and that is the only place a typed value is safe.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install_ops_notes as ion  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


# Synthetic headers only — the shape of a role tab, none of its content.
ROLE = ["Status", "Full name", "Timestamp", "Name", "Email", "Chapter",
        "Reviewed by", "Decision notes", "Issues"]

check("a missing column goes after the last header",
      ion.plan(ROLE, grid_cols=30), {"present": False, "col": 10, "widen": False})
check("a grid exactly as wide as its headers is widened first",
      ion.plan(ROLE, grid_cols=9)["widen"], True)
check("a present column is found by name, wherever it sits",
      ion.plan(ROLE[:3] + [ion.HEADER] + ROLE[3:], 30),
      {"present": True, "col": 4, "widen": False})
check("header whitespace does not hide a present column",
      ion.plan(ROLE + [" Ops Notes "], 30)["present"], True)
check("a trailing blank header still counts as a column",
      ion.plan(ROLE + [""], 30)["col"], 11)
check("A1 letters", [ion.col_letter(n) for n in (1, 26, 27, 52, 53)],
      ["A", "Z", "AA", "AZ", "BA"])

_dup = []
try:
    ion.plan(ROLE + [ion.HEADER, ion.HEADER], 30)
except SystemExit as e:
    _dup.append(str(e.code)[:5])
check("a duplicated header aborts rather than picking one", _dup, ["ABORT"])

# Every target is a (spreadsheet, tab) pair the estate really has; the sheet
# ids are the two this repo already names everywhere, and the tabs are the
# three role tabs plus the feed. Pinned so a typo cannot install the column
# on the wrong tab.
check("targets", [t for _s, t in ion.TARGETS],
      ["Organizers", "Hosts", "Speakers", "Chapters & Teams"])

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("install_ops_notes: all checks passed")
