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
import collections
import os
import re
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


@pytest.mark.parametrize("part", ["word/document.xml", "word/styles.xml",
                                  "word/header1.xml", "word/footer2.xml"])
def test_mono_survives_the_sweep_in_every_word_part(part):
    """DESIGN.md reserves mono for "metadata and eyebrows", so a run already
    set in it is the rule being honoured, not drift — in the body like anywhere.

    An earlier pass demoted mono to the sans in `word/document.xml` alone, on
    the reading that the trackers' 205 mono runs were body prose. Every one of
    them is a field label, a table header, a date, a status or a phase eyebrow;
    the prose was already in the sans. `word/document.xml` is parametrized
    FIRST here because it is the part that regressed.
    """
    xml = _docx('<w:rPr><w:rFonts w:ascii="JetBrains Mono" w:hAnsi="JetBrains Mono"/></w:rPr>')
    data = xml.encode()
    out = ox.restyle_part(part, data)
    # Byte-identical, not merely mono-preserving: the sweep uses "did the bytes
    # change" as its upload test, so a tracker whose only non-sans face is mono
    # must not be re-uploaded at all.
    assert out is data
    assert ox.MONO in out.decode() and ox.SANS not in out.decode()


def test_the_faces_around_mono_still_move():
    """The parametrized test above passes trivially if the docx pass stopped
    rewriting fonts altogether. Pin that it did not."""
    xml = _docx('<w:rPr><w:rFonts w:ascii="Space Grotesk" w:hAnsi="Consolas"/></w:rPr>')
    out = ox.restyle_part("word/document.xml", xml.encode()).decode()
    assert 'w:ascii="%s"' % ox.SANS in out      # display drift -> the sans
    assert 'w:hAnsi="%s"' % ox.MONO in out      # a metadata face -> the mono


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
    off the design system, this fails in review rather than months later.

    Note what they CANNOT catch. Both were captured after a sweep that demoted
    every mono run to the sans, so neither holds a single JetBrains Mono run —
    and neither holds any `word/fonts/` part at all. They are ~19KB each
    because of both absences, while a real tracker lands at ~205KB (210,123
    bytes; the figures in this file are KiB unless a byte count is given). So:

    * **mono preservation** is pinned by
      `test_mono_survives_the_sweep_in_every_word_part` on synthetic XML, the
      declared-but-not-embedded rule by
      `test_a_face_in_use_keeps_its_declaration_but_loses_its_embed`, and the
      interaction of all three passes by
      `test_a_real_tracker_keeps_its_mono_through_the_whole_pipeline`;
    * **`ensure_fallback_font` is not covered here either** — this test calls
      only `audit()`. It is what makes a real tracker large: running it on a
      shipped fixture alone takes it 19,769 -> 210,090. Effectively ALL of the
      size gap is the fallback, not mono, which is embedded nowhere now.

    That ~205KB is softer than it looks: `ensure_fallback_font` writes its two
    TTFs STORED, so deflating them would drop ~106KB and stale every size
    figure in this repo at once."""
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


def _parts(path):
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            yield n, z.read(n)


def _replace_part(path, name, data):
    """Rewrite one part in place, preserving every other byte."""
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, blob in items:
            z.writestr(info, data if info.filename == name else blob)


def test_a_real_tracker_keeps_its_mono_through_the_whole_pipeline(tmp_path):
    """End to end over the actual pre-sweep tracker, not synthetic XML.

    The unit tests pin restyle and prune separately; this pins the INTERACTION,
    which is where removing `_MONO_IS_PROSE` actually has to hold up: restyle
    must leave the 205 metadata runs alone, prune must then see the face as
    in-use and keep its four embeds, and the fallback pass must not undo
    either. The shipped fixtures cannot serve here — they are post-flattening
    and hold no mono — so this reads the pre-sweep copy out of git.
    """
    import subprocess
    blob = subprocess.run(
        ["git", "show", "78a6599:lib/aaif_events/tests/fixtures/event_tracker_irl.docx"],
        cwd=os.path.dirname(FIXTURES), capture_output=True)
    if blob.returncode != 0 or not blob.stdout:
        # In CI this must FAIL, not skip. It skipped silently for exactly as
        # long as `validate.yml` checked out at depth 1, and it was the only
        # test covering a partial embed strip — a green run that had quietly
        # stopped testing the thing. `fetch-depth: 0` is the fix; this is the
        # tripwire for anyone who removes it.
        if os.environ.get("CI"):
            raise AssertionError(
                "pre-sweep tracker unreachable in CI — validate.yml needs "
                "fetch-depth: 0 on the pytest job")
        pytest.skip("pre-sweep tracker not reachable from this shallow checkout")
    path = str(tmp_path / "tracker.docx")
    with open(path, "wb") as fh:
        fh.write(blob.stdout)

    def _faces(p_):
        with zipfile.ZipFile(p_) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return collections.Counter(re.findall(r'w:ascii="([^"]+)"', xml))

    assert _faces(path)[ox.MONO] == 205, "the pre-sweep tracker changed shape"

    for name, data in list(_parts(path)):
        out = ox.restyle_part(name, data)
        if out is not data:
            _replace_part(path, name, out)
    pruned = ox.prune_embedded_fonts(path)
    ox.ensure_fallback_font(path)
    # Mono is reported as having shed bytes, alongside the faces that lost
    # their entries outright — the distinction is asserted on the table below.
    # Two channels, two meanings: Space Grotesk lost its declaration, mono
    # kept its and lost only its bytes.
    assert ox.MONO in pruned.unembedded and ox.MONO not in pruned.faces
    assert "Space Grotesk" in pruned.faces

    after = _faces(path)
    assert after[ox.MONO] == 205, "restyle flattened the metadata runs"
    assert ox.SANS in after, "the display drift was not folded into the sans"
    assert "Space Grotesk" not in after and "Manrope" not in after
    # In use, so the table entry must survive — the embeds must not.
    with zipfile.ZipFile(path) as z:
        names, table = z.namelist(), z.read("word/fontTable.xml").decode()
    # Declared, because 205 runs ask for it; not embedded, because Google Docs
    # resolves it — see NEVER_EMBED.
    assert ox.MONO in table, "the in-use face lost its declaration"
    assert not [n for n in names if "JetBrainsMono" in n], "mono was embedded"
    assert not [n for n in names if "SpaceGrotesk" in n]
    assert ox.audit(path) == [], "the swept tracker is still off-system"


# ------------------------------------------------------- plate slides --------

def _deck(tmp_path, bg='<p:bg><p:bgPr><a:solidFill><a:srgbClr val="000000"/></a:solidFill></p:bgPr></p:bg>'):
    """A minimal but structurally real .pptx with two slides."""
    path = str(tmp_path / "deck.pptx")
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
             "<p:cSld>%s<p:spTree/></p:cSld></p:sld>")
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%s" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="%s" Target="../notesSlides/notesSlide2.xml"/>'
            "</Relationships>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="ct"><Default ContentType="image/png" Extension="png"/>'
                   '<Override ContentType="%s" PartName="/ppt/slides/slide1.xml"/>'
                   '<Override ContentType="%s" PartName="/ppt/slides/slide2.xml"/></Types>'
                   % (ox._SLIDE_CT, ox._SLIDE_CT))
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p" xmlns:r="r"><p:sldIdLst>'
                   '<p:sldId id="256" r:id="rId5"/><p:sldId id="257" r:id="rId6"/>'
                   '</p:sldIdLst><p:sldSz cy="5143500" cx="9144000"/></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId5" Type="%s" Target="slides/slide1.xml"/>'
                   '<Relationship Id="rId6" Type="%s" Target="slides/slide2.xml"/>'
                   "</Relationships>" % (ox._SLIDE_REL, ox._SLIDE_REL))
        z.writestr("ppt/slides/slide1.xml", slide % "")
        z.writestr("ppt/slides/slide2.xml", slide % bg)
        for n in (1, 2):
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % n,
                       rels % (ox._SLIDE_REL, ox._NOTES_REL))
    return path


def _png(tmp_path, name="p.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    return str(p)


def test_a_solid_background_becomes_the_plate(tmp_path):
    deck = _deck(tmp_path)
    assert ox.add_plate_slides(deck, [("dusk", _png(tmp_path))]) == ["dusk"]
    with zipfile.ZipFile(deck) as z:
        new = z.read("ppt/slides/slide3.xml").decode()
        rels = z.read("ppt/slides/_rels/slide3.xml.rels").decode()
    assert "a:blipFill" in new and "solidFill" not in new
    assert "../media/image1.png" in rels


def test_the_notes_relationship_is_dropped(tmp_path):
    """A notes part cannot be shared between two slides, and a dangling
    relationship is a file PowerPoint reports as corrupt."""
    deck = _deck(tmp_path)
    ox.add_plate_slides(deck, [("dusk", _png(tmp_path))])
    with zipfile.ZipFile(deck) as z:
        assert ox._NOTES_REL not in z.read("ppt/slides/_rels/slide3.xml.rels").decode()


def test_the_deck_is_registered_everywhere_a_slide_must_be(tmp_path):
    """Four places have to agree or the deck will not open: the part, its
    content type, the presentation relationship, and the slide id list."""
    deck = _deck(tmp_path)
    ox.add_plate_slides(deck, [("dusk", _png(tmp_path))])
    with zipfile.ZipFile(deck) as z:
        assert "ppt/slides/slide3.xml" in z.namelist()
        assert "/ppt/slides/slide3.xml" in z.read("[Content_Types].xml").decode()
        assert "slides/slide3.xml" in z.read("ppt/_rels/presentation.xml.rels").decode()
        presentation = z.read("ppt/presentation.xml").decode()
        assert presentation.count("<p:sldId ") == 3


def test_a_gif_plate_declares_its_content_type(tmp_path):
    """The decks declare image/png already but never image/gif, and a part with
    no declared content type makes PowerPoint call the whole file corrupt."""
    deck = _deck(tmp_path)
    gif = tmp_path / "p.gif"
    gif.write_bytes(b"GIF89a" + b"\x00" * 20)
    ox.add_plate_slides(deck, [("rail", str(gif))])
    with zipfile.ZipFile(deck) as z:
        assert 'Extension="gif"' in z.read("[Content_Types].xml").decode()


def test_adding_plates_twice_adds_nothing_the_second_time(tmp_path):
    """Without the marker, a sweep would append six more slides to every deck in
    the estate on every run."""
    deck = _deck(tmp_path)
    plates = [("dusk", _png(tmp_path)), ("dawn", _png(tmp_path, "q.png"))]
    assert sorted(ox.add_plate_slides(deck, plates)) == ["dawn", "dusk"]
    assert ox.add_plate_slides(deck, plates) == []
    assert ox.plate_labels(deck) == {"dusk", "dawn"}
    with zipfile.ZipFile(deck) as z:
        assert len([n for n in z.namelist() if n.startswith("ppt/slides/slide")]) == 4


def test_a_deck_with_no_background_is_refused(tmp_path):
    deck = _deck(tmp_path, bg="")
    with pytest.raises(RuntimeError, match="no <p:bg>"):
        ox.add_plate_slides(deck, [("dusk", _png(tmp_path))])


def test_slide_size_is_read_by_attribute_name_not_position(tmp_path):
    """These decks write cy BEFORE cx. Reading them positionally swaps width and
    height, and the full-bleed test then silently never matches."""
    deck = _deck(tmp_path)
    ox.add_plate_slides(deck, [("dusk", _png(tmp_path))])   # would raise if swapped


def test_cloned_text_moves_to_the_on_dark_ramp():
    """The black-plate slide sets its eyebrow, subtitle and the wordmark of the
    host lockup in the LIGHT ink ramp — invisible on its own black, and muddy
    the moment a plate goes behind them."""
    xml = ('<p:sld xmlns:p="p" xmlns:a="a"><a:rPr><a:solidFill>'
           '<a:srgbClr val="%s"/></a:solidFill></a:rPr></p:sld>' % ox.token("ink-3"))
    out = ox.to_on_dark(xml)
    assert ox.token("ink-inv-2") in out


def test_on_dark_leaves_non_text_colours_alone():
    xml = ('<p:sld xmlns:p="p" xmlns:a="a"><p:spPr><a:solidFill>'
           '<a:srgbClr val="%s"/></a:solidFill></p:spPr></p:sld>' % ox.token("ink"))
    assert ox.to_on_dark(xml) == xml


# --------------------------------------------------- measured contrast repair --

def _ct_deck(tmp_path, slides, name="c.pptx"):
    """A deck whose slides are given as (background, [runs]) pairs."""
    from aaif_events.tests import test_contrast as tc
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", tc._THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", tc._MASTER)
        for i, (bg, runs) in enumerate(slides, 1):
            z.writestr("ppt/slides/slide%d.xml" % i, tc._slide(bg, runs))
    return path


def test_the_repair_rescues_invisible_text_on_a_dark_plate(tmp_path):
    from aaif_events.tests import test_contrast as tc
    deck = _ct_deck(tmp_path, [(tc.SOLID_BLACK, [tc._run("HOST", "0A0A0A")])])
    fixed, before, after = ox.improve_contrast(deck)
    assert (fixed, before, after) == (1, 1, 0)
    from aaif_events import contrast as ct
    assert ct.check_pptx(deck) == []


def test_the_repair_leaves_a_light_slide_alone(tmp_path):
    """Whitening text on a white ground would turn a passing slide into an
    invisible one — the exact bug, inverted."""
    from aaif_events.tests import test_contrast as tc
    deck = _ct_deck(tmp_path, [(tc.SOLID_WHITE, [tc._run("BODY", "0A0A0A")])])
    with zipfile.ZipFile(deck) as z:
        before = z.read("ppt/slides/slide1.xml")
    assert ox.improve_contrast(deck) == ox.Rescued()
    with zipfile.ZipFile(deck) as z:
        assert z.read("ppt/slides/slide1.xml") == before


def test_the_repair_never_pushes_a_passing_run_below_the_threshold(tmp_path):
    """A slide mixing a rescuable run with one that the remap would break is
    left untouched: the repair is all-or-nothing per slide."""
    from aaif_events.tests import test_contrast as tc
    # White text already passing on black, plus ink text that needs rescuing —
    # the remap helps the second and cannot hurt the first, so this IS taken.
    deck = _ct_deck(tmp_path, [(tc.SOLID_BLACK,
                                [tc._run("OK", "FFFFFF"), tc._run("BAD", "0A0A0A")])])
    fixed, _b, after = ox.improve_contrast(deck)
    assert fixed == 1 and after == 0


def test_a_small_drop_inside_the_passing_band_does_not_block_the_repair(tmp_path):
    """--ink-4 to --ink-inv-3 moves a run from 5.89 to 5.71 on a black plate.
    Both pass AA and the on-dark ramp is the correct one; an earlier version of
    this rule rejected the whole slide over that, leaving the invisible run."""
    from aaif_events.tests import test_contrast as tc
    from aaif_events import contrast as ct
    deck = _ct_deck(tmp_path, [(tc.SOLID_BLACK,
                                [tc._run("META", "8C8C8C"), tc._run("HOST", "0A0A0A")])])
    fixed, _b, after = ox.improve_contrast(deck)
    assert fixed == 1 and after == 0
    scored = ct.check_pptx(deck, include_passes=True)
    assert {ct._fmt(f.fg) for f in scored} == {"#8A8A86", "#FFFFFF"}


def test_a_clean_deck_is_not_rewritten(tmp_path):
    from aaif_events.tests import test_contrast as tc
    deck = _ct_deck(tmp_path, [(tc.SOLID_WHITE, [tc._run("BODY", "0A0A0A", size=2400)])])
    assert ox.improve_contrast(deck) == ox.Rescued()


def test_escaping_the_invisible_band_counts_even_without_an_aa_crossing(tmp_path):
    """On the old plate the remap lifts text from 1.19 to 4.48 against a 4.50
    threshold. An AA-crossing-only rule scores that as no improvement and leaves
    the text unreadable, which is how 354 invisible runs survived the first
    estate repair."""
    from aaif_events.tests import test_contrast as tc
    from aaif_events import contrast as ct
    # A mid-purple ground, as the old plate is: ink-3 on it is 1.2:1, and
    # ink-inv-2 on it is ~4.5 — right at the line.
    bg = ('<p:bg><p:bgPr><a:solidFill><a:srgbClr val="6B4A6D"/></a:solidFill>'
          "</p:bgPr></p:bg>")
    deck = _ct_deck(tmp_path, [(bg, [tc._run("FOOTER", "4A4A4A")])])
    worst_before = min(f.ratio for f in ct.check_pptx(deck, include_passes=True))
    assert worst_before < ct.INVISIBLE
    fixed, _b, _a = ox.improve_contrast(deck)
    worst_after = min(f.ratio for f in ct.check_pptx(deck, include_passes=True))
    assert worst_after >= ct.AA_LARGE, worst_after
    assert worst_after > worst_before * 2


# --------------------------------------------------------- retiring a plate ---

def _bg_deck(tmp_path, images, name="r.pptx"):
    """A deck whose slide N has media `images[N-1]` as its <p:bg> blip fill."""
    from aaif_events.tests import test_contrast as tc
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", tc._THEME)
        for i, blob in enumerate(images, 1):
            z.writestr("ppt/media/image%d.png" % i, blob)
            z.writestr("ppt/slides/slide%d.xml" % i,
                       '<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
                       "<p:cSld><p:bg><p:bgPr><a:blipFill>"
                       '<a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch>'
                       "</a:blipFill></p:bgPr></p:bg><p:spTree/></p:cSld></p:sld>")
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % i,
                       '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                       'openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="%s" Target="../media/image%d.png"/>'
                       "</Relationships>" % (ox._IMAGE_REL, i))
    return path


def test_only_the_unrecognised_background_is_retired(tmp_path):
    """Ours is identified by hash and kept; whatever else is a background is
    legacy by definition. Recognising the LEGACY plate instead would be fragile
    and would fail open on a plate nobody has seen."""
    import hashlib
    ours, legacy = b"OURS-PLATE", b"LEGACY-PLATE"
    deck = _bg_deck(tmp_path, [ours, legacy])
    keep = {hashlib.sha256(ours).hexdigest()}
    assert ox.retire_plates(deck, b"NEW", keep) == ["ppt/media/image2.png"]
    with zipfile.ZipFile(deck) as z:
        assert z.read("ppt/media/image1.png") == ours
        assert z.read("ppt/media/image2.png") == b"NEW"


def test_a_deck_with_only_our_plates_is_untouched(tmp_path):
    import hashlib
    ours = b"OURS"
    deck = _bg_deck(tmp_path, [ours])
    before = open(deck, "rb").read()
    assert ox.retire_plates(deck, b"NEW", {hashlib.sha256(ours).hexdigest()}) == []
    assert open(deck, "rb").read() == before


def test_a_picture_is_not_a_background(tmp_path):
    """The world map on the network slide is a <p:pic>, not a plate. Swapping
    content for a gradient would be a very visible bug."""
    from aaif_events.tests import test_contrast as tc
    path = str(tmp_path / "pic.pptx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", tc._THEME)
        z.writestr("ppt/media/image1.png", b"MAP")
        z.writestr("ppt/slides/slide1.xml",
                   '<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
                   "<p:cSld><p:spTree><p:pic><p:blipFill>"
                   '<a:blip r:embed="rId1"/></p:blipFill><p:spPr><a:xfrm>'
                   '<a:off x="0" y="0"/><a:ext cx="9144000" cy="5143500"/></a:xfrm>'
                   "</p:spPr></p:pic></p:spTree></p:cSld></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="%s" Target="../media/image1.png"/>'
                   "</Relationships>" % ox._IMAGE_REL)
    assert ox.retire_plates(path, b"NEW", set()) == []
    with zipfile.ZipFile(path) as z:
        assert z.read("ppt/media/image1.png") == b"MAP"


def test_one_media_part_shared_by_several_slides_is_retired_once(tmp_path):
    """The legacy plate is the same file behind two slides of Slides.pptx.
    Swapping the bytes fixes both in one move."""
    deck = _bg_deck(tmp_path, [b"LEGACY"])
    with zipfile.ZipFile(deck, "a") as z:
        z.writestr("ppt/slides/slide9.xml", z.read("ppt/slides/slide1.xml"))
        z.writestr("ppt/slides/_rels/slide9.xml.rels",
                   z.read("ppt/slides/_rels/slide1.xml.rels"))
    used = ox.background_media(deck)
    assert used["ppt/media/image1.png"] == ["ppt/slides/slide1.xml",
                                            "ppt/slides/slide9.xml"]
    assert ox.retire_plates(deck, b"NEW", set()) == ["ppt/media/image1.png"]


# ------------------------------------------------------- embedded Word fonts --

def _docx_with_fonts(tmp_path, name="f.docx"):
    """A .docx shaped like the real trackers: a mix of self-closing and
    container <w:font> entries, three of them carrying embedded faces."""
    path = str(tmp_path / name)
    table = ('<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
             '<w:font w:name="Georgia"/>'
             '<w:font w:name="Arial"/>'
             '<w:font w:name="Manrope"><w:embedRegular r:id="rId1"/></w:font>'
             '<w:font w:name="JetBrains Mono"><w:embedRegular r:id="rId2"/></w:font>'
             '<w:font w:name="Space Grotesk"><w:embedRegular r:id="rId3"/></w:font>'
             "</w:fonts>")
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="font" Target="fonts/Manrope.ttf"/>'
            '<Relationship Id="rId2" Type="font" Target="fonts/JetBrainsMono.ttf"/>'
            '<Relationship Id="rId3" Type="font" Target="fonts/SpaceGrotesk.ttf"/>'
            "</Relationships>")
    ct = ('<Types xmlns="ct">'
          '<Override ContentType="font" PartName="/word/fonts/Manrope.ttf"/>'
          '<Override ContentType="font" PartName="/word/fonts/JetBrainsMono.ttf"/>'
          '<Override ContentType="font" PartName="/word/fonts/SpaceGrotesk.ttf"/>'
          "</Types>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("word/document.xml", '<w:document xmlns:w="w"/>')
        z.writestr("word/fontTable.xml", table)
        z.writestr("word/_rels/fontTable.xml.rels", rels)
        for f in ("Manrope", "JetBrainsMono", "SpaceGrotesk"):
            z.writestr("word/fonts/%s.ttf" % f, b"TTF" * 100)
    return path


def test_the_font_table_ends_up_declaring_only_the_faces_in_use(tmp_path):
    deck = _docx_with_fonts(tmp_path)
    pruned = ox.prune_embedded_fonts(deck)
    # `faces` means exactly one thing again: the declaration went. Mono is not
    # here — it kept its entry — and asserting that is what stops a future edit
    # from dropping the declaration unnoticed.
    assert pruned.faces == ("Arial", "Georgia", "Manrope", "Space Grotesk")
    assert pruned.unembedded == (ox.MONO,)
    with zipfile.ZipFile(deck) as z:
        table = z.read("word/fontTable.xml").decode()
    assert sorted(set(re.findall(r'w:name="([^"]+)"', table))) == \
        ["Instrument Sans", "JetBrains Mono"]


def test_a_self_closing_entry_does_not_swallow_the_next_one(tmp_path):
    """`<w:font w:name="Arial"/>` followed by a container entry: with the
    alternation the wrong way round, one match spans three entries and the two
    in the middle are silently never rewritten."""
    deck = _docx_with_fonts(tmp_path)
    pruned = ox.prune_embedded_fonts(deck)
    assert "Arial" in pruned.faces and "Manrope" in pruned.faces


def test_the_renamed_faces_embedded_bytes_are_dropped_not_relabelled(tmp_path):
    """Manrope's bytes must not end up called Instrument Sans — Word would then
    render the OLD face under the new name, which is worse than substituting."""
    deck = _docx_with_fonts(tmp_path)
    parts = ox.prune_embedded_fonts(deck).parts
    # Mono's part is here too — its bytes go for a different reason
    # (NEVER_EMBED), but `parts` is genuinely one meaning: bytes deleted.
    assert parts == ("word/fonts/JetBrainsMono.ttf", "word/fonts/Manrope.ttf",
                     "word/fonts/SpaceGrotesk.ttf")
    with zipfile.ZipFile(deck) as z:
        names = z.namelist()
        table = z.read("word/fontTable.xml").decode()
        # Its ENTRY survives — this fixture's document.xml names no faces, so
        # `in_use` is empty and the no-evidence escape hatch keeps every
        # declaration. Its EMBED does not, because NEVER_EMBED applies whether
        # or not the face is provably in use.
        assert ox.MONO in table
        assert "word/fonts/JetBrainsMono.ttf" not in names
        assert "word/fonts/Manrope.ttf" not in names
        assert "Manrope.ttf" not in z.read("[Content_Types].xml").decode()


def test_a_face_in_use_keeps_its_declaration_but_loses_its_embed(tmp_path):
    """The direction the real trackers take, and the one nothing tested.

    Removing `_MONO_IS_PROSE` means a tracker's 205 metadata runs stay in
    JetBrains Mono, so the face is genuinely referenced and its declaration
    must survive the prune — a dropped entry would leave 205 runs asking for
    a face the table does not name. Its EMBED must go anyway: `NEVER_EMBED`,
    ~215KB a file, verified rendering in Google Docs without it.

    The other mono test above cannot show this — its document names no faces,
    so the declaration survives there for the opposite reason. This one
    references mono AND embeds it, which is the combination a real tracker has.
    """
    path = str(tmp_path / "in-use.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="ct"><Override ContentType="font" '
                   'PartName="/word/fonts/JetBrainsMono.ttf"/><Override '
                   'ContentType="font" PartName="/word/fonts/Manrope.ttf"/></Types>')
        # Both faces named by real runs; only Manrope is unreferenced.
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="Instrument Sans"/></w:rPr></w:r>'
                   '<w:r><w:rPr><w:rFonts w:ascii="JetBrains Mono" '
                   'w:hAnsi="JetBrains Mono"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="Instrument Sans"/>'
                   '<w:font w:name="JetBrains Mono">'
                   '<w:embedRegular r:id="rId1"/></w:font>'
                   '<w:font w:name="Manrope"><w:embedRegular r:id="rId2"/>'
                   '</w:font></w:fonts>')
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" '
                   'Target="fonts/JetBrainsMono.ttf"/>'
                   '<Relationship Id="rId2" Type="font" '
                   'Target="fonts/Manrope.ttf"/></Relationships>')
        z.writestr("word/fonts/JetBrainsMono.ttf", b"JBM" * 200)
        z.writestr("word/fonts/Manrope.ttf", b"MNR" * 200)

    pruned = ox.prune_embedded_fonts(path)
    # Both shed bytes; only one loses its entry, and the two are now reported
    # on separate channels so the operator line can tell the truth.
    assert pruned.faces == ("Manrope",)
    assert pruned.unembedded == ("JetBrains Mono",)
    assert pruned.parts == ("word/fonts/JetBrainsMono.ttf",
                            "word/fonts/Manrope.ttf")
    with zipfile.ZipFile(path) as z:
        names, table = z.namelist(), z.read("word/fontTable.xml").decode()
        assert ox.MONO in table, "the in-use face lost its declaration"
        assert "Manrope" not in table, "the unused face kept its declaration"
        assert not [n for n in names if "JetBrainsMono" in n]
        assert "word/fonts/Manrope.ttf" not in names
    # Mono has no surviving embed by design here, so `expect_embeds=False`:
    # the point is the orphan-part and orphan-override directions, which do
    # have something to say even when no embed survives.
    assert_package_is_consistent(path, expect_embeds=False)


def assert_package_is_consistent(path, expect_embeds=True):
    """Every direction of the font-table <-> rels <-> parts <-> CT graph.

    `expect_embeds` guards against the trap these checks fell into: once mono
    stopped being embedded, the surviving-embed set went empty and every loop
    below iterated nothing, so the checks PASSED BY CHECKING NOTHING. A caller
    that expects at least one surviving embed must say so.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        table = z.read("word/fontTable.xml").decode()
        rels = (z.read("word/_rels/fontTable.xml.rels").decode()
                if "word/_rels/fontTable.xml.rels" in names else "")
        ct = z.read("[Content_Types].xml").decode()
    embids = set(re.findall(r'r:id="([^"]+)"', table))
    relmap = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    if expect_embeds:
        assert embids, "this fixture no longer exercises anything — see docstring"
    # embed -> rel -> part
    for rid in embids:
        target = relmap.get(rid)
        assert target, "embed %s has no relationship" % rid
        assert "word/" + target in names, "embed %s -> missing part" % rid
    # rel -> part (a rel nothing declares is still a broken reference)
    for rid, target in relmap.items():
        assert "word/" + target in names, "rel %s -> missing part %s" % (rid, target)
    # part -> rel: an orphan TTF is dead weight nothing can reach
    reachable = {"word/" + t for t in relmap.values()}
    for n in names:
        if n.startswith("word/fonts/"):
            assert n in reachable, "orphan font part left in the zip: %s" % n
    # Content_Types must not name a part that is gone
    for over in re.findall(r'<Override[^>]*PartName="/([^"]+)"', ct):
        assert over in names, "Content_Types override -> missing part: %s" % over


