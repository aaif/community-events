---
name: aaif-sync-chapters
description: Push intake decisions out of the Intake Ops sheet — accepted organizers onto the Chapters List and into each chapter's About doc, intake people plus their survey interest into their chapter's Attendee CRM (accepted people always; organizer candidates only where the chapter is big enough to self-serve), per-chapter Drive access to replace the folder's public link-share, and each chapter's Drive folder and Slack channels onto the Chapters List resource map. Reports & proposes by default; only writes on explicit approval. Use when asked to sync organizers/chapters/CRMs, push intake decisions to the chapters list, update a chapter's About doc organizers, add intake people to a chapter's CRM, give organizers access to their own chapter, or record which Slack channel and Drive folder a chapter uses.
argument-hint: "[chapters|about|crm|access|resources|nightly] [--write]"
---

# Sync the Intake → Chapters List, About docs, chapter CRMs, chapter access

Five engines, one intake sheet, same house rules — **the intake sheet is only
ever read**, the report is the default, and `--write` re-verifies itself:

| Engine | Script | Pushes | Into |
|---|---|---|---|
| Chapters feed | `sync_chapters.py` | **accepted organizer names** | the public Chapters List sheet |
| About docs | `sync_about.py` | **accepted organizer names** | each chapter folder's `About.docx` |
| Chapter CRMs | `sync_crm.py` | **accepted + pipeline people (self-serve policy) + their survey interest** | each chapter's private `<City> CRM.xlsx` |
| Chapter access | `sync_access.py` | **per-chapter Drive grants** | the Chapters folder's sharing |
| Resource map | `sync_resources.py` | **Drive folder + Slack channels** | the Chapters List resource columns |

Run whichever the user asked for. "Sync everything" means feed → about → CRM →
access → resources, in that order: a net-new city needs its folder before its
About doc or CRM can be written, its CRM should hold the right people before
anyone is granted access to it, and the resource map records what exists once it
exists. The feed and the About docs read the same accepted-organizer list, so
they are run together and a chapter's doc agrees with its website row.

`sync_resources.py` is the odd one out — it reads nothing from the intake. It
answers "where does this chapter actually live", from Drive and Slack.

## The recurring pipeline

The whole loop, in dependency order. Every step reports first and writes only on
approval, so this is a sequence of decisions, not a batch job. Skip what has not
changed; never reorder.

| # | Step | Command | Why here |
|---|---|---|---|
| 1 | Resolve cities | `aaif-clean-data` | an unresolved city is invisible to every step below — fix it at the source first |
| 2 | Triage decisions | `aaif-triage-intake` | only `Accepted` / `Existing (from MLOps)` flow onward |
| 3 | Chapters feed | `sync_chapters.py` | a net-new city needs its row before anything can hang off it |
| 4 | About docs | `sync_about.py` | same accepted-organizer list as the feed, so the two agree |
| 5 | Chapter CRMs | `sync_crm.py` | the CRM decides who gets Drive access, so it lands before access does |
| 6 | Drive access | `sync_access.py` | grants come after the CRM holds the right people |
| 7 | Resource map | `sync_resources.py --plan` | records folder + channels; `--plan` names channels that do not exist yet |
| 8 | Create/rename channels | `provision_channels.py` | makes step 7's plan true. **Renames before creates**, in a computed order |
| 9 | Add organizers | `invite_organizers.py` | needs the channels from step 8 to exist |
| 10 | Verify | `aaif-audit-slack` | the independent check: coverage, who is missing, who is in a room we never accepted |

Two ordering constraints that are not negotiable and not obvious:

- **Between 7 and 8 the audit aborts.** The sheet names channels that do not
  resolve yet, and `assert_aliases_resolve()` refuses rather than downgrading
  those chapters to "no channel". Close the gap by running 8, not by skipping
  the check.
- **9 must follow 8, and a merge makes that sharper.** A *renamed* room keeps its
  members; a *merged* one does not — `#southbay-chapter-leads` is retired as
  `-deprecated` and its members only reach `#bay-area-organizers` when step 9
  invites them. Retiring without running step 9 strands them.

A new chapter additionally needs **`aaif-create-chapter`** for its Drive folder
and assets, and its Luma page created by hand, both before step 7 can find them.

## Unattended runs (`nightly.py`)

`nightly.py` runs the five engines in the pipeline order above as subprocesses —
report-only by default, `--write` passes through to every engine. It exists for
a scheduled CI job, and two conventions make that workable:

- **Exit codes are the drift signal.** Every engine now exits `0` when in sync,
  `2` when its report proposes changes (or, for `sync_resources`, when a filled
  channel cell is malformed), and anything else on failure. The runner
  aggregates the same way: `0` all clean, `2` drift somewhere, `1` any failure.
  `sync_access` counts pending grants and a pending lock as drift, but **not**
  pending banner pins — those are a standing human decision and would read as
  drift every night forever.
