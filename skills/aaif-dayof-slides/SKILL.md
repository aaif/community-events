---
name: aaif-dayof-slides
description: Turn an event's tracker entry into the slide text for the AAIF "Day of Event" deck. Use when asked to fill/write the day-of slides or event deck for an AAIF event.
argument-hint: '[event title / paste tracker entry]'
---

# AAIF Day-of Slides (from the tracker)

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

Turn an event's tracker entry into the text for the chapter's **"Day of Event"**
deck (`Event Template/Slides.pptx`). Fill the per-event slides from the tracker and
**leave the fixed brand slides** (`[FIXED]`: About AAIF, the global-network stats)
**exactly as written** — they are brand-standard.

Keep it **terse and label-driven** (the deck voice). Output as `Slide N — <name>:`
then the fields, slide for slide, then paste into the template.

## Input (from the event tracker)
- Event : `[EVENT TITLE]`   Series: `[SERIES]`   Theme: `[THEME + ONE-LINER]`
- When : `[DATE & TIME]`   Venue/City: `[VENUE], [CITY]`
- Host : `[HOST VENUE]`   Members: `[MEMBER LOGOS]`
- Speakers : `[FOR EACH: NAME | ROLE | TALK or DEMO | "QUOTE"]`
- Agenda : `[RUN-OF-SHOW: TIME | BLOCK | NOTE]`
- Next : `[NEXT EVENT + DATE]`   Links: `[LUMA / DISCORD / NEWSLETTER]`

## Slides (in order)
```
 1 Cover            6 Tonight's theme    11 Demo lineup
 2 Welcome          7 Run-of-show        12 Thank you
 3 About AAIF[FIXED]8 On mic (speakers)  13 Join the chapter
 4 Local chapter    9 Talk one           14 Networking
 5 Network [FIXED] 10 Demos              15 Next up
```

## Example (tested — match this format and voice; abbreviated)
Agentic AI Night:

> **Slide 1 — Cover:**
>   Kicker: THE AAIF COMMUNITY · Title: Agentic AI Night. · Sub: Launch Series —
>   San Francisco · Date: TUE — JUNE 24, 2026 — 17:30 — LATE · Hosted by: Host
>   Venue Co. · With: [member logos]
>
> **Slide 6 — Tonight's theme:**
>   Label: TONIGHT'S THEME 01 · Title: Agents in production. · Body: What's working
>   at scale, what's breaking in ways the demos never showed, what nobody's figured
>   out yet. · Footer: STANDARDS AT WORK → MCP · AGENTS.MD · GOOSE
>
> **Slide 7 — Run-of-show:**
>   17:30/30m Doors & networking | 18:00/5m Why we're here | 18:05/25m Talk one —
>   Maya Chen | 18:30/25m Demos ×3 | 18:55/5m Wrap up | 19:00/60m Networking
>
> **Slide 8 — On mic:**
>   01/TALK Maya Chen — STAFF ENGINEER — PAYMENTS — "Tool calling at scale — what
>   broke at 10M requests a day." · 02/DEMO Diego Alvarez — AGENT INFRA —
>   "AGENTS.md in a 4,000-repo monorepo. Yes, really." · 03/DEMO Priya Nair —
>   GOOSE CONTRIBUTOR — "Sandboxing goose: lessons from letting agents run wild."
>
> **Slide 15 — Next up:**
>   Title: "AI in Finance" — July 22. · RSVP: lu.ma/aaif-sanfrancisco
>
> (Slides 2, 4, 9, 10, 11, 12, 13, 14 follow the same pattern.)
