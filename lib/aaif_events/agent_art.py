"""Background plates and the agent motif, generated from the design system.

The two hero decks ship one colourful plate between them — a single ~700KB PNG
that every event in the estate has therefore used. This draws a set of them, and
draws them *from the tokens* rather than by hand, so a plate cannot quietly stop
matching the brand the way the hand-authored one did.

Everything here is stdlib. Art is authored as SVG, rasterised with the headless
Chrome route `DESIGN.md` already documents (never LibreOffice), and animation is
packed into GIF89a by the encoder at the bottom of this file.

**Which plates move, and why those.** The system licenses motion that is
"constant-speed and small". Two plates are animated and they are deliberately
the *flat* ones — the spectrum-disc field and the bracket plate. A GIF is
palette-indexed, so it renders flat vector art exactly and a smooth gradient
only approximately; animating the gradient plates would have meant either
visible banding across a 1920×1080 background or a multi-megabyte file cloned
into 83 chapter decks. The gradient plates stay static PNG, where they are
pixel-perfect. Flat art moves, smooth art doesn't.

**Frame 1 has to stand alone.** The Slides API thumbnail, every PDF export, and
the Luma/LinkedIn banner crops all show a single frame, so each animation starts
at its rest pose rather than mid-travel.
"""
import os
import re
import subprocess
import zlib

from aaif_events.ooxml_style import TOKENS, token

#: 16:9 for `Event-Hero.pptx`, 1:1 for `Event-Hero-Square.pptx`.
ASPECTS = {"wide": (1920, 1080), "square": (1080, 1080)}

#: The six plates, in the order they are offered to an organizer.
PLATES = ("hero-gradient", "soft-plate", "night-ridge", "bracket",
          "disc-corner", "spectrum-rail")

#: The two that move. See the module docstring for why these and not the others.
ANIMATED = ("spectrum-rail", "bracket", "disc-corner")

#: One hue leads each plate. spec-1..5 are the primaries the system says lead a
#: surface; the plate's secondary marks derive as (primary + 5), which is the
#: chapter-plate rule.
LEAD = {"hero-gradient": 1, "soft-plate": 3, "night-ridge": 3,
        "bracket": 2, "disc-corner": 1, "spectrum-rail": 2}


def hue(n):
    """`--spec-N` as `#RRGGBB`. Wraps 1..10 so `(primary + 5)` is always valid."""
    return "#" + token("spec-%d" % (((n - 1) % 10) + 1))


def ink():
    return "#" + token("ink")


def paper():
    return "#" + token("paper")


def void():
    return "#" + token("void")


# ------------------------------------------------------------------ the agent --
#: The agent, on its 48-unit grid, exactly as the design system draws it: shell,
#: near-black visor, two eyes, two pods, round-cap legs and antenna. The visor is
#: load-bearing — a saturated shell alone dies on a light ground — so it is drawn
#: at full black on every surface.
_AGENT = """\
<g class="agent" transform="translate({x},{y}) scale({s})">
  <path d="M24 10V6.6" stroke="{stroke}" stroke-width="2.5" stroke-linecap="round" fill="none"/>
  <circle cx="24" cy="3.9" r="2.4" fill="{hue}"/>
  <path d="M17 37v6M31 37v6" stroke="{stroke}" stroke-width="2.5" stroke-linecap="round" fill="none"/>
  <rect x="3.6" y="19.4" width="4.6" height="9.6" rx="2.3" fill="{hue}"/>
  <rect x="39.8" y="19.4" width="4.6" height="9.6" rx="2.3" fill="{hue}"/>
  <rect x="9" y="10" width="30" height="28" rx="10" fill="{hue}"/>
  <rect x="13.5" y="16.4" width="21" height="12.2" rx="6.1" fill="{visor}"/>
  <circle cx="19.8" cy="22.5" r="2.45" fill="{glow}"/>
  <circle cx="28.2" cy="22.5" r="2.45" fill="{glow}"/>
  <rect x="19" y="31.8" width="10" height="2.4" rx="1.2" fill="{visor}" opacity="0.26"/>
</g>"""


