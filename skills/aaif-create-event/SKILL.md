---
name: aaif-create-event
description: Create a new event in an AAIF chapter or online series by cloning the example section in its Event Tracker.docx and stamping all phase task due-dates from the event date; can then create the live Luma event page from the entry (proposal shown first, created only on explicit user approval). Use when asked to add/schedule/set up a new event for a chapter or series, or to put an event on Luma.
argument-hint: '<chapter|series> --title "..." --date "..."'
---

# AAIF Create Event

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

Add a new event to a chapter/series `Event Tracker.docx`: clone the example event
section, fill the detail block, and compute every phase task's DUE date backward from
the event date (the template's exact cadence is preserved per task). Mode is implicit —
a chapter clones the in-person task set, an online series the online set, because you
download whichever tracker the folder holds.

**You (the agent) drive Google Drive via the `gws` CLI; the Python script only does the
deterministic docx edit on a local file.** Prereq: `gws` installed and authenticated
(`gws-cli-access`).

## Steps

1. **Locate the tracker.** Chapters parent `1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx`, Online
   parent `1g2vHrqDHfh9wBkDJryJIl8wqXA4J-d4i`. Find the named folder, then its
   `Event Tracker.docx` id (see `aaif-event-status` for the exact `gws drive files list`
   queries).

2. **Download it:**

   ```
   gws drive files get --params '{"fileId":"<DOC_ID>","alt":"media"}' --output tracker.docx
   ```

3. **Add the event (deterministic, local).** Aborts if the title already exists:

   ```
   # in-person (chapter) tracker
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_event.py tracker.docx \
     --title "Eval Night · Builder Series" \
     --date "Wed · August 12, 2026 · 18:00 — late" \
     [--theme ...] [--venue ...] [--location ...] [--speakers ...] \
     [--luma ...] [--capacity ...] [--organizer ...] [--dry-run]

   # online (series) tracker — use --platform / --join, NOT --venue / --location
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_event.py tracker.docx \
     --title "..." --date "..." [--platform "Zoom Webinar"] [--join "lu.ma/..."] ...
   ```
   Flags must match the tracker's labels: a chapter tracker has `VENUE` /
   `LOCATION / CITY`; a series tracker has `PLATFORM` / `STREAM / JOIN LINK`. Passing
   a flag whose label doesn't exist in that tracker **aborts loudly** (it is not
   silently dropped). Omitted fields keep the example's text for the organizer to
   fill later. Note: `--luma` sets the displayed URL text only; the clickable Luma
   link target (per chapter/series) is not rewritten here — set it on the Luma page.

4. **Upload it back:**

   ```
   gws drive files update --params '{"fileId":"<DOC_ID>"}' --upload tracker.docx \
     --upload-content-type application/vnd.openxmlformats-officedocument.wordprocessingml.document
   ```

Use `--dry-run` in step 3 first if you want to preview without modifying the local file.

## Put the event on Luma (LIVE — always confirm first)

Every event needs a Luma page. `scripts/luma_push.py` creates it on the
chapter/series calendar from the tracker entry — **when Luma is connected**,
i.e. that calendar's API key is in `LUMA_API_KEY` or the keychain
(`security add-generic-password -s luma-api-key -a aaif -w THE_KEY`; Luma Plus,
keys are per-calendar). The script detects this itself and its dry-run says so:

- **Connected** → show the user the printed proposal; on their explicit approval
  (and ONLY then — Luma is live and guest-facing) re-run with `--create`.
- **Not connected** → do NOT try to work around it: skip the automated push and
  ask the user to create the page manually at luma.com using the proposal's
  details, then record the URL in the tracker's LUMA URL field (or set up the
  key and re-run).

1. **Prepare the assets**: write the page copy with `aaif-luma-description` and
   save it as markdown; export the event banner to PNG for the cover via the
   Slides API (see the tooling rule at the top of this file):
   ```
   PYTHONPATH=lib python3 -c "
   from aaif_events.slides_export import render_slide_png
   render_slide_png('<Banner.pptx file id>', 'banner.png', slide_index=0)
   "
   ```
   Determine the city's IANA timezone yourself and include it in the proposal
   for the user to check.

2. **Propose (default, sends nothing):**
   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/luma_push.py tracker.docx "Eval Night · Builder Series" \
     --timezone America/Los_Angeles --description-file luma.md --cover banner.png \
     --host "maya@example.com" --host "vol@example.com:check-in"
   ```
   It prints the full payload, hosts, and which calendar the API key targets.
   Show all of it to the user. The start time comes from the first `HH:MM` in
   DATE & TIME (a second one is the end time; else `--duration-hours`, default 3).
   It aborts if the tracker's LUMA URL already holds an event page (`--force` to override).

3. **Create — only after the user says yes:** re-run the same command with
   `--create`. It uploads the cover, creates the event, adds hosts, writes the
   new event URL into the tracker's LUMA URL field (re-upload the docx to Drive),
   and prints the URL.

4. **Verify**: open the printed URL and check name/time/venue/cover/description
   against the proposal; confirm the hosts appear. Later detail changes go
   through `aaif-update-event` (which diffs against the live page), not a re-push.
