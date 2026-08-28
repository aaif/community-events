"""Find text in a .pptx that is not readable against what is behind it.

This exists because it happened. The black-plate title slide set its eyebrow,
its subtitle and the wordmark of its host lockup in the LIGHT ink ramp — black
on black — and nobody noticed, because on that slide the text was invisible
rather than wrong-looking. The moment a background plate went behind it the same
runs came back as muddy grey. A conformance check on the *tokens* cannot catch
that: every one of those colours was a correct AAIF token. Only the pairing was
wrong.

So this measures pairings. For each text run it resolves the colour actually
drawn and the colour actually behind it, and reports the WCAG contrast ratio.

**How the background is resolved**, in the order PowerPoint itself paints:

1. the shape's own `<a:solidFill>`, if it has one;
2. the slide's `<p:bg>`;
3. the layout's, then the master's;
4. failing all of that, white.

A `<p:bg>` can also be an image, and the interesting text sits on exactly those.
Rather than give up there, the image is decoded and sampled **under the run's
own shape** — the picture is stretched across the slide, so a shape's EMU
rectangle maps to a pixel rectangle. Text over the dark corner of a plate and
text over its bright disc get different answers, which is the point.

**What it will not guess.** A run with no explicit colour inherits from the
placeholder, the layout's list styles and the master's text styles, and
resolving that chain properly means implementing most of the inheritance model.
Those runs are reported as `unresolved` and counted, never silently passed. The
same goes for a background this cannot pin down. An honest "unchecked" is worth
more than a confident wrong ratio, because the whole point is to be able to
trust a clean report.
"""
import hashlib
import os
import re
import zipfile

from aaif_events.agent_art import read_image

#: WCAG 2.1 AA. Large text is >=18pt, or >=14pt bold — the same carve-out the
#: spec makes, because weight and size both buy legibility.
AA_NORMAL = 4.5
AA_LARGE = 3.0
LARGE_PT = 18.0
LARGE_BOLD_PT = 14.0

#: Below this, text is not "low contrast" but effectively invisible: the same
#: colour, or near enough that no reader will find it. Reported separately
#: because it is a different bug with a different urgency.
INVISIBLE = 1.5


