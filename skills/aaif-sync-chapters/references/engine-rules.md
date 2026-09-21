# Chapters feed — what the engine does, rule by rule

> Load this when the report surprises you: a row skipped, a city called a near-miss,
> a value not written, or a run that aborted. Also before editing the engine.

## The engine (`sync_chapters.py`)


Push organizer decisions from the **AAIF Community Intake Ops** sheet
(id `1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o`, tab `Organizers`) into the
**AAIF Community Chapters List** (id `18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg`,
tab `Chapters & Teams`): every organizer whose Status is **`Accepted`** or
**`Existing (from MLOps)`** must appear in their city row's **Organizers** column,
and cities with no row yet get one appended. The intake sheet is only ever **read**;
all writes go to the chapters list. Idempotent — a second run right after a sync
proposes zero changes.

**The report prints:** per-city adds to existing rows (with the exact new B value),
proposed new city rows (appended row number + Luma slug + whether the page is live),
near-miss city names, unresolved-city rows, and deduped duplicates. `--write` applies
everything in **one** `values batchUpdate` — a partial failure cannot half-sync the sheet.

### Rules

- **Status filter is exact-string**: `Accepted` and `Existing (from MLOps)` only.
  (Matching a prefix like `Existing` once missed all 23 MLOps rows.)
- **City resolution per intake row**: `City (New)` wins if non-empty; else
  `City (Existing)` unless it's an `Other…` placeholder; else the row is
  **unresolved** — reported with its free-text answers quoted (and an inferred
  city when the text explicitly names a chapter city), **never written**. The fix
  is to fill `City (New)` on the intake row (see `aaif-clean-data`), then re-run.
- **Merge, don't overwrite**: the existing `Organizers` cell is parsed on `;`,
  intake names are appended only if missing (compared case-, whitespace- and
  accent-insensitively); names already there but absent from intake are left alone
  (manual entries live there). Written values keep original UTF-8 — an accented
  name stays accented.
- **Near-miss cities** are reported, not auto-matched — confirm the right row or
  fix the intake city, never create a near-duplicate row. A near-miss fires on a
  substring (intake `Delhi` vs row `Delhi NCR`) **or a shared discriminating word**
  (`New Delhi` vs `Delhi NCR`). Generic words are excluded — `new`, `san`, `city`,
  `saint`, `north`… — because a near-miss is never written and has no override
  flag, so a false positive doesn't cost one confirmation, it blocks the city
  **permanently**. Without that stoplist `San Diego` matches `San Francisco` and
  can never be added.
- **`MLOps Community Organizers` is read-only history — never modified.** Its spellings can differ
  from intake (e.g. "Adam Lite" vs "Adam Liter"); intake wins for `Organizers`.
  San Francisco people are **not** mirrored into the Silicon Valley row —
  `Organizers` follows the intake city; the MLOps column is where the legacy
  duplication lives.
- **New city rows** are appended after the last non-empty City row (not at the
  grid bottom), written across the **full feed width** so nothing lands in the
  wrong column: `Title` = `AAIF <City> Chapter`, `City`, `Organizers` = names
  joined `"; "`, `CTA` = `Stay Updated`, and `https://luma.com/aaif-SLUG` in both
  `URL for CTA` and `Chapter Luma Link` (slug = city lowercased, spaces/accents
  removed; same exceptions as `aaif-create-chapter`, e.g. Denver → `aaif-colorado`).
  `Country`, `Generated Geolocation`, `Summary` and `Image` are left **blank for a
  human** — the report names them; the row isn't site-ready until they're filled.
  The report says whether the Luma page is live, and **the row is written either
  way** (2026-09-17, user-decided: the page is made by hand and can follow the
  row). `--require-luma` restores the old gate, holding back a row whose page
  isn't live; held cities are named in the output, re-propose on every run, and
  the write exits `2` until their pages exist. Held rows never leave a blank row
  in the feed: the written rows are renumbered onto consecutive rows.
  **Why the gate went:** a new row is not site-ready regardless until `Country`,
  `Generated Geolocation`, `Summary` and `Image` are filled in by a human, so
  holding it back was never what kept a dead CTA off the site — it only delayed
  the row. What the gate *did* do was re-propose the city every run, keeping the
  missing page visible; **`--audit-luma` replaces that** and covers more, checking
  `Chapter Luma Link` on **every** feed row rather than only the cities being
  added today, and exiting `2` (report mode) when anything is dead, blank or
  unverified. It costs one paced request per row that has a link, and
  **luma.com rate-limits a full sweep**: verified live 2026-09-17, a 96-row run
  draws a `429` with no `Retry-After`, after which every later row 429s too. The
  sweep therefore stops at the first 429 and says so with a `PARTIAL:` marker
  rather than reporting the remaining rows as findings — one upstream fact must
  not become ninety false ones. Re-run later to finish. Page creation is manual, and a net-new city still needs
  its Drive folder/assets: run **`aaif-create-chapter`** for it as the follow-up.
- Duplicate intake rows for the same person+city are deduped (first wins, reported).
  Duplicate **chapter** rows (two rows for one city) are reported too — only the
  last is ever updated, so merge them by hand.
- **Malformed public-form text is excluded and reported, never written.** An
  intake name or city containing markup or control characters, or exceeding 120
  chars, gets its row skipped with a loud per-row line (row, city, reason) while
  every other row still syncs — one hostile or fat-fingered submission must not
  freeze the whole engine, but a flagged value still reaches no cell, About doc
  or CRM until the intake row is fixed. The CRM engine enforces the same check
  itself (`sync_crm.read_role_tab` reads the role tabs directly, not through
  this engine's intake read). And because `sync_about` rewrites its section
  **wholesale**, a chapter whose roster lost a row to this filter has its whole
  About doc **held back** — not planned, not written, exit non-zero — rather
  than rewritten minus the excluded organizer, which would silently delete an
  accepted person from a shared doc over a data bug in their row.
- **The run aborts rather than guessing** when: a header is duplicated (reads and
  writes would resolve to different columns); any written column is missing; a row
  below the last City row is non-empty (new rows are appended there and would wipe
  it); a city has no ASCII characters, so its Luma slug would be empty; or the
  sheet changed between building the proposal and writing it (row numbers are
  snapshot indices, and the per-city Luma checks sit in that window).
