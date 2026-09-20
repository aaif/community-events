# The Chapters List is the audit's configuration

Schema reference for `aaif-audit-slack`. Both engines read which rooms
belong to which chapter, and how a subject-matter channel is classified,
from columns on the Chapters List rather than from anything in this repo.
Nothing here is a step to run; it is what the columns mean.

Channel naming is not consistent, so matching is config-driven. The map is **three
columns on the AAIF Community Chapters List**, read straight off the sheet by
`read_chapters()`:

| Column | Was | Means |
|---|---|---|
| `Slack Channel` | `public` | the chapter's own channel, where the slug convention doesn't hold (`San Francisco → bay-area`; NOT `Madrid → españa` — that's a country room, and filing it here falsely reports coverage) |
| `Organizer Channel` | `organizers` | its organizer channel, where not named `<city>-organizers` |
| `Country Channel` | `regional` | a channel that *serves* the city without being its own (`Chennai → india`, `Lagos → africa`). Reported as **regional only**, never counted as chapter coverage — a member there has no local room. |
| `Ops Notes` | — | free text a person keeps beside the chapter row; `chapter_health` quotes it, nothing writes it or acts on it |

- A **blank** cell means nobody has looked yet: the matcher falls through to the
  prefix/suffix scan below.
- The literal **`none`** records that no channel exists and stops the matcher
  guessing. It is the sheet's stand-in for the JSON `null` this map used to use.
- A leading `#` is tolerated and stripped — people type it about half the time.

That move was deliberate. The people who can say whether `#bay-area` really is San
Francisco's room are the organizers, and they will open a spreadsheet; they were
never going to open a pull request against a JSON file in a plugin repo. Keeping
the map where the people who know can correct it is the point.

**`scripts/channel_map.json` is gone.** The matching vocabularies that were the
last thing in it now live on a **`Slack Config`** tab of the same spreadsheet,
`Setting | Value | Notes`, one row per value in priority order:

| Setting | Values |
|---|---|
| `Public channel prefix` | tried in order when matching a chapter's own channel — `(none)`, `meetup-`, `aaif-`, `mlops-`, `aaif` |
| `Organizer channel suffix` | tried in order as `<city><suffix>` — `-organizers`, `-organisers`, `-chapter-leads`, `-organizer`, `-leads`, `-meetup-organizers` |
| `Staff email domain` | addresses here count as staff, not as unaccounted members |

**Row order is load-bearing** — the prefixes are tried top to bottom, so the bare
slug beats `meetup-<slug>` deterministically rather than by whatever order the
Slack API returned channels in. Reordering the rows changes which channel matches.

`(none)` is the first prefix and means *no prefix at all* — the plain city slug.
A spreadsheet cannot hold an empty string distinguishably from an empty cell, so
it needs a visible sentinel, exactly as `none` does in the channel columns. A
genuinely blank Value cell **aborts** rather than being read as the bare prefix:
a half-typed row must not quietly widen the matcher.

`load_config` also aborts on a row whose `Setting` label names nothing it knows —
a typo'd label would silently drop a prefix and change what matches.

Proposals come from `aaif-sync-slack`' `sync_resources.py`, which writes a cell
only on an **exact** channel-name hit and prints everything weaker as a candidate.

**Never auto-map an alias.** Every channel named on a chapter row is a human
judgement that it really is that chapter's channel. When the report flags a
near-miss, or you spot a likely match yourself, **propose it and wait** — show
the user the chapter, the candidate channel, its member count and why you think
they correspond, and let them confirm before anything is written. Do not fill the
cell on your own authority, and do not widen the matcher to catch it.

The audit is allowed to answer "no channel found"; that is correct and someone
will notice. A wrong alias is neither — it reports a chapter as covered when it
has no room, and nothing downstream ever re-checks it. Prefer the flagged unknown
every time.

The engine enforces this rather than trusting it:

- **`none` genuinely stops the matcher** in all three columns. It records "a human
  checked and there is no channel", which is an answer, so no slug guess and no
  suffix scan follows. Near-misses are still listed for a human to look at.
- **An alias that no longer resolves aborts the run — in all three columns**,
  `regional` included. A renamed or archived channel is a configuration bug;
  left alone it downgrades the chapter to "no channel at all" and generates
  advice to create a room it already has.
- **A `public` alias pointing at a private channel aborts.** The automatic path
  refuses private channels, and an alias must not be a way around that. Under
  `--planned-ok` it is downgraded to "held private" and the chapter truthfully
  reports as having no public channel yet — never as covered.
- **The report says how each chapter matched.** Aliased chapters and deliberate
  `null`s are listed in *Data quality*, so coverage that rests on the map is
  visible rather than indistinguishable from a name match.

`_provenance` at the top of the map records that the current entries were
inferred by an agent on the first run and never confirmed by anyone who runs
these chapters. Until a human signs them off, say so when you report coverage
numbers. Delete that block once they are checked.


Same decision as the channel map, for the same reason: whether `#be-shameless`
is a subject or plumbing is a **human judgement**, and the people who can say are
the people with the spreadsheet. It is a **`Topics` tab**, `Channel | Kind |
Theme | Notes`, one row per channel someone has classified — anything live and
public without a row is reported as *unclassified*, never guessed at.

| Column | Means |
|---|---|
| `Channel` | the slug; a leading `#` is tolerated and stripped |
| `Kind` | `topic`, `software`, `cloud`, `vendor` — the **subject** rooms, which is what the report measures — or `geo`, `community`, `ops`, which are filed as deliberately *not* topics |
| `Theme` | the grouping the report charts and clusters by (`LLMs & agents`, `Data & pipelines`, …) |
| `Notes` | free text for the human |

`topic` vs `software` is a subject the community discusses (`#agents`,
`#coding-agents`) against one named piece of software you install or call
(`#fastmcp`, `#oss-zenml`, `#triton-inference-server`). It is **not** an
open-source/commercial line: a room named after a company's product is still a
room about that software. `vendor` is left for the handful of shared rooms a
partner runs *with* us (the `ext-*` Slack Connect rooms) — the kind records the
relationship, never the subject, because no channel here is about a company
rather than about its software.

**The engine never infers a topic.** A channel with no row is reported as
*unclassified* — an answer someone will notice — and is counted in nothing else.
`geo`/`community`/`ops` rows exist so that "not a topic" is a filed decision
rather than an absence, exactly as `none` does in the channel map.

- **A blank or misspelled `Kind` aborts the run.** Silently dropping a row would
  remove a room from every number on the page.
- **A channel on the tab that no longer resolves aborts** — renamed or archived,
  same class of configuration bug the chapter map aborts on.
- **Chapter, organizer and country rooms are excluded** from *unclassified* by
  reading the organizer engine's `audit.json`. Without that file the list is
  inflated, and the page says so on its face rather than under-reporting.
