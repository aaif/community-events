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
import json
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


def _fake_engine(seen, code, log_text="", findings=True, written=False, render_code=0,
                 hang=False):
    """A stand-in for every subprocess the runner launches.

    Engines write `log_text` and, when asked for `--json-out` and `findings`
    is on, a minimal format-1 findings file (the runner reads `written` from
    it, and reads exit 2 with NO file as a usage error). The renderer argv
    gets `render_code` so its failure path can be chosen deliberately.
    """
    def fake_run(cmd, stdout=None, stderr=None, **kw):
        seen.append(cmd)
        if cmd[1] == sync.RENDERER:
            return type("R", (), {"returncode": render_code})()
        if hang:
            raise sync.subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
        if stdout is not None and log_text:
            stdout.write(log_text)
        if findings and "--json-out" in cmd:
            with open(cmd[cmd.index("--json-out") + 1], "w", encoding="utf-8") as fh:
                json.dump({"format": 1, "step": "x", "mode": "write" if written else "report",
                           "summary": "", "measured": [], "findings": [],
                           "written": written}, fh)
        return type("R", (), {"returncode": code})()
    return fake_run


def _drive(argv, code=0, entry=None, log_text="", keep=None, **engine):
    """Run the runner end to end with every engine stubbed at one exit code.

    Returns (exit code, the argv of every subprocess it would have launched).
    `keep`: a directory to run in that outlives the call (for the integration
    test that renders the run afterwards); default is a temp dir.
    """
    seen = []
    fake_run = _fake_engine(seen, code, log_text, **engine)

    global _LAST_MANIFEST
    with mock.patch.object(sync.subprocess, "run", fake_run), \
         tempfile.TemporaryDirectory() as tmp, \
         contextlib.redirect_stdout(io.StringIO()) as out:
        td = keep or tmp
        rc = (entry or sync.main)(list(argv) + ["--report-dir", td])
        # The run directory dies with the `with`; keep what the runner recorded.
        runs = sorted(os.listdir(td))
        _LAST_MANIFEST, _LAST_STDOUT[:] = None, [out.getvalue()]
        if runs:
            with open(os.path.join(td, runs[-1], sync.MANIFEST), encoding="utf-8") as fh:
                _LAST_MANIFEST = json.load(fh)
    return rc, seen


_LAST_STDOUT = [""]


_LAST_MANIFEST = None


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

# --- the runner records, the renderer draws ------------------------------------
# run.json is the contract between the two: one entry per step in run order,
# with the log each wrote, and the RESULT notes. The renderer runs last, as a
# subprocess like every engine, pointed at the run directory.
_rc, _seen = _drive(["preflight"], code=2, log_text="3 awaiting review\n")
_m = _LAST_MANIFEST
check("run.json is written", _m is not None, True)
# Every step, every time: a preflight-only run still lists the whole pipeline,
# so the page it draws is the estate with two steps measured, not a two-step
# estate. The steps not selected say so.
check("...every pipeline step, in pipeline order",
      [s["step"] for s in _m["steps"]], sync.STEP_NAMES)
check("...carrying outcome, exit and log for the steps that ran",
      [(s["outcome"], s["exit"], s["log"]) for s in _m["steps"][:2]],
      [("DRIFT", 2, "clean.log"), ("DRIFT", 2, "triage.log")])
check("...and NOT_RUN, with no log, for the rest",
      {(s["outcome"], s["log"]) for s in _m["steps"][2:]}, {(sync.NOT_RUN, None)})
check("...and where each step's findings file is",
      [s["findings"] for s in _m["steps"][:2]], ["clean.json", "triage.json"])
check("...and the RESULT notes and exit code",
      (_m["exit"], any(n.startswith("RESULT:") for n in _m["notes"])), (2, True))
check("the renderer is the last subprocess, given the run directory",
      (_seen[-1][1] == sync.RENDERER, os.path.basename(_seen[-1][2]) == _m["stamp"]),
      (True, True))
check("a skipped step is recorded with no log",
      [(s["outcome"], s["log"]) for s in
       (_drive(["provision", "--write"]) and _LAST_MANIFEST)["steps"]
       if s["step"] == "provision"],
      [("skipped", None)])
