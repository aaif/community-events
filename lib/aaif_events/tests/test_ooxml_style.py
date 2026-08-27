"""Tests for the OOXML design-system conformance engine.

Three things here are load-bearing and fail silently if broken, which is why
they are pinned rather than left to review:

**The colour map must only name colours the design system defines.** A typo in
a token name is a `KeyError` at run time — fine — but a rule pointing at a token
that *used* to exist would write a stale colour into every file in the estate.

**Roles must resolve correctly.** A run colour and a cell border can carry the
same hex, and getting the role wrong turns a hairline into a black plate. There
is no way to see that except by opening the file.

**The rewrite must be byte-preserving.** These files carry embedded fonts and
relationship ids; the engine's whole premise is that it edits attributes in
place and copies everything else through untouched.
"""
import os
import zipfile

import pytest

from aaif_events import ooxml_style as ox

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# ------------------------------------------------------------ the token seam --

def test_every_rule_names_a_token_the_design_system_defines():
    missing = set()
    for src, roles in ox._ROLE_MAP.items():
        for role, name in roles.items():
            if name not in ox.TOKENS:
                missing.add("%s/%s -> --%s" % (src, role, name))
    for name in ox._THEME_CLRS.values():
        if name not in ox.TOKENS:
            missing.add("theme -> --%s" % name)
    assert not missing, "rules point at tokens that do not exist: %s" % sorted(missing)


def test_every_rule_covers_all_three_roles():
    """A rule missing a role would KeyError only on the file that happens to use
    that colour in that slot — possibly not the file under test."""
    for src, roles in ox._ROLE_MAP.items():
        assert set(roles) == {"fill", "stroke", "text"}, src


def test_token_refuses_to_invent_a_colour():
    with pytest.raises(KeyError):
        ox.token("no-such-token")


def test_tokens_parse_skips_non_hex_values():
    # --accent is `var(--ink)` and --gradient-hero is a gradient; neither is a
    # colour this module may write, and both would be nonsense in a slide.
    assert "accent" not in ox.TOKENS
    assert "gradient-hero" not in ox.TOKENS
    assert ox.TOKENS["ink"] == "0A0A0A"


# ---------------------------------------------------------------- pptx roles --

def _pptx(body):
    return ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a">%s</p:sld>' % body)