def test_all_four_embed_weights_are_stripped_not_just_the_regular(tmp_path):
    """A real tracker embeds mono in four weights; every synthetic fixture in
    this file embedded exactly one.

    So a change reconciling only `<w:embedRegular>` — leaving Bold, Italic and
    BoldItalic — passed the whole suite, and was caught ONLY by the end-to-end
    tracker test, which skips whenever git history is unavailable (a shallow
    CI checkout). Every tracker would have kept ~75% of its mono bytes and a
    dangling relationship per weight, green all the way.

    This fixture makes that regression fail without needing git."""
    path = str(tmp_path / "weights.docx")
    weights = ("Regular", "Bold", "Italic", "BoldItalic")
    embeds = "".join('<w:embed%s r:id="rId%d"/>' % (w, i)
                     for i, w in enumerate(weights, 1))
    rels = "".join('<Relationship Id="rId%d" Type="font" '
                   'Target="fonts/JBM-%s.ttf"/>' % (i, w)
                   for i, w in enumerate(weights, 1))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="ct">%s</Types>'
                   % "".join('<Override ContentType="font" '
                             'PartName="/word/fonts/JBM-%s.ttf"/>' % w
                             for w in weights))
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="JetBrains Mono"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="JetBrains Mono">%s</w:font></w:fonts>' % embeds)
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">%s'
                   "</Relationships>" % rels)
        for w in weights:
            z.writestr("word/fonts/JBM-%s.ttf" % w, b"TTF" * 300)

    pruned = ox.prune_embedded_fonts(path)
    assert pruned.unembedded == (ox.MONO,)
    assert len(pruned.parts) == 4, "only some weights were stripped: %s" % (
        pruned.parts,)
    with zipfile.ZipFile(path) as z:
        names, table = z.namelist(), z.read("word/fontTable.xml").decode()
    assert not [n for n in names if n.startswith("word/fonts/")]
    assert ox.MONO in table, "the declaration must survive all four strips"
    assert "<w:embed" not in table, "an embed element survived"
    assert_package_is_consistent(path, expect_embeds=False)


