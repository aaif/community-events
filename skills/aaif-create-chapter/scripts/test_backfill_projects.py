#!/usr/bin/env python3
"""Tests for backfill_projects.py's slide-XML engine. Plain script: run it, it
exits 1 on the first failure. No Drive access, and every slide here is synthetic
markup built by `slide()` below — no real chapter, organizer or file, per the
no-PII rule in AGENTS.md.
"""
import os, sys, tempfile, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill_projects as bp
import create_chapter as cc

# No test in this file may touch Drive — the same guard the sibling suite
# carries, after a "unit" test there once listed the live Chapters folder and
# downloaded a production deck. main() has no pre-Drive validation, so one
# mocked argv line would be enough to start a sweep.
for _name in ("gws_json", "gws_download", "gws_upload", "list_children", "_gws"):
    setattr(cc, _name, (lambda n: lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("test touched the network via cc.%s" % n)))(_name))

FAILED = []


def eq(name, got, want):
    if got != want:
        FAILED.append("%s\n     got:  %r\n     want: %r" % (name, got, want))


def ok(name, cond, detail=""):
    if not cond:
        FAILED.append("%s%s" % (name, "\n     " + detail if detail else ""))


def sp(x, y, cx, cy, text, sz=1400):
    """One text shape at the given geometry, in the template's own markup shape."""
    return ('<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody>'
            '<a:bodyPr/><a:lstStyle/><a:p><a:pPr indent="0"/><a:r>'
            '<a:rPr b="1" lang="en-US" sz="%d"/><a:t>%s</a:t></a:r>'
            '<a:endParaRPr sz="%d"/></a:p></p:txBody></p:sp>'
            % (x, y, cx, cy, sz, text, sz))


def slide(*shapes):
    return ('<p:sld><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>' % "".join(shapes))


# Geometry lifted from the real About slide (the template's own numbers, which
# are design, not data): eyebrow, roster, members line, and the stat to the right.
EYEBROW = sp(457200, 3246120, 3657600, 219456, "THE PROJECTS", sz=850)
OLD = "MCP · goose · AGENTS.md · agentgateway"
ROSTER = sp(457200, 3520440, 5120640, 320040, OLD)
MEMBERS = sp(457200, 3931920, 5486400, 237744, "8 FOUNDING PLATINUM MEMBERS", sz=900)
STAT = sp(6126480, 3648456, 2560320, 502920, "OPEN", sz=3000)
ABOUT = slide(EYEBROW, ROSTER, MEMBERS, STAT)


# ---------------------------------------------------------------- finding it
shp = bp.de.shapes(ABOUT)
eq("roster is the line under the eyebrow, not the members line",
      shp[bp.find_roster(shp)].text, OLD)

no_label = bp.de.shapes(slide(ROSTER, MEMBERS))
eq("no eyebrow -> no roster", bp.find_roster(no_label), None)

# A deck whose roster shape was deleted must NOT promote the members line: it is
# below the eyebrow, but too far below and (here) at a different left edge.
far = bp.de.shapes(slide(EYEBROW, MEMBERS, STAT))
eq("members line is out of the eyebrow's reach", bp.find_roster(far), None)

indented = bp.de.shapes(slide(EYEBROW, sp(914400, 3520440, 5120640, 320040, OLD)))
eq("a line on a different left edge is not the roster",
      bp.find_roster(indented), None)


# ------------------------------------------------------------------- guards
ok("the stock four-project roster is stock", bp.is_stock(OLD))
ok("the new six-project roster is stock", bp.is_stock(bp.ROSTER))
ok("long-form names count as stock",
   bp.is_stock("Model Context Protocol · goose · AGENTS.md · agentgateway"))
ok("a comma-separated retype counts as stock", bp.is_stock("MCP, goose, AGENTS.md"))
ok("a chapter's own wording is NOT stock",
   not bp.is_stock("MCP · goose · and our own thing"))
ok("prose is not a roster", not bp.is_stock("The projects agents are built on."))
ok("a trailing separator is not a roster", not bp.is_stock("MCP · goose ·"))


