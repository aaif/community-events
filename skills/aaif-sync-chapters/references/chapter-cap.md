# The 100-chapter cap and the chapters-tab `Status` column

> Load this when a new chapter row is refused, when deciding whether to retire a
> chapter, or before setting a `Status` value by hand. `chapter_health.py` is the
> read-only evidence for that decision.

The chapters tab carries `Status` (column R) and `Merged Into` (S), added
2026-09-18. `Status` is set by a **human**, exactly like the organizer `Status`
it is modelled on, and for a concrete reason: the estate holds no chapter
founding date. Channel creation clusters on the bulk provisioning runs, and all
98 Drive folders carry just 9 distinct creation dates — the oldest younger than
events already recorded on `Past Events`. So "stood up last week" and "died a
year ago" are indistinguishable from data, and any script that guessed would
mislabel around 30 chapters.

**No script writes `Status` today.** Every chapter is born blank, blank counts
as live, and moving a row out of blank is entirely manual. Stamping
`Provisioned` at creation is the obvious producer — it is the one moment the
answer is known — but `create_chapter.py` does not write the feed row (this
engine's new-row path does), so that is its own change and is not in this
version.

| Status | Meaning | Counts toward the cap |
|---|---|---|
| `Active` | an event or a Slack message inside the window | yes |
| `Provisioned` | rooms and folder exist, not launched yet | yes |
| `Dormant` | was active, has gone quiet — a review queue, not a decision | yes |
| `Merged` | folded into the chapter named in `Merged Into` | **no** |
| `Deprecated` | retired outright | **no** |
| *(blank)* | not yet triaged | **yes** |

Blank counting as live is deliberate: the alternative lets the estate grow past
the cap simply by nobody filling the column in.

`CHAPTER_CAP = 100` counts **rows on the chapters tab**, and the feed row is the
only thing that moves that count — so this engine's new-row path is the real
chokepoint. `create_chapter.py` checks the same cap before building a Drive
folder, but that check is an early warning, not a guarantee: creating a folder
adds no row, so three `create_chapter --write` runs at 99 live chapters each
read 99, each pass, and each build a folder, and the next sync refuses all three
rows together. Closing that properly means `create_chapter` claiming its row as
it creates the folder, which it does not do yet. The number is defined once, in
`sync_chapters.py`, and `create_chapter.py` imports it; `census_of()` is the one
routine that decides which statuses count, for the same reason. Planning and report runs are exempt
from the refusal on purpose — seeing what a chapter *would* look like, and which
cities are queued, is how an operator decides what to retire, so refusing the
report would remove the tool they need in order to comply.

`chapter_health.py` is the read-only evidence for that decision. It ranks
chapters on two signals — recorded events and last human Slack message — and
never writes a `Status`. Both signals are needed: 14 chapters have no recorded
event but a live public channel, so an events-only rule would retire chapters
that are demonstrably running. The `Past Events` tab is hand-maintained — nothing in this
repo *writes* it, and `chapter_health.py` is its only reader — which is why its
silence is weak evidence on its own, and why an absence of events can never by
itself put a chapter in the queue.

**Not yet wired: the website.** The tab *is* the feed — the site reads the sheet
directly and nothing here publishes to it. `Status` therefore has defined
semantics but no effect on what aaif.io shows; making the site skip
`Merged`/`Deprecated` rows is a change on the website side.

`--scope {organizer,country,both,champs}` picks the target, default
`organizer` — each chapter's private Organizer Channel **and the one
workspace-wide `#local-champs` room**. `--scope country` targets the public Country Channel instead (several
chapters routinely share one, `#españa` serves Madrid/Barcelona/Bilbao — the
roster is the union of every contributing chapter's accepted organizers,
deduplicated by email), which is a deliberate, narrower version of the same
override Rahul made 2026-08-25 for public *city* channels: being an organizer
of your own chapter's public room is part of the role, the room is public
anyway, leaving is one click. `--scope both` runs every pass off one fetch, and
`--scope champs` targets the champs room alone.

`#local-champs` joined the **default** scope on 2026-09-18. It had been a
curated leadership room that nothing in this repo ever wrote to, and it drifted
exactly as an unmaintained room does: 28 of the 155 accepted organizers with a
Slack account were missing from it. It is one workspace-wide room rather than a
per-chapter channel, so it has no Chapters List column — the `CHAMPS_COLUMN`
sentinel stands in for one, and the room's name is imported from
`audit_organizers` so the repo holds a single definition of it. Two
consequences worth knowing: its roster is the union of every chapter's accepted
organizers deduplicated by email, and members who are *not* accepted organizers
are deliberately **not** counted into the report's "in a channel the intake
does not list them for" tally — a leadership room legitimately holds them, and
folding ~135 such people in would bury the real signal. `aaif-audit-slack`
flags absence from it as an issue to match (and stays silent when the room
could not be read, which is not the same fact as absence).

Otherwise, scope is deliberately narrow:

- **Adds, never removes.** Someone in the channel the intake doesn't know is
  reported and left alone — an audit finding, not a cleanup task.
- **Batched per channel**, so Slack renders one event rather than N join lines.
- `already_in_channel` is treated as benign: someone may join between the read
  and the write.

Gated harder than a sheet write, for the same reason `sync_access.py` doesn't
mail share notices by default: an invitation is a notification to a real person,
and a hundred arriving at once reads as a phishing wave. Needs
`groups:write.invites` / `channels:write.invites`, which the audit token does not
carry.
