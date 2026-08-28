# Design

Everything this repo generates for a human to look at — the Slack audit PDFs,
any HTML report a skill writes — is drawn with the **AAIF design system**.

## The source of truth

| File | What it is |
|---|---|
| `design/aaif-design-system.html` | The design system itself. Open it in a browser. Self-extracting bundle: tokens, the agent motif, component specs, and a worked one-pager layout. |
| `design/aaif-tokens.css` | **Generated.** The `:root` token block lifted out of the bundle so `report_style.py` can read it and prepend it verbatim. Never hand-edit. |
| `scripts/extract_design_tokens.py` | Regenerates the above. `--check` fails CI when it is stale. |
| `assets/fonts/` | Instrument Sans (OFL), the system's one typeface, embedded in every report. |

Replacing the design system is: drop the new bundle at
`design/aaif-design-system.html`, run `python3 scripts/extract_design_tokens.py`,
re-run whatever generates reports. Nothing else should need touching — that is
the whole point of the seam.

## What the system actually says

Worth stating, because the obvious guesses are wrong:

- **The accent is black.** `--accent: var(--ink)` — "primary actions are black".
  The spectrum hues (`--spec-1` … `--spec-10`) are accents *within* a surface,
  one hue leading at a time. Coral is `--spec-8`, not the brand colour. An
  earlier pass at the report styling made coral the accent and the output looked
  like a different organisation's.
- **One typeface.** Instrument Sans at 400/500/600/700, for display and UI
  alike. There is no second display face. Mono is a system stack, used only for
  metadata and eyebrows — never for body copy.
- **Headings are weight 500**, tracking `-0.02em`, line-height 1.05–1.15. Not
  600, not bold.
- **Flat.** No drop shadows anywhere. Depth comes from 1px hairlines, surface
  tinting (`--paper` → `--paper-2` → `--paper-3`) and inset strokes.
- **Two first-class surfaces, and no dark mode.** White editorial and black
  plate — always know which one a component is drawn on, because the *designer*
  picks it per component. Reports are editorial, so the page is white and stays
  white; black appears only where something is deliberately drawn on it (the
  closing band), and those rules carry their own light-on-dark colours. There
  is no `prefers-color-scheme` block anywhere and there must not be one: a
  light palette plus an inverted twin is a different design language wearing
  AAIF's tokens, and it shipped here once already.
- **The warm off-white is not a background.** `--paper-2` is occasional section
  banding. The page ground is `--paper`; tinted panels are `--paper-3`.
