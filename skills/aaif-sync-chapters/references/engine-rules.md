# Engine rules — what each sync engine does, rule by rule

> Load this when an engine's report is surprising: a row skipped, a city reported as a
> near-miss, a value not written, or a run that aborted. Each section is that engine's
> complete decision table. Also load it before editing the engine.

## 1. Chapters feed (`sync_chapters.py`)


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


## 1b. About docs (`sync_about.py`)

Every chapter folder holds one **`About.docx`** whose **Organizers** section is a
bulleted list of names. `sync_about.py` rewrites that list from the same accepted
organizers the feed gets, so a chapter's doc names its OWN organizers.

It had never named them. Every About doc was cloned from **TemplateCity**, which
is itself a copy of the San Francisco doc, so **79 of 80 chapters shipped listing
the same four people** (`TEMPLATE_NAMES` in `sync_about.py` — the publicly listed
San Francisco organizers) — correct for San Francisco, wrong everywhere else.

**The report prints:** a per-chapter `-`/`+` diff of the Organizers list, the chapters
already correct, the removals needing a second look, and near-miss / folder-less cities.
A full run downloads every chapter doc and takes about a minute.

### The section is rewritten wholesale — and that is the point

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

### Rules

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


## 2. Chapter CRMs (`sync_crm.py`)

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

**`Signal` is the one column the automation never writes.** Eleven of the twelve
are, including the four survey-detail columns — see the column mapping below for
why that changed on 2026-08-25. `Signal` is the chapter's own private rating of a
person, which no form answer can supply, so it stays hand-entered.

**The report prints:** per chapter, the people it would add (`+`) or fill in (`~`) with
the exact cell values, then near-miss cities, cities with no chapter folder, and a count
of un-synced intake rows (`--verbose` lists them). A full run takes a few minutes.
`--write` first saves each workbook's pre-edit bytes to `<repo>/backups/crm-before-<UTC stamp>/`
(gitignored via `**/backups/*`, created `0700`; the engine aborts if this checkout does not
ignore it). The temp working directory — downloads, re-reads, verify copies, all full of
real people — is deleted on exit in **both** modes; only that backup set survives.
**Retention is on you** — nothing prunes them, so delete the directory once the write is
confirmed good. `--write` then compares a fresh download against the bytes the plan was
built on and **skips any workbook that changed in the window** (loud, exits non-zero, and
the workbook re-proposes next run). A workbook that fails is reported and the rest still
finish — one bad file must not abandon the rest.

### Column mapping (intake → CRM `Attendees`)

| CRM column | Filled from |
|---|---|
| `Full name` | role tab `Name` / `Full name` |
| `Trusted/Regular` | `Yes` for an **accepted** organizer — they're on the team, not a guest to triage |
| `Status` | the **decision**, mirrored from the intake: `Prospect` / `In progress` / `Interviewing` / `Tentative` / `Accepted` |
| `Interested in` | what they **applied for**: `Organizer` / `Speaker` / `Host`, `/`-joined for someone who asked for more than one |
| `Notes (CRM)` | provenance — `Intake: Organizer · Accepted · 2026-08-07` (every merged role and status, in priority order) |
| `Email` | role tab `Email` — **also the dedupe key** |
| `LinkedIn URL` | role tab `LinkedIn` |
| `Company` | speakers `Affiliation`, hosts `Company` — organizers are not asked |
| `Role / title` | speakers `Headline` — organizers and hosts are not asked |
| `Technical expertise` | organizers `Technical expertise`, speakers `Areas of expertise`, hosts `Industry` |
| `What brings you here?` | the survey answer **verbatim**, plus the role's detail (`Talk title` / `Venue name` / `Chapter / city wanted`) |

#### `Status` and `Interested in` are two different questions

Split apart on **2026-08-25**. Before that there was one `Status` column and the
engine wrote the **role** into it, so a venue host whose intake row still said
`Prospect` appeared in their chapter's CRM as a flat `Host` — an organizer
scanning the list saw settled people where triage had settled nothing. Pipeline
*organizers* were the one role spared (special-cased to `Prospect`), which is
what made it easy to miss. `Notes (CRM)` had carried both facts correctly all
along.

- `Interested in` is a fact about the **application** and a decision never
  changes it. Accepted in **any** role makes `Status` = `Accepted`; otherwise
  `Status` is the intake's own pipeline value, verbatim.
- No role word can reach `Status` and none is on its dropdown any more —
  asserted by the tests, from `CRM_ROLE` / `CRM_LIFECYCLE` in `sync_crm.py`.
- A workbook last touched before the split has no `Interested in` column, and
  `sync_crm` **refuses to open it** rather than writing by column letter. Run
  `migrations/migrate_interested_in.py --write` first (see
  `references/completed-migrations.md`).

