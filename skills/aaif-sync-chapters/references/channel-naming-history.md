# Why some channel names are not `<city>`

Background for `aaif-sync-chapters`. Nothing here is a step to run — it is
the record of how the Slack channel names got the shape they have, so that a
name that looks wrong is not "fixed" into a squatted or duplicated room.

The 2026-08-17 naming sweep renamed the legacy meetup-era, wider-scope and
local-language channels to the convention (a rename keeps members and history,
so nothing was lost). The exceptions that remain are all deliberate and all
recorded in code:

- **`KEPT_NON_CONVENTIONAL`** — a *decision record* (nothing reads it at
  runtime) of city rooms whose name is kept: `#bay-area` (one room serving two
  chapters) plus the 2026-08-22 qualified **city** slugs and `#munchen`. The
  qualified *organizer* slugs live on the sheet and in `CHANNEL_RENAMES`, not
  here.
- **Qualified slugs (2026-08-22, user-decided — "fewest divergences"):** where a
  conventional name is held by an *invisible private squatter* the Pro plan
  cannot reclaim, the room takes a qualified form instead of waiting: city rooms
  get a state code (`#austin-tx`, `#charlotte-nc`, `#dallas-tx`) or keep the
  native name (`#munchen`, the `#españa` precedent); organizer rooms get
  `<city>-<state|countrycode>-organizers` (`seattle-wa-organizers`,
  `toronto-ca-organizers`, …). Country rooms follow the same native-name move —
  `#deutschland` was *created* as Germany's country room because `#germany` is
  squatted (`COUNTRY_CHANNELS`). The suffix and column semantics never change —
  only the slug diverges.
- **`Erstwhile Channels` (sheet column):** every squatted or superseded name is
  recorded there per chapter, and `provision_channels.py` **refuses to create or
  rename into any recorded name** (`forbid_erstwhile`, fed by
  `sync_resources.read_erstwhile`). If a refusal is wrong, fix the sheet — both
  the resource cell and the history column — not the plan.
- **Renames are individually confirmed.** Never batch-rename on a scheme
  approval: present each `old → new` to the user and wait, then delete the
  "renamed the channel" system message the rename leaves behind.

### A country room is not a chapter room

`#españa` was seeded as Madrid's, Bilbao's and Logroño's **own** channel. It is
Spain's *country* channel, and the difference is the entire point of the two
columns: a country room means those chapters have **no local room**, which is what
the audit is supposed to report ("regional only — a member there has no local
room"). Filed as a chapter channel it reported all three as covered instead: the
exact failure the never-auto-map rule exists to prevent, arriving through a seed
rather than a guess. `MISFILED_COLUMNS` corrects it, and `COUNTRY_CHANNELS` stops
a brand-new `#spain` being planned beside the well-populated room that already exists.

Watch for this shape whenever one channel serves several chapters — a shared
*chapter* room and a *country* room look identical on the sheet and mean opposite
things.

The sheet-side `RENAMES` map repoints cells whose channel took a new name
(`#bangalore` → `#bengaluru` was the first; the London and Bay Area organizer
consolidations followed). Always a *rename* on the Slack side, which keeps the
members and the history — never a create, which would split the chapter across
two rooms. The much larger Slack-side queue lives in
`provision_channels.CHANNEL_RENAMES`; the two maps stopped being mirror images
once the 2026-08-17 sweep's sheet cells were edited directly.

A dead Slack token skips the three channel columns and still reports the folder
column; the report says which half was skipped, so an empty channel section is
never mistaken for "nothing to do". Set `AAIF_SLACK_READ_TOKEN` or
`AAIF_SLACK_WRITE_TOKEN` (environment variable, or the repo-root `.env`) and
re-run for the rest — the Slack CLI credential expired for good in 2026-08 and
is only the last-resort fallback.

One check runs even without Slack: a **filled channel cell that cannot possibly
name a channel** (whitespace, `/`, `:`, `#`, `@` or `,` in it — a pasted URL,
an email address, a sentence) is reported as malformed and counts as drift.
"Filled" otherwise reads as healthy forever: Montréal's `Slack Channel` cell
held a copy of its Drive folder URL and every count said the chapter was mapped.
The character list is deliberately minimal so the accented `#españa` is never
flagged.
