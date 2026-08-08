---
name: aaif-sync-chapters
description: Push intake decisions out of the Intake Ops sheet — accepted organizers onto the Chapters List, accepted people plus their survey interest into their chapter's Attendee CRM, and per-chapter Drive access to replace the folder's public link-share. Reports & proposes by default; only writes on explicit approval. Use when asked to sync organizers/chapters/CRMs, push intake decisions to the chapters list, add intake people to a chapter's CRM, or give organizers access to their own chapter.
argument-hint: "[chapters|crm|access] [--write]"
---

# Sync the Intake → Chapters List, chapter CRMs, chapter access

Three engines, one intake sheet, same house rules — **the intake sheet is only
ever read**, the report is the default, and `--write` re-verifies itself:

| Engine | Script | Pushes | Into |
|---|---|---|---|
| Chapters feed | `sync_chapters.py` | **accepted organizer names** | the public Chapters List sheet |
| Chapter CRMs | `sync_crm.py` | **accepted people + their survey interest** | each chapter's private `<City> CRM.xlsx` |
| Chapter access | `sync_access.py` | **per-chapter Drive grants** | the Chapters folder's sharing |

Run whichever the user asked for. "Sync everything" means feed → CRM → access, in
that order: a net-new city needs its folder before its CRM can be written, and its
CRM should hold the right people before anyone is granted access to it.

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
  (manual entries live there). Written values keep original UTF-8 (`Médéric Hurier`
  stays accented).
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
  The report says whether the Luma page is live, and **`--write` refuses to create
  a row whose page isn't live** (its CTA would point at a 404) unless you pass
  `--allow-missing-luma`. Page creation is manual, and a net-new city still needs
  its Drive folder/assets: run **`aaif-create-chapter`** for it as the follow-up.
- Duplicate intake rows for the same person+city are deduped (first wins, reported).
  Duplicate **chapter** rows (two rows for one city) are reported too — only the
  last is ever updated, so merge them by hand.
- **The run aborts rather than guessing** when: a header is duplicated (reads and
  writes would resolve to different columns); any written column is missing; a row
  below the last City row is non-empty (new rows are appended there and would wipe
  it); a city has no ASCII characters, so its Luma slug would be empty; intake text
  contains markup or control characters, or exceeds 120 chars; or the sheet changed
  between building the proposal and writing it (row numbers are snapshot indices,
  and the per-city Luma checks sit in that window).

---

# 2. Sync Intake People + Interests → chapter CRMs

Every chapter folder under the **Chapters** Drive folder
(`1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx`) holds one **`<City> CRM.xlsx`** whose
`Attendees` tab is the chapter's private people database. `sync_crm.py` fills it
from the intake and carries each person's survey interest across.

**This CRM is the onboarding list.** It decides who gets access to a chapter's
Drive folder, so a person reaches it after a **decision**, not on submitting the
form. Only `Accepted` and `Existing (from MLOps)` sync; `New`, `Tentative` and
`Denied` are all held back and reported.

> As of 2026-08 that means **organizers only** — the Hosts and Speakers tabs have
> never been triaged off `New` (0 of 26 and 0 of 55). Both start flowing the
> moment someone accepts them; nothing in the engine needs to change for that.

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
   Saves each workbook's pre-edit bytes to a temp `before/` directory, uploads,
   then re-downloads every written workbook and confirms a fresh plan is empty.
   A workbook that fails is reported and the rest still finish — one bad file
   must not abandon the rest.

## Column mapping (intake → CRM `Attendees`)

| CRM column | Filled from |
|---|---|
| `Full name` | role tab `Name` / `Full name` |
| `Trusted/Regular` | `Yes` for an organizer — they're on the team, not a guest to triage |
| `Status` | `Organizer` / `Speaker` / `Host` (everyone syncing is already accepted) |
| `Notes (CRM)` | provenance — `Intake: Organizer · Accepted · 2026-08-07` |
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

- **Chapter resolution per intake row**: the role tab's `Chapter` assignment wins
  (a human made it), then `City (New)`, then `City (Existing)` unless it's an
  `Other…` placeholder. The result is matched to a chapter **folder** with the
  same accent-, case- and punctuation-folded name as the chapters feed uses
  (`Washington, DC` → the `Washington DC` folder; `Montreal` → `Montréal`).
- **Near-miss folders are reported, never written** — same discriminating-token
  rule and generic-word stoplist as the chapters engine, so `San Diego` never
  lands in `San Francisco`. Cities with no folder at all are listed as the
  follow-up queue for **`aaif-create-chapter`**.
- **One row per person per chapter, deduped on email** — the workbook's own Guide
  tab says to merge by email, so that is the key. Someone who applied as both an
  organizer and a speaker gets **one** row: the higher-priority role sets
  `Status`, and both interests are recorded in `What brings you here?`.