# ------------------------------------------------------------------ rewrite
out, status, detail = bp.rewrite_slide(ABOUT)
eq("the four-project roster is updated", status, "updated")
new_shp = bp.de.shapes(out)
i = bp.find_roster(new_shp)
eq("the roster now names all six", new_shp[i].text, bp.ROSTER)
eq("the roster keeps its point size", bp.de.font_size(new_shp[i].body), 1400)
ok("the roster was widened to hold six names",
   new_shp[i].box.cx > 5120640,
   "cx stayed at %d" % new_shp[i].box.cx)
ok("the widened roster clears the stat column's gutter",
   new_shp[i].box.x + new_shp[i].box.cx <= 6126480 - bp.GUTTER,
   "roster runs to %d, stat starts at 6126480" % (new_shp[i].box.x + new_shp[i].box.cx))
ok("the roster is still wide enough for its text at 14pt",
   new_shp[i].box.cx >= bp.text_width(bp.ROSTER, 1400),
   "cx %d < needed %d" % (new_shp[i].box.cx, bp.text_width(bp.ROSTER, 1400)))
eq("nothing else on the slide moved",
      [(s.text, s.box) for s in new_shp if s.text != bp.ROSTER],
      [(s.text, s.box) for s in shp if s.text != OLD])
ok("the detail line names both rosters", OLD in detail and bp.ROSTER in detail, detail)

# Idempotence: the second pass must change nothing at all, byte for byte.
again, status2, _d = bp.rewrite_slide(out)
eq("a rewritten slide reports as current", status2, "current")
eq("a rewritten slide is not touched again", again, out)

# A slide with no eyebrow is reported as having no roster, not as clean-and-done.
_x, status3, _d = bp.rewrite_slide(slide(MEMBERS, STAT))
eq("a slide without the eyebrow has no roster", status3, "none")

custom = slide(EYEBROW, sp(457200, 3520440, 5120640, 320040,
                           "MCP · goose · our local agent guild"), MEMBERS, STAT)
kept, status4, detail4 = bp.rewrite_slide(custom)
eq("a hand-written roster is skipped", status4, "custom")
eq("a hand-written roster is left byte-identical", kept, custom)
eq("the skip reports what it found", detail4, "MCP · goose · our local agent guild")


# ----------------------------------------------------------- the tight case
# A roster boxed in by a neighbour it cannot be widened past must be stepped
# down in type rather than run into it.
NEAR_STAT = sp(4500000, 3648456, 2560320, 502920, "OPEN", sz=3000)
tight = slide(EYEBROW, ROSTER, MEMBERS, NEAR_STAT)
out5, status5, detail5 = bp.rewrite_slide(tight)
eq("a boxed-in roster is shrunk, not run into its neighbour", status5, "shrunk")
tight_shp = bp.de.shapes(out5)
j = bp.find_roster(tight_shp)
sz = bp.de.font_size(tight_shp[j].body)
ok("the shrunk roster is smaller than 14pt but not smaller than the floor",
   bp.FALLBACK_SIZES[-1] <= sz < 1400, "sz=%d" % sz)
ok("the shrunk roster fits the space it has",
   bp.text_width(bp.ROSTER, sz) <= 4500000 - 457200 - bp.GUTTER,
   "needs %d EMU" % bp.text_width(bp.ROSTER, sz))
ok("the shrink is reported in the detail", "to fit" in detail5, detail5)

# A band so tight that even the floor size overruns it: the roster still goes to
# the floor and still reports "shrunk" — an operator reads that line and decides
# — but it is never widened to make room it does not have.
crushed = slide(EYEBROW, ROSTER, MEMBERS,
                sp(3657600, 3648456, 2560320, 502920, "OPEN", sz=3000))
out7, status7, _d = bp.rewrite_slide(crushed)
crushed_shp = bp.de.shapes(out7)
n = bp.find_roster(crushed_shp)
eq("an impossible band is still flagged", status7, "shrunk")
eq("an impossible band goes to the floor size",
      bp.de.font_size(crushed_shp[n].body), bp.FALLBACK_SIZES[-1])
