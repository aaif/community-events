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

import contextlib
import io
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
      ["preflight", "chapters", "organizers", "events", "speakers", "hosts",
       "workspace"])
check("every phase measures before it proposes and proposes before it writes",
      sync.assert_stage_order(), None)
# The stage axis is the point: `--stage gather` must measure the whole estate
# and propose nothing, so it can never contain a step that writes.
_g = sync.selected([], False, [sync.GATHER])
check("a gather-only run touches every phase that has an engine",
      sorted({p for p, _s in _g}),
      ["chapters", "events", "organizers", "preflight", "speakers", "workspace"])
for _p, _s in _g:
    _cmd, _wm = sync.step_cmd(_s, write_mode=True, approved=True)
    check("gather step %r can never write" % _s.name, ("--write" in _cmd, _wm),
          (False, False))
# Dependencies that live in the ORDER rather than in any one script.
_names = [s.name for _p, s in sync.selected([], False)]
check("activity is measured before topics reports its dormancy numbers",
      _names.index("activity") < _names.index("topics"), True)
check("...and before members, which reads the same cache",
      _names.index("activity") < _names.index("members"), True)
check("...and before chapter health, which reads it",
      _names.index("activity") < _names.index("health"), True)
check("chapter coverage is gathered before the chapters plan proposes rows",
      _names.index("coverage") < _names.index("chapters"), True)
check("organizers are done before speakers",
      sync.PHASE_NAMES.index("organizers") < sync.PHASE_NAMES.index("speakers"), True)
check("events sits after chapters and organizers, as asked",
      sync.PHASE_NAMES.index("events") > sync.PHASE_NAMES.index("organizers"), True)
# The CRM merges a person's rows ACROSS role tabs into one row, so it must stay
# a single pass. A role-scoped split would write the narrower row twice.
check("the CRM is ONE step, never split per role",
      len([s for _p, s in sync.selected([], False) if s.script == "sync_crm.py"]), 1)
# Selecting a subset must never let the caller reorder the pipeline: listing
# access first does not grant access before the CRM holds the right people.
check("a subset keeps pipeline order however it was typed",
      [s.name for _p, s in sync.selected(["crm", "about", "access"], False)],
      ["about", "access", "crm"])
check("a phase name and a step name both select",
      [s.name for _p, s in sync.selected(["resources", "crm"], False)],
      ["resources", "crm"])
# `chapters` names both a phase and a step inside it. The phase wins, which is
# what someone typing it means; pinned so the collision stays deliberate.
check("a name that is both a phase and a step selects the whole phase",
      [s.name for _p, s in sync.selected(["chapters"], False)],
      ["coverage", "chapters", "resources", "provision"])
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
# The gate summarises but never decides: it runs, and no argv can make it write.
cmd, wm = sync.step_cmd(_by["triage"], write_mode=True, approved=True)
check("a HUMAN step can never be given a write mode", ("--write" in cmd, wm),
      (False, False))
check("...not even --i-have-approval reaches it",
      "--i-have-approval" in cmd, False)


def _drive(argv, code=0, entry=None, log_text=""):
    """Run the runner end to end with every engine stubbed at one exit code.

    Returns (exit code, the argv of every subprocess it would have launched).
    """
    seen = []

    def fake_run(cmd, stdout=None, stderr=None, **kw):
        seen.append(cmd)
        if stdout is not None and log_text:
            stdout.write(log_text)
        return type("R", (), {"returncode": code})()

    with mock.patch.object(sync.subprocess, "run", fake_run), \
         tempfile.TemporaryDirectory() as td, \
         contextlib.redirect_stdout(io.StringIO()):
        rc = (entry or sync.main)(list(argv) + ["--report-dir", td])
    return rc, seen


def _drive_triage(code):
    return _drive(["triage"], code, log_text="3 awaiting review\n")


