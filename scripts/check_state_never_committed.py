#!/usr/bin/env python3
"""Fail if any path the toolkit caches or reports into could be committed.

Caching locally is deliberate — a Slack directory pull takes twenty minutes,
and `aaif_events.jsoncache` exists to avoid paying it twice in a day. What must
never happen is that cache reaching git: this repo is public, and the files hold
the whole member directory, every synced person's name and address, and the
per-person diffs an engine printed.

Two layers already guard this at runtime — the audits call
`report_style.assert_git_ignored` on their `--cache` and output paths, and
`sync.py` and `sync_crm.backup_root` refuse to start if their directory is
committable. Both read `.gitignore` at run time, so both are only as good as
that file, and a well-meant tidy-up of it would disarm every one of them at
once while every other check stayed green.

This asserts the file itself, for the paths the toolkit actually writes:

    python3 scripts/check_state_never_committed.py

A path is tested as a FILE INSIDE the directory, not as the directory itself:
`**/backups/*` ignores the contents while leaving `backups/` itself reportable,
which is what lets `backups/README.md` stay tracked.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (a file the toolkit really writes, what writes it).
WRITES = [
    (".slack-audit-cache/users.json", "aaif-audit-slack — the member directory"),
    (".slack-audit-cache/channels.json", "aaif-audit-slack — every channel"),
    (".slack-audit-cache/activity.json", "aaif-audit-slack — per-channel activity"),
    (".slack-audit-cache/audit.json", "aaif-audit-slack — the joined audit"),
    (".pulse-cache/local-champs.json", "aaif-community-pulse — real message text"),
    (".pulse-cache/pulse-general.txt", "aaif-community-pulse — a drafted post"),
    ("sync-reports/2026-01-01T000000Z/crm.log", "aaif-sync — a per-step report"),
    ("nightly-reports/2026-01-01T000000Z/crm.log", "aaif-sync — the nightly wrapper"),
    ("backups/crm-before-2026-01-01T000000Z/Boston CRM.xlsx",
     "sync_crm — pre-edit workbook bytes"),
    ("backups/crm-role-tabs-2026-01-01T000000Z/Boston CRM.xlsx",
     "migrate_role_tabs — pre-edit workbook bytes"),
    ("evals/results/2026-01-01/report.html", "claude plugin eval — transcripts"),
    ("slack-full-audit.html", "aaif-audit-slack — the deliverable"),
    ("slack-organizers-audit.html", "aaif-audit-slack — a standalone report"),
]

#: Deliberately tracked, and must stay that way — a guard that demanded these be
#: ignored would be "fixed" by deleting the one file explaining the directory.
TRACKED = ["backups/README.md"]


def ignored(rel):
    return subprocess.run(["git", "-C", REPO, "check-ignore", "-q", rel]).returncode == 0


def main():
    if subprocess.run(["git", "-C", REPO, "rev-parse", "--git-dir"],
                      capture_output=True).returncode:
        print("check_state_never_committed: not a git checkout — nothing to "
              "commit into, skipping.")
        return 0

    leaks = [(p, why) for p, why in WRITES if not ignored(p)]
    wrongly_ignored = [p for p in TRACKED if ignored(p)]

    if not leaks and not wrongly_ignored:
        print("check_state_never_committed: %d cache/report path(s) are all "
              "gitignored." % len(WRITES))
        return 0

    if leaks:
        print("ERROR: these paths are written by the toolkit and are NOT "
              "gitignored.\nThis repo is public and the files hold real people's "
              "names and addresses.\nRestore the .gitignore rule — do not delete "
              "the entry here to make this pass.\n")
        for p, why in leaks:
            print("  %-56s %s" % (p, why))
    if wrongly_ignored:
        print("\nERROR: these are meant to stay TRACKED but are now ignored:\n")
        for p in wrongly_ignored:
            print("  %s" % p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
