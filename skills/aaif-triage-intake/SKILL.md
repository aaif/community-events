---
name: aaif-triage-intake
description: Triage new AAIF community intake submissions (organizers, hosts/venues, speakers) from the Intake Ops sheet — summarize who's awaiting review, assess fit, and draft next-step outreach. Use when asked to review/triage new applicants, check the intake queue, or produce an intake digest.
argument-hint: "[organizers|hosts|speakers]"
---

# Triage AAIF Intake

Review the people who applied through the **"AAIF Community — Get Involved"** form
and decide what happens next. The form feeds the **AAIF Community Intake Ops**
sheet (id `1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o`), which auto-routes each
submission to the **Organizers**, **Hosts**, or **Speakers** tab. Submissions
land automatically; this skill is the human review loop on top of them.

Prereq: the `gws` CLI must be installed and authenticated (see the user's
`gws-cli-access` memory). See the user's `aaif-intake-ops-sheet` memory for the
sheet's structure.

## Status model (drives the queue and the sheet's cell colors)

The Status values (dropdown on column A, matched exactly by the sheet's
whole-row colors): `New` (blue) → `In progress` (orange) → `Tentative` (teal) →
`Interviewing` (indigo) → `Accepted` (green) / `Denied` (maroon); `Inactive` (gray);
`Duplicate` (brown); and `Existing (from MLOps)`
(**violet**) for a prior organizer imported from the MLOps community. `Tentative` is a
real dropdown value: it marks a candidate who has passed LinkedIn vetting but isn't yet
accepted (pending the interview / chapter-champs intro in the review flow below).
`Interviewing` is the stage after it — the interview is scheduled or under way — so
"vetted, waiting on us to book it" and "already in the process" stop sharing one
color. MLOps veterans skip both and convert straight through.
`Duplicate` is the disposition for a **repeat submission from someone already in the
queue** — the person is triaged on their original row, and the duplicate is parked
rather than denied (a `Denied` row reads as a decision about the person, which this
is not). It is not a sync status: nothing carries a `Duplicate` row to the chapters
feed, a CRM or a Drive grant. Rows are only ever marked by hand — the form can't
know it has seen someone before, so no automation sets this value. A
**blank** Status cell is treated as `New`. Two overrides beat the status color: a **data error** (missing/invalid email
or broken LinkedIn) paints the row bright red, and an **SLA breach** — a `New`/blank
row older than 1 week (of a 2-week response SLA) — paints it pink. Acting on a row
(moving it off `New`) clears the pink. Each role tab also has `Reviewed by`,
`Reviewed at`, `Decision notes`, and a `Chapter` assignment.

**City provenance colors** (on the two city columns, below the error rule; installed by
`aaif-clean-data install-colors`): the role tabs show **`City (Existing)`** (col G, the
submitted dropdown) and **`City (New)`** (col H, the resolved city for `Other` rows).
`City (New)` is painted **amber** when it holds a net-new resolved city; `City
(Existing)` is painted **green** when it holds a real submitted city (non-empty, not
"Other"). These tell you at a glance whether an applicant is from an existing chapter
city or a brand-new one.

## Organizer review flow

Every credible applicant goes to **Tentative** first — no one is accepted directly.
MLOps veterans convert straight through; everyone else is accepted only **after an
interview** (existing-city candidates also get an intro to the chapter champs):

```
Form submission ─→ New
   └ Review LinkedIn: credible organizer?  ── no ─→ Denied
        │ yes
        ▼  Tentative (vetted, not yet accepted)
        ├ Prior organizer, existing chapter (MLOps) → violet → Accepted (existing MLOps)
        ├ Existing city, net-new → green City (Existing) → intro chapter champs → Interviewing → Accepted (after interview)
        └ New city / new chapter → amber City (New) → Interviewing → Accepted (after interview)
   On final Accept (either) ─→ grant: local chapter Drive folder + local-champs
        channel + guidelines (confirm they've read & understood them)
```

The same flow (and colors) is documented on the sheet's **"How to use"** tab.

## Procedure

1. **Pull the queue.** Rows needing attention = Status blank / `New` / `In progress`:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/intake.py
   ```
   Add `--json` for structured data, `--all` for every row, or
   `--status Accepted` to filter explicitly. If the user named one type
   (`organizers` / `hosts` / `speakers`), focus there but pull all so counts are right.

2. **Assess fit per applicant**, using these signals (don't over-weight any one):
   - **Organizer** — real ties to a local AI community, has run events before,
     a concrete programming idea, and a city. Watch for a `City` of "Other" with a
     non-obvious location (it's in their text) → note the actual city.
   - **Host** — capacity ≥ 30 (`Holds 30+?`), A/V + wifi, a real company/venue,
     and ideally `Recurring support?`. Logistics gaps are follow-ups, not denials.
   - **Speaker** — talk relevance to AAIF (agents/MCP/infra/applied AI), ships in
     production, and evidence (`Past talks / portfolio`). A thin abstract is a
     follow-up for specifics.

3. **Produce the triage digest** — grouped by tab, and for each applicant give a
   one-line recommendation: **Accept**, **Follow up** (what to ask), or **Pass**
   (why). Lead with the strongest candidates. Keep it skimmable.

4. **Draft outreach where it helps** — don't just judge, move it forward:
   - Speakers worth pursuing → use the **`aaif-speaker-invite`** skill for the DM.
   - An accepted organizer for a city that has **no chapter yet** → suggest running
     **`aaif-create-chapter`** for that city.

5. **Write back only if asked.** Default is read-only. If the user wants to record
   decisions, set `Status` / `Reviewed by` / `Reviewed at` / `Decision notes`
   (and `Chapter`) via `gws sheets spreadsheets values batchUpdate`
   (`valueInputOption: USER_ENTERED`). Resolve the target cell by the row number
   from step 1 and the column's header name — never assume a fixed column letter.

## Digest mode (for automation)

`intake.py --json` is the data source for a future scheduled digest routine
(delivery channel TBD with the user). The same selection logic powers both the
interactive triage and the unattended digest, so they never drift.

## Notes

- The sheet is read by **header name**, not column letter — robust to the form or
  sheet gaining/reordering columns. Keep that property in any edits here.
- `Other:` responses to "What brings you here?" match no tab and won't appear in
  any queue. If counts look short, check `Form Responses` for unrouted rows.
