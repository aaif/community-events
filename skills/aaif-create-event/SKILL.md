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


## Preflight

- [ ] `gws` installed and authenticated (see the user's `gws-cli-access` memory).
- [ ] The chapter or series name **exactly as the Drive folder spells it**.
- [ ] Know which kind of tracker you are editing. A **chapter** tracker has
      `VENUE` / `LOCATION / CITY`; a **series** tracker has `PLATFORM` /
      `STREAM / JOIN LINK`. Passing a flag whose label does not exist in that
      tracker **aborts loudly** — it is never silently dropped.
- [ ] For the Luma push: that calendar's API key in `LUMA_API_KEY` or keychain
      item `luma-api-key`. Store it with
      `security add-generic-password -s luma-api-key -a aaif -w` — **with no
      value after `-w`**, so it prompts instead of landing in shell history.
      Keys are per-calendar (Luma Plus).

## Add the event to the tracker

- [ ] **1. Fetch the tracker.** One command resolves the folder and downloads
      it. `--keep` puts it in a directory you name, so the later Luma steps can
      write `luma.md` and `banner.png` beside it:
      ```bash
      WORK=$(mktemp -d)
      python3 skills/aaif-event-status/scripts/fetch_tracker.py "<Chapter or Series>" --keep "$WORK"
      ```
      These downloads hold organizer, speaker and venue details — **never commit
      them** (or any `tracker.docx` / `luma.md` / `banner.png` / `new.*`).
- [ ] **2. Preview** the edit. `--dry-run` writes nothing, not even locally:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/create_event.py $WORK/tracker.docx \
        --title "Eval Night · Builder Series" \
        --date "Wed · August 12, 2026 · 18:00 — late" --dry-run
      ```
- [ ] **3. Apply** it locally (deterministic; aborts if the title already exists):
      ```bash
      # in-person (chapter) tracker
      python3 ${CLAUDE_SKILL_DIR}/scripts/create_event.py $WORK/tracker.docx \
        --title "..." --date "..." \
        [--theme ...] [--venue ...] [--location ...] [--speakers ...] \
        [--luma ...] [--capacity ...] [--organizer ...]

      # online (series) tracker — --platform / --join, NOT --venue / --location
      python3 ${CLAUDE_SKILL_DIR}/scripts/create_event.py $WORK/tracker.docx \
        --title "..." --date "..." [--platform "Zoom Webinar"] [--join "lu.ma/..."]
      ```
      Omitted fields keep the example's text for the organizer to fill later.
- [ ] **4. Upload it back:**
      ```bash
      gws drive files update --params '{"fileId":"<DOC_ID>"}' --upload $WORK/tracker.docx \
        --upload-content-type application/vnd.openxmlformats-officedocument.wordprocessingml.document
      ```

## Put the event on Luma — LIVE, always confirm first

Every event needs a Luma page. `luma_push.py` creates it on the chapter/series
calendar from the tracker entry, **when Luma is connected**. The script detects
this itself and its dry run says so.

**Not connected → do NOT work around it.** Skip the automated push, ask the user
to create the page by hand at luma.com from the proposal's details, and record
the URL in the tracker's `LUMA URL` field (or set the key up and re-run).

- [ ] **1. Prepare the assets.** Write the page copy with `aaif-luma-description`
      into `$WORK/luma.md`, and export the banner for the cover:
      ```bash
      PYTHONPATH=lib python3 -c "
      from aaif_events.slides_export import render_slide_png
      render_slide_png('<Banner.pptx file id>', '$WORK/banner.png', slide_index=0)
      "
      ```
      Determine the city's IANA timezone yourself and include it in the proposal
      for the user to check.
- [ ] **2. Propose** — the default, sends nothing:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/luma_push.py $WORK/tracker.docx "Eval Night · Builder Series" \
        --timezone America/Los_Angeles --description-file $WORK/luma.md --cover $WORK/banner.png \
        --host "maya@example.com" --host "vol@example.com:check-in"
      ```
      It prints the full payload (the header shows the visibility), the hosts,
      and which calendar the API key targets.
- [ ] **3. Show the user all of it and get explicit approval.** Luma is live and
      guest-facing. Never skip to `--create`.
- [ ] **4. Create** — re-run the same command with `--create`. It uploads the
      cover, creates the event **private** (the default), adds hosts, writes the
      new URL into the tracker's `LUMA URL` field, and prints the URL.
      Re-upload the docx to Drive afterwards.
- [ ] **5. Verify, then publish.** Open the printed URL and check
      name/time/venue/cover/description against the proposal, and that the hosts
      appear. Then have **the user** set the page public on Luma.

## Gotchas

- **The start time comes from the first `HH:MM` in `DATE & TIME`.** A second one
  is read as the end time; otherwise `--duration-hours` (default 3).
- **A `LUMA URL` that already holds an event page aborts the push.** `--force`
  overrides — read why it is already filled before you use it.
- **`--luma` and the write-back set the displayed URL *text* only.** If the
  template pre-filled that cell as a hyperlink, the clickable target still points
  at the old destination and must be fixed on the doc (or on the Luma page link).
- **The page stays private until a human flips it**, so a mistaken push never
  reaches guests. `--visibility public` exists but is not the norm.
- **Later detail changes go through `aaif-update-event`**, which diffs against
  the live page — never a re-push.
- **Delete `$WORK` when done.** It holds organizer, speaker and venue details.

## Verify

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/test_create_event.py
python3 ${CLAUDE_SKILL_DIR}/scripts/test_luma_push.py
```