def test_a_container_form_embed_is_stripped_too(tmp_path):
    """`<w:embedRegular ...></w:embedRegular>` is legal OOXML. Matching only
    the self-closing form made such a face a total silent no-op: reported as
    unembedded, bytes still in the file."""
    path = str(tmp_path / "container.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="JetBrains Mono"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="JetBrains Mono">'
                   '<w:embedRegular r:id="rId1"></w:embedRegular>'
                   "</w:font></w:fonts>")
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" Target="fonts/JBM.ttf"/>'
                   "</Relationships>")
        z.writestr("word/fonts/JBM.ttf", b"TTF" * 200)
    pruned = ox.prune_embedded_fonts(path)
    assert pruned.unembedded == (ox.MONO,)
    with zipfile.ZipFile(path) as z:
        assert "word/fonts/JBM.ttf" not in z.namelist()


def test_a_face_outside_never_embed_keeps_its_embed(tmp_path):
    """The membership test must MEAN something: replacing it with `if True:`
    (strip every in-use face) has to fail.

    The obvious fixture — the metric fallback — does NOT prove this, and the
    first version of this test used it and passed under the mutant. Manrope
    hits the fallback exemption several lines earlier and never reaches the
    NEVER_EMBED branch at all, so it is shielded by the wrong guard. The face
    has to be one that actually traverses the branch: in use, embedded, not
    the fallback, and not in FONT_MAP (so `new_face is face`)."""
    path = str(tmp_path / "outside.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="%s"/></w:rPr></w:r></w:document>' % ox.SANS)
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="%s"><w:embedRegular r:id="rId1"/>'
                   "</w:font></w:fonts>" % ox.SANS)
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" Target="fonts/IS.ttf"/>'
                   "</Relationships>")
        z.writestr("word/fonts/IS.ttf", b"TTF" * 200)
    assert ox.SANS not in ox.NEVER_EMBED and ox.SANS != ox.FALLBACK_FACE
    pruned = ox.prune_embedded_fonts(path)
    assert pruned.unembedded == (), "a face outside NEVER_EMBED was unembedded"
    with zipfile.ZipFile(path) as z:
        assert "word/fonts/IS.ttf" in z.namelist(), \
            "a face outside NEVER_EMBED lost its embed"


