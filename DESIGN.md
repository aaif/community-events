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
- **Two first-class surfaces.** White editorial and black plate — always know
  which one a component is drawn on. Reports are editorial, so they are white,
  and their dark mode uses the system's `--void*` / `--ink-inv*` inverse ramp
  rather than an invented dark palette.
- **The agent motif** (the bot built from the mark's primitives) is for empty
  states, loading, event graphics and dividers — **never** governance, board or
  press material. Audit reports are closer to the latter, so they do not use it.

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
