# Contributing

Thanks for helping improve the AAIF Community Events Toolkit. The repo root is both the
marketplace and a single plugin (`aaif-events`) — `marketplace.json` and
`plugin.json` sit side by side in `.claude-plugin/`, and the skills live under
`skills/` at the repo root.

## Adding or editing a skill

A skill is a folder with a `SKILL.md` (plus optional `scripts/` and
`references/` dirs):

```
skills/<skill-name>/
├── SKILL.md            # the procedural spine — under 500 lines / 5k tokens
├── scripts/            # helper scripts, each with a test_*.py beside it
└── references/         # deep detail, loaded on demand
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

### Write the procedure, not the prose

A `SKILL.md` is read by an agent that is about to act, with the whole document
competing for attention against the conversation. Four rules follow from that,
and the skills in this repo were restructured onto them:

- **Keep `SKILL.md` under 500 lines and ~5,000 tokens.** That is the
  [spec's](https://code.claude.com/docs/en/skills) guideline and it is not
  advisory here: `aaif-sync-chapters` was 1,229 lines and 18k tokens, and an
  agent reading it had to find the four sentences that governed the command it
  was about to run. Move the rest to `references/`.
- **Everything in `references/` needs a *when*.** Each file opens with a
  one-line "load this when …", and `SKILL.md` closes with a table saying the
  same thing. "See references/ for details" is not progressive disclosure — it
  is a pointer the agent has no reason to follow at the right moment.
- **Multi-step work is a checklist, not a paragraph.** Use
  `- [ ] **1. Step** — command, then why it is here.` Steps with dependencies or
  an approval gate especially: the report → approve → write contract is stated
  once per skill and then referred to, rather than retyped per engine.
- **Gotchas earn their place; explanations of the obvious do not.** A gotcha is
  something that defies a reasonable assumption and that the agent *will* get
  wrong unguarded — "a duplicated column header aborts the engine, on purpose",
  not "handle errors appropriately". When you have to correct an agent, the
  correction belongs in that section. Don't explain what a `.docx` is.

If the agent reinvents the same logic on every run — composing the same `gws`
query, parsing the same file — that is the signal to write a tested script and
bundle it, not to describe the logic better.
`skills/aaif-event-status/scripts/fetch_tracker.py` exists for exactly that
reason: eight content skills used to describe the same two-call Drive lookup in
prose, and the nested shell quoting was where it went wrong.

### Other guidelines

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
  every copy together**, never just one. Its sibling
  **`check_tooling_banner_coverage.py`** catches the other half: a skill whose
  scripts drive `gws` but whose `SKILL.md` carries no tooling rule at all.
  Copies agreeing is worth nothing if a skill that needs one has none — which is
  how splitting a sync skill into four lost the banner from every one of them
  with the build still green.
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