**Never written:** `Signal` — the chapter's own private judgement of a person,
which no form answer can supply. It is absent from the mapping rather than
written blank, so the automation cannot touch it even on a row it creates. The
canonical list is `CRM_WRITTEN` in `sync_crm.py`, asserted by the tests. (Until
2026-08-25 the four detail columns above were also withheld, on a rule dating
from when the Chapters folder was public-link; it is now 92 individual
per-chapter organizer grants, and the audience for a chapter's CRM is that
chapter's own organizers.)

`What brings you here?` is the form's routing question, and the role tabs are
filtered views that drop it — so it is read from `Form Responses` and joined back
on email. When a row can't be joined, the form's own wording for that branch is
used instead of inventing one.

### Rules

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
- **`Status` and `Interested in` are the two exceptions**: each is upgraded
  while it still holds a value the automation itself wrote (`AUTO_OWNED` in
  `sync_crm.py` — for `Status`, every lifecycle value plus the legacy `New` and
  the three pre-split role words; for `Interested in`, any `/`-joined
  combination of the role words). That is how a re-triage reaches a chapter —
  while a human's `Attended`, `Regular`, `Volunteer`, `Declined`, or a typed
  `Organizer (co-lead)`, is never undone.
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
- **The dropdowns are CHECKED here, never written.** `sync_crm` reports any
  workbook whose `Status` or `Interested in` list is not `DV_EXPECTED` and tells
  you to run `migrations/migrate_interested_in.py`; that script is the only thing that
  writes them. Schema in one place, data in the other — before 2026-08-25 this
  file patched the `Status` list in place, which made the *order* of the
  dropdown patch and the row serializer load-bearing inside `finalize()`, and
  getting it wrong silently threw the patch away while reporting it applied.
  People still sync into a workbook with a stale list; the values written are
  correct either way. The unrelated `New` on the **Signal** list is not touched
  by anything.
- **TemplateCity never receives people**, and is no longer opened for a patch —
  the migration covers it, so every chapter cloned from it is born split.
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


### Sharing: minimal by design

The status allowlists (`SYNC_STATUSES`, `PIPELINE_STATUSES` and the
`SELF_SERVE_MIN` gate on pipeline organizers) are what keep **declined
applicants** out of a folder shared more widely than intended — and since
2026-08 a *vetting-in-progress* person can legitimately appear in a self-serve
chapter's CRM as a `Prospect`.

`CRM_WRITTEN` is **no longer part of that mitigation.** It was six columns, on
the reasoning that the Chapters folder was link-readable; it is not — it is 92
individual per-chapter organizer grants — and since 2026-08-25 it is eleven
columns including LinkedIn, employer, job title and expertise. The audience for
a chapter's CRM is that chapter's own organizers. If the folder's sharing is
ever widened, that is now a decision about **everyone's survey detail**, not
just about which people appear.
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


## 3. Per-chapter access (`sync_access.py`)

`--notify` and `--mail-if-required` make Drive email real people, so both
require `--i-have-approval` (refused at parse time otherwise) — the same consent
the Slack write steps demand.

Moves the Chapters folder off its public link-share and onto per-chapter grants.
Report-only by default. Two phases, and **the order is not negotiable**:

Numbered as the console prints them:

1. **grant** — give each accepted organizer **`writer`** (the `--role` default)
   on their chapter folder. Must precede the lock: the public link is currently
   their only access.
2. **lock** — remove `anyone:reader` from Chapters/.

`--write` runs **grant then lock**. `--phase grant|lock` runs one phase, and
the run verifies whatever it ran; the final `Verified:` line claims **only**
the phases that actually ran and checked something. **`lock` refuses to run
when any grant failed** — locking then would leave those organizers with no
access at all; `--lock-anyway` overrides.

> **The website does not depend on this share — verified, not assumed.** The
> chapters feed's `Image` column is full of `lh3.googleusercontent.com/d/<id>`
> URLs pointing at `Web Banner.png` files inside chapter folders, which reads as
> "80 public Drive images the site serves". It isn't. `aaif.io/community-chapters`
> was loaded and inspected on 2026-08-07: 26 images, **all** from `cdn.sanity.io`,
> none from Drive. Chapter content and imagery live in Sanity; the Drive banners
> are source assets. A column full of image URLs is not evidence that anything
> fetches them — load the page and look. Nothing in this tree needs a public
> share (2026-09-03, user-decided): a `pin` phase used to exist for making a
> banner directly public, on the theory that something might genuinely need it;
> it never did, and was removed.

- **`assert_all_accepted()` is the last gate before write** and re-reads the
  intake through a different code path than the filter that built the plan.
  "The filter that made the list says the list is fine" is not a check. A grant
  the `Drive Email` column redirected is checked **twice over**: the acceptance
  and the chapter still come from the intake row, and the recorded address is
  re-derived from its own fresh read rather than trusted from the plan. Typing an
  address into that column can only redirect a grant an accepted organizer row
  already justifies — it can never manufacture one, which is what keeps a
  hand-editable cell from being a way in.
- **A bad address never abandons the run.** The intake is fed by a public form,
  so a typo'd address is normal input and Drive rejects it with a hard 400.
  Failures are collected and reported; every other grant still lands.
- **The grant goes to `Drive Email` when a human recorded one**, and to the
  intake `Email` otherwise. The intake address is never rewritten — it is what
  the person told us and the key the CRM merges on — so a second address is
  recorded beside it instead. See `references/identity-columns.md`; the mechanics are in
  `reviewed_drive_emails()`.
- **Addresses with no Google account** are refused by Drive unless it may email
  the person. There is no silent path, so they are skipped and reported. The fix
  that emails nobody is to record a Google-backed address in `Drive Email` and
  re-run; `--mail-if-required --i-have-approval` (or `--notify
  --i-have-approval`) sends the invitation instead — mail to real people is
  never a side effect of a sync.
- Notifications are **off** by default: a share-mail per organizer, arriving
  unannounced and all at once, reads as a phishing wave.
- `linuxfoundation.org` domain access is **kept** — that is LF staff reach, a
  separate decision from de-publicising the folder.
- Pre-existing direct grants on chapter folders are left alone, and every one
  held by someone the intake does not know about is **listed** in the report.
  They survive the lock — a denied ex-organizer keeps `writer` until a human
  removes it — so they are exactly what an audit needs to see.


## 4. Resource map (`sync_resources.py`)

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
