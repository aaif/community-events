# Conforming the estate to the design system — `restyle_design_system.py`

> Load this when asked to audit or fix the estate's styling, contrast or background
> plates, or to give chapters their agent art. Not needed to create a chapter.

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
