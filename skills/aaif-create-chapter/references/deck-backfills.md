# Estate backfills — map dots, host footer, projects roster

> Load this when sweeping an existing estate rather than creating a chapter: after a
> projection refit, after a design change to the footer, or when the projects list
> changes. All three are plan-by-default and have **no undo** beyond Drive revision
> history. Read a plan run first, every time.

## Map dots (`backfill_map_dots.py`)

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

## Host footer (`backfill_host_footer.py`)

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

## Projects roster (`backfill_projects.py`)

`scripts/backfill_projects.py` brings the **`THE PROJECTS` roster** on each
deck's About slide up to the projects AAIF actually hosts. The decks were drawn
against four (`MCP · goose · AGENTS.md · agentgateway`); aaif.io/projects now
lists six, with **A2A** and **Agent Router** added. The roster lives in
`PROJECTS` at the top of the script — when the foundation takes on another
project, edit that tuple and sweep again. Nothing watches for drift on its own,
and the durable fix is the TemplateCity edit the sweep makes: the script exists
to bring the copies already in Drive up to it.

The roster is found **structurally** — the `THE PROJECTS` eyebrow, then the text
shape directly below it on the same left edge — so a deck an organizer had
already half-corrected is still found. Two guards sit on top of that:

- A roster naming anything that is not one of the projects is a chapter's **own
  wording**, so it is left untouched and reported. Adding a project therefore
  never overwrites a hand-written line.
- Six names do not fit the box four were drawn in. The box is **widened** to fit
  but never past a gutter in front of whatever is to its right (on the About
  slide, the `OPEN / BY DEFAULT` stat), so it cannot collide. If even the full
  width is too narrow the type steps down; and a roster that still overruns —
  because it is already at the smallest size the script will use, or because its
  run declares no size to rewrite — is reported as **overflowing** rather than
  quietly written. A shrunk or colliding roster is a design decision an operator
  should see.

Scope and the estate-coverage lines of the `ATTENTION` block come from
`scripts/deck_estate.py`, shared with `backfill_host_footer.py`: templates only,
across all chapters, the online series and the shared Templates folder. A run
that never reaches **TemplateCity** — or that reaches it and finds no roster in
it — says so and exits non-zero, because new chapters would otherwise still be
minted with the old list. So does a run where an unusual share of templates
showed no roster at all: the estate is one cloned design, so that is the matcher
having stopped matching, not good news.

Exit codes are `0` (nothing left to do), `1` (a plan run with work outstanding)
and `2` (something a human must read: a failure, a skipped roster, an overflow,
or coverage the scan could not achieve). A deck already naming the six is not
re-uploaded, so **re-running is a no-op**; a plan run does not repack the decks
it would change, which also means it does not exercise the repack — only
`--write` does.

```bash
# Plan (default) — show every template's roster, writes nothing:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_projects.py

# Apply across the estate:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_projects.py --write

# One chapter (matches anywhere in the Drive path, case-insensitive):
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_projects.py \
    --chapter "New York City" --write

# Test the XML engine on a local file, no Drive at all:
python3 ${CLAUDE_SKILL_DIR}/scripts/backfill_projects.py \
    --rewrite-local ./Slides.pptx
```

There is **no undo** beyond Drive's revision history. Read a plan run first.
