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
Never include emails, phone numbers, door codes, or attendee names that are not already public in any post, slide, or message.

**Workflow:** update the LinkedIn Carousel deck (`Event Template/LinkedIn Carousel.pptx`
in the chapter's Drive folder) with this copy, then export the PDF:
`gws drive files copy` the `.pptx`, converting it to a Google Slides file →
`gws drive files export` that copy to PDF → trash the copy. (The conversion can
substitute fonts; when fidelity matters, render each slide to PNG via
`aaif_events.slides_export` instead.) Post the PDF.

## Input (from the event tracker)
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