def _srgb(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_srgb(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    """WCAG contrast ratio between two RGB triples, 1.0 to 21.0."""
    a, b = luminance(fg), luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def _hex(value):
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# ------------------------------------------------------------ colour model ---
def _apply_mods(rgb, block):
    """Apply the OOXML colour transforms that change what is actually drawn.

    Only the ones that move luminance, which are the ones that change a contrast
    ratio: lumMod/lumOff, shade/tint. `alpha` is deliberately NOT composited —
    doing that needs the backdrop, and a translucent run is reported with its
    alpha noted instead of silently scored as if it were opaque.
    """
    def pct(tag):
        m = re.search(r'<a:%s val="(\d+)"\s*/>' % tag, block)
        return int(m.group(1)) / 100000.0 if m else None

    r, g, b = rgb
    lum_mod, lum_off = pct("lumMod"), pct("lumOff")
    if lum_mod is not None:
        r, g, b = (v * lum_mod for v in (r, g, b))
    if lum_off is not None:
        r, g, b = (v + 255 * lum_off for v in (r, g, b))
    shade, tint = pct("shade"), pct("tint")
    if shade is not None:
        r, g, b = (v * shade for v in (r, g, b))
    if tint is not None:
        r, g, b = (v * tint + 255 * (1 - tint) for v in (r, g, b))
    return tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def _alpha(block):
    m = re.search(r'<a:alpha val="(\d+)"\s*/>', block)
    return int(m.group(1)) / 100000.0 if m else 1.0


class Theme(object):
    """The theme palette plus the master's colour map, which is what turns a
    `schemeClr val="tx1"` into an actual colour. The map is not decorative: it
    is legal for a master to send `tx1` to `lt1`, and a checker that assumed
    tx1==dk1 would then read every run backwards."""

    def __init__(self, scheme, clrmap):
        self.scheme = scheme
        self.clrmap = clrmap

    ambiguous = None

    def resolve(self, name):
        if self.ambiguous:
            return None
        name = self.clrmap.get(name, name)
        return self.scheme.get(name)


def _parse_theme(xml):
    m = re.search(r"<a:clrScheme\b.*?</a:clrScheme>", xml, re.S)
    if not m:
        return {}
    out = {}
    for slot in re.finditer(r"<a:(\w+)>\s*<a:(srgbClr|sysClr)\b([^>]*)/?>", m.group(0)):
        name, kind, attrs = slot.group(1), slot.group(2), slot.group(3)
        if kind == "srgbClr":
            v = re.search(r'val="([0-9A-Fa-f]{6})"', attrs)
            if v:
                out[name] = _hex(v.group(1))
        else:
            # <a:sysClr lastClr="..."> carries the resolved value; without it
            # the colour is whatever the OS says, which is not knowable here.
            v = re.search(r'lastClr="([0-9A-Fa-f]{6})"', attrs)
            if v:
                out[name] = _hex(v.group(1))
    return out


def _parse_clrmap(xml):
    m = re.search(r"<p:clrMap\b([^>]*)/>", xml)
    return dict(re.findall(r'(\w+)="(\w+)"', m.group(1))) if m else {}


def _solid_colour(block, theme):
    """The RGB of the first `<a:solidFill>` in `block`, or None.

    Returns (rgb, alpha, note). `note` is set when the colour could not be
    pinned down, so the caller can report it rather than assume.
    """
    m = re.search(r"<a:solidFill>(.*?)</a:solidFill>", block, re.S)
    if not m:
        return None
    inner = m.group(1)
    srgb = re.search(r'<a:srgbClr val="([0-9A-Fa-f]{6})"', inner)
    if srgb:
        return (_apply_mods(_hex(srgb.group(1)), inner), _alpha(inner), None)
    scheme = re.search(r'<a:schemeClr val="(\w+)"', inner)
    if scheme:
        rgb = theme.resolve(scheme.group(1))
        if rgb is None:
            return (None, 1.0, theme.ambiguous
                    or "scheme colour %r not in the theme" % scheme.group(1))
        return (_apply_mods(rgb, inner), _alpha(inner), None)
    return (None, 1.0, "fill is not a plain colour")


#: Fill elements that may appear as a direct child of <p:spPr>, in the order
#: OOXML allows exactly one of them.
_FILL_KINDS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")


def shape_fill(sppr_xml, theme):
    """The shape's OWN fill colour, or None if it has none this can score.

    The outline subtree has to come out first. PowerPoint writes
    `<a:ln><a:noFill/></a:ln>` on almost every filled shape — that is the LINE
    having no fill, not the shape — so a naive "is there a noFill anywhere in
    spPr" test reads a white card as unfilled and scores its text against the
    slide behind it. On a black slide that turns readable black-on-white into a
    1.00:1 failure, and a repair driven by it then whitens the text and makes it
    genuinely invisible. The mirror image is just as wrong: with the outline
    left in, `_solid_colour` picks the first solidFill it finds, which for an
    unfilled shape with a coloured border is the BORDER's colour.
    """
    body = re.sub(r"<a:ln\b[^>]*>.*?</a:ln>", "", sppr_xml, flags=re.S)
    body = re.sub(r"<a:ln\b[^>]*/>", "", body)
    m = re.search(r"<a:(%s)\b" % "|".join(_FILL_KINDS), body)
    if not m or m.group(1) != "solidFill":
        # noFill, or a gradient/picture/pattern fill this does not evaluate.
        return None
    return _solid_colour(body, theme)


# ------------------------------------------------------------- backgrounds ---
class _Ground(object):
    """Whatever is behind the text: a flat colour, or a picture to sample."""

    def __init__(self, rgb=None, image=None, note=None, source=""):
        self.rgb, self.image, self.note, self.source = rgb, image, note, source

    def at(self, box, slide_wh):
        """(rgb, note) behind the EMU rectangle `box`, sampling if we have a
        picture. The mean is used rather than a single pixel: a run sits across
        a region, and one sampled pixel would swing wildly on a gradient."""
        if self.image is None:
            return self.rgb, self.note
        iw, ih, rows = self.image
        sw, sh = slide_wh
        x0, y0, cx, cy = box
        px0 = max(0, min(iw - 1, int(iw * x0 / sw)))
        py0 = max(0, min(ih - 1, int(ih * y0 / sh)))
        px1 = max(px0 + 1, min(iw, int(iw * (x0 + cx) / sw)))
        py1 = max(py0 + 1, min(ih, int(ih * (y0 + cy) / sh)))
        # Sample on a bounded grid — a full-bleed 1920x1080 region is two
        # million pixels and this runs over hundreds of slides.
        xs = range(px0, px1, max(1, (px1 - px0) // 24))
        ys = range(py0, py1, max(1, (py1 - py0) // 24))
        tot = [0, 0, 0]
        n = 0
        for y in ys:
            row = rows[y]
            for x in xs:
                i = x * 3
                tot[0] += row[i]
                tot[1] += row[i + 1]
                tot[2] += row[i + 2]
                n += 1
        if not n:
            return None, "could not sample the background image"
        return tuple(v // n for v in tot), None


def _background_of(xml, rels, media, theme, source):
    """The `<p:bg>` of one slide/layout/master part, as a _Ground or None."""
    m = re.search(r"<p:bg>.*?</p:bg>", xml, re.S)
    if not m:
        return None
    block = m.group(0)
    blip = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', block)
    if blip:
        target = rels.get(blip.group(1))
        img = media.get(target) if target else None
        if img is None:
            return _Ground(note="background image %s could not be read" % target,
                           source=source)
        return _Ground(image=img, source=source + " image")
    got = _solid_colour(block, theme)
    if got is None:
        return _Ground(note="background is neither a colour nor an image",
                       source=source)
    rgb, _alpha_, note = got
    return _Ground(rgb=rgb, note=note, source=source)


def _rels_of(z, part):
    name = "%s/_rels/%s.rels" % (os.path.dirname(part), os.path.basename(part))
    if name not in z.namelist():
        return {}
    xml = z.read(name).decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r"<Relationship\b[^>]*/>", xml):
        rid = re.search(r'Id="([^"]+)"', m.group(0))
        tgt = re.search(r'Target="([^"]+)"', m.group(0))
        if rid and tgt:
            out[rid.group(1)] = os.path.normpath(
                os.path.join(os.path.dirname(part), tgt.group(1))).replace(os.sep, "/")
    return out


#: Decoded backgrounds, keyed by the image's own bytes.
#:
#: The estate shares its plates: the same 1920x1080 PNG is embedded in every one
#: of the hero decks. Decoding is pure-Python PNG unfiltering, so without this
#: the check spends all its time decoding the same twelve pictures hundreds of
#: times and an estate run does not finish.
_CACHE = {}

#: Longest side kept after decoding. Only regional MEANS are ever read off these
#: images, and a mean does not need full resolution — this bounds both the time
#: spent sampling and the memory held for a deck full of full-bleed plates.
SAMPLE_MAX = 160


def _shrink(image):
    w, h, rows = image
    step = max(1, (max(w, h) + SAMPLE_MAX - 1) // SAMPLE_MAX)
    if step == 1:
        return image
    out = []
    for y in range(0, h, step):
        row = rows[y]
        out.append(bytearray(b"".join(bytes(row[x * 3:x * 3 + 3])
                                      for x in range(0, w, step))))
    return (len(range(0, w, step)), len(out), out)


def _decode(blob, suffix):
    """A decoded, downsampled background image, or None if it cannot be read."""
    key = hashlib.sha256(blob).digest()
    if key in _CACHE:
        return _CACHE[key]
    import tempfile
    result = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(blob)
            tmp = fh.name
        try:
            result = _shrink(read_image(tmp))
        finally:
            os.remove(tmp)
    except Exception:
        result = None            # reported as unchecked wherever it is used
    _CACHE[key] = result
    return result


# ------------------------------------------------------------------ finding --
class Finding(object):
    __slots__ = ("part", "text", "fg", "bg", "ratio", "size_pt", "bold",
                 "threshold", "ground", "note")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def invisible(self):
        return self.ratio is not None and self.ratio < INVISIBLE

    def __repr__(self):
        if self.ratio is None:
            return "%s: %r — UNCHECKED (%s)" % (self.part, self.text, self.note)
        return ("%s: %r  %s on %s  %.2f:1 (needs %.1f, %s)"
                % (self.part, self.text, _fmt(self.fg), _fmt(self.bg),
                   self.ratio, self.threshold, self.ground))


def _fmt(rgb):
    return "#%02X%02X%02X" % rgb if rgb else "?"


def _threshold(size_pt, bold):
    if size_pt is None:
        size_pt = 18.0            # OOXML's own default body size
    if size_pt >= LARGE_PT or (bold and size_pt >= LARGE_BOLD_PT):
        return AA_LARGE
    return AA_NORMAL


_SP = re.compile(r"<p:sp>.*?</p:sp>", re.S)
_GRPSP = re.compile(r"<p:grpSp>.*?</p:grpSp>", re.S)
_RUN = re.compile(r"<a:r>(.*?)</a:r>", re.S)
#: An <a:rPr> is either self-closing or a container. Matching it as
#: `<a:rPr\b.*?(?:/>|</a:rPr>)` looks right and is not: the non-greedy `/>`
#: matches the first self-closing CHILD — `<a:srgbClr .../>` — so the captured
#: block holds the opening <a:solidFill> but not its close, every run reads as
#: "no explicit colour", and the whole check reports a clean 0 failures.
_RPR = re.compile(r"<a:rPr\b[^>]*/>|<a:rPr\b[^>]*>.*?</a:rPr>", re.S)


def check_pptx(path, include_passes=False):
    """Every text run in `path` whose contrast is below WCAG AA.

    Set `include_passes` to get every run instead, which is what the tests use
    to prove the resolver agrees with a hand-computed answer.
    """
    findings = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        def read(n):
            return z.read(n).decode("utf-8", "replace")

        presentation_xml = read("ppt/presentation.xml")
        sz = re.search(r"<p:sldSz[^>]*/?>", presentation_xml)
        dims = dict(re.findall(r'\b(cx|cy)="(\d+)"', sz.group(0))) if sz else {}
        slide_wh = (int(dims.get("cx", 9144000)), int(dims.get("cy", 5143500)))

        themes = [n for n in names if re.match(r"ppt/theme/theme\d+\.xml$", n)]
        masters = [n for n in names if re.match(r"ppt/slideMasters/slideMaster\d+\.xml$", n)]
        # A deck with several masters resolves each slide's scheme colours
        # through ITS OWN master and theme. Taking the first of each would
        # answer confidently against the wrong palette — the one thing this
        # module refuses to do — so a multi-master deck reports its scheme
        # colours as unchecked instead. Every deck in this estate has one.
        ambiguous = len(themes) > 1 or len(masters) > 1
        scheme = _parse_theme(read(themes[0])) if themes and not ambiguous else {}
        master_xml = read(masters[0]) if masters else ""
        theme = Theme(scheme, _parse_clrmap(master_xml) if not ambiguous else {})
        if ambiguous:
            theme.ambiguous = ("deck has %d themes and %d masters; a scheme "
                               "colour cannot be resolved without following "
                               "each slide's own chain"
                               % (len(themes), len(masters)))

        media = {}
        for n in names:
            if re.match(r"ppt/media/.*\.(png|gif)$", n, re.I):
                media[n] = _decode(z.read(n), os.path.splitext(n)[1])

        master_bg = (_background_of(master_xml, _rels_of(z, masters[0]), media,
                                    theme, "master")
                     if masters else None)

        for part in sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)):
            xml = read(part)
            rels = _rels_of(z, part)
            ground = _background_of(xml, rels, media, theme, "slide")
            if ground is None:
                layout = next((t for r, t in rels.items()
                               if "/slideLayouts/" in t), None)
                if layout and layout in names:
                    ground = _background_of(read(layout), _rels_of(z, layout),
                                            media, theme, "layout")
            if ground is None:
                ground = master_bg
            if ground is None:
                ground = _Ground(rgb=(255, 255, 255), source="default white")

            # Shapes inside a <p:grpSp> state their offsets in the GROUP's
            # child coordinate space (a:chOff/a:chExt), not the slide's. Mapping
            # those straight onto the slide samples the background somewhere
            # unrelated to where the text actually sits — a confidently wrong
            # ratio, which is worse than an honest gap. Transforming them
            # properly means implementing the group transform; until that
            # exists, a grouped run is reported unchecked.
            grouped = set()
            for g in _GRPSP.finditer(xml):
                for sp in _SP.finditer(g.group(0)):
                    grouped.add(sp.group(0))

            for sp in _SP.finditer(xml):
                block = sp.group(0)
                in_group = block in grouped
                sppr = re.search(r"<p:spPr>.*?</p:spPr>", block, re.S)
                shape_ground = ground
                if sppr:
                    got = shape_fill(sppr.group(0), theme)
                    if got and got[0] is not None and got[1] >= 0.999:
                        shape_ground = _Ground(rgb=got[0], source="shape fill")

                off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"/>', block)
                ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', block)
                box = ((int(off.group(1)), int(off.group(2)),
                        int(ext.group(1)), int(ext.group(2)))
                       if off and ext else (0, 0, slide_wh[0], slide_wh[1]))

                for run in _RUN.finditer(block):
                    body = run.group(1)
                    text = "".join(re.findall(r"<a:t>(.*?)</a:t>", body, re.S)).strip()
                    if not text:
                        continue
                    rpr = _RPR.search(body)
                    rpr_xml = rpr.group(0) if rpr else ""
                    sz_m = re.search(r'\bsz="(\d+)"', rpr_xml)
                    size_pt = int(sz_m.group(1)) / 100.0 if sz_m else None
                    bold = bool(re.search(r'\bb="1"', rpr_xml))

                    # Strip the outline first, for the same reason shape_fill
                    # does: DrawingML writes <a:rPr><a:ln><a:solidFill>…</a:ln>
                    # for outlined text, and taking the first solidFill would
                    # then score the run using its OUTLINE colour as its text
                    # colour. improve_contrast keeps or discards a whole slide
                    # on these ratios, so a wrong reading either whitens
                    # readable text or throws away a real repair.
                    got = _solid_colour(
                        re.sub(r"<a:ln\b[^>]*>.*?</a:ln>|<a:ln\b[^>]*/>", "",
                               rpr_xml, flags=re.S), theme)
                    if got is None:
                        findings.append(Finding(
                            part=part, text=text[:60], size_pt=size_pt, bold=bold,
                            ground=shape_ground.source,
                            note="run has no explicit colour; it inherits from the "
                                 "placeholder/layout/master chain, which this does "
                                 "not resolve"))
                        continue
                    fg, alpha, note = got
                    if fg is None:
                        findings.append(Finding(part=part, text=text[:60],
                                                size_pt=size_pt, bold=bold,
                                                ground=shape_ground.source, note=note))
                        continue
                    if alpha < 0.999:
                        findings.append(Finding(
                            part=part, text=text[:60], fg=fg, size_pt=size_pt,
                            bold=bold, ground=shape_ground.source,
                            note="run is %.0f%% opaque; its drawn colour depends on "
                                 "the backdrop" % (alpha * 100)))
                        continue

                    if in_group and shape_ground.image is not None:
                        findings.append(Finding(
                            part=part, text=text[:60], fg=fg, size_pt=size_pt,
                            bold=bold, ground=shape_ground.source,
                            note="run sits in a <p:grpSp>, whose offsets are in "
                                 "the group's child coordinate space; the "
                                 "background image cannot be sampled under it"))
                        continue
                    bg, bg_note = shape_ground.at(box, slide_wh)
                    if bg is None:
                        findings.append(Finding(
                            part=part, text=text[:60], fg=fg, size_pt=size_pt,
                            bold=bold, ground=shape_ground.source,
                            note=bg_note or "background could not be resolved"))
                        continue

                    ratio = contrast(fg, bg)
                    threshold = _threshold(size_pt, bold)
                    if include_passes or ratio < threshold:
                        findings.append(Finding(
                            part=part, text=text[:60], fg=fg, bg=bg, ratio=ratio,
                            size_pt=size_pt, bold=bold, threshold=threshold,
                            ground=shape_ground.source))
    return findings


def summarise(findings):
    """(failures, invisible, unchecked) counts."""
    fails = [f for f in findings if f.ratio is not None and f.ratio < f.threshold]
    return (len(fails), len([f for f in fails if f.invisible]),
            len([f for f in findings if f.ratio is None]))