def test_the_metric_fallback_is_never_in_never_embed():
    """The one entry that must never join the tuple, pinned as an assertion
    rather than as branch order.

    Adding it does not produce a noisy fight: `ensure_fallback_font` returns
    early on the mere presence of the ENTRY, so prune would strip the bytes,
    the fallback pass would decline to restore them, and the estate would
    converge silently on trackers whose <w:altName> points at a face nobody
    carries. Today only statement ordering prevents it — hoisting the
    NEVER_EMBED check three lines would reach it."""
    assert ox.FALLBACK_FACE not in ox.NEVER_EMBED


def test_no_embed_reference_is_left_dangling(tmp_path):
    """A relationship pointing at a part that is gone is a corrupt file.

    `expect_embeds=False`: `_docx_with_fonts`' only embedded survivor was mono,
    and mono is no longer embedded, so nothing survives here. That is exactly
    why this test went quietly vacuous when `NEVER_EMBED` landed — the loop it
    used to run now has an empty set. The orphan-part and orphan-override
    directions still bite, and `test_a_surviving_embed_stays_fully_wired`
    below covers the case where something IS left embedded."""
    deck = _docx_with_fonts(tmp_path)
    ox.prune_embedded_fonts(deck)
    assert_package_is_consistent(deck, expect_embeds=False)


