"""Tests for the text-legibility check.

The bug this exists to catch is invisible by construction: black text on a
black plate looks like an empty slide, not like a mistake. So the checker's own
failure mode is the dangerous one — a resolver that quietly finds no colour
reports zero problems and looks like good news. That happened once already
during development: an `<a:rPr>` regex that terminated on the first
self-closing CHILD truncated every run's fill, and the check came back clean on
a deck with 23 real failures.

Every test below is therefore built so that a broken resolver fails it, rather
than passing with an empty result.
"""
import os
import zipfile

import pytest

from aaif_events import contrast as ct

BLACK, WHITE = (0, 0, 0), (255, 255, 255)
INK, VOID2 = (0x0A, 0x0A, 0x0A), (0x0A, 0x0A, 0x0A)
INK3 = (0x4A, 0x4A, 0x4A)


# ------------------------------------------------------------------ maths ----

def test_the_extremes_are_the_wcag_extremes():
    assert round(ct.contrast(BLACK, WHITE), 2) == 21.00
    assert ct.contrast(WHITE, WHITE) == 1.0


def test_contrast_is_symmetric():
    assert ct.contrast(INK3, WHITE) == ct.contrast(WHITE, INK3)


def test_the_black_on_black_case_scores_one():
    """The actual bug: two correct AAIF tokens, one unreadable pairing."""
    assert ct.contrast(INK, VOID2) == 1.0


@pytest.mark.parametrize("size,bold,want", [
    (12.0, False, ct.AA_NORMAL),
    (18.0, False, ct.AA_LARGE),
    (14.0, True, ct.AA_LARGE),     # bold buys the large-text threshold
    (14.0, False, ct.AA_NORMAL),
    (None, False, ct.AA_LARGE),    # OOXML's default body size is 18pt
])
def test_the_threshold_follows_wcags_large_text_carve_out(size, bold, want):
    assert ct._threshold(size, bold) == want


# ------------------------------------------------------------- a real deck ---
_THEME = ('<a:theme xmlns:a="a"><a:themeElements><a:clrScheme name="AAIF">'
          '<a:dk1><a:srgbClr val="0A0A0A"/></a:dk1>'
          '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
          '<a:accent1><a:srgbClr val="6C5CE7"/></a:accent1>'
          '</a:clrScheme></a:themeElements></a:theme>')

_MASTER = ('<p:sldMaster xmlns:p="p" xmlns:a="a"><p:clrMap bg1="lt1" tx1="dk1" '
           'bg2="dk2" tx2="lt2" accent1="accent1" accent2="accent2" '
           'accent3="accent3" accent4="accent4" accent5="accent5" '
           'accent6="accent6" hlink="hlink" folHlink="folHlink"/></p:sldMaster>')


def _run(text, colour, size=1200, bold=0, scheme=False):
    fill = ('<a:solidFill><a:schemeClr val="%s"/></a:solidFill>' % colour
            if scheme else
            '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % colour)
    return ('<a:r><a:rPr b="%d" sz="%d" lang="en-US">%s'
            '<a:latin typeface="Instrument Sans"/></a:rPr>'
            '<a:t>%s</a:t></a:r>' % (bold, size, fill, text))


def _slide(bg, runs, off=(0, 0), ext=(9144000, 5143500)):
    return ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
            "<p:cSld>%s<p:spTree><p:sp><p:spPr>"
            '<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            "<a:noFill/></p:spPr><p:txBody><a:p>%s</a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>"
            % (bg, off[0], off[1], ext[0], ext[1], "".join(runs)))


def _deck(tmp_path, slide_xml, media=None, name="d.pptx"):
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", _THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr("ppt/slides/slide1.xml", slide_xml)
        for n, blob in (media or {}).items():
            z.writestr(n, blob)
        if media:
            z.writestr("ppt/slides/_rels/slide1.xml.rels",
                       '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                       'openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId9" Type="img" Target="../media/%s"/>'
                       "</Relationships>" % os.path.basename(list(media)[0]))
    return path


SOLID_WHITE = '<p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></p:bgPr></p:bg>'
SOLID_BLACK = '<p:bg><p:bgPr><a:solidFill><a:srgbClr val="0A0A0A"/></a:solidFill></p:bgPr></p:bg>'


def test_ink_on_a_black_plate_is_reported_as_invisible(tmp_path):
    deck = _deck(tmp_path, _slide(SOLID_BLACK, [_run("EYEBROW", "0A0A0A")]))
    found = ct.check_pptx(deck)
    assert len(found) == 1
    f = found[0]
    assert f.ratio == 1.0 and f.invisible
    assert f.text == "EYEBROW"


