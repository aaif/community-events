#!/usr/bin/env python3
"""Unit tests for the pure logic in sync_about.py (no network/gws).

The document fixtures below are the real markup TemplateCity ships — the same
<w:pPr>, the same numId 1, the same after="0"/after="140" spacing — so a change
that would corrupt a live About.docx fails here first.
"""
import sys, os, tempfile, zipfile, io
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_about
import sync_chapters
from sync_about import (PLACEHOLDER, TEMPLATE_NAMES, clone_bullet,
                        find_section, is_list, para_text, paragraphs, plan_doc,
                        read_document, removals, render, set_spacing,
                        wanted_names, write_document)
from sync_chapters import fold, fold_city

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))

# --- fixtures: TemplateCity's own markup -------------------------------------
PA = ' w:rsidR="00000000" w:rsidDel="00000000" w:rsidP="00000000" w:rsidRPr="00000000"'
PPR_BULLET = ('<w:pPr><w:numPr><w:ilvl w:val="0" /><w:numId w:val="1" /></w:numPr>'
              '<w:spacing w:after="%s" w:line="288" w:lineRule="auto" />'
              '<w:ind w:left="720" w:hanging="360" /><w:rPr /></w:pPr>')
RUN = ('<w:r%s><w:rPr><w:rtl w:val="0" /></w:rPr>'
       '<w:t xml:space="preserve">%s</w:t></w:r>')

def bullet(name, after="0", paraid="0000002A", link=None):
    body = RUN % (PA, name)
    if link:
        body = '<w:hyperlink r:id="%s">%s</w:hyperlink>' % (link, body)
    return ('<w:p%s w14:paraId="%s">%s%s</w:p>'
            % (PA, paraid, PPR_BULLET % after, body))

def heading(text="Organizers "):
    return ('<w:p%s w14:paraId="00000028"><w:pPr><w:pStyle w:val="Heading2" />'
            '<w:spacing w:after="140" w:line="288" w:lineRule="auto" /><w:rPr />'
            '</w:pPr>%s</w:p>' % (PA, RUN % (PA, text)))

LUMA = ('<w:p%s><w:pPr><w:pStyle w:val="Heading2" /></w:pPr>%s</w:p>'
        % (PA, RUN % (PA, "Luma &amp; Socials")))
LUMA_ITEM = ('<w:p%s><w:pPr><w:numPr><w:ilvl w:val="0" /><w:numId w:val="4" />'
             '</w:numPr></w:pPr>%s</w:p>' % (PA, RUN % (PA, "https://luma.com/aaif-x")))

def doc(*paras):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="w" xmlns:w14="w14" xmlns:r="r"><w:body>'
            + "".join(paras) + "<w:sectPr /></w:body></w:document>")

TEMPLATE_BLOCK = [bullet(TEMPLATE_NAMES[0], "0", "2A", link="rId14"),
                  bullet(TEMPLATE_NAMES[1], "0", "2B"),
                  bullet(TEMPLATE_NAMES[2], "0", "2C"),
                  bullet(TEMPLATE_NAMES[3], "140", "2D")]
TEMPLATE_DOC = doc(heading(), *TEMPLATE_BLOCK, LUMA, LUMA_ITEM)

# --- paragraph scanning -------------------------------------------------------
check("paragraphs finds every top-level w:p", len(paragraphs(TEMPLATE_DOC)), 7)
check("para_text unescapes entities",
      para_text(LUMA), "Luma & Socials")
check("is_list sees numPr", (is_list(TEMPLATE_BLOCK[0]), is_list(heading())), (True, False))

# A paragraph nested in a textbox must not end the outer paragraph early — a flat
# `<w:p>.*?</w:p>` match splices the document in half here.
NESTED = doc(heading(), bullet("A", "140"),
             '<w:p%s><w:pPr /><w:r><w:txbxContent><w:p><w:r>'
             '<w:t>inner</w:t></w:r></w:p></w:txbxContent></w:r></w:p>' % PA)
check("paragraphs depth-counts nested w:p", len(paragraphs(NESTED)), 3)

# Self-closing paragraphs appear in Word-saved docs and must count as one.
check("paragraphs handles <w:p/>", len(paragraphs(doc(heading(), "<w:p/>", LUMA))), 3)

