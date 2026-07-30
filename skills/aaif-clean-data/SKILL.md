---
name: aaif-clean-data
description: Normalize and fix data quality in the AAIF Community Intake Ops sheet — canonicalize LinkedIn URLs, fix name/city casing & whitespace, resolve City="Other", flag bad/missing emails and duplicates, and surface broken rows in bright red. Reports & proposes by default; only writes on explicit approval. Use when asked to clean up / normalize / fix the intake data.
argument-hint: "[scan|apply|flags]"
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

3. **Install-flags (maintenance)** — add/refresh the live error flag:
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

4. **Install-colors (maintenance)** — label the two city columns and provenance-color
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
   `City="Other"` row, read that person's free-text in `Form Responses` (their
   "Why organize / ties", "Have you helped run events before?", LinkedIn, etc.) and
   infer the real city — e.g. Bangalore, Frankfurt, Luxembourg. Write the inferred
   value into the **`Resolved City`** source column (found by header name — shown in
   the role tabs as **`City (New)`**), **never overwrite the submitted `City` dropdown**
   (shown as **`City (Existing)`**) — that's the non-destructive rule. `City (New)`
   holds **only net-new cities** (rows where `City = "Other"`); existing form cities
   stay in `City (Existing)` and must **not** be copied across. A row stops being
   flagged once `Resolved City` is filled. Don't guess with no signal.
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
