#!/usr/bin/env python3
"""Run the whole AAIF estate sync in dependency order, and print a PII-free summary.

**This file is the one definition of the pipeline.** The `aaif-sync` skill
documents it, `nightly.py` wraps it for CI, and the per-phase skills
(`aaif-sync-chapters`, `aaif-sync-organizers`, `aaif-sync-slack`) document
their own engine in detail. Anything that needs to know "what runs, in what
order, behind which gate" reads `PHASES` below rather than restating it —
a second copy of an order this load-bearing is how two callers come to
disagree about whether the CRM is written before access is granted.

The order is not arbitrary and must not be reordered:

    clean      an unresolved city is invisible to every step below
    triage     only Accepted / Existing (from MLOps) flow onward
    chapters   a net-new city needs its feed row before anything hangs off it
    organizers the same accepted list reaches the About doc, the CRM and the grant
    resources  records the folder and channels that now exist
    slack      creates what `resources --plan` named, then invites people into it
    luma       every chapter row's page is still live
    verify     the independent check, from a different code path

Each step runs as a subprocess; its FULL report — which names real people and
their email addresses — goes only to a log file under a gitignored directory.
**This script's own stdout never contains a person**: on a public repo a CI job
log is a publication, so the summary is step names, outcomes, durations and log
paths, nothing else. Any print added here must be composed only of fixed
strings and values this script computed itself, never step output.

Four gates, because "can this run unattended" is not one question:

  OPEN         --write passes through.
  REPORT_ONLY  never receives --write, whatever the runner was told. `access`
               grants standing Drive access to addresses typed into a public
               form, and Drive may email the person as a side effect.
  APPROVAL     needs --i-have-approval as well as --write, and is refused
               outright under --unattended: these notify or add real people.
  READ_ONLY    has no write mode at all.

Exit codes, matching the per-engine convention:
    0  everything in sync
    2  drift, writes applied, partial coverage, or a gate needs a human
    1  any step failed
"""

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.abspath(os.path.join(HERE, "..", ".."))
REPO = os.path.abspath(os.path.join(SKILLS, ".."))

OPEN, REPORT_ONLY, APPROVAL, READ_ONLY = "open", "report-only", "approval", "read-only"

#: Steps that take --redact/--no-redact. The runner passes --no-redact: the
#: logs are 0600 files in a 0700 gitignored directory, the NEEDS-A-HUMAN note
#: needs the real addresses in access.log, and the engines' CI default (redact
#: when CI is set) is aimed at a direct terminal run whose stdout IS the CI
#: log — which this runner's stdout never carries.
#: `provision` and `directory` are deliberately absent: neither takes the flag.
#: Adding them here would append an argument argparse rejects, and every run of
#: those two would die on a usage error — the test pins this set against the
#: scripts' own source so the two cannot drift apart again.
REDACTING = frozenset({
    "chapters", "about", "crm", "access", "resources", "invite",
})


class Step:
    """One script in the pipeline: where it lives, how it is called, its gate."""

    def __init__(self, name, skill, script, args=(), gate=OPEN, why=""):
        self.name, self.skill, self.script = name, skill, script
        self.args, self.gate, self.why = list(args), gate, why

    @property
    def path(self):
        sub = "migrations" if self.script.startswith("migrate_") else "scripts"
        return os.path.join(SKILLS, self.skill, sub, self.script)


