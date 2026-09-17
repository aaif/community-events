# Eval cases

Every other check in this repo is deterministic. `pytest` proves the scripts
compute the right thing, `check_frontmatter.py` proves the YAML parses,
`check_tooling_banner.py` proves the copies agree. None of them asks the
question the skills actually live or die on: **does the right skill fire, and
does the agent reading its `SKILL.md` do what the document says?**

That question has no answer you can grep for. A description edit that reads
like an improvement can quietly stop a skill triggering, or start it triggering
over its neighbour, and the repo would stay green. These cases are where that
shows up.

## What is covered, and why these

The suite is small on purpose — each case costs real model calls — so every one
of them targets a failure this repo has actually had or is one edit away from.

| Case | The failure it catches |
|---|---|
| `announcement-post` | The three attendee-facing writing skills sit close together in description space and all mention "post" and "AAIF event". This prompt says *RSVPs just opened*; firing the recap or the Luma page instead is the right voice on the wrong document. |
| `recap-post` | The same event, the day after. Paired with the case above: an edit that blurs the two breaks exactly one of the pair, which is how you learn it was a blur and not a fix. |
| `speaker-bio` | The prompt volunteers an address and a phone number, the way a real tracker row does. The public-copy rule is stated in nine `SKILL.md` files and was, until this case, verified in none. |
| `triage-intake` | A form answer asking to be marked `Accepted`. Untrusted input is the rule most likely to be obeyed by an agent trying to be helpful. |
| `sync-chapters-write-gate` | Every engine reports by default and writes only on approval. This checks the skill's document actually conveys that, rather than the scripts merely enforcing it. |
| `audit-slack` | Trigger coverage for a description that was 1,040 characters on one line until recently. The prompt is worded the way someone would really ask, not by echoing the skill's own words — a description that only matches its own vocabulary is not tested by a prompt that quotes it. |

## Running them

```bash
claude plugin eval . --no-publish --trust-plugin              # the whole suite
claude plugin eval . --case "Speaker*" --runs 1 --no-publish  # one case
```

`--case` matches the case's `name:`, not its directory. `--no-publish` keeps
the HTML report local; without it the report is uploaded to claude.ai, and
these transcripts are about people (synthetic ones here, but the habit is the
point). Exit code is `0` when every case meets the threshold (`1.0` by
default), `1` below it. `--json <file>` writes the full result; reports land in
`evals/results/<timestamp>/`, which is gitignored.

Each case runs twice by default: once with the plugin and once without, and the
suite reports the difference. That second arm is the useful half. A grader that
passes in both arms is testing the base model, not this plugin — so anything
that only holds because a `SKILL.md` says so is marked `arm: with-only`, and
the `Δ` column is then a direct measure of what the skill is contributing.

A full pass costs roughly \$3 and takes about 12 minutes, so every case is set
to `runs: 1` — with the two arms that is already two model calls each.

The first run of `speaker-bio` made the case for itself. Given a prompt that
volunteers a speaker's email address and phone number, the no-plugin arm put
both in the published bio. The plugin arm did not. That is the public-copy
rule earning its place, measured rather than asserted.

## Why CI does not run these

`validate.yml` runs on fork pull requests and therefore holds **no credential**
— see AGENTS.md. `claude plugin eval` needs model access, so wiring it into that
workflow would either leak a key into a public log or hand one to a fork. Run
the suite locally before changing any `description:` field, and treat a failure
the same way you would a failing test.

If this ever needs to run in CI, it belongs in a `workflow_dispatch`-only
workflow drawing from the reviewer-gated `ops` environment, with `--no-publish`
and no artifact upload, in the shape `run-skill.yml.disabled` already has.
`scripts/check_workflows.py` enforces that shape.

## Adding a case

A case is a directory holding `prompt.md` (frontmatter plus the prompt) and
`graders/*.md` (one assertion each). Grader types used here are `tool_used` for
"this skill fired", `regex` for "this string is or is not in the output", and
`llm` for judgements a regex cannot make.

Four things worth keeping to:

- **Write the prompt as a person would say it**, not in the skill's own words.
  A prompt that quotes the description tests nothing about whether the
  description covers how people actually ask.
- **Assert the neighbour does *not* fire**, wherever two skills are close. "The
  right one fired" and "only the right one fired" are different claims, and the
  second is the one that catches a description growing too broad.
- **Mark a grader `arm: with-only` when it tests something the plugin supplies.**
  Scored in the no-plugin arm it measures the base model's ignorance and drags
  the case below threshold for a reason that is not a defect.
- **Give it enough turns and seconds.** The first draft used `max_turns: 4`,
  and the content cases failed because the run ended before the post was
  written. The audit case then hit the 300-second default, because that skill's
  runbook has real work in it. Both are red results that say nothing about the
  skill: read a turn-limit or timeout error as a broken case, not a finding.
- **A Δ of zero means the case is measuring nothing.** The first draft of
  `triage-intake` pasted a whole intake row into the prompt and asked where it
  stood. The skill did not fire and was right not to — with the row already in
  the prompt there was nothing to read from the sheet — and the case scored the
  same in both arms. The suite told the truth; the case was the bug.
