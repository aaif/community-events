#!/usr/bin/env python3
"""
AAIF chapter organizer badge generator.

Usage:
    python make_badges.py OUTDIR "Mexico City" "Sao Paulo" "Dublin"
    python make_badges.py OUTDIR --slug delhi_ncr "Delhi NCR"
    python make_badges.py OUTDIR --upright-bottom "Dublin"

Produces, per chapter, in OUTDIR/<slug>/ :
    organizer_badge_<slug>_colour.svg
    organizer_badge_<slug>_white.svg
    organizer_badge_<slug>_colour_1000.png
    organizer_badge_<slug>_white_1000.png
"""
import os, re, sys, unicodedata
from xml.sax.saxutils import escape as _xml_escape

# --- Brand tokens -----------------------------------------------------------
ORANGE = "#E9852B"   # primary accent: ring, city text, endpoint dots, ORGANIZER pill
INK    = "#14141C"   # badge background + text knocked out of the pill
LILAC  = "#A99BCB"   # secondary: inner rings, foundation arc, COMMUNITY EVENTS
CREAM  = "#F7F5EF"   # AAIF wordmark
FONTS  = "Liberation Sans, DejaVu Sans, sans-serif"

BOTTOM_ARC_ORIG = "M 860.0,500 A 360.0,360.0 0 0,1 140.0,500"
BOTTOM_ARC_UPRIGHT = "M 140.0,500 A 360.0,360.0 0 0,0 860.0,500"
# True => bottom text reads left-to-right instead of upside-down along the arc
# (set via --upright-bottom; useful when a city name is long enough to wrap
# past the arc's bottom, where the default orientation reads inverted).
UPRIGHT_BOTTOM = False

def _svg(city, ring_fill, c_ring, c_city, c_arc, c_dot, c_word, c_sub, pill, c_pill_text):
    bottom = BOTTOM_ARC_UPRIGHT if UPRIGHT_BOTTOM else BOTTOM_ARC_ORIG
    city = _xml_escape(city)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><circle cx="500" cy="500" r="470.0" fill="{ring_fill}" stroke="{c_ring}" stroke-width="24.0"/>
<circle cx="500" cy="500" r="432.0" fill="none" stroke="{c_arc}" stroke-width="4.0"/>
<circle cx="500" cy="500" r="300.0" fill="none" stroke="{c_arc}" stroke-width="2.0" opacity="0.5"/>
<path id="tp500500" d="M 140.0,500 A 360.0,360.0 0 0,1 860.0,500" fill="none"/>
<path id="bp500500" d="{bottom}" fill="none"/>
<text font-family="{FONTS}" font-weight="bold" font-size="48.0" letter-spacing="5.0" fill="{c_city}"><textPath href="#tp500500" startOffset="50%" text-anchor="middle">{city}</textPath></text>
<text font-family="{FONTS}" font-weight="bold" font-size="37.0" letter-spacing="4.0" fill="{c_arc}"><textPath href="#bp500500" startOffset="50%" text-anchor="middle">AGENTIC AI FOUNDATION</textPath></text>
<circle cx="140.0" cy="500" r="11.0" fill="{c_dot}"/>
<circle cx="860.0" cy="500" r="11.0" fill="{c_dot}"/>
<text x="500" y="470.0" font-family="{FONTS}" font-weight="bold" font-size="150.0" text-anchor="middle" fill="{c_word}">AAIF</text>
<text x="500" y="540.0" font-family="{FONTS}" font-weight="bold" font-size="36.0" letter-spacing="6.0" text-anchor="middle" fill="{c_sub}">COMMUNITY EVENTS</text>
<rect x="296.0" y="572.0" width="408.0" height="88.0" rx="44.0" {pill}/>
<text x="500" y="632.0" font-family="{FONTS}" font-weight="bold" font-size="46.0" letter-spacing="6.0" text-anchor="middle" fill="{c_pill_text}">ORGANIZER</text></svg>'''

def colour_svg(city):
    return _svg(city, INK, ORANGE, ORANGE, LILAC, ORANGE, CREAM, LILAC,
                f'fill="{ORANGE}"', INK)

def white_svg(city):
    W = "#FFFFFF"
    return _svg(city, "none", W, W, W, W, W, W,
                f'fill="none" stroke="{W}" stroke-width="5.0"', W)

def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s

def build(name, outroot, slug=None):
    slug = slug or slugify(name)
    city = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    d = os.path.join(outroot, slug)
    os.makedirs(d, exist_ok=True)
    made = []
    for variant, svg in (("colour", colour_svg(city)), ("white", white_svg(city))):
        sp = os.path.join(d, f"organizer_badge_{slug}_{variant}.svg")
        with open(sp, "w") as f:
            f.write(svg)
        made.append(sp)
        pp = os.path.join(d, f"organizer_badge_{slug}_{variant}_1000.png")
        import cairosvg  # lazy: plan-only callers of this module never need it installed
        cairosvg.svg2png(url=sp, write_to=pp, output_width=1000, output_height=1000)
        made.append(pp)
    return slug, made

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)
    outroot, rest = args[0], args[1:]
    override = None
    names = []
    i = 0
    while i < len(rest):
        if rest[i] == "--upright-bottom":
            globals()["UPRIGHT_BOTTOM"] = True; i += 1
        elif rest[i] == "--slug":
            if i + 1 >= len(rest):
                print("--slug requires a value"); sys.exit(1)
            override = rest[i+1]; i += 2
        else:
            names.append(rest[i]); i += 1
    for n in names:
        slug, made = build(n, outroot, override)
        override = None
        print(f"{n} -> {slug}/  ({len(made)} files)")