def test_the_same_text_on_white_passes(tmp_path):
    """The control. Without it, a resolver that finds NO text also passes the
    test above by reporting nothing."""
    deck = _deck(tmp_path, _slide(SOLID_WHITE, [_run("EYEBROW", "0A0A0A")]))
    assert ct.check_pptx(deck) == []
    assert len(ct.check_pptx(deck, include_passes=True)) == 1


def test_every_run_is_actually_resolved(tmp_path):
    """The regression guard for the truncated-rPr bug: a broken resolver reports
    these as 'unchecked' rather than scoring them, and the check silently stops
    finding anything."""
    deck = _deck(tmp_path, _slide(SOLID_WHITE, [
        _run("one", "0A0A0A"), _run("two", "4A4A4A"), _run("three", "8C8C8C")]))
    found = ct.check_pptx(deck, include_passes=True)
    assert len(found) == 3
    assert all(f.ratio is not None for f in found), [f.note for f in found]


def test_a_scheme_colour_is_resolved_through_the_masters_colour_map(tmp_path):
    """`tx1` is not a colour; the master's clrMap sends it to dk1."""
    deck = _deck(tmp_path, _slide(SOLID_BLACK, [_run("X", "tx1", scheme=True)]))
    found = ct.check_pptx(deck)
    assert len(found) == 1 and found[0].fg == (0x0A, 0x0A, 0x0A)


def test_a_shape_fill_beats_the_slide_background(tmp_path):
    """White text on a white chip, on a black slide, is invisible — and a
    checker that only looked at <p:bg> would call it 21:1."""
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a">'
             "<p:cSld>%s<p:spTree><p:sp><p:spPr>"
             '<a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm>'
             '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
             "</p:spPr><p:txBody><a:p>%s</a:p></p:txBody></p:sp>"
             "</p:spTree></p:cSld></p:sld>" % (SOLID_BLACK, _run("X", "FFFFFF")))
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1 and found[0].ratio == 1.0


def test_a_run_with_no_colour_is_reported_unchecked_never_passed(tmp_path):
    """Inheritance is not resolved, and pretending otherwise would be worse than
    saying so: a wrong 'pass' is what let the original bug ship."""
    slide = _slide(SOLID_BLACK, ['<a:r><a:rPr sz="1200"/><a:t>X</a:t></a:r>'])
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1
    assert found[0].ratio is None and "inherits" in found[0].note


def test_a_translucent_run_is_not_scored_as_opaque(tmp_path):
    run = ('<a:r><a:rPr sz="1200"><a:solidFill><a:srgbClr val="FFFFFF">'
           '<a:alpha val="30000"/></a:srgbClr></a:solidFill></a:rPr>'
           "<a:t>X</a:t></a:r>")
    found = ct.check_pptx(_deck(tmp_path, _slide(SOLID_BLACK, [run])))
    assert len(found) == 1 and found[0].ratio is None
    assert "opaque" in found[0].note


def test_lum_mod_changes_the_score(tmp_path):
    """A transform that moves luminance moves the ratio; ignoring it would score
    the untransformed colour."""
    plain = ct.check_pptx(_deck(tmp_path, _slide(SOLID_WHITE, [_run("X", "6C5CE7")]),
                                name="a.pptx"), include_passes=True)[0]
    run = ('<a:r><a:rPr sz="1200"><a:solidFill><a:srgbClr val="6C5CE7">'
           '<a:lumMod val="50000"/></a:srgbClr></a:solidFill></a:rPr>'
           "<a:t>X</a:t></a:r>")
    dimmed = ct.check_pptx(_deck(tmp_path, _slide(SOLID_WHITE, [run]),
                                 name="b.pptx"), include_passes=True)[0]
    assert dimmed.fg != plain.fg
    assert dimmed.ratio > plain.ratio      # darker ink on white reads better


# --------------------------------------------------- image backgrounds -------
def _png(w, h, rgb):
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


IMAGE_BG = ('<p:bg><p:bgPr><a:blipFill><a:blip r:embed="rId9"/>'
            "<a:stretch><a:fillRect/></a:stretch></a:blipFill></p:bgPr></p:bg>")


def test_an_image_background_is_decoded_and_sampled(tmp_path):
    """The plates are images, and the runs that matter sit on them. Giving up at
    a blipFill would leave exactly the interesting text unchecked."""
    deck = _deck(tmp_path, _slide(IMAGE_BG, [_run("X", "0A0A0A")]),
                 media={"ppt/media/image1.png": _png(8, 8, (10, 10, 10))})
    found = ct.check_pptx(deck)
    assert len(found) == 1
    assert found[0].ratio is not None and found[0].ratio < 1.2
    assert "image" in found[0].ground


