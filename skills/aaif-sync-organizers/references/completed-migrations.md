# Completed migrations

Background for `aaif-sync-organizers`. All four of these have already run
against the live estate. They live in `migrations/`, not `scripts/`, and
they are kept for two reasons: a live engine names one of them as the fix
when a sheet is missing its columns, and each one is still the tool that
proves the estate is consistent with the schema it established.

**Do not run one as part of normal work.** Each is a report by default and
writes only under `--write`, the same gate as every engine.

## One-time migration (`migrate_resource_columns.py`)

The columns did not exist until 2026-08-10, and the whole map lived in
`aaif-audit-slack`'s `channel_map.json`. `migrate_resource_columns.py` runs in two
independently-guarded phases:

1. Inserts the resource columns (`RESOURCE_COLUMNS`; the original 2026-08-10 run
   inserted four, `Organizer Handles` joined the block after) and seeds the three
   channel ones from that file's
   `public`, `organizers` and `regional` tables.
2. Writes the file's remaining matching config — the prefix and suffix
   vocabularies and the staff email domain — onto a new **`Slack Config`** tab,
   then **deletes `channel_map.json`**.

After it, the channel map and its config live entirely on the sheet; there is no
JSON file. Re-running it reports "nothing to do".

It is one-shot and already run — it is documented here as the record of how the
layout changed. Do not re-run it. Note that everything from the old column `D`
onward shifted right by four; nothing in this repo cared, because all four
readers resolve columns by header name, but see the Notes below.

> **The seeded values are UNCONFIRMED.** `channel_map.json`'s `_provenance` block
> said its entries were inferred by an agent from channel names during the first
> audit and never checked with anyone who runs these chapters. Migrating them did
> not make them true — it made them visible to the organizers who can correct
> them, which is the actual argument for the move. Treat a seeded cell as a
> proposal until someone who knows the chapter confirms it. The ones worth a
> second look, because the channel name shares nothing with the chapter name:
> Madrid/Bilbao/Logroño → `#españa` (one channel, three chapters), San Francisco
> and Silicon Valley → `#bay-area`. (The rest of this list — `#colorado`,
> `#munchen`, `#washington-dc-the-capital`, and 2026-08-10's `#bangalore` —
> has been renamed onto the convention, a few entries still pending behind
> invisible squatters.)


## One-shot: retiring the status `New` (`migrate_status_prospect.py`)

Renames the intake status `New` → `Prospect` everywhere it is *stored*. Ran
2026-08-21/22; kept because it is the tool that proves the estate is still
consistent, and the pattern for the next status rename.

```bash
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_status_prospect.py             # report, writes nothing
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_status_prospect.py --city Boston
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_status_prospect.py --write     # apply, then verify
```

- **Phase A — the Intake Ops spreadsheet**: the Status dropdown, every Status
  cell (column A only — B+ are ARRAYFORMULA mirrors, and it refuses a tab whose
  Status column moved), the hand-made **conditional-format rules that test the
  literal** (the blue row color and the pink SLA-breach rule — `clean.py` does
  not own these, so nothing else would ever repair them), and the **"How to
  use"** tab's status prose.
- **Phase B — every chapter CRM** plus TemplateCity/TemplateSeries: the Status
  column is located by header name, and only a validation whose sqref covers
  *exactly* that column is touched, so the Signal list's unrelated `New`
  survives. Zip-part surgery; every other part is repacked byte-identically.
- **`--city` scopes Phase B only.** Phase A is the whole spreadsheet, so with
  `--city` it is reported but **not** written unless you add `--include-intake`.
