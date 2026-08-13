---
name: aaif-create-chapter
description: Create a new AAIF city chapter in the "Chapters" Google Drive by cloning TemplateCity and rebranding all assets. Use when asked to add/launch/set up a new AAIF city, chapter, or location.
argument-hint: '<City Name> [--slug <lumaslug>] [--lat <deg> --lon <deg>]'
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
| `aaif-sanfrancisco` / `AAIF-SANFRANCISCO` (Luma slug, incl. hyperlink targets) | `aaif-<slug>` / `AAIF-<SLUG>` | see slug rules below |

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
sub-pixel accuracy (mean residual 0.64 px), so no per-city overrides are needed
anywhere. If the template's map image ever changes, refit (see below).

Two consequences of dropping the override table:

- **Every city now needs coordinates** — geocoding, or `--lat`/`--lon`. The
  former override cities (Seoul, Sydney, Melbourne, Shanghai) used to be
  placeable with no network at all; now they hit the geocoder like everyone
  else, and if it is down the fallback above applies to them too.
- **Decks placed under the old projection sit a few px off the fitted position**
  (the pre-Stuttgart chapters, and the template's own hand-placed SF dot, ~9 px).
  A small dot delta against an old deck is the fit being *right*, not a
  regression — see the validate note below.

## Procedure

1. **Confirm the city name and slug with the user.** Ask for the exact display
   name (with spaces, e.g. "New York") and whether the Luma page exists / what
   its slug is. If they don't know, the default slug is fine — the script will
   tell you if it's not live.

2. **Dry run first** to surface the slug, Luma status, and any name collision:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
       --city "New York" --dry-run
   ```
   - If it aborts with "already exists", stop — the chapter is already there.
   - If Luma shows NOT LIVE, tell the user the page needs creating at
     `luma.com/aaif-<slug>` (or that the slug differs — re-run with `--slug`).

3. **Create the chapter:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
       --city "New York"            # add --slug <x> if overriding
   ```
   The script clones TemplateCity → a new `<City>` folder under Chapters, then
   downloads, rebrands, and re-uploads each `.pptx/.docx/.xlsx` in place. It
   prints a tree and flags any file with `!! residual` tokens.

4. **Verify.** Confirm the run printed no `!! residual` flags and report the new
   folder URL to the user. If the Luma page wasn't live, remind them to create it.
   Open slide 5 ("THE NETWORK") of `Event Template/Slides.pptx` and confirm the
   green dot sits on the correct city (the `Slides.pptx` line shows `+map dot` when
   it was moved). A misplaced dot means the coordinates were wrong (re-run with
   `--lat`/`--lon`) or the map art changed (refit the projection — see below). To
   check against a render, export slide 5 to PNG via the Slides API (see the
   tooling rule at the top of this file):
   ```bash
   PYTHONPATH=lib python3 -c "
   from aaif_events.slides_export import render_slide_png
   render_slide_png('<Slides.pptx file id>', 'slide5.png', slide_index=4)
   "
   ```

## How it works / maintenance

`scripts/create_chapter.py` is the engine. It rebrands at the paragraph level
(concatenate the text runs, transform, write back into the first run) so it is
robust to OOXML run-splitting. The `SF`-abbreviation casing is decided by the
surrounding words. The Drive layer uses `gws` (`files.copy`, `create`, `get`,
`update`).

The slide-5 map dot is placed by `reposition_map_marker` using a lat/lon → pixel
**Gall Stereographic** projection (`lon2x` linear in longitude; `lat2y` linear in
`(1 + √2/2)·tan(lat/2)`), fitted 2026-07-30 against Natural Earth 110m coastlines
composited over `image18.png` (mean residual 0.64 px). **If the template's map
image changes, refit**: composite the transparent PNG over `#F6F5F1`, build a
distance transform of the drawn pixels, and optimize (scale_x, scale_y, offset_x,
offset_y, central meridian) per candidate projection family by Nelder-Mead to
minimise the mean distance from projected coastline vertices (lat > -60; the map
omits Antarctica) to the nearest drawn pixel. `scripts/test_create_chapter.py`
covers the San Francisco calibration lock, the label offset, monotonicity/canvas
bounds, and the 2-shapes-or-raise guard.

To validate the engine after any edit, rebrand a throwaway copy of the template
and diff it against an existing chapter (the canonical end-state):
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
    --city "Los Angeles" --lat 34.05 --lon -118.24 --rebrand-local /path/to/template-copy
# then compare paragraph text against the real Los Angeles chapter, and open
# slide 5 of the rebranded Slides.pptx to confirm the dot moved (+map dot).
# EXPECTED: the dot sits ~8-15 px from the existing chapter's — old decks were
# placed by the pre-2026-07-30 anchors/overrides projection. Judge the dot
# against the COASTLINE, not the old deck, and never "fix" the projection back
# toward a hand-placed dot.
python3 ${CLAUDE_SKILL_DIR}/scripts/test_create_chapter.py   # unit tests
```

Constants (Chapters parent id, TemplateCity id) live at the top of the script.
The template must stay "clean": `San Francisco` contiguous (no run/paragraph
splits) and the slug normalized to `aaif-sanfrancisco`. If a future template edit
re-introduces a split, the paragraph-level engine still handles it, but the big
stacked title on Carousel slide 2 is intentionally a single adaptive line.