def test_the_image_is_sampled_under_the_run_not_globally(tmp_path):
    """Half-black, half-white plate: the same ink text passes on one side and
    fails on the other. A whole-image average would score both as mid-grey and
    be wrong about both."""
    import struct
    import zlib
    w = h = 8
    raw = b""
    for _y in range(h):
        row = b"".join(bytes((0, 0, 0)) if x < w // 2 else bytes((255, 255, 255))
                       for x in range(w))
        raw += b"\x00" + row

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    left = _deck(tmp_path, _slide(IMAGE_BG, [_run("X", "0A0A0A")],
                                  off=(0, 0), ext=(4572000, 5143500)),
                 media={"ppt/media/image1.png": png}, name="l.pptx")
    right = _deck(tmp_path, _slide(IMAGE_BG, [_run("X", "0A0A0A")],
                                   off=(4572000, 0), ext=(4572000, 5143500)),
                  media={"ppt/media/image1.png": png}, name="r.pptx")
    assert ct.check_pptx(left)                    # ink on the black half: fails
    assert ct.check_pptx(right) == []             # ink on the white half: passes


def test_summarise_counts_failures_invisibles_and_unchecked(tmp_path):
    slide = _slide(SOLID_BLACK, [
        _run("invisible", "0A0A0A"),
        _run("low", "4A4A4A"),
        '<a:r><a:rPr sz="1200"/><a:t>unchecked</a:t></a:r>'])
    fails, invisible, unchecked = ct.summarise(ct.check_pptx(_deck(tmp_path, slide)))
    assert (fails, invisible, unchecked) == (2, 1, 1)


# ---------------------------------------------------- shape fill resolution ---
# PowerPoint writes <a:ln><a:noFill/></a:ln> on almost every filled shape. A
# checker that reads that as "the shape has no fill" scores the text against the
# slide instead — and a repair driven by it then makes readable text invisible.

_LN_NOFILL = "<a:ln><a:noFill/></a:ln>"


def _shape(fill, runs, ln=_LN_NOFILL):
    return ('<p:sp><p:spPr><a:xfrm><a:off x="0" y="0"/>'
            '<a:ext cx="100" cy="100"/></a:xfrm>%s%s</p:spPr>'
            "<p:txBody><a:p>%s</a:p></p:txBody></p:sp>"
            % (fill, ln, "".join(runs)))


def _sp_slide(bg, shapes):
    return ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
            "<p:cSld>%s<p:spTree>%s</p:spTree></p:cSld></p:sld>"
            % (bg, "".join(shapes)))


