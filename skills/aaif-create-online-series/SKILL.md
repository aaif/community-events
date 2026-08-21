---
name: aaif-create-online-series
description: Create a new AAIF online event series in the "Online" Google Drive folder by cloning TemplateSeries and rebranding all assets. Use when asked to add/launch/set up a new AAIF online series (reading group, paper club, webinar, online discussion) — not a city chapter.
argument-hint: '<Series Name> [--slug <lumaslug>] [--resume]'
---

# Create AAIF Online Series

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

Spin up a new AAIF **online event series** (e.g. a Reading Group, a Paper Club) by
cloning the **TemplateSeries** folder in the top-level **Online** Google Drive
folder and rebranding every Office file from San Francisco to the new series. This
is the online sibling of [aaif-create-chapter]: same folder shape — `Event
Tracker.docx`, `Attendee CRM.xlsx`, and the `Event Template/` + `Banners (...)/`
subfolders of `.pptx` design assets — but the **Event Tracker is
the no-venue "online" runbook** (platform / join link / tech check / recording /
chat-Q&A moderation instead of venue / A-V / food / door).

Online series live under **Online/**, NOT under Chapters/. Use a city chapter
(aaif-create-chapter) for an in-person, city-based community; use this for a
recurring online program with no venue.

Prereq: the `gws` CLI must be installed and authenticated (see the user's
`gws-cli-access` memory). All Drive calls go through it.

## What gets replaced (and what does NOT)

The rebrand swaps two tokens and leaves everything else alone. Event-specific
content — the example-event block (dates, speakers, example title), the agenda —
is **template content** organizers fill per-event using the `aaif-*` content
skills in this repo. Do not touch it. The TemplateSeries master is already
series-shaped (no "Chapter" wording in identity; the About blurb is a `[bracketed]`
placeholder the organizer fills in).

| Token in template | Becomes | Notes |
|---|---|---|
| `San Francisco` / `SAN FRANCISCO` | new series, case-matched | contiguous in the clean template |
| `SF` abbreviation (`AAIF SF …`, doc metadata) | full series name | **UPPER** in all-caps contexts, **Title case** in prose |
| `aaif-sanfrancisco` / `aaif-sf` (Luma slug, incl. hyperlink targets) | `aaif-<slug>` | see slug rules below |
| File/folder **names** carrying any of the above (e.g. `San Francisco CRM.xlsx`) | renamed with the same transform | not just file contents; unit-tested |

## Luma slug rules

- Default slug = series lowercased, spaces/accents removed: `Reading Group → readinggroup`.
- A brand-new series usually has **no live Luma page yet** — the script warns; the
  page is created manually at luma.com. Pass `--slug` to override.
- Pages resolve at both `https://luma.com/aaif-<slug>` and `https://lu.ma/aaif-<slug>`.

## Procedure

1. **Confirm the series display name and slug with the user.** Ask for the exact
   name (e.g. "Reading Group", or "Online Reading Group" if they want the word
   Online in the title) and the Luma slug if one exists.

2. **Dry run first** to surface the slug, Luma status, and any name collision:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_series.py \
       --series "Reading Group" --dry-run
   ```
   - If it aborts with "already exists", stop — the series is already there.
   - If Luma shows NOT LIVE, tell the user the page needs creating at
     `luma.com/aaif-<slug>` (or that the slug differs — re-run with `--slug`).

3. **Create the series:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_series.py \
       --series "Reading Group"        # add --slug <x> if overriding
   ```
   The script clones TemplateSeries → a new `<Series>` folder under Online, then
   downloads, rebrands, and re-uploads each `.pptx/.docx/.xlsx` in place. It prints
   a tree and flags any file with `!! residual` tokens.

   **Recovering a failed or partial run — `--resume`.** If a run dies midway
   (a `gws` 403, template drift raising in the rebrand engine, network loss),
   the half-created series folder is already in Drive and a plain re-run aborts
   on the name collision. Don't trash the folder — re-run with `--resume`:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_series.py --series "Reading Group" --resume
   ```
   It enters the existing folder, skips every child whose (rebranded) name is
   already present (logged `exists, skipped`), and clones/rebrands only what's
   missing — so resuming a fully-cloned series is a no-op. It is also the
   backfill path when an existing series folder is missing part of the template
   (the Luxembourg-chapter situation, series-side). Note it matches by **name
   only**: a present-but-corrupt file is skipped, not repaired.

4. **Verify & hand off.** Confirm the run printed no `!! residual` flags and report
   the new folder URL. Remind the user to (a) fill the `[bracketed]` About-the-
   series blurb in `Event Tracker.docx`, and (b) create the Luma page if it wasn't
   live.

## How it works / maintenance

`scripts/create_series.py` shares the **same text engine** as aaif-create-chapter
(paragraph-level concatenate → transform → write-back, robust to OOXML
run-splitting). Constants at the top: `ONLINE_PARENT` (the Online folder) and
`TEMPLATE_FOLDER` (TemplateSeries). The master's design decks (`Event Template/`,
`Slides.pptx`) were authored from the chapter decks with the front-facing brand
taglines de-chaptered; their **body content may still carry chapter/in-person
phrasing** ("global network of chapters", "same venue") — that's the organizer-
customized starting point, same as the example-event block.

To validate the engine after any edit, rebrand a throwaway copy of the template
and check for residuals + that identity reads right:
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/create_series.py \
    --series "Reading Group" --rebrand-local /path/to/templateseries-copy
python3 ${CLAUDE_SKILL_DIR}/scripts/test_create_series.py   # unit tests (offline)
```

The template must stay "clean": `San Francisco` contiguous and the slug normalized
to `aaif-sanfrancisco`, so the two-token swap stays exhaustive.
