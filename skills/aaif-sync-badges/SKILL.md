---
name: aaif-sync-badges
description: Sync AAIF chapter organizer badges (SVG + PNG) into each chapter's own Drive folder — generate missing badges for chapters that don't have them yet, and optionally regenerate all of them after a design change. Use when asked to add/sync/update/regenerate chapter organizer badges.
argument-hint: '[--chapter <City Name>] [--regenerate] [--write]'
---

# Sync AAIF Chapter Badges

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

Keeps every chapter's own **`Badges/`** Drive subfolder (created directly inside
the chapter's folder under the **Chapters** Drive folder, id
`1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx` — same one `aaif-create-chapter` clones from
— alongside its existing `Icons/`, `Event Templates (…)/`, etc.) in sync. Each
chapter's `Badges/` folder holds **two badge styles**, 6 files total:

```
organizer_badge_<slug>_colour.svg          \
organizer_badge_<slug>_white.svg            |  style 1 — scripts/make_badges.py
organizer_badge_<slug>_colour_1000.png      |  (self-contained, hand-tuned palette)
organizer_badge_<slug>_white_1000.png      /

organizer_badge_<slug>_agent.svg            \  style 2 — scripts/make_agent_badge.py
organizer_badge_<slug>_agent_1000.png      /   (real AAIF design-system tokens
                                                 + the chapter's own agent mascot)
```

`<slug>` is `make_badges.slugify(city name)` — e.g. "Mexico City" → `mexico_city`,
"Delhi NCR" → `delhi_ncr`.

## The two badge styles

- **`make_badges.py`** — a ring-and-arc badge in its own hand-picked orange/ink/
  lilac palette and a system font stack (`Liberation Sans, DejaVu Sans`), rendered
  to PNG via `cairosvg`. Self-contained (no `lib` import), per this repo's usual
  skill-script convention. This was the original design and is deliberately kept
  as-is — it does not pull from the AAIF design system.
- **`make_agent_badge.py`** — draws from the real design system instead: Instrument
  Sans embedded exactly as `report_style.font_css()` does for every HTML report,
  ink/paper/hairline tokens, and the chapter's own **agent mascot**
  (`agent_art.chapter_scene()` / `agent_art.agent()` — the same deterministic
  per-chapter colour already used for that chapter's `Icons/` folder and decks, so
  the badge and the chapter's other AAIF-generated art always agree). Rendered via
  **headless Chrome** — DESIGN.md's one allowed SVG→PNG renderer — not `cairosvg`.
  This intentionally takes the `lib/aaif_events` coupling AGENTS.md otherwise asks
  skill scripts to avoid, for the same reason `restyle_design_system.py` does: the
  whole point of this variant is to be provably on-system.

Prereqs: the `gws` CLI installed and authenticated (see the user's
`gws-cli-access` memory); `cairosvg` importable for style 1
(`python3 -m pip install cairosvg`); a Chrome/Chromium install for style 2. Both
are imported lazily (only when actually rendering a PNG), so a plan-only run
needs neither Drive write access nor either renderer, and a chapter only missing
one style's files never invokes the other style's renderer.

## Usage (engine: `scripts/sync_badges.py`)

```bash
# Plan (default) — nothing is created/uploaded, just reported:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py

# Apply — create missing chapter subfolders and upload missing files:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --write

# Regenerate every file (after a design change) and overwrite what's already there:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --write --regenerate

# One chapter only (Drive chapter-folder name, case-insensitive substring):
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --chapter "Mexico City" --write
```

## What it does and doesn't touch

- **Source of truth for the chapter list** is the live "Chapters" Drive
  folder, not a hardcoded list — a chapter created by `aaif-create-chapter`
  shows up here on the next run with no code change. `TemplateCity` (the
  clone source, not a real chapter) is excluded.
- **Additive only.** A chapter's `Badges/` subfolder or file already present is
  left alone unless `--regenerate` is passed — badges are never deleted, and a
  file that already exists is *updated in place* (same Drive file id), never
  duplicated.
- A collision — two chapter folder names slugifying to the same value — aborts
  the whole run rather than silently dropping one of them; rename one of the
  chapters in Drive first.

## Migrating from the old shared chapter-badges folder

Badges used to live in one shared parent folder (`<parent>/<slug>/…`) instead
of inside each chapter. `scripts/migrate_legacy_badges.py` moves files out of
that old layout into the per-chapter `Badges/` folder this skill now targets —
a Drive **reparent** (`addParents`/`removeParents`), not a re-upload, so file
ids and revision history survive. Plan-only by default:

```bash
# Plan — nothing is moved:
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_legacy_badges.py

# Apply the moves:
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_legacy_badges.py --write

# Also trash (Drive trash, recoverable) a legacy folder left empty by the move:
python3 ${CLAUDE_SKILL_DIR}/scripts/migrate_legacy_badges.py --write --trash-empty
```

`--trash-empty` is resilient per folder: one folder's trash failing (e.g. a
Drive permissions mismatch on that specific folder — differing ownership or
sharing from its siblings) is reported and skipped, never lets an exception
stop the rest from being cleaned up. A folder that can't be trashed this way
is harmless either way — it's already empty, and every file it held has
already been safely moved — and can be deleted by hand in Drive by an owner,
or re-run later if permissions change. If many folders in a row fail with the
*same* error, the script stops instead of continuing to fail identically
through every remaining folder — that pattern means something systemic (a
stale `gws` credential, a broken API call), not a per-folder quirk, and is
worth fixing before re-running rather than working around.

A file already present at the destination (by name) is left alone and
reported, never overwritten by the migration — run `sync_badges.py
--regenerate` afterward if the content should also be refreshed. A legacy
folder matching no canonical chapter (a renamed/retired chapter, or a stray
item) is reported and never touched.

## Procedure

1. **Plan first** (the default) to see what's missing:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py
   ```
   Review the `+ create folder` / `upload` / `overwrite` lines before writing
   anything.
2. **Apply** with `--write` once the plan looks right.
3. New badges land as `image/svg+xml` and `image/png` files inside the
   chapter's own `Badges/` subfolder; re-running afterward reports "Up to date".

## Notes

- Badges are built into a private `tempfile.mkdtemp()` directory that is
  deleted at the end of the run — nothing lands in the repo working tree, so
  there's no `.gitignore` entry to add.
- To change either badge design, edit the corresponding script
  (`make_badges.py` or `make_agent_badge.py`) directly, then run `--write
  --regenerate` to push the new design to every chapter.
- Both chapter-name display strings that reach a generated SVG are
  XML-escaped before use: the Drive folder name is organizer-editable
  (any of a chapter's accepted organizers can rename their own chapter
  folder), so this is untrusted input, not just untrusted-shaped text.
