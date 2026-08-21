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
helper scripts), codespell, gitleaks secret scanning, a SKILL.md
frontmatter check, and a tooling-banner drift check. On the last one: the
tooling-rule banner is deliberately copied into every ops `SKILL.md` (skills
ship downstream without the repo docs), and `scripts/check_tooling_banner.py`
fails when the copies drift — **edit all of them together**, never just one.

Then run the tests. Ruff is pyflakes-only and does not resolve imports, so
pytest is what actually catches a broken import or a stale module name:

```bash
PYTHONPATH=lib python -m pytest lib/aaif_events/tests -q    # shared library
python skills/aaif-sync-chapters/scripts/test_sync_crm.py   # per-skill tests:
                                                            # plain scripts, exit 1 on failure
```

Skill scripts are not a package, so each skill's `scripts/test_*.py` runs as a
plain Python script rather than through pytest.

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
