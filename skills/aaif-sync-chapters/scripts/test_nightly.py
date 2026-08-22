#!/usr/bin/env python3
"""Self-tests for the nightly runner. No network; subprocess.run is mocked.

Three load-bearing seams, all pinned here: classify() (the shared exit-code
convention crossed with the log markers), run_engine()'s marker DETECTION
(a reworded engine print silently degrades WROTE to IN_SYNC, or loses the
PARTIAL that stops a half-checked night reading healthy), and the literal
marker strings' presence in every engine source — the contract is spelled in
several files and nothing else checks they agree.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nightly  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


check("report mode, exit 0 -> in sync",
      nightly.classify(0, wrote_marker=False, write_mode=False), nightly.IN_SYNC)
check("report mode, exit 2 -> drift",
      nightly.classify(2, wrote_marker=False, write_mode=False), nightly.DRIFT)
check("report mode, exit 1 -> failed",
      nightly.classify(1, wrote_marker=False, write_mode=False), nightly.FAILED)
check("write mode, exit 0 with Verified marker -> wrote+verified",
      nightly.classify(0, wrote_marker=True, write_mode=True), nightly.WROTE)
# sync_chapters --write exits 2 when it held back a row with no live Luma page —
# possibly after writing the rest. The pending work is what needs eyes, so DRIFT
# wins even over the wrote-marker; the log's Verified: line records the write.
check("write mode, exit 2 -> drift, even when part of the run wrote",
      nightly.classify(2, wrote_marker=True, write_mode=True), nightly.DRIFT)
check("write mode, exit 0 without marker -> nothing needed doing",
      nightly.classify(0, wrote_marker=False, write_mode=True), nightly.IN_SYNC)
check("write mode, nonzero is failed even if the marker printed "
      "(a verify that failed after a partial write)",
      nightly.classify(1, wrote_marker=True, write_mode=True), nightly.FAILED)
check("a stray Verified line in report mode never claims a write",
      nightly.classify(0, wrote_marker=True, write_mode=False), nightly.IN_SYNC)
check("a PARTIAL marker beats in-sync — a half-checked night is never healthy",
      nightly.classify(0, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.PARTIAL)
check("a PARTIAL marker beats drift — the log has the details either way",
      nightly.classify(2, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.PARTIAL)
check("but never beats FAILED, which is strictly worse news",
      nightly.classify(1, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.FAILED)

# The pipeline order the runner encodes must be the SKILL.md order — feed first,
# resources last — because each stage's writes are inputs to the next.
check("engine order is the documented pipeline order",
      [n for n, _ in nightly.ENGINES],
      ["chapters", "about", "crm", "access", "resources"])

# --- run_engine's marker detection: the seam classify() depends on -------------
# A reworded engine print ("Verified." instead of "Verified:") silently turns
# WROTE into IN_SYNC, and a missed PARTIAL makes a half-checked night read
# healthy — the exact outcome the marker exists to prevent.
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402


def drive(log_text, write_mode, code=0):
    class _Res:
        returncode = code

    def fake_run(cmd, stdout, stderr):
        stdout.write(log_text)
        return _Res()

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(nightly.subprocess, "run", fake_run):
        outcome, got_code, _secs = nightly.run_engine(
            "chapters", "sync_chapters.py", os.path.join(td, "x.log"), write_mode)
    return outcome, got_code


check("a Verified: line at line start marks a write",
      drive("stuff\nVerified: a fresh run proposes zero changes.\n", True),
      (nightly.WROTE, 0))
check("Verified: mid-line does not count",
      drive("note: Verified: something\n", True), (nightly.IN_SYNC, 0))
check("a PARTIAL: line is seen even after a blank line",
      drive("report...\n\nPARTIAL: Slack unavailable\n", False, code=2),
      (nightly.PARTIAL, 2))
check("accented engine output does not crash the log re-read",
      drive("españa Montréal Logroño\nVerified: ok\n", True),
      (nightly.WROTE, 0))

# --- access never runs with --write from the nightly ----------------------------
# Its grants hand Drive access to form-supplied addresses (and may email them);
# that is a human's call every time, so the runner strips --write for it and
# turns a pending grant into an explicit needs-a-human line + exit 2.
check("access is the report-only engine", nightly.REPORT_ONLY, frozenset({"access"}))
cmd, wm = nightly.engine_cmd("access", "sync_access.py", write_mode=True)
check("access never receives --write, even under nightly --write",
      ("--write" in cmd, wm), (False, False))
cmd, wm = nightly.engine_cmd("crm", "sync_crm.py", write_mode=True)
check("the other engines still get --write",
      ("--write" in cmd, wm), (True, True))
# The logs are 0600 files in a 0700 gitignored dir and the NEEDS-A-HUMAN step
# needs real addresses, so the runner turns the engines' CI default OFF.
for _n, _s in nightly.ENGINES:
    _cmd, _ = nightly.engine_cmd(_n, _s, write_mode=False)
    check("%s runs with --no-redact (logs are private, never CI output)" % _n,
          "--no-redact" in _cmd, _n in nightly.REDACTING)
check("every engine is in the redacting set", {n for n, _ in nightly.ENGINES}, nightly.REDACTING)
for _n, _s in nightly.ENGINES:
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), _s),
                encoding="utf-8").read()
    check("%s actually accepts --no-redact" % _s, "add_redact_flag(ap)" in _src, True)


def access_drive(log_text, code):
    """run_engine for access under --write, capturing the argv actually used."""
    seen = {}

    class _Res:
        returncode = code

    def fake_run(cmd, stdout, stderr):
        seen["cmd"] = cmd
        stdout.write(log_text)
        return _Res()

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(nightly.subprocess, "run", fake_run):
        log = os.path.join(td, "access.log")
        outcome, _c, _s = nightly.run_engine("access", "sync_access.py", log, True)
        mode = os.stat(log).st_mode & 0o777
    return outcome, seen["cmd"], mode


outcome, cmd, mode = access_drive("PHASE 2 — 3 new grant(s)\n", code=2)
check("pending grants under nightly --write classify as DRIFT (report mode)",
      outcome, nightly.DRIFT)
check("...and the subprocess argv carried no --write", "--write" in cmd, False)
check("engine logs are created 0o600", mode, 0o600)
outcome, _cmd, _m = access_drive("Verified: x\n", code=0)
check("a stray Verified: from access never reads as a write",
      outcome, nightly.IN_SYNC)

notes = nightly.summary_notes({"crm": nightly.WROTE, "access": nightly.DRIFT}, True)
check("pending access grants are named as needing a human in the summary",
      "NEEDS A HUMAN" in notes[0] and "sync_access.py --write" in notes[0], True)
check("...and that run exits 2",
      nightly.exit_code({"crm": nightly.WROTE, "access": nightly.DRIFT}), 2)
check("access drift alone is not reported as generic drift",
      "drift" in nightly.summary_notes({"access": nightly.DRIFT}, True)[0], False)
check("all in sync exits 0 with the in-sync line",
      (nightly.exit_code({"crm": nightly.IN_SYNC, "access": nightly.IN_SYNC}),
       nightly.summary_notes({"crm": nightly.IN_SYNC}, True)),
      (0, ["RESULT: everything in sync."]))
check("a failure anywhere exits 1",
      nightly.exit_code({"crm": nightly.FAILED, "access": nightly.DRIFT}), 1)
check("summary lines never carry engine output (fixed strings only)",
      any(c in " ".join(notes) for c in "@<>"), False)

# --- the marker strings must actually appear in the engine sources -------------
# The contract is two magic prefixes spelled in several files; this is the check
# that a reword in any one of them cannot pass silently.
_HERE = os.path.dirname(os.path.abspath(__file__))
for eng in ("sync_chapters.py", "sync_about.py", "sync_crm.py",
            "sync_access.py", "sync_resources.py"):
    src = open(os.path.join(_HERE, eng), encoding="utf-8").read()
    check("%s prints the literal 'Verified: ' marker" % eng,
          '"Verified: ' in src or '"\\nVerified: ' in src, True)
for eng in ("sync_about.py", "sync_resources.py"):
    src = open(os.path.join(_HERE, eng), encoding="utf-8").read()
    check("%s prints the literal 'PARTIAL: ' marker" % eng,
          '"PARTIAL: ' in src or '"\\nPARTIAL: ' in src, True)

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nnightly: all checks passed")
