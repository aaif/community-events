"""Shared page chrome for the Slack audit reports.

One token system, four documents, so every report reads as a
set. Everything is inlined into a single self-contained HTML file — no CDN, no
webfont fetch — because the pages are published as Artifacts under a strict CSP
and printed to PDF offline.

Colours are defined once, on bare `:root`, and the page is white. There is no
dark mode and no `prefers-color-scheme` block: AAIF is a **two-surface** system —
white editorial and a black plate — where the surface is chosen per component by
the designer, not flipped wholesale by the viewer's OS. An earlier pass here
shipped a light palette plus an inverted twin, which is a different design
language wearing AAIF's tokens. Black still appears, but only where something is
deliberately drawn on it (`.closing`), and those rules carry their own light-on-
dark colours inline.

Output files are written 0600 through a `<name>.<random>.partial` sibling that
is `os.replace`d into place (`write_private`). A run killed between the two
steps can orphan such a `.partial` beside the report; it is 0600 and holds
the same content as the report would have, so delete it rather than publish
it.
"""

import errno
import base64
import functools
import html
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from aaif_events.slack import scrubbed_env

#: Credential shapes that a child process can echo back in its stderr: Google
#: access/refresh tokens, OAuth client secrets and API keys, Slack bot/user/app
#: tokens (including the rotating `xoxe.xoxp-…` form), GitHub, OpenAI-style and
#: Anthropic-style keys, bearer headers, and the repo's own `NAME=value` env
#: lines as a crashing child would dump them. The `access_token`/`refresh_token`
#: rule matches only a token-*looking* value (20+ token characters), so
#: diagnostics such as `access_token: missing` stay readable.
_SECRET_RE = re.compile(
    r"ya29\.[\w-]+"
    r"|(?:xoxe\.)?xox[a-z]-[\w-]+"
    r"|xapp-[\w-]+"
    r"|GOCSPX-[\w-]+"
    r"|1//0[\w-]{20,}"
    r"|AIza[\w-]{30,}"
    r"|ghp_\w{30,}|github_pat_\w+"
    r"|sk-[\w-]{20,}"
    r"|secret-[\w-]{20,}"
    r"|Bearer\s+[\w.-]{20,}"
    r"|(?:LUMA_API_KEY|AAIF_SLACK_\w*_TOKEN|GOOGLE_WORKSPACE_CLI_\w+)\s*=\s*\S+"
    r'|"?(?:access|refresh)_token"?\s*[:=]\s*"?[A-Za-z0-9._/-]{20,}')


def redact(text, limit=400):
    """`text` with anything credential-shaped replaced, bounded to `limit`.

    For subprocess output that is about to land in an exception message: `gws`
    and Chrome both print their environment or a request dump on some
    failures, and exceptions end up in terminals, logs and bug reports.
    Redact first, then truncate, so a token straddling the cut cannot survive.
    """
    return _SECRET_RE.sub("<redacted>", text or "")[:limit]

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
)

#: Instrument Sans is the aaif.io typeface. It is EMBEDDED as base64 rather
#: than linked: these reports are rendered to PDF by headless Chrome and are
#: routinely read from a laptop with no network, and a linked webfont that
#: fails to load silently falls back to system-ui — the page still renders, so
#: nothing looks broken, and the output quietly stops matching the site. The
#: variable font covers every weight from one file per subset.
#: SIL Open Font License 1.1; see assets/fonts/OFL.txt.
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "assets", "fonts")
_FONT_SUBSETS = (
    ("InstrumentSans-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
     "U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,"
     "U+2212,U+2215,U+FEFF,U+FFFD"),
    ("InstrumentSans-latin-ext.woff2",
     "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,"
     "U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,"
     "U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"),
)


@functools.lru_cache(maxsize=1)
def font_css():
    """@font-face rules with the woff2 files inlined, or "" if they are absent.

    Absent is not fatal — the stack falls back to ui-sans-serif and the report
    is still correct, just off-brand. A missing font must never stop an audit
    from being written.
    """
    rules, absent = [], []
    for name, unicode_range in _FONT_SUBSETS:
        path = os.path.join(_FONT_DIR, name)
        try:
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
        except OSError as exc:
            absent.append("%s (%s)" % (name, exc.strerror or exc))
            continue
        rules.append(
            "@font-face{font-family:'Instrument Sans';font-style:normal;"
            "font-weight:400 700;font-display:block;"
            "src:url(data:font/woff2;base64,%s) format('woff2');"
            "unicode-range:%s}" % (b64, unicode_range))

    if absent and rules:
        # PARTIAL is the dangerous case and the silent one. If `latin` loads and
        # `latin-ext` does not, the report renders in Instrument Sans except for
        # accented glyphs — so "München", "Zürich", "España" change face
        # mid-word. Nobody diagnoses that from a PDF. A partial set is never
        # intentional, so refuse it and fall back wholly to the system stack.
        print("WARNING: only %d of %d font subsets loaded (%s) — falling back "
              "to the system sans entirely rather than rendering accented "
              "names in a second face."
              % (len(rules), len(_FONT_SUBSETS), "; ".join(absent)),
              file=sys.stderr)
        return ""
    if absent:
        print("note: no embedded fonts found in %s (%s) — reports will render "
              "in the system sans." % (_FONT_DIR, "; ".join(absent)),
              file=sys.stderr)
    return "".join(rules)


