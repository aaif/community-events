---
name: aaif-create-chapter
description: Create a new AAIF city chapter in the "Chapters" Google Drive by cloning TemplateCity and rebranding all assets. Use when asked to add/launch/set up a new AAIF city, chapter, or location.
argument-hint: '<City Name> [--slug <lumaslug>] [--lat <deg> --lon <deg>] [--write] [--resume] [--repair-existing]'
---

# Create AAIF Chapter

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

Spin up a new AAIF city "chapter" by cloning the **TemplateCity** folder in the
**Chapters** Google Drive and rebranding every Office file from San Francisco to
the new city. Each chapter folder is the standard template: `Event Tracker.docx`,
`Attendee CRM.xlsx`, and the `Event Template/` + `Banners (...)/` subfolders of `.pptx`
design assets. (The old `SKILLS.md.docx` of paste-into-Claude prompts is retired —
those prompts now live as the `aaif-*` content skills in this repo.)

Prereq: the `gws` CLI must be installed and authenticated (see the user's
`gws-cli-access` memory). All Drive calls go through it.

## What gets replaced (and what does NOT)

The rebrand swaps two tokens and leaves everything else alone. Event-specific
content — dates ("JUNE 24"), speakers ("Maya Chen"), venue, agenda, the SoMa /
"SOUTH OF MARKET" neighbourhood placeholder — is **template content** that
organizers fill per-event later using the `aaif-*` content skills in this repo. Do
not touch it.

| Token in template | Becomes | Notes |
|---|---|---|
| `San Francisco` / `SAN FRANCISCO` | new city, case-matched | contiguous in the clean template |
| `SF` abbreviation (`AAIF · SF`, `SF CHAPTER`, `About the AAIF SF Chapter`, `AAIF SF — Attendee CRM`, doc metadata) | full city name | **UPPER** in all-caps contexts, **Title case** in prose |
| `aaif-sanfrancisco` / `AAIF-SANFRANCISCO` (Luma slug, incl. hyperlink targets) | `aaif-<slug>` / `AAIF-<SLUG>` | see **Luma slug rules** below |
| File/folder **names** carrying any of the above (e.g. `San Francisco CRM.xlsx`) | renamed with the same transform | not just file contents; unit-tested |

Beyond text, the script also **repositions the green "you-are-here" dot and its
`<CITY> · TONIGHT` label** on slide 5 ("THE NETWORK") of `Event Template/Slides.pptx`
to the new city's real place on the world map. Previously only the label text was
rebranded and the dot stayed parked at San Francisco — this closes that gap. The
city's coordinates come from `--lat`/`--lon` if given, otherwise from geocoding the
city name (Nominatim, keyless). If neither resolves (offline, or a fictional name),
the dot is left at San Francisco with a clear warning — chapter creation never
fails over the dot. See **Map dot coordinates** below.

## Luma slug rules

- Default slug = city lowercased, spaces/accents removed: `New York → newyork`,
  `Mexico City → mexicocity`, `Montréal → montreal`.
- **Exceptions exist** — e.g. **Denver's page lives at `aaif-colorado`**, not
  `aaif-denver`. Always confirm the live page; pass `--slug` to override.
- Live pages resolve at both `https://luma.com/aaif-<slug>` and
  `https://lu.ma/aaif-<slug>`. The design files display the brand form
  `LU.MA / AAIF-<SLUG>`; keep that — only the slug changes.