_pending = sync.summary_notes({"invite": sync.DRIFT}, True)[0]
check("...and its note sends a human to the terminal with both flags",
      "--write --i-have-approval" in _pending, True)

# The logs are 0600 files in a 0700 gitignored dir and the NEEDS-A-HUMAN note
# needs real addresses, so the runner turns the engines' CI default OFF.
for _name, _step in _by.items():
    _cmd, _ = sync.step_cmd(_step, write_mode=False, approved=False)
    check("%s --no-redact matches the redacting set" % _name,
          "--no-redact" in _cmd, _name in sync.REDACTING)
for _p, _s in sync.selected([], False):
    _src = open(_s.path, encoding="utf-8").read()
    # `add_redact_flag(ap` — the call may carry a `masks=` argument naming what
    # this engine prints, so the prefix is what is pinned. Both directions: a
    # step that takes the flag and is not in the set comes out redacted under
    # CI while every other log does not.
    check("%s is in REDACTING iff its source takes --redact" % _s.name,
          _s.name in sync.REDACTING, "add_redact_flag(ap" in _src)

# --- run_step's marker detection ----------------------------------------------
def drive(log_text, write_mode, code=0, step="chapters", approved=False, findings={}):
    class _Res:
        returncode = code

    def fake_run(cmd, stdout, stderr, **kw):
        stdout.write(log_text)
        if "--json-out" in cmd and findings is not None:
            with open(cmd[cmd.index("--json-out") + 1], "w", encoding="utf-8") as fh:
                json.dump(dict({"format": 1, "step": step, "mode": "report", "summary": "",
                                "measured": [], "findings": [], "written": False}, **findings), fh)
        return _Res()

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(sync.subprocess, "run", fake_run):
        outcome, got_code, _secs = sync.run_step(
            _by[step], os.path.join(td, "x.log"), write_mode, approved)
    return outcome, got_code


# The marker is the fallback for a step with no findings file; when the file
# is there, its `written` is the one definition and a stray marker cannot
# outrank it.
check("a Verified: line at line start marks a write (no findings file)",
      drive("stuff\nVerified: a fresh run proposes zero changes.\n", True, findings=None),
      (sync.WROTE, 0))
check("...but the findings file's written=False outranks the marker",
      drive("stuff\nVerified: ok\n", True), (sync.IN_SYNC, 0))
check("Verified: mid-line does not count",
      drive("note: Verified: something\n", True), (sync.IN_SYNC, 0))
check("a PARTIAL: line is seen even after a blank line",
      drive("report...\n\nPARTIAL: Slack unavailable\n", False, code=2),
      (sync.PARTIAL, 2))
check("accented engine output does not crash the log re-read",
      drive("españa Montréal Logroño\nVerified: ok\n", True, findings=None), (sync.WROTE, 0))
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

    def fake_run(cmd, stdout, stderr, **kw):
        seen["cmd"] = cmd
        stdout.write(log_text)
        if "--json-out" in cmd:      # access emits findings; exit 2 without one is FAILED
            with open(cmd[cmd.index("--json-out") + 1], "w", encoding="utf-8") as fh:
                json.dump({"format": 1, "step": "access", "mode": "report", "summary": "",
                           "measured": [], "findings": [], "written": False}, fh)
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
with contextlib.redirect_stderr(io.StringIO()):
    try:
        sync.main(["--unattended", "--i-have-approval"])
    except SystemExit as e:
        _refused.append(e.code)
check("--unattended refuses --i-have-approval", _refused, [2])
check("unattended runs only the unattended phases",
      sorted({p for p, _s in sync.selected([], True)}),
      sorted(sync.UNATTENDED_PHASES))
# Explicit names cannot widen the unattended scope: `nightly.py luma` would run
# the rate-limited sweep the set exists to keep out of a scheduled job.
_outside = []
with contextlib.redirect_stderr(io.StringIO()):
    try:
        sync.main(["luma", "--unattended"])
    except SystemExit as e:
        _outside.append(e.code)
check("--unattended refuses a phase or step outside its scope", _outside, [2])
# A selection that matches nothing is an error, never "everything in sync".
_empty = []
with contextlib.redirect_stderr(io.StringIO()):
    try:
        sync.main(["hosts"])
    except SystemExit as e:
        _empty.append(e.code)
