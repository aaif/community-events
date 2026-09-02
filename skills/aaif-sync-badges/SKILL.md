---
name: aaif-sync-badges
description: Sync AAIF chapter organizer badges (SVG + PNG) into the chapter-badges Google Drive folder — generate missing badges for chapters that don't have them yet, and optionally regenerate all of them after a design change. Use when asked to add/sync/update/regenerate chapter organizer badges.
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

Keeps the **chapter-badges** Drive folder (id `1ViKjLZh-4KrMBVihOGQyAL2SVsXcI3B9`)
in sync with the **Chapters** Drive folder (id `1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx`,
same one `aaif-create-chapter` clones from). Each chapter gets a subfolder named
by its **slug** (`make_badges.slugify` — e.g. "Mexico City" → `mexico_city`,
"Delhi NCR" → `delhi_ncr`), holding 4 files:

```
organizer_badge_<slug>_colour.svg
organizer_badge_<slug>_white.svg
organizer_badge_<slug>_colour_1000.png
organizer_badge_<slug>_white_1000.png
```

Badges are generated locally by `scripts/make_badges.py` (a ring-and-arc AAIF
organizer badge, city name on the top arc, "AGENTIC AI FOUNDATION" on the
bottom arc, "ORGANIZER" pill) and rendered to PNG via `cairosvg`.

Prereqs: the `gws` CLI must be installed and authenticated (see the user's
`gws-cli-access` memory), and `cairosvg` must be importable
(`python3 -m pip install cairosvg`, or run inside a venv that has it — the
engine imports it lazily, only when actually generating a PNG, so a plan-only
run needs neither Drive write access nor `cairosvg`).

## Usage (engine: `scripts/sync_badges.py`)

```bash
# Plan (default) — nothing is created/uploaded, just reported:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py

# Apply — create missing chapter subfolders and upload missing files:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --write

# Regenerate every file (after a design change to make_badges.py) and
# overwrite what's already there:
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --write --regenerate

# One chapter only (Drive chapter-folder name, case-insensitive substring):
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py --chapter "Mexico City" --write
```

## What it does and doesn't touch

- **Source of truth for the chapter list** is the live "Chapters" Drive
  folder, not a hardcoded list — a chapter created by `aaif-create-chapter`
  shows up here on the next run with no code change. `TemplateCity` (the
  clone source, not a real chapter) is excluded.
- **Additive only.** A chapter subfolder or file already present is left
  alone unless `--regenerate` is passed — badges are never deleted, and a
  file that already exists is *updated in place* (same Drive file id), never
  duplicated.
- **Badge folders with no matching chapter** (a chapter renamed or retired in
  Drive after its badge folder was created) are reported as orphans and never
  touched — deleting or renaming them is a manual call.
- **Non-folder items** directly under the chapter-badges parent (stray files
  like a `.DS_Store`) are reported and ignored.
- A collision — two chapter folder names slugifying to the same value — aborts
  the whole run rather than silently dropping one of them; rename one of the
  chapters in Drive first.

## Procedure

1. **Plan first** (the default) to see what's missing:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/sync_badges.py
   ```
   Review the `+ create folder` / `upload` / `overwrite` lines, and any
   reported orphans, before writing anything.
2. **Apply** with `--write` once the plan looks right.
3. New badges land as `image/svg+xml` and `image/png` files inside the
   chapter's slug subfolder; re-running afterward reports "Up to date".

## Notes

- The generator (`make_badges.py`) is a verbatim copy of the AAIF badge
  script — kept self-contained here rather than imported from `lib`, per this
  repo's usual skill-script convention, so this skill still runs standalone.
- Badges are built into a private `tempfile.mkdtemp()` directory that is
  deleted at the end of the run — nothing lands in the repo working tree, so
  there's no `.gitignore` entry to add.
- To change the badge design (colours, layout, text), edit
  `scripts/make_badges.py` directly, then run `--write --regenerate` to push
  the new design to every chapter.
