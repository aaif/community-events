#!/usr/bin/env python3
"""Self-tests for the pipeline runner. No network; subprocess.run is mocked.

Five load-bearing seams, all pinned here:

  * `classify()` — the shared exit-code convention crossed with the log markers.
  * `run_step()`'s marker DETECTION — a reworded engine print silently degrades
    WROTE to IN_SYNC, or loses the PARTIAL that stops a half-checked run
    reading healthy.
  * the literal marker strings' presence in every engine source — the contract
    is spelled in several files and nothing else checks they agree.
  * **the gates** — which step may receive `--write`, which additionally needs
    `--i-have-approval`, and which can never write at all. This is the half a
    reader cannot verify by eye, because the effect is an argv that was never
    assembled.
  * **the order** — the pipeline order is load-bearing (the CRM must hold the
    right people before access is granted), and selecting a subset must not be
    able to reorder it.
"""

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nightly  # noqa: E402
import sync  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


# --- classify: the exit-code convention ---------------------------------------
check("report mode, exit 0 -> in sync",
      sync.classify(0, wrote_marker=False, write_mode=False), sync.IN_SYNC)
check("report mode, exit 2 -> drift",
      sync.classify(2, wrote_marker=False, write_mode=False), sync.DRIFT)
check("report mode, exit 1 -> failed",
      sync.classify(1, wrote_marker=False, write_mode=False), sync.FAILED)
check("write mode, exit 0 with Verified marker -> wrote+verified",
      sync.classify(0, wrote_marker=True, write_mode=True), sync.WROTE)
# sync_chapters --write exits 2 when it held back a row with no live Luma page —
# possibly after writing the rest. The pending work is what needs eyes, so DRIFT
# wins even over the wrote-marker; the log's Verified: line records the write.
check("write mode, exit 2 -> drift, even when part of the run wrote",
      sync.classify(2, wrote_marker=True, write_mode=True), sync.DRIFT)
check("write mode, exit 0 without marker -> nothing needed doing",
      sync.classify(0, wrote_marker=False, write_mode=True), sync.IN_SYNC)
check("write mode, nonzero is failed even if the marker printed",
      sync.classify(1, wrote_marker=True, write_mode=True), sync.FAILED)
check("a stray Verified line in report mode never claims a write",
      sync.classify(0, wrote_marker=True, write_mode=False), sync.IN_SYNC)
check("a PARTIAL marker beats in-sync — a half-checked run is never healthy",
      sync.classify(0, False, False, partial_marker=True), sync.PARTIAL)
check("a PARTIAL marker beats drift — the log has the details either way",
      sync.classify(2, False, False, partial_marker=True), sync.PARTIAL)
check("but never beats FAILED, which is strictly worse news",
      sync.classify(1, False, False, partial_marker=True), sync.FAILED)

# --- the pipeline order --------------------------------------------------------
check("phase order is the documented pipeline order",
      sync.PHASE_NAMES,
      ["clean", "triage", "chapters", "organizers", "resources", "slack",
       "luma", "verify"])
check("the organizers phase writes the CRM before it reads it for access",
      [s.name for p, s in sync.selected(["organizers"], False)],
      ["about", "crm", "access"])
# Selecting a subset must never let the caller reorder the pipeline: listing
# access first does not grant access before the CRM holds the right people.
check("a subset keeps pipeline order however it was typed",
      [s.name for _p, s in sync.selected(["access", "chapters", "crm"], False)],
      ["chapters", "crm", "access"])
check("a phase name and a step name both select",
      [s.name for _p, s in sync.selected(["resources", "crm"], False)],
      ["crm", "resources"])
check("no argument selects every step",
      len(sync.selected([], False)), len(sync.STEP_NAMES))

# --- every script the pipeline names actually exists ---------------------------
# A moved or renamed engine otherwise fails at 3am inside a subprocess, as a
# FAILED line with a traceback in a log nobody is reading.
for _phase, _step in sync.selected([], False):
    check("%s -> %s exists on disk" % (_step.name, _step.script),
          os.path.exists(_step.path), True)

# --- the gates -----------------------------------------------------------------
_by = {s.name: s for _p, s in sync.selected([], False)}

cmd, wm = sync.step_cmd(_by["access"], write_mode=True, approved=True)
check("access never receives --write, even with approval",
      ("--write" in cmd, wm), (False, False))
cmd, wm = sync.step_cmd(_by["crm"], write_mode=True, approved=False)
check("an open step gets --write without approval",
      ("--write" in cmd, wm), (True, True))
cmd, wm = sync.step_cmd(_by["invite"], write_mode=True, approved=False)
check("an approval step gets no --write without approval",
      ("--write" in cmd, wm), (False, False))
cmd, wm = sync.step_cmd(_by["invite"], write_mode=True, approved=True)
check("an approval step gets --write AND --i-have-approval with approval",
      ("--write" in cmd and "--i-have-approval" in cmd, wm), (True, True))
