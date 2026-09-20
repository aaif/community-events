#!/usr/bin/env python3
"""Run the whole AAIF estate sync in dependency order, and print a PII-free summary.

**This file is the one definition of the pipeline.** The `aaif-sync` skill
documents it, `nightly.py` wraps it for CI, and the per-phase skills
(`aaif-sync-chapters`, `aaif-sync-organizers`, `aaif-sync-slack`) document
their own engine in detail. Anything that needs to know "what runs, in what
order, behind which gate" reads `PHASES` below rather than restating it —
a second copy of an order this load-bearing is how two callers come to
disagree about whether the CRM is written before access is granted.

The order is not arbitrary and must not be reordered: seven phases —
preflight, chapters, organizers, events, speakers, hosts, workspace — each a
subject, each running its steps gather -> plan -> execute. `PHASES` below
carries the reason for every step and its place.

Each step runs as a subprocess; its FULL report — which names real people and
their email addresses — goes only to a log file under a gitignored directory.
**This script's own stdout never contains a person**: on a public repo a CI job
log is a publication, so the summary is step names, outcomes, durations and log
paths, nothing else. Any print added here must be composed only of fixed
strings and values this script computed itself, never step output.

Five gates, because "can this run unattended" is not one question:

  OPEN         --write passes through.
  REPORT_ONLY  never receives --write, whatever the runner was told. `access`
               grants standing Drive access to addresses typed into a public
               form, and Drive may email the person as a side effect.
  APPROVAL     needs --i-have-approval as well as --write: these notify or
               add real people. Unattended it behaves as REPORT_ONLY.
  READ_ONLY    has no write mode at all.
  HUMAN        runs, read-only, and **summarises without deciding**. Triage is
               a judgement about a person, and a judgement nobody made is not a
               judgement — so no mode of this runner supplies one. What it can
               do with nobody watching is say how deep the queue is, which is
               the difference between "nothing to do" and "nobody has looked".
               The step exits 2 while rows await a decision, and the summary
               names the skill a human runs to work them.

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

OPEN, REPORT_ONLY, APPROVAL, READ_ONLY, HUMAN = (
    "open", "report-only", "approval", "read-only", "human")

#: The second axis. A phase is a SUBJECT (chapters, organizers, hosts); a stage
#: is the verb applied to it, and every phase runs its stages in this order.
#:
#: The engines already worked this way without naming it — report -> approve ->
#: write IS plan -> execute — so a stage is a label on work that exists, not a
#: new kind of run. What the axis buys is the missing half: `gather` steps,
#: which measure and never propose, were previously scattered into one audit
#: skill at the end, where a finding about chapters arrived long after the
#: chapters work had been done.
#:
#:   gather   measure the world. Read-only in every mode, always runs.
#:   plan     propose changes against what gather found. The report.
#:   execute  apply them, subject to the step's gate.
GATHER, PLAN, EXECUTE = "gather", "plan", "execute"
STAGES = (GATHER, PLAN, EXECUTE)

#: This runner keeps NO state between runs. It writes logs and reads back only
#: the one it just wrote; there is no checkpoint, no resume, no memo of a
#: previous run. Every conclusion comes from data observed during the run that
#: printed it.
#:
#: One thing does outlive a run, deliberately: `.slack-audit-cache/`. Six steps
#: read it, and users.json alone takes ~20 minutes to page on a 30k-member
#: workspace, so caching it locally is the point. It is a memo of the WORKSPACE,
#: not state of the pipeline.
#:
#: The expiry lives in the shared `jsoncache` module (MAX_AGE, one day) rather than
#: here, because it has to hold for a standalone `audit_topics.py` run too, not
#: only for steps this runner happens to schedule. A cache past it is discarded
#: and refetched by whichever step reads it first, announced on that step's log.
#: The runner therefore needs no refresh policy of its own, and must not force
#: one: throwing away an hour-old pull on every run is the opposite of caching.

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
    "chapters", "resources", "about", "access", "crm", "invite", "luma",
})


class Step:
    """One script in the pipeline: where it lives, how it is called, its gate."""

    def __init__(self, name, skill, script, args=(), gate=OPEN, stage=PLAN, why="",
                 cached=False):
        self.name, self.skill, self.script = name, skill, script
        self.args, self.gate, self.stage, self.why = list(args), gate, stage, why
        #: Reads the shared `.slack-audit-cache` (see the cache note above).
        self.cached = cached

    @property
    def path(self):
        sub = "migrations" if self.script.startswith("migrate_") else "scripts"
        return os.path.join(SKILLS, self.skill, sub, self.script)


#: The pipeline, on two axes: PHASE (the subject) x STAGE (the verb).
#:
#: Phase order is dependency order and is not negotiable. Stage order within a
#: phase is always gather -> plan -> execute, so a phase measures the world
#: before it proposes anything, and proposes before it writes.
#:
#: The Slack audit is deliberately NOT a phase. Its engines answer questions
#: that belong to different subjects — "does this chapter have a room" is a
#: chapters question, "is the right person in it" is an organizers question,
#: "are the subject rooms alive" is a topics question — and
#: `audit_organizers.render_body()` already returns those first two as separate
#: fragments for exactly that reason. Each engine now gathers for the phase
#: whose question it answers.
PHASES = (
    # 1. Is the source sound? Everything below reads the intake, so a header
    #    that moved or a city that never resolved is a bug the whole pipeline
    #    inherits. Cheapest place to find it is before anything acts on it.
    ("preflight", [
        Step("clean", "aaif-clean-data", "clean.py", ["scan"], READ_ONLY, GATHER,
             "unresolved cities and malformed rows, before anything reads them"),
        Step("triage", "aaif-triage-intake", "intake.py", [], HUMAN, GATHER,
             "how deep the decision queue is — summarised, never decided"),
    ]),
    # 2. Chapters: does the chapter exist, on the sheet, in Drive, in Slack?
    #    `--planned-ok` is what lets the audit gather here rather than only at
    #    the end: without it, a sheet naming channels that provisioning has not
    #    created yet aborts the run.
    ("chapters", [
        Step("coverage", "aaif-audit-slack", "audit_organizers.py", ["--planned-ok"],
             READ_ONLY, GATHER,
             "which chapters have a room and a folder, from live Slack and Drive",
             cached=True),
        Step("chapters", "aaif-sync-chapters", "sync_chapters.py", [], OPEN, PLAN,
             "a net-new city needs its feed row before anything hangs off it"),
        Step("resources", "aaif-sync-slack", "sync_resources.py", [], OPEN, PLAN,
             "records the folder and channels that now exist"),
        Step("provision", "aaif-sync-slack", "provision_channels.py", [], APPROVAL,
             EXECUTE, "creates and renames real rooms; renames before creates"),
    ]),
    # 3. Organizers: the only people who get a name in an About doc and a grant
    #    on a chapter folder. `sync_access` reads ACCESS_TABS = ("Organizers",)
    #    from the INTAKE, never the CRM, so this phase waits on nothing below.
    ("organizers", [
        Step("identity", "aaif-sync-organizers", "resolve_slack_ids.py", [],
             READ_ONLY, GATHER,
             "which accepted organizer maps to which Slack account, by email",
             cached=True),
        Step("about", "aaif-sync-organizers", "sync_about.py", [], OPEN, PLAN,
             "same accepted list as the feed, so doc and website row agree"),
        Step("access", "aaif-sync-organizers", "sync_access.py", [], REPORT_ONLY,
             PLAN, "grants standing Drive access from a public form — a human every time"),
        Step("crm", "aaif-sync-organizers", "sync_crm.py", [], OPEN, EXECUTE,
             "one row per person per chapter, merged across every role — see the note below"),
        Step("invite", "aaif-sync-slack", "invite_organizers.py", [], APPROVAL,
             EXECUTE, "adds real people to their organizer room"),
        Step("directory", "aaif-sync-slack", "post_country_directory.py", [],
             APPROVAL, EXECUTE, "a shared country room needs its people before its signpost"),
    ]),
    # 4. Events: estate-wide only. Everything per-event (create_event,
    #    update_event, luma_push) is interactive one-event work and is NOT a
    #    sync step. What IS estate-wide: is every chapter's page still live, and
    #    which chapters have gone quiet. There is no execute — making a Luma
    #    page is manual, and nothing in this repo writes the Past Events tab.
    ("events", [
        # activity comes first: `health` reads its cache for the last-human-
        # message signal, and `topics` and `members` (later phases) take their
        # dormancy numbers from the same file. Producer before consumers.
        Step("activity", "aaif-audit-slack", "audit_activity.py", [], READ_ONLY,
             GATHER, "last human message and posting volume — the measurement layer",
             cached=True),
        Step("health", "aaif-sync-chapters", "chapter_health.py", [], READ_ONLY,
             GATHER, "recorded events and last human Slack message, per chapter",
             cached=True),
        Step("luma", "aaif-sync-chapters", "sync_chapters.py", ["--audit-luma"],
             READ_ONLY, GATHER,
             "every chapter row's Stay Updated page — a dead CTA is invisible otherwise"),
    ]),
    # 5. Speakers and topics: what the community talks about. The subject rooms
    #    are the estate-wide half of this question; the Speakers tab is the
    #    per-person half.
    #
    #    No execute of its own YET. Speakers reach exactly one surface, the
    #    chapter CRM, and that write cannot be scoped by role: `merge_people`
    #    combines a person's rows ACROSS role tabs into a single row reading
    #    "Organizer/Speaker", with expertise joined from both. A role-scoped
    #    pass would write the narrower half and the next pass could not see the
    #    other application to widen it. `sync_crm`'s own held-row message names
    #    the prerequisite — "held until per-role CRM tabs exist" — so per-role
    #    execution is unblocked by that migration, not by this file.
    ("speakers", [
        # `activity` ran in the events phase; its cache is where the dormancy
        # numbers on this report come from.
        Step("topics", "aaif-audit-slack", "audit_topics.py", [], READ_ONLY, GATHER,
             "are the subject rooms alive, and can a newcomer find them",
             cached=True),
    ]),
    # 6. Hosts: venues. Same shared-write constraint as speakers, and thinner
    #    still — there is no estate-wide venue engine yet, so this phase has
    #    only what the CRM pass already carries. It exists as a named phase so
    #    the gap is visible rather than implied.
    ("hosts", []),
    # 7. The workspace itself, which belongs to no single subject: accounts,
    #    channel counts, what an ordinary member sees.
    ("workspace", [
        Step("members", "aaif-audit-slack", "audit_members.py", [], READ_ONLY,
             GATHER, "accounts, channel sizes, the newcomer experience — reads "
             "the activity cache the events phase filled",
             cached=True),
    ]),
)

PHASE_NAMES = [p for p, _ in PHASES]

#: Phases a scheduled job runs. The APPROVAL steps inside them run read-only
#: (see `step_cmd`). `events` is absent because luma.com rate-limits the
#: `luma` sweep: a 96-row run draws a 429 with no Retry-After (measured
#: 2026-09-17), so unattended it would report PARTIAL every night, and a check
#: that can never complete teaches operators to ignore the one signal it
#: shares with real findings. `speakers` and `workspace` are absent because
#: they only publish audits. `coverage` is the one Slack pull that does run
#: unattended (see the cache note above for what that costs cold).
UNATTENDED_PHASES = ("preflight", "chapters", "organizers")

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


def step_cmd(step, write_mode, approved, unattended=False):
    """The argv for one step, and the write mode it actually ran under.

    Every gate is applied here, in one place, so no caller can route around one
    by assembling its own command line.

    Unattended, an APPROVAL step is a REPORT_ONLY step: it runs read-only under
    both a scheduled report and a scheduled write, so the two agree on what is
    pending, and its DRIFT is the note that sends a human to a terminal — the
    same contract `access` already has. A scheduled job is nobody's approval.
    """
    if step.gate in (READ_ONLY, REPORT_ONLY, HUMAN):
        write_mode = False
    elif step.gate == APPROVAL and (unattended or not approved):
        write_mode = False
    cmd = [sys.executable, step.path] + step.args
    if write_mode:
        cmd.append("--write")
        if step.gate == APPROVAL:
            cmd.append("--i-have-approval")
    if step.name in REDACTING:
        cmd.append("--no-redact")
    return cmd, write_mode


def run_step(step, log_path, write_mode, approved, unattended=False):
    cmd, write_mode = step_cmd(step, write_mode, approved, unattended)
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
    # lstrip: the marker is the first word of a line, wherever the engine
    # indents it. sync_chapters prints its Luma-sweep PARTIAL two spaces in,
    # and a column-zero match classified a rate-limited sweep as DRIFT
    # (verified live 2026-09-20) — the one outcome the marker exists to prevent.
    marked = lambda tag: any(l.lstrip().startswith(tag) for l in lines)  # noqa: E731
    return (classify(code, marked("Verified:"), write_mode, marked("PARTIAL:")),
            code, time.monotonic() - t0)


STEP_NAMES = [s.name for _, steps in PHASES for s in steps]


def assert_stage_order():
    """Every phase lists its steps gather -> plan -> execute.

    The order is data, not code, so nothing stops a step being added in the
    wrong place — and the failure would be silent and wrong in the worst way:
    a plan proposing changes against a world it had not measured yet.
    """
    for phase, steps in PHASES:
        seen = [STAGES.index(x.stage) for x in steps]
        if seen != sorted(seen):
            raise AssertionError(
                "phase %r lists its steps out of stage order (%s); a phase must "
                "measure before it proposes and propose before it writes"
                % (phase, ", ".join("%s:%s" % (x.name, x.stage) for x in steps)))


assert_stage_order()


def selected(names, unattended, stages=None):
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
            if stages and s.stage not in stages:
                continue
            if phase in want or s.name in want:
                out.append((phase, s))
    return out


def summary_notes(by_name, write_mode):
    """The PII-free RESULT lines for a run; pure, so the tests can pin them."""
    results = list(by_name.values())
    if FAILED in results:
        return ["RESULT: failure — read the log(s) above. Later steps still "
                "ran; the pipeline's report modes are read-only and independent."]
    notes = []
    # WROTE and DRIFT are separate notes: a write run can exit 2 having written
    # nothing (every proposal held back), and "changes were applied" would then
    # mask a chapter stuck behind a missing Luma page.
    if WROTE in results:
        notes.append("changes were applied and verified")

    # Every note below is "(gate, outcome) -> what a human does next", because
    # DRIFT means something different behind each gate: a proposal to apply
    # (OPEN), a finding to fix at its source (READ_ONLY — there is no --write
    # to name), a queue to work (HUMAN), a grant to make by hand (REPORT_ONLY),
    # or a Slack change that needs BOTH flags (APPROVAL — --write alone skips
    # it). Partitioning by gate once keeps each note from having to subtract
    # every other note's set.
    gate_of = {s.name: s.gate for _p, s in selected([], False)}

    def names(outcome, gate):
        return sorted(n for n, o in by_name.items()
                      if o == outcome and gate_of.get(n) == gate)

    if names(DRIFT, OPEN):
        notes.append("drift remains — a step held back or re-proposed changes; "
                     "read its log" if write_mode
                     else "drift — re-run the flagged step(s) with --write after review")
    for outcome, reason in ((DRIFT, "propose Slack changes"), (SKIPPED, "did not run")):
        gated = names(outcome, APPROVAL)
        if gated:
            notes.append("%s %s — they add or notify real people; apply with "
                         "--write --i-have-approval from a human at the terminal"
                         % (", ".join(gated), reason))
    findings = names(DRIFT, READ_ONLY)
    if findings:
        notes.append("%s reported findings — these steps only measure, so there "
                     "is nothing to apply; fix each at its source and re-measure"
                     % ", ".join(findings))
    pending_access = names(DRIFT, REPORT_ONLY)
    if pending_access:
        notes.append("%s has pending Drive grants/lock — NEEDS A HUMAN: read "
                     "access.log, then run sync_access.py --write by hand (this "
                     "runner never grants Drive access)" % ", ".join(pending_access))
    needs_human = names(DRIFT, HUMAN)
    if needs_human:
        notes.append("%s has rows awaiting a decision — the runner summarised the "
                     "queue but will never decide it; nothing downstream moves "
                     "until a human works it (the aaif-triage-intake skill). The "
                     "full digest is in its log"
                     % ", ".join(needs_human))
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
    ap.add_argument("--stage", action="append", choices=STAGES, dest="stages",
                    help="run only these stages (repeatable). `--stage gather` "
                         "measures the whole estate and proposes nothing.")
    ap.add_argument("--unattended", action="store_true",
                    help="scheduled-job mode: runs %s only; the Slack "
                         "approval steps run read-only."
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

    steps = selected(a.phases, a.unattended, a.stages)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    run_dir = os.path.join(a.report_dir, stamp)
    guard_report_dir(a.report_dir)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    os.chmod(run_dir, 0o700)   # makedirs' mode is umask-masked; be explicit

    print("aaif-sync — %s mode%s — %d step(s)"
          % ("write" if a.write else "report",
             ", unattended" if a.unattended else "", len(steps)))
    print("full reports (contain names/emails — NOT for public logs): %s" % run_dir)
    print()

    by_name, phase_shown, stage_shown = {}, None, None
    for phase, step in steps:
        if phase != phase_shown:
            print("  [%s]" % phase)
            phase_shown, stage_shown = phase, None
        if step.stage != stage_shown:
            print("    · %s" % step.stage)
            stage_shown = step.stage
        if step.gate == APPROVAL and a.write and not a.approved and not a.unattended:
            by_name[step.name] = SKIPPED
            print("      %-10s %-15s (needs --i-have-approval)" % (step.name, SKIPPED))
            continue
        outcome, code, secs = run_step(
            step, os.path.join(run_dir, step.name + ".log"), a.write, a.approved,
            a.unattended)
        by_name[step.name] = outcome
        note = ""
        if step.gate == REPORT_ONLY:
            note = "  (report mode — never written by this runner)"
        elif step.gate == APPROVAL and a.unattended:
            note = "  (report mode — never written unattended)"
        elif step.gate == HUMAN:
            note = ("  (summary only — a human decides)" if outcome == DRIFT
                    else "  (summary only)")
        elif step.gate == READ_ONLY:
            note = "  (read-only)"
        print("      %-10s %-15s exit %d  %4.0fs  %s.log%s"
              % (step.name, outcome, code, secs, step.name, note))

    print()
    for line in summary_notes(by_name, a.write):
        print(line)
    return exit_code(by_name)


if __name__ == "__main__":
    sys.exit(main())