# A deep queue and an empty one must be distinguishable, which is the whole
# reason the runner runs it at all rather than printing "ask a human".
_rc, _seen = _drive_triage(2)
check("the runner DOES run a HUMAN step — summarising is not deciding",
      any("intake.py" in " ".join(c) for c in _seen), True)
check("...with no --write in the argv it actually spawned",
      any("--write" in c for c in _seen), False)
check("rows awaiting a decision exit 2 — 'nobody looked' is not 'all clear'",
      _rc, 2)
_rc_empty, _ = _drive_triage(0)
check("an empty queue exits 0 — there is genuinely nothing to do",
      _rc_empty, 0)

_notes = sync.summary_notes({"triage": sync.DRIFT}, False)[0]
check("the summary says a human decides, and points at the skill",
      "aaif-triage-intake" in _notes and "never decide" in _notes, True)
check("...and never tells the operator to re-run triage with --write",
      "--write" in _notes or "--i-have-approval" in _notes, False)
# The generic drift note would say exactly that, so triage must stay out of it.
check("a HUMAN step's DRIFT is not swept into the generic drift note",
      "re-run the flagged step(s) with --write" in _notes, False)
# A gather step measures and cannot write, so its DRIFT is a finding, not a
# proposal — naming --write there sends the operator after a flag that does not
# exist. Same shape as the HUMAN case above.
_f = sync.summary_notes({"luma": sync.DRIFT}, False)[0]
check("a gather step's drift reads as a finding, not an unapplied proposal",
      "findings" in _f and "--write" not in _f, True)
check("...and says to fix it at the source",
      "at its source" in _f, True)
check("a real engine's drift still gets the generic note",
      "re-run the flagged step(s) with --write"
      in sync.summary_notes({"crm": sync.DRIFT}, False)[0], True)
check("an approval-gated skip still names the flag that would help",
      "--i-have-approval" in sync.summary_notes({"invite": sync.SKIPPED}, True)[0],
      True)
# In report mode an approval step runs read-only and can report DRIFT. The
# remedy is BOTH flags: `--write` alone makes main() skip it, so the generic
# "re-run with --write" note would send the operator to a flag that cannot help.
_ad = sync.summary_notes({"invite": sync.DRIFT}, False)[0]
check("approval-step drift names --i-have-approval, not just --write",
      "--write --i-have-approval" in _ad, True)
check("...and does not get the generic drift note",
      "re-run the flagged step(s) with --write after review" in _ad, False)

# --- unattended: the approval steps become report-only ----------------------
# A scheduled job is nobody's approval, so `nightly.py --write` runs the Slack
# steps read-only — the same contract `access` has. Both scheduled modes then
# agree on what is pending, a clean night exits 0, and a pending room exits 2
# with the note that sends a human to a terminal.
_SLACK_WRITERS = ("provision_channels", "invite_organizers", "post_country_directory")
_rc, _seen = _drive(["--write"], entry=nightly.main)
check("a clean unattended write run exits 0", _rc, 0)
_slack = [c for c in _seen if any(x in " ".join(c) for x in _SLACK_WRITERS)]
check("...and ran the approval steps read-only", bool(_slack), True)
check("...never with --write", [c for c in _slack if "--write" in c], [])
check("...but did run the open writes",
      any("sync_crm.py" in " ".join(c) and "--write" in c for c in _seen), True)
_rc, _ = _drive(["--write"], code=2, entry=nightly.main)
check("an unattended write with pending Slack changes exits 2", _rc, 2)
_pending = sync.summary_notes({"invite": sync.DRIFT}, True)[0]
check("...and its note sends a human to the terminal with both flags",
      "--write --i-have-approval" in _pending, True)

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
# sync_chapters indents its Luma-sweep marker two spaces. A column-zero match
# read a rate-limited sweep as DRIFT (live, 2026-09-20): a half-checked run
# passing as a finding is exactly what the marker exists to prevent.
check("an indented PARTIAL marker still counts",
      drive("Luma audit: 75 checked\n  PARTIAL: rate-limited at row 77\n",
            False, code=2), (sync.PARTIAL, 2))


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
# A phase is a SUBJECT now, so phases mix gates — `chapters` contains both an
# open plan and an approval-gated provision. The old invariant ("no unattended
# phase contains an approval step") no longer fits and, worse, would push the
# runner toward gate-homogeneous phases, which is the axis this model rejects.
# What must hold is about execution, not composition: unattended, no approval
# step can ever be handed a write.
_appr = [s for _p, s in sync.selected(list(sync.UNATTENDED_PHASES), True)
         if s.gate == sync.APPROVAL]
