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

It is checked by `--check` in CI, so a stale tokens file fails the build rather
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
    """The design system's own <style> text, out of the bundle."""
    with open(path, encoding="utf-8") as fh:
        page = fh.read()
    m = re.search(r'<script type="__bundler/template"[^>]*>(.*?)</script>',
                  page, re.S)
    if not m:
        raise SystemExit(
            "ABORT: %s has no __bundler/template block — it is not a design "
            "bundle of the shape this extractor understands." % path)
    # The block is a JSON string literal; json.loads gives back the real markup
    # (it carries \\u002F-escaped closing tags, so naive slicing corrupts it).
    markup = json.loads(m.group(1).strip())
    styles = re.findall(r"<style>(.*?)</style>", markup, re.S)
    if not styles:
        raise SystemExit("ABORT: no <style> block inside the bundle template.")
    return max(styles, key=len)


def extract(css):
    """The `:root{…}` token block plus the metric-matched fallback face."""
    root = re.search(r"(:root\s*\{.*?\n\})", css, re.S)
    if not root:
        raise SystemExit(
            "ABORT: no :root token block in the design system. The bundle "
            "format changed — this extractor must be updated, not bypassed.")
    parts = [root.group(1)]

    fallback = re.search(
        r"(@font-face\s*\{[^}]*'Instrument Sans Fallback'[^}]*\})", css, re.S)
    if fallback:
        # Metric-matched fallback only. The real webfont faces in the bundle
        # point at UUID blobs that mean nothing outside it; report_style.py
        # embeds its own copy from assets/fonts/.
        parts.append(fallback.group(1))
    return HEADER + "\n" + "\n\n".join(p.strip() for p in parts) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed file is stale (for CI)")
    args = ap.parse_args()

    want = extract(template_css(BUNDLE))
    if args.check:
        have = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
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
