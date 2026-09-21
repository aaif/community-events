# Resource map — what the engine does, rule by rule

> Load this when a resource cell was not filled, a channel was reported as a candidate
> rather than written, or the handles column changed unexpectedly.

## The engine (`sync_resources.py`)

Five columns on the Chapters List, inserted **after `Country`**, answering "where
does this chapter actually live":

| Column | Holds | Filled from |
|---|---|---|
| `Chapter Folder` | Drive folder URL | the Chapters Drive folder, matched on folded name |
| `Slack Channel` | the chapter's own public channel | live Slack, exact match only |
| `Organizer Channel` | its private organizer channel | live Slack, exact match only |
| `Country Channel` | the country/regional room serving it | live Slack, exact match on the row's `Country` |
| `Organizer Handles` | **who should be in that organizer channel** | the intake's accepted organizers, resolved to Slack handles by email |

`Chapter Luma Link` was already on the sheet and stays
where it is, because `sync_chapters.py` derives it for new rows (a new chapter's
CTA depends on it) and this engine must not fight it for the cell.

### `Organizer Handles` is the one column that is rewritten

Every other resource column records *where a thing lives* and is only ever filled
when blank. Handles are **derived** from the intake and are therefore replaced
whenever they differ, with the old value shown in the report as a `~` diff. A
stale handle list is worse than an empty one: it reads as a roster, so someone
who left keeps looking current.

Someone with no Slack account is written as `Name (no Slack account)` rather than
omitted — they are exactly who an organizer needs to chase, and dropping them
would make the roster look complete. **Their name, never their email**: this
sheet is world-readable.

Because it is a replacement, `--write` re-checks that each cell still holds what
it held when the proposal was built (`was`), not merely that it is still blank.
Checking for blankness would have silently clobbered every hand-corrected list.

### Blank vs `none` — the distinction the whole engine turns on

- **Blank** = nobody has looked yet. Every run proposes for it again, and the
  audit's matcher falls through to its prefix/suffix scan.
- **`none`** = a human checked and there genuinely is no such channel. Proposals
  stop, and the audit stops guessing.

This is the sheet's stand-in for the JSON `null` that `channel_map.json` used to
carry. Collapsing the two would either re-ask a settled question on every run
forever, or freeze every row nobody has filled in yet.

### Only exact matches are ever written

The audit skill's rule applies here unchanged, and it is the reason this engine
proposes so little:

> **NEVER AUTO-MAP AN ALIAS.** A wrong alias reports a chapter as covered when it
> has no room, and nothing downstream re-checks it.

So `#berlin` is written for Berlin and `#india` for a row whose Country is India,
because those are exact name hits. `#cape-town-ai` for Cape Town is **printed as
a candidate and never written**, however obvious it looks. "No channel found" is a
correct, recoverable answer; a wrong channel is not.

`Chapter Folder` is the exception and is filled freely — it is *derived*, not
claimed. A folder either exists under the Chapters parent under this city's name
or it doesn't, and Drive can be re-asked at any time.

`Country Channel` cannot be derived for `#africa`, `#nordics-public`,
`#spanish-speaking` or `#french-speaking` — they serve several countries and no
rule gets them from a country name. Those stay whatever a human put there. The
report lists countries that have chapters but no channel named after them, which
is the queue for creating one.
