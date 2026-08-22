#!/usr/bin/env python3
"""Lint .github/workflows/*.yml for the patterns that leak secrets or PII from a public repo.

The repo is public and its skills run against real people's data, so a workflow
is a trust boundary, not plumbing. Each rule names the failure it prevents:

* forbidden-trigger — no `pull_request_target`, `issue_comment`, `workflow_run`
  and friends: they run with the base repo's secrets on content a stranger wrote.
* secret-trigger   — a workflow that can reach secrets (`${{ secrets… }}` in any
  form, `toJSON(secrets)`, `secrets: inherit`, `workflow_call` secrets) may
  trigger only on `workflow_dispatch`, `schedule`, or `push` restricted to main.
* job-environment  — every job that touches secrets runs in a named
  `environment:` (reviewers + branch rule) — checked per job, not per file.
* no-exfil         — secret-holding jobs never upload artifacts, write the step
  summary, run `actions/github-script`, dump the environment, or print a PII
  output path; the log is public.
* inherit          — `secrets: inherit` is refused; name what a callee gets.
* secret-in-run    — `${{ secrets… }}` only in `env:`, never in `run:` (argv → log).
* approval         — `--i-have-approval` never comes from CI.
* pinned-uses      — third-party actions by 40-hex commit SHA, docker images by
  digest; `checkout` sets `persist-credentials: false`.

The file is parsed as YAML (not grepped), so quoting, indentation, flow style and
comments cannot hide a trigger. Anything the parser cannot read is an error,
never a pass. Line numbers in messages are best-effort text lookups.

Usage:  python3 scripts/check_workflows.py [FILE...]
        (no args = every workflow, parked `*.yml.disabled` files included)
"""
import glob, json, re, sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check_workflows.py needs PyYAML (pip install pyyaml); refusing to pass without it")

FORBIDDEN_TRIGGERS = {"pull_request_target", "issue_comment", "issues", "discussion",
                      "discussion_comment", "workflow_run", "fork", "watch", "pull_request_review",
                      "pull_request_review_comment"}
SAFE_SECRET_TRIGGERS = {"workflow_dispatch", "schedule", "push", "workflow_call"}
MAIN_REFS = {"main", "refs/heads/main"}
# `secrets` anywhere inside an expression: secrets.X, secrets['X'], secrets[format()],
# toJSON(secrets). Context names are case-insensitive in Actions.
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)


class _SecretExpr:
    """`${{ … }}` expressions that can reach a secret other than GITHUB_TOKEN.

    Matches the body, not `[^}]*`, so `secrets[format('T{0}', x)]` is seen."""

    @staticmethod
    def search(text):
        for m in EXPR.finditer(text):
            body = m.group(1)
            if re.search(r"\bsecrets\b", body, re.I) and not re.search(
                    r"\bsecrets\s*(\.\s*GITHUB_TOKEN\b|\[\s*['\"]GITHUB_TOKEN['\"]\s*\])", body, re.I):
                return m
        return None


SECRET_EXPR = _SecretExpr()
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PII_PATHS = (".slack-audit-cache", "nightly-reports", "slack-members-audit", "slack-organizers-audit",
             "slack-activity-audit", "backups")
PRINTERS = re.compile(r"(^|[\s;|&(])(cat|tail|head|less|more|grep|ls|find|base64|xxd)\b")
ENV_DUMP = re.compile(r"(^|[\s;|&(])(env|printenv|set|export -p|declare -p)\s*($|[;|&)])|/proc/self/environ", re.M)


# --- model -------------------------------------------------------------------

class Unparsable(Exception):
    pass


def load(text):
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise Unparsable("top level is not a mapping")
    return doc


def triggers(doc):
    """`on:` as {name: config}. YAML 1.1 reads a bare `on` as boolean True."""
    on = doc.get("on", doc.get(True))
    if on is None:
        raise Unparsable("no `on:` block")
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        if not all(isinstance(t, str) for t in on):
            raise Unparsable("`on:` list holds a non-string")
        return {t: None for t in on}
    if isinstance(on, dict):
        return {str(k): v for k, v in on.items()}
    raise Unparsable("`on:` has an unreadable shape")


def jobs(doc):
    j = doc.get("jobs")
    if not isinstance(j, dict) or not j:
        raise Unparsable("no `jobs:` mapping")
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in j.items()}


def blob(obj):
    """Everything under a node as one string, for expression searches."""
    return json.dumps(obj, default=str)


def steps(job):
    s = job.get("steps") or []
    return [st for st in s if isinstance(st, dict)]


def runs(job):
    return [str(st["run"]) for st in steps(job) if "run" in st and st["run"] is not None]


def job_touches_secrets(doc, name, job):
    if SECRET_EXPR.search(blob(job)):
        return True
    if SECRET_EXPR.search(blob(doc.get("env") or {})):
        return True
    if str(job.get("secrets", "")).strip().lower() == "inherit" or isinstance(job.get("secrets"), dict):
        return True
    return False


def workflow_touches_secrets(doc):
    if any(job_touches_secrets(doc, n, j) for n, j in jobs(doc).items()):
        return True
    call = triggers(doc).get("workflow_call")
    return isinstance(call, dict) and bool(call.get("secrets"))


