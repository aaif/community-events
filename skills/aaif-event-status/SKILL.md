---
name: aaif-event-status
description: Report task status for an AAIF chapter or online series — which event tasks are overdue or due soon, grouped by owner, read from the Event Tracker.docx — plus read-only Luma registration stats (going/waitlist/checked-in counts) for pushed events. Use when asked for the status / health / what's-due / RSVP numbers of a chapter or series' events.
argument-hint: '<chapter|series> [event]'
---

# AAIF Event Status

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

Read-only digest of a chapter or online series' `Event Tracker.docx`: for each event,

Read-only digest of a chapter or online series' `Event Tracker.docx`: for each event,
the **overdue** and **due-soon** (within 7 days) tasks, grouped by owner. Nothing is
ever written back — not to Drive, not to the tracker, not to any sheet.

## Preflight

- [ ] `gws` installed and authenticated (see the user's `gws-cli-access` memory).
- [ ] The chapter or series name **exactly as the Drive folder spells it** —
      `New York City`, not `NYC`. `aaif-sync-slack`' resource map holds the
      folder URL for every chapter on the Chapters List.
- [ ] Working from a full checkout (these scripts import `lib/aaif_events`).

## Steps

- [ ] **1. Fetch the tracker.** One command resolves the folder (Chapters, then
      Online — the mode is implicit in which parent holds the name), finds
      `Event Tracker.docx`, and downloads it to a fresh `0700` temp directory:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_tracker.py "New York City"
      ```
      It prints `tracker: <path>`. **Do not hand-compose `gws drive files list`
      queries for this** — the nested quoting is where it goes wrong, and the
      script already handles a name with an apostrophe.
- [ ] **2. Run the digest** (local, deterministic, no network):
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/event_status.py <path> ["event"]
      ```
      Status is computed against today from each task's DUE cell; clock-time
      day-of tasks and `Done` tasks are excluded.
- [ ] **3. Registration stats**, for events whose tracker `LUMA URL` holds their
      event page (written by `aaif-create-event`'s Luma push):
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/luma_stats.py <path> ["event"]
      python3 ${CLAUDE_SKILL_DIR}/scripts/luma_stats.py --url https://luma.com/EVENT_SLUG
      ```
      Going, pending, waitlist, invited, declined, checked-in, plus registration
      state. The script detects whether Luma is connected (that calendar's API
      key in `LUMA_API_KEY` or keychain item `luma-api-key`; see
      `aaif-create-event` for setup) and skips the stats with a note when it is
      not — the task digest still works and the user can read the numbers off the
      event page by hand.
- [ ] **4. Delete the temp directory.** It holds organizer, speaker and venue
      details. `fetch_tracker.py --print-cleanup` prints the `rm -rf` command.

## Feeding a content skill

The eight content skills (`aaif-announcement-post`, `aaif-recap-post`,
`aaif-luma-description`, `aaif-carousel-copy`, `aaif-speaker-bio`,
`aaif-speaker-invite`, `aaif-attendee-reminder`, `aaif-dayof-slides`) all open on
the same question, and this is the answer to it:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_tracker.py "New York City" \
    --event "Agentic AI Night"          # or --event next / --event latest
```

`--event` takes a title, a **unique** substring of one, or `next` / `latest`. An
ambiguous substring **raises rather than resolving** — a draft written for the
wrong event reads exactly like a correct one. Add `--json` for machine-readable
fields.

## Gotchas

- **Contact details are withheld from the field digest on purpose.** `SPEAKER
  EMAIL`, `DOOR CODE`, `VENUE CONTACT` and their siblings are read but not
  printed: the surest way to keep an address out of a published post is to keep
  it out of the agent's context. The run *names* which fields it withheld, so a
  blank is never mistaken for an empty tracker. `--all-fields` overrides, for a
  human debugging a tracker — never for drafting copy.
- **The tracker is downloaded outside the repo**, into a system temp dir, not
  into the working directory. `.gitignore` is a weaker guarantee than a path
  with nothing to commit it to. **Never commit a tracker.**
- **Luma numbers never flow back.** This skill is strictly read-only; Luma data
  is never fed into the Intake Ops sheet. Use the counts for the day-of slides
  (`aaif-dayof-slides`) and the recap post (`aaif-recap-post`).
- **A folder name that matches two folders aborts.** Rename one rather than
  letting the script pick.

## Verify

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/test_fetch_tracker.py
python3 ${CLAUDE_SKILL_DIR}/scripts/test_event_status.py
python3 ${CLAUDE_SKILL_DIR}/scripts/test_luma_stats.py
```