def test_a_line_with_no_fill_does_not_hide_the_shapes_fill(tmp_path):
    """The HIGH-severity case: a white card on a black slide. Read wrongly, its
    black text scores 1.00:1 and a repair would whiten it into invisibility."""
    slide = _sp_slide(SOLID_BLACK, [
        _shape('<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>',
               [_run("CARD", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide), include_passes=True)
    assert len(found) == 1
    assert found[0].bg == (255, 255, 255)
    assert found[0].ratio > 15          # black on white, plainly readable
    assert ct.check_pptx(_deck(tmp_path, slide, name="x.pptx")) == []


def test_an_outline_colour_is_never_used_as_the_background(tmp_path):
    """An unfilled shape with a coloured border: the border is not the ground."""
    slide = _sp_slide(SOLID_BLACK, [
        _shape("<a:noFill/>", [_run("X", "FFFFFF")],
               ln='<a:ln><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:ln>')])
    found = ct.check_pptx(_deck(tmp_path, slide), include_passes=True)
    assert found[0].bg == (0x0A, 0x0A, 0x0A)     # the slide, not the outline
    assert found[0].ratio > 15


def test_an_explicit_nofill_falls_through_to_the_slide(tmp_path):
    slide = _sp_slide(SOLID_BLACK, [_shape("<a:noFill/>", [_run("X", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1 and found[0].ratio == 1.0


def test_a_gradient_shape_fill_is_not_scored_as_a_colour(tmp_path):
    """A gradient fill is not a single colour; falling through to the slide is
    wrong too, but inventing one would be worse. It must not be read as the
    first stop's colour."""
    slide = _sp_slide(SOLID_BLACK, [
        _shape('<a:gradFill><a:gsLst><a:gs pos="0">'
               '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:gs>'
               "</a:gsLst></a:gradFill>", [_run("X", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide), include_passes=True)
    assert found[0].bg != (255, 255, 255)


def test_a_grouped_shape_over_an_image_is_unchecked_not_guessed(tmp_path):
    """Offsets inside a <p:grpSp> are in the group's child coordinate space. Read
    as slide coordinates they sample the plate somewhere else entirely, which
    produces a confident wrong ratio rather than an honest gap."""
    inner = ('<p:sp><p:spPr><a:xfrm><a:off x="10" y="10"/>'
             '<a:ext cx="100" cy="100"/></a:xfrm><a:noFill/></p:spPr>'
             "<p:txBody><a:p>%s</a:p></p:txBody></p:sp>" % _run("X", "0A0A0A"))
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
             "<p:cSld>%s<p:spTree><p:grpSp><p:grpSpPr><a:xfrm>"
             '<a:off x="0" y="0"/><a:ext cx="9144000" cy="5143500"/>'
             '<a:chOff x="0" y="0"/><a:chExt cx="1000" cy="1000"/>'
             "</a:xfrm></p:grpSpPr>%s</p:grpSp></p:spTree></p:cSld></p:sld>"
             % (IMAGE_BG, inner))
    deck = _deck(tmp_path, slide, media={"ppt/media/image1.png": _png(8, 8, (10, 10, 10))})
    found = ct.check_pptx(deck)
    assert len(found) == 1
    assert found[0].ratio is None and "grpSp" in found[0].note


def test_a_grouped_shape_on_a_SOLID_ground_is_still_scored(tmp_path):
    """Only sampling needs the geometry. On a flat colour the group's transform
    is irrelevant, so refusing there would throw away a real answer."""
    inner = ('<p:sp><p:spPr><a:xfrm><a:off x="10" y="10"/>'
             '<a:ext cx="100" cy="100"/></a:xfrm><a:noFill/></p:spPr>'
             "<p:txBody><a:p>%s</a:p></p:txBody></p:sp>" % _run("X", "0A0A0A"))
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a">'
             "<p:cSld>%s<p:spTree><p:grpSp>%s</p:grpSp></p:spTree></p:cSld></p:sld>"
             % (SOLID_BLACK, inner))
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1 and found[0].ratio == 1.0


def test_an_outlined_run_is_scored_by_its_fill_not_its_outline(tmp_path):
    """DrawingML writes <a:rPr><a:ln><a:solidFill>…</a:ln><a:solidFill>… for
    outlined text. Taking the first solidFill reads the OUTLINE as the text
    colour — and improve_contrast keeps or discards a whole slide on that."""
    run = ('<a:r><a:rPr sz="1200">'
           '<a:ln><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:ln>'
           '<a:solidFill><a:srgbClr val="0A0A0A"/></a:solidFill>'
           "</a:rPr><a:t>X</a:t></a:r>")
    found = ct.check_pptx(_deck(tmp_path, _slide(SOLID_BLACK, [run])),
                          include_passes=True)
    assert len(found) == 1
    assert found[0].fg == (0x0A, 0x0A, 0x0A)      # the fill, not the outline
    assert found[0].ratio == 1.0


def test_a_multi_master_deck_will_not_resolve_scheme_colours(tmp_path):
    """Each slide resolves scheme colours through its OWN master and theme.
    Answering from the first of each would be confidently wrong, which is the
    one outcome this module refuses."""
    path = str(tmp_path / "multi.pptx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", _THEME)
        z.writestr("ppt/theme/theme2.xml", _THEME.replace("0A0A0A", "FF0000"))
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr("ppt/slideMasters/slideMaster2.xml", _MASTER)
        z.writestr("ppt/slides/slide1.xml",
                   _slide(SOLID_BLACK, [_run("X", "tx1", scheme=True)]))
    found = ct.check_pptx(path)
    assert len(found) == 1
    assert found[0].ratio is None
    assert "themes" in found[0].note


def test_a_single_master_deck_still_resolves_scheme_colours(tmp_path):
    """The control: the guard must not switch itself on for a normal deck."""
    deck = _deck(tmp_path, _slide(SOLID_BLACK, [_run("X", "tx1", scheme=True)]))
    found = ct.check_pptx(deck)
    assert len(found) == 1 and found[0].fg == (0x0A, 0x0A, 0x0A)


def test_a_gradient_filled_shape_is_unchecked_not_scored_against_the_slide(tmp_path):
    """An unscorable fill is NOT "no fill". Falling through to the slide scores
    the run against something that is not behind it — a confident wrong ratio,
    which improve_contrast then acts on. A white card on a black plate would
    read as 1.00:1 and its readable black text would be whitened."""
    slide = _sp_slide(SOLID_BLACK, [
        _shape('<a:gradFill><a:gsLst><a:gs pos="0">'
               '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:gs>'
               "</a:gsLst></a:gradFill>", [_run("CARD", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1
    assert found[0].ratio is None, "a gradient fill must not produce a ratio"
    assert "gradFill" in found[0].note


def test_a_translucent_shape_fill_is_unchecked(tmp_path):
    slide = _sp_slide(SOLID_BLACK, [
        _shape('<a:solidFill><a:srgbClr val="FFFFFF"><a:alpha val="40000"/>'
               "</a:srgbClr></a:solidFill>", [_run("X", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1 and found[0].ratio is None
    assert "opaque" in found[0].note


def test_a_shape_with_no_fill_still_falls_through_to_the_slide(tmp_path):
    """The control: "unscorable" must not swallow the ordinary case."""
    slide = _sp_slide(SOLID_BLACK, [_shape("<a:noFill/>", [_run("X", "0A0A0A")])])
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1 and found[0].ratio == 1.0


@pytest.mark.parametrize("off,ext", [
    ('<a:off x="0" y="0"/>', '<a:ext cx="4572000" cy="5143500"/>'),
    ('<a:off y="0" x="0"/>', '<a:ext cy="5143500" cx="4572000"/>'),   # reordered
    ('<a:off x="0" y="0" />', '<a:ext cx="4572000" cy="5143500" />'),  # spaced
])
def test_geometry_is_read_by_attribute_name_not_position(tmp_path, off, ext):
    """Attribute order is not fixed in OOXML — this repo already reads
    <p:sldSz> by name because these decks write cy before cx. A positional
    pattern that stops matching does not fail: it falls back to a full-slide
    box, so every run is scored against the AVERAGE of the whole plate and the
    numbers still look plausible."""
    import struct
    import zlib
    w = h = 8
    raw = b""
    for _y in range(h):
        raw += b"\x00" + b"".join(
            bytes((0, 0, 0)) if x < w // 2 else bytes((255, 255, 255))
            for x in range(w))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
             "<p:cSld>%s<p:spTree><p:sp><p:spPr><a:xfrm>%s%s</a:xfrm>"
             "<a:noFill/></p:spPr><p:txBody><a:p>%s</a:p></p:txBody></p:sp>"
             "</p:spTree></p:cSld></p:sld>"
             % (IMAGE_BG, off, ext, _run("X", "0A0A0A")))
    deck = _deck(tmp_path, slide, media={"ppt/media/image1.png": png})
    found = ct.check_pptx(deck, include_passes=True)
    assert len(found) == 1
    # The run sits over the BLACK half. Sampled correctly it is ~1:1; sampled
    # over the whole plate it would average to mid-grey and read as passing.
    assert found[0].ratio is not None and found[0].ratio < 1.5, found[0]


def test_a_shape_with_no_geometry_over_an_image_is_unchecked(tmp_path):
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
             "<p:cSld>%s<p:spTree><p:sp><p:spPr><a:noFill/></p:spPr>"
             "<p:txBody><a:p>%s</a:p></p:txBody></p:sp>"
             "</p:spTree></p:cSld></p:sld>" % (IMAGE_BG, _run("X", "0A0A0A")))
    deck = _deck(tmp_path, slide,
                 media={"ppt/media/image1.png": _png(8, 8, (10, 10, 10))})
    found = ct.check_pptx(deck)
    assert len(found) == 1 and found[0].ratio is None
    assert "no <a:off>" in found[0].note


def test_table_text_is_counted_as_unchecked_not_ignored(tmp_path):
    """A <p:graphicFrame> holds tables and charts, which the <p:sp> walk never
    sees. Producing NO finding meant a deck whose only unreadable text was in a
    table reported "0 issues" and counted as clean."""
    frame = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc>'
             "<a:txBody><a:p>%s</a:p></a:txBody></a:tc></a:tr></a:tbl>"
             "</a:graphicData></a:graphic></p:graphicFrame>"
             % _run("CELL", "0A0A0A"))
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
             "<p:cSld>%s<p:spTree>%s</p:spTree></p:cSld></p:sld>"
             % (SOLID_BLACK, frame))
    found = ct.check_pptx(_deck(tmp_path, slide))
    assert len(found) == 1
    assert found[0].ratio is None and "graphicFrame" in found[0].note
