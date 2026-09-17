# Contributing

Thanks for helping improve the AAIF Community Events Toolkit. The repo root is both the
marketplace and a single plugin (`aaif-events`) — `marketplace.json` and
`plugin.json` sit side by side in `.claude-plugin/`, and the skills live under
`skills/` at the repo root.

## Adding or editing a skill

A skill is a folder with a `SKILL.md` (plus an optional `scripts/` dir):

```
skills/<skill-name>/
├── SKILL.md
└── scripts/            # optional helper scripts
```

`SKILL.md` starts with YAML frontmatter:

```yaml
---
name: aaif-something
description: One line — what it does AND when to use it ("Use when asked to …"),
  so Claude auto-activates it at the right moment.
argument-hint: "<optional> [args]"
---

# Title
Clear, step-by-step instructions…
```

Guidelines:
- **Reference bundled scripts with `${CLAUDE_SKILL_DIR}/scripts/...`**, never a
  hardcoded `.claude/skills/...` path — the variable resolves wherever the skill
  is installed.
- Keep the `description` action-oriented; it's what triggers auto-activation.
- **Secrets are never command-line parameters.** argv is visible in `ps` and
  gets echoed in logs/console, so no `--token`/`--key` style flags — scripts read
  tokens and API keys from environment variables (or `.env` / keychain) only.
  `scripts/check_no_secret_args.py` enforces this in CI.
- **Quote `argument-hint` values fully.** A value like `"<City>" [--slug <x>]`
  (a quoted scalar followed by bare text) is *invalid YAML* — the whole
  frontmatter then fails to parse and the skill loads with empty metadata
  (description dropped, so it never auto-activates). Single-quote the entire
  value instead: `argument-hint: '<City> [--slug <x>]'`.
- Read and write Google Sheets/Drive **by header name / resource lookup**, not by
  fixed column letters, so skills survive layout changes.
- These skills ship with AAIF's own Google resource IDs. If you're adapting them
  for another chapter, change the constants at the top of each `scripts/*.py` and
  the IDs referenced in the `SKILL.md`.

## Checks

This repo ships a [pre-commit](https://pre-commit.com/) config and a `validate`
GitHub Actions workflow. Set up the hooks once:

```bash
pipx install pre-commit        # or: pip install --user pre-commit
pre-commit install             # run the hooks on every commit
pre-commit run --all-files     # run them now against the whole repo
```

The hooks cover JSON/YAML/whitespace hygiene, Ruff (bug-focused lint of the
helper scripts), codespell, gitleaks secret scanning, a SKILL.md frontmatter
check, and seven repo-specific guards — `check_no_secret_args.py`,
`check_workflows.py` and `extract_design_tokens.py --check` alongside the four
below. These four are the ones whose mistake is invisible in the output:

- **`check_tooling_banner.py`** — three banners are deliberately copied into
  every `SKILL.md` that needs them (skills ship downstream without the repo
  docs): the tooling rule, the public-copy rule, and the attendee legal footer.
  Duplication is the decision; *drift* between the copies is the bug. **Edit
  every copy together**, never just one.
- **`check_no_local_redaction.py`** — `--redact` only works when the flag and
  the helpers that read it come from `lib/aaif_events/redact.py`. A helper
  reading a different module's flag is a helper `--redact` does not govern, and
  that is how an address once reached a public CI log. Import them; never
  redefine them.
- **`check_no_real_pii.py`** — no real address or Slack id in a tracked file.
  **Adding to its allowlist to make a check pass is the bug, not the fix**,
  unless the value really is invented.
- **`check_portable_skills.py`** — a skill importing `lib/aaif_events` cannot be
  zipped standalone for claude.ai. Taking that on is allowed and sometimes
  right; doing it without saying so is not, so the README's list of coupled
  skills has to move in the same commit as the import.

Then run the tests. Ruff is pyflakes-only and does not resolve imports, so
pytest is what actually catches a broken import or a stale module name:

```bash
PYTHONPATH=lib python -m pytest lib/aaif_events/tests -q    # shared library
python skills/aaif-sync-chapters/scripts/test_sync_crm.py   # per-skill tests:
                                                            # plain scripts, exit 1 on failure
python scripts/test_check_portable_skills.py                # the repo guards
```

Skill scripts are not a package, so each skill's `scripts/test_*.py` runs as a
plain Python script rather than through pytest. CI's loop is:

```bash
for t in skills/*/scripts/test_*.py skills/*/migrations/test_*.py scripts/test_*.py; do
  python "$t"
done
```

`migrations/` holds one-shots that have already run against the live estate.
They stay tested because two live engines name one of them as the fix when a
sheet is missing its columns — see
`skills/aaif-sync-chapters/references/completed-migrations.md`.

### Evals — the check for whether a skill actually fires

Everything above is deterministic and none of it asks whether the right skill
triggers for a real request. `evals/` holds a small suite that does:

```bash
claude plugin eval . --no-publish --trust-plugin
```

**Run it before changing any `description:` field.** That field is what decides
whether a skill activates, and an edit that reads like an improvement can stop
it triggering, or start it triggering over its neighbour, with every other
check still green.

Each case runs with and without the plugin and reports the delta, so the suite
measures what the skill contributes rather than what the base model already
does. It needs model access and therefore does **not** run in `validate.yml`,
which holds no credential because it runs on fork PRs. See `evals/README.md`.

Then validate the manifests. The repo root is both the marketplace and the
single plugin (marketplace `source: "./"`), so one call validates the
`marketplace.json` **and** the `plugin.json` schema:

```bash
claude plugin validate .
```

This does *not* parse SKILL.md frontmatter — that's covered by the
`check-skill-frontmatter` pre-commit hook above. CI runs both on each PR.
Finally, install your local copy to try it live:

```bash
/plugin marketplace add ./           # from the repo root
/plugin install aaif-events@aaif
```

## Pull requests

Fork, branch, commit, and open a PR against `main`. Keep changes focused and
explain what a reviewer should check.