# --- find_section -------------------------------------------------------------
sec = find_section(TEMPLATE_DOC)
check("find_section finds 4 bullets", len(sec["bullets"]), 4)
check("find_section stops at the next non-list paragraph",
      TEMPLATE_DOC[sec["span"][1]:].startswith('<w:p%s><w:pPr><w:pStyle w:val="Heading2"' % PA),
      True)
check("find_section reads the bullets in order",
      [para_text(TEMPLATE_DOC[s:e]) for s, e in sec["bullets"]], list(TEMPLATE_NAMES))
# The heading is matched on TEXT, so a doc round-tripped through desktop Word
# with a restyled heading stays syncable.
check("find_section tolerates a restyled heading",
      len(find_section(doc('<w:p><w:pPr><w:pStyle w:val="berschrift2" /></w:pPr>'
                           '<w:r><w:t>Organizers</w:t></w:r></w:p>',
                           bullet("A", "140")))["bullets"]), 1)
check("find_section tolerates a trailing colon",
      find_section(doc(heading("Organizers:"), bullet("A", "140"))) is not None, True)
check("find_section returns None with no heading",
      find_section(doc(LUMA, LUMA_ITEM)), None)
check("find_section handles an empty section",
      find_section(doc(heading(), LUMA))["bullets"], [])
# A bulleted line that merely READS "Organizers" is a list item, not the heading.
_decoy = doc(bullet("Organizers", "0"), heading(), bullet("Asha Rao", "140"))
check("find_section ignores a list item named Organizers",
      [para_text(_decoy[s:e]) for s, e in find_section(_decoy)["bullets"]],
      ["Asha Rao"])

# --- removals -----------------------------------------------------------------
# The intake knows these three for the chapter; only Asha Rao is accepted.
ROSTER = {fold("Asha Rao"), fold("Bo Lin"), fold("Beru Lars")}

# The Melbourne shape: sub-headings interleaved with names, one of them an
# applicant who was never accepted. Both classes must come out.
MEL = ["Approved", "Asha Rao", "Submitted Application", "Beru Lars",
       "Planning Application", "Dr Sam Donegan"]
check("removals: non-accepted applicants are called out",
      removals(MEL, ["Asha Rao"], ROSTER)[0], ["Beru Lars"])
check("removals: lines the intake cannot account for",
      removals(MEL, ["Asha Rao"], ROSTER)[1],
      ["Approved", "Submitted Application", "Planning Application", "Dr Sam Donegan"])
check("removals: a name we are keeping is not a removal",
      removals(["Asha Rao"], ["Asha Rao"], ROSTER), ([], []))
# The template block and the placeholder are the fixture this engine exists to
# clear — the per-chapter diff shows them, so they must not spam either list.
check("removals: the template block is not reported as a removal",
      removals(list(TEMPLATE_NAMES), ["Asha Rao"], set()), ([], []))
check("removals: the placeholder is not reported as a removal",
      removals([PLACEHOLDER], ["Asha Rao"], set()), ([], []))
check("removals: blank bullets are ignored",
      removals(["", "Asha Rao"], ["Asha Rao"], ROSTER), ([], []))

# --- render / clone_bullet ----------------------------------------------------
out = render(["Asha Rao", "Bo Lin"], TEMPLATE_BLOCK)
check("render emits one bullet per name", len(paragraphs(out)), 2)
check("render writes the names in order",
      [para_text(out[s:e]) for s, e in paragraphs(out)], ["Asha Rao", "Bo Lin"])
check("render keeps numId 1 (the Organizers list, not Luma's numId 4)",
      out.count('<w:numId w:val="1" />'), 2)
check("render puts the bottom margin on the LAST bullet only",
      (out.count('w:after="140"'), out.count('w:after="0"')), (1, 1))
check("render drops the model's hyperlink from a new name", "hyperlink" in out, False)
check("render strips w14:paraId from clones", "w14:paraId" in out, False)
check("render escapes markup in a name",
      "&lt;b&gt;" in render(["<b>"], TEMPLATE_BLOCK), True)

# A name already in the block is reused VERBATIM, so its hyperlink and any hand
# formatting survive a re-run.
reused = render([TEMPLATE_NAMES[0], "Asha Rao"], TEMPLATE_BLOCK)
check("render reuses an existing bullet verbatim", 'r:id="rId14"' in reused, True)
check("render keeps the reused bullet's paraId", 'w14:paraId="2A"' in reused, True)
check("render re-spaces a reused bullet that is no longer last",
      reused.split("</w:p>")[0].count('w:after="0"'), 1)

