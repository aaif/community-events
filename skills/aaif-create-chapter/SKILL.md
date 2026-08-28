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
| `aaif-sanfrancisco` / `AAIF-SANFRANCISCO` (Luma slug, incl. hyperlink targets) | `aaif-<slug>` / `AAIF-<SLUG>` | see slug rules below |
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
sub-pixel accuracy (mean residual 0.64 px), so no per-city overrides are needed
anywhere. If the template's map image ever changes, refit (see below).

Two consequences of dropping the override table:

- **Every city now needs coordinates** — geocoding, or `--lat`/`--lon`. The
  former override cities (Seoul, Sydney, Melbourne, Shanghai) used to be
  placeable with no network at all; now they hit the geocoder like everyone
  else, and if it is down the fallback above applies to them too.
- **Decks placed under the old projection sit a few px off the fitted position**
  (the template's own hand-placed SF dot, ~9 px). A small dot delta against an old
  deck is the fit being *right*, not a regression — see the validate note below.
  For chapter decks, `backfill_map_dots.py` below reports and repairs this.

## Backfilling existing decks

`scripts/backfill_map_dots.py` re-places the markers in chapter decks that
already exist. Decks created before the Gall Stereographic fit shipped (PR #20)
carry their dot from the old placement, which was wrong in two distinct ways:
the **projection** was ~9% too wide with a ~20 px offset (Tokyo landed in the
Pacific), and the four `PIXEL_OVERRIDES` cities bypassed the projection entirely
(Shanghai landed on Honshu because that hand-tuned **override** was wrong — not
evidence about the formula).

Run the plan (the default) to find out where the estate actually stands: an
already-corrected estate reports every chapter as `already correct`, and that
report — not a number written down here — is the authoritative answer. Reach for
this after a refit, or to check the estate.

Coordinates come from the **Chapters & Teams** sheet's `Generated Geolocation`
column, joined to Drive by the folder URL in `Chapter Folder` — not from the
folder name. That is what the website feed already draws, so the deck and the
site agree; it is also the only source that maps a folder to its real city, and
a folder's name can lag that city (they have been renamed before). A Drive
folder with no sheet row is reported and skipped, never guessed at.

```bash
# Plan (default) — per-chapter drift in pixels, writes nothing:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_map_dots.py

# Apply:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_map_dots.py --write

# One chapter, coordinates given rather than read from the sheet:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_map_dots.py \
    --city Shanghai --lat 31.2304 --lon 121.4737 --write
```

`--city` takes the Drive folder's **name** or the city the sheet gives that
folder, so both `--city Scotland` and `--city Edinburgh` reach the same chapter.

A deck already within `--tolerance` (default 1 px — a pixel or less is rounding,
not misplacement) is left untouched, so **re-running is a no-op on decks that are
already correct**. Drift is the worse of the dot and its label, so a refit that
moves only the label is still caught. A deck whose slide 5 does not hold exactly
one green dot and one green label is reported with a reason and skipped, never
rewritten on a guess — and a run that could not evaluate part of the estate exits
non-zero rather than reading as a finished backfill.

There is **no undo** beyond Drive's revision history: `--write` replaces up to 80
production decks in place. Read a plan run first.

The script imports the projection and the OOXML surgery from `create_chapter.py`
— do not reimplement either of those inside the backfill script.

## Backfilling the host footer

`scripts/backfill_host_footer.py` reworks the **"HOSTED BY / WITH" logo footer**
in the event templates. The footer used to draw each logo as a bordered, filled
rounded-rect button holding centred bold text; the current design has no boxes,
puts the **AAIF lockup** in the host slot, and leaves the remaining slots as
muted `LOGO 1`, `LOGO 2`, … placeholders, with the row packed left on one even
gap.

The lockup is built from the mark image the slide **already embeds for its own
header**, plus the wordmark set in Space Grotesk bold. No media and no
relationship is added, so the footer lockup cannot drift from the header's.

Three cases the script keeps apart, and they are not interchangeable: an
**unfilled slot** (`MEMBER LOGO`, `HOST VENUE CO.`, `VENUE NAME`, `SPONSOR`, or
anything containing the word `LOGO`) is renumbered `LOGO n` and muted; a **real
name** (the carousel's founding-member grid) keeps its text and ink always, and
keeps its position too unless it sits in the host's own row, which is the one row
re-packed; the old **`AAIF · SF` badge** beside the host is dropped, because the
lockup now says the same thing. The script's
own docstring explains why each rule is drawn where it is.

Scope is **templates**, not the copies organizers have already made for a given
event: every `.pptx` under a folder matching `Event Templates…` / `Event Name`,
across all chapters, the online series, and the shared Templates folder. That set
includes **TemplateCity** — the folder `create_chapter.py` clones for every new
chapter — so a full sweep is what stops new chapters being minted on the old
footer. A full-estate run that never reaches TemplateCity, that finds a chapter
folder contributing no template, that sees a `.pptx`-bearing folder the name
regex declined (all three are what a rename looks like), or that removes a
file's boxes without drawing its lockup, prints an `ATTENTION` block and exits
non-zero rather than reading as finished. A `--chapter` run checks only that
last one — it is scoped by design and cannot speak for the estate. A file
whose footer has already been reworked has no chips left to find, so it is not
re-uploaded and **re-running is a no-op**.

```bash
# Plan (default) — list every template and its footer, writes nothing:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_host_footer.py

# Apply across the estate:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_host_footer.py --write

# One chapter (matches anywhere in the Drive path, case-insensitive):
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_host_footer.py \
    --chapter "New York City" --write

# Test the XML engine on a local file, no Drive at all:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_host_footer.py \
    --rework-local ./Event-Hero-Square.pptx
```

There is **no undo** beyond Drive's revision history, and `--write` replaces
every template the scan finds, in place — a plan run prints that count. Read one
first.

## Conforming the estate to the design system

`scripts/restyle_design_system.py` is the sweep that keeps every deck, tracker
and CRM in the estate on the AAIF design system. The rules live in
`lib/aaif_events/ooxml_style.py` — shared with the repo's own CI check, so what
the sweep writes and what the tests assert cannot drift apart — and the
background plates in `lib/aaif_events/agent_art.py`.

Scope is **templates, not events**, and being *in* a template folder is not
enough. The sweep only touches files whose NAME is a template — the set in
`TEMPLATE_FILES` plus each chapter's own `<City> CRM.xlsx` — inside a chapter /
online-series / shared-Templates folder or its `Event Templates (Copy for Each
Event)`, `Event Template`, `Event Name` and `Banners (…)` subfolders.

Organizers park their own work in those folders: a dated event deck, a "Copy
of …", a personal draft. Rebranding someone's finished event deck is not this
script's business, and the first estate run swept eleven such files before the
allowlist existed (they were restored from the archive). Anything in a template
folder that is not a template is **skipped and named in the report**, so a
genuinely new template gets noticed rather than silently missed — add it to
`TEMPLATE_FILES` when that happens. Copies deeper in the tree are counted too,
so "out of scope" never reads as "missed".

Only a CRM's *styling* is ever rewritten — its `xl/styles.xml` and workbook
theme. Cell values live in `xl/worksheets/` and `xl/sharedStrings.xml`, which
`restyle_part` never opens.

A workbook is **SpreadsheetML, not DrawingML**: fonts are `<font><name
val="Calibri"/></font>`, fills are `<patternFill><fgColor rgb="FF1E2761"/>`,
and colours are ARGB (eight digits, alpha first). Handing those to the deck
pass changes nothing silently, which is exactly what happened until this was
written — every CRM in the estate audited "clean" while full of Calibri and
navy.

It is read-only by default, and **archives every pre-change file** to
`./backups/restyle-<UTC>/` before uploading it — `--write` refuses to start if
that directory cannot be created. A re-run over a conformant estate uploads
nothing.

An archive entry is **never overwritten**: the earliest copy is the pristine
one, so a second run sharing a `--backup-dir` keeps it. Without that, the second
run archives the already-restyled file over the original and the archive is
silently useless as a rollback for exactly the files that needed two passes.

```bash
# Audit: what is still off the design system? (exit 1 if anything is)
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py --check

# Plan (default), then apply:
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py --write

# One folder. Matched as a whole path SEGMENT, so "Templates" selects the
# shared folder and not every chapter's "Event Templates (…)" subfolder:
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py \
    --chapter TemplateCity --write

# ---- anything that needs generated art -------------------------------
# Build it into a PRIVATE directory FIRST. A fixed /tmp path is world-writable
# and predictable, so on a shared host someone else can pre-create it and choose
# the bytes that then land in all 83 chapter decks.
ART=$(mktemp -d)
python3 -c "import sys; sys.path.insert(0, 'lib'); \
    from aaif_events import agent_art as a; \
    a.build('$ART'); a.build_logos('$ART')"

# Give the two hero decks their background plates (idempotent):
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py \
    --plates "$ART" --write

# Retire the hand-made plate the decks were built with, replacing every
# background this toolkit did not generate with the AAIF soft plate. Repairs
# the text against the NEW plate in the same pass, which is why --fix-contrast
# rides along:
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py \
    --retire-plates --fix-contrast --plates "$ART" --write

# Audit TEXT LEGIBILITY: every run below WCAG AA against what is behind it.
# Catches what a token check cannot — black-on-black is two correct tokens.
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py --contrast

# Repair it, by measurement — a slide is kept only when at least one run is
# materially rescued and none crosses from passing to failing (or from readable
# into the invisible band):
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py \
    --fix-contrast --write

# Give every chapter its own agent, the ten generic ones and the AAIF logos, in
# an Icons/ folder. build_agents needs the chapter NAMES, so read them from the
# same estate walk the sweep uses. NOT done by create_chapter: cloning
# TemplateCity would hand a new chapter TemplateCity's agent, not its own.
python3 -c "import sys; sys.path.insert(0, 'lib'); \
    sys.path.insert(0, '${CLAUDE_SKILL_DIR}/scripts'); \
    import create_chapter as cc, restyle_design_system as rd; \
    from aaif_events import agent_art as a; \
    names=[c['name'] for k in cc.list_children(rd.COMMUNITY_ROOT) \
           if k['name']==rd.CHAPTERS_FOLDER \
           for c in cc.list_children(k['id']) if c['mimeType']==cc.FOLDER]; \
    a.build_agents('$ART', names)"
python3 ${CLAUDE_SKILL_DIR}/scripts/upload_agents.py --art "$ART" --write

# Run the engine on a local file, no Drive at all:
python3 ${CLAUDE_SKILL_DIR}/scripts/restyle_design_system.py \
    --restyle-local ./Slides.pptx
```

A full run asserts it reached **TemplateCity**, **TemplateSeries** and the
shared **Templates** folder and exits non-zero if it did not: those three mint
everything else, so missing one means every chapter created afterwards is born
off-brand again.

**The map-marker fill is shared state.** `create_chapter.GREEN` is `--spec-3`
(`14B8B0`) and `ooxml_style` maps the legacy `14964A` onto it. `MARKER_FILLS`
still recognises the old value so a deck the sweep has not reached yet stays
findable — keep the three in step, and a test pins that `GREEN` equals the
design system's `--spec-3`.

## Procedure

1. **Confirm the city name and slug with the user.** Ask for the exact display
   name (with spaces, e.g. "New York") and whether the Luma page exists / what
   its slug is. If they don't know, the default slug is fine — the script will
   tell you if it's not live.

2. **Plan first** (the default — nothing is created without `--write`) to surface
   the slug, Luma status, and any name collision:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
       --city "New York"
   ```
   (`--dry-run` is still accepted as a no-op alias.) The slug must match
   `^[a-z0-9-]+$`; anything else aborts before the luma.com URL is built.
   - If it aborts with "already exists", stop — the chapter is already there.
   - If Luma shows NOT LIVE, tell the user the page needs creating at
     `luma.com/aaif-<slug>` (or that the slug differs — re-run with `--slug`).

3. **Create the chapter** — only after the user confirms the plan:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
       --city "New York" --write    # add --slug <x> if overriding
   ```
   The script clones TemplateCity → a new `<City>` folder under Chapters, then
   downloads, rebrands, and re-uploads each `.pptx/.docx/.xlsx` in place. It
   prints a tree and flags any file with `!! residual` tokens.

   **Recovering a failed or partial run — `--resume`.** If a run dies midway
   (a `gws` 403, template drift raising in the rebrand engine, network loss),
   the half-created chapter folder is already in Drive and a plain re-run aborts
   on the name collision. Don't trash the folder — re-run with `--resume`
   (which, like any mutation, requires `--write`):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py --city "New York" --write --resume
   ```
   It enters the existing folder and clones/rebrands only what's missing — so
   resuming a fully-cloned chapter is a no-op. The same flag is the backfill
   path when a chapter is missing part of the template (e.g. Luxembourg, whose
   6 design assets were never cloned). Two safeguards make the skip decision
   trustworthy:
   - Existing children are matched by their **rebranded or original** template
     name. A survivor still under its original name (a folder part-cloned before
     the rename step existed, or a hand copy) is renamed in place (logged `~ old
     -> new`) and treated as present — never re-cloned as a duplicate.
   - Every skipped Office file is **residual-checked**, because the likeliest
     crash state is copied-but-never-rebranded. A clean file logs `exists,
     skipped — residual-checked clean`. A dirty one is **reported, not
     rewritten**: it logs `!! residual in existing file <name>` and fails the
     run. Files already in Drive are never modified on faith — to have the
     script repair them in place (download, rebrand, re-upload, dot moved), add
     the explicit `--repair-existing` flag. Even then, **`*CRM.xlsx` and
     `*Tracker.docx` are never rewritten** — they hold member data once a
     chapter is live — and are always reported for a hand fix.

   A present file whose *content* is corrupt but token-clean is still skipped,
   not repaired. For a Luxembourg-style backfill (design assets missing, CRM and
   tracker already in use), the sequence is: `--write --resume` to clone the
   missing items; read the `!! residual in existing file` lines (the run ends
   with exit 2 and "N existing file(s) still carry source tokens"); re-run with
   `--write --resume --repair-existing` only if the flagged files are design
   assets; anything flagged under `*CRM.xlsx` / `*Tracker.docx` is fixed by hand
   in Drive. A residual in a *freshly cloned* file is a different failure (exit
   1): the template or the rebrand engine is broken — fix that, don't resume.

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
# --rebrand-local requires --lat/--lon: local mode is fully offline and refuses
# to geocode, so the coordinates must be passed explicitly.
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
