"""Tests for backfill_host_footer's slide-XML engine.

Plain script, no pytest: run it, it exits 1 on the first failure. The fixtures
are synthetic PresentationML built here, not a real chapter deck — see the PII
rule in AGENTS.md.

  python skills/aaif-create-chapter/scripts/test_backfill_host_footer.py
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill_host_footer as bf

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


# ----------------------------------------------------------------------------
# Fixture builders — a minimal slide in the shape the real templates use
# ----------------------------------------------------------------------------
def text_sp(sid, x, y, cx, cy, text, bold=1, colour=bf.INK, sz=1200, algn="ctr"):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Shape %d"/><p:cNvSpPr/><p:nvPr/>'
            '</p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/>'
            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody>'
            '<a:bodyPr/><a:lstStyle/><a:p><a:pPr indent="0" rtl="0" algn="%s"><a:buNone/>'
            '</a:pPr><a:r><a:rPr b="%d" sz="%d"><a:solidFill><a:srgbClr val="%s"/>'
            '</a:solidFill><a:latin typeface="JetBrains Mono"/></a:rPr><a:t>%s</a:t></a:r>'
            '</a:p></p:txBody></p:sp>'
            % (sid, sid, x, y, cx, cy, algn, bold, sz, colour, text))


def box_sp(sid, x, y, cx, cy):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Shape %d"/><p:cNvSpPr/><p:nvPr/>'
            '</p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/>'
            '</a:xfrm><a:prstGeom prst="roundRect"><a:avLst><a:gd fmla="val 17857" '
            'name="adj"/></a:avLst></a:prstGeom><a:solidFill><a:srgbClr val="F6F5F1"/>'
            '</a:solidFill><a:ln w="12700"><a:solidFill><a:srgbClr val="C9C6BF"/>'
            '</a:solidFill></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p>'
            '<a:pPr><a:buNone/></a:pPr><a:r><a:t> </a:t></a:r></a:p></p:txBody></p:sp>'
            % (sid, sid, x, y, cx, cy))


def header_pic(sid=16, embed="rId3"):
    return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name="Logo"/><p:cNvPicPr/><p:nvPr/>'
            '</p:nvPicPr><p:blipFill><a:blip r:embed="%s"/><a:stretch/></p:blipFill>'
            '<p:spPr><a:xfrm><a:off x="548640" y="512064"/><a:ext cx="475488" cy="475488"/>'
            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            % (sid, embed))


Y, CY = 7516368, 512064
# A chip in its own band, well below the host row — the carousel's member grid.
GRID_BOX = (548640, 8000000, 1901952, 475488)


def hero_slide(with_badge=False):
    """HOSTED BY [HOST VENUE CO.] WITH [MEMBER LOGO] [MEMBER LOGO], all in one band."""
    parts = [header_pic(),
             text_sp(25, 548640, Y, 914400, CY, "HOSTED BY", 0, bf.MUTED, 1100, "l"),
             box_sp(26, 1536192, Y, 1883664, CY),
             text_sp(27, 1536192, Y, 1883664, CY, "HOST VENUE CO.")]
    nxt = 28
    if with_badge:
        parts += [box_sp(nxt, 3600000, Y, 1200000, CY),
                  text_sp(nxt + 1, 3600000, Y, 1200000, CY, "AAIF · SF")]
        nxt += 2
    parts += [text_sp(nxt, 4900000, Y, 566928, CY, "WITH", 0, bf.MUTED, 1100, "l"),
              box_sp(nxt + 1, 5600000, Y, 1792224, CY),
              text_sp(nxt + 2, 5600000, Y, 1792224, CY, "MEMBER LOGO"),
              box_sp(nxt + 3, 7500000, Y, 1792224, CY),
              text_sp(nxt + 4, 7500000, Y, 1792224, CY, "MEMBER LOGO")]
    return "<p:sld><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>" % "".join(parts)


def texts(xml):
    return [t for t in re.findall(r"<a:t>(.*?)</a:t>", xml) if t.strip()]


def offsets(xml):
    """(x, cx) for every element in the footer band, left to right. The lockup
    is two shapes — the mark is vertically centred in the band, so it does not
    share the band's y — and is merged back into the single element it reads as,
    otherwise the gap in front of the wordmark measures the mark as empty space."""
    spans, lock = [], []
    for s in bf.shapes(xml):
        if not s.box:
            continue
        name = re.search(r'<p:cNvPr[^>]*name="([^"]*)"', s.body)
        if name and name.group(1).startswith("AAIF Lockup"):
            lock.append((s.box.x, s.box.x + s.box.cx))
        elif s.box.y == Y and s.box.cy == CY:
            spans.append((s.box.x, s.box.x + s.box.cx))
    if lock:
        spans.append((min(a for a, _ in lock), max(b for _, b in lock)))
    return [(a, b - a) for a, b in sorted(spans)]


# ----------------------------------------------------------------------------
print("rework_slide — the hero footer")
src = hero_slide()
out, chips, host = bf.rework_slide(src)

check("finds all three chips", chips == 3, "got %d" % chips)
check("replaces the host slot", host)
check("no roundRect survives", "roundRect" not in out)
check("the old host name is gone", "HOST VENUE CO." not in out)
check("the lockup wordmark is drawn", texts(out).count("Agentic AI") == 1
      and texts(out).count("Foundation") == 1)
check("the lockup reuses the header's image", out.count('r:embed="rId3"') == 2)
check("placeholders are renumbered", "LOGO 1" in out and "LOGO 2" in out
      and "MEMBER LOGO" not in out)
check("labels survive untouched", "HOSTED BY" in out and "WITH" in out)
check("placeholders are muted", out.count('<a:srgbClr val="%s"/>' % bf.MUTED) >= 4)
check("nothing is bold any more in the row", 'b="1" sz="1200"' not in out)
check("nothing stays centre-aligned", 'algn="ctr"' not in out)

print("rework_slide — left-packing")
xs = offsets(out)
check("row starts at the original left margin", xs[0][0] == 548640, "got %d" % xs[0][0])
gaps = [xs[i + 1][0] - (xs[i][0] + xs[i][1]) for i in range(len(xs) - 1)]
check("every gap is the same", len(set(gaps)) == 1, "gaps=%s" % gaps)
check("the gap is the configured fraction of the band",
      gaps and gaps[0] == int(CY * bf.ROW_GAP), "got %s" % gaps[:1])
check("shapes never overlap", all(g > 0 for g in gaps), "gaps=%s" % gaps)

print("rework_slide — the redundant chapter badge")
out2, chips2, _ = bf.rework_slide(hero_slide(with_badge=True))
check("the badge chip is counted", chips2 == 4, "got %d" % chips2)
check("the badge is dropped, not restyled", "AAIF · SF" not in out2)
check("badge removal does not renumber past LOGO 2",
      "LOGO 1" in out2 and "LOGO 2" in out2 and "LOGO 3" not in out2)

print("rework_slide — real names keep their ink")
grid = ("<p:sld><p:cSld><p:spTree>%s%s%s%s%s</p:spTree></p:cSld></p:sld>"
        % (header_pic(),
           text_sp(20, 548640, Y, 914400, CY, "HOSTED BY", 0, bf.MUTED, 1100, "l"),
           box_sp(21, 1536192, Y, 1883664, CY),
           text_sp(22, 1536192, Y, 1883664, CY, "HOST VENUE CO."),
           box_sp(23, *GRID_BOX) + text_sp(24, *GRID_BOX, text="AWS")))
out3, chips3, _ = bf.rework_slide(grid)
check("a named chip is kept verbatim", "AWS" in out3)
check("a named chip is not renumbered", "LOGO" not in out3)
check("a named chip loses its box", "roundRect" not in out3)
check("a chip outside the host row is not moved",
      any(s.box == GRID_BOX for s in bf.shapes(out3)))

print("rework_slide — idempotence and no-ops")
again, chips_again, _ = bf.rework_slide(out)
check("a second pass finds nothing", chips_again == 0, "got %d" % chips_again)
check("a second pass is byte-identical", again == out)
plain = "<p:sld><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>" % header_pic()
same, n, host_none = bf.rework_slide(plain)
check("a footerless slide is untouched", same == plain and n == 0 and not host_none)

print("rework_slide — a slide with no image to borrow")
no_pic = hero_slide().replace(header_pic(), "")
out4, chips4, host4 = bf.rework_slide(no_pic)
check("chips are still unboxed without a mark", chips4 == 3 and "roundRect" not in out4)
check("the host slot is left alone rather than half-drawn", not host4)
check("no lockup is invented", "Agentic AI" not in out4)

print("retext — run formatting survives")
split = ('<a:r><a:rPr b="1" sz="1200"><a:solidFill><a:srgbClr val="0A0A0A"/></a:solidFill>'
         '</a:rPr><a:t>MEMBER </a:t></a:r><a:r><a:rPr b="1"/><a:t>LOGO</a:t></a:r>')
res = bf.retext(split, "LOGO 7")
check("the first run carries the new text", "<a:t>LOGO 7</a:t>" in res)
check("the trailing run is emptied", res.count("<a:t></a:t>") == 1)
check("the run properties are untouched", 'sz="1200"' in res)
check("XML-special text is escaped", "&amp;" in bf.retext(split, "A & B"))

print("text_width and font_size")
check("mono width is exact", bf.text_width("LOGO 1", 1200)
      == int(6 * bf.MONO_EM * 12 * bf.EMU_PER_PT))
check("font_size reads the first run", bf.font_size('<a:rPr b="0" sz="1100"/>') == 1100)
check("font_size falls back when absent", bf.font_size("<a:rPr/>") == 1100)

print("TEMPLATE_FOLDER_RE")
for good in ("Event Templates (Copy for Each Event)", "Event Template", "Event Name",
             "event templates"):
    check("matches %r" % good, bool(bf.TEMPLATE_FOLDER_RE.match(good)))
for bad in ("Banners (Chapter Specific, Changed Rarely)", "Chapters", "Archive",
            "2026-07-28 · MCP Release Party", "Event Names of Note"):
    check("skips %r" % bad, not bf.TEMPLATE_FOLDER_RE.match(bad))

print("find_host — the label stacked ABOVE its chip row")
# The deck cover and the carousel put HOSTED BY on its own line above the chips,
# so the label shares a band with none of them. A band-only lookup finds no host
# here and would silently leave the slot un-lockup'd — the regression this locks.
LY, LCY = 3877056, 219456
CHIP_Y, CHIP_CY = 4151376, 347472
stacked = ("<p:sld><p:cSld><p:spTree>%s%s%s%s%s%s%s</p:spTree></p:cSld></p:sld>"
           % (header_pic(),
              text_sp(20, 457200, LY, 1828800, LCY, "HOSTED BY", 0, bf.MUTED, 1100, "l"),
              box_sp(21, 457200, CHIP_Y, 1737360, CHIP_CY),
              text_sp(22, 457200, CHIP_Y, 1737360, CHIP_CY, "HOST VENUE CO."),
              text_sp(23, 2331720, CHIP_Y, 1417320, CHIP_CY, "AAIF · SF"),
              box_sp(24, 3886200, CHIP_Y, 1554480, CHIP_CY),
              text_sp(25, 3886200, CHIP_Y, 1554480, CHIP_CY, "MEMBER LOGO")))
# Shape 23 has no box of its own, so make it a real chip by giving it one.
stacked = stacked.replace(text_sp(23, 2331720, CHIP_Y, 1417320, CHIP_CY, "AAIF · SF"),
                          box_sp(26, 2331720, CHIP_Y, 1417320, CHIP_CY)
                          + text_sp(23, 2331720, CHIP_Y, 1417320, CHIP_CY, "AAIF · SF"))
out5, chips5, host5 = bf.rework_slide(stacked)
check("the host is found across bands", host5)
check("the lockup is drawn", "Agentic AI" in out5 and "Foundation" in out5)
check("the stale host name is gone", "HOST VENUE CO." not in out5)
check("the badge is still dropped", "AAIF · SF" not in out5)
check("the remaining slot is renumbered", "LOGO 1" in out5 and "MEMBER LOGO" not in out5)
check("the label itself is not moved into the chip row",
      any(s.box and s.box.y == LY and "HOSTED BY" in s.text for s in bf.shapes(out5)))

print("re-saved XML — attributes on tags and a spaced self-close")
# Google Slides re-serializes a deck the moment anyone opens it: shape tags come
# back carrying attributes and xfrm children self-close as " />". Both forms must
# still parse, or a re-run silently reports a modified deck as clean.
resaved = (hero_slide().replace("<p:sp>", '<p:sp xmlns:foo="urn:x">')
                       .replace("<p:pic>", '<p:pic xmlns:foo="urn:x">')
                       .replace('"/>', '" />'))
check("shapes are still found", len(bf.shapes(resaved)) == len(bf.shapes(hero_slide())))
check("every shape still has geometry",
      all(s.box for s in bf.shapes(resaved) if s.kind in ("sp", "pic")))
out6, chips6, host6 = bf.rework_slide(resaved)
check("chips are still found in a re-saved slide", chips6 == 3, "got %d" % chips6)
check("the host slot is still replaced", host6)
check("boxes are still removed", "roundRect" not in out6)
check("placeholders are still renumbered", "LOGO 1" in out6 and "LOGO 2" in out6)

print("rework_pptx — no output file when there is nothing to upload")
import tempfile, zipfile as _zip, os as _os
with tempfile.TemporaryDirectory() as td:
    src = _os.path.join(td, "plain.pptx")
    dst = _os.path.join(td, "out.pptx")
    with _zip.ZipFile(src, "w") as z:
        z.writestr("ppt/slides/slide1.xml", plain)
        z.writestr("ppt/media/image1.png", b"\x89PNG" + b"0" * 4096)
    rep = bf.rework_pptx(src, dst)
    check("a footerless deck reports nothing", rep == {})
    check("and no output file is written", not _os.path.exists(dst))

print()
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all passed")