check("a selection matching no step is refused", _empty, [2])

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
      ["activity", "audit", "coverage", "health", "identity", "members", "topics"])

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
# Every EMITS_FINDINGS step really takes --json-out (pinned against its
# source), the runner points it at <run_dir>/<step>.json, and the manifest
# records where. Audits render HTML instead and get no --json-out.
# A lib-coupled engine gets the flag from findings.add_flag(); a portable one
# spells it out. Either is the flag; a script with neither would die on a
# usage error the first time the runner passed it.
for _name in sync.EMITS_FINDINGS:
    with open(_by[_name].path, encoding="utf-8") as _fh:
        _src = _fh.read()
    check("%s takes --json-out" % _name,
          '"--json-out"' in _src or "findings.add_flag(" in _src, True)
for _p, _s in sync.selected([], False):
    _cmd, _wm = sync.step_cmd(_s, False, False, out_dir="/x/run")
    check("%s gets --json-out iff it emits findings" % _s.name,
          "--json-out" in _cmd, _s.name in sync.EMITS_FINDINGS)
check("the composed audit is the last step of the pipeline",
      [s.name for _p, s in sync.selected([], False)][-1], "audit")

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

# --- a usage error is FAILED, not drift ---------------------------------------
# argparse and "can't open file" both exit 2. An engine that never reached a
# report must not read as "drift, re-run with --write"; the findings file is
# the proof it reported, and its `written` is the one definition of "wrote".
check("exit 2 with a findings file is DRIFT",
      drive("report\n", False, code=2), (sync.DRIFT, 2))
check("exit 2 with NO findings file is FAILED (usage error / missing script)",
      drive("usage: x.py [-h]\n", False, code=2, findings=None), (sync.FAILED, 1))
check("written=True in the findings file is WROTE, with no Verified: line",
      drive("done\n", True, code=0, approved=True, findings={"written": True, "mode": "write"}),
      (sync.WROTE, 0))
check("a step that hangs past the budget is FAILED",
      _drive(["clean"], hang=True)[0], 1)
check("...and its outcome says so", _LAST_MANIFEST["steps"][0]["outcome"], sync.FAILED)

# --- gates are a closed set, and fail closed ----------------------------------
_bad = []
try:
    sync.Step("x", "aaif-sync", "sync.py", gate="report_only")
except ValueError as e:
    _bad.append("gate" in str(e))
try:
    sync.Step("x", "aaif-sync", "sync.py", stage="verify")
except ValueError as e:
    _bad.append("stage" in str(e))
check("a misspelled gate or stage is refused at construction", _bad, [True, True])
_typo = sync.Step("x", "aaif-sync", "sync.py")
_typo.gate = "report_only"          # past the constructor, by force
_fell = []
try:
    sync.step_cmd(_typo, True, True)
except ValueError as e:
    _fell.append("unknown gate" in str(e))
check("step_cmd refuses an unknown gate rather than handing it --write", _fell, [True])
_incoherent = [("p", [sync.Step("x", "aaif-sync", "sync.py", gate=sync.READ_ONLY,
                                stage=sync.EXECUTE)])]
_caught = []
with mock.patch.object(sync, "PHASES", _incoherent):
    try:
        sync.assert_gate_stage_coherent()
    except AssertionError as e:
        _caught.append("read-only" in str(e) and "execute" in str(e))
check("a read-only execute step is refused at import", _caught, [True])
_dup = [("p", [sync.Step("x", "aaif-sync", "sync.py"), sync.Step("x", "aaif-sync", "sync.py")])]
_caught = []
with mock.patch.object(sync, "PHASES", _dup):
    try:
        sync.assert_gate_stage_coherent()
    except AssertionError as e:
        _caught.append("duplicate" in str(e))
check("a duplicate step name is refused at import", _caught, [True])

# --- the run directory is fresh, and the parent private ------------------------
with tempfile.TemporaryDirectory() as _td:
    _base = os.path.join(_td, "reports")
    _a = sync.fresh_run_dir(_base, "STAMP")
    _b = sync.fresh_run_dir(_base, "STAMP")
    check("two runs in one second get two directories",
          (os.path.basename(_a), os.path.basename(_b)), ("STAMP", "STAMP-2"))
    check("...both 0700, and the parent too",
          [oct(os.stat(p).st_mode & 0o777) for p in (_base, _a, _b)], ["0o700"] * 3)

