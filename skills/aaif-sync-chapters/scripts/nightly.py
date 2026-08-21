#!/usr/bin/env python3
"""Run the five sync engines in dependency order and print a PII-free summary.

Built for an unattended (nightly CI) run of the pipeline this skill documents:
feed -> about -> CRM -> access -> resources. Each engine runs as a subprocess;
its FULL report — which names real people and their email addresses — goes only
to a log file under a `.gitignore`d directory. **This script's own stdout never
contains a person**: on a public repo, a CI job log is a publication, so the
summary is engine names, outcomes, durations and log paths, nothing else. Keep
it that way — any new print here must be composed only of fixed strings and
values this script computed itself, never engine output.

Exit-code convention, in two scopes that deliberately differ:

  Engines: 0 = in sync, OR --write applied and verified (a `Verified:` line on
           stdout is what separates those two); 2 = a report proposed changes,
           a --write held back or left pending work (sync_chapters holds new
           rows whose Luma page is not live — it may still have written the
           rest, which the `Verified:` line in its log records), or coverage
           was involuntarily partial (`PARTIAL:` line); anything else = failure.
  Runner:  0 = every engine in sync; 2 = drift found, writes applied, or
           partial coverage anywhere; 1 = any engine failed.

The Slack write steps (provision_channels.py, invite_organizers.py,
prune_organizers.py) are deliberately NOT here: they carry their own
--i-have-approval gate because they notify or affect real people, and a nightly
job must never hold that approval. sync_resources' Slack half degrades to
folder-only on a dead token, which is the right nightly behaviour. For the same
reason this runner passes ONLY --write and must never pass sync_access's
--pins: publishing a banner to the internet is a standing human decision, and
the engine leaves it pending (and visible in its report) until someone runs
`sync_access.py --write --pins` by hand.

Usage:
    python3 nightly.py                 # report-only run of all five engines
    python3 nightly.py --write         # apply each engine's proposal unattended
    python3 nightly.py crm resources   # a subset, in pipeline order
"""

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

#: Pipeline order is load-bearing (see SKILL.md): a net-new city needs its feed
#: row before its doc/CRM, the CRM decides access, and resources record what
#: exists once it exists.
ENGINES = (
    ("chapters", "sync_chapters.py"),
    ("about", "sync_about.py"),
    ("crm", "sync_crm.py"),
    ("access", "sync_access.py"),
    ("resources", "sync_resources.py"),
)

IN_SYNC, DRIFT, WROTE, FAILED, PARTIAL = (
    "in sync", "DRIFT", "wrote+verified", "FAILED", "PARTIAL")


def classify(code, wrote_marker, write_mode, partial_marker=False):
    """Map an engine's exit code (+ two log markers) onto one of five outcomes.

    'Verified:' only ever follows an applied write, so it tells "--write had
    nothing to do" apart from "--write wrote". Exit 2 in write mode means work
    is still pending (e.g. sync_chapters held back a row with no live Luma
    page) and classifies as DRIFT even when part of the run wrote — the drift
    is what needs eyes, and the log's 'Verified:' line records the write. 'PARTIAL:' means the engine
    involuntarily skipped part of its coverage (today: sync_resources' Slack
    half on a dead token) — it wins over in-sync/drift because a half-checked
    night must never read as a healthy one, but never over FAILED, which is
    strictly worse news.
    """
    if code not in (0, 2):
        return FAILED
    if partial_marker:
        return PARTIAL
    if code == 0:
        return WROTE if (write_mode and wrote_marker) else IN_SYNC
    return DRIFT