- **The runner's stdout never names a person.** This repo is public and a CI log
  is a publication, so the summary is engine names, outcomes, durations and log
  paths only. The full reports — which do carry names, emails and per-person
  diffs — go to `nightly-reports/<UTC stamp>/<engine>.log`, which `.gitignore`
  covers and which must never be uploaded as a public artifact. Any print added
  to `nightly.py` must be composed of fixed strings and its own computed values,
  never engine output.

The Slack write steps (8, 9, and `prune_organizers.py`) are deliberately absent:
they carry `--i-have-approval` because they notify or affect real people, and a
scheduled job must never hold that approval. `sync_resources`' Slack half
already degrades to folder-only on a dead token, which is the right unattended
behaviour — and an *involuntary* skip (a dead token, as opposed to `--only
folder`) prints a stdout `PARTIAL:` marker and exits `2`, which the runner
surfaces as its own **PARTIAL** outcome. A half-checked night must never read
green, or a token that dies in CI stays dead forever; recovery costs nothing
because blank cells re-propose on the next authenticated run.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py                # report all five
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py --write        # apply, unattended
python3 ${CLAUDE_SKILL_DIR}/scripts/nightly.py crm resources  # a subset
```

Note `--write` keeps each engine's own refusals: `sync_chapters` still holds
back a new row whose Luma page is not live, and `sync_access --write` runs its
grant/lock sequence (**never** the pin phase — that needs the explicit `--pins`
flag, which `nightly.py` must never pass; pending pins stay named in the
report) — so an unattended write run is the same set of decisions the
interactive flow would have made, minus the pause for approval.
Wiring this into GitHub Actions (auth, secrets, where the digest goes) is a
separate change; the runner is deliberately CI-agnostic.

> **Tooling rule — `gws` + Python only.** Every read, edit, and write of a Drive
> file goes through the `gws` CLI, driven from Python. **Prefer native Google
> formats**: edit `application/vnd.google-apps.*` files with the Docs/Sheets/
> Slides API. Drop to byte-level OOXML surgery on the `.docx`/`.pptx`/`.xlsx`
> zip parts (embedded fonts and untouched parts survive) only when the file
> genuinely is a stored Office file. **Never use LibreOffice / `soffice`** — not to edit, not to convert,
> and not to render a "just checking it locally" preview: it substitutes local
> system fonts for the brand fonts and drops OOXML it doesn't understand, so its
> output and its renders both misrepresent the real file. Same for `unoconv` and
> any desktop office suite. To *see* a file, render it through the API instead —
> a slide via `aaif_events.slides_export.render_slide_png`, a doc via
> `gws drive files copy` to a Google Doc → `gws drive files export` to PDF →
> trash the copy. Never round-trip a native Doc through `.docx` — it strips
> native features like Tabs.

---

# 1. Sync Intake Organizers → Chapters List

Push organizer decisions from the **AAIF Community Intake Ops** sheet
(id `1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o`, tab `Organizers`) into the
**AAIF Community Chapters List** (id `18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg`,
tab `Chapters & Teams`): every organizer whose Status is **`Accepted`** or
**`Existing (from MLOps)`** must appear in their city row's **Organizers** column,
and cities with no row yet get one appended. The intake sheet is only ever **read**;
all writes go to the chapters list. Idempotent — a second run right after a sync
proposes zero changes.

Prereq: the `gws` CLI must be installed and authenticated (see the user's
`gws-cli-access` memory).

## The flow: report → approve → write

1. **Report (default, read-only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_chapters.py
   ```
   Shows per-city adds to existing rows (with the exact new B value), proposed
   new city rows (appended row number + Luma slug + whether the page is live),
   near-miss city names, unresolved-city rows, and deduped duplicates. Ends with
   a "No changes needed" line when the sheets are already in sync.

2. **Show the user the proposal** and get explicit approval. Never skip to write.