- **Exit codes** follow the house contract: `0` in sync, `2` changes proposed or
  applied, `1` failure. A **refusal** ("this is shaped in a way I will not
  rewrite — a range-backed list, an `x14` validation, an `EXACT()` color rule")
  is reported as *needs a human* and never counted as pending work, so it cannot
  pin later runs to a permanent failure.
- Pre-edit workbook copies are kept under
  `<repo>/backups/crm-status-before-<UTC stamp>/before/` (gitignored, `0700`,
  printed at the end) after a `--write`; the temp working copies are deleted
  in both modes, since they hold the same member PII. Same retention rule as
  `sync_crm.py`: nothing prunes these — delete the directory once the write
  is confirmed good.
- Phase A writes each tab in **one** `spreadsheets.batchUpdate` (dropdown
  rule, color rules, and the Status cells as `updateCells` with raw string
  values), so a row inserted between calls can no longer shift the stamp onto
  a different row. `intake_tab_drifted` still gates the whole batch.

Once a run over the full estate exits `0`, the legacy `New` entries in
`sync_crm.PIPELINE_STATUSES` / `AUTO_STATUS` and `intake.normalize_status` can
be deleted — that exit code is the evidence they are waiting on.

## Notes

- Both tabs are read by **header name** (`Status`, `Full name`, `City (Existing)`,
  `City (New)`, `Run events before?`, `Why organize / ties`, `City`, `Organizers`),
  never by fixed column letter — the script aborts loudly if a header disappears.
  **Writes** are addressed the same way: the chapters tab is read `A:AZ` and every
  write target is derived from the header row's index. Every column a new row
  writes (`Title`, `City`, `Organizers`, `CTA`, `URL for CTA`, `Chapter Luma Link`)
  is resolved up front and aborts if missing — a silently skipped one would publish
  a chapter with no title or a dead CTA. The tab was restructured twice: from
  `City | Organizers | Previous MLOps Organizers | Chapter Luma Link` into an
  11-column website feed, and then again when the four resource columns were
  **inserted after `Country`**, shifting everything from the old `D` rightwards.
  The canonical column list lives in `HEADERS` in `scripts/test_sync_chapters.py`,
  which is executable and therefore can't go stale.
  The old hardcoded `B`/`A:D` writes are why nothing may be addressed by letter again —
  and the insert is why that rule now has teeth: every in-repo reader survived it
  untouched precisely because none of them spells a column letter. The tests assert
  the write ranges by *deriving* them from the header row for the same reason; a
  hardcoded `I2` there would have failed as a stale expectation, not a real bug.
  Note `Chapter Luma Link` is **hidden** in the sheet UI — hidden ≠ absent.
- **A consumer outside Drive does not get the same protection.** Sheets rewrites
  its own references on an insert; a Sanity import, a saved query or an external
  script reading the feed by column position does not move. There was none to fix
  when the resource block went in (no bound Apps Script, no formulas, no named or
  protected ranges, and `Past Events` is keyed on City with its own columns) — but
  confirm before inserting another column, because nothing here can detect one.
- Quote the tab name in any manual A1 ranges (`'Chapters & Teams'!I11`) — it
  contains `&` and spaces.
- Unresolved rows already hand-placed on the chapters list are flagged
  "no action needed" so they don't nag every run.
- `sync_crm.py` and `sync_about.py` import the `gws` wrapper, the Drive
  `download()`/`upload()` helpers, city folding, the near-miss stoplist and
  `resolve_city()` **from `sync_chapters.py`** rather than copying them. Two
  copies would drift, and a city that folds one way in one engine and another
  way in the other would put a person in a CRM whose feed row says something
  else. In `sync_crm` the shared `resolve_city()` is the **fallback** beneath
  the role tab's own `Chapter` formula, which additionally resolves the form's
  free-text city (the one extra step documented in §2's chapter-resolution
  note).
- `sync_about.py` re-reads the intake a second time for its **roster** — every
  name the intake knows for a city, at any status. That is what tells a removal
  "applicant we decided against" apart from "line we cannot account for", and
  it is deliberately not filtered to accepted rows.
- The CRM's `Attendees` sheet is resolved through `xl/workbook.xml` and its rels,
  never by guessing `xl/worksheets/sheet1.xml` — sheet order and file numbering
  are independent, and the older workbooks are packed in a different order and
  store their strings in a shared table rather than inline. Both are read; only
  inline strings are ever written.
- Bad LinkedIn values (`https://google.com/url`) and odd city spellings come
  straight from the public form and are copied as-is. Fix them at the source with
  **`aaif-clean-data`**, then re-run — the CRM only fills blanks, so a corrected
  intake value will **not** overwrite the bad one already written. Clean first.

## One-shot: splitting `Status` into decision + role (`migrate_interested_in.py`)

Adds the `Interested in` column to every chapter CRM and moves the role out of
`Status`. Written 2026-08-25. **`sync_crm.py` refuses to open a workbook that
has not had this run**, so it goes first.

```bash
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_interested_in.py             # report, writes nothing
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_interested_in.py --city Boston
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_interested_in.py --write     # apply, then verify
```

Five parts, per workbook (templates included):

- **The column is APPENDED**, at the first free column past the last header —
  `L` on the shipped layout. Inserting it in place would renumber every cell
  ref in ~1000 rows, every `dataValidation` sqref, and the Guide tab's
  cross-sheet formulas; appending got the split shipped without touching any of
  that. `migrate_column_order.py` (below) then does the insert properly. Its
  `<col>` width is split out of the shipped `L..S` run rather than narrowing
  all eight.
- **Rows are backfilled from their own `Notes (CRM)`** provenance string, which
  has carried both facts all along. Nothing is invented: a row whose `Status`
  holds a role but whose note won't parse has the role **relocated** and its
  `Status` left **blank** — blank is in `AUTO_STATUS`, so the next sync fills it
  from the live intake, whereas a guess would outrank it. A `Status` that isn't
  a role word (`Attended`, `Regular`, `Volunteer`, `Declined`, a pipeline value,
  or blank) is never touched, and an `Interested in` that already has content is
  never restated.
- **Both dropdowns are rewritten** to `sync_crm.DV_EXPECTED`. A validation whose
  `sqref` spans several columns is **refused and reported** — rewriting it would
  silently re-validate a neighbour (the Signal list's unrelated `New`), and
  there's no safe way to split a merged sqref without knowing what a human meant.
- **The `Status` column gets colour rules**, so the decision reads at a glance:
  green for `Accepted` / `Regular` / `Volunteer`, amber for `In progress` /
  `Interviewing` / `Tentative`, red for `Declined`. `Prospect` (the commonest
  value) and `Attended` are deliberately **left unpainted** — colouring every
  value colours nothing. A blank `Status` is not painted either: the range is
  `D2:D1000` and the template pre-creates 1000 empty rows, so a blank rule
  would light up the whole column in every chapter.
  The three `dxf` styles already in every workbook are **referenced, not
  added** — `dxfId` is a positional index into `<dxfs>`, so appending a style
  to some workbooks and not others silently paints the wrong colour in
  whichever drifted. A workbook with fewer than three is refused and reported,
  and a `Status` column someone has **already** painted is reported and left
  alone. The block is inserted after `sheetData` and before `dataValidations`,
  because `CT_Worksheet` fixes that order and appending to the end of the
  worksheet makes Excel call the file corrupt.
  (Nothing tested `Status` before this — the existing rules cover **Signal**
  and **Trusted/Regular** only, which is why the split broke none of them.)
- **The Guide tab's formulas are repointed.** Two landmines: the dashboard
  counted `COUNTIF(Attendees!D2:D1000,"Speaker")` (and `"Organizer"`), which
  reads **0 in every chapter** the moment roles leave `Status` — moved to the
  new column with `*…*` wildcards, since a cell can say `Organizer/Speaker`; and
  the "Live list" `FILTER` already referenced `Attendees!L2:L`, a column that
  did not exist on the 11-column layout, so it had been returning a blank column
  since the columns were last renumbered. A Guide someone has edited reports the
  miss instead of having a regex rewrite a formula it doesn't understand. Cached
  `<v>` results are left stale; both Sheets and Excel recalculate on open.

Idempotent — a second pass proposes nothing. Report is the default; `--write`
applies, re-downloads every written workbook and prints a `Verified` line.
Pre-edit bytes land in `<repo>/backups/crm-split-before-<stamp>/` (gitignored,
and holding real names and emails) — **delete the directory once the write is
confirmed good**; nothing prunes it.


## One-shot: `Interested in` before `Status` (`migrate_column_order.py`)

`migrate_interested_in.py` appended the new column at `L`. This moves it to `D`,
immediately before `Status`, so "what they asked for" and "how far the decision
got" are read together. Run **after** the split migration.

```bash
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_column_order.py             # report, writes nothing
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_column_order.py --city Boston
python3 ${CLAUDE_SKILL_DIR}/migrations/migrate_column_order.py --write     # apply, then verify
```

```
before   A Full name  B Signal  C Trusted  D Status  E Notes … K What…  L Interested in
after    A Full name  B Signal  C Trusted  D Interested in  E Status  F Notes … L What…
```

Everything carrying a column position is renumbered: every `<c r="…">` (re-sorted
into ascending order — a row left out of order reads as a corrupt file), the
`<cols>` width runs (expanded, mapped and re-collapsed, because they are
**ranges** and rewriting min/max in place re-widens whatever shared a run),
`<dimension>`, `<autoFilter>` (which shipped as `$A$1:$K$1` and never covered
the appended column — it is widened to the real last column), every
`dataValidation` and `conditionalFormatting` sqref, A1-style column letters
inside cf **formulas** (the name-turns-red rule tests `$B2="Non-grata"`), and
the Guide's `Attendees!<col>` references.

- **Only `Attendees!`-prefixed refs move in the Guide.** A Guide-local `B5` is a
  cell of the dashboard itself; without the prefix scope the Guide rewrites its
  own layout along with the references it makes.
- **The verify is a data check, not an "it opens" check.** Every row is
  snapshotted **by header** before the write and compared after. A workbook whose
  refs were renumbered inconsistently still opens, still has twelve headers, and
  quietly shows one person's email against another's name — that fails here.
- **The mapping is derived by rebuilding the column order as a list**, not by
  arithmetic on "shift everything between src and dst": the arithmetic version
  needs a different sign for a left-move than a right-move and gets one boundary
  wrong. A chapter's own extra columns keep their relative position.
- `sync_crm.py` is unaffected — it addresses every column by header name, so the
  move is invisible to it. `migrate_interested_in.py` derives the Guide's target
  formulas from the live header map for the same reason; hardcoded letters would
  make it report three unrecognised Guide formulas on all 83 chapters forever.

Idempotent — a workbook already in the target order plans nothing.