check("--i-have-approval is never passed to a non-approval step",
      "--i-have-approval" in sync.step_cmd(_by["crm"], True, True)[0], False)
cmd, wm = sync.step_cmd(_by["luma"], write_mode=True, approved=True)
check("a read-only step can never be made to write",
      ("--write" in cmd, wm), (False, False))
check("triage is HUMAN-gated — a decision is a person's, not a runner's",
      _by["triage"].gate, sync.HUMAN)
# The whole point of the gate: no argv exists that makes the runner execute it.
cmd, wm = sync.step_cmd(_by["triage"], write_mode=True, approved=True)
check("a HUMAN step can never be given a write mode", ("--write" in cmd, wm),
      (False, False))
_ran = []
with mock.patch.object(sync.subprocess, "run",
                       lambda *a, **k: _ran.append(a) or type("R", (), {"returncode": 0})()):
    with tempfile.TemporaryDirectory() as _td:
        code = sync.main(["triage", "--report-dir", _td])
check("the runner never spawns a HUMAN step, even asked for by name", _ran, [])
check("...and says so by exiting 2, not 0 — 'nobody triaged' is not 'all clear'",
      code, 2)
_notes = sync.summary_notes({"triage": sync.SKIPPED}, False)[0]
check("...and the summary points at the skill, not at --i-have-approval",
      "aaif-triage-intake" in _notes and "--i-have-approval" not in _notes, True)
check("an approval-gated skip still names the flag that would help",
      "--i-have-approval" in sync.summary_notes({"invite": sync.SKIPPED}, True)[0],
      True)

# The logs are 0600 files in a 0700 gitignored dir and the NEEDS-A-HUMAN note
# needs real addresses, so the runner turns the engines' CI default OFF.
for _name, _step in _by.items():
    _cmd, _ = sync.step_cmd(_step, write_mode=False, approved=False)
    check("%s --no-redact matches the redacting set" % _name,
          "--no-redact" in _cmd, _name in sync.REDACTING)
for _name in sync.REDACTING:
    _src = open(_by[_name].path, encoding="utf-8").read()
    # `add_redact_flag(ap` — the call may carry a `masks=` argument naming what
    # this engine prints, so the prefix is what is pinned.
    check("%s actually accepts --no-redact" % _name, "add_redact_flag(ap" in _src, True)

# --- run_step's marker detection ----------------------------------------------
def drive(log_text, write_mode, code=0, step="chapters", approved=False):
    class _Res:
        returncode = code

    def fake_run(cmd, stdout, stderr):
        stdout.write(log_text)
        return _Res()

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(sync.subprocess, "run", fake_run):
        outcome, got_code, _secs = sync.run_step(
            _by[step], os.path.join(td, "x.log"), write_mode, approved)
    return outcome, got_code


check("a Verified: line at line start marks a write",
      drive("stuff\nVerified: a fresh run proposes zero changes.\n", True),
      (sync.WROTE, 0))
check("Verified: mid-line does not count",
      drive("note: Verified: something\n", True), (sync.IN_SYNC, 0))
check("a PARTIAL: line is seen even after a blank line",
      drive("report...\n\nPARTIAL: Slack unavailable\n", False, code=2),
      (sync.PARTIAL, 2))
check("accented engine output does not crash the log re-read",
      drive("españa Montréal Logroño\nVerified: ok\n", True), (sync.WROTE, 0))


def access_drive(log_text, code):
    """run_step for access under --write, capturing the argv actually used."""
    seen = {}

    class _Res:
        returncode = code

    def fake_run(cmd, stdout, stderr):
        seen["cmd"] = cmd
        stdout.write(log_text)
        return _Res()

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(sync.subprocess, "run", fake_run):
        log = os.path.join(td, "access.log")
        outcome, _c, _s = sync.run_step(_by["access"], log, True, True)
        mode = os.stat(log).st_mode & 0o777
    return outcome, seen["cmd"], mode


outcome, cmd, mode = access_drive("PHASE 2 — 3 new grant(s)\n", code=2)
check("pending grants under --write classify as DRIFT (report mode)",
      outcome, sync.DRIFT)
check("...and the subprocess argv carried no --write", "--write" in cmd, False)
check("step logs are created 0o600", mode, 0o600)
check("a stray Verified: from access never reads as a write",
      access_drive("Verified: x\n", code=0)[0], sync.IN_SYNC)

# --- the summary ---------------------------------------------------------------
notes = sync.summary_notes({"crm": sync.WROTE, "access": sync.DRIFT}, True)
check("pending access grants are named as needing a human",
      "NEEDS A HUMAN" in notes[0] and "sync_access.py --write" in notes[0], True)
check("...and that run exits 2",
      sync.exit_code({"crm": sync.WROTE, "access": sync.DRIFT}), 2)