#: The pipeline. Phase name -> the steps it runs, in order.
PHASES = (
    ("clean", [
        Step("clean", "aaif-clean-data", "clean.py", ["scan"], READ_ONLY,
             "an unresolved city is invisible to every step below"),
    ]),
    ("triage", [
        Step("triage", "aaif-triage-intake", "intake.py", [], READ_ONLY,
             "only Accepted / Existing (from MLOps) flow onward — a human decides"),
    ]),
    ("chapters", [
        Step("chapters", "aaif-sync-chapters", "sync_chapters.py", [], OPEN,
             "a net-new city needs its feed row before anything hangs off it"),
    ]),
    ("organizers", [
        Step("about", "aaif-sync-organizers", "sync_about.py", [], OPEN,
             "same accepted list as the feed, so doc and website row agree"),
        Step("crm", "aaif-sync-organizers", "sync_crm.py", [], OPEN,
             "the CRM decides who gets Drive access, so it lands first"),
        Step("access", "aaif-sync-organizers", "sync_access.py", [], REPORT_ONLY,
             "grants standing Drive access from a public form — a human every time"),
    ]),
    ("resources", [
        Step("resources", "aaif-sync-slack", "sync_resources.py", [], OPEN,
             "records the folder and channels that now exist"),
    ]),
    ("slack", [
        Step("provision", "aaif-sync-slack", "provision_channels.py", [], APPROVAL,
             "creates and renames real rooms; renames run before creates"),
        Step("invite", "aaif-sync-slack", "invite_organizers.py", [], APPROVAL,
             "adds real people to a channel — needs the channels from provision"),
        Step("directory", "aaif-sync-slack", "post_country_directory.py", [], APPROVAL,
             "posts in a shared country room, after its people are in it"),
    ]),
    ("luma", [
        Step("luma", "aaif-sync-chapters", "sync_chapters.py", ["--audit-luma"],
             READ_ONLY,
             "every chapter row's Stay Updated page is still live"),
    ]),
    ("verify", [
        Step("verify", "aaif-audit-slack", "audit_organizers.py", [], READ_ONLY,
             "the independent check, from a different code path"),
    ]),
)

PHASE_NAMES = [p for p, _ in PHASES]

#: Phases a scheduled job runs. `slack` is absent because every step in it is
#: APPROVAL-gated and a scheduled job must never hold that approval. `luma` is
#: absent because luma.com rate-limits the sweep: a 96-row run draws a 429 with
#: no Retry-After (measured 2026-09-17), so unattended it would stop at row 2
#: and report PARTIAL every night — and a check that can never complete
#: unattended teaches operators to ignore the one signal it shares with real
#: findings. Both stay manual. `verify` is absent because the Slack audit's
#: first run takes ~20 minutes on a 30k-member workspace.
UNATTENDED_PHASES = ("clean", "chapters", "organizers", "resources")

IN_SYNC, DRIFT, WROTE, FAILED, PARTIAL, SKIPPED = (
    "in sync", "DRIFT", "wrote+verified", "FAILED", "PARTIAL", "skipped")


def classify(code, wrote_marker, write_mode, partial_marker=False):
    """Map a step's exit code (+ two log markers) onto an outcome.

    'Verified:' only ever follows an applied write, so it separates "--write
    had nothing to do" from "--write wrote". Exit 2 in write mode means work is
    still pending (sync_chapters holds back a row with no live Luma page) and
    classifies as DRIFT even when part of the run wrote — the drift is what
    needs eyes, and the log's 'Verified:' line records the write. 'PARTIAL:'
    means the step involuntarily skipped part of its coverage; it beats
    in-sync/drift because a half-checked run must never read as a healthy one,
    but never FAILED, which is strictly worse news.
    """
    if code not in (0, 2):
        return FAILED
    if partial_marker:
        return PARTIAL
    if code == 0:
        return WROTE if (write_mode and wrote_marker) else IN_SYNC
    return DRIFT


def step_cmd(step, write_mode, approved):
    """The argv for one step, and the write mode it actually ran under.

    Every gate is applied here, in one place, so no caller can route around one
    by assembling its own command line.
    """
    if step.gate in (READ_ONLY, REPORT_ONLY):
        write_mode = False
    elif step.gate == APPROVAL and not approved:
        write_mode = False
    cmd = [sys.executable, step.path] + step.args
    if write_mode:
        cmd.append("--write")
        if step.gate == APPROVAL:
            cmd.append("--i-have-approval")
    if step.name in REDACTING:
        cmd.append("--no-redact")
    return cmd, write_mode


