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
their email addresses — goes only to files under a gitignored run directory:
its log, its findings file, and for the audits its own page.
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
import atexit
import datetime as dt
import json
import os
import shutil
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
GATES = (OPEN, REPORT_ONLY, APPROVAL, READ_ONLY, HUMAN)

#: Which gates make sense at which stage. A gather only measures, so it is
#: READ_ONLY or HUMAN; a plan proposes, so it is OPEN or REPORT_ONLY; an
#: execute applies, so it is OPEN or APPROVAL. Today's PHASES happen to obey
#: this; `assert_gate_stage_coherent` makes it a rule rather than a fact.
STAGE_GATES = {GATHER: (READ_ONLY, HUMAN), PLAN: (OPEN, REPORT_ONLY),
               EXECUTE: (OPEN, APPROVAL)}

#: This runner keeps NO state between runs. It writes logs and reads back only
#: the one it just wrote; there is no checkpoint, no resume, no memo of a
#: previous run. Every conclusion comes from data observed during the run that
#: printed it.
#:
#: One thing does outlive a run, deliberately: `.slack-audit-cache/`. Every
#: `cached=True` step reads it, and users.json alone takes ~20 minutes to page on a 30k-member
#: workspace, so caching it locally is the point. It is a memo of the WORKSPACE,
#: not state of the pipeline.
#:
#: The expiry lives in the shared `jsoncache` module (MAX_AGE, one day) rather than
#: here, because it has to hold for a standalone `audit_topics.py` run too, not
#: only for steps this runner happens to schedule. A cache past it is discarded
#: and refetched by whichever step reads it first, announced on that step's log.
#: The runner therefore needs no refresh policy of its own, and must not force
#: one: throwing away an hour-old pull on every run is the opposite of caching.
#:
#: WITHIN a run, the steps share two memos in the run directory (see
#: step_env): Slack identity lookups, always, and Google reads, in a report run
#: only. The runner deletes both when the run ends, so they are not state
#: between runs.

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
    "health", "identity",
})

#: Steps that render an HTML report and take `--out <basename>`. The runner
#: points each at `<run_dir>/<step>` so the page lands beside the step's log
#: instead of at the engine's default in the repo root, where a run's pages
#: pile up undated and the runner's stdout never mentions them. The test pins
#: this set against the scripts' own source, as it does for REDACTING.
RENDERS_HTML = frozenset({"coverage", "activity", "topics", "members", "audit"})

#: Steps that take `--json-out PATH` and write their report as data — measured
#: counts and findings in the shape `aaif_events.findings` defines. The runner
#: points each at `<run_dir>/<step>.json`; the page opens on those, and the
#: text log becomes the appendix. Pinned against the scripts' own source.
EMITS_FINDINGS = frozenset({
    "clean", "triage", "chapters", "luma", "health", "resources", "provision",
    "identity", "about", "access", "crm", "invite", "directory",
})

#: The run's own page. The runner RECORDS — outcomes, exit codes, durations,
#: which log is whose — into run.json, and a separate script DRAWS report.html
#: from that manifest and the logs. Two files, two jobs: the runner stays
#: lib-free and subprocess-only (a test pins that), the renderer may use the
#: design system, and a run can be re-rendered later from what it left behind.
RENDERER = os.path.join(HERE, "render_report.py")
MANIFEST = "run.json"