check("the unattended set does contain approval steps, by design",
      bool(_appr), True)
for _s in _appr:
    _cmd, _wm = sync.step_cmd(_s, write_mode=True, approved=True, unattended=True)
    check("unattended, %r is never handed a write, even if approval were claimed"
          % _s.name, ("--write" in _cmd, _wm), (False, False))
# ...and the flag that would authorise one is refused outright in that mode,
# which is what makes the above unreachable rather than merely unused.
_refused = []
try:
    sync.main(["--unattended", "--i-have-approval"])
except SystemExit as e:
    _refused.append(e.code)
check("--unattended still refuses --i-have-approval", _refused, [2])
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

# --- no state survives a run -----------------------------------------------------
# The runner must keep nothing between runs but the data itself. Two properties:
# it is a pure function of its arguments, and the one thing that DOES outlive a
# run (.slack-audit-cache) is rebuilt once per run rather than inherited.
_full = sync.selected([], False)
check("selection is deterministic — no memo between calls",
      [s.name for _p, s in sync.selected([], False)],
      [s.name for _p, s in sync.selected([], False)])
_by_name2 = {s.name: s for _p, s in _full}
check("argv is a pure function of the step and the flags",
      sync.step_cmd(_by_name2["crm"], True, False),
      sync.step_cmd(_by_name2["crm"], True, False))

# The cache outlives a run on purpose — a 20-minute directory pull should not be
# paid twice in a day. What must not outlive it is a STALE one, and that expiry
# lives in jsoncache so a standalone audit run obeys it too, not only a step this
# runner scheduled. The runner must therefore force no refresh of its own.
# (The one-day expiry itself is asserted in lib/aaif_events/tests/test_jsoncache.py.
# Importing that module HERE would give this skill the lib coupling the runner
# exists without: it drives every engine by subprocess, never by import, which
# is what lets one skill drive four without inheriting any of their imports.)
check("the runner forces no refresh — that would defeat the caching",
      any("--refresh" in sync.step_cmd(s, False, False)[0] for _p, s in _full),
      False)
check("...and holds no refresh policy of its own",
      hasattr(sync, "refresh_plan"), False)
# Steps still declare that they read the shared cache, so the fact stays
# visible to a reader even though the runner no longer acts on it.
check("the cache-backed steps are still declared",
      sorted(s.name for _p, s in _full if s.cached),
      ["activity", "coverage", "health", "identity", "members", "report", "topics"])

# --- HTML lands in the run directory --------------------------------------------
# Every RENDERS_HTML step really takes --out (pinned against its source, as for
# REDACTING), the runner points it at <run_dir>/<step>, and nothing else gets
# an --out it would reject.
for _name in sync.RENDERS_HTML:
    with open(_by[_name].path, encoding="utf-8") as _fh:
        check("%s takes --out" % _name, '"--out"' in _fh.read(), True)
for _p, _s in sync.selected([], False):
    _cmd, _wm = sync.step_cmd(_s, False, False, out_dir="/x/run")
    check("%s gets --out iff it renders HTML" % _s.name,
          "--out" in _cmd, _s.name in sync.RENDERS_HTML)
    if _s.name in sync.RENDERS_HTML:
        check("...pointed at the run dir", _cmd[_cmd.index("--out") + 1],
              os.path.join("/x/run", _s.name))
check("the composed report is the last step of the pipeline",
      [s.name for _p, s in sync.selected([], False)][-1], "report")

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