# With no bullet left to clone, MODEL_BULLET rebuilds the list — and it must
# carry numId 1, or the rebuilt section renumbers off the Luma list.
fresh = render(["Asha Rao"], [])
check("render rebuilds from MODEL_BULLET when the block is empty",
      (para_text(fresh), '<w:numId w:val="1" />' in fresh), ("Asha Rao", True))
check("render on an empty name list emits nothing", render([], TEMPLATE_BLOCK), "")
check("clone_bullet accepts a self-closing model",
      para_text(clone_bullet("<w:p/>", "Asha Rao")), "Asha Rao")
check("set_spacing rewrites only the first spacing",
      set_spacing(bullet("A", "0"), "140").count('w:after="140"'), 1)

# --- plan_doc -----------------------------------------------------------------
new, current = plan_doc(TEMPLATE_DOC, ["Asha Rao"], set())
check("plan_doc reports what was there", current, list(TEMPLATE_NAMES))
check("plan_doc replaces the block",
      [para_text(new[s:e]) for s, e in find_section(new)["bullets"]], ["Asha Rao"])
check("plan_doc leaves the rest of the part alone",
      new[new.index("<w:sectPr"):], TEMPLATE_DOC[TEMPLATE_DOC.index("<w:sectPr"):])
check("plan_doc keeps the Luma list intact", new.count('<w:numId w:val="4" />'), 1)

# Idempotence is the property the whole write path is verified against.
again, _ = plan_doc(new, ["Asha Rao"], set())
check("plan_doc is idempotent", again, new)

# A hand-edited list is rewritten, NOT skipped: skipping it is what would leave
# an un-accepted applicant's name published in a shared doc.
hand = doc(heading(), bullet("Approved"), bullet("Asha Rao"),
           bullet("Submitted Application"), bullet("Beru Lars", "140"))
cleaned, was = plan_doc(hand, ["Asha Rao"], ROSTER)
check("plan_doc rewrites a hand-edited list rather than skipping it",
      [para_text(cleaned[s:e]) for s, e in find_section(cleaned)["bullets"]],
      ["Asha Rao"])
check("plan_doc reports the applicant it removed",
      removals(was, ["Asha Rao"], ROSTER)[0], ["Beru Lars"])
check("plan_doc refuses a doc with no heading",
      plan_doc(doc(LUMA), ["Asha Rao"], set())[0], None)

# Emptied to the placeholder, then the chapter's first organizer is accepted.
emptied, _ = plan_doc(TEMPLATE_DOC, [PLACEHOLDER], set())
refilled, _ = plan_doc(emptied, ["Asha Rao"], set())
check("placeholder round-trip refills",
      [para_text(refilled[s:e]) for s, e in find_section(refilled)["bullets"]],
      ["Asha Rao"])

# --- wanted_names -------------------------------------------------------------
BY_CITY = {fold_city("Washington DC"): ["Asha Rao"]}
check("wanted_names folds the folder name onto the intake city",
      wanted_names("Washington, DC", BY_CITY), ["Asha Rao"])
check("wanted_names falls back to the placeholder",
      wanted_names("Bilbao", BY_CITY), [PLACEHOLDER])

# --- zip repack ---------------------------------------------------------------
def make_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types />")
        z.writestr("word/document.xml", TEMPLATE_DOC)
        zi = zipfile.ZipInfo("word/fonts/Manrope-bold.ttf")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, b"\x00\x01FONTBYTES")
    return buf.getvalue()

RAW = make_docx()
repacked = write_document(RAW, "<w:document>new</w:document>")
check("write_document swaps document.xml",
      read_document(repacked), "<w:document>new</w:document>")
with zipfile.ZipFile(io.BytesIO(repacked)) as _z:
    check("write_document preserves other members byte-for-byte",
          _z.read("word/fonts/Manrope-bold.ttf"), b"\x00\x01FONTBYTES")
    check("write_document preserves each member's compress_type",
          _z.getinfo("word/fonts/Manrope-bold.ttf").compress_type, zipfile.ZIP_STORED)
    check("write_document keeps the member order",
          _z.namelist(), ["[Content_Types].xml", "word/document.xml",
                          "word/fonts/Manrope-bold.ttf"])