3. **Write (on approval only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_chapters.py --write
   ```
   Recomputes the proposal from a **fresh read** (a stale proposal is never
   applied), applies everything in **one** `values batchUpdate` (a partial
   failure can't half-sync the sheet), then re-reads and verifies a fresh run
   proposes zero changes (exits non-zero otherwise).

## Sync rules (what the engine does)

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
  The report says whether the Luma page is live, and **`--write` holds back a
  row whose page isn't live** (its CTA would point at a 404) unless you pass
  `--allow-missing-luma` — the adds and the live rows still land, the held
  cities are named in the output and re-propose on every run (the write exits
  `2`, the shared drift code, until their pages exist). Held rows never leave a
  blank row in the feed: the written rows are renumbered onto consecutive rows.
  Page creation is manual, and a net-new city still needs
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

---

# 1b. Sync accepted organizers → chapter About docs

Every chapter folder holds one **`About.docx`** whose **Organizers** section is a
bulleted list of names. `sync_about.py` rewrites that list from the same accepted
organizers the feed gets, so a chapter's doc names its OWN organizers.

It had never named them. Every About doc was cloned from **TemplateCity**, which
is itself a copy of the San Francisco doc, so **79 of 80 chapters shipped listing
the same four people** (`TEMPLATE_NAMES` in `sync_about.py` — the publicly listed
San Francisco organizers) — correct for San Francisco, wrong everywhere else.

## The flow: report → approve → write

1. **Report (default, read-only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_about.py
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_about.py --city Melbourne
   ```
   Prints a per-chapter `-` / `+` diff of the Organizers list, the chapters
   already correct, the removals that need a second look (below), near-miss and
   folder-less cities, and a "No changes needed" line when everything matches.
   A full run downloads every chapter doc and takes about a minute.

2. **Show the user the proposal** and get explicit approval. Never skip to write.

3. **Write (on approval only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_about.py --write
   ```
   Recomputes from a fresh read, re-downloads each doc right before its upload
   and **skips it if it changed since the plan was built** (a human edit in the
   approval window is never silently reverted; the skip is loud and the doc
   re-proposes next run), uploads the rest, then re-downloads **every** written
   doc and confirms a fresh plan is empty. One failed upload is reported and
   the rest still finish.

## The section is rewritten wholesale — and that is the point

There is deliberately **no "this list looks hand-edited, leave it alone" branch**,
which is the opposite of the CRM engine's never-clobber-a-human rule. The one
hand-edited list in the estate is why:

> **Melbourne's About doc grouped applicants under `Approved` / `Submitted
> Application` / `Planning Application`** — publishing, in a doc shared with the
> chapter, that two named people had applied and had **not** been approved (one
> still `New` on the intake — the pre-2026-08-22 spelling of `Prospect`; this is
> a record of what the doc said at the time, not current vocabulary — one not on
> it at all). Skipping a hand-edited list
> preserves that disclosure. So anything in the section that is not an accepted
> organizer comes out.

The safety valve is the **report plus the approval gate**, not a skip. Removals
are itemised in two classes, and both must be read before approving:

- **Non-accepted applicants** — someone this chapter's intake knows who is not
  `Accepted` / `Existing (from MLOps)`. This is the disclosure class; it gets its
  own section in the report.
- **Lines the intake cannot account for** — the interleaved sub-headings, and any
  organizer kept off the intake entirely. Removing a real person's name because
  "the intake has never heard of them" is the operator's call at the gate, never
  the script's in silence. If they belong, add them to the intake and re-run.

## Sync rules (what the engine does)

- **Source of truth is the intake**, filtered to `Accepted` and
  `Existing (from MLOps)`, with the city resolved through `resolve_city()`
  **imported from `sync_chapters.py`** — a row that resolved to one city on the
  feed and another here would put an organizer on one city's website row and in a
  different city's doc. Names are written in intake row order.
- **A chapter with no accepted organizer gets the placeholder `[Organizer name]`**,
  not an empty section: a heading with nothing under it reads as a broken doc, and
  the block keeps a bullet to clone from when the chapter's first organizer lands.
  As of 2026-08-11, 28 chapters are in this state. **TemplateCity gets the placeholder too** —
  otherwise every chapter created from it re-inherits the four wrong names, the
  same reason `sync_crm` patches the template's Status dropdown.
- **A name already in the list is reused verbatim**, so a hyperlinked name (the
  template links Rahul's) and any hand formatting survive a re-run. Only the
  trailing spacing is normalised, so the last bullet keeps its bottom margin.
- **Near-miss cities are reported, never written** — same folding and the same
  generic-word stoplist as the other engines, so `San Diego` never lands in
  `San Francisco`. Cities with no folder are the `aaif-create-chapter` queue.
- **A doc with no `Organizers` heading is skipped with a reason**, never guessed
  at. The heading is matched on its **text**, not its style, because two of these
  docs have been round-tripped through desktop Word — a restyled heading must not
  make a chapter silently unsyncable.
- **A chapter whose intake lost a row to the malformed-text filter is held back
  wholesale** — the doc is neither planned nor written (report and `--write`
  alike), the hold is named in the output beside the malformed rows, and the run
  exits non-zero until the intake row is fixed. The rewrite is wholesale, so
  proceeding would have deleted that accepted organizer from the doc as a side
  effect of a data bug — the one removal class the report could never itemise.

## Editing the docs

The About docs are **stored `.docx`**, not native Google Docs, so they are edited
as OOXML — and specifically by **byte-level surgery on `word/document.xml`**, not
through ElementTree. Re-serializing the part reorders every namespace declaration
on `<w:document>` and rewrites markup the engine never meant to touch; splicing
one paragraph range leaves the rest of the part, and every other zip member
(the embedded brand fonts among them, stored uncompressed), byte-identical.

- Paragraphs are found by **depth-counted** `<w:p>` scanning. A flat
  `<w:p>.*?</w:p>` match ends at the first inner `</w:p>` of a textbox paragraph
  and splices the document in half.
- The block runs from the heading to the **first non-list paragraph** — the
  `Luma & Socials` heading in every chapter doc.
- New bullets are cloned from an existing one so they inherit its `<w:pPr>`, and
  `w14:paraId` is stripped from the clone (it is meant to be unique per
  paragraph). When the block has no bullet left to clone, `MODEL_BULLET` rebuilds
  it — and it carries **`numId 1`**, the Organizers list. The `Luma & Socials`
  list below it is `numId 4`, so cloning the nearest bullet renumbers the list.

---

# 2. Sync Intake People + Interests → chapter CRMs

Every chapter folder under the **Chapters** Drive folder
(`1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx`) holds one **`<City> CRM.xlsx`** whose
`Attendees` tab is the chapter's private people database. `sync_crm.py` fills it
from the intake and carries each person's survey interest across.

**Who syncs — the self-serve organizer policy (2026-08).**

- `Accepted` and `Existing (from MLOps)` people always sync, all three role tabs.
- **Hosts and speakers still in the pipeline** (`Prospect` — including its
  legacy spelling `New`, retired 2026-08-22 by `migrate_status_prospect.py` — /
  blank / `In progress` / `Tentative` / `Interviewing`) sync too, so a chapter
  sees its candidate venues and talks without waiting on central triage.
- **Organizers still in the pipeline** sync **only into a self-serve chapter** —
  one with **4+ accepted organizers** (`SELF_SERVE_MIN`), not counting AAIF ops
  people (`AAIF_OPS_NAMES`: Rahul, Demetrios, Ijeoma). Those chapters run their
  own interviews and grow their own team; below the threshold, organizer
  approval stays with AAIF ops and the candidates are held back and reported
  ("Held" line; `--verbose` names them).
- `Denied` / `Inactive` / `Duplicate` never sync. Both status sets are
  **allowlists** in `sync_crm.py` (`SYNC_STATUSES`, `PIPELINE_STATUSES`), so a
  new dropdown value syncs nobody until it's placed — fail closed.

A pipeline person lands as `Prospect` (organizers) or their role status
(hosts/speakers), never `Trusted/Regular`. Acceptance upgrades the row in place
on a later run. **Drive access still keys off acceptance** — `sync_access.py`
reads the intake with the accepted-only default, so a Prospect in the CRM gets
no folder grant. One merge refusal (the form is public and email is the merge
key): a **not-yet-accepted row never merges into a person whose rows are all
accepted** — it is reported under the not-synced count ("SECURITY" line;
`--verbose` names it) for a human to review, so a stranger submitting under an
accepted organizer's address cannot write into that person's CRM row. One gap to know about: a Prospect whose intake row is later
`Denied` stays in the CRM (the engine never deletes people) and shows up under
"Already in a CRM and NOT touched" — remove that row by hand.

**Keep it minimal.** Only six of the eleven columns are ever written. `Signal`,
`LinkedIn URL`, `Company`, `Role / title` and `Technical expertise` still exist
for an organizer to fill in by hand — the automation just doesn't push a survey's
worth of personal detail into a folder that is still link-readable while chapters
are being onboarded (see the sharing note at the end of this section).

## The flow: report → approve → write

1. **Report (default, read-only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_crm.py            # all chapters
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_crm.py --city Boston
   ```
   Opens every chapter workbook and prints, per chapter, the people it would add
   (`+`) or fill in (`~`) with the exact cell values, then the near-miss cities,
   the cities with no chapter folder, and a count of un-synced intake rows
   (`--verbose` lists them individually). A full run reads every chapter workbook and
   takes a few minutes.

2. **Show the user the proposal** and get explicit approval. Never skip to write.

3. **Write (on approval only):**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_crm.py --write
   ```
   Saves each workbook's pre-edit bytes to a temp `before/` directory (the
   output names the path; report-only runs delete their downloads on exit
   instead — the workbooks are full of real people), compares that fresh
   download against the bytes the plan was built on and **skips any workbook
   that changed in the window** (planning takes minutes, and a human edit must
   never be silently reverted — the skip is loud, exits non-zero, and the
   workbook re-proposes next run), uploads the rest, then re-downloads every
   written workbook and confirms a fresh plan is empty. A workbook that fails
   is reported and the rest still finish — one bad file must not abandon the
   rest.

## Column mapping (intake → CRM `Attendees`)

| CRM column | Filled from |
|---|---|
| `Full name` | role tab `Name` / `Full name` |
| `Trusted/Regular` | `Yes` for an **accepted** organizer — they're on the team, not a guest to triage |
| `Status` | the accepted role (`Organizer` / `Speaker` / `Host`); a pipeline organizer is `Prospect`, a pipeline host/speaker keeps the role status |
| `Notes (CRM)` | provenance — `Intake: Organizer · Accepted · 2026-08-07` (every merged role and status, in priority order) |
| `Email` | role tab `Email` — **also the dedupe key** |
| `What brings you here?` | the survey answer **verbatim**, plus the role's detail (`Talk title` / `Venue name` / `Chapter / city wanted`) |

**Never written:** `Signal`, `LinkedIn URL`, `Company`, `Role / title`,
`Technical expertise`. They are absent from the mapping rather than written
blank, so the automation cannot touch them even on a row it creates. The
canonical list is `CRM_WRITTEN` in `sync_crm.py`, asserted by the tests.

`What brings you here?` is the form's routing question, and the role tabs are
filtered views that drop it — so it is read from `Form Responses` and joined back
on email. When a row can't be joined, the form's own wording for that branch is
used instead of inventing one.

## Sync rules (what the engine does)

- **Chapter resolution per intake row**: the role tab's `Chapter` column wins,
  then `City (New)`, then `City (Existing)` unless it's an `Other…` placeholder.
  The result is matched to a chapter **folder** with the same accent-, case- and
  punctuation-folded name as the chapters feed uses (`Washington, DC` → the
  `Washington DC` folder; `Montreal` → `Montréal`).

  > **`Chapter` is a formula, not a human assignment** — this said "a human made
  > it" until 2026-08-10, and `sync_crm` was built around that. It is an
  > `ARRAYFORMULA` on all three role tabs (`Organizers!P2`, `Speakers!V`,
  > `Hosts!AA`), filled on 223/228, 56/58 and 26/26 rows, resolving `City (New)`
  > → `City (Existing)` unless `Other…` → **the form's free-text city**. That last
  > fallback is one step more than `resolve_city()` performs, so an accepted person
  > whose only city signal is the free text would land in a CRM here while the
  > chapters feed and the About docs still count them unresolved. Measured across
  > all 103 accepted rows on 2026-08-10 the two agree **0 disagreements** — but the
  > gap is real, and closing it is the point of collapsing these columns into one.
- **Near-miss folders are reported, never written** — same discriminating-token
  rule and generic-word stoplist as the chapters engine, so `San Diego` never
  lands in `San Francisco`. Cities with no folder at all are listed as the
  follow-up queue for **`aaif-create-chapter`**.
- **One row per person per chapter, deduped on email** — the workbook's own Guide
  tab says to merge by email, so that is the key. Someone who applied as both an
  organizer and a speaker gets **one** row: the higher-priority role sets
  `Status`, and both interests are recorded in `What brings you here?`.
- **A changed email is a two-place edit — always do both.** Email is the CRM's
  dedupe key *and* the address Drive grants are issued to, and the engine only
  ever fills blanks. So correcting an address on the intake row alone leaves the
  CRM holding the old one, and the next run reads the new address as a person it
  has never seen and **adds a second row**. Whenever an email changes: update the
  Drive grant (revoke the old address if it holds one, grant the new) **and**
  overwrite that person's `Email` cell in their chapter CRM. Neither engine does
  this for you — `sync_access` never revokes, `sync_crm` never overwrites.
- **Never clobber a human.** A CRM cell that already has content is left alone —
  corrected spellings, hand-written notes and manually added companies all
  survive every re-run. Only genuinely blank cells are filled.
- **`Status` is the one exception**: it is upgraded when it still holds a value
  the automation itself wrote (`Prospect` — or its legacy spelling `New` —
  `Organizer`, `Speaker`, `Host`). That is how a person's role is corrected
  after re-triage — while a human's `Attended`, `Regular`, `Volunteer` or
  `Declined` is never undone.
- **Fixture rows are cleared, and only fixture rows.** A row is wiped **only** if
  its `Email` is at a reserved example domain (`@example.com`, `.org`, `.net`,
  `.edu`) — that is the sole gate, deliberately narrow and anchored on the `@` so
  look-alikes (`a@examples.com`, `x@example.company`) are never caught. This
  removes the `Sam Taylor` sample the template puts in **every** chapter CRM, and
  the Tatooine test chapter's cast. The freed row is **reused**, so a chapter's
  first real organizer lands at the top of the list instead of below a blank.
  Anything with a real-looking address is left exactly where it is and listed
  under "Already in a CRM and NOT touched" — a row a human typed is
  indistinguishable from one we don't recognise, so it is never guessed at.
  Clearing is planned for **every** chapter, including those that gain nobody.
- **Rows land in the workbook's pre-created empty rows** (the template ships 1000
  of them, already carrying the dropdowns and conditional formatting), lowest
  first, copying row 2's per-column cell styles so a synced person looks like a
  hand-entered one. Past row 1000 new `<row>` elements are inserted in ascending
  order.
- **The `Status` dropdown gains a `Host` value.** It shipped as
  `New,Prospect,Attended,Regular,Speaker,Organizer,Volunteer,Declined` with no
  value for a venue host, so hosts had nowhere honest to land. Every workbook the
  script opens is patched — including **TemplateCity**, or every chapter cloned
  from it would re-inherit the gap. Both quote encodings are handled (the
  template writes `"…"`, older workbooks write `&quot;…&quot;`), and so are the
  post-migration lists without the legacy `New` (removed by
  `migrate_status_prospect.py`; the unrelated `New` on the **Signal** list is
  untouched by that migration). A workbook with
  no Status list at all is reported, not guessed at.
- **TemplateCity never receives people** — only the dropdown patch.
- **Rows are skipped, and reported, when**: `Status` is neither a decided-yes
  (`Accepted` / `Existing (from MLOps)`) nor a recognised pipeline status —
  `Denied` / `Inactive` / `Duplicate` and any dropdown value the allowlists do
  not name fail closed; the row is a pipeline **organizer** for a chapter below
  the self-serve threshold (held back under central approval — see "Who syncs"
  above); the email is missing or unparsable (there'd be no dedupe key, so
  every run would re-add them); the row's name or city fails the same
  `bad_public_text` check the feed engine runs (markup, control characters,
  absurd length) — enforced here directly, since the role tabs are read
  without passing through `sync_chapters.read_intake`, so a flagged value
  really does reach no cell, no About doc and no CRM; or the row has no
  chapter or city at all. Pipeline hosts and speakers are never skipped for
  their status alone.
- **The run aborts** when `Form Responses` or a role tab comes back empty, or a
  role tab has no `Status`/`Email` header. A workbook whose `Attendees` tab is
  missing a column is **skipped with a reason**, never written by column letter.

## Editing the workbooks

The CRMs are **stored `.xlsx`**, not native Sheets, so they are edited as OOXML
zip parts: download → rewrite `Attendees`' sheet XML → upload. Every part the
script doesn't touch is repacked byte-for-byte, and values are written as
**inline strings**, which Excel and Sheets both treat as literal text — a name
starting with `=` can never become a formula, so there is no RAW-vs-`USER_ENTERED`
hazard here.

**Write order is load-bearing.** `Attendees.serialize()` rewrites the sheet part
wholesale from the element tree, so a bytes-level dropdown patch applied *before*
it is silently discarded — and the run still reports the patch as applied. That
is why row writes, serialization and the dropdown patch all live inside
`finalize()`, in that order; call it, don't re-implement it.

## Sharing: minimal by design

`CRM_WRITTEN` (six columns) plus the status allowlists (`SYNC_STATUSES`,
`PIPELINE_STATUSES` and the `SELF_SERVE_MIN` gate on pipeline organizers) are
what keep declined applicants and everyone's survey detail out of a folder
shared more widely than intended — and since 2026-08 a *vetting-in-progress*
person can legitimately appear in a self-serve chapter's CRM as a `Prospect`.
**Re-check the folder's sharing before widening any of them** — they
are the whole mitigation, and the CRM is written *before* access is narrowed
(feed → CRM → access), so the write lands under whatever sharing exists at the
time.

> **State as of 2026-08-07:** the Chapters folder previously carried
> `anyone → reader`, inherited all the way down to every `CRM.xlsx`. That share
> has been removed (§3), the `linuxfoundation.org → commenter` grant was kept
> deliberately, and each accepted organizer now holds `writer` on their own
> chapter folder only. Confirm with `sync_access.py` (report mode) rather than
> trusting this paragraph.

---

# 3. Per-chapter access (`sync_access.py`)

Moves the Chapters folder off its public link-share and onto per-chapter grants.
Report-only by default. Three phases, and **the order is not negotiable**:

Numbered as the console prints them:

1. **pin** — give each banner its own `anyone:reader`. Usually a **no-op** (see
   below), and not needed for the website; it exists for the case where
   something public genuinely does live in the tree.
2. **grant** — give each accepted organizer **`writer`** (the `--role` default)
   on their chapter folder. Must precede the lock: the public link is currently
   their only access.
3. **lock** — remove `anyone:reader` from Chapters/.

`--write` runs **grant then lock**. The pin phase runs only behind the explicit
**`--pins`** flag (`--write --pins` runs all three, pin first) or as
`--phase pin` — publishing a file to the whole internet is a standing human
decision, never a side effect of syncing grants, and `nightly.py` must never
pass `--pins`. Unpinned banners stay named in every report and write run so the
pending decision is visible. `--phase pin|grant|lock` runs one phase, and the
run verifies whatever it ran; the final `Verified:` line claims **only** the
phases that actually ran and checked something. **`lock` refuses to run when
any grant failed** — locking then would leave those organizers with no access
at all; `--lock-anyway` overrides.

> **The website does not depend on this share — verified, not assumed.** The
> chapters feed's `Image` column is full of `lh3.googleusercontent.com/d/<id>`
> URLs pointing at `Web Banner.png` files inside chapter folders, which reads as
> "80 public Drive images the site serves". It isn't. `aaif.io/community-chapters`
> was loaded and inspected on 2026-08-07: 26 images, **all** from `cdn.sanity.io`,
> none from Drive. Chapter content and imagery live in Sanity; the Drive banners
> are source assets. A column full of image URLs is not evidence that anything
> fetches them — load the page and look.

> **`pin` is a no-op while the parent is still shared.** Drive merges a child's
> `anyone:reader` into the inherited one, returns `200` with a permission id, and
> stores nothing new — the file still reads `inherited: true`. A child can only
> hold its own public share *after* the parent's is removed. Never trust the
> phase's "N changes" line; re-read the permission and check
> `permissionDetails[].inherited`.

- **`assert_all_accepted()` is the last gate before write** and re-reads the
  intake through a different code path than the filter that built the plan.
  "The filter that made the list says the list is fine" is not a check.
- **A bad address never abandons the run.** The intake is fed by a public form,
  so a typo'd address is normal input and Drive rejects it with a hard 400.
  Failures are collected and reported; every other grant still lands.
- **Addresses with no Google account** are refused by Drive unless it may email
  the person. There is no silent path, so they are skipped and reported unless
  `--mail-if-required` (or `--notify`) is passed — sending mail to real people is
  never a side effect of a sync.
- Notifications are **off** by default: a share-mail per organizer, arriving
  unannounced and all at once, reads as a phishing wave.
- `linuxfoundation.org` domain access is **kept** — that is LF staff reach, a
  separate decision from de-publicising the folder.
- Pre-existing direct grants on chapter folders are left alone, and every one
  held by someone the intake does not know about is **listed** in the report.
  They survive the lock — a denied ex-organizer keeps `writer` until a human
  removes it — so they are exactly what an audit needs to see.

---

# 4. The chapter resource map (`sync_resources.py`)

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

## `Organizer Handles` is the one column that is rewritten

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

## Blank vs `none` — the distinction the whole engine turns on

- **Blank** = nobody has looked yet. Every run proposes for it again, and the
  audit's matcher falls through to its prefix/suffix scan.
- **`none`** = a human checked and there genuinely is no such channel. Proposals
  stop, and the audit stops guessing.

This is the sheet's stand-in for the JSON `null` that `channel_map.json` used to
carry. Collapsing the two would either re-ask a settled question on every run
forever, or freeze every row nobody has filled in yet.

## Only exact matches are ever written

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

## The flow: report → approve → write

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_resources.py                # report
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_resources.py --only folder  # no Slack auth needed
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_resources.py --city Boston
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_resources.py --write
```

`--write` recomputes from a fresh read and **drops any proposal whose cell changed
while the report was being read** — a human typing the right answer during the
approval window wins. It then re-reads every written cell and verifies.

## `--plan`: naming channels that do not exist yet

By default the sheet means *the channel that exists*. `--plan` fills blank cells
with the **convention** name — `<city>`, `<city>-organizers`, `<country>` — even
where nothing of that name exists, turning those cells into a build list for
`provision_channels.py`.

That is a deliberate, temporary state with a real cost: **the organizer audit
aborts** while the sheet names channels that do not resolve. That abort is not a
bug to route around — it is the check that stops a chapter being silently
downgraded to "no channel". So only run `--plan` if the channels really are about
to be created.

Naming, and the one rule that is not obvious:

- **The organizer channel follows the chapter's OWN channel, not the city slug.**
  SF's room is `#bay-area`, so its organizers belong in `#bay-area-organizers` —
  `san-francisco-organizers` would name a room after a chapter that, in Slack,
  does not go by that name.
- **Every chapter gets one**, including those with no accepted organizer yet (26 as of 2026-08-11), so
  the room is ready for a chapter's first organizer rather than something someone
  has to remember to create.
- A **filled cell is never re-planned.** That is what protects `#españa` and the
  deliberate multi-chapter room `#bay-area` (SF + Silicon Valley), which the
  `<city>` convention cannot express at all.

## Why some channel names are still not `<city>`, and why that is correct

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
never mistaken for "nothing to do". Set `$AAIF_SLACK_WRITE_TOKEN` (env var, or
`.env` in the working directory) and re-run for the rest — the Slack CLI
credential expired for good in 2026-08 and is only the last-resort fallback.

One check runs even without Slack: a **filled channel cell that cannot possibly
name a channel** (whitespace, `/`, `:`, `#`, `@` or `,` in it — a pasted URL,
an email address, a sentence) is reported as malformed and counts as drift.
"Filled" otherwise reads as healthy forever: Montréal's `Slack Channel` cell
held a copy of its Drive folder URL and every count said the chapter was mapped.
The character list is deliberately minimal so the accented `#españa` is never
flagged.

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

## Creating the planned channels (`provision_channels.py`)

The only script in the repo that **writes to Slack**. It reads the sheet, creates
what the plan names and applies `RENAMES`.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/provision_channels.py               # report
python3 ${CLAUDE_SKILL_DIR}/scripts/provision_channels.py --write --i-have-approval
```

It does **not** share the audit's Slack client. `lib/aaif_events/slack.py` refuses
any method outside `ALLOWED_METHODS`, and that refusal is why a typo in a
30k-member workspace cannot post, invite or archive — widening it for one script
that runs once would remove the guarantee from every audit. So this file carries
its own small write client, with its own allowlist.

What it will never do: **delete** anything, **invite** anyone
(`Organizer Handles` says who belongs where; a human does the inviting, because a
script mass-inviting 100 people to 100 channels is indistinguishable from an
attack and cannot be undone), or create a channel the sheet does not name.

It **can archive — but only rooms a rename already retired** (authorised
2026-08-17). The deprecated-room sweep closes `*-deprecated` rooms only: public
ones get a farewell pointer post first (no post → no archive), private ones are
archived only once every member is already in the recorded successor room, and
a `-deprecated` room queued for a rename back into service is skipped. A room
with no recorded successor is reported, never archived.

Renames run **before** creates — creating `#bengaluru` first would take the name
and strand `#bangalore`'s members in the old room.

Two prerequisites, neither in place by default: a token with `channels:write`,
`groups:write` and `chat:write` in `$AAIF_SLACK_WRITE_TOKEN` (user-token scopes
for create/rename and the farewell pointer; `channels:join` is worth adding so
the sweep can join a public room before posting in it), and `--i-have-approval`
alongside `--write`. Since 2026-08-22 the **read-only report also prefers
`$AAIF_SLACK_WRITE_TOKEN`** — the Slack CLI credential expired for good and
cannot be re-scoped, so without the env var the run falls back to a dead
credential and says so on stderr.

## Adding organizers to their channel (`invite_organizers.py`)

Answers "who is missing from their organizer channel", and behind a second gate,
adds them.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py            # who is missing
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py --city Berlin
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py --write --i-have-approval
```

**Identity comes from the intake, not from the handles column.**
`Organizer Handles` is the human-readable mirror; the authoritative chain is
intake row → email → `users.lookupByEmail` → user id, the same chain that filled
the column. A Slack handle is a display name a person can change, so resolving
`@someone` back to an account would break the day they rename themselves — and an
organizer would quietly stop being invited to their own chapter's room.

Scope is deliberately narrow:

- **Organizer channels only.** The public chapter channel is for people to join
  when they choose; being an organizer is not consent to be placed in a public
  room.
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

## Removing non-organizers (`prune_organizers.py`)

The gated counterpart to "adds, never removes": the ONLY script that removes
people, and only from organizer channels. Driven by an explicit keep-list (tab
`Organizer Keeplist`; prefer Slack user IDs over @handles — a handle is
self-service and the sheet is world-readable), never by a heuristic: someone
whose display name matches an intake row lands in a REVIEW bucket that is
reported and never touched. Report-only by default; `--write --i-have-approval`
acts on the REMOVE bucket alone, and it refuses entirely while no keep-list tab
exists. The script's own docstring carries the full design.

---

# Verify

After any run (and after editing the engine):

- The report's intake counts should match a manual count of the sheet's Status
  column; a delta means status strings drifted.
- After `--write`, each engine prints its OWN verify line — they differ:
  `sync_chapters` "a fresh run proposes zero changes"; `sync_about` "a fresh read
  of every written doc proposes zero changes"; `sync_crm` "a fresh read
  of every written workbook proposes zero changes"; `sync_access` a composed
  line naming only the phases that ran (e.g. "every accepted organizer holds
  their grant; Chapters/ is not link-shared" — the banner claim appears only
  after a pin run); `sync_resources` "a fresh read of every written cell
  matches the proposal".
- Spot-check one touched row in the sheet: `Organizers` merged correctly, the
  MLOps and Luma columns untouched, and the version history shows a single edit
  for the whole sync.
- After a CRM `--write`, open one touched workbook and check the person's row
  reads correctly, the sample row and any hand-written notes are untouched, and
  the `Status` cell offers `Host` in its dropdown.
- After an About `--write`, open one rewritten doc and check the Organizers list
  reads as a proper bulleted list (not renumbered off the Luma list below it),
  the rest of the doc is untouched, and the brand fonts still render.
- After a resources `--write`, open the sheet and check the folder URL opens that
  chapter's folder, and that no cell someone had already filled was overwritten.
- Report-mode exit codes are part of the contract now: `0` in sync, `2` drift,
  else failure (see the `nightly.py` section) — a wrapper that treats `2` as an
  error will misread every report that proposes anything.
- Unit tests for the pure logic in the five engines and the one-shot migration
  (no network, no `gws`):
  ```bash
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_chapters.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_about.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_crm.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_access.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_resources.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_invite_organizers.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_prune_organizers.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_nightly.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_migrate_status_prospect.py
  ```

## One-shot: retiring the status `New` (`migrate_status_prospect.py`)

Renames the intake status `New` → `Prospect` everywhere it is *stored*. Ran
2026-08-21/22; kept because it is the tool that proves the estate is still
consistent, and the pattern for the next status rename.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_status_prospect.py             # report, writes nothing
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_status_prospect.py --city Boston
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_status_prospect.py --write     # apply, then verify
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
- Pre-edit workbook copies are kept under a `before/` directory (printed at the
  end) after a `--write`; the working copies are deleted, since they hold the
  same member PII.

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