- **The agent motif** (the bot built from the mark's primitives) is for empty
  states, loading, event graphics and dividers — **never** governance, board or
  press material. Audit reports are closer to the latter, so they do not use it.

## OOXML: decks, trackers, and the Drive estate

The same rule, one format further out. `lib/aaif_events/ooxml_style.py` is the
only module that knows an AAIF token name inside a `.pptx` / `.docx` / `.xlsx`,
and **skill scripts never write a font name or a hex of their own** — they call
`restyle_part` and add a rule there when the vocabulary is missing.

Three things about it are worth knowing before editing:

- **Colour is role-aware.** The same hex means different things in different
  slots. In the event trackers `1e2761` is a table-header *fill* — which becomes
  the black plate — and forty characters later in the same run of XML it is a
  cell *border*, which becomes a hairline. Every rule in `_ROLE_MAP` therefore
  names a token per role (`fill` / `stroke` / `text`), and a test asserts all
  three are present and all of them resolve against `design/aaif-tokens.css`.
- **The rewrite is minimal-diff.** Only the colour's `val=`/`w:fill=` and the
  font's `typeface=` change; every other byte of every part is copied through.
  That is what lets these files keep their embedded fonts, their
  `mc:AlternateContent` fallbacks and their relationship ids, none of which
  survive an XML round-trip. It also makes "did the bytes change" a correct
  upload test, so re-running the sweep over a clean estate uploads nothing.
- **A colour with no rule is left alone and reported.** `audit()` lists what is
  still off-system; silence would let new drift through unnoticed. It audits
  exactly the parts `restyle_part` can rewrite — reporting drift somewhere the
  sweep never touches would be a finding nobody can act on.
- **A workbook is not a deck.** `xl/styles.xml` is SpreadsheetML with ARGB
  colours, and needs its own pass; running the DrawingML one over it changes
  nothing and reports clean.

### Legibility is a separate check from conformance

A token check cannot catch unreadable text, because **black-on-black is two
correct AAIF tokens in the wrong pairing**. That shipped: the black-plate title
slide set its eyebrow, its subtitle and the wordmark of its host lockup in the
light ink ramp, and the slide looked empty rather than wrong.

`lib/aaif_events/contrast.py` measures pairings. For every run it resolves the
colour actually drawn and the colour actually behind it — shape fill, then the
slide's `<p:bg>`, then the layout's, then the master's — and scores WCAG AA
(4.5:1, or 3:1 for text ≥18pt or ≥14pt bold). Where the background is an
**image**, which is where the interesting text sits, the picture is decoded and
sampled *under that run's own shape*, so text over a plate's dark corner and
text over its bright disc get different answers.

It reports rather than guesses. A run that inherits its colour through the
placeholder/layout/master chain is `unchecked`, never passed, and so is a
translucent run whose drawn colour depends on the backdrop. A confident wrong
ratio would be worse than an honest gap: the value of the check is being able
to trust a clean report.

```bash
python3 skills/aaif-create-chapter/scripts/restyle_design_system.py --contrast
```

`ooxml_style.improve_contrast` repairs what it finds, and decides by
measurement: a slide is re-scored with the on-dark remap applied and kept only
if a run **crosses** the threshold upward and none crosses down. The rule is
about crossings, not about the numbers — requiring that no ratio drop at all
rejects rescuing a 1.00:1 wordmark because `--ink-4` → `--ink-inv-3` moves
already-passing runs from 5.89 to 5.71.

### The agent, per chapter

`agent_art.chapter_scene(name)` derives a chapter's hue, action, ridge and
mirror from an FNV-1a hash of **its own name**, which is the design system's
chapter-plate rule: a chapter renders the same scene every time and neighbours
in a list never match. Eight actions x four ridges x mirrored x ten hues covers
every chapter from one small vocabulary — the alternative, a landmark each,
would be eighty illustrations to draw, approve and maintain.

`skills/aaif-create-chapter/scripts/upload_agents.py` puts each chapter's own
agent, plus the ten generic ones, into an `Agents/` folder in its Drive folder
as animated GIFs. **`create_chapter.py` deliberately does not do this**: a new
chapter is cloned from TemplateCity, and cloning would hand it TemplateCity's
agent rather than one derived from its own name. Run the upload after creating
a chapter — a full run names the chapters that are missing theirs.

`lib/aaif_events/agent_art.py` draws the background plates and the agent motif
from the same tokens, rasterising through headless Chrome and packing animation
with its own GIF89a encoder. The rule about which plates may animate is
technical as well as aesthetic: a GIF is palette-indexed, so it renders flat
vector art exactly and a smooth gradient only approximately. The encoder
measures the share of pixels a 256-entry palette would move and refuses over 1%
— the flat plates measure 0.002%, the gradient plates 7.5%.

To sweep Drive:

```bash
# what is off the design system, estate-wide? (exit 1 if anything is)
python3 skills/aaif-create-chapter/scripts/restyle_design_system.py --check

# apply, archiving every pre-change file to ./backups/restyle-<UTC>/
python3 skills/aaif-create-chapter/scripts/restyle_design_system.py --write
```

It is read-only by default and asserts it reached **TemplateCity**,
**TemplateSeries** and the shared **Templates** folder — the three that mint
everything else — exiting non-zero if it did not.

Scope is the template *files*, by name, not everything sitting in a template
folder: organizers keep their own decks there and those are not the toolkit's to
rebrand. Anything skipped for that reason is named in the report.

## How report-generating skills consume it

`lib/aaif_events/report_style.py` is the only module that knows AAIF token
names. It:

1. embeds Instrument Sans from `assets/fonts/` as base64 — the reports render to
   PDF through headless Chrome on machines that may be offline, and a linked
   webfont that fails to load falls back silently, so the page still renders and
   quietly stops matching the brand;
2. reads `design/aaif-tokens.css` at render time; and
3. maps its own component vocabulary (`--ground`, `--ink-1`, `--accent`, `--ok`
   / `--warn` / `--bad`) onto those tokens, each with a fallback so a missing
   tokens file degrades to legible greys instead of an unstyled page — and the
   rendered page then carries a visible note saying so, because a warning on
   stderr dies with the terminal while the PDF outlives it.

The fallback rule applies to `extra_css` in skill scripts too, not just to
`BASE_CSS`. That is exactly where it was broken first: an appendix separator
referenced an undefined `var(--rule)` with no fallback and simply never
rendered.

**Skill scripts never write colour or font values of their own**, and reach for
a new component in `report_style.py` rather than an inline style. (A few legacy
inline `margin-top`s survive in `audit_members.py` and `audit_organizers.py` —
they are not a pattern to copy.) They emit markup that uses the shared component
classes (`.stats`/`.stat`, `.tablewrap`,
`.actions`, `.eyebrow`, `.caveat`, `rs.bars()`, `rs.actions()`) and call
`rs.page()`. If a report needs something the vocabulary does not have, add it to
`report_style.py` — a one-off inline style in a skill script is how one report
starts looking different from the rest.

Two existing rules follow from this and are repeated here because they are easy
to break:

- **Never hand-edit generated HTML.** Change the script or `report_style.py` and
  re-run, so the next run keeps the fix.
- **Never use LibreOffice/`soffice`** to render or preview anything. To *see* a
  report, render it with `rs.to_pdf()` (headless Chrome) or screenshot the HTML
  with Chrome directly.

## Checking your work

```bash
python3 scripts/extract_design_tokens.py --check   # tokens match the bundle
```

To eyeball a report without a PDF viewer:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --screenshot=/tmp/preview.png \
  --window-size=1240,1600 --hide-scrollbars "file://$PWD/some-report.html"
```

Remember that report HTML holds member names and email addresses: keep previews
out of the repo and delete them when you are done.
