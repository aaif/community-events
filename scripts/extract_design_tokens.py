#!/usr/bin/env python3
"""Regenerate `design/aaif-tokens.css` from `design/aaif-design-system.html`.

The design system ships as a self-extracting bundle: its markup and CSS live
JSON-encoded inside a `<script type="__bundler/template">` tag, with fonts and
images as UUID-keyed blobs. That is fine for opening in a browser and useless
to a stylesheet, so this lifts the **token layer** — the `:root` block and the
metric-matched fallback face — into a plain CSS file the report generator can
read.

Tokens only, deliberately. The bundle's semantic element rules (`h1`, `p`, `a`,
`code`) assume a marketing page; the reports have their own components and only
need the vocabulary those components resolve against. One source of truth for
the values, two sets of components drawn with them.

Run after replacing the bundle:

    python3 scripts/extract_design_tokens.py

It is checked by `--check` in pre-commit and CI, so a stale tokens file fails the build rather
than silently drifting from the design system it claims to follow.
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(ROOT, "design", "aaif-design-system.html")
OUT = os.path.join(ROOT, "design", "aaif-tokens.css")

HEADER = """/* AAIF design tokens — GENERATED, DO NOT EDIT.
 *
 * Source: design/aaif-design-system.html
 * Regenerate: python3 scripts/extract_design_tokens.py
 *
 * Edit the design system, not this file. See DESIGN.md.
 */
"""


def template_css(path):
    """The design system's own <style> text, out of the bundle at `path`."""
    with open(path, encoding="utf-8") as fh:
        return template_css_from_text(fh.read())


def template_css_from_text(page):
    """Same, from an in-memory bundle. Split out so the tests need no file."""
    m = re.search(r'<script type="__bundler/template"[^>]*>(.*?)</script>',
                  page, re.S)
    if not m:
        raise SystemExit(
            "ABORT: no __bundler/template block — this is not a design bundle "
            "of the shape this extractor understands.")
    # The block is a JSON string literal; json.loads gives back the real markup
    # (it carries \\u002F-escaped closing tags, so naive slicing corrupts it).
    markup = json.loads(m.group(1).strip())
    styles = re.findall(r"<style>(.*?)</style>", markup, re.S)
    if not styles:
        raise SystemExit("ABORT: no <style> block inside the bundle template.")
    return max(styles, key=len)


#: Tokens `lib/aaif_events/report_style.py` maps its own vocabulary onto. A
#: design-system release that renames its neutrals would otherwise extract
#: cleanly, pass --check after a regenerate, and leave every report silently
#: falling through to the hardcoded var() fallbacks — the "almost-right palette
#: that drifts for months" this whole seam exists to prevent.
REQUIRED = ("--ink", "--ink-2", "--ink-3", "--ink-4", "--line", "--line-2",
            "--paper", "--paper-2", "--paper-3", "--void", "--void-2",
            "--void-3", "--ink-inv", "--ink-inv-2", "--ink-inv-3", "--line-inv",
            "--spec-2", "--success", "--warning", "--danger",
            "--font-mono", "--tr-tight", "--tr-eyebrow")


def root_blocks(css):
    """Every `:root{...}` block, brace-matched rather than regex-guessed.

    A non-greedy regex stops at the first `}`, which for a block containing a
    nested rule returns a fragment; an anchored one still matches a `:root`
    nested inside `@media print{...}` when that happens to sit at column 0.
    Counting braces is the only thing that gets both right, and it is six lines.
    """
    out = []
    for m in re.finditer(r":root\s*\{", css):
        depth, i = 0, m.start()
        while i < len(css):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[m.start():i + 1])
                    break
            i += 1
    return out


def extract(css):
    """The `:root{...}` token block plus the metric-matched fallback face."""
    # Select by CONTENT, not by position: the design system's token block is
    # the one that defines the tokens the reports resolve against. Position is
    # not a reliable signal — a `:root` inside `@media print{...}` can appear
    # first and at column 0, and picking it yields a file that is valid CSS,
    # passes --check forever, and silently defines none of the palette.
    candidates = [b for b in root_blocks(css)
                  if all(t + ":" in b for t in REQUIRED)]
    if not candidates:
        blocks = root_blocks(css)
        if not blocks:
            raise SystemExit(
                "ABORT: no :root block in the design system. The bundle format "
                "changed — this extractor must be updated, not bypassed.")
        absent = [t for t in REQUIRED
                  if not any(t + ":" in b for b in blocks)]
        raise SystemExit(
            "ABORT: no :root block defines all the tokens the reports resolve "
            "against; %d missing across %d block(s):\n  %s\nUpdate the mapping "
            "in lib/aaif_events/report_style.py to match the new names."
            % (len(absent), len(blocks), ", ".join(absent)))
    parts = [max(candidates, key=len)]

    fallback = re.search(
        r"(@font-face\s*\{[^}]*'Instrument Sans Fallback'[^}]*\})", css, re.S)
    if fallback:
        # Metric-matched fallback only. The real webfont faces in the bundle
        # point at UUID blobs that mean nothing outside it; report_style.py
        # embeds its own copy from assets/fonts/.
        parts.append(fallback.group(1))
    else:
        # Silently optional would mean the output is merely shorter and CI says
        # "stale" rather than "the metric-matched face vanished".
        raise SystemExit(
            "ABORT: the 'Instrument Sans Fallback' face is gone from the "
            "design system. Without it, layout shifts before the webfont "
            "loads. Update this extractor deliberately if that was intended.")

    out = HEADER + "\n" + "\n\n".join(p.strip() for p in parts) + "\n"

    # Structural sanity: an unbalanced block is invalid CSS that fails silently
    # in the browser — every report renders in fallback greys with no error.
    if out.count("{") != out.count("}"):
        raise SystemExit(
            "ABORT: extracted CSS has %d '{' and %d '}'. Refusing to write a "
            "brace-unbalanced token file — it would render as nothing."
            % (out.count("{"), out.count("}")))
    absent = [t for t in REQUIRED if t + ":" not in out]
    if absent:
        raise SystemExit(
            "ABORT: the design system no longer defines %d token(s) the "
            "reports resolve against:\n  %s\nUpdate the mapping in "
            "lib/aaif_events/report_style.py to match the new names."
            % (len(absent), ", ".join(absent)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed file is stale (for CI)")
    args = ap.parse_args()

    want = extract(template_css(BUNDLE))
    if args.check:
        have = ""
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as fh:
                have = fh.read()
        if have != want:
            print("ABORT: design/aaif-tokens.css is stale. Run:\n"
                  "  python3 scripts/extract_design_tokens.py", file=sys.stderr)
            return 1
        print("design tokens up to date")
        return 0

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(want)
    n = len(re.findall(r"^\s*--", want, re.M))
    print("wrote %s (%d tokens)" % (os.path.relpath(OUT, ROOT), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
