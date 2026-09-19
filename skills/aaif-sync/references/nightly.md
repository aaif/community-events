# Unattended runs — `nightly.py`

> Load this before scheduling the pipeline in CI, when reading a `nightly-reports/` log,
> or when a nightly run exits non-zero.

`nightly.py` runs the five engines in the pipeline order SKILL.md gives as subprocesses —
report-only by default, `--write` passes through to every engine. It exists for
a scheduled CI job, and two conventions make that workable:

- **Exit codes are the drift signal.** Every engine now exits `0` when in sync,
  `2` when its report proposes changes (or, for `sync_resources`, when a filled
  channel cell is malformed), and anything else on failure. The runner
  aggregates the same way: `0` all clean, `2` drift somewhere, `1` any failure.
  `sync_access` counts pending grants and a pending lock as drift.
- **The runner's stdout never names a person.** This repo is public and a CI log
  is a publication, so the summary is engine names, outcomes, durations and log
  paths only. The full reports — which do carry names, emails and per-person
  diffs — go to `nightly-reports/<UTC stamp>/<engine>.log`, which `.gitignore`
  covers and which must never be uploaded as a public artifact. Any print added
  to `nightly.py` must be composed of fixed strings and its own computed values,
  never engine output.

The Slack write steps (8, 9, and `prune_organizers.py`) are deliberately absent:
they carry `--i-have-approval` because they notify or affect real people, and a
scheduled job must never hold that approval. `sync_resources`' Slack half
already degrades to folder-only on a dead token, which is the right unattended
behaviour — and an *involuntary* skip (a dead token, as opposed to `--only
folder`) prints a stdout `PARTIAL:` marker and exits `2`, which the runner
surfaces as its own **PARTIAL** outcome. A half-checked night must never read
green, or a token that dies in CI stays dead forever; recovery costs nothing
because blank cells re-propose on the next authenticated run.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py                # report all five
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py --write        # apply, unattended
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py crm resources  # a subset
```

Note `--write` keeps each engine's own refusals: `sync_chapters` still holds
back a new row whose Luma page is not live — so an unattended write run is the
same set of decisions the interactive flow would have made, minus the pause for
approval. **Except `access`: the runner never passes `--write` to
`sync_access.py`**, even under `nightly.py --write`. Its grants hand standing
Drive access to addresses typed into a public form, and Drive may email the
person as a side effect — that is a human's call every time. The nightly runs
`access` in report mode, marks the line `(report mode — never written
unattended)`, and if the report has pending grants or a pending lock it exits
`2` with an explicit `NEEDS A HUMAN` note telling the operator to read
`access.log` and run `sync_access.py --write` by hand.

The run directory is created `0700` and each engine log is opened `0600` —
the logs hold names and emails, and a CI checkout is often world-readable.
Engine stdout (what the logs hold) contains names and emails: **never quote it
in a commit message, PR body, or public post.** All five engines (and the
three Slack write steps) take `--redact` — on by default when the `CI` env var
is `1`/`true`/`yes`, announced by one stderr line — masking emails as
`a***@***.tld` and names as a first initial. The nightly passes `--no-redact`
to every engine on purpose: its logs are `0600` files in a `0700` gitignored
directory, never CI output, and the `NEEDS A HUMAN` step needs the real
addresses in `access.log`. Run an engine directly under CI and its stdout
comes out redacted instead.
Wiring this into GitHub Actions (auth, secrets, where the digest goes) is a
separate change; the runner is deliberately CI-agnostic.
