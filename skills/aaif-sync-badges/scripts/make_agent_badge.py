#!/usr/bin/env python3
"""AAIF chapter organizer badge — design-system variant.

Unlike make_badges.py (a self-contained, hand-tuned palette), this generator
draws from the actual AAIF design system: `report_style.font_css()` embeds
the real Instrument Sans, and each chapter's mascot comes from
`agent_art.chapter_scene()` / `agent_art.agent()` -- the same deterministic
per-chapter colour already used for chapter Icons/ folders and decks, so a
chapter's badge and its other AAIF-generated art always agree.

This intentionally takes the `lib/aaif_events` coupling that AGENTS.md
otherwise asks skill scripts to avoid (see its "Architecture" section): the
whole point of this variant is to be provably on-system, which means calling
the system's own renderer and mascot generator rather than re-implementing
either. `skills/aaif-create-chapter/scripts/restyle_design_system.py` takes
the same trade-off for the same reason.

Rendered via headless Chrome (the repo's one allowed SVG->PNG renderer -- see
DESIGN.md), so a Chrome/Chromium install is required; make_badges.py's
`cairosvg` route has no such requirement.

Usage:
    python make_agent_badge.py OUTDIR "Mexico City" "Dublin"
    python make_agent_badge.py OUTDIR --slug delhi_ncr "Delhi NCR"

Produces, per chapter, in OUTDIR/<slug>/ :
    organizer_badge_<slug>_agent.svg
    organizer_badge_<slug>_agent_1000.png
"""
import os, re, sys, unicodedata
from xml.sax.saxutils import escape as _xml_escape

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "lib"))
from aaif_events import agent_art, report_style  # noqa: E402

INK = "#0A0A0A"
INK_3 = "#4A4A4A"
PAPER = "#FFFFFF"
LINE_2 = "#CFCFC9"


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s


def _svg(city, chapter_name):
    """One badge: chapter name on the top arc, the chapter's own agent mascot
    (deterministic colour/action/mirroring from agent_art.chapter_scene), the
    AAIF wordmark, and an ORGANIZER pill -- all in real design-system tokens."""
    spec, secondary, action, ridge, mirrored = agent_art.chapter_scene(chapter_name)
    asz = 300
    ax, ay = 500 - asz / 2, 330
    art = agent_art.agent(ax, ay, asz, spec=spec)
    if mirrored:
        art = f'<g transform="translate(1000,0) scale(-1,1)">{art}</g>'
    accent = agent_art.hue(secondary)
    city = _xml_escape(city)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000">
<style>{report_style.font_css()}</style>
<circle cx="500" cy="500" r="480" fill="{PAPER}"/>
<circle cx="500" cy="500" r="460" fill="none" stroke="{LINE_2}" stroke-width="2"/>
<path id="tp" d="M 160,500 A 340,340 0 0 1 840,500" fill="none"/>
<text font-family="Instrument Sans" font-weight="600" font-size="46" letter-spacing="6"
      fill="{INK}"><textPath href="#tp" startOffset="50%" text-anchor="middle">{city}</textPath></text>
{art}
<circle cx="500" cy="675" r="4" fill="{accent}"/>
<text x="500" y="720" font-family="Instrument Sans" font-weight="500" font-size="26"
      letter-spacing="5" text-anchor="middle" fill="{INK_3}">AGENTIC AI FOUNDATION</text>
<rect x="368" y="752" width="264" height="60" rx="30" fill="{INK}"/>
<text x="500" y="791" font-family="Instrument Sans" font-weight="600" font-size="26"
      letter-spacing="4" text-anchor="middle" fill="{PAPER}">ORGANIZER</text>
</svg>'''


def build(name, outroot, slug=None):
    slug = slug or slugify(name)
    city = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    d = os.path.join(outroot, slug)
    os.makedirs(d, exist_ok=True)
    svg = _svg(city, name)
    sp = os.path.join(d, f"organizer_badge_{slug}_agent.svg")
    with open(sp, "w") as f:
        f.write(svg)
    pp = os.path.join(d, f"organizer_badge_{slug}_agent_1000.png")
    agent_art.render_png(svg, pp, (1000, 1000), ground=PAPER)
    return slug, [sp, pp]


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)
    outroot, rest = args[0], args[1:]
    override = None
    names = []
    i = 0
    while i < len(rest):
        if rest[i] == "--slug":
            if i + 1 >= len(rest):
                print("--slug requires a value"); sys.exit(1)
            override = rest[i + 1]; i += 2
        else:
            names.append(rest[i]); i += 1
    for n in names:
        slug, made = build(n, outroot, override)
        override = None
        print(f"{n} -> {slug}/  ({len(made)} files)")