# --- a failed step, end to end ------------------------------------------------
_rc, _seen = _drive(["clean"], code=1)
_e = _LAST_MANIFEST["steps"][0]
check("a failed step is FAILED with exit 1, its log kept, its findings kept if written",
      (_rc, _e["outcome"], _e["exit"], _e["log"], _e["findings"]),
      (1, sync.FAILED, 1, "clean.log", "clean.json"))
check("...and RESULT says failure", _LAST_STDOUT[0].count("RESULT: failure"), 1)
_rc, _seen = _drive(["clean"], code=1, findings=False)
check("a failed step that wrote no findings file has no pointer to one",
      _LAST_MANIFEST["steps"][0]["findings"], None)

# --- a failed render never changes the exit code, and is recorded -------------
_rc, _seen = _drive(["clean"], code=0, render_code=1)
check("a failed render keeps the run's exit code", _rc, 0)
check("...says so on stdout with the by-hand command",
      ("render FAILED (exit 1)" in _LAST_STDOUT[0], "render_report.py" in _LAST_STDOUT[0]),
      (True, True))
check("...and is recorded in the manifest",
      _LAST_MANIFEST["render"], {"ok": False, "exit": 1, "log": "render.log"})
_rc, _seen = _drive(["clean"], code=0)
check("a good render is recorded too, and named on stdout",
      (_LAST_MANIFEST["render"]["ok"], "HTML report: " in _LAST_STDOUT[0]), (True, True))

# --- the runner and the renderer agree: render a manifest sync.py wrote ------
import render_report as rr  # noqa: E402  (the test may read lib; sync.py may not)
check("every runner outcome has a pill tone in the renderer",
      set(rr.TONE), {sync.IN_SYNC, sync.WROTE, sync.DRIFT, sync.PARTIAL, sync.SKIPPED,
                     sync.FAILED, sync.NOT_RUN})
with tempfile.TemporaryDirectory() as _td:
    _rc, _seen = _drive(["preflight"], code=2, log_text="3 awaiting review\n", keep=_td)
    _run = os.path.join(_td, sorted(os.listdir(_td))[-1])
    with contextlib.redirect_stdout(io.StringIO()):
        _rr = rr.main([_run])
    _page = open(os.path.join(_run, rr.PAGE), encoding="utf-8").read()
    check("render_report draws the manifest the runner wrote", _rr, 0)
    check("...with every step on it, run or not",
          all('id="%s"' % n in _page and 'id="log-%s"' % n in _page for n in sync.STEP_NAMES),
          True)
    check("...and the two that ran carry their logs",
          _page.count("3 awaiting review"), 2)
    check("...and the page is private", oct(os.stat(os.path.join(_run, rr.PAGE)).st_mode & 0o777),
          "0o600")

# --- the two portable findings writers emit the shape lib defines -------------
import importlib.util as _ilu  # noqa: E402
sys.path.insert(0, os.path.join(sync.REPO, "lib"))
from aaif_events import findings as _fd  # noqa: E402