def test_a_surviving_embed_stays_fully_wired(tmp_path):
    """The direction the fixtures above can no longer reach.

    After `NEVER_EMBED`, the metric fallback is the only face that keeps an
    embed — so it is the only fixture that can prove embed -> rel -> part still
    holds end to end. Without this, every integrity assertion in the suite
    iterates an empty set."""
    path = _docx_asking_for_the_sans(tmp_path, name="wired.docx")
    ox.ensure_fallback_font(path)
    ox.prune_embedded_fonts(path)
    assert_package_is_consistent(path, expect_embeds=True)


def test_two_faces_sharing_one_ttf_do_not_leave_a_broken_reference(tmp_path):
    """Dropping a part because ONE referrer was unembedded breaks the other.

    `testzip()` passes (the remaining members are intact) and `audit()` passes
    (no token drift), so nothing else in this suite would notice; Word offers
    to repair the file."""
    path = str(tmp_path / "shared.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="JetBrains Mono"/></w:rPr></w:r><w:r><w:rPr>'
                   '<w:rFonts w:ascii="Manrope"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="JetBrains Mono">'
                   '<w:embedRegular r:id="rId1"/></w:font>'
                   '<w:font w:name="Manrope"><w:altName w:val="Manrope"/>'
                   '<w:embedRegular r:id="rId2"/></w:font></w:fonts>')
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" Target="fonts/Shared.ttf"/>'
                   '<Relationship Id="rId2" Type="font" Target="fonts/Shared.ttf"/>'
                   "</Relationships>")
        z.writestr("word/fonts/Shared.ttf", b"TTF" * 200)
    ox.prune_embedded_fonts(path)
    assert_package_is_consistent(path, expect_embeds=True)
    with zipfile.ZipFile(path) as z:
        assert "word/fonts/Shared.ttf" in z.namelist(), \
            "the part was dropped while a surviving embed still pointed at it"