#: The design system's own token file, read at render time rather than copied
#: into this module. A second hand-maintained palette is a palette that drifts:
#: the reports would keep looking almost-right for months after the system moved.
#: See DESIGN.md.
_TOKENS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "design", "aaif-tokens.css")


@functools.lru_cache(maxsize=1)
def design_tokens():
    """`design/aaif-tokens.css`, or "" if it is missing.

    Missing is survivable — every var() below carries a fallback — but it is
    not silent: the report would render in the fallback greys and nobody would
    know why, so say it once on stderr.
    """
    try:
        with open(_TOKENS, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        print("note: design/aaif-tokens.css not found — reports will render in "
              "fallback colours. Run scripts/extract_design_tokens.py.",
              file=sys.stderr)
        return ""


#: The report vocabulary, mapped onto the AAIF tokens. These names are what the
#: components below and every skill's report markup use; the right-hand side is
#: the only place that knows an AAIF token name, so a rename in the design
#: system is a change here and nowhere else — with three typography exceptions
#: used directly in the component rules below, listed so the claim stays honest:
#: `--tr-tight` (heading tracking), `--font-mono` and `--tr-eyebrow` (eyebrows).
#:
#: Note what the accent is: **black**. The design system is explicit that
#: "primary actions are black", and the spectrum hues are accents *within* a
#: surface, not the brand colour. An earlier pass here made coral the accent and
#: the reports came out looking like a different organisation's.
BASE_CSS = """
:root{
  --ground:var(--paper,#FFFFFF); --surface:var(--paper,#FFFFFF);
  --sunken:var(--paper-3,#ECEBE6); --inset:var(--paper-3,#ECEBE6);
  --ink-1:var(--ink,#0A0A0A); --ink-soft:var(--ink-2,#1A1A1A);
  --ink-mid:var(--ink-3,#4A4A4A); --ink-faint:var(--ink-4,#8C8C8C);
  --line-1:var(--line,#E5E5E2); --line-soft:var(--line,#E5E5E2);
  --line-hard:var(--line-2,#CFCFC9);
  --accent:var(--ink,#0A0A0A);
  --accent-soft:var(--paper-3,#ECEBE6);
  --marker:var(--spec-2,#4D7CFE);
  --ok:var(--success,#2BB673); --ok-bg:var(--paper-3,#ECEBE6);
  --warn:var(--warning,#E0A23A); --warn-bg:var(--paper-3,#ECEBE6);
  --bad:var(--danger,#E26052); --bad-bg:var(--paper-3,#ECEBE6);
}
*{box-sizing:border-box}
body{margin:0; background:var(--ground); color:var(--ink-soft);
  font-family:"Instrument Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased}
/* The design system's own link treatment (`--link`/`--link-hover`) — a
   real <a> in a report is a page-internal jump (the sections index), not a
   web link, so no unstyled browser blue should ever show through. */
a{color:var(--link,var(--ink-1)); text-decoration:none; border-bottom:1px solid currentColor}
a:hover{color:var(--link-hover,var(--marker))}
.wrap{max-width:1120px; margin:0 auto; padding:56px 28px 96px;
  display:flex; flex-direction:column; gap:44px}
h1,h2,h3,h4{font-family:inherit; color:var(--ink-1);
  text-wrap:balance; margin:0; font-weight:500;
  letter-spacing:var(--tr-tight,-.02em); line-height:1.15}
h1{font-size:2.5rem; line-height:1.12}
h2{font-size:1.4rem}
h3{font-size:1.1rem}
.eyebrow{font-family:var(--font-mono,ui-monospace,Menlo,monospace);
  font-size:12px; line-height:20px; text-transform:uppercase;
  letter-spacing:var(--tr-eyebrow,.16em); color:var(--ink-faint);
  font-weight:500; margin-bottom:12px}
.lede{color:var(--ink-soft); max-width:66ch; margin-top:14px}
code.chan{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.82em;
  background:var(--sunken); border:1px solid var(--line-soft); border-radius:0;
  padding:1px 6px; color:var(--ink-1); white-space:nowrap}
/* Masthead + closing band — the design system's own document furniture. */
.masthead{display:flex; align-items:center; justify-content:space-between;
  gap:24px; flex-wrap:wrap; padding-bottom:20px;
  border-bottom:1px solid var(--line-hard)}
.masthead .mark{width:150px; height:auto; display:block}
.masthead .mark-text{font-weight:600; font-size:1.3rem; letter-spacing:-.02em}
.mh-meta{font-family:var(--font-mono,ui-monospace,Menlo,monospace);
  font-size:12px; text-transform:uppercase;
  letter-spacing:var(--tr-eyebrow,.16em); color:var(--ink-faint)}
.closing{display:flex; align-items:center; justify-content:space-between;
  gap:24px; flex-wrap:wrap; background:var(--void,#000); color:#FFF;
  border-radius:0; padding:26px 30px; margin-top:8px}
.closing .cl-line{font-size:1.05rem; font-weight:500; letter-spacing:-.01em}
.closing .cl-meta{font-family:var(--font-mono,ui-monospace,Menlo,monospace);
  font-size:12px; text-transform:uppercase;
  letter-spacing:var(--tr-eyebrow,.16em); color:#8A8A86}
.nil{color:var(--ink-faint)}
/* Used by every report for de-emphasised cell text and empty-table
   placeholders ("no purpose set", "Nothing quiet."). It was referenced in ten
   places before it existed, so those cells rendered at full ink weight. */
.mute{color:var(--ink-faint)}
.bad{color:var(--bad); font-weight:650}
.caveat{background:var(--warn-bg); border:1px solid color-mix(in srgb,var(--warn) 30%,transparent);
  border-radius:0; padding:16px 20px; color:var(--ink-1); font-size:.9rem}
.caveat strong{color:var(--warn)}
/* Per-cell borders, not a 1px grid gap over a tinted container: a short final
   row would otherwise leave a slab of container colour where cells are missing. */
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(158px,1fr)); gap:10px}
.stat{background:var(--surface); border:1px solid var(--line-1); border-radius:0;
  padding:18px 20px; display:flex; flex-direction:column; gap:3px}
.stat .v{font-size:2rem; font-weight:600; font-variant-numeric:tabular-nums;
  line-height:1.05; color:var(--ink-1); letter-spacing:-.025em}
.stat .k{font-size:.74rem; text-transform:uppercase; letter-spacing:.09em; color:var(--ink-faint)}
.stat.s-ok .v{color:var(--ok)} .stat.s-warn .v{color:var(--warn)} .stat.s-bad .v{color:var(--bad)}
.controls{display:flex; flex-wrap:wrap; gap:8px; align-items:center}
.controls span.lbl{font-size:.74rem; text-transform:uppercase; letter-spacing:.09em;
  color:var(--ink-faint)}
button.f{font:inherit; font-size:.82rem; padding:5px 13px; border-radius:99px; cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--line-1)}
button.f:hover{border-color:var(--accent); color:var(--ink-1)}
button.f[aria-pressed="true"]{background:var(--accent); border-color:var(--accent);
  color:var(--ground)}
button.f:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.tablewrap{overflow-x:auto; border:1px solid var(--line-1); border-radius:0;
  background:var(--surface)}
/* width:max-content + min-width:100% (not a flat width:100%) so a table
   with few columns still fills its wrapper, but one whose columns need more
   room than that GROWS past it — which is what makes .tablewrap's
   overflow-x:auto actually engage instead of squeezing every column down to
   fit and clipping the last one's header text. */
table{border-collapse:collapse; width:max-content; min-width:100%; font-size:.87rem}
th,td{text-align:left; padding:9px 14px; border-bottom:1px solid var(--line-soft);
  vertical-align:top}
thead th{position:sticky; top:0; background:var(--sunken); font-size:.7rem;
  text-transform:uppercase; letter-spacing:.08em; color:var(--ink-faint); font-weight:650;
  border-bottom:1px solid var(--line-1); white-space:nowrap}
tbody th{font-weight:600; white-space:nowrap}
tbody tr:last-child th,tbody tr:last-child td{border-bottom:none}
tbody tr:hover th,tbody tr:hover td{background:var(--sunken)}
td.n{text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap}
.num{font-size:.75rem; color:var(--ink-faint); font-variant-numeric:tabular-nums;
  margin-left:5px}
.tag{font-size:.63rem; text-transform:uppercase; letter-spacing:.07em; padding:1px 6px;
  border-radius:0; margin-left:5px; white-space:nowrap; font-weight:650}
.tag-reg{background:var(--warn-bg); color:var(--warn)}
.tag-pub{background:var(--bad-bg); color:var(--bad)}
.two{display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:20px}
.card{border:1px solid var(--line-1); border-radius:0; background:var(--surface);
  padding:20px 22px}
.card h3{margin-bottom:4px}
.card .sub{font-size:.8rem; color:var(--ink-faint)}
.note{font-size:.82rem; color:var(--ink-faint); margin-top:12px;
  border-left:2px solid var(--line-1); padding-left:12px}
.thesis{font-size:1.15rem; line-height:1.5; border-left:3px solid var(--accent);
  padding-left:20px; color:var(--ink-1); max-width:62ch}
.chips{display:flex; flex-wrap:wrap; gap:6px; margin-top:12px}
.chip{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.72rem;
  background:var(--sunken); border:1px solid var(--line-soft); border-radius:0;
  padding:2px 7px}
.chap{border:1px solid var(--line-1); border-radius:0; background:var(--surface);
  padding:18px 22px; min-width:0}
.chap h3{display:flex; flex-wrap:wrap; gap:10px; align-items:baseline; margin-bottom:12px}
.chapchans{display:flex; gap:6px; flex-wrap:wrap}
/* Full-width, one chapter per row — a 2-up card grid squeezed a 9-column
   table into ~360px and truncated it. Each chapter is its own subsection,
   not a card competing for horizontal room with its neighbour. */
.stack-chaps{display:flex; flex-direction:column; gap:28px}
.pill{font-size:.66rem; padding:2px 8px; border-radius:99px; white-space:nowrap;
  font-weight:650}
.pill-ok{background:var(--ok-bg); color:var(--ok)}
.pill-warn{background:var(--warn-bg); color:var(--warn)}
.pill-bad{background:var(--bad-bg); color:var(--bad)}
.pill-mute{background:var(--sunken); color:var(--ink-faint)}
/* A row with at least one Issues entry — tinted so a scan of the table finds
   it without reading every cell, using the same restrained warn/bad tokens
   the pills already use, never a new colour. */
tr.has-issue td,tr.has-issue th{background:var(--warn-bg)}
tr.has-issue:hover td,tr.has-issue:hover th{background:var(--warn-bg)}
/* Not yet accepted — a fact about the ROW, not an issue by itself (a pending
   applicant with nothing else wrong gets no has-issue tint), so it gets its
   own, lighter signal. */
tr.pending td,tr.pending th{font-style:italic}
.bars{display:flex; flex-direction:column; gap:7px; margin-top:14px}
.brow{display:grid; grid-template-columns:132px 1fr 62px; gap:12px; align-items:center}
.blab{font-size:.8rem; color:var(--ink-soft); text-align:right}
.btrack{background:var(--sunken); border-radius:0; height:14px; position:relative;
  border:1px solid var(--line-soft)}
.bfill{position:absolute; inset:0 auto 0 0; border-radius:0; min-width:3px}
.t-accent{background:var(--accent)} .t-bad{background:var(--bad)} .t-warn{background:var(--warn)}
.bval{font-size:.8rem; font-variant-numeric:tabular-nums; color:var(--ink-1);
  font-weight:600; text-align:right}
.funnel{display:flex; flex-direction:column; gap:10px; margin-top:18px}
.frow{display:grid; grid-template-columns:196px 1fr 96px 52px; gap:14px; align-items:center}
.flab{font-size:.85rem; color:var(--ink-soft); text-align:right}
.ftrack{background:var(--sunken); border:1px solid var(--line-soft); border-radius:0;
  height:22px; position:relative}
.ffill{position:absolute; inset:0 auto 0 0; border-radius:0}
.fval{font-size:1.05rem; font-weight:650; font-variant-numeric:tabular-nums; text-align:right}
.fpct{display:block; font-size:.7rem; color:var(--ink-faint); font-weight:500}
.drop{font-size:.78rem; color:var(--bad); font-variant-numeric:tabular-nums; font-weight:650}
.stack{display:flex; height:34px; border-radius:0; overflow:hidden; margin-top:14px;
  border:1px solid var(--line-1); gap:2px; background:var(--line-1)}
.seg{display:flex; align-items:center; justify-content:center; font-size:.68rem;
  font-weight:700; color:var(--ground); overflow:hidden; white-space:nowrap}
.legend{display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:.78rem;
  color:var(--ink-soft)}
.legend span.sw{width:10px; height:10px; border-radius:0; display:inline-block;
  margin-right:6px; vertical-align:-1px}
/* Prose in a print table fights the column algorithm and overflows the page box,
   so ranked actions are a grid, not a <table>. */
ol.actions{list-style:none; margin:16px 0 0; padding:0; display:flex; flex-direction:column;
  border-top:1px solid var(--line-1)}
.arow{display:grid; grid-template-columns:32px 1fr 150px; gap:14px; padding:14px 2px;
  border-bottom:1px solid var(--line-soft); align-items:start}
.rank{ font-size:1.3rem; color:var(--ink-faint);
  font-variant-numeric:tabular-nums; line-height:1.2}
.abody{min-width:0}
.atitle{display:block; font-weight:650; font-size:.95rem; margin-bottom:4px}
.awhy{display:block; font-weight:400; color:var(--ink-soft); font-size:.85rem}
.ameta{display:flex; flex-direction:column; gap:5px; align-items:flex-start}
.eff{color:var(--ink-soft); font-size:.8rem}
ul.plain{margin:14px 0 0; padding-left:20px; color:var(--ink-soft); font-size:.9rem}
ul.plain li{margin-bottom:7px}
footer{color:var(--ink-faint); font-size:.8rem; border-top:1px solid var(--line-1);
  padding-top:20px}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}

@media print{
  /* No palette override here. This block used to pin a purple set that
     predated AAIF (--accent:#5A3D8C), and because it redefined the DESIGN
     SYSTEM's own names (--ink, --line) rather than the report vocabulary
     (--ink-1, --line-1), every PDF printed in the old brand while the screen
     rendered correctly. The page is white on screen and on paper now, so
     there is nothing to pin — only geometry belongs in @media print. */
  @page{size:A4; margin:14mm 12mm}
  body{font-size:9.5pt; background:#fff}
  .wrap{max-width:none; padding:0; gap:22px}
  h1{font-size:22pt} h2{font-size:13pt} h3{font-size:10.5pt}
  .controls{display:none}
  .tablewrap{overflow:visible; border-radius:0}
  table{min-width:0; font-size:8pt}
  th,td{padding:3.5px 7px}
  thead{display:table-header-group}
  thead th{position:static}
  tbody tr{break-inside:avoid}
  .stats{grid-template-columns:repeat(5,1fr); gap:7px; break-inside:avoid}
  .stat{padding:10px 12px} .stat .v{font-size:16pt}
  .two{grid-template-columns:1fr 1fr; gap:12px}
  .card,.chap{break-inside:avoid}
  .card{padding:12px 14px}
  .chap{padding:12px 14px}
  .stack-chaps{gap:14px}
  .brow{grid-template-columns:106px 1fr 52px; gap:8px}
  .blab,.bval{font-size:7.5pt}
  .btrack{height:11px}
  .bars{gap:5px}
  .frow{grid-template-columns:150px 1fr 78px 44px; gap:9px}
  .flab{font-size:8pt} .fval{font-size:10pt} .ftrack{height:16px}
  .arow{grid-template-columns:24px 1fr 122px; gap:9px; padding:8px 2px; break-inside:avoid}
  .rank{font-size:11pt} .atitle{font-size:9pt} .awhy{font-size:7.5pt} .eff{font-size:7pt}
  .thesis{font-size:10.5pt}
  a{color:inherit}
}
"""


#: The AAIF wordmark, lifted from the design system bundle. Inlined as a data
#: URI for the same reason the fonts are: these render to PDF offline, and a
#: missing logo is the kind of thing nobody notices until the document is in
#: front of a board.
_MARK = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "assets", "aaif-mark.svg")


