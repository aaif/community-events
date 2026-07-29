---
name: aaif-update-event
description: Apply a change to an existing AAIF event (chapter or series) — edit detail fields like speakers/venue/capacity, or move the date and recompute all task due-dates, then flag which marketing/banner assets are now stale; can also sync the change to the live Luma event page (diff shown first, pushed only on explicit user approval). Use when asked to update/change/edit an event's details or date.
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

## Steps

1. **Locate + download the tracker** (Chapters parent `1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx`,
   Online parent `1g2vHrqDHfh9wBkDJryJIl8wqXA4J-d4i`; see `aaif-event-status` for the
   `gws drive files list` queries):

   ```
   gws drive files get --params '{"fileId":"<DOC_ID>","alt":"media"}' --output tracker.docx
   ```

2. **Apply the change (deterministic, local):**

   ```
   # add/replace a speaker
   python3 ${CLAUDE_SKILL_DIR}/scripts/update_event.py tracker.docx "Agentic AI Night" \
     --set "SPEAKER(S)=Jane Doe (Agent Infra)"

   # move the date (recomputes all due-dates from the original date)
   python3 ${CLAUDE_SKILL_DIR}/scripts/update_event.py tracker.docx "Agentic AI Night" \
     --date "Wed · July 8, 2026 · 17:30 — late"
   ```
   The event argument matches an **exact** (case-insensitive) title first, then a
   unique substring; an ambiguous substring (2+ matching titles) errors rather than
   guessing. You can also pass `next` / `latest`.

   Detail labels depend on the tracker type:
   - **chapter (in-person):** EVENT TITLE, DATE & TIME, LOCATION / CITY, VENUE,
     THEME / SERIES, FORMAT(S), SPEAKER(S), LUMA URL, CAPACITY / RSVPS, ORGANIZER ON POINT.
   - **series (online):** same, but `PLATFORM` and `STREAM / JOIN LINK` replace
     `LOCATION / CITY` and `VENUE`.

   `--set` with a label absent from that tracker raises an error (it won't silently no-op).

3. **Upload it back:**

   ```
   gws drive files update --params '{"fileId":"<DOC_ID>"}' --upload tracker.docx \
     --upload-content-type application/vnd.openxmlformats-officedocument.wordprocessingml.document
   ```

The script prints the stale-asset list in step 2 — surface that so the organizer knows
which content/banner skills to re-run.

## Sync the change to Luma (LIVE — always confirm first)

If the event has a Luma page (the tracker's LUMA URL holds its event URL, written
by `aaif-create-event`'s push), `scripts/luma_sync.py` diffs the tracker against
the live event and pushes only the changed fields. It detects whether Luma is
connected (that calendar's API key in `LUMA_API_KEY` or keychain item
`luma-api-key`; see `aaif-create-event` for setup):

- **Connected** → show the user the printed diff; on their explicit approval
  (and ONLY then — Luma is live, and it emails guests about changes unless
  `--quiet`) re-run with `--apply`.
- **Not connected** → the script prints the desired values as a manual
  checklist; pass it to the user to apply on the Luma page by hand.

```
# diff only (default, sends nothing) — show this to the user
python3 ${CLAUDE_SKILL_DIR}/scripts/luma_sync.py tracker.docx "Agentic AI Night" \
  --timezone Europe/Berlin

# push, only after the user says yes; add --quiet to skip guest notifications
python3 ${CLAUDE_SKILL_DIR}/scripts/luma_sync.py tracker.docx "Agentic AI Night" \
  --timezone Europe/Berlin --apply
```

Add `--description-file new.md` / `--cover new.png` only when replacing those —
omitted means left alone. After `--apply` it re-fetches the event and verifies
the diff is clean. **Event cancellation is deliberately not automated** (it's
irreversible and refunds/notifies everyone) — if the user asks to cancel, point
them to the Luma page.