def test_a_collapsed_duplicate_entry_still_gives_up_its_embeds(tmp_path):
    """`FONT_MAP` maps Consolas onto mono, so a table naming Consolas BEFORE a
    real JetBrains Mono entry collapses the mono entry onto it.

    That path `continue`s past the block, and it used to do so without
    harvesting the block's embed r:ids — so the entry that actually HELD the
    embeds vanished from the table while its TTFs and relationships stayed,
    and `Pruned` reported only "Consolas". Two orphan parts, silently."""
    path = str(tmp_path / "collapse.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="JetBrains Mono"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="Consolas"/>'
                   '<w:font w:name="JetBrains Mono">'
                   '<w:embedRegular r:id="rId1"/>'
                   '<w:embedBold r:id="rId2"/></w:font></w:fonts>')
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" Target="fonts/JBM-r.ttf"/>'
                   '<Relationship Id="rId2" Type="font" Target="fonts/JBM-b.ttf"/>'
                   "</Relationships>")
        z.writestr("word/fonts/JBM-r.ttf", b"R" * 500)
        z.writestr("word/fonts/JBM-b.ttf", b"B" * 500)
    ox.prune_embedded_fonts(path)
    assert_package_is_consistent(path, expect_embeds=False)
    with zipfile.ZipFile(path) as z:
        assert not [n for n in z.namelist() if n.startswith("word/fonts/")], \
            "the collapsed entry's embeds were left behind"


def test_pruning_is_idempotent(tmp_path):
    deck = _docx_with_fonts(tmp_path)
    ox.prune_embedded_fonts(deck)
    assert ox.prune_embedded_fonts(deck) == ox.Pruned()


def test_a_bad_repack_never_replaces_the_original(tmp_path):
    """_rewrite_zip_to writes over the good file and the sweep uploads it, so a
    failed repack must leave both the original and the filesystem untouched."""
    deck = _docx_with_fonts(tmp_path)
    before = open(deck, "rb").read()

    def boom(name, data):
        raise RuntimeError("disk says no")

    with pytest.raises(RuntimeError):
        ox._rewrite_zip_to(deck, deck + ".new", boom)
    assert open(deck, "rb").read() == before
    assert not os.path.exists(deck + ".new")


def test_retiring_refuses_to_put_png_bytes_under_another_type(tmp_path):
    """Swapping bytes in place keeps the part NAME, so the replacement has to
    match the type that name declares."""
    path = str(tmp_path / "j.pptx")
    from aaif_events.tests import test_contrast as tc
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="p"><p:sldSz cy="5143500" cx="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", tc._THEME)
        z.writestr("ppt/media/image1.jpeg", b"JPEGBYTES")
        z.writestr("ppt/slides/slide1.xml",
                   '<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r">'
                   "<p:cSld><p:bg><p:bgPr><a:blipFill>"
                   '<a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch>'
                   "</a:blipFill></p:bgPr></p:bg><p:spTree/></p:cSld></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="%s" Target="../media/image1.jpeg"/>'
                   "</Relationships>" % ox._IMAGE_REL)
    with pytest.raises(RuntimeError, match="declares another"):
        ox.retire_plates(path, b"PNGBYTES", set())


def test_the_repair_will_not_push_readable_text_into_the_invisible_band(tmp_path):
    """The asymmetry that mattered: escaping the invisible band counted as a
    rescue, but FALLING into it did not count as a break — because `broke` only
    tested an AA crossing, and a run already below AA cannot cross it again.

    Here a mid-grey label is legible on a mid-grey ground and would be whitened
    into near-invisibility, while a second run is genuinely rescued. The slide
    must be rejected: one rescue does not buy one disappearance."""
    from aaif_events.tests import test_contrast as tc
    from aaif_events import contrast as ct
    # A light-ish ground: ink-3 on it is legible, ink-inv-2 on it is not.
    bg = ('<p:bg><p:bgPr><a:solidFill><a:srgbClr val="C9C9C5"/></a:solidFill>'
          "</p:bgPr></p:bg>")
    deck = _ct_deck(tmp_path, [(bg, [tc._run("LABEL", ox.token("ink-3")),
                                     tc._run("HOST", ox.token("ink"))])])
    before = {round(f.ratio, 2) for f in ct.check_pptx(deck, include_passes=True)}
    assert ox.improve_contrast(deck)[0] == 0, "the slide should have been rejected"
    after = {round(f.ratio, 2) for f in ct.check_pptx(deck, include_passes=True)}
    assert before == after, "the file must be left untouched"


