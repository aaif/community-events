# Caches, shared flags, and the combined report

> Load this when a run is slow or reusing stale data, when choosing between
> `--refresh` and a plain re-run, or before editing `summarize_audits.py` or any
> engine's `render_body`/`build_body`.

## Shared flags

| Flag | Effect |
|---|---|
| `--refresh` | Re-fetch from the API instead of reusing the cache |
| `--out NAME` | Output basename |
| `--cache DIR` | Where raw pulls are stored (default `.slack-audit-cache`) |

**Both engines cache their raw pulls**, and the first run of each is slow:
`users.list` pages 200 at a time (~20 min on a 30k-member workspace) and
`users.lookupByEmail` is ~1.5s per organizer. Run the first one in the background
and do something else.

Caches are written atomically and stamped with the workspace they came from, and
every reuse prints the age (`reusing users.json (32543 records, fetched 3 days
ago)`), so you can judge staleness instead of guessing. A cache discarded for
any reason — wrong format, wrong workspace — says so on the progress line rather
than silently re-fetching.

`--refresh` is rarely needed. The organizer engine **reconciles** rather than
reusing wholesale: organizers accepted since the last run are looked up, and so
are people previously recorded as having no Slack account, since that answer
changes when someone joins. Reach for `--refresh` when the *channel* list is
stale — someone created, renamed or archived a channel.

Cache files and the reports hold member names, email addresses and admin flags,
so all of them are created 0600 inside a 0700 directory, and **both engines
refuse to start unless their cache and output paths would be safe from `git add
-A`.** That check is not tidiness: this repo is public, and one commit would
publish the workspace directory irreversibly. It covers already-tracked files
too, which `.gitignore` alone does not, and it allows paths outside any
repository — there is nothing to commit them to.

## The single-HTML deliverable (`summarize_audits.py`)

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/summarize_audits.py
```

Writes `slack-full-audit.html`: a short index of anchor links, then four full
sections — Chapters, Organizers, Topics, Members — with the TL;DR (ranked
focus page) last, as a recap rather than a gate the reader scrolls past
first. Chapters and Organizers are two separate top-level sections, not one
nested under the other, because they answer different questions ("does this
chapter have a home?" vs "is the right person in it?") even though both come
from `audit_organizers.render_body()`'s two returned fragments.

It **measures nothing new**: it reads the four caches the engines wrote and
aborts naming the engine whose cache is missing, empty, or stamped with a
different workspace, rather than estimating a number nobody measured. It does
re-read the **Topics tab** over `gws` — the classification is config, not a
measurement, and is not cached — and it re-runs `classify`/`attach_activity`
once so the focus page and the Topics section select from the *same* records.
They used to derive separately and immediately disagreed about how many rooms
were quiet. The focus page ranks findings by what it costs to leave them alone
(a public organizers channel outranks everything; a quiet topic room outranks
a missing purpose line), and each section is the engine's own report body,
unmodified — plus that engine's own "Issues" → "To-do" subsection, built
directly from lists the report already computes (never new prioritization
logic invented for the combined document).

The seam is `render_body` / `build_body` in the three engines: they return the
page fragment, and their `render` / `build_report` wrap it for standalone use.
Keep both paths — a change to a report body must show up in the combined
document and the standalone one at once, which is the whole point of
composing fragments rather than stitching generated HTML.

The organizer report's filter buttons are removed from the combined document on
purpose — **both** the script and the markup. The script hides rows via a global
`tbody tr` query and would reach into the other sections; dropping it alone
left five controls that look interactive and do nothing, so
`audit_organizers.strip_controls()` takes the markup too. That is a real trade
in the combined document — a standalone `audit_organizers.py` run keeps
working, interactive filters.