def line_of(lines, needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i + 1
    return 0


# --- rules: each is fn(doc, lines) -> list[str] ------------------------------

def rule_forbidden_trigger(doc, lines):
    return ["forbidden trigger `%s` (runs privileged on untrusted content)" % t
            for t in sorted(set(triggers(doc)) & FORBIDDEN_TRIGGERS)]


def rule_secret_trigger(doc, lines):
    if not workflow_touches_secrets(doc):
        return []
    errs, trig = [], triggers(doc)
    bad = set(trig) - SAFE_SECRET_TRIGGERS
    if bad:
        errs.append("touches secrets but triggers on %s — a fork PR could reach them; allowed: %s"
                    % (sorted(bad), sorted(SAFE_SECRET_TRIGGERS)))
    push = trig.get("push")
    if "push" in trig:
        branches = (push or {}).get("branches") if isinstance(push, dict) else None
        if not branches or set(map(str, branches)) - MAIN_REFS:
            errs.append("touches secrets on `push` without `branches: [main]` — any branch push would run it")
    return errs


def rule_job_environment(doc, lines):
    return ["job `%s` touches secrets without an `environment:` (no reviewers, no branch restriction)" % n
            for n, j in jobs(doc).items()
            if job_touches_secrets(doc, n, j) and not j.get("environment")]


def rule_no_exfil(doc, lines):
    errs = []
    for n, j in jobs(doc).items():
        if not job_touches_secrets(doc, n, j):
            continue
        text = blob(j)
        for st in steps(j):
            u = str(st.get("uses", ""))
            if "upload-artifact" in u or "github-script" in u:
                errs.append("job `%s` holds secrets and uses `%s` — artifacts and scripted API access "
                            "leave the runner" % (n, u))
        if "GITHUB_STEP_SUMMARY" in text:
            errs.append("job `%s` holds secrets and writes the step summary (public)" % n)
        for r in runs(j):
            if ENV_DUMP.search(r):
                errs.append("job `%s` dumps the environment in a run step (secrets are env vars)" % n)
            for ln in r.split("\n"):
                if PRINTERS.search(ln) and any(p in ln for p in PII_PATHS):
                    errs.append("%d: prints a PII output path into the public log: %s"
                                % (line_of(lines, ln.strip()), ln.strip()))
    return errs


def rule_inherit(doc, lines):
    return ["job `%s` uses `secrets: inherit` — pass named secrets so the callee's scope is visible" % n
            for n, j in jobs(doc).items() if str(j.get("secrets", "")).strip().lower() == "inherit"]


def rule_secret_in_run(doc, lines):
    errs = []
    for n, j in jobs(doc).items():
        for r in runs(j):
            for ln in r.split("\n"):
                if SECRET_EXPR.search(ln):
                    errs.append("%d: secret interpolated into a run step (argv → log); pass it via env:"
                                % line_of(lines, ln.strip()))
    return errs


def rule_approval(doc, lines):
    return ["%d: passes --i-have-approval from CI; people-affecting ops stay a local, human action"
            % line_of(lines, "--i-have-approval")
            for n, j in jobs(doc).items() for r in runs(j) if "--i-have-approval" in r]


def rule_pinned_uses(doc, lines):
    errs = []
    for n, j in jobs(doc).items():
        targets = [j] + steps(j)
        for t in targets:
            u = t.get("uses")
            if not isinstance(u, str) or u.startswith("./"):
                continue
            ln = line_of(lines, u)
            if u.startswith("docker://"):
                if "@sha256:" not in u:
                    errs.append("%d: `uses: %s` docker image is not pinned by digest" % (ln, u))
                continue
            ref = u.rsplit("@", 1)[1] if "@" in u else ""
            if not SHA40.match(ref):
                errs.append("%d: `uses: %s` is not pinned to a 40-hex commit SHA" % (ln, u))
            if u.startswith("actions/checkout@"):
                with_ = t.get("with") or {}
                if with_.get("persist-credentials") is not False:
                    errs.append("%d: checkout without `persist-credentials: false`" % ln)
    return errs


RULES = [rule_forbidden_trigger, rule_secret_trigger, rule_job_environment, rule_no_exfil,
         rule_inherit, rule_secret_in_run, rule_approval, rule_pinned_uses]


def check_text(text):
    lines = text.split("\n")
    try:
        doc = load(text)
        triggers(doc)
        jobs(doc)
    except (Unparsable, yaml.YAMLError) as e:
        return ["cannot parse workflow (%s) — refusing to pass what cannot be checked" % e]
    errs = []
    for rule in RULES:
        errs.extend(rule(doc, lines))
    return errs


def check(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return check_text(fh.read())
    except OSError as e:
        return ["cannot read: %s" % e]


def main(argv):
    paths = argv[1:] or sorted(p for pat in ("*.yml", "*.yaml", "*.yml.disabled", "*.yaml.disabled")
                               for p in glob.glob(".github/workflows/" + pat))
    if not paths:
        print("no workflows found under .github/workflows (run from the repo root)")
        return 1
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