def test_a_face_nothing_references_is_dropped_even_if_it_was_not_renamed(tmp_path):
    """The table must agree with USAGE, not just with the rename map: a face
    can go unreferenced without the rename map ever naming it. The document
    below asks only for Instrument Sans, so the mono entry and its embed go.

    See `test_a_face_in_use_keeps_its_declaration_and_its_embed` for the other
    direction — the real trackers DO reference mono, and there its embed must
    survive."""
    path = str(tmp_path / "u.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="ct"><Override ContentType="font" '
                   'PartName="/word/fonts/JetBrainsMono.ttf"/></Types>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="Instrument Sans"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="Instrument Sans"/>'
                   '<w:font w:name="JetBrains Mono">'
                   '<w:embedRegular r:id="rId1"/></w:font></w:fonts>')
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" '
                   'Target="fonts/JetBrainsMono.ttf"/></Relationships>')
        z.writestr("word/fonts/JetBrainsMono.ttf", b"TTF" * 200)
    pruned = ox.prune_embedded_fonts(path)
    assert pruned.faces == ("JetBrains Mono",)      # unused: declaration went
    assert pruned.unembedded == ()                  # not the NEVER_EMBED path
    assert pruned.parts == ("word/fonts/JetBrainsMono.ttf",)
    with zipfile.ZipFile(path) as z:
        assert "word/fonts/JetBrainsMono.ttf" not in z.namelist()
        assert "JetBrains Mono" not in z.read("word/fontTable.xml").decode()


def test_a_face_that_is_still_referenced_is_kept(tmp_path):
    """The control: pruning by usage must not strip a face in use."""
    path = str(tmp_path / "k.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="JetBrains Mono"/></w:rPr></w:r></w:document>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="JetBrains Mono"/></w:fonts>')
    assert ox.prune_embedded_fonts(path) == ox.Pruned()
    with zipfile.ZipFile(path) as z:
        assert "JetBrains Mono" in z.read("word/fontTable.xml").decode()


# ------------------------------------------------ the audit must FIND drift ---
# audit() underpins every "N off-system" number the sweep reports, and its only
# other assertion is `== []` over fixtures this branch replaced with
# post-restyle copies — the trivially-passing shape. Mutation testing showed
# audit() -> [] leaves every suite green. These are the positive controls.

_DRIFT_FACES = ("Space Grotesk", "Manrope", "Arial")
_DRIFT_COLOUR = "1E2761"


def _pptx_with_drift(tmp_path, name="d.pptx"):
    path = str(tmp_path / name)
    runs = "".join('<a:rPr><a:latin typeface="%s"/></a:rPr>' % f for f in _DRIFT_FACES)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml",
                   '<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a">'
                   "<p:spTree><p:sp><p:spPr><a:solidFill>"
                   '<a:srgbClr val="%s"/></a:solidFill></p:spPr>%s</p:sp>'
                   "</p:spTree></p:sld>" % (_DRIFT_COLOUR, runs))
    return path


def _docx_with_drift(tmp_path, name="d.docx"):
    path = str(tmp_path / name)
    fonts = "".join('<w:rFonts w:ascii="%s"/>' % f for f in _DRIFT_FACES)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:p><w:rPr>%s'
                   '<w:color w:val="%s"/></w:rPr>'
                   '<w:tcPr><w:shd w:fill="%s" w:val="clear"/></w:tcPr>'
                   "</w:p></w:document>"
                   % (fonts, _DRIFT_COLOUR.lower(), _DRIFT_COLOUR.lower()))
    return path


def _xlsx_with_drift(tmp_path, name="d.xlsx"):
    path = str(tmp_path / name)
    fonts = "".join('<font><name val="%s"/></font>' % f for f in _DRIFT_FACES)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0"?><styleSheet><fonts>%s</fonts>'
                   '<fills><fill><patternFill patternType="solid">'
                   '<fgColor rgb="FF%s"/></patternFill></fill></fills>'
                   "</styleSheet>" % (fonts, _DRIFT_COLOUR))
    return path


@pytest.mark.parametrize("build", [_pptx_with_drift, _docx_with_drift, _xlsx_with_drift],
                         ids=["pptx", "docx", "xlsx"])
def test_the_audit_names_the_drift_it_exists_to_find(tmp_path, build):
    """Per DIALECT on purpose: `_is_restyled_part` decides which parts are
    opened at all, and a filter that quietly stops matching one dialect is how
    88 spreadsheets audited clean while full of Calibri and navy."""
    hits = ox.audit(build(tmp_path))
    assert hits, "audit found nothing in a file built to be full of drift"
    fonts = {v for _p, kind, v in hits if kind == "font"}
    colours = {v for _p, kind, v in hits if kind == "colour"}
    for face in _DRIFT_FACES:
        assert face in fonts, "%s not reported (got %s)" % (face, sorted(fonts))
    assert _DRIFT_COLOUR in colours, sorted(colours)


@pytest.mark.parametrize("build", [_pptx_with_drift, _docx_with_drift, _xlsx_with_drift],
                         ids=["pptx", "docx", "xlsx"])
def test_the_audit_is_silent_once_the_drift_is_gone(tmp_path, build):
    """The other half of the pair: restyle the same file and the audit must
    then report nothing. Without this, "found drift" could just mean "reports
    everything"."""
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        "skills", "aaif-create-chapter", "scripts"))
    import create_chapter as cc
    path = build(tmp_path, name="r" + os.path.splitext(build(tmp_path))[1])
    cc._rewrite_zip(path, ox.restyle_part)
    assert ox.audit(path) == []


# --------------------------------------------- the embedded metric fallback --

def _docx_asking_for_the_sans(tmp_path, entry=None, name="fb.docx"):
    path = str(tmp_path / name)
    entry = entry or '<w:font w:name="%s"/>' % ox.SANS
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="%s"/></w:rPr></w:r></w:document>' % ox.SANS)
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">%s'
                   "</w:fonts>" % entry)
    return path