#: Where the agent's feet actually are within its 48-unit box. The legs end at
#: y=43, not y=48, so placing the BOX on a ridge leaves the agent hovering five
#: units above it — small on screen, and exactly the "standing near the ridge
#: rather than on it" the composition rule exists to prevent.
FEET = 43.0 / 48.0


def agent(x, y, size, spec=3, on_dark=False):
    """The agent at `size` px tall, its box top-left at (x, y).

    `on_dark` switches the antenna and legs to white — the one per-surface
    variant the system allows, because a black stroke vanishes on the plate.
    """
    return _AGENT.format(
        x=x, y=y, s=size / 48.0, hue=hue(spec), visor=ink(),
        glow="#FFFFFF", stroke="rgba(255,255,255,0.92)" if on_dark else ink())


# ------------------------------------------------------------------- plates ---
def _ridge(w, h, y, amp, fill, seed=0):
    """One edge-to-edge terrain ridge as a cubic path. Ridges run the full width
    so the plate reads as a landscape, never as a floating shape."""
    return ('<path d="M0 {y1} C {c1x} {c1y}, {c2x} {c2y}, {w} {y2} L {w} {h} L 0 {h} Z" '
            'fill="{f}"/>').format(
        y1=y + amp * 0.3, c1x=w * 0.3, c1y=y - amp, c2x=w * 0.68, c2y=y + amp * 1.2,
        w=w, y2=y - amp * 0.2 + seed, h=h, f=fill)


def _ridge_y(w, h, y, amp, seed, x):
    """The ridge's height at `x` — solved, not eyeballed, so the agent stands ON
    the ridge rather than near it (the composition rule the system states)."""
    t = x / float(w)
    p0, p1, p2, p3 = y + amp * 0.3, y - amp, y + amp * 1.2, y - amp * 0.2 + seed
    u = 1 - t
    return (u ** 3) * p0 + 3 * (u ** 2) * t * p1 + 3 * u * (t ** 2) * p2 + (t ** 3) * p3


def _svg(w, h, body, bg):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d"><rect width="%d" height="%d" fill="%s"/>%s</svg>'
            % (w, h, w, h, w, h, bg, body))