class Step:
    """One script in the pipeline: where it lives, how it is called, its gate."""

    def __init__(self, name, skill, script, args=(), gate=OPEN, stage=PLAN, why="",
                 cached=False):
        # Fail closed on a typo: `step_cmd` treats an unknown gate as an error
        # rather than as OPEN, and this is the earlier, louder place to say so.
        if gate not in GATES:
            raise ValueError("step %r: gate must be one of %r, not %r" % (name, GATES, gate))
        if stage not in STAGES:
            raise ValueError("step %r: stage must be one of %r, not %r" % (name, STAGES, stage))
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
        # Last, because it composes: the Slack audit as one page, opening on
        # where to focus, with four sections (chapters, organizers, topics,
        # members) drawn from the caches the gathers above just filled — not
        # from their pages — so it needs nothing but the order it sits in. The RUN's page (every step, every
        # log) is report.html, drawn by the renderer after the pipeline ends.
        Step("audit", "aaif-audit-slack", "summarize_audits.py", [], READ_ONLY,
             GATHER, "the Slack audit as one page — the four audits as appendices",
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
#: they only publish audits. `coverage` and `identity` share the one directory
#: pull that runs unattended (see the cache note above for what it costs cold);
#: the other unattended Slack reads are per-channel or per-email and cheap.
UNATTENDED_PHASES = ("preflight", "chapters", "organizers")

IN_SYNC, DRIFT, WROTE, FAILED, PARTIAL, SKIPPED = (
    "in sync", "DRIFT", "wrote+verified", "FAILED", "PARTIAL", "skipped")
#: The manifest's word for a step this invocation did not select. Never an
#: outcome the runner computes for by_name, so it never touches the exit code.
NOT_RUN = "not run"


def classify(code, wrote_marker, write_mode, partial_marker=False):
    """Map a step's exit code (+ the markers) onto an outcome.

    `wrote_marker` is True when the engine said it applied a write: `written`
    in its findings file (the one definition, read by `run_step`), or a
    'Verified:' log line for a step that writes no findings file. It separates
    "--write had nothing to do" from "--write wrote". Exit 2 in write mode
    means work is still pending (sync_chapters holds back a row with no live
    Luma page) and classifies as DRIFT even when part of the run wrote — the
    drift is what needs eyes, and the log records the write. 'PARTIAL:'
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


def step_cmd(step, write_mode, approved, unattended=False, out_dir=None):
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
    elif step.gate == APPROVAL:
        if unattended or not approved:
            write_mode = False
    elif step.gate != OPEN:
        # Fail closed: an unknown gate must never fall through to "--write".
        raise ValueError("step %r has an unknown gate %r" % (step.name, step.gate))
    cmd = [sys.executable, step.path] + step.args
    if write_mode:
        cmd.append("--write")
        if step.gate == APPROVAL:
            cmd.append("--i-have-approval")
    if step.name in REDACTING:
        cmd.append("--no-redact")
    if step.name in RENDERS_HTML and out_dir:
        cmd += ["--out", os.path.join(out_dir, step.name)]
    if step.name in EMITS_FINDINGS and out_dir:
        cmd += ["--json-out", os.path.join(out_dir, step.name + ".json")]
    return cmd, write_mode


#: A step that has not returned in an hour is hung, not slow: the longest
#: honest step (a cold 30k-member directory pull) takes ~20 minutes. Without a
#: budget a Slack call that never returns hangs the nightly forever, and a
#: missing run is harder to notice than a FAILED one.
STEP_TIMEOUT_S = 3600


#: The run's Slack identity memo (see aaif_events.slack.RUN_MEMO_ENV). The name
#: is spelled here rather than imported: the runner stays lib-free.
SLACK_MEMO_ENV = "AAIF_SLACK_RUN_MEMO"
SLACK_MEMO_FILE = "slack-memo.jsonl"
#: The run's Google read memo (see aaif_events.gws.READ_MEMO_ENV).
GWS_MEMO_ENV = "AAIF_GWS_RUN_MEMO"
GWS_MEMO_FILE = "gws-memo.jsonl"


def step_env(run_dir, run_writes):
    """The engine's environment: ours, plus the paths of this run's memos.

    Both sit in the run directory, so they inherit its 0700 and its
    .gitignore rule, and `drop_memos` deletes them when the run ends: they hold
    whole intake tabs and Slack profiles, more than any report keeps, and
    nothing reads them once the last step is done.

    `run_writes` is whether the RUN asked to write, not whether this step may:
    under --write, `luma` never writes, yet it reads the Chapters List after
    `chapters` and `resources` have written it, so a read it was handed from
    before those writes would be stale. The Google memo is therefore off for
    the whole of such a run. The Slack memo stays on — it keeps only accounts
    that were found, and nothing a run writes changes whose account an
    address is.
    """
    env = dict(os.environ)
    env.pop(GWS_MEMO_ENV, None)   # never inherited from whoever launched us
    env[SLACK_MEMO_ENV] = os.path.join(run_dir, SLACK_MEMO_FILE)
    if not run_writes:
        env[GWS_MEMO_ENV] = os.path.join(run_dir, GWS_MEMO_FILE)
    return env


def drop_memos(run_dir):
    """Delete this run's memo files. Idempotent; a missing file is fine."""
    for name in (SLACK_MEMO_FILE, GWS_MEMO_FILE):
        try:
            os.remove(os.path.join(run_dir, name))
        except FileNotFoundError:
            pass


def run_step(step, log_path, write_mode, approved, unattended=False):
    run_dir = os.path.dirname(log_path)
    env = step_env(run_dir, run_writes=write_mode)   # before gating narrows it
    cmd, write_mode = step_cmd(step, write_mode, approved, unattended, run_dir)
    t0 = time.monotonic()
    # 0o600: the log holds names and emails; no other local user gets to read
    # it just because the checkout happens to be world-readable.
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as log:
        log.write("$ %s\n\n" % " ".join(cmd))
        log.flush()
        # stderr merges in too: the engines print progress and gws retry notes
        # there, and a FAILED outcome is undiagnosable without it.
        try:
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  env=env,
                                  timeout=STEP_TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            log.write("\nTIMEOUT: no exit after %d s — the runner killed it; "
                      "any write it was mid-way through is unverified\n" % STEP_TIMEOUT_S)
            code = 124
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
    wrote = marked("Verified:")
    if step.name in EMITS_FINDINGS:
        # The findings file is the engine's own account of the run. Its absence
        # after an exit 2 means the engine never got as far as a report — a
        # usage error or a missing script also exit 2 — and that is a FAILED
        # step, not drift a human should review. Its `written` is the one
        # definition of "this step wrote" for every engine that emits one.
        doc = read_findings(os.path.join(run_dir, step.name + ".json"))
        if code == 2 and doc is None:
            log_note(log_path, "FAILED: exit 2 with no findings file — a usage "
                               "error or a missing script, not drift")
            code = 1
        elif doc is not None:
            wrote = bool(doc.get("written"))
    return (classify(code, wrote, write_mode, marked("PARTIAL:")),
            code, time.monotonic() - t0)


def read_findings(path):
    """The engine's findings dict, or None when the file is absent or unreadable.

    stdlib only: this file imports nothing from `lib`. A present-but-bad file
    reads as None here and is reported by the renderer, which does read `lib`.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def log_note(log_path, line):
    with open(log_path, "a", encoding="utf-8") as log:
        log.write("\n%s\n" % line)


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


def assert_gate_stage_coherent():
    """A step's gate fits its stage (STAGE_GATES), and every name is unique.

    Names key the logs, the findings files and every per-step set; two steps
    sharing one would overwrite each other's log mid-run and collapse in
    `by_name`. Both checks run at import so a wrong PHASES edit cannot ship.
    """
    for phase, steps in PHASES:
        for s in steps:
            if s.gate not in STAGE_GATES[s.stage]:
                raise AssertionError(
                    "step %r in phase %r is %s at stage %s; a %s step must be one of %s"
                    % (s.name, phase, s.gate, s.stage, s.stage,
                       "/".join(STAGE_GATES[s.stage])))
    names = [s.name for _p, ss in PHASES for s in ss]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise AssertionError("duplicate step name(s) in PHASES: %s" % ", ".join(dupes))


assert_stage_order()
assert_gate_stage_coherent()


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


def keep_awake():
    """Hold the Mac awake until this process exits.

    A full run takes ~20 minutes of step time, and on 2026-09-22 a laptop that
    slept mid-run came back with an expired Google session: `access` FAILED
    and the run took four hours of wall clock. `caffeinate -w` watches our pid
    and lets go by itself when we exit, however we exit. Elsewhere (Linux CI,
    no caffeinate) this does nothing — a server does not idle-sleep.
    """
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return
    subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

    if a.unattended and a.phases:
        # A scheduled job's scope is UNATTENDED_PHASES. Naming a phase outside
        # it would run the rate-limited Luma sweep or the audits unattended —
        # exactly what the set exists to keep out — so refuse rather than obey.
        allowed = {p for p, _s in selected([], True)} | {s.name for _p, s in selected([], True)}
        outside = [p for p in a.phases if p not in allowed]
        if outside:
            ap.error("%s is not in the unattended scope (%s); run it from a terminal"
                     % (", ".join(map(repr, outside)), "/".join(UNATTENDED_PHASES)))
    steps = selected(a.phases, a.unattended, a.stages)
    if not steps:
        # "everything in sync" over zero steps is a lie a scheduler would believe.
        ap.error("nothing to run: that selection matches no step")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    guard_report_dir(a.report_dir)
    run_dir = fresh_run_dir(a.report_dir, stamp)
    keep_awake()

    print("aaif-sync — %s mode%s — %d step(s)"
          % ("write" if a.write else "report",
             ", unattended" if a.unattended else "", len(steps)))
    print("full reports (contain names/emails — NOT for public logs): %s" % run_dir)
    print()

    # The memos go when the run does — including a run that crashes or is
    # interrupted, which never reaches the explicit call after the last step.
    atexit.register(drop_memos, run_dir)
    by_name, phase_shown, stage_shown = {}, None, None
    record = []   # what run.json carries: one entry per step, in run order
    for phase, step in steps:
        if phase != phase_shown:
            print("  [%s]" % phase)
            phase_shown, stage_shown = phase, None
        if step.stage != stage_shown:
            print("    · %s" % step.stage)
            stage_shown = step.stage
        entry = manifest_entry(phase, step, ran=True)
        record.append(entry)
        if step.gate == APPROVAL and a.write and not a.approved and not a.unattended:
            by_name[step.name] = SKIPPED
            entry.update(outcome=SKIPPED, exit=None, seconds=0, log=None, html=None,
                         findings=None)
            print("      %-10s %-15s (needs --i-have-approval)" % (step.name, SKIPPED))
            continue
        _cmd, ran_write = step_cmd(step, a.write, a.approved, a.unattended)
        outcome, code, secs = run_step(
            step, os.path.join(run_dir, step.name + ".log"), a.write, a.approved,
            a.unattended)
        by_name[step.name] = outcome
        entry.update(outcome=outcome, exit=code, seconds=round(secs, 1),
                     ran_write=ran_write)
        # A FAILED step keeps its findings file if it managed to write one —
        # the engines land their "grant failed" / "invite failed" rows on
        # purpose before exiting 1 — and loses the pointer only when the file
        # is not there to read.
        for key in ("html", "findings"):
            if entry[key] and not os.path.exists(os.path.join(run_dir, entry[key])):
                entry[key] = None
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
        if step.name in RENDERS_HTML and outcome != FAILED:
            note += "  + %s.html" % step.name
        print("      %-10s %-15s exit %d  %4.0fs  %s.log%s"
              % (step.name, outcome, code, secs, step.name, note))

    drop_memos(run_dir)   # nothing reads them after the last step
    print()
    notes = summary_notes(by_name, a.write)
    for line in notes:
        print(line)
    doc = write_manifest(run_dir, stamp, a, record, notes, exit_code(by_name))
    html, render_code = render(run_dir)
    # The manifest is the durable record; a page that was never drawn must be
    # visible there too, not only on a stdout line a scheduler discards.
    doc["render"] = {"ok": render_code == 0, "exit": render_code, "log": "render.log"}
    dump_manifest(run_dir, doc)
    if html:
        print("HTML report: %s" % html)
    return exit_code(by_name)


def fresh_run_dir(report_dir, stamp):
    """`<report_dir>/<stamp>`, 0700, never one that already exists.

    Two runs in one second would otherwise share a directory and overwrite
    each other's logs, findings and manifest; the second gets a `-2` suffix.
    The parent is created 0700 as well: `makedirs(mode=...)` applies its mode
    to the leaf only, and the parent's listing is the run stamps.
    """
    if not os.path.isdir(report_dir):
        os.makedirs(report_dir, mode=0o700)
        os.chmod(report_dir, 0o700)
    for n in range(1, 100):
        run_dir = os.path.join(report_dir, stamp if n == 1 else "%s-%d" % (stamp, n))
        try:
            os.mkdir(run_dir, 0o700)
        except FileExistsError:
            continue
        os.chmod(run_dir, 0o700)   # mkdir's mode is umask-masked; be explicit
        return run_dir
    sys.exit("ABORT: could not create a fresh run directory under %s" % report_dir)


def manifest_entry(phase, step, ran):
    """One run.json entry, built the same way for a step that ran and one that
    did not, so the two cannot drift apart a field at a time."""
    return {"phase": phase, "stage": step.stage, "step": step.name,
            "gate": step.gate, "why": step.why,
            "log": (step.name + ".log") if ran else None,
            "html": (step.name + ".html") if (ran and step.name in RENDERS_HTML) else None,
            "findings": (step.name + ".json") if (ran and step.name in EMITS_FINDINGS) else None,
            "outcome": None if ran else NOT_RUN, "exit": None, "seconds": None,
            "ran_write": False}


def write_manifest(run_dir, stamp, a, record, notes, code):
    """run.json: everything the renderer needs and nothing a person is named in.

    Step names, outcomes, exit codes, durations, the log each step wrote, the
    RESULT notes and the mode the run was in. The logs themselves stay separate
    files: the manifest says where they are, and the renderer reads them.
    """
    # Every step of the pipeline, every time, in pipeline order: a two-step
    # write run still draws the whole estate, with the steps it did not touch
    # marked NOT_RUN rather than absent — a page with two rows on it reads as
    # a two-step estate.
    ran = {e["step"]: e for e in record}
    steps = [ran.get(s.name) or manifest_entry(phase, s, ran=False)
             for phase, ss in PHASES for s in ss]
    doc = {"stamp": stamp, "mode": "write" if a.write else "report",
           "unattended": bool(a.unattended), "approved": bool(a.approved),
           "phases": list(a.phases) or None, "stages": a.stages,
           "steps": steps, "notes": notes, "exit": code}
    dump_manifest(run_dir, doc)
    return doc


def dump_manifest(run_dir, doc):
    fd = os.open(os.path.join(run_dir, MANIFEST),
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)


def render(run_dir):
    """Draw report.html from run.json and the logs; return (path or None, exit).

    A subprocess, like every engine: the renderer imports the design system
    and this file must not. Its own output goes to render.log — a failed
    render is reported on stdout and never changes the run's exit code, which
    is about the estate, not about the page.
    """
    log = os.path.join(run_dir, "render.log")
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        try:
            code = subprocess.run([sys.executable, RENDERER, run_dir],
                                  stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=600).returncode
        except subprocess.TimeoutExpired:
            fh.write("TIMEOUT: the renderer did not finish in 600 s\n")
            code = 124
    if code:
        print("HTML report: render FAILED (exit %d) — see %s; re-run "
              "render_report.py %s by hand" % (code, log, run_dir))
        return None, code
    return os.path.join(run_dir, "report.html"), 0


if __name__ == "__main__":
    sys.exit(main())