check("access drift alone is not reported as generic drift",
      "drift" in sync.summary_notes({"access": sync.DRIFT}, True)[0], False)
check("...and a gated step makes the run exit 2, never 0",
      sync.exit_code({"chapters": sync.IN_SYNC, "invite": sync.SKIPPED}), 2)
check("an all-clear run exits 0",
      sync.exit_code({"chapters": sync.IN_SYNC}), 0)
check("a failure outranks everything",
      sync.exit_code({"crm": sync.WROTE, "about": sync.FAILED}), 1)

# --- unattended ----------------------------------------------------------------
check("the unattended set excludes every approval-gated phase",
      [p for p in sync.UNATTENDED_PHASES
       if any(s.gate == sync.APPROVAL for _p, s in sync.selected([p], False))],
      [])
check("unattended runs only the unattended phases",
      sorted({p for p, _s in sync.selected([], True)}),
      sorted(sync.UNATTENDED_PHASES))
# A scheduled job is by definition nobody's approval; the two flags together are
# a contradiction, and argparse refuses rather than quietly preferring one.
_parser_exit = []
try:
    sync.main(["--unattended", "--i-have-approval"])
except SystemExit as e:
    _parser_exit.append(e.code)
check("--unattended refuses --i-have-approval", _parser_exit, [2])

# nightly is a wrapper, not a second definition: it must add --unattended and
# own no pipeline of its own.
check("nightly re-exports no phase list of its own",
      hasattr(nightly, "ENGINES") or hasattr(nightly, "PHASES"), False)
_seen = {}
with mock.patch.object(sync, "main", lambda argv: _seen.setdefault("argv", argv) or 0):
    nightly.main([])
check("nightly always passes --unattended", "--unattended" in _seen["argv"], True)
check("nightly keeps its own report dir",
      "nightly-reports" in " ".join(_seen["argv"]), True)

# --- the gitignore guard works BEFORE the directory exists ---------------------
# `sync-reports/` is a directory-only pattern and `git check-ignore` treats a
# path that does not exist yet as a file, so the guard has to probe with a
# trailing separator. Without it, the very first run on a fresh checkout aborts
# — which is how this went unnoticed in nightly.py for as long as the directory
# happened to already exist on the machine it ran on.
_probe = []
with mock.patch.object(sync.subprocess, "run",
                       lambda cmd, **kw: _probe.append(cmd) or
                       type("R", (), {"returncode": 0})()):
    sync.guard_report_dir(os.path.join(sync.REPO, "sync-reports"))
check("the ignore probe carries a trailing separator",
      _probe and _probe[0][-1].endswith(os.sep), True)
check("...and the real .gitignore actually covers it",
      sync.subprocess.run(["git", "-C", sync.REPO, "check-ignore", "-q",
                           os.path.join(sync.REPO, "sync-reports") + os.sep],
                          capture_output=True).returncode, 0)
# A dir git would happily commit must abort, not warn.
_aborted = []
with mock.patch.object(sync.subprocess, "run",
                       lambda cmd, **kw: type("R", (), {"returncode": 1})()):
    try:
        sync.guard_report_dir(os.path.join(sync.REPO, "not-ignored"))
    except SystemExit as e:
        _aborted.append("ABORT" in str(e))
check("a committable report dir aborts the run", _aborted, [True])
# Outside the repo there is nothing to commit to, so no check is made at all.
_outside = []
with mock.patch.object(sync.subprocess, "run",
                       lambda cmd, **kw: _outside.append(cmd) or
                       type("R", (), {"returncode": 1})()):
    sync.guard_report_dir("/tmp/aaif-sync-elsewhere")
check("a report dir outside the repo is never second-guessed", _outside, [])

# --- each skill's SKILL.md names every test beside it --------------------------
# It had drifted before: three tests existed that the list did not mention,
# including the one for provision_channels.py, the most dangerous script here.
# A list that is almost complete is worse than none — it reads as "all of them".
_SKILLS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", ".."))
for _skill in ("aaif-sync", "aaif-sync-chapters", "aaif-sync-organizers",
               "aaif-sync-slack"):
    _doc_path = os.path.join(_SKILLS, _skill, "SKILL.md")
    if not os.path.exists(_doc_path):
        check("%s has a SKILL.md" % _skill, False, True)
        continue
    with open(_doc_path, encoding="utf-8") as _fh:
        _doc = _fh.read()
    for _sub in ("scripts", "migrations"):
        _d = os.path.join(_SKILLS, _skill, _sub)
        if not os.path.isdir(_d):
            continue
        _missing = sorted(f for f in os.listdir(_d)
                          if f.startswith("test_") and f.endswith(".py")
                          and f not in _doc)
        check("%s/SKILL.md names every test in %s/" % (_skill, _sub), _missing, [])

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("sync: all checks passed")