def run_step(step, log_path, write_mode, approved):
    cmd, write_mode = step_cmd(step, write_mode, approved)
    t0 = time.monotonic()
    # 0o600: the log holds names and emails; no other local user gets to read
    # it just because the checkout happens to be world-readable.
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as log:
        log.write("$ %s\n\n" % " ".join(cmd))
        log.flush()
        # stderr merges in too: the engines print progress and gws retry notes
        # there, and a FAILED outcome is undiagnosable without it.
        code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    # Explicit encoding: step output is full of accented city and channel names
    # (españa, Montréal), and a LANG=C container would otherwise open this as
    # ASCII and crash the runner AFTER a step already applied its writes.
    with open(log_path, encoding="utf-8", errors="replace") as log:
        lines = log.readlines()
    return (classify(code, any(l.startswith("Verified:") for l in lines),
                     write_mode, any(l.startswith("PARTIAL:") for l in lines)),
            code, time.monotonic() - t0)


STEP_NAMES = [s.name for _, steps in PHASES for s in steps]


def selected(names, unattended):
    """The (phase, step) pairs to run, in pipeline order.

    A name may be a PHASE (`organizers`) or a single STEP (`crm`). Both are
    accepted because both are real requests — "sync the organizers" and "just
    re-run the CRM engine" — and because `nightly.py` has always been called
    with step names. Order always comes from PHASES, never from the order the
    names were typed: the caller does not get to reorder the pipeline by
    listing `access` before `crm`.
    """
    want = list(names or (UNATTENDED_PHASES if unattended else PHASE_NAMES))
    out = []
    for phase, steps in PHASES:
        for s in steps:
            if phase in want or s.name in want:
                out.append((phase, s))
    return out


def summary_notes(by_name, write_mode):
    """The PII-free RESULT lines for a run; pure, so the tests can pin them."""
    results = [o for o in by_name.values() if o != SKIPPED]
    if FAILED in results:
        return ["RESULT: failure — read the log(s) above. Later steps still "
                "ran; the pipeline's report modes are read-only and independent."]
    notes = []
    # WROTE and DRIFT are separate notes: a write run can exit 2 having written
    # nothing (every proposal held back), and "changes were applied" would then
    # mask a chapter stuck behind a missing Luma page.
    if WROTE in results:
        notes.append("changes were applied and verified")
    pending_access = by_name.get("access") == DRIFT
    gated = sorted(n for n, o in by_name.items() if o == SKIPPED)
    other_drift = any(o == DRIFT for n, o in by_name.items() if n != "access")
    if other_drift:
        notes.append("drift remains — a step held back or re-proposed changes; "
                     "read its log" if write_mode
                     else "drift — re-run the flagged step(s) with --write after review")
    if pending_access:
        notes.append("access has pending Drive grants/lock — NEEDS A HUMAN: read "
                     "access.log, then run sync_access.py --write by hand (this "
                     "runner never grants Drive access)")
    if gated:
        notes.append("%s did not run — they add or notify real people and need "
                     "--i-have-approval from a human at the terminal"
                     % ", ".join(gated))
    if PARTIAL in results:
        notes.append("PARTIAL coverage — a step involuntarily skipped part of "
                     "its scope (usually Slack auth); fix it and re-run")
    return ["RESULT: " + "; ".join(notes) + "."] if notes else \
           ["RESULT: everything in sync."]


def exit_code(by_name):
    """0 all in sync; 2 drift/writes/partial/gated; 1 any failure."""
    results = list(by_name.values())
    if FAILED in results:
        return 1
    return 2 if any(o in (WROTE, DRIFT, PARTIAL, SKIPPED) for o in results) else 0


