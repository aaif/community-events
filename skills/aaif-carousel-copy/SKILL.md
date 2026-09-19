---
name: aaif-carousel-copy
description: Write copy for a 6-slide LinkedIn carousel announcing an AAIF event. Use when asked to draft carousel slides/copy for an AAIF event (built from the LinkedIn Carousel template).
argument-hint: '[event title / paste tracker entry]'
---

# AAIF LinkedIn Carousel Copy

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

Caption text for a **6-slide** LinkedIn carousel built from the AAIF LinkedIn
Carousel template. Each slide: a **headline (max 7 words)** + one short supporting
line. **Slide 1 hooks, slide 6 is the CTA.**

**House voice:** share the practice, never sell the product. Specific over grand,
builder-to-builder. Signal, not numbers.
> **Public-copy rule.** Everything these skills write gets published — a post,
> a slide, an event page, a DM. Never include an email address, a phone number,
> a door code, or an attendee's name that is not already public. A name on an
> intake row is not public. Where a detail is needed but not publishable,
> describe it ("the venue sends door access to everyone who RSVPs") instead of
> printing it.
>
> **This covers your reply, not just the draft.** Saying which detail you left
> out is right; repeating its value to say so is not — "I left out her email"
> and "I left out maya@example.com" leave a very different thing in the
> transcript, and the transcript is copied, pasted and pushed like anything
> else. Name the field, never the value. Do not echo one back to confirm it,
> to explain an omission, or to ask whether it may be used.

**Workflow:** update the LinkedIn Carousel deck (`Event Template/LinkedIn Carousel.pptx`
in the chapter's Drive folder) with this copy, then export the PDF:
`gws drive files copy` the `.pptx`, converting it to a Google Slides file →
`gws drive files export` that copy to PDF → trash the copy. (The conversion can
substitute fonts; when fidelity matters, render each slide to PNG via
`aaif_events.slides_export` instead.) Post the PDF.

## Input (from the event tracker)

> **If the details were not pasted, fetch them — do not ask for a paste and
> stop.** One command reads the event's tracker entry:
>
> ```bash
> python3 skills/aaif-event-status/scripts/fetch_tracker.py "<Chapter or Series>" \
>     --event "<Event Title>"     # or --event next / --event latest
> ```
>
> It resolves the Drive folder, downloads the tracker to a private temp
> directory and prints the fields below. `--event` takes a title, a **unique**
> substring of one, or `next`/`latest`; an ambiguous substring raises rather
> than guessing, because a draft written for the wrong event reads exactly like
> a correct one. Contact details (`SPEAKER EMAIL`, `DOOR CODE`, venue contact)
> are deliberately **withheld** and only named — you never need them to write
> copy, and the public-copy rule above forbids publishing them. Delete the temp
> directory when done (`--print-cleanup` prints the command).
>
> `aaif-create-event` and `aaif-update-event` know the same layout if the event
> is being written in the same session. Ask the user only for what the tracker
> genuinely does not hold, and name the missing fields rather than asking again
> in general.

- Event : `[EVENT TITLE] ([SERIES]) — [THEME]`
- Speakers : `[SPEAKER + TOPIC; DEMO NAMES]`
- When : `[DATE & TIME]`   Where: `[VENUE / CITY]`   RSVP: `[LUMA URL]`

## Example (tested — match this format and voice)
Agentic AI Night:

| # | Headline | Supporting line |
|---|----------|-----------------|
| 1 | Agents in production. | What works at scale — and what doesn't. |
| 2 | Tool calling at 10M/day. | Maya Chen on what broke, and the fixes. |
| 3 | Three live demos. | AGENTS.md at monorepo scale. Sandboxing goose. |
| 4 | Builder-first, always. | Vendor-neutral. No pitches. People who ship. |
| 5 | Wed June 24 — 17:30. | SoMa, San Francisco. Doors at 5:30. |
| 6 | Grab a seat. | Curated + limited. RSVP at lu.ma/aaif-sanfrancisco |
