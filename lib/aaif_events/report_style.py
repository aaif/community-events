"""Shared page chrome for the Slack audit reports.

One token system, two documents, so the member and organizer reports read as a
set. Everything is inlined into a single self-contained HTML file — no CDN, no
webfont fetch — because the pages are published as Artifacts under a strict CSP
and printed to PDF offline.

Colours are defined three times on purpose: bare `:root` carries the full light
palette, and the dark values are repeated under both
`@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]`. A viewer
on the default "system" setting gets no `data-theme` stamp at all, so a colour
defined only inside a `[data-theme]` block would never apply to them. The media
block is additionally scoped to `:root:not([data-theme="light"])` so an explicit
light choice still wins on a dark OS — do not simplify that selector to a bare
`:root`, which silently breaks light-on-dark-OS with nothing to signal it.
"""

import html
import os
import pathlib
import shutil
import subprocess

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
)

BASE_CSS = """
:root{
  --ground:#FAF8FB; --surface:#FFFFFF; --sunken:#F2EEF6;
  --ink:#211B2C; --ink-soft:#5C5468; --ink-faint:#8B8398;
  --line:#E3DCEB; --line-soft:#EFEAF4;
  --accent:#6B4BA1; --accent-soft:#F0E9F9;
  --ok:#2C7A57; --ok-bg:#E4F2EB;
  --warn:#9A6512; --warn-bg:#F8EEDA;
  --bad:#A63645; --bad-bg:#F8E6E8;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#141019; --surface:#1D1826; --sunken:#221C2E;
    --ink:#EDE8F2; --ink-soft:#A79DB6; --ink-faint:#7C7289;
    --line:#312840; --line-soft:#271F34;
    --accent:#B69AE4; --accent-soft:#2A2038;
    --ok:#6FC79C; --ok-bg:#1B3329;
    --warn:#DFAE5E; --warn-bg:#33280F;
    --bad:#E8909C; --bad-bg:#361D22;
  }
}
:root[data-theme="dark"]{
  --ground:#141019; --surface:#1D1826; --sunken:#221C2E;
  --ink:#EDE8F2; --ink-soft:#A79DB6; --ink-faint:#7C7289;
  --line:#312840; --line-soft:#271F34;
  --accent:#B69AE4; --accent-soft:#2A2038;
  --ok:#6FC79C; --ok-bg:#1B3329;
  --warn:#DFAE5E; --warn-bg:#33280F;
  --bad:#E8909C; --bad-bg:#361D22;
}
*{box-sizing:border-box}
body{margin:0; background:var(--ground); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px; margin:0 auto; padding:56px 28px 96px;
  display:flex; flex-direction:column; gap:44px}
h1,h2,h3,h4{font-family:ui-serif,"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  text-wrap:balance; margin:0; font-weight:600; letter-spacing:-.01em}
h1{font-size:2.5rem; line-height:1.12}
h2{font-size:1.4rem}
h3{font-size:1.1rem}
.eyebrow{font-size:.7rem; text-transform:uppercase; letter-spacing:.16em;
  color:var(--accent); font-weight:650; margin-bottom:12px}
.lede{color:var(--ink-soft); max-width:66ch; margin-top:14px}
code.chan{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.82em;
  background:var(--sunken); border:1px solid var(--line-soft); border-radius:5px;
  padding:1px 6px; color:var(--ink); white-space:nowrap}
.nil{color:var(--ink-faint)}
.bad{color:var(--bad); font-weight:650}
.caveat{background:var(--warn-bg); border:1px solid color-mix(in srgb,var(--warn) 30%,transparent);
  border-radius:10px; padding:16px 20px; color:var(--ink); font-size:.9rem}
.caveat strong{color:var(--warn)}
/* Per-cell borders, not a 1px grid gap over a tinted container: a short final
   row would otherwise leave a slab of container colour where cells are missing. */
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(158px,1fr)); gap:10px}
.stat{background:var(--surface); border:1px solid var(--line); border-radius:12px;
  padding:18px 20px; display:flex; flex-direction:column; gap:3px}
.stat .v{font-size:2rem; font-weight:600; font-variant-numeric:tabular-nums;
  line-height:1.05; font-family:ui-serif,"Iowan Old Style",Georgia,serif}
.stat .k{font-size:.74rem; text-transform:uppercase; letter-spacing:.09em; color:var(--ink-faint)}
.stat.s-ok .v{color:var(--ok)} .stat.s-warn .v{color:var(--warn)} .stat.s-bad .v{color:var(--bad)}
.controls{display:flex; flex-wrap:wrap; gap:8px; align-items:center}
.controls span.lbl{font-size:.74rem; text-transform:uppercase; letter-spacing:.09em;
  color:var(--ink-faint)}
button.f{font:inherit; font-size:.82rem; padding:5px 13px; border-radius:99px; cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--line)}
button.f:hover{border-color:var(--accent); color:var(--ink)}
button.f[aria-pressed="true"]{background:var(--accent); border-color:var(--accent);
  color:var(--ground)}
button.f:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.tablewrap{overflow-x:auto; border:1px solid var(--line); border-radius:12px;
  background:var(--surface)}
table{border-collapse:collapse; width:100%; font-size:.87rem; min-width:800px}
th,td{text-align:left; padding:9px 14px; border-bottom:1px solid var(--line-soft);
  vertical-align:top}
thead th{position:sticky; top:0; background:var(--sunken); font-size:.7rem;
  text-transform:uppercase; letter-spacing:.08em; color:var(--ink-faint); font-weight:650;
  border-bottom:1px solid var(--line)}
tbody th{font-weight:600; white-space:nowrap}
tbody tr:last-child th,tbody tr:last-child td{border-bottom:none}
tbody tr:hover th,tbody tr:hover td{background:var(--sunken)}
td.n{text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap}
.num{font-size:.75rem; color:var(--ink-faint); font-variant-numeric:tabular-nums;
  margin-left:5px}
.tag{font-size:.63rem; text-transform:uppercase; letter-spacing:.07em; padding:1px 6px;
  border-radius:4px; margin-left:5px; white-space:nowrap; font-weight:650}
.tag-reg{background:var(--warn-bg); color:var(--warn)}
.tag-pub{background:var(--bad-bg); color:var(--bad)}
.two{display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:20px}
.card{border:1px solid var(--line); border-radius:12px; background:var(--surface);
  padding:20px 22px}
.card h3{margin-bottom:4px}
.card .sub{font-size:.8rem; color:var(--ink-faint)}
.note{font-size:.82rem; color:var(--ink-faint); margin-top:12px;
  border-left:2px solid var(--line); padding-left:12px}
.thesis{font-size:1.15rem; line-height:1.5; border-left:3px solid var(--accent);
  padding-left:20px; color:var(--ink); max-width:62ch}
.chips{display:flex; flex-wrap:wrap; gap:6px; margin-top:12px}
.chip{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.72rem;
  background:var(--sunken); border:1px solid var(--line-soft); border-radius:5px;
  padding:2px 7px}
.chap{border:1px solid var(--line); border-radius:12px; background:var(--surface);
  padding:18px 22px}
.chap h3{display:flex; flex-wrap:wrap; gap:10px; align-items:baseline; margin-bottom:12px}
.chapchans{display:flex; gap:6px; flex-wrap:wrap}
.grid-chaps{display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:16px}
.grid-rosters{display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px}
ul.plist{list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:9px}
ul.plist li{display:flex; flex-wrap:wrap; gap:4px 10px; align-items:baseline;
  padding-bottom:9px; border-bottom:1px dashed var(--line-soft)}
ul.plist li:last-child{border-bottom:none; padding-bottom:0}
ul.plist li.none{color:var(--ink-faint); font-size:.85rem; font-style:italic}
.pname{font-weight:600; font-size:.9rem}
.pmail{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.72rem;
  color:var(--ink-faint); word-break:break-all}
.pills{display:flex; gap:5px; flex-wrap:wrap; margin-left:auto}
.pill{font-size:.66rem; padding:2px 8px; border-radius:99px; white-space:nowrap;
  font-weight:650}
.pill-ok{background:var(--ok-bg); color:var(--ok)}
.pill-warn{background:var(--warn-bg); color:var(--warn)}
.pill-bad{background:var(--bad-bg); color:var(--bad)}
.pill-mute{background:var(--sunken); color:var(--ink-faint)}
.cnt{background:var(--accent-soft); color:var(--accent); border-radius:99px; padding:1px 7px;
  font-size:.68rem; font-weight:700}
.plist-x li{opacity:.82}
.roster h3{align-items:center; font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:.95rem}
.rgrps{display:flex; flex-direction:column; gap:14px}
.rgrp h4{display:flex; gap:8px; align-items:center; margin-bottom:8px; font-size:.8rem}
.bars{display:flex; flex-direction:column; gap:7px; margin-top:14px}
.brow{display:grid; grid-template-columns:132px 1fr 62px; gap:12px; align-items:center}
.blab{font-size:.8rem; color:var(--ink-soft); text-align:right}
.btrack{background:var(--sunken); border-radius:4px; height:14px; position:relative;
  border:1px solid var(--line-soft)}
.bfill{position:absolute; inset:0 auto 0 0; border-radius:3px; min-width:3px}
.t-accent{background:var(--accent)} .t-bad{background:var(--bad)} .t-warn{background:var(--warn)}
.bval{font-size:.8rem; font-variant-numeric:tabular-nums; color:var(--ink);
  font-weight:600; text-align:right}
.funnel{display:flex; flex-direction:column; gap:10px; margin-top:18px}
.frow{display:grid; grid-template-columns:196px 1fr 96px 52px; gap:14px; align-items:center}
.flab{font-size:.85rem; color:var(--ink-soft); text-align:right}
.ftrack{background:var(--sunken); border:1px solid var(--line-soft); border-radius:5px;
  height:22px; position:relative}
.ffill{position:absolute; inset:0 auto 0 0; border-radius:4px}
.fval{font-size:1.05rem; font-weight:650; font-variant-numeric:tabular-nums; text-align:right}
.fpct{display:block; font-size:.7rem; color:var(--ink-faint); font-weight:500}
.drop{font-size:.78rem; color:var(--bad); font-variant-numeric:tabular-nums; font-weight:650}
.stack{display:flex; height:34px; border-radius:8px; overflow:hidden; margin-top:14px;
  border:1px solid var(--line); gap:2px; background:var(--line)}
.seg{display:flex; align-items:center; justify-content:center; font-size:.68rem;
  font-weight:700; color:var(--ground); overflow:hidden; white-space:nowrap}
.legend{display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:.78rem;
  color:var(--ink-soft)}
.legend span.sw{width:10px; height:10px; border-radius:3px; display:inline-block;
  margin-right:6px; vertical-align:-1px}
/* Prose in a print table fights the column algorithm and overflows the page box,
   so ranked actions are a grid, not a <table>. */
ol.actions{list-style:none; margin:16px 0 0; padding:0; display:flex; flex-direction:column;
  border-top:1px solid var(--line)}
.arow{display:grid; grid-template-columns:32px 1fr 150px; gap:14px; padding:14px 2px;
  border-bottom:1px solid var(--line-soft); align-items:start}
.rank{font-family:ui-serif,Georgia,serif; font-size:1.3rem; color:var(--ink-faint);
  font-variant-numeric:tabular-nums; line-height:1.2}
.abody{min-width:0}
.atitle{display:block; font-weight:650; font-size:.95rem; margin-bottom:4px}
.awhy{display:block; font-weight:400; color:var(--ink-soft); font-size:.85rem}
.ameta{display:flex; flex-direction:column; gap:5px; align-items:flex-start}
.eff{color:var(--ink-soft); font-size:.8rem}
ul.plain{margin:14px 0 0; padding-left:20px; color:var(--ink-soft); font-size:.9rem}
ul.plain li{margin-bottom:7px}
footer{color:var(--ink-faint); font-size:.8rem; border-top:1px solid var(--line);
  padding-top:20px}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}

@media print{
  /* A PDF is printed on paper, not in the viewer's theme — pin the light set. */
  :root{
    --ground:#FFFFFF; --surface:#FFFFFF; --sunken:#F4F1F7;
    --ink:#1A1522; --ink-soft:#4A4356; --ink-faint:#6E6679;
    --line:#CFC6DA; --line-soft:#E4DEEC;
    --accent:#5A3D8C; --accent-soft:#EFE9F7;
    --ok:#1F5F42; --ok-bg:#E4F2EB; --warn:#7A4E0A; --warn-bg:#F8EEDA;
    --bad:#8C2634; --bad-bg:#F8E6E8;
  }
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
  .card,.chap,.roster{break-inside:avoid}
  .card{padding:12px 14px}
  .chap{padding:12px 14px}
  .grid-chaps,.grid-rosters{grid-template-columns:1fr 1fr; gap:10px}
  .pmail{font-size:7pt}
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


def page(title, body, extra_css="", script=""):
    """Wrap a body fragment in a complete, self-contained HTML document.

    The doctype and charset are not decoration: without a doctype Chrome renders
    the PDF in quirks mode, and without a declared charset it guesses the
    encoding of pages that legitimately contain `españa`, `Montréal` and `—`.
    Write the result with `encoding="utf-8"`, never the locale default.
    """
    return ('<!doctype html>\n<meta charset="utf-8">\n'
            '<title>%s</title>\n<style>%s%s</style>\n<div class="wrap">%s</div>%s\n'
            % (html.escape(str(title)), BASE_CSS, extra_css, body,
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

    # Remove any earlier render first: if Chrome fails without writing, a stale
    # file left in place is indistinguishable from a fresh one.
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    # as_uri(), not "file://" + path: a '#' or '?' in the output name would
    # otherwise start a URL fragment/query and silently render a different file.
    url = pathlib.Path(html_path).absolute().as_uri()
    try:
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             "--print-to-pdf=" + str(pdf_path), url],
            capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise SystemExit(
            "Chrome hung for %ds rendering %s — usually another Chrome instance "
            "holding the profile lock. Quit Chrome and retry, or pass --no-pdf "
            "and print %s by hand." % (timeout_s, pdf_path, html_path))

    stderr = (proc.stderr or "").strip()[:800]
    if proc.returncode != 0:
        raise SystemExit(
            "Chrome failed to render %s (exit %d).\n%s\nThe HTML is intact at %s "
            "— open it and print to PDF by hand. Do not use LibreOffice."
            % (pdf_path, proc.returncode, stderr, html_path))

    if not os.path.exists(pdf_path):
        raise SystemExit(
            "Chrome exited 0 but wrote no PDF at %s. This is usually another "
            "Chrome instance holding the profile lock — quit Chrome and retry, "
            "or pass --no-pdf and print %s by hand.\n%s"
            % (pdf_path, html_path, stderr))
    with open(pdf_path, "rb") as fh:
        head = fh.read(5)
    if head != b"%PDF-" or os.path.getsize(pdf_path) < 1024:
        raise SystemExit(
            "Chrome exited 0 but produced no usable PDF at %s (%d bytes). Quit "
            "any running Chrome and retry, or pass --no-pdf and print %s by "
            "hand.\n%s" % (pdf_path, os.path.getsize(pdf_path), html_path, stderr))
    os.chmod(pdf_path, 0o600)
    return pdf_path


def _repo_root(path):
    """Repo root containing `path`, or None when it lives outside any repo.

    Resolved from the path's own directory, not the process cwd — running the
    script from elsewhere must not decide whether the *output* is protected.
    """
    probe_dir = os.path.dirname(os.path.abspath(path)) or os.sep
    while not os.path.isdir(probe_dir) and probe_dir != os.sep:
        probe_dir = os.path.dirname(probe_dir)   # output dir may not exist yet
    try:
        proc = subprocess.run(["git", "-C", probe_dir, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit(
            "REFUSING TO RUN: git is not installed, so this cannot verify that "
            "%s is ignored. These files hold Slack member names, email addresses "
            "and 2FA/admin flags; install git or point --cache/--out outside any "
            "repository." % path)
    return proc.stdout.strip() if proc.returncode == 0 else None


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
        # A directory is probed through a child: `check-ignore` answers
        # differently for a bare directory name, and this works before the
        # directory exists.
        probe = os.path.join(path, "probe") if path.endswith(os.sep) else path
        ignored = subprocess.run(["git", "-C", root, "check-ignore", "-q", probe],
                                 capture_output=True).returncode == 0
        # .gitignore has no effect on an already-tracked file, so a report
        # committed before these rules landed would still ride along on `git
        # add -A` while check-ignore reports it as ignored.
        tracked = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", path],
                                 capture_output=True).returncode == 0
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
