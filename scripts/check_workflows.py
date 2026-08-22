#!/usr/bin/env python3
"""Lint .github/workflows/*.yml for the patterns that leak secrets or PII from a public repo.

The repo is public and its skills run against real people's data, so a workflow
is a trust boundary, not plumbing. The rules, each with the failure it prevents:

* No `pull_request_target`, `issue_comment`, `discussion*`, or `workflow_run` triggers
  — they run with the base repo's secrets on content a stranger controls.
* A workflow that references `secrets.` (other than GITHUB_TOKEN) may trigger only
  on `workflow_dispatch` / `schedule` / `push` to main, and must run every
  secret-holding job in a named `environment:` — so a fork PR can never reach them
  and a run needs the environment's reviewers.
* Secret-holding workflows must not upload artifacts, write the job summary, or
  `cat`/`tail` the PII output paths — logs are public on a public repo.
* `${{ secrets.X }}` may appear only in an `env:` block, never inline in `run:`
  — that is argv, and it lands in the log verbatim.
* Never pass `--i-have-approval` from CI: invites, kicks, archives and share mail
  stay a human, local action.
* Third-party actions are pinned to a 40-hex commit SHA, and `checkout` sets
  `persist-credentials: false`.

Usage:  python3 scripts/check_workflows.py [FILE...]   (no args = every workflow)
"""
import glob, re, sys

SHA_RE = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\b")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@(\S+)")
SECRET_RE = re.compile(r"\$\{\{\s*secrets\.(?!GITHUB_TOKEN\b)\w+")
FORBIDDEN_TRIGGERS = ("pull_request_target", "issue_comment", "discussion", "discussion_comment",
                      "workflow_run", "issues", "fork", "watch")
SAFE_SECRET_TRIGGERS = {"workflow_dispatch", "schedule", "push"}
PII_PATHS = (".slack-audit-cache", "nightly-reports", "slack-members-audit", "slack-organizers-audit",
             "slack-activity-audit", "backups/")


def triggers(text):
    """Top-level `on:` keys. Handles `on: [a, b]`, `on: a`, and the block form."""
    m = re.search(r"^on:[ \t]*(.*)$", text, re.M)
    if not m:
        return set()
    inline = m.group(1).strip()
    if inline:
        return {t.strip() for t in inline.strip("[]").split(",") if t.strip()}
    found, in_block = set(), False
    for line in text.split("\n"):
        if re.match(r"^on:\s*$", line):
            in_block = True
            continue
        if in_block:
            if line and not line[0].isspace():
                break
            k = re.match(r"^  (\w+):", line)
            if k:
                found.add(k.group(1))
    return found


def check(path):
    raw = open(path, encoding="utf-8").read()
    # Comments explain the rules and naturally mention the forbidden strings.
    lines = [re.sub(r"(^|\s)#.*$", "", l) for l in raw.split("\n")]
    text = "\n".join(lines)
    errs = []
    trig = triggers(text)
    for t in FORBIDDEN_TRIGGERS:
        if t in trig:
            errs.append("forbidden trigger `%s` (runs privileged on untrusted content)" % t)

    uses_secrets = bool(SECRET_RE.search(text))
    if uses_secrets:
        bad = trig - SAFE_SECRET_TRIGGERS
        if bad:
            errs.append("references secrets but triggers on %s — fork PRs could reach them; "
                        "allowed: workflow_dispatch, schedule, push" % sorted(bad))
        if "environment:" not in text:
            errs.append("references secrets without a gated `environment:` (no reviewers, no branch restriction)")
        if "upload-artifact" in text or "GITHUB_STEP_SUMMARY" in text:
            errs.append("secret-holding workflow uploads artifacts or writes the job summary — "
                        "skill output is PII and the log is public")
        if "--i-have-approval" in text:
            errs.append("passes --i-have-approval from CI; people-affecting ops stay local")
        for n, line in enumerate(lines, 1):
            if "secrets." in line and re.match(r"^\s*-?\s*run:", line):
                errs.append("%d: secret interpolated into `run:` (argv → log); use env:" % n)
            if re.search(r"\b(cat|tail|head|less|grep|ls)\b", line) and \
               any(p in line for p in PII_PATHS):
                errs.append("%d: prints a PII output path into the public log" % n)
        # Inline `run: ${{ secrets.X }}` on continuation lines of a run block.
        in_run = False
        for n, line in enumerate(lines, 1):
            if re.match(r"^\s*-?\s*run:\s*\|", line):
                in_run = True
                continue
            if in_run and line and not line.startswith("        "):
                in_run = False
            if in_run and SECRET_RE.search(line):
                errs.append("%d: secret interpolated inside a run block; pass it via env:" % n)

    for n, line in enumerate(lines, 1):
        m = USES_RE.match(line)
        if m and not m.group(1).startswith("./") and not SHA_RE.match(line):
            errs.append("%d: `uses: %s@%s` is not pinned to a commit SHA" % (n, m.group(1), m.group(2)))
        if "actions/checkout@" in line:
            window = "\n".join(lines[n:n + 3])
            if "persist-credentials: false" not in window:
                errs.append("%d: checkout without `persist-credentials: false`" % n)
    return errs


def main(argv):
    paths = argv[1:] or sorted(glob.glob(".github/workflows/*.yml") + glob.glob(".github/workflows/*.yaml"))
    failed = 0
    for p in paths:
        for e in check(p):
            failed += 1
            print("%s: %s" % (p, e))
    if failed:
        return 1
    print("OK: %d workflow(s) pass the public-repo secret/PII rules" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
