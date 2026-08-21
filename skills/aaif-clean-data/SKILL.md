---
name: aaif-clean-data
description: Normalize and fix data quality in the AAIF Community Intake Ops sheet — canonicalize LinkedIn URLs, fix name/city casing & whitespace, derive each person's city from the form's free-text answer (City > Extracted, capital when only a country is given), flag bad/missing emails and duplicates, and surface broken rows in bright red. Reports & proposes by default; only writes on explicit approval. Use when asked to clean up / normalize / fix the intake data.
argument-hint: "[scan|apply|cities|install-flags|install-colors]"
---

# Clean AAIF Intake Data

Normalize the intake data **without silently changing it**: detect issues, propose
fixes with a before→after diff, and only write when the user approves. Fixes are
applied to the **source** tab `Form Responses` (id `1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o`)
so the cleaned values flow through to the computed role tabs. Every applied change
is noted per row in an **`Autofixes`** column on `Form Responses` (created on first
use) — provenance for what the cleanup touched.

Prereq: the `gws` CLI must be installed and authenticated (`gws-cli-access` memory).
See `aaif-intake-ops-sheet` memory for the sheet's structure. All reads/writes go
by **header name**, never column letter.

## The modes (engine: `scripts/clean.py`)

1. **Scan (default, read-only)** — detect & propose:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py scan        # human-readable
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py scan --json # structured
   ```
   Mechanical fixes proposed automatically: trim/collapse whitespace, re-case
   clearly all-upper/all-lower names & cities, canonicalize LinkedIn URLs
   (`https://www.linkedin.com/in/...`, strip tracking params & trailing slash).
   Flags raised (need judgment): `City="Other"`, missing/invalid email, duplicate
   email, LinkedIn that isn't a profile URL, missing name.