@functools.lru_cache(maxsize=1)
def mark_data_uri():
    """The wordmark as a data: URI, or "" when the asset is missing."""
    try:
        with open(_MARK, "rb") as fh:
            return ("data:image/svg+xml;base64,"
                    + base64.b64encode(fh.read()).decode("ascii"))
    except OSError:
        return ""


def masthead(title, eyebrow=""):
    """The document furniture every AAIF report opens with.

    Tokens alone are not a brand: the design system's own one-pager opens on a
    logo lockup over a mono metadata rule, and closes on a black band. Reports
    that carried only the palette read as a tool's output rather than as
    something the foundation issued — which is what they are.
    """
    src = mark_data_uri()
    logo = ('<img class="mark" alt="AAIF" src="%s">' % src) if src else (
        '<span class="mark-text">AAIF</span>')
    return ('<header class="masthead">%s<span class="mh-meta">%s</span></header>'
            % (logo, html.escape(str(eyebrow or title))))


def closing(line="Take your seat in what comes next.", meta="aaif.io"):
    """The black closing band, matching the design system's one-pager."""
    return ('<footer class="closing"><span class="cl-line">%s</span>'
            '<span class="cl-meta">%s</span></footer>'
            % (html.escape(str(line)), html.escape(str(meta))))


def page(title, body, extra_css="", script=""):
    """Wrap a body fragment in a complete, self-contained HTML document.

    The doctype and charset are not decoration: without a doctype Chrome renders
    the PDF in quirks mode, and without a declared charset it guesses the
    encoding of pages that legitimately contain `españa`, `Montréal` and `—`.
    Write the result with `encoding="utf-8"`, never the locale default.
    """
    # A stderr note dies with the terminal; the PDF outlives it. A reader
    # holding an off-palette document otherwise has no way to know why.
    tokens = design_tokens()
    marker = "" if tokens else (
        '<footer><b>Note:</b> the AAIF design tokens were not available when '
        'this document was rendered, so it is shown in fallback colours. '
        'Run scripts/extract_design_tokens.py and re-render.</footer>')
    return ('<!doctype html>\n<meta charset="utf-8">\n'
            '<title>%s</title>\n<style>%s%s</style>\n<div class="wrap">%s</div>%s\n'
            % (html.escape(str(title)),
               font_css() + tokens + BASE_CSS, extra_css, body + marker,
               ("\n<script>%s</script>" % script) if script else ""))