def plate(kind, aspect, frame=0.0):
    """One plate as an SVG string. `frame` is 0..1 through the loop and is
    ignored by the static plates.

    **Every plate is a dark ground, and every mark stays out of the type.** The
    hero decks set their title, subtitle and date as one left-aligned stack from
    roughly 24% to 72% of the height, with a header rule at 11% and a footer row
    at 82%. A plate is a BACKGROUND for that stack, so its marks live at the
    edges — a corner, the bottom rail, a disc bleeding off the top right — and
    the middle stays clear. The one existing plate in the estate ignored this
    and its eyebrow and subtitle are unreadable.

    Dark, not light, for the same reason: the deck's colour slide sets its type
    in white. A light plate would need a different text colour, i.e. a different
    slide, and the deck already has a white slide for that.
    """
    if kind not in PLATES:
        # Checked before the LEAD lookup, which would otherwise raise a bare
        # KeyError naming a dict the caller has never heard of.
        raise ValueError("unknown plate %r — expected one of %s"
                         % (kind, ", ".join(PLATES)))
    if aspect not in ASPECTS:
        raise ValueError("unknown aspect %r — expected one of %s"
                         % (aspect, ", ".join(sorted(ASPECTS))))
    w, h = ASPECTS[aspect]
    lead = LEAD[kind]
    sec = lead + 5           # the chapter-plate rule for a secondary hue

    if kind == "hero-gradient":
        # The hero/CTA plate: a black ground carrying indigo -> blue -> teal.
        # The radii are the CSS ellipses' WIDTHS (120%/90%/100%), not circle
        # radii — shrinking them to circles separates the three stops into
        # discs instead of one merged plate.
        body = """
<defs>
 <radialGradient id="g1" cx="15%%" cy="20%%" r="120%%">
  <stop offset="0" stop-color="%s" stop-opacity="1"/><stop offset="0.55" stop-color="%s" stop-opacity="0"/></radialGradient>
 <radialGradient id="g2" cx="85%%" cy="30%%" r="90%%">
  <stop offset="0" stop-color="%s" stop-opacity="1"/><stop offset="0.6" stop-color="%s" stop-opacity="0"/></radialGradient>
 <radialGradient id="g3" cx="50%%" cy="100%%" r="100%%">
  <stop offset="0" stop-color="%s" stop-opacity="1"/><stop offset="0.55" stop-color="%s" stop-opacity="0"/></radialGradient>
</defs>
<rect width="%d" height="%d" fill="url(#g1)"/>
<rect width="%d" height="%d" fill="url(#g2)"/>
<rect width="%d" height="%d" fill="url(#g3)"/>""" % (
            hue(1), hue(1), hue(2), hue(2), hue(3), hue(3), w, h, w, h, w, h)
        return _svg(w, h, body, void())

    if kind == "soft-plate":
        # The people-card / daily-briefing plate: the same idea at much lower
        # saturation, on --void-2. This is the one to reach for when the title
        # is long, because it is nearly plain black behind the type.
        body = """
<defs>
 <radialGradient id="s1" cx="20%%" cy="0%%" r="120%%">
  <stop offset="0" stop-color="%s" stop-opacity="0.55"/><stop offset="0.6" stop-color="%s" stop-opacity="0"/></radialGradient>
 <radialGradient id="s2" cx="100%%" cy="100%%" r="100%%">
  <stop offset="0" stop-color="%s" stop-opacity="0.45"/><stop offset="0.6" stop-color="%s" stop-opacity="0"/></radialGradient>
</defs>
<rect width="%d" height="%d" fill="url(#s1)"/>
<rect width="%d" height="%d" fill="url(#s2)"/>""" % (
            hue(1), hue(1), hue(3), hue(3), w, h, w, h)
        return _svg(w, h, body, "#" + token("void-2"))

    if kind == "night-ridge":
        # The dune situation, inverted onto the black plate and pushed into the
        # bottom 18% so it sits under the footer rule rather than through the
        # date line. Depth still comes from three VALUES, but on a dark ground
        # those are three tints of the lead hue rather than the sand ramp --
        # sand on black would read as a light band, not as distance.
        body = ""
        for i, (yf, op) in enumerate(((0.86, 0.22), (0.90, 0.42), (0.94, 0.75))):
            body += ('<g opacity="%.2f">%s</g>'
                     % (op, _ridge(w, h, h * yf, h * 0.022, hue(lead), seed=h * 0.006 * i)))
        body += _ridge(w, h, h * 0.975, h * 0.012, "#" + token("void"))
        return _svg(w, h, body, void())

    if kind == "bracket":
        # The mark's own motif as corner ticks on a full-bleed band: three
        # stacked squares at each corner. The 2x2 dot grid that would normally
        # sit between them is NOT drawn centred here -- that is exactly where
        # the title goes -- so the dots run as a small rail in the top right,
        # and the lead steps around them.
        u = h * 0.020
        pad = h * 0.06
        body = ""
        for cx, cy, sx, sy in ((pad, pad, 1, 1), (w - pad, pad, -1, 1),
                               (pad, h - pad, 1, -1), (w - pad, h - pad, -1, -1)):
            for i in range(3):
                body += '<rect x="%f" y="%f" width="%f" height="%f" fill="%s" opacity="0.55"/>' % (
                    cx if sx > 0 else cx - u,
                    cy + sy * i * u * 1.7 - (0 if sy > 0 else u), u, u,
                    "#" + token("ink-inv"))
        d = h * 0.030
        gap = d * 2.9
        gx, gy = w - pad - gap * 1.9, pad + gap * 1.9
        lit = int(frame * 4) % 4
        for i, (dx, dy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
            body += '<circle cx="%f" cy="%f" r="%f" fill="%s" opacity="%s"/>' % (
                gx + dx * gap / 2, gy + dy * gap / 2, d,
                hue(lead) if i == lit else "#" + token("ink-inv"),
                "1" if i == lit else "0.5")
        return _svg(w, h, body, void())

    if kind == "disc-corner":
        # One hue disc bleeding off the top-right corner -- the single largest
        # mark in the set, and the plate to use when the title is short. It
        # breathes: the radius moves by a few percent over the loop, which is
        # the smallest constant-speed motion the system's vocabulary contains.
        r = h * (0.46 + 0.03 * abs(0.5 - frame) * 2)
        body = '<circle cx="%f" cy="%f" r="%f" fill="%s"/>' % (w * 0.96, h * 0.06, r, hue(lead))
        body += ('<circle cx="%f" cy="%f" r="%f" fill="none" stroke="%s" '
                 'stroke-width="%f" opacity="0.35"/>'
                 % (w * 0.96, h * 0.06, r * 1.22, hue(sec), max(1.5, h * 0.0025)))
        return _svg(w, h, body, void())

    if kind == "spectrum-rail":
        # Ten discs on a rail along the bottom edge, one hue each -- the only
        # place the system lets the whole spectrum appear at once, because none
        # of them is a fill on a component. The motion is the system's own "one
        # hue leads a surface" rule made literal: the lead steps along the rail,
        # one disc enlarged at a time. Frame 1 is disc one leading, so the
        # single frame every PDF and thumbnail shows is a finished composition.
        r = h * 0.020
        lane = h * 0.945
        lead_i = int(frame * 10) % 10
        body = ('<line x1="%f" y1="%f" x2="%f" y2="%f" stroke="%s" stroke-width="%f" '
                'opacity="0.30"/>' % (w * 0.06, lane, w * 0.94, lane,
                                      "#" + token("ink-inv"), max(1, h * 0.0015)))
        for i in range(10):
            x = w * (0.06 + i * 0.0978)
            body += '<circle cx="%f" cy="%f" r="%f" fill="%s"/>' % (
                x, lane, r * (1.9 if i == lead_i else 1.0), hue(i + 1))
        return _svg(w, h, body, void())

    raise ValueError("unknown plate %r" % kind)


# -------------------------------------------------------------- rasterising --
def _chrome():
    from aaif_events.report_style import find_chrome
    exe = find_chrome()
    if not exe:
        raise RuntimeError(
            "no headless Chrome found — it is the only renderer this repo "
            "allows (LibreOffice substitutes fonts and drops OOXML). Install "
            "Chrome or Chromium.")
    return exe


def render_png(svg, out_path, size):
    """Rasterise an SVG string to a PNG with headless Chrome.

    Chrome screenshots the viewport, so the SVG is wrapped in a page whose body
    has zero margin and exactly the target size — otherwise the plate lands
    inset by the default 8px body margin, which is invisible until the deck is
    projected.
    """
    w, h = size
    html = ("<!doctype html><meta charset='utf-8'>"
            "<style>html,body{margin:0;padding:0;background:transparent;"
            "width:%dpx;height:%dpx;overflow:hidden}svg{display:block}</style>%s"
            % (w, h, svg))
    page = out_path + ".html"
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(html)
    try:
        subprocess.run([_chrome(), "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1",
                        "--screenshot=" + out_path,
                        "--window-size=%d,%d" % (w, h),
                        "file://" + os.path.abspath(page)],
                       check=True, capture_output=True, timeout=120)
    finally:
        if os.path.exists(page):
            os.remove(page)
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 100:
        raise RuntimeError("Chrome produced no usable PNG at %s" % out_path)
    return out_path


# ------------------------------------------------------------- PNG -> pixels --
def read_png(path):
    """(width, height, rows) with rows as bytearrays of RGB triples.

    A minimal reader for exactly what Chrome writes: 8-bit truecolour, with or
    without alpha, non-interlaced. Anything else raises rather than guessing —
    a silently misread frame would show up only as a corrupt GIF.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG" % path)
    pos, idat, w = 8, [], None
    while pos < len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if kind == b"IHDR":
            w = int.from_bytes(body[0:4], "big")
            h = int.from_bytes(body[4:8], "big")
            depth, colour, _comp, _filt, interlace = body[8:13]
            if depth != 8 or colour not in (2, 6) or interlace:
                raise ValueError("unsupported PNG (depth=%d colour=%d interlace=%d)"
                                 % (depth, colour, interlace))
            channels = 3 if colour == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + ln
    if w is None:
        raise ValueError("%s has no IHDR" % path)
    raw = zlib.decompress(b"".join(idat))
    stride = w * channels
    rows, prev, at = [], bytearray(stride), 0
    for _y in range(h):
        ft = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        # Undo the per-scanline filter. This is the whole of the PNG format that
        # matters here, and getting any one case wrong shears the image.
        if ft == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ft != 0:
            raise ValueError("bad PNG filter %d" % ft)
        prev = line
        if channels == 4:
            rows.append(bytearray(b for i in range(0, stride, 4)
                                  for b in line[i:i + 3]))
        else:
            rows.append(line)
    return w, h, rows


# --------------------------------------------------------------- GIF encoder --
def _quantise(frames, limit=256):
    """(palette, {colour: index}) for the whole animation.

    Flat vector art is still *antialiased*, so a plate drawn in six colours
    reaches the encoder as a few thousand: every edge pixel is a blend. The
    palette is therefore the `limit` most frequent colours — which captures the
    flats and the bulk of each edge ramp exactly — and everything else maps to
    its nearest neighbour.

    The mapping error is measured, not assumed. A genuinely smooth gradient
    cannot survive 256 entries, and the caller raises on a large error rather
    than shipping a visibly banded 1920x1080 background; that is the whole
    reason only the flat plates are animated.
    """
    hist = {}
    for _w, _h, rows in frames:
        for row in rows:
            mv = bytes(row)
            for i in range(0, len(mv), 3):
                c = mv[i:i + 3]
                hist[c] = hist.get(c, 0) + 1
    chosen = sorted(hist, key=lambda c: -hist[c])[:limit]
    index = {c: i for i, c in enumerate(chosen)}
    total = sum(hist.values())
    drifted = 0
    for c in hist:
        if c in index:
            continue
        best, bd = 0, None
        for i, p in enumerate(chosen):
            d = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2
            if bd is None or d < bd:
                best, bd = i, d
                if d == 0:
                    break
        index[c] = best
        if bd > _DRIFT_D2:
            drifted += hist[c]
    return chosen, index, 100.0 * drifted / total


#: A colour is "drifted" when quantising moves it further than this in RGB.
_DRIFT_D2 = 24 ** 2

#: Banding is a LARGE-AREA artefact, so the guard is the share of pixels that
#: drift — not the worst single colour. Antialiased edges are one pixel wide and
#: a handful of them landing on a neighbouring palette entry is invisible; a
#: smooth gradient drifts across whole regions and shows as banding. Measured on
#: these plates: bracket 0.000%, spectrum-field 0.002%, hero-gradient 7.458%.
#: The two cases are three orders of magnitude apart, so 1% sits nowhere near
#: either and does not need retuning when a plate is edited.
MAX_DRIFT_PCT = 1.0


def _lzw(indexed, min_code_size):
    """GIF's variable-width LZW. The clear code is emitted at the start and
    whenever the dictionary fills, which is what keeps the code width bounded."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    width = min_code_size + 1
    table = {bytes([i]): i for i in range(clear)}
    nxt = end + 1
    out, cur, nbits = bytearray(), 0, 0

    def emit(code):
        nonlocal cur, nbits
        cur |= code << nbits
        nbits += width
        while nbits >= 8:
            out.append(cur & 0xFF)
            cur >>= 8
            nbits -= 8

    emit(clear)
    buf = b""
    for px in indexed:
        nb = buf + bytes([px])
        if nb in table:
            buf = nb
            continue
        emit(table[buf])
        table[nb] = nxt
        nxt += 1
        if nxt > (1 << width) and width < 12:
            width += 1
        elif nxt >= 4096:
            emit(clear)
            table = {bytes([i]): i for i in range(clear)}
            nxt, width = end + 1, min_code_size + 1
        buf = bytes([px])
    if buf:
        emit(table[buf])
    emit(end)
    if nbits:
        out.append(cur & 0xFF)
    return bytes(out)


def write_gif(frames, out_path, delay_cs=8):
    """Write an animated GIF89a from `frames` (as returned by `read_png`).

    `delay_cs` is hundredths of a second per frame. Loops forever via the
    NETSCAPE2.0 application extension, which is the only way GIF expresses it.
    """
    chosen, pal, drift = _quantise(frames)
    if drift > MAX_DRIFT_PCT:
        raise ValueError(
            "quantising this animation moves %.2f%% of pixels off their colour "
            "(limit %.2f%%) — it is a gradient, not flat art, and would band "
            "across the whole plate. Ship it as a static PNG instead."
            % (drift, MAX_DRIFT_PCT))
    bits = max(1, (len(chosen) - 1).bit_length())
    size = 1 << bits
    table = bytearray(size * 3)
    for idx, colour in enumerate(chosen):
        table[idx * 3:idx * 3 + 3] = colour
    w, h, _ = frames[0]

    out = bytearray(b"GIF89a")
    out += w.to_bytes(2, "little") + h.to_bytes(2, "little")
    out += bytes([0xF0 | (bits - 1), 0, 0]) + table
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"

    mcs = max(2, bits)
    for fw, fh, rows in frames:
        if (fw, fh) != (w, h):
            raise ValueError("frames differ in size: %dx%d vs %dx%d" % (fw, fh, w, h))
        out += b"\x21\xF9\x04\x04" + delay_cs.to_bytes(2, "little") + b"\x00\x00"
        out += b"\x2C" + (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
        out += fw.to_bytes(2, "little") + fh.to_bytes(2, "little") + b"\x00"
        indexed = bytearray()
        for row in rows:
            mv = bytes(row)
            for i in range(0, len(mv), 3):
                indexed.append(pal[mv[i:i + 3]])
        data = _lzw(indexed, mcs)
        out += bytes([mcs])
        for i in range(0, len(data), 255):
            chunk = data[i:i + 255]
            out += bytes([len(chunk)]) + chunk
        out += b"\x00"
    out += b"\x3B"
    with open(out_path, "wb") as fh:
        fh.write(bytes(out))
    return out_path


# ------------------------------------------------------------------ building --
def build(out_dir, aspects=None, frames=8, verbose=False):
    """Render every plate for every aspect into `out_dir`.

    Returns `{(kind, aspect): path}`. Static plates come out as `.png`, the two
    animated ones as `.gif` whose first frame is the rest pose.
    """
    os.makedirs(out_dir, exist_ok=True)
    made = {}
    for aspect in (aspects or ASPECTS):
        size = ASPECTS[aspect]
        for kind in PLATES:
            stem = os.path.join(out_dir, "plate-%s-%s" % (kind, aspect))
            if kind in ANIMATED:
                shots = []
                for i in range(frames):
                    png = "%s-f%02d.png" % (stem, i)
                    render_png(plate(kind, aspect, i / float(frames)), png, size)
                    shots.append(read_png(png))
                    os.remove(png)
                made[(kind, aspect)] = write_gif(shots, stem + ".gif")
            else:
                made[(kind, aspect)] = render_png(plate(kind, aspect), stem + ".png", size)
            if verbose:
                path = made[(kind, aspect)]
                print("  %-28s %8.1f KB" % (os.path.basename(path),
                                            os.path.getsize(path) / 1024.0))
    return made


def offbrand_colours(svg):
    """Every hex in an SVG that the design system does not define.

    The guard that keeps this module honest: it draws with tokens, so a literal
    that is not a token is a mistake, and the tests assert this is empty for
    every plate.
    """
    known = {("#" + v).upper() for v in TOKENS.values()} | {"#FFFFFF", "#000000"}
    return sorted({m.upper() for m in re.findall(r"#[0-9A-Fa-f]{6}", svg)} - known)


# --------------------------------------------------------------- GIF decoder --
def _unlzw(data, min_code_size, expected):
    """Inverse of `_lzw`. Returns the index stream."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    width = min_code_size + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    out, prev = bytearray(), None
    acc = nbits = 0
    for byte in data:
        acc |= byte << nbits
        nbits += 8
        while nbits >= width:
            code = acc & ((1 << width) - 1)
            acc >>= width
            nbits -= width
            if code == clear:
                table = [bytes([i]) for i in range(clear)] + [b"", b""]
                width, prev = min_code_size + 1, None
                continue
            if code == end:
                return bytes(out[:expected])
            if code < len(table):
                entry = table[code]
            elif prev is not None:
                entry = prev + prev[:1]     # the KwKwK case
            else:
                raise ValueError("corrupt GIF stream")
            out += entry
            if prev is not None:
                table.append(prev + entry[:1])
                if len(table) == (1 << width) and width < 12:
                    width += 1
            prev = entry
            if len(out) >= expected:
                return bytes(out[:expected])
    return bytes(out[:expected])


def read_gif(path):
    """(width, height, rows) for a GIF's FIRST frame, rows as RGB bytearrays.

    Only the first frame, because that is the one every static consumer shows —
    the Slides thumbnail, a PDF export, a banner crop — and therefore the one
    worth measuring. Interlaced frames are refused rather than de-interlaced
    wrongly; nothing this repo writes is interlaced.
    """
    with open(path, "rb") as fh:
        d = fh.read()
    if d[:6] not in (b"GIF89a", b"GIF87a"):
        raise ValueError("%s is not a GIF" % path)
    screen = (int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little"))
    packed = d[10]
    pos = 13
    table = None
    if packed & 0x80:
        n = 1 << ((packed & 7) + 1)
        table = d[pos:pos + n * 3]
        pos += n * 3
    while pos < len(d):
        block = d[pos]
        if block == 0x21:                    # extension — skip its sub-blocks
            pos += 2
            while d[pos]:
                pos += d[pos] + 1
            pos += 1
        elif block == 0x2C:                  # image descriptor
            fw = int.from_bytes(d[pos + 5:pos + 7], "little")
            fh_ = int.from_bytes(d[pos + 7:pos + 9], "little")
            fpacked = d[pos + 9]
            pos += 10
            if fpacked & 0x80:
                n = 1 << ((fpacked & 7) + 1)
                table = d[pos:pos + n * 3]
                pos += n * 3
            if fpacked & 0x40:
                raise ValueError("interlaced GIF not supported: %s" % path)
            if (fw, fh_) != screen:
                # A frame smaller than the logical screen sits at an offset over
                # whatever the previous frame left behind. Callers here sample
                # by fraction of the image, so a partial frame would be measured
                # in the wrong place — refuse rather than answer wrongly.
                raise ValueError("%s frame %dx%d does not fill its %dx%d screen"
                                 % (path, fw, fh_, screen[0], screen[1]))
            mcs = d[pos]
            pos += 1
            chunks = bytearray()
            while d[pos]:
                chunks += d[pos + 1:pos + 1 + d[pos]]
                pos += d[pos] + 1
            idx = _unlzw(bytes(chunks), mcs, fw * fh_)
            if table is None:
                raise ValueError("GIF has no colour table: %s" % path)
            rows = []
            for y in range(fh_):
                row = bytearray()
                for x in range(fw):
                    i = idx[y * fw + x] * 3
                    row += table[i:i + 3]
                rows.append(row)
            return fw, fh_, rows
        elif block == 0x3B:
            break
        else:
            raise ValueError("unexpected GIF block 0x%02X in %s" % (block, path))
    raise ValueError("%s has no image frame" % path)


def read_image(path):
    """(width, height, rows) for a PNG or GIF — whichever a deck embedded."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".png":
        return read_png(path)
    if ext == ".gif":
        return read_gif(path)
    raise ValueError("unsupported image type %s" % ext)
