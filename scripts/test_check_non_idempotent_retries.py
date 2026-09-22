#!/usr/bin/env python3
"""check_non_idempotent_retries' own tests. Plain script; exit 1 on failure.

The guard exists because the rule it enforces was documentation for a while,
and documentation had four counterexamples in the tree. A guard that is itself
only asserted against a tree that already passes is the same mistake one level
up, so most of what is below is synthetic sources that MUST fail.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_non_idempotent_retries as chk  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def lines(src):
    return sorted(ln for ln, _what, _tier in chk.findings(src))


# --- Drive: the exact tier ---------------------------------------------------

check("an unguarded files.create is a finding",
      lines('gws_json("drive", "files", "create", body={})'), [1])
check("an unguarded files.copy is a finding",
      lines('gws_json("drive", "files", "copy", body={})'), [1])
check("the guard by attribute clears it",
      lines('gws_json("drive", "files", "create", retries=gwsmod.NO_RETRY)'), [])
check("the guard by bare name clears it",
      lines('gws_json("drive", "files", "create", retries=NO_RETRY)'), [])
check("a bare literal 1 clears it too — same behaviour, and the check "
      "enforces the property rather than the spelling",
      lines('gws_json("drive", "files", "create", retries=1)'), [])
check("the shared default does NOT clear it",
      lines('gws_json("drive", "files", "create", retries=gwsmod.RETRIES)'), [1])
check("some other number does not clear it",
      lines('gws_json("drive", "files", "create", retries=3)'), [1])
check("a read is not a finding",
      lines('gws_json("drive", "files", "list", params={})'), [])
check("an update by id is not a finding",
      lines('gws_json("drive", "files", "update", params={})'), [])
# The file helpers pass a whole argv list, because they need --upload/--output
# and a cwd. sync_badges.upload_new is that shape AND is a files.create, so a
# check that only understood positional verbs would have left it unguarded.
check("an unguarded argv-list create is a finding",
      lines('_gws(["gws", "drive", "files", "create", "--upload", p])'), [1])
check("...and the guard on that shape clears it",
      lines('_gws(["gws", "drive", "files", "create"], retries=gwsmod.NO_RETRY)'), [])
check("an argv-list read is not a finding",
      lines('_gws(["gws", "drive", "files", "get", "--output", p])'), [])
check("an argv list without the leading `gws` is read the same way",
      lines('run(["drive", "files", "copy"])'), [1])
check("a non-drive create is not a finding",
      lines('gws_json("slides", "presentations", "create")'), [])
check("both calls in one file are reported",
      lines('gws_json("drive", "files", "create")\ngws_json("drive", "files", "copy")'),
      [1, 2])
check("a mention in a comment is not a call",
      lines('# gws_json("drive", "files", "create") is guarded below\nx = 1'), [])
check("a mention in a docstring is not a call",
      lines('"""Calls drive files create."""'), [])


# --- Sheets: the coarse tier -------------------------------------------------

SHEETS = 'body={"requests": [{"appendDimension": {"length": 1}}]}'

check("a file naming appendDimension and never NO_RETRY is a finding",
      lines(SHEETS), [1])
check("insertDimension likewise", lines('r = {"insertDimension": {}}'), [1])
check("addSheet likewise", lines('r = {"addSheet": {}}'), [1])
check("duplicateSheet likewise", lines('r = {"duplicateSheet": {}}'), [1])
check("naming the guard anywhere in the file clears the coarse tier",
      lines(SHEETS + "\ngws(x, retries=gwsmod.NO_RETRY)"), [])
check("a plain repeatCell is not a finding",
      lines('r = {"repeatCell": {}}'), [])
check("the two tiers are reported together",
      lines('gws_json("drive", "files", "create")\nr = {"addSheet": {}}'), [1, 2])


# --- Scanning ----------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _write(tmp, name, body):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    bad = _write(tmp, "engine.py", 'gws_json("drive", "files", "create")')
    tst = _write(tmp, "test_engine.py", 'gws_json("drive", "files", "create")')
    check("a real finding is returned by scan", list(chk.scan([bad])), [bad])
    check("a test file is skipped — it issues no live write",
          list(chk.scan([tst])), [])
    chk.ALLOW[bad] = "synthetic"
    check("an allowlisted file is skipped", list(chk.scan([bad])), [])
    del chk.ALLOW[bad]

    broken = _write(tmp, "broken.py", "def f(:\n")
    _aborted = False
    try:
        chk.scan([broken])
    except SystemExit:
        _aborted = True
    check("an unparsable file aborts rather than reading as clean",
          _aborted, True)

# The tree itself. Kept last so a real regression reads as a regression rather
# than as this file being broken.
os.chdir(_ROOT)
check("the repo has no unguarded non-replayable call", chk.main(), 0)

if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("check_non_idempotent_retries: all checks passed")