def test_the_fallback_is_embedded_and_pointed_at_by_altname(tmp_path):
    """Instrument Sans cannot be embedded from here, so the file must carry
    something for Word to reach for — named through <w:altName>, which is the
    OOXML mechanism for "use this when the requested face is missing"."""
    deck = _docx_asking_for_the_sans(tmp_path)
    assert ox.ensure_fallback_font(deck) is True
    with zipfile.ZipFile(deck) as z:
        table = z.read("word/fontTable.xml").decode()
        names = z.namelist()
    assert '<w:altName w:val="%s"/>' % ox.FALLBACK_FACE in table
    assert table.count("<w:altName") == 1, "altName inserted more than once"
    for _tag, filename in ox.FALLBACK_FILES:
        assert "word/fonts/%s" % filename in names


def test_the_fallback_is_not_relabelled_as_the_brand_face(tmp_path):
    """The whole point. Embedding Manrope's bytes under the name Instrument
    Sans would make Word render one face under another's name — worse than
    substituting, because nothing downstream can tell."""
    deck = _docx_asking_for_the_sans(tmp_path)
    ox.ensure_fallback_font(deck)
    with zipfile.ZipFile(deck) as z:
        table = z.read("word/fontTable.xml").decode()
    entries = re.findall(r'<w:font w:name="([^"]+)"', table)
    assert ox.FALLBACK_FACE in entries, "the fallback keeps its own name"
    assert ox.SANS in entries
    # The embeds hang off the FALLBACK's entry, never the brand entry.
    brand = re.search(r'<w:font w:name="%s".*?</w:font>' % re.escape(ox.SANS),
                      table, re.S)
    assert brand and "<w:embed" not in brand.group(0)


def test_every_embed_resolves_to_a_part_that_exists(tmp_path):
    deck = _docx_asking_for_the_sans(tmp_path)
    ox.ensure_fallback_font(deck)
    with zipfile.ZipFile(deck) as z:
        names = set(z.namelist())
        table = z.read("word/fontTable.xml").decode()
        rels = z.read("word/_rels/fontTable.xml.rels").decode()
        assert 'Extension="ttf"' in z.read("[Content_Types].xml").decode()
    relmap = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    ids = re.findall(r'r:id="([^"]+)"', table)
    assert ids
    for rid in ids:
        assert "word/" + relmap[rid] in names


def test_it_is_idempotent(tmp_path):
    deck = _docx_asking_for_the_sans(tmp_path)
    assert ox.ensure_fallback_font(deck) is True
    before = open(deck, "rb").read()
    assert ox.ensure_fallback_font(deck) is False
    assert open(deck, "rb").read() == before


def test_a_container_entry_also_gets_the_altname(tmp_path):
    """The brand entry may be self-closing or a container; both must work, and
    neither may end up with the altName twice."""
    deck = _docx_asking_for_the_sans(
        tmp_path, entry='<w:font w:name="%s"><w:charset w:val="00"/></w:font>'
                        % ox.SANS, name="c.docx")
    assert ox.ensure_fallback_font(deck) is True
    with zipfile.ZipFile(deck) as z:
        table = z.read("word/fontTable.xml").decode()
    assert table.count("<w:altName") == 1


def test_a_document_that_does_not_ask_for_the_sans_is_untouched(tmp_path):
    path = str(tmp_path / "other.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml", '<w:document xmlns:w="w"/>')
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w">'
                   '<w:font w:name="Calibri"/></w:fonts>')
    before = open(path, "rb").read()
    assert ox.ensure_fallback_font(path) is False
    assert open(path, "rb").read() == before


def test_the_fallback_face_is_metrically_the_closest_of_the_candidates():
    """Chosen by measurement. Instrument Sans's proportions are derived from
    the design system's own metric-matched fallback (Arial, size-adjust
    102.74%); the candidates are the faces these files already embedded."""
    arial_xh, arial_cap = 1062 / 2048.0, 1467 / 2048.0
    inst_xh, inst_cap = arial_xh * 1.0274, arial_cap * 1.0274
    candidates = {"Manrope": (0.540, 0.720), "Space Grotesk": (0.486, 0.700)}
    scored = {n: abs(xh - inst_xh) + abs(cap - inst_cap)
              for n, (xh, cap) in candidates.items()}
    assert min(scored, key=scored.get) == ox.FALLBACK_FACE


def test_pruning_keeps_a_face_that_only_an_altname_references(tmp_path):
    """The fallback is referenced by <w:altName>, never by a w:rFonts. Pruning
    it as "unused" makes prune and ensure_fallback_font fight: one drops it,
    the other re-adds it, and every sweep re-uploads every tracker forever."""
    deck = _docx_asking_for_the_sans(tmp_path, name="alt.docx")
    ox.ensure_fallback_font(deck)
    assert ox.prune_embedded_fonts(deck) == ox.Pruned(), "the fallback was pruned"
    with zipfile.ZipFile(deck) as z:
        assert ox.FALLBACK_FACE in z.read("word/fontTable.xml").decode()


def test_the_two_font_passes_converge_with_a_never_embed_face(tmp_path):
    """The convergence test below uses a fixture declaring only the sans, so
    the NEVER_EMBED branch never runs inside it. This one puts mono in the
    file, which is the shape every real tracker has.

    Two full rounds of prune+fallback must reach a fixed point: if they did
    not, every sweep would re-upload every tracker forever."""
    path = str(tmp_path / "converge-mono.docx")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts '
                   'w:ascii="%s"/></w:rPr></w:r><w:r><w:rPr><w:rFonts '
                   'w:ascii="%s"/></w:rPr></w:r></w:document>' % (ox.SANS, ox.MONO))
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">'
                   '<w:font w:name="%s"/>'
                   '<w:font w:name="%s"><w:embedRegular r:id="rId1"/>'
                   "</w:font></w:fonts>" % (ox.SANS, ox.MONO))
        z.writestr("word/_rels/fontTable.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="font" Target="fonts/JBM.ttf"/>'
                   "</Relationships>")
        z.writestr("word/fonts/JBM.ttf", b"TTF" * 200)

    ox.prune_embedded_fonts(path)
    ox.ensure_fallback_font(path)
    with open(path, "rb") as fh:
        first = fh.read()
    second_pruned = ox.prune_embedded_fonts(path)
    second_fallback = ox.ensure_fallback_font(path)
    with open(path, "rb") as fh:
        second = fh.read()
    assert second_pruned == ox.Pruned(), "the second prune found work to do"
    assert second_fallback is False, "the fallback pass ran twice"
    assert first == second, "the two passes do not converge"
    assert_package_is_consistent(path, expect_embeds=True)


def test_the_two_font_passes_converge(tmp_path):
    """Run both, twice, in the order the sweep runs them. The second round must
    change nothing at all — otherwise the estate never reaches a steady state."""
    deck = _docx_asking_for_the_sans(tmp_path, name="conv.docx")
    ox.prune_embedded_fonts(deck)
    ox.ensure_fallback_font(deck)
    settled = open(deck, "rb").read()
    ox.prune_embedded_fonts(deck)
    ox.ensure_fallback_font(deck)
    assert open(deck, "rb").read() == settled
