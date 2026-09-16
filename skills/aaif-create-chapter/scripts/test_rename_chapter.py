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
def part(name, text, tx_=tx, rels=False):
    return rc.rename_part(name, text.encode("utf-8"), tx_, rels).decode("utf-8")


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
      part("word/_rels/document.xml.rels", RELS, txs, True),
      '<Relationship Id="rId7" Target="https://luma.com/aaif-edinburgh"/>')
# Theme and font parts must never be rewritten: they are not brand text, and the
# embedded brand fonts are what a careless pass destroys.
THEME = "<a:theme><a:srgbClr val=\"000000\"/>Scotland</a:theme>"
check("an unrouted part is returned unchanged", part("ppt/theme/theme1.xml", THEME), THEME)
check("binary parts survive a decode failure",
      rc.rename_part("word/fonts/font1.odttf", b"\x00\x01\xff", tx, False), b"\x00\x01\xff")


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

print()
print("FAILED %d check(s)" % len(FAILS) if FAILS else
      "rename_chapter: all checks passed.")
sys.exit(1 if FAILS else 0)
