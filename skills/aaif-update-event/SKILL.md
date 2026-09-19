---
name: aaif-update-event
description: Apply a change to an existing AAIF event (chapter or series) — edit detail fields like speakers/venue/capacity, or move the date and recompute all task due-dates, then flag which marketing/banner assets are now stale (speaker, venue/location, platform/join-link, and date changes set flags); can also sync the change to the live Luma event page (diff shown first, pushed only on explicit user approval). Use when asked to update/change/edit an event's details or date.
argument-hint: '<chapter|series> <event> [--set "LABEL=value"] [--date "..."]'
---

# AAIF Update Event

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

Change-driven editor for one event in a chapter/series `Event Tracker.docx`. State the
change; the script edits the right detail fields. If you move the date, every phase task
DUE date is recomputed (clock-time day-of tasks are left alone). It reports which
downstream assets (banner, Luma cover, posts, slides) are now stale so you can re-run
those skills — it does not regenerate them.

**You (the agent) drive Google Drive via the `gws` CLI; the Python script only does the
deterministic docx edit on a local file.** Prereq: `gws` installed and authenticated
(`gws-cli-access`).


## Preflight

- [ ] `gws` installed and authenticated (see the user's `gws-cli-access` memory).
- [ ] Know which kind of tracker this is. **Chapter (in-person):** `EVENT TITLE`,
      `DATE & TIME`, `LOCATION / CITY`, `VENUE`, `THEME / SERIES`, `FORMAT(S)`,
      `SPEAKER(S)`, `LUMA URL`, `CAPACITY / RSVPS`, `ORGANIZER ON POINT`.
      **Series (online):** the same, but `PLATFORM` and `STREAM / JOIN LINK`
      replace `LOCATION / CITY` and `VENUE`.
- [ ] For the Luma sync: that calendar's API key in `LUMA_API_KEY` or keychain
      item `luma-api-key` (see `aaif-create-event` for setup).

## Update the tracker

- [ ] **1. Fetch the tracker** into a temp directory you name, so the later
      Luma steps can write `new.md` / `new.png` beside it:
      ```bash
      WORK=$(mktemp -d)
      python3 skills/aaif-event-status/scripts/fetch_tracker.py "<Chapter or Series>" --keep "$WORK"
      ```
      Never commit a `tracker.docx` / `luma.md` / `banner.png` / `new.*`.
- [ ] **2. Preview** with `--dry-run`. It prints the field diff (old → new) and
      the stale-asset list, and writes nothing:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/update_event.py $WORK/tracker.docx "Agentic AI Night" \
        --set "SPEAKER(S)=Jane Doe (Agent Infra)" --dry-run
      ```
- [ ] **3. Apply** it locally:
      ```bash
      # add/replace a field
      python3 ${CLAUDE_SKILL_DIR}/scripts/update_event.py $WORK/tracker.docx "Agentic AI Night" \
        --set "SPEAKER(S)=Jane Doe (Agent Infra)"

      # move the date — recomputes every due date from the ORIGINAL date
      python3 ${CLAUDE_SKILL_DIR}/scripts/update_event.py $WORK/tracker.docx "Agentic AI Night" \
        --date "Wed · July 8, 2026 · 17:30 — late"
      ```
- [ ] **4. Upload it back:**
      ```bash
      gws drive files update --params '{"fileId":"<DOC_ID>"}' --upload $WORK/tracker.docx \
        --upload-content-type application/vnd.openxmlformats-officedocument.wordprocessingml.document
      ```
- [ ] **5. Surface the stale-asset list** the script printed, so the organizer
      knows which content/banner skills to re-run.

## Sync the change to Luma — LIVE, always confirm first

If the tracker's `LUMA URL` holds an event URL, `luma_sync.py` diffs the tracker
against the live event and pushes **only the changed fields**.

**Not connected → the script prints the desired values as a manual checklist.**
Hand it to the user to apply on the Luma page themselves.

- [ ] **1. Diff** — the default, sends nothing:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/luma_sync.py $WORK/tracker.docx "Agentic AI Night" \
        --timezone Europe/Berlin
      ```
- [ ] **2. Show the user the diff and get explicit approval.** Luma is live.
- [ ] **3. Apply:**
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/luma_sync.py $WORK/tracker.docx "Agentic AI Night" \
        --timezone Europe/Berlin --apply [--notify-guests]
      ```
      It re-fetches the event afterwards and verifies the diff is clean.

## Gotchas

- **`--set "DATE & TIME=..."` is refused.** A bare field write would skip the
  due-date recompute, which has to run against the *original* date. Move a date
  with `--date` only.
- **`--set` with a label absent from that tracker raises**, rather than silently
  no-opping. A chapter flag on a series tracker is an error, not a no-op.
- **An ambiguous event title errors rather than guessing.** Exact
  (case-insensitive) title first, then a *unique* substring; 2+ matches raise.
  `next` / `latest` also work.
- **Guest notifications are suppressed by default.** The dry run says `Guests
  will NOT be notified (pass --notify-guests)`. Add `--notify-guests` **only**
  when the user explicitly wants every registered guest emailed — a moved date,
  a new venue. Never for copy tweaks or an iterative sync.
- **Changing `PLATFORM` or `STREAM / JOIN LINK` flags the same stale assets a
  venue change does** — the reminder and the slides carry the join link.
- **`--description-file` / `--cover` only when replacing those.** Omitted means
  left alone.
- **Event cancellation is deliberately not automated.** It is irreversible and it
  refunds and notifies everyone. If the user asks to cancel, point them at the
  Luma page.
- **Delete `$WORK` when done.**

## Verify

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/test_update_event.py
python3 ${CLAUDE_SKILL_DIR}/scripts/test_luma_sync.py
```