def bars(rows, tone="accent", fmt=lambda v: format(v, ",")):
    """A single-series horizontal bar chart.

    One measure, one hue — no categorical palette is involved, so there is no
    legend and nothing to validate for colourblind separation.

    Labels are escaped **here**, not at the call sites. These reports render
    Slack- and spreadsheet-sourced text (channel names, city names, email
    domains) that outsiders can influence, so the helper is safe by
    construction. Do NOT pre-escape arguments — they would double-escape.
    """
    top = max((v for _, v in rows), default=0) or 1
    cells = []
    for label, value in rows:
        width = max(value / top * 100, 0.6) if value else 0
        cells.append('<div class="brow"><span class="blab">%s</span>'
                     '<span class="btrack"><span class="bfill t-%s" style="width:%s%%">'
                     '</span></span><span class="bval">%s</span></div>'
                     % (html.escape(str(label)), tone, width,
                        html.escape(str(fmt(value)))))
    return '<div class="bars">%s</div>' % "".join(cells)


def actions(items):
    """Ranked action list. Each item is (title, why, effort, owner, when).

    Every field is escaped here — see `bars()`. Action text routinely embeds
    chapter and channel names straight from the sheet, which is the highest-risk
    interpolation in either report, so pass plain text and never markup.
    """
    tone = {"now": "bad", "next": "warn", "later": "mute"}
    rows = []
    for i, (title, why, effort, owner, when) in enumerate(items, 1):
        rows.append(
            '<li class="arow"><span class="rank">%d</span>'
            '<span class="abody"><span class="atitle">%s</span>'
            '<span class="awhy">%s</span></span>'
            '<span class="ameta"><span class="pill pill-%s">%s</span>'
            '<span class="eff">%s</span><span class="eff">%s</span></span></li>'
            % (i, html.escape(str(title)), html.escape(str(why)),
               tone.get(when, "mute"), html.escape(str(when)),
               html.escape(str(effort)), html.escape(str(owner))))
    return '<ol class="actions">%s</ol>' % "".join(rows)


