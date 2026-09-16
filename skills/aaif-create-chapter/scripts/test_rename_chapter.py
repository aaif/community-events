#!/usr/bin/env python3
"""Tests for rename_chapter.py — renaming a chapter that already exists.

Plain script, exit 1 on failure, same shape as the other skill tests. No network:
the transform and the part routing are the parts that decide what gets rewritten,
and they are pure.

What is pinned here is the distinction the capital-city migration got wrong: a
chapter's NAME and its LUMA SLUG are two identities, and renaming the first must
not silently move the second — `aaif-switzerland` was still the live page for
Bern when this script was written, so rewriting that link would have replaced a
working URL with a 404.
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rename_chapter as rc  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print("ok   %s" % label)
    else:
        FAILS.append(label)
        print("FAIL %s: got %r, want %r" % (label, got, want))


# --- make_transform: the name moves, the slug does not ----------------------
tx = rc.make_transform("Scotland", "Edinburgh")
check("prose city is renamed", tx("The AAIF Scotland chapter"), "The AAIF Edinburgh chapter")
check("UPPER heading is renamed", tx("AAIF · SCOTLAND"), "AAIF · EDINBURGH")
check("both cases in one string",
      tx("SCOTLAND — the Scotland chapter"), "EDINBURGH — the Edinburgh chapter")
# The whole point of the --slug-from/--slug-to pair being opt-in.
check("the Luma slug is left alone by default",
      tx("https://luma.com/aaif-scotland"), "https://luma.com/aaif-scotland")
check("...and so is an UPPER slug", tx("AAIF-SCOTLAND"), "AAIF-SCOTLAND")

txs = rc.make_transform("Scotland", "Edinburgh", "scotland", "edinburgh")
# The bug this pair caught on first run: "SCOTLAND" is a substring of the UPPER
# slug, so the name pass rewrote AAIF-SCOTLAND -> AAIF-EDINBURGH and turned a
# live Luma link into a 404 during a rename that was told not to move the slug.
check("an UPPER slug survives a name-only rename inside prose",
      tx("AAIF-SCOTLAND · the Scotland chapter"),
      "AAIF-SCOTLAND · the Edinburgh chapter")
check("a lowercase slug survives it too",
      tx("see luma.com/aaif-scotland for the Scotland chapter"),
      "see luma.com/aaif-scotland for the Edinburgh chapter")
check("an unrelated chapter's slug is never touched",
      tx("aaif-boston"), "aaif-boston")

check("with --slug-to the slug moves too",
      txs("https://luma.com/aaif-scotland"), "https://luma.com/aaif-edinburgh")
check("...case-matched in an UPPER context", txs("AAIF-SCOTLAND"), "AAIF-EDINBURGH")
check("...and the prose still renames", txs("AAIF Scotland"), "AAIF Edinburgh")

# A rename that only DROPS a qualifier — the Madison case.
txm = rc.make_transform("Madison, WI", "Madison")
check("a dropped qualifier renames", txm("AAIF Madison, WI Chapter"), "AAIF Madison Chapter")
check("...in UPPER too", txm("MADISON, WI"), "MADISON")
# The old name is a PREFIX of the new one here, so a careless second pass would
# produce "Madison, WI" -> "Madison" -> and leave nothing to re-match. Applying
# the transform twice must be a no-op.
check("the transform is idempotent", txm(txm("AAIF Madison, WI Chapter")),
      "AAIF Madison Chapter")

# A multi-word new name must not be mangled.
txu = rc.make_transform("Utah", "Salt Lake City")
check("a multi-word new name", txu("AAIF Utah Chapter"), "AAIF Salt Lake City Chapter")
check("...UPPER multi-word", txu("UTAH CHAPTER"), "SALT LAKE CITY CHAPTER")


# --- rename_part: which parts are eligible ----------------------------------
#: The slug-only transform `.rels` now receives — the city-name pass must never
#: reach a relationship part (see rename_part).
SLUG_ONLY = rc.make_transform("Scotland", "Scotland", "scotland", "edinburgh")


def part(name, text, tx_=tx, slug_tx=None):
    return rc.rename_part(name, text.encode("utf-8"), tx_, slug_tx).decode("utf-8")


check("a word document paragraph is rewritten",
      part("word/document.xml", "<w:p><w:r><w:t>AAIF Scotland</w:t></w:r></w:p>"),
      '<w:p><w:r><w:t xml:space="preserve">AAIF Edinburgh</w:t></w:r></w:p>')
check("a slide paragraph is rewritten",
      part("ppt/slides/slide3.xml", "<a:p><a:r><a:t>SCOTLAND</a:t></a:r></a:p>"),
      '<a:p><a:r><a:t xml:space="preserve">EDINBURGH</a:t></a:r></a:p>')
check("a workbook's shared strings are rewritten",
      part("xl/sharedStrings.xml", "<si><t>AAIF Scotland — Attendee CRM</t></si>"),
      '<si><t xml:space="preserve">AAIF Edinburgh — Attendee CRM</t></si>')
check("a worksheet's inline strings are rewritten",
      part("xl/worksheets/sheet1.xml", "<is><t>Scotland</t></is>"),
      '<is><t xml:space="preserve">Edinburgh</t></is>')
check("document metadata is rewritten",
      part("docProps/core.xml", "<dc:title>AAIF Scotland</dc:title>"),
      "<dc:title>AAIF Edinburgh</dc:title>")

# A .rels part holds ids and hyperlink targets, not prose. Touching it without a
# slug change is all risk and no benefit.
RELS = '<Relationship Id="rId7" Target="https://luma.com/aaif-scotland"/>'
check("rels is untouched without a slug change", part("word/_rels/document.xml.rels", RELS), RELS)
check("rels IS rewritten with a slug change",
      part("word/_rels/document.xml.rels", RELS, txs, SLUG_ONLY),
      '<Relationship Id="rId7" Target="https://luma.com/aaif-edinburgh"/>')
# Theme and font parts must never be rewritten: they are not brand text, and the
# embedded brand fonts are what a careless pass destroys.
THEME = "<a:theme><a:srgbClr val=\"000000\"/>Scotland</a:theme>"
check("an unrouted part is returned unchanged", part("ppt/theme/theme1.xml", THEME), THEME)
check("binary parts survive a decode failure",
      rc.rename_part("word/fonts/font1.odttf", b"\x00\x01\xff", tx, None), b"\x00\x01\xff")
check("...and a binary MEDIA part is not recorded as uninspected",
      "word/fonts/font1.odttf" in rc.UNDECODABLE, False)
# An undecodable part that claims to BE xml is a part the verifier could not
# read, and the verifier shares this decoder — so it must be recorded, or a
# never-rewritten part passes verification by being invisible to both.
rc.UNDECODABLE.clear()
rc.rename_part("word/document.xml", b"\xff\xfe<", tx, None)
check("an undecodable XML part IS recorded", "word/document.xml" in rc.UNDECODABLE, True)
rc.UNDECODABLE.clear()


# --- strings_changed: what a human is shown before a CRM is touched ---------
def book(*strings):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   "".join("<si><t>%s</t></si>" % s for s in strings))
    return buf.getvalue()


check("only the strings that actually change are reported",
      rc.strings_changed(book("AAIF Scotland — CRM", "Ada Lovelace", "Boston"), tx),
      ["AAIF Scotland — CRM"])
check("a member row that happens to name the city is surfaced too",
      rc.strings_changed(book("Ada Lovelace", "moved here from Scotland"), tx),
      ["moved here from Scotland"])
check("nothing to change reports nothing",
      rc.strings_changed(book("Ada Lovelace", "Boston"), tx), [])

# --- parts_changed: the selection test IS the rewrite test -------------------
# The bug this pins: strings_changed only ever saw <w:t>/<a:t>/<t> in .xml
# members, while rename_part also rewrites docProps and .rels. Files whose only
# stale text lived there were never queued, and the verify — using that same
# blind predicate — printed "Verified" over them.
def zipped(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in parts.items():
            z.writestr(n, d)
    return buf.getvalue()


DOCPROPS = zipped({"docProps/core.xml": "<dc:title>AAIF Scotland</dc:title>"})
RELS_ONLY = zipped({"word/_rels/document.xml.rels":
                    '<Relationship Id="rId7" Target="https://luma.com/aaif-scotland"/>'})

check("a docProps-only change is SEEN by the selection test",
      rc.parts_changed(DOCPROPS, tx), ["docProps/core.xml"])
check("...and strings_changed alone still cannot see it (why it is not the gate)",
      rc.strings_changed(DOCPROPS, tx), [])
check("a rels-only slug change is seen when a slug move was asked for",
      rc.parts_changed(RELS_ONLY, txs, SLUG_ONLY), ["word/_rels/document.xml.rels"])
check("...and is NOT seen when no slug move was asked for",
      rc.parts_changed(RELS_ONLY, tx), [])
check("a file with nothing to change is not queued",
      rc.parts_changed(zipped({"word/document.xml": "<w:p><w:t>Boston</w:t></w:p>"}), tx), [])

# The .rels part must never receive the city-name pass — rewriting a
# relationship Target while the zip member keeps its name dangles the
# relationship, and the document opens as corrupt.
EMBED = zipped({"word/_rels/document.xml.rels":
                '<Relationship Target="../embeddings/Scotland_Data.xlsx"/>'})
check("a rels Target naming the old city is left alone",
      rc.parts_changed(EMBED, txs, SLUG_ONLY), [])

# --- the XML well-formedness gate -------------------------------------------
# docProps and .rels take the transform over RAW markup (no escaping layer), so
# they are where a `--to` containing markup could produce an unopenable file.
BROKEN = rc.make_transform("Scotland", "Edin<burgh")
try:
    rc.rename_part("docProps/core.xml",
                   '<?xml version="1.0"?><cp:x xmlns:cp="u"><dc:t xmlns:dc="v">'
                   'Scotland</dc:t></cp:x>'.encode("utf-8"), BROKEN, None)
    _raised = False
except RuntimeError:
    _raised = True
check("a transform that breaks the XML is refused, not uploaded", _raised, True)
# The paragraph path escapes its text, so the same value is harmless there —
# worth pinning so nobody "fixes" the escaping later.
check("the same value is escaped, not injected, in a text run",
      "&lt;" in rc.rename_part("word/document.xml",
          '<?xml version="1.0"?><w:p xmlns:w="u"><w:r><w:t>Scotland</w:t></w:r></w:p>'
          .encode("utf-8"), BROKEN, None).decode("utf-8"), True)
check("a well-formed rewrite still passes the gate",
      "Edinburgh" in rc.rename_part("word/document.xml",
          '<?xml version="1.0"?><w:p xmlns:w="u"><w:r><w:t>Scotland</w:t></w:r></w:p>'
          .encode("utf-8"), tx, None).decode("utf-8"), True)

# --- member data and native files -------------------------------------------
check("a chapter CRM is recognised as member data",
      rc.is_member_data("Edinburgh CRM.xlsx"), True)
check("an event tracker is too", rc.is_member_data("Event Tracker.docx"), True)
check("a design asset is not", rc.is_member_data("Slides.pptx"), False)
check("a native Google file is flagged as uninspectable",
      (rc.is_native("About"), rc.is_native("Notes.gdoc")), (True, True))
check("an Office file is not native", rc.is_native("About.docx"), False)

# --- redaction ---------------------------------------------------------------
rc.REDACT = False
check("redaction off: document text is shown", rc.redact_text("AAIF Scotland"),
      "'AAIF Scotland'")
rc.REDACT = True
try:
    check("redaction on: only the shape survives", rc.redact_text("AAIF Scotland"),
          "<13 chars>")
    check("a member row's text does not leak",
          "Ada" in rc.redact_text("Ada Lovelace, Scotland"), False)
finally:
    rc.REDACT = False

print()
print("FAILED %d check(s)" % len(FAILS) if FAILS else
      "rename_chapter: all checks passed.")
sys.exit(1 if FAILS else 0)