def guard_report_dir(report_dir):
    """Refuse to write PII logs anywhere git would happily commit them.

    The probe carries a TRAILING SEPARATOR, and that is the whole trick.
    `sync-reports/` in .gitignore is a directory-only pattern, and
    `git check-ignore` treats a path that does not exist yet as a *file* — so
    on a fresh checkout, before the first run has created the directory, the
    pattern does not match and this guard aborts the very run it exists to
    protect. `nightly.py` carried this bug unnoticed for as long as the
    directory happened to already exist on the machine it ran on. Asking about
    `<path>/` matches the directory pattern whether or not anything is there.
    """
    probe = os.path.abspath(report_dir)
    if probe.startswith(REPO + os.sep):
        if subprocess.run(["git", "-C", REPO, "check-ignore", "-q",
                           probe + os.sep]).returncode:
            sys.exit("ABORT: %s is inside the repo but NOT gitignored — these "
                     "logs hold names and emails and this repo is public. Add it "
                     "to .gitignore (sync-reports/ already is) or point "
                     "--report-dir elsewhere." % probe)


def build_parser():
    ap = argparse.ArgumentParser(
        description="Run the AAIF estate sync pipeline in dependency order.")
    # No argparse `choices` with nargs="*": some Python versions validate the
    # empty default against it and reject a bare `sync.py`.
    ap.add_argument("phases", nargs="*", metavar="phase|step",
                    help="phases (%s) or individual steps to run; default is "
                         "all, always in pipeline order"
                         % "/".join(PHASE_NAMES))
    ap.add_argument("--write", action="store_true",
                    help="apply each step's proposal (report-only by default). "
                         "Never reaches a REPORT_ONLY or READ_ONLY step.")
    ap.add_argument("--i-have-approval", action="store_true", dest="approved",
                    help="the human at the terminal has approved the Slack "
                         "writes: creating rooms, adding people, posting.")
    ap.add_argument("--unattended", action="store_true",
                    help="scheduled-job mode: runs %s only, and refuses the "
                         "Slack approval gate outright."
                         % "/".join(UNATTENDED_PHASES))
    ap.add_argument("--report-dir", default=os.path.join(REPO, "sync-reports"),
                    help="where the full (PII-carrying) logs land; must be "
                         "gitignored and must never be uploaded anywhere public")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)

    bogus = [p for p in a.phases if p not in PHASE_NAMES and p not in STEP_NAMES]
    if bogus:
        ap.error("unknown phase/step %s — phases are %s; steps are %s"
                 % (", ".join(map(repr, bogus)), "/".join(PHASE_NAMES),
                    "/".join(STEP_NAMES)))
    if a.unattended and a.approved:
        # The whole point of the approval flag is that a human is present.
        ap.error("--i-have-approval cannot be combined with --unattended: the "
                 "Slack steps notify and add real people, and a scheduled job "
                 "is by definition nobody's approval.")

    steps = selected(a.phases, a.unattended)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    run_dir = os.path.join(a.report_dir, stamp)
    guard_report_dir(a.report_dir)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    os.chmod(run_dir, 0o700)   # makedirs' mode is umask-masked; be explicit

    print("aaif-sync — %s mode%s — %d step(s)"
          % ("write" if a.write else "report",
             ", unattended" if a.unattended else "", len(steps)))
    print("full reports (contain names/emails — NOT for public logs): %s\n" % run_dir)

    by_name, phase_shown = {}, None
    for phase, step in steps:
        if phase != phase_shown:
            print("  [%s]" % phase)
            phase_shown = phase
        if step.gate == APPROVAL and a.write and not a.approved:
            by_name[step.name] = SKIPPED
            print("    %-10s %-15s (needs --i-have-approval)" % (step.name, SKIPPED))
            continue
        outcome, code, secs = run_step(
            step, os.path.join(run_dir, step.name + ".log"), a.write, a.approved)
        by_name[step.name] = outcome
        note = ""
        if step.gate == REPORT_ONLY:
            note = "  (report mode — never written by this runner)"
        elif step.gate == READ_ONLY:
            note = "  (read-only)"
        print("    %-10s %-15s exit %d  %4.0fs  %s.log%s"
              % (step.name, outcome, code, secs, step.name, note))

    print()
    for line in summary_notes(by_name, a.write):
        print(line)
    return exit_code(by_name)


if __name__ == "__main__":
    sys.exit(main())