def find_chrome():
    """Path to a headless-capable Chrome/Chromium, or None."""
    for candidate in CHROME_PATHS:
        if candidate.startswith("/"):
            if os.path.exists(candidate):
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def write_private(path, text):
    """Write a PII-carrying report file 0600 from the first byte.

    jsoncache's rule, restated for the report side: a chmod *after* the write
    leaves the page world-readable for its duration — permanently, if the run
    is killed in between.

    Written the way jsoncache.write is: into a 0600 temp file beside the
    target, then `os.replace`d, so the report is either the previous complete
    one or the new one. `os.replace` is also what defeats a symlink planted at
    `path`: it swaps the *link entry* for the new file rather than writing
    through it, and the explicit check below refuses such a path outright so
    the link is left alone rather than silently replaced. The `O_NOFOLLOW`
    open only tightens a pre-existing regular file's mode before the swap —
    it is not what protects against the symlink.
    """
    directory = os.path.dirname(os.path.abspath(path))
    tmp = None
    pre_existed = os.path.lexists(path)
    try:
        try:
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".",
                                       suffix=".partial")
        except OSError as exc:
            raise SystemExit("Cannot write %s: %s (does the directory exist?)"
                             % (path, exc.strerror or exc))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.islink(path):
            raise SystemExit("Refusing to write %s: it is a symlink." % path)
        # O_NOFOLLOW re-checks at the kernel, closing the race above; the open
        # also tightens a pre-existing looser file before it is replaced.
        try:
            existing = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SystemExit("Refusing to write %s: it is a symlink." % path)
            raise
        try:
            os.fchmod(existing, 0o600)
        finally:
            os.close(existing)
        try:
            os.replace(tmp, path)      # atomic; the 0600 mode travels with the file
        except OSError:
            # The O_CREAT above made an empty 0600 file that nothing will
            # ever fill; do not leave it masquerading as a report.
            if not pre_existed and os.path.isfile(path) and os.path.getsize(path) == 0:
                os.remove(path)
            raise
    except BaseException:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        raise