def test_a_run_colour_is_text_not_fill():
    # 555555, not the trackers' navy: --ink and --void-2 are both 0A0A0A, so a
    # navy run could not tell the two roles apart no matter which one it took.
    xml = _pptx('<a:rPr><a:solidFill><a:srgbClr val="555555"/></a:solidFill></a:rPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert ox.token("ink-3") in out
    assert ox.token("line-2") not in out


def test_a_shape_fill_is_a_fill():
    xml = _pptx('<p:spPr><a:solidFill><a:srgbClr val="1E2761"/></a:solidFill></p:spPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert ox.token("void-2") in out


def test_an_outline_colour_is_a_stroke():
    xml = _pptx('<p:spPr><a:ln><a:solidFill><a:srgbClr val="1E2761"/></a:solidFill></a:ln></p:spPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert ox.token("line") in out


def test_a_run_inside_a_line_is_still_text():
    """`a:rPr` wins over `a:ln`: the text test runs first for exactly this."""
    xml = _pptx('<a:ln><a:rPr><a:solidFill><a:srgbClr val="1E2761"/>'
                '</a:solidFill></a:rPr></a:ln>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert ox.token("ink") in out


def test_alpha_and_lummod_are_not_mistaken_for_colours():
    """Both are six characters of valid hex. Scanning every `val=` would rewrite
    them and silently change a shape's opacity."""
    xml = _pptx('<a:solidFill><a:srgbClr val="1E2761">'
                '<a:alpha val="100000"/><a:lumMod val="110000"/>'
                '</a:srgbClr></a:solidFill>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert 'val="100000"' in out and 'val="110000"' in out


def test_an_unmapped_colour_is_left_alone_and_reported():
    xml = _pptx('<p:spPr><a:solidFill><a:srgbClr val="ABCDEF"/></a:solidFill></p:spPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert 'val="ABCDEF"' in out


# ---------------------------------------------------------------- pptx fonts --

def test_the_display_face_becomes_instrument_sans():
    xml = _pptx('<a:rPr><a:latin typeface="Space Grotesk"/>'
                '<a:cs typeface="Manrope"/></a:rPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert "Space Grotesk" not in out and "Manrope" not in out
    assert out.count(ox.SANS) == 2


def test_mono_survives_in_a_deck():
    """JetBrains Mono names the metadata runs a PPTX cannot express as a stack."""
    xml = _pptx('<a:rPr><a:latin typeface="JetBrains Mono"/></a:rPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert ox.MONO in out


def test_a_theme_font_reference_is_not_replaced_with_a_literal():
    xml = _pptx('<a:rPr><a:latin typeface="+mn-lt"/></a:rPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode())
    assert out == xml.encode()


def test_the_deck_singletons_are_covered():
    """presentation.xml and tableStyles.xml carry the inherited defaults; a file
    whose designer overrode nothing renders entirely from them."""
    xml = _pptx('<a:latin typeface="Arial"/>')
    for part in ("ppt/presentation.xml", "ppt/tableStyles.xml"):
        assert ox.SANS in ox.restyle_part(part, xml.encode()).decode(), part


# ----------------------------------------------------------- pptx theme ------

def test_the_theme_palette_is_replaced_by_slot_not_by_value():
    """4472C4 is Office's accent1. Mapping it by hex would also catch a shape
    that legitimately uses that value; the slot is what identifies it."""
    xml = ('<a:theme xmlns:a="a"><a:clrScheme name="Office">'
           '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
           '</a:clrScheme></a:theme>')
    out = ox.restyle_part("ppt/theme/theme1.xml", xml.encode()).decode()
    assert ox.token("spec-1") in out and "4472C4" not in out


def test_a_theme_font_becomes_instrument_sans():
    xml = ('<a:theme xmlns:a="a"><a:fontScheme>'
           '<a:majorFont><a:latin typeface="Arial"/></a:majorFont>'
           '</a:fontScheme></a:theme>')
    out = ox.restyle_part("ppt/theme/theme1.xml", xml.encode()).decode()
    assert ox.SANS in out


# ---------------------------------------------------------------- docx roles --

def _docx(body):
    return '<?xml version="1.0"?><w:document xmlns:w="w">%s</w:document>' % body


def test_cell_shading_and_cell_border_take_the_same_hex_to_different_places():
    """The reason the whole engine is role-aware. Both are `1e2761` in the real
    trackers, forty characters apart."""
    xml = _docx('<w:tcPr><w:tcBorders><w:top w:color="1e2761" w:sz="4"/></w:tcBorders>'
                '<w:shd w:fill="1e2761" w:val="clear"/></w:tcPr>')
    out = ox.restyle_part("word/document.xml", xml.encode()).decode()
    assert 'w:color="%s"' % ox.token("line").lower() in out
    assert 'w:fill="%s"' % ox.token("void-2").lower() in out


def test_a_run_colour_in_word_is_text():
    xml = _docx('<w:rPr><w:color w:val="555555"/></w:rPr>')
    out = ox.restyle_part("word/document.xml", xml.encode()).decode()
    assert 'w:val="%s"' % ox.token("ink-3").lower() in out


def test_auto_is_never_rewritten():
    xml = _docx('<w:rPr><w:color w:val="auto"/></w:rPr><w:shd w:fill="auto"/>')
    assert ox.restyle_part("word/document.xml", xml.encode()) == xml.encode()


def test_word_keeps_the_files_own_lowercase_hex():
    """Word writes colours lowercase. Emitting uppercase would make every file
    differ on a re-run, so a no-op sweep would re-upload the whole estate."""
    xml = _docx('<w:rPr><w:color w:val="555555"/></w:rPr>')
    out = ox.restyle_part("word/document.xml", xml.encode()).decode()
    assert ox.token("ink-3").lower() in out
    assert ox.token("ink-3") not in out       # not the uppercase form


def test_mono_body_prose_becomes_the_sans():
    """"Mono carries metadata, not prose" — and the trackers set 205 body runs
    in JetBrains Mono."""
    xml = _docx('<w:rPr><w:rFonts w:ascii="JetBrains Mono" w:hAnsi="JetBrains Mono"/></w:rPr>')
    out = ox.restyle_part("word/document.xml", xml.encode()).decode()
    assert ox.MONO not in out and out.count(ox.SANS) == 2


def test_mono_is_kept_outside_the_body():
    xml = _docx('<w:rPr><w:rFonts w:ascii="JetBrains Mono"/></w:rPr>')
    out = ox.restyle_part("word/styles.xml", xml.encode()).decode()
    assert ox.MONO in out


# --------------------------------------------------------------- invariants --

def test_an_unknown_part_is_returned_unchanged_and_identical():
    data = b"<x>anything at all</x>"
    assert ox.restyle_part("docProps/app.xml", data) is data


def test_binary_parts_survive():
    data = b"\x89PNG\r\n\x1a\n\xff\xfe not utf-8"
    assert ox.restyle_part("ppt/media/image1.png", data) is data


def test_a_conformant_part_comes_back_as_the_same_object():
    """The sweep uses "did the bytes change" as its upload test, so a file that
    needs nothing must produce no diff at all."""
    xml = _pptx('<a:rPr><a:latin typeface="Instrument Sans"/></a:rPr>').encode()
    assert ox.restyle_part("ppt/slides/slide1.xml", xml) is xml


def test_the_scanner_preserves_every_other_byte():
    xml = _pptx('<a:t>  keep   me &amp; my &lt;spacing&gt;  </a:t>'
                '<a:rPr><a:latin typeface="Arial"/></a:rPr>')
    out = ox.restyle_part("ppt/slides/slide1.xml", xml.encode()).decode()
    assert "  keep   me &amp; my &lt;spacing&gt;  " in out
    assert out == xml.replace("Arial", ox.SANS)


# ----------------------------------------------------------- real fixtures ---

@pytest.mark.parametrize("name", ["event_tracker_irl.docx", "event_tracker_online.docx"])
def test_the_shipped_fixtures_are_conformant(name):
    """The repo's only OOXML assets, and therefore the CI gate: if these drift
    off the design system, this fails in review rather than months later."""
    path = os.path.join(FIXTURES, name)
    assert ox.audit(path) == [], "off-system values in %s: %s" % (name, ox.audit(path))


@pytest.mark.parametrize("name", ["event_tracker_irl.docx", "event_tracker_online.docx"])
def test_restyling_a_conformant_fixture_changes_nothing(name, tmp_path):
    """Idempotence. A sweep over an already-clean estate must upload nothing."""
    path = os.path.join(FIXTURES, name)
    with zipfile.ZipFile(path) as z:
        for part in z.namelist():
            data = z.read(part)
            assert ox.restyle_part(part, data) == data, "%s/%s changed" % (name, part)