def run_engine(script, log_path, write_mode):
    cmd = [sys.executable, os.path.join(HERE, script)] + (
        ["--write"] if write_mode else [])
    t0 = time.monotonic()
    with open(log_path, "w") as log:
        log.write("$ %s\n\n" % " ".join(cmd))
        log.flush()
        # stderr merges into the log too: the engines print progress and gws
        # retry notes there, and a FAILED outcome is undiagnosable without it.
        code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    # Explicit encoding: engine output is full of accented city/channel names
    # (españa, Montréal), and a LANG=C CI container would otherwise open this
    # ASCII and crash the runner AFTER the engine already applied its writes.
    with open(log_path, encoding="utf-8", errors="replace") as log:
        lines = log.readlines()
    wrote_marker = any(l.startswith("Verified:") for l in lines)
    partial_marker = any(l.startswith("PARTIAL:") for l in lines)
    return (classify(code, wrote_marker, write_mode, partial_marker),
            code, time.monotonic() - t0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # No argparse `choices` here: with nargs="*" some Python versions validate
    # the empty default against it and reject a bare `nightly.py`.
    ap.add_argument("engines", nargs="*", metavar="engine",
                    help="subset of %s to run (default: all five, in pipeline "
                         "order)" % "/".join(n for n, _ in ENGINES))
    ap.add_argument("--write", action="store_true",
                    help="pass --write through to every engine (default: report only)")
    ap.add_argument("--report-dir", default=os.path.join(REPO, "nightly-reports"),
                    help="where the full (PII-carrying) engine logs land; must be "
                         "gitignored and must never be uploaded anywhere public")
    a = ap.parse_args()

    known = {n for n, _ in ENGINES}
    bogus = [e for e in a.engines if e not in known]
    if bogus:
        ap.error("unknown engine(s) %s — pick from %s"
                 % (", ".join(map(repr, bogus)), "/".join(sorted(known))))
    picked = [(n, s) for n, s in ENGINES if not a.engines or n in a.engines]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    run_dir = os.path.join(a.report_dir, stamp)

    # The logs carry names and emails. If they land inside the repo, git must
    # already be ignoring them — the docstring's "must be gitignored" promise
    # is enforced here, not assumed (same guard the Slack audit uses for its
    # cache). A report dir outside the repo is the operator's own business.
    probe = os.path.abspath(a.report_dir)
    if probe.startswith(REPO + os.sep):
        r = subprocess.run(["git", "-C", REPO, "check-ignore", "-q", probe])
        if r.returncode != 0:
            sys.exit("ABORT: %s is inside the repo but NOT gitignored — these "
                     "logs hold names and emails and this repo is public. Add "
                     "it to .gitignore (nightly-reports/ already is) or point "
                     "--report-dir elsewhere." % probe)
    os.makedirs(run_dir, exist_ok=True)

    print("aaif-sync-chapters nightly — %s mode — %d engine(s)"
          % ("write" if a.write else "report", len(picked)))
    print("full reports (contain names/emails — NOT for public logs): %s\n" % run_dir)

    results = []
    for name, script in picked:
        outcome, code, secs = run_engine(
            script, os.path.join(run_dir, name + ".log"), a.write)
        results.append(outcome)
        print("  %-10s %-15s exit %d  %4.0fs  %s.log"
              % (name, outcome, code, secs, name))

    print()
    if FAILED in results:
        print("RESULT: failure — read the log(s) above. Later engines still ran; "
              "the pipeline's report modes are read-only and independent.")
        return 1
    notes = []
    # WROTE and DRIFT are separate notes: a write run can exit 2 without having
    # written anything (every proposal held back), and "changes were applied"
    # for that night would mask a chapter stuck behind a missing Luma page.
    if WROTE in results:
        notes.append("changes were applied and verified")
    if DRIFT in results:
        notes.append("drift remains — an engine held back or re-proposed "
                     "changes; read its log" if a.write
                     else "drift — run the flagged engine(s) with --write after review")
    if PARTIAL in results:
        notes.append("PARTIAL coverage — Slack was unavailable, the channel "
                     "columns went unchecked; fix Slack auth")
    if notes:
        print("RESULT: " + "; ".join(notes) + ".")
        return 2
    print("RESULT: everything in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