eq("an impossible band does not widen the box",
      crushed_shp[n].box.cx, 5120640)

# With nothing to its right, the roster may widen to the slide's right margin.
open_band = slide(EYEBROW, ROSTER, MEMBERS)
out6, status6, _d = bp.rewrite_slide(open_band)
k = bp.find_roster(bp.de.shapes(out6))
eq("an unobstructed roster keeps its size", status6, "updated")
ok("an unobstructed roster stays inside the slide",
   bp.de.shapes(out6)[k].box.cx <= bp.DEFAULT_SLIDE_CX - 457200 - bp.GUTTER)


# --------------------------------------------------------------- .pptx pass
def build_pptx(path, slide_xml, decl_cx=bp.DEFAULT_SLIDE_CX):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ppt/presentation.xml",
                   '<p:presentation><p:sldSz cx="%d" cy="5143500"/></p:presentation>'
                   % decl_cx)
        z.writestr("ppt/slides/slide1.xml", '<p:sld><p:cSld><p:spTree/></p:cSld></p:sld>')
        z.writestr("ppt/slides/slide3.xml", slide_xml)
        z.writestr("docProps/app.xml", "<Properties/>")


with tempfile.TemporaryDirectory() as tmp:
    src, dst = os.path.join(tmp, "in.pptx"), os.path.join(tmp, "out.pptx")
    build_pptx(src, ABOUT)
    report = bp.rewrite_pptx(src, dst)
    eq("only the About slide is reported",
          sorted(report), ["ppt/slides/slide3.xml"])
    eq("the About slide is updated", report["ppt/slides/slide3.xml"][0], "updated")
    ok("a changed deck is repacked", os.path.exists(dst))
    with zipfile.ZipFile(dst) as z:
        ok("the repack is valid", z.testzip() is None)
        eq("every part survives the repack", sorted(z.namelist()),
              ["docProps/app.xml", "ppt/presentation.xml",
               "ppt/slides/slide1.xml", "ppt/slides/slide3.xml"])
        ok("the rewritten part carries the six",
           bp.ROSTER in z.read("ppt/slides/slide3.xml").decode("utf-8"))

    # A deck already carrying the six is not repacked at all: no upload is paid
    # for a file that would be byte-identical.
    src2, dst2 = os.path.join(tmp, "in2.pptx"), os.path.join(tmp, "out2.pptx")
    build_pptx(src2, bp.rewrite_slide(ABOUT)[0])
    report2 = bp.rewrite_pptx(src2, dst2)
    eq("a current deck reports current",
          report2["ppt/slides/slide3.xml"][0], "current")
    ok("a current deck is never repacked", not os.path.exists(dst2))

    # A plan run reports the same thing without paying for a repack it would
    # throw away — the deck is only rebuilt when the caller means to upload it.
    src4, dst4 = os.path.join(tmp, "in4.pptx"), os.path.join(tmp, "out4.pptx")
    build_pptx(src4, ABOUT)
    plan = bp.rewrite_pptx(src4, dst4, repack=False)
    eq("a plan run reports the same status",
       plan["ppt/slides/slide3.xml"][0], "updated")
    ok("a plan run writes no deck", not os.path.exists(dst4))

    # The right margin comes from the deck's own declared width.
    src3, dst3 = os.path.join(tmp, "in3.pptx"), os.path.join(tmp, "out3.pptx")
    build_pptx(src3, slide(EYEBROW, ROSTER, MEMBERS), decl_cx=6858000)
    bp.rewrite_pptx(src3, dst3)
    with zipfile.ZipFile(dst3) as z:
        wide = bp.de.shapes(z.read("ppt/slides/slide3.xml").decode("utf-8"))
    m = bp.find_roster(wide)
    ok("a narrower deck's roster stays inside that deck",
       wide[m].box.x + wide[m].box.cx <= 6858000 - bp.GUTTER,
       "roster runs to %d on a 6858000-wide slide" % (wide[m].box.x + wide[m].box.cx))


if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("backfill_projects: all checks passed")