def to_pdf(html_path, pdf_path, timeout_s=180):
    """Print a local HTML file to PDF with headless Chrome, and verify it worked.

    Chrome, never LibreOffice: `soffice` substitutes local system fonts and drops
    markup it does not understand, so its render misrepresents the document.

    Verification is the point of the length here. Headless Chrome has been
    observed exiting **0** without rendering the document you asked for — a
    contested profile lock produces no file, and a URL it cannot open produces a
    perfectly valid PDF of Chrome's own error page (46 KB, observed 2026-08).
    Rather than enumerate Chrome's failure modes, check the inputs before
    invoking it and the artifact after.

    Size alone is not a check: an error page is *larger* than a short report.
    """
    chrome = find_chrome()
    if not chrome:
        raise SystemExit(
            "No Chrome/Chromium found for PDF rendering. Install Google Chrome, or "
            "open the HTML and print to PDF by hand. Do not use LibreOffice.")

    # Chrome renders its "file not found" page and exits 0, so a missing input
    # would otherwise be published as the report. Check before invoking.
    if not os.path.isfile(html_path):
        raise SystemExit("Cannot render %s to PDF — the HTML does not exist." % html_path)

    # Remove any earlier render first, so a failed run does not leave an
    # outdated report at the final path — it would be indistinguishable from
    # a fresh one.
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    # as_uri(), not "file://" + path: a '#' or '?' in the output name would
    # otherwise start a URL fragment/query and silently render a different file.
    url = pathlib.Path(html_path).absolute().as_uri()
    # Chrome creates the PDF with default (world-readable) permissions, and it
    # carries the same PII as the 0600 HTML it was rendered from. Render into a
    # private (0700) scratch directory, tighten, then move into place — so
    # there is no window in which the final path is readable by others.
    scratch = tempfile.mkdtemp(prefix="aaif-pdf-",
                               dir=os.path.dirname(os.path.abspath(pdf_path)))
    tmp_pdf = os.path.join(scratch, "render.pdf")
    try:
        try:
            proc = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                 "--print-to-pdf=" + tmp_pdf, url],
                capture_output=True, text=True, timeout=timeout_s,
                env=scrubbed_env(strict=True))   # Chrome needs no credentials
        except subprocess.TimeoutExpired:
            raise SystemExit(
                "Chrome hung for %ds rendering %s — usually another Chrome instance "
                "holding the profile lock. Quit Chrome and retry, or pass --no-pdf "
                "and print %s by hand." % (timeout_s, pdf_path, html_path))

        stderr = redact(proc.stderr, 800)
        if proc.returncode != 0:
            raise SystemExit(
                "Chrome failed to render %s (exit %d).\n%s\nThe HTML is intact at %s "
                "— open it and print to PDF by hand. Do not use LibreOffice."
                % (pdf_path, proc.returncode, stderr, html_path))

        if not os.path.exists(tmp_pdf):
            raise SystemExit(
                "Chrome exited 0 but wrote no PDF at %s. This is usually another "
                "Chrome instance holding the profile lock — quit Chrome and retry, "
                "or pass --no-pdf and print %s by hand.\n%s"
                % (pdf_path, html_path, stderr))
        with open(tmp_pdf, "rb") as fh:
            head = fh.read(5)
        if head != b"%PDF-" or os.path.getsize(tmp_pdf) < 1024:
            raise SystemExit(
                "Chrome exited 0 but produced no usable PDF at %s (%d bytes). Quit "
                "any running Chrome and retry, or pass --no-pdf and print %s by "
                "hand.\n%s" % (pdf_path, os.path.getsize(tmp_pdf), html_path, stderr))
        os.chmod(tmp_pdf, 0o600)
        os.replace(tmp_pdf, pdf_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.exists(scratch):
            print("WARNING: could not remove scratch directory %s — it may still "
                  "hold the rendered report; delete it by hand." % scratch,
                  file=sys.stderr)
    return pdf_path


def _repo_root(path):
    """Repo root containing `path`, or None when it lives outside any repo.

    Resolved from the path's own directory, not the process cwd — running the
    script from elsewhere must not decide whether the *output* is protected.

    Only git's own "not a git repository" answer means outside-a-repo. Any
    other failure (dubious ownership, a corrupt .git, …) aborts: mapping it to
    None would silently disengage the PII guard for a path that may well sit
    inside this public repo.
    """
    probe_dir = os.path.dirname(os.path.abspath(path)) or os.sep
    while not os.path.isdir(probe_dir) and probe_dir != os.sep:
        probe_dir = os.path.dirname(probe_dir)   # output dir may not exist yet
    try:
        # LC_ALL=C: the "not a git repository" match below reads git's stderr,
        # and localized git says e.g. "kein Git-Repository" — which would abort
        # legitimate outside-repo runs instead of returning None.
        proc = subprocess.run(["git", "-C", probe_dir, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True,
                              env={**scrubbed_env(strict=True), "LC_ALL": "C", "LANG": "C"})
    except FileNotFoundError:
        raise SystemExit(
            "REFUSING TO RUN: git is not installed, so this cannot verify that "
            "%s is ignored. These files hold Slack member names, email addresses "
            "and 2FA/admin flags; install git or point --cache/--out outside any "
            "repository." % path)
    if proc.returncode == 0:
        return proc.stdout.strip()
    stderr = (proc.stderr or "").strip()
    if "not a git repository" in stderr.lower():
        return None
    raise SystemExit(
        "REFUSING TO RUN: `git rev-parse` failed in %s (exit %d: %s), so this "
        "cannot verify that %s is ignored. Fix the git error, or point "
        "--cache/--out outside any repository."
        % (probe_dir, proc.returncode,
           stderr.splitlines()[-1] if stderr else "no stderr", path))


def assert_git_ignored(*paths):
    """Refuse to write audit output that git would happily commit.

    These reports and caches carry the workspace's entire user directory and the
    organizers' names and email addresses. The repo they are generated in is
    public, so `.gitignore` coverage is a safety control, not tidiness — and a
    control nobody checks is not a control. This runs before collection, so a
    missing rule costs a second rather than a 20-minute pull.

    A path outside every repository is fine — there is nothing to commit it to.
    That case is not the same as `git check-ignore` failing, which is why the
    repo root is resolved first: `check-ignore` exits 128 (not 1) for an
    outside path, and treating that as "unignored" would reject the very
    remedy this function's own error message recommends.
    """
    offenders = []
    for path in paths:
        root = _repo_root(path)
        if root is None:
            continue                      # outside any repo — nothing to leak into
        # Absolute before probing: check-ignore/ls-files run with `-C root`, so
        # a --out relative to a repo *sub*directory would otherwise be judged
        # against the wrong file.
        abs_path = os.path.abspath(path)
        # A directory is probed through a child: `check-ignore` answers
        # differently for a bare directory name, and this works before the
        # directory exists.
        probe = os.path.join(abs_path, "probe") if path.endswith(os.sep) else abs_path
        ignored = subprocess.run(["git", "-C", root, "check-ignore", "-q", probe],
                                 capture_output=True, env=scrubbed_env(strict=True)).returncode == 0
        # .gitignore has no effect on an already-tracked file, so a report
        # committed before these rules landed would still ride along on `git
        # add -A` while check-ignore reports it as ignored.
        tracked = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", abs_path],
                                 capture_output=True, env=scrubbed_env(strict=True)).returncode == 0
        if tracked:
            offenders.append("%s (already TRACKED — git rm --cached it)" % path)
        elif not ignored:
            offenders.append("%s (not ignored in %s)" % (path, root))
    if offenders:
        raise SystemExit(
            "REFUSING TO RUN: these output paths would be committable:\n  %s\n"
            "They will hold Slack member names, email addresses and 2FA/admin "
            "flags. Add them to .gitignore, or pass --cache/--out to a location "
            "outside any git repository." % "\n  ".join(offenders))