- The script **cannot create the Luma page** (that's done manually at luma.com).
  It checks whether the page is live and warns if not.

## Map dot coordinates

The slide-5 network-map dot is placed from the city's latitude/longitude:

- **Default:** the script geocodes the `--city` name via Nominatim (keyless, no
  key/setup). The dry run prints the resolved `Coords:` line so you can sanity-check
  it before creating anything.
- **Override:** pass `--lat <deg> --lon <deg>` (both required together) to skip
  geocoding — useful when a city name is ambiguous or geocodes to the wrong place.
- **Fallback:** if geocoding returns nothing or the service is unreachable and no
  override was given, the dot is left at San Francisco and a warning is printed. The
  chapter is still created; just fix slide 5's dot manually (or re-run with `--lat`/`--lon`).

The projection is calibrated to the **current** `image18.png` world map: it is a
**Gall Stereographic** projection, fitted against Natural Earth coastlines to
sub-pixel accuracy (mean residual 0.64 px), so **no per-city overrides exist any
more** — every city is geocoded or given `--lat`/`--lon`. If the template's map
image ever changes it must be refitted: `references/engine-internals.md`. To
repair dots on decks that already exist, `references/deck-backfills.md`.

## Preflight

- [ ] `gws` CLI installed and authenticated (see the user's `gws-cli-access` memory).
      All Drive calls go through it.
- [ ] The exact display name confirmed with the user, spaces and all ("New York",
      not "NYC").
- [ ] The Luma slug confirmed. If they don't know, the default is fine — step 1
      reports whether the page is live.
- [ ] Working from a full checkout (`create_chapter.py` imports `lib/aaif_events`).

## Create a chapter

Run these in order. Nothing is created without `--write`.

- [ ] **1. Plan** — surfaces the slug, the Luma status, the resolved coordinates
      and any name collision. Writes nothing.
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py --city "New York"
      ```
      The slug must match `^[a-z0-9-]+$`; anything else aborts before the
      luma.com URL is built. (`--dry-run` is accepted as a no-op alias.)
      - Aborts with **"already exists"** → stop. The chapter is already there.
        (To fill in a partially-built one, go to step 4.)
      - Luma **NOT LIVE** → tell the user the page needs creating at
        `luma.com/aaif-<slug>`, or that the slug differs — re-run with `--slug`.
      - Check the printed `Coords:` line. Wrong city? Re-run with `--lat`/`--lon`.
- [ ] **2. Show the user the plan and get explicit approval.** The slug and the
      coordinates are the two things only a human can confirm.
- [ ] **3. Write** — clones TemplateCity → a new `<City>` folder under Chapters,
      then downloads, rebrands and re-uploads each `.pptx`/`.docx`/`.xlsx` in
      place, and moves the slide-5 map dot.
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py --city "New York" --write
      ```
- [ ] **4. Verify** (below). A run is not finished until this passes.
- [ ] **5. Hand off.** Report the new folder URL. A new chapter is not on the
      website until **`aaif-sync-chapters`** writes its feed row — that engine
      also enforces the 100-chapter cap, which `create_chapter` only warns about.

### Recovering a failed or partial run — `--resume`

A run that dies midway (a `gws` 403, template drift, network loss) leaves a
half-created folder, and a plain re-run aborts on the name collision. **Don't
trash the folder.**

- [ ] **a.** `create_chapter.py --city "New York" --write --resume` — enters the
      existing folder and clones/rebrands only what is missing. Resuming a
      fully-cloned chapter is a no-op. This is also the backfill path for a
      chapter missing part of the template (Luxembourg's 6 design assets were
      never cloned). Existing children are matched by their **rebranded or
      original** template name, so a survivor still under its original name is
      renamed in place (logged `~ old -> new`) and treated as present, never
      re-cloned as a duplicate.
- [ ] **b.** Read the `!! residual in existing file` lines. The run exits `2`
      with "N existing file(s) still carry source tokens". Files already in Drive
      are **never modified on faith**.
- [ ] **c.** Only if every flagged file is a **design asset**, re-run with
      `--write --resume --repair-existing`.
- [ ] **d.** Anything flagged under `*CRM.xlsx` or `*Tracker.docx` is fixed
      **by hand in Drive**. Those hold member data and are never rewritten, even
      under `--repair-existing`.

A present file whose *content* is corrupt but token-clean is still skipped, not
repaired — the residual check reads tokens, not validity.

A residual in a *freshly cloned* file is a different failure (exit `1`): the
template or the rebrand engine is broken. Fix that — don't resume.

## Verify

- [ ] The run printed **no `!! residual`** flags.
- [ ] Slide 5 ("THE NETWORK") of `Event Template/Slides.pptx` has the green dot
      on the right city. The `Slides.pptx` line shows `+map dot` when it moved.
      To check against a render:
      ```bash
      PYTHONPATH=lib python3 -c "
      from aaif_events.slides_export import render_slide_png
      render_slide_png('<Slides.pptx file id>', 'slide5.png', slide_index=4)
      "
      ```
- [ ] If the Luma page wasn't live, the user has been reminded to create it.
- [ ] Unit tests still pass after any engine edit:
      ```bash
      python3 ${CLAUDE_SKILL_DIR}/scripts/test_create_chapter.py
      ```

## Gotchas

- **Judge a map dot against the COASTLINE, not against an existing deck.** Decks
  placed before the Gall Stereographic fit sit ~8–15 px off the fitted position.
  A small delta against an old deck is the fit being *right*, not a regression.
  **Never "fix" the projection back toward a hand-placed dot.**
- **Every city now needs coordinates** — geocoded, or `--lat`/`--lon`. There is
  no per-city override table any more, so the four former override cities
  (Seoul, Sydney, Melbourne, Shanghai) hit the geocoder like everyone else.
- **Geocoding failure never fails the run.** The dot is left at San Francisco
  with a warning; fix slide 5 by hand or re-run with `--lat`/`--lon`.
- **A chapter is not born with its own agent art.** Cloning TemplateCity would
  hand it TemplateCity's agent. That is `upload_agents.py`'s job — see
  `references/design-system-sweep.md`.
- **`create_chapter` writes no Chapters List row**, so its 100-chapter cap check
  is an early warning, not a guarantee: three runs at 99 live chapters each read
  99, each pass, and the next sync refuses all three rows together.
- **The template must stay "clean"**: `San Francisco` contiguous (no run or
  paragraph splits) and the slug normalized to `aaif-sanfrancisco`.
- **The map-marker fill is shared state.** `create_chapter.GREEN` is `--spec-3`
  (`14B8B0`); `ooxml_style` maps the legacy `14964A` onto it and `MARKER_FILLS`
  still recognises the old value so an unswept deck stays findable. Keep the
  three in step.
- **Slug and chapter name are two identities.** A renamed chapter routinely keeps
  serving from its original Luma slug — see `references/rename-chapter.md`.

## References — load on demand

| Read this | When |
|---|---|
| `references/rename-chapter.md` | Renaming an existing chapter, or repairing a half-finished rename. |
| `references/deck-backfills.md` | Sweeping the existing estate: map dots after a refit, the host footer after a design change, the projects roster when AAIF takes on a project. |
| `references/design-system-sweep.md` | Auditing or fixing estate styling, contrast, background plates, or agent art. |
| `references/engine-internals.md` | Before editing the rebrand engine or the projection, or when the template's map image changes and needs a refit. |