2. **Apply (writes, on approval only)** — feed an approved change list:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py apply changes.json
   ```
   `changes.json` is `[{"row": <source row>, "header": "<column>", "value": "<new>"}]`.
   Writes those cells in `Form Responses` and appends a note per row to the
   `Autofixes` column, formatted **`<phrase> -> <new value>`** (`;` joins phrases
   within one run, `|` joins runs). The value is carried so a second edit to the
   same field isn't deduped away as a repeat of the first; separators are stripped
   out of it, because a phrase that can't be split back out re-appends every run.

   `row` must be a data row (2..last); row 1 is the header and is refused. Values
   are written **RAW**, never `USER_ENTERED` — the form is public, and the
   normalizers will happily re-case `=IMPORTXML(...)` into a still-valid formula
   that would otherwise go live on write. If no requested header matches a column,
   the run aborts rather than reporting "No changes to apply".

3. **Cities** — turn the free-text city answer into a real one:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py cities           # report only
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py cities --write   # on approval
   ```
   Fills an **`Extracted City`** column on `Form Responses` (created on first use).

   ### The four columns, and the precedence
   | Column | What it is |
   |---|---|
   | `City` | the form's dropdown answer |
   | `Don't see your city above? Enter it here.` | the free text — an **input**, never the answer |
   | `Extracted City` | the city derived from that free text; human-correctable |
   | `Resolved City` | **`City` when it is a real city, else `Extracted City`** |

   > **`Resolved City` is an `ARRAYFORMULA` as of 2026-08-10** — it is derived,
   > never typed. It lives in `Form Responses!CJ2` and reads:
   > ```
   > =ARRAYFORMULA(IF(A2:A="","",IF((F2:F<>"")*(LEFT(F2:F,5)<>"Other"),F2:F,CL2:CL)))
   > ```
   > Row-emptiness is tested on `Timestamp` (A), **not** on `City` — a submission
   > with no dropdown answer at all must still resolve from `Extracted City`
   > rather than be blanked. `(cond)*(cond)` multiplies elementwise; `AND()` would
   > collapse the whole array to one value.
   >
   > **`apply` refuses to write to it** (`DERIVED_COLUMNS`). A literal in any cell
   > of that spill range collapses the column to `#REF!` — and the previously
   > documented fix for a wrong city was to write exactly that. Correct a city via
   > `City` or `Extracted City` instead.

   Raw free text never becomes the answer: "Hyderabad, India" resolves to
   `Hyderabad`, not to itself. To move someone, edit **`City`** — it outranks
   everything below it. To fix a bad parse, edit **`Extracted City`**.

   > **Why this exists.** Nothing ever read the free text, so someone who typed
   > their city sat `Accepted` but unplaced forever, while `Resolved City` — a
   > hand-filled override — silently outranked a *corrected* dropdown. On
   > 2026-08-10 both misfired at once: an organizer's city was edited on the
   > dropdown and nothing moved, and another sat unresolved with "Stuttgart"
   > typed into a column no tab reads.

   ### Extraction rules (`extract_city`, unit-tested offline)
   Each step exists for an answer the form actually received:
   1. **the whole answer is a chapter** — `Madison, WI`, `Delhi NCR` (the chapter's
      own name contains the comma, so splitting on it first is wrong);
   2. **a segment is a chapter** — `UAE, Dubai` → `Dubai`; `Udaipur , Jaipur` →
      `Jaipur`, the one that is a chapter;
   3. **a chapter is named anywhere** — `I am in Paris, France and Toronto` →
      `Paris`; longest name wins so `Delhi` never beats `Delhi NCR`;
   4. **a country was all they gave** → its capital — `Bulgaria` → `Sofia`. **A
      named city always wins**, so `UAE, Dubai` is never `Abu Dhabi`;
   5. **otherwise the first city-like segment**, with a leading/trailing country
      stripped — `Noida India` → `Noida`, `India, Gurugram` → `Gurugram`.

   Answers naming more than one chapter are proposed **and flagged `AMBIGUOUS`**.
   A typo is preserved, never guessed at (`Monterreyy Mexico` → `Monterreyy`) —
   the fuzzy match that would "fix" it is the one that silently moves someone.
   `ALIASES` maps the short forms people use (`DC`, `NYC`, `Bangalore`) onto the
   chapter's own name; it is deliberately small and explicit.

   ### Migration policy — an existing `Resolved City` always wins
   `Extracted City` is **seeded from the 122 hand-filled `Resolved City` values**
   rather than derived fresh. 23 of those rows have **empty** free text (the
   question didn't exist yet) and a human supplied the city from context, so
   deriving them would blank real organizers — Luxembourg, Tokyo, Vancouver, Pune
   and Singapore among them. Extraction fills only what is blank, which makes
   switching `Resolved City` to a derived value **lossless by construction**.

   `--write` **refuses to run** while any row has a real `City` contradicted by a
   different `Resolved City`, because deriving `Resolved` would then change that
   person's chapter. There are currently none; the check is what proves it.

   **The migration ran on 2026-08-10** — 154 `Extracted City` values written, then
   `Resolved City` converted to the formula above. Verified against a pre-write
   snapshot of all 315 rows: **0 values lost, 0 changed**, 185 gained (153 mirroring
   a real `City`, 32 newly derived from the free text), no `#REF!` on any role tab,
   and `sync_chapters` + `sync_about` both reported an unchanged world afterwards.
   To revert, paste the snapshot back over `CJ2:CJ` — the formula is one cell.

4. **Install-flags (maintenance)** — add/refresh the live error flag:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py install-flags
   ```
   Adds an `Issues` column (live `ARRAYFORMULA`) to each role tab plus a
   top-priority conditional rule that turns the whole row **bright red** whenever
   there's a genuine error — **missing/invalid email or a broken LinkedIn URL**. It
   auto-clears once fixed, and is distinct from the light-red "Denied" status.
   `City="Other"` is deliberately **NOT** an error (it's a normalization to resolve,
   surfaced by `scan`), so it never turns a row red. Already installed — re-run to
   re-point the `Issues` formula after a column move. An existing red rule is not
   duplicated: its **range** is re-pointed in place (reported as `range re-pointed`)
   while its colour is left alone, since the red has been re-picked in the UI and
   that choice is the operator's.

5. **Install-colors (maintenance)** — label the two city columns and provenance-color
   the role tabs:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/clean.py install-colors
   ```
   **Overwrites the two header cells** of the adjacent `City` / `Resolved City`
   pair the role-tab array formula emits, relabelling them **`City (Existing)`** /
   **`City (New)`**, and installs three rules just **under** the bright-red error
   rule (so errors keep top priority): **violet** whole-row when
   `Status = "Existing (from MLOps)"`, **amber** on `City (New)` when it has a
   value (a net-new resolved city), and **green** on `City (Existing)` when it
   holds a real city (non-empty, not "Other").

   Every column it touches — the city pair **and** `Status` for the violet rule —
   is located **by header name at run time**, never by a fixed letter. The pair
   started at G/H and moved to H/I when a column was inserted upstream, which made
   the hardcoded version abort every run.

   `install-colors` **validates all three role tabs before writing to any of
   them**, and aborts if a tab has no city pair, more than one, a half-labelled
   one, no `Status` column, or a sheetId missing from the spreadsheet. That covers
   *validation* failures only — a mid-run API error can still leave one tab
   written and the next not, since tabs are written one at a time. Note that
   invoking this via `install-flags` writes the `Issues` columns to all three tabs
   **first**, so an abort there is not "before writing anything".

   Idempotent across a column move: the rules it owns are matched by formula
   *shape* (any column) plus one of its three colors, so a refresh replaces them
   instead of stacking a second set. Four traps that made the "safe to re-run"
   claim false before:

   - colors must be compared with a **±1 tolerance** — Sheets floors the
     float→8-bit conversion, so its own colors read back one unit low. This
     applies to the bright-red rule too, not just the three provenance colors;
   - the bright-red error rule is found by the **`Issues` column it references**
     *and* verified to actually be red — that red has been re-picked in the UI and
     no longer matches the constant, and matching on formula text alone put the
     provenance rules above an operator's own rule on the same column. If it
     cannot be located, the run warns on stderr and installs at index 0 anyway —
     **above** any error highlighting;
   - patterns must accept **any column** — Sheets rewrites stored rule formulas
     when a column is inserted, so a pattern pinned to a letter stops matching
     exactly when it is needed most. But a pattern must still bind one rule to
     one column: the green ones backreference, so a rule painting H while testing
     Z is not recognised as ours;
   - matching a rule is not the same as rebuilding it correctly. Widening the
     violet pattern only fixed *recognition*; `Status` had to be discovered by
     header too, or a rule deleted at `$B2` would be reinstalled at `$A2`.

   `install-flags` now also runs this, so one command does the full setup.

## Procedure

1. **Scan** and show the user the proposed mechanical fixes and the flags, grouped
   and skimmable. Lead with anything that blocks usability (missing/invalid email).
2. **Resolve judgment flags yourself before asking the user to.** For each
   `City="Other"` row, run `cities` first — extraction resolves most of them from
   the free text. For the rows it leaves unresolved, read that person's free-text
   in `Form Responses` (their "Why organize / ties", "Have you helped run events
   before?", LinkedIn, etc.) and infer the real city — e.g. Bangalore, Frankfurt,
   Luxembourg. Write the inferred value into **`Extracted City`** via `apply`
   (`{"row": ..., "header": "Extracted City", "value": ...}`) — the derived
   `Resolved City` (shown in the role tabs as **`City (New)`**) picks it up on
   its own. **Never write `Resolved City` itself**: it is an `ARRAYFORMULA` (see
   the Cities section above) and `apply` refuses it — a literal would `#REF!`
   the whole column. And **never overwrite the submitted `City` dropdown**
   (shown as **`City (Existing)`**) — that's the non-destructive rule. `City
   (New)` holds **only net-new cities** (rows where `City = "Other"`); existing
   form cities stay in `City (Existing)` and must **not** be copied across. A
   row stops being flagged once `Extracted City` fills its `Resolved City`.
   Don't guess with no signal.
3. **Confirm with the user** which fixes to apply. Mechanical fixes are safe to
   batch; city resolutions should be eyeballed since they're inferred.
4. **Build `changes.json`** (rows + header names + new values) and run `apply`.
   Re-run `scan` to confirm the diff shrank and check the `Autofixes` column.
5. Mechanical fixes are idempotent — running scan again after apply should show
   them gone.

## Notes & guardrails

- **Never** edit the role tabs' computed columns; fixes go to `Form Responses`.
- Name re-casing only triggers on clearly all-upper/all-lower input (won't mangle
  "McDonald", "von Neumann"); when unsure it leaves the value alone — verify odd ones.
- Don't sort/insert rows in `Form Responses` (breaks row alignment everywhere).
- Duplicate-email flag surfaces repeat submissions; decide which row wins before
  acting — the engine won't merge or delete rows.