# --- apply_writes: a doc edited during the plan window is skipped, not reverted --
# compute() downloads ~80 docs and the approval pause adds more, so a human
# edit in that window is normal. The pre-upload re-download must be compared
# against the compute()-time bytes; uploading anyway silently reverts the edit.
def _doc(name, about_id):
    return sync_about.Doc({"name": name}, {"id": about_id}, RAW, TEMPLATE_DOC,
                          list(TEMPLATE_NAMES), ["Asha Rao"],
                          plan_doc(TEMPLATE_DOC, ["Asha Rao"], set())[0],
                          None, [], [])

_remote = {"a-same": RAW,              # untouched since compute()
           "a-edit": RAW + b"\x00tail"}  # changed in the window
_ups = []
# The freshness compare goes through sync_chapters.fresh_if_unchanged, so the
# download to intercept lives in THAT module's namespace, not sync_about's.
with tempfile.TemporaryDirectory() as _wd, \
     mock.patch.object(sync_chapters, "download", lambda fid, path: _remote[fid]), \
     mock.patch.object(sync_about, "upload",
                       lambda fid, path, raw, ct: _ups.append((fid, ct))):
    _ok, _failed = sync_about.apply_writes([_doc("Boston", "a-same"),
                                            _doc("Pune", "a-edit")], _wd)
check("an unchanged doc is written", _ok, ["Boston"])
check("only the unchanged doc was uploaded", [u[0] for u in _ups], ["a-same"])
check("uploads carry the docx content type", [u[1] for u in _ups], [sync_about.DOCX])
check("a changed doc is skipped and counted with the failures",
      [(n, "changed since the plan" in why) for n, why in _failed],
      [("Pune", True)])

# --- a malformed intake row holds the whole chapter's doc back ----------------
# read_intake EXCLUDES a malformed row, and the section is rewritten WHOLESALE,
# so planning that chapter anyway would DELETE the accepted organizer the row
# names — on --write, with a clean exit. The chapter must be held instead: not
# planned, not written, exit non-zero, while every other chapter still syncs.
_MAL = [{"row": 3, "name": "Bo <b>Lin</b>", "city": "Boston",
         "why": "name %r contains control characters or angle brackets" % "Bo <b>Lin</b>"}]
_ENTRIES = [{"row": 2, "name": "Asha Rao", "city": "Pune", "status": "Accepted"}]
_COUNTS = {"Accepted": 2, "Existing (from MLOps)": 0}

def _run_main(argv):
    _rem = {"a-Boston": RAW, "a-Pune": RAW}
    dl, ups = [], []

    def _dl(fid, path):
        dl.append(fid)
        return _rem[fid]

    def _up(fid, path, raw, ct):
        ups.append(fid)
        _rem[fid] = raw                     # so --write's re-verify converges

    with mock.patch.object(sync_about, "read_intake",
                           lambda: (_ENTRIES, [], _COUNTS, [], _MAL)), \
         mock.patch.object(sync_about, "read_roster", lambda: {}), \
         mock.patch.object(sync_about, "list_chapter_folders",
                           lambda: [{"id": "f-Boston", "name": "Boston"},
                                    {"id": "f-Pune", "name": "Pune"}]), \
         mock.patch.object(sync_about, "find_about",
                           lambda fid: ({"id": fid.replace("f-", "a-")}, None)), \
         mock.patch.object(sync_about, "download", _dl), \
         mock.patch.object(sync_chapters, "download", _dl), \
         mock.patch.object(sync_about, "upload", _up), \
         mock.patch.object(sys, "argv", ["sync_about.py"] + argv):
        return sync_about.main(), dl, ups

_rc, _dl, _up2 = _run_main([])
check("a malformed row makes the report run exit non-zero", _rc, 2)
check("report mode never even downloads the held chapter's doc",
      "a-Boston" in _dl, False)
check("the other chapter is still read and planned", "a-Pune" in _dl, True)

_rc, _dl, _up2 = _run_main(["--write"])
check("write mode writes only the unaffected chapter", _up2, ["a-Pune"])
check("write mode neither reads nor writes the held chapter",
      "a-Boston" in _dl, False)
check("the hold keeps the write run's exit non-zero", _rc, 2)

print("\n%s (%d failure(s))" % ("ALL PASS" if not fails else "FAILURES", fails))
sys.exit(1 if fails else 0)