def _load(path):
    spec = _ilu.spec_from_file_location(os.path.basename(path)[:-3], path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_clean = _load(_by["clean"].path)
_intake = _load(_by["triage"].path)
_ref = set(_fd.Report("x").to_dict())
_cdoc = _clean.build_findings([], [{"row": 4, "who": "Ada", "issue": "missing email"}])
_idoc = _intake.build_findings({"Organizers": [{"row": 2, "status": "Prospect"}], "Hosts": [],
                                "Speakers": []})
check("clean.py's portable writer emits lib's shape", set(_cdoc) == _ref, True)
check("intake.py's portable writer emits lib's shape", set(_idoc) == _ref, True)
check("...with severities lib accepts",
      all(f["severity"] in _fd.SEVERITIES for d in (_cdoc, _idoc) for f in d["findings"]), True)

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

# --- the run's Slack memo lives and dies with the run directory ---------------
# It must sit inside the run dir (0700, gitignored, deleted with the reports), the
# name must match what the lib reads, and the engine must otherwise see our env.
sys.path.insert(0, os.path.join(sync.REPO, "lib"))
from aaif_events import slack as _slack  # noqa: E402
check("the runner's memo variable is the one the lib reads",
      sync.SLACK_MEMO_ENV, _slack.RUN_MEMO_ENV)
_env = sync.step_env("/r/2026-01-01T000000Z", run_writes=False)
check("the memo path is inside the run directory",
      os.path.dirname(_env[sync.SLACK_MEMO_ENV]), "/r/2026-01-01T000000Z")
check("the engine still inherits the runner's environment",
      _env.get("PATH"), os.environ.get("PATH"))
with mock.patch.object(sync.subprocess, "run") as _run, \
        tempfile.TemporaryDirectory() as _td:
    _run.return_value.returncode = 0
    sync.run_step(sync.selected([], False)[0][1], os.path.join(_td, "x.log"), False, False)
    check("run_step spawns the engine with the memo in its env",
          _run.call_args.kwargs["env"][sync.SLACK_MEMO_ENV],
          os.path.join(_td, sync.SLACK_MEMO_FILE))

# --- the Google read memo is a report-run thing only ---------------------------
from aaif_events import gws as _gws  # noqa: E402
check("the runner's read-memo variable is the one the lib reads",
      sync.GWS_MEMO_ENV, _gws.READ_MEMO_ENV)
check("a report run gets a read memo inside its run directory",
      sync.step_env("/r/s", run_writes=False).get(sync.GWS_MEMO_ENV),
      os.path.join("/r/s", sync.GWS_MEMO_FILE))
check("a run that asked to write gets NO read memo, for any step",
      sync.GWS_MEMO_ENV in sync.step_env("/r/s", run_writes=True), False)
with mock.patch.dict(os.environ, {sync.GWS_MEMO_ENV: "/elsewhere"}):
    check("an inherited read memo never leaks into a write run",
          sync.GWS_MEMO_ENV in sync.step_env("/r/s", run_writes=True), False)
_access = [st for _ph, st in sync.selected([], False) if st.name == "access"][0]
with mock.patch.object(sync.subprocess, "run") as _run, \
        tempfile.TemporaryDirectory() as _td:
    _run.return_value.returncode = 0
    sync.run_step(_access, os.path.join(_td, "access.log"), True, False)
    check("access — never written — still gets no read memo in a --write run",
          sync.GWS_MEMO_ENV in _run.call_args.kwargs["env"], False)

# --- the memos go when the run does --------------------------------------------
# They hold whole intake tabs and Slack profiles; nothing reads them after the
# last step, so a finished run must not leave them in sync-reports/.
check("a --write run still gets the Slack memo",
      sync.SLACK_MEMO_ENV in sync.step_env("/r/s", run_writes=True), True)
_memo_seen = []
_inner = _fake_engine([], 0, "")


def _engine_that_memoises(cmd, stdout=None, stderr=None, **kw):
    for var in (sync.SLACK_MEMO_ENV, sync.GWS_MEMO_ENV):
        path = (kw.get("env") or {}).get(var)
        if path:
            with open(path, "a") as fh:
                fh.write('["k", "v"]\n')
            _memo_seen.append(path)
    return _inner(cmd, stdout=stdout, stderr=stderr, **kw)


with mock.patch.object(sync.subprocess, "run", _engine_that_memoises), \
        tempfile.TemporaryDirectory() as _td, \
        contextlib.redirect_stdout(io.StringIO()):
    sync.main(["chapters", "--report-dir", _td])
    check("engines were handed memo paths", bool(_memo_seen), True)
    check("no memo file survives a finished run",
          [p for p in set(_memo_seen) if os.path.exists(p)], [])
_rd = tempfile.mkdtemp()
for _n in (sync.SLACK_MEMO_FILE, sync.GWS_MEMO_FILE):
    open(os.path.join(_rd, _n), "w").close()
sync.drop_memos(_rd)
sync.drop_memos(_rd)   # idempotent: the atexit hook runs it a second time
check("drop_memos removes both files and tolerates their absence",
      sorted(os.listdir(_rd)), [])
os.rmdir(_rd)

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("sync: all checks passed")