- **Never clobber a human.** A CRM cell that already has content is left alone —
  corrected spellings, hand-written notes and manually added companies all
  survive every re-run. Only genuinely blank cells are filled.
- **`Status` is the one exception**: it is upgraded when it still holds a value
  the automation itself wrote (`New`, `Prospect`, `Organizer`, `Speaker`,
  `Host`). That is how a person's role is corrected after re-triage — while a
  human's `Attended`, `Regular`, `Volunteer` or `Declined` is never undone.
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
  template writes `"…"`, older workbooks write `&quot;…&quot;`). A workbook with
  no Status list at all is reported, not guessed at.
- **TemplateCity never receives people** — only the dropdown patch.
- **Rows are skipped, and reported, when**: `Status` is anything other than
  `Accepted` / `Existing (from MLOps)`; the email is missing or unparsable
  (there'd be no dedupe key, so every run would re-add them); or the row has no
  chapter or city at all.
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

`CRM_WRITTEN` (six columns) and `SYNC_STATUSES` (accepted only) are what keep
un-vetted applicants and their survey detail out of a folder shared more widely
than intended. **Re-check the folder's sharing before widening either** — they
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

`--write` runs all three in that order; `--phase pin|grant|lock` runs one, and
verifies whatever it ran. **`lock` refuses to run when any grant failed** —
locking then would leave those organizers with no access at all; `--lock-anyway`
overrides.

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

# Verify

After any run (and after editing the engine):

- The report's intake counts should match a manual count of the sheet's Status
  column; a delta means status strings drifted.
- After `--write`, each engine prints its OWN verify line — they differ:
  `sync_chapters` "a fresh run proposes zero changes"; `sync_crm` "a fresh read
  of every written workbook proposes zero changes"; `sync_access` "banners are
  directly public and Chapters/ is no longer link-shared".
- Spot-check one touched row in the sheet: `Organizers` merged correctly, the
  MLOps and Luma columns untouched, and the version history shows a single edit
  for the whole sync.
- After a CRM `--write`, open one touched workbook and check the person's row
  reads correctly, the sample row and any hand-written notes are untouched, and
  the `Status` cell offers `Host` in its dropdown.
- Unit tests for the pure logic in all three engines (no network, no `gws`):
  ```bash
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_chapters.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_crm.py
  python3 ${CLAUDE_SKILL_DIR}/scripts/test_sync_access.py
  ```

## Notes

- Both tabs are read by **header name** (`Status`, `Full name`, `City (Existing)`,
  `City (New)`, `Run events before?`, `Why organize / ties`, `City`, `Organizers`),
  never by fixed column letter — the script aborts loudly if a header disappears.
  **Writes** are addressed the same way: the chapters tab is read `A:AZ` and every
  write target is derived from the header row's index. Every column a new row
  writes (`Title`, `City`, `Organizers`, `CTA`, `URL for CTA`, `Chapter Luma Link`)
  is resolved up front and aborts if missing — a silently skipped one would publish
  a chapter with no title or a dead CTA. The tab was restructured from
  `City | Organizers | Previous MLOps Organizers | Chapter Luma Link` into the
  11-column website feed it is now; the canonical column list lives in `HEADERS` in
  `scripts/test_sync_chapters.py`, which is executable and therefore can't go stale.
  The old hardcoded `B`/`A:D` writes are why nothing may be addressed by letter again.
  Note `Chapter Luma Link` is **hidden** in the sheet UI — hidden ≠ absent.
- Quote the tab name in any manual A1 ranges (`'Chapters & Teams'!I11`) — it
  contains `&` and spaces.
- Unresolved rows already hand-placed on the chapters list are flagged
  "no action needed" so they don't nag every run.
- `sync_crm.py` imports the `gws` wrapper, city folding and near-miss stoplist
  **from `sync_chapters.py`** rather than copying them. Two copies would drift,
  and a city that folds one way in one engine and another way in the other would
  put a person in a CRM whose feed row says something else.
- The CRM's `Attendees` sheet is resolved through `xl/workbook.xml` and its rels,
  never by guessing `xl/worksheets/sheet1.xml` — sheet order and file numbering
  are independent, and the older workbooks are packed in a different order and
  store their strings in a shared table rather than inline. Both are read; only
  inline strings are ever written.
- Bad LinkedIn values (`https://google.com/url`) and odd city spellings come
  straight from the public form and are copied as-is. Fix them at the source with
  **`aaif-clean-data`**, then re-run — the CRM only fills blanks, so a corrected
  intake value will **not** overwrite the bad one already written. Clean first.
