# Unattended runs — `nightly.py`

> Load this before scheduling the pipeline in CI, when reading a `nightly-reports/` log,
> or when a nightly run exits non-zero.

`nightly.py` is a thin wrapper over `sync.py --unattended`: it adds that flag and
its own report directory (`nightly-reports/`) and owns no pipeline of its own.
What "unattended" means is decided in `sync.py`:

- **Only `preflight`, `chapters` and `organizers` run** (`sync.UNATTENDED_PHASES`).
  `events` is out because luma.com rate-limits the sweep and it would report
  PARTIAL every night; `speakers` and `workspace` are out because they only
  publish audits.
- **The approval steps run read-only, in both modes.** `provision`, `invite`
  and `directory` add or notify real people, and a scheduled job is nobody's
  approval: `--i-have-approval` is refused with `--unattended`, and under
  `--write` those steps still only report. A scheduled report and a scheduled
  write therefore agree on what is pending; a clean night exits `0`, and a
  pending room exits `2` with the note that sends a human to a terminal.
- **`access` never receives `--write` here either** — see the gate table in
  `SKILL.md`. Pending grants exit `2` with the **NEEDS A HUMAN** note.

Two conventions make a scheduled run workable:

- **Exit codes are the drift signal.** Every engine exits `0` in sync, `2` when
  its report proposes changes, anything else on failure. The runner aggregates:
  `0` all clean, `2` drift / writes applied / PARTIAL / a human is needed, `1`
  any failure.
- **The runner's stdout never names a person.** This repo is public and a CI log
  is a publication, so the summary is step names, outcomes, durations and log
  paths only. The full reports — names, emails, per-person diffs — go to
  `nightly-reports/<UTC stamp>/<step>.log`, `0600` files in a `0700` directory
  that `.gitignore` covers and that must never be uploaded as an artifact. Any
  print added to the runner must be composed of fixed strings and its own
  computed values, never engine output.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py                # report the unattended phases
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py --write        # apply what is safe unattended
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py crm resources  # a subset, still in pipeline order
```

`--write` keeps each engine's own refusals: `sync_chapters` still holds back a
new row whose Luma page is not live, so an unattended write is the same set of
decisions the interactive flow would make, minus the pause for approval.

**A dead Slack token is never green.** The Slack gathers **FAIL** without one
and `sync_resources` degrades to folder-only with a **PARTIAL** outcome (see the
Preflight list in `SKILL.md`). Either way the night reads red, or a token that
dies in CI stays dead forever.

**Cost.** `coverage` is the one Slack pull that runs unattended. Its first
`users.json` pull takes ~20 minutes on a 30k-member workspace; after that the
day-old `.slack-audit-cache/` covers it. A runner that wipes the cache between
jobs pays the full pull every night.

The engines take `--redact` (on by default when `CI` is set). The runner passes
`--no-redact` on purpose: its logs are private files, never CI output, and the
NEEDS-A-HUMAN note needs the real addresses in `access.log`. Run an engine
directly under CI and its stdout comes out redacted instead. Engine stdout
contains names and emails: **never quote it in a commit message, PR body, or
public post.**

Wiring this into GitHub Actions (auth, secrets, where the digest goes) is a
separate change; `run-skill.yml.disabled` has the shape, and the runner is
deliberately CI-agnostic.
