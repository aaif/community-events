#!/usr/bin/env python3
"""Rename an existing chapter everywhere its old city name is written.

`create_chapter.py` rebrands San Francisco -> <City> while CLONING the template.
This does the same transform between two arbitrary cities, in place, on a chapter
that already exists — the step the capital-city migration (2026-08) never had.
That migration renamed the Chapters List rows (Scotland -> Edinburgh, Switzerland
-> Bern, Utah -> Salt Lake City) and stopped there, so the Drive folders, the CRM
workbooks, the About docs and every design asset kept the old name for weeks. The
feed says one thing and the files organizers open say another.

Four surfaces, all of them in Drive:

  * the chapter FOLDER name          "Scotland"            -> "Edinburgh"
  * file and subfolder NAMES         "Scotland CRM.xlsx"   -> "Edinburgh CRM.xlsx"
  * OOXML text in .docx/.pptx/.xlsx  "AAIF Scotland"       -> "AAIF Edinburgh"
  * document metadata (docProps)

**The Luma slug is NOT renamed unless you ask.** It is a separate identity that
lives on luma.com, and the page does not move because a chapter was renamed: as
of 2026-09-17 `aaif-switzerland` and `aaif-utah` are still the LIVE pages for
Bern and Salt Lake City. Rewriting those links to match the new name would point
every organizer at a 404 — the opposite of the bug this fixes. Pass
`--slug-from/--slug-to` only once the page itself has actually been moved.

The Chapters List row, the intake's city cells and Slack channels are NOT touched
here: the first two are `aaif-sync-chapters`' surfaces (and a rename must be
reflected there for the engines to keep matching), and a channel rename needs its
own per-channel consent. The report says so.

Usage:
    python3 rename_chapter.py --from Scotland --to Edinburgh           # report
    python3 rename_chapter.py --from Scotland --to Edinburgh --write
    python3 rename_chapter.py --from Utah --to "Salt Lake City" \
        --slug-from utah --slug-to saltlakecity --write   # only after Luma moved
"""
import argparse
import datetime as dt
import io
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from create_chapter import (CHAPTERS_PARENT, _process_paragraphs,  # noqa: E402
                            _rewrite_zip, gws_download, gws_upload,
                            list_children, rename_file)

#: A Luma slug token. Matched so the city-name pass can be kept out of it.
SLUG_TOKEN = re.compile(r"(?i)\baaif-[a-z0-9]+\b")

OFFICE = (".docx", ".pptx", ".xlsx")
MIME = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
FOLDER_MIME = "application/vnd.google-apps.folder"


def make_transform(old, new, slug_from=None, slug_to=None):
    """Plain-text transform for one chapter rename.

    Case is handled by replacing the two spellings the templates actually use —
    Title Case in prose and UPPER in headings — rather than by a case-insensitive
    pass, which would also rewrite the lowercase city inside URLs and ids that
    are not brand text.
    """
    old_u, new_u = old.upper(), new.upper()

    def tx(text):
        if slug_from and slug_to:
            text = re.sub(r"(?i)aaif-%s\b" % re.escape(slug_from),
                          lambda m: ("AAIF-" + slug_to.upper()) if m.group(0).isupper()
                          else ("aaif-" + slug_to), text)
        # Hide every `aaif-<slug>` token from the name pass, then restore it.
        # Without this, renaming Scotland -> Edinburgh also rewrites the UPPER
        # slug `AAIF-SCOTLAND` in slide headings, because "SCOTLAND" is a
        # substring of it — turning a LIVE Luma link into a 404 as a side effect
        # of a rename that was explicitly asked not to move the slug. A slug only
        # ever changes through the branch above, which is opt-in.
        saved = []

        def hide(m):
            saved.append(m.group(0))
            return "\x00%d\x00" % (len(saved) - 1)

        text = SLUG_TOKEN.sub(hide, text)
        text = text.replace(old, new).replace(old_u, new_u)
        return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)
    return tx


def rename_part(part_name, data, tx, touch_rels):
    """Rebranded bytes for one OOXML part, or the original when unchanged.

    Mirrors create_chapter.rebrand_part's part routing on purpose: the same
    paragraph-aware rewrite, so text split across runs is handled and formatting
    survives. `.rels` is touched ONLY for a slug change — a relationship part
    holds ids and targets, not prose, and a stray replacement there breaks the
    document rather than renaming it.
    """
    try:
        xml = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if re.match(r"ppt/slides/slide\d+\.xml$", part_name):
        xml = _process_paragraphs(xml, "a:p", "a:t", tx)
    elif part_name == "word/document.xml":
        xml = _process_paragraphs(xml, "w:p", "w:t", tx)
    elif part_name == "xl/sharedStrings.xml":
        xml = _process_paragraphs(xml, "si", "t", tx)
    elif re.match(r"xl/worksheets/sheet\d+\.xml$", part_name):
        xml = _process_paragraphs(xml, "is", "t", tx)
    elif part_name in ("docProps/core.xml", "docProps/app.xml"):
        xml = tx(xml)
    elif part_name.endswith(".rels") and touch_rels:
        xml = tx(xml)
    else:
        return data
    return xml.encode("utf-8")


def strings_changed(raw, tx):
    """The distinct pieces of visible text this rename would alter.

    Printed for review because a chapter's CRM is member data: the transform is
    aimed at a title cell, and a row whose own text happens to contain the old
    city name would be rewritten too. That is a judgement a human makes, so the
    strings are shown rather than silently applied.
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            if not n.endswith(".xml"):
                continue
            try:
                xml = z.read(n).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for m in re.finditer(r"<(?:w:t|a:t|t)[^>]*>([^<]+)</(?:w:t|a:t|t)>", xml):
                s = m.group(1)
                if tx(s) != s and s not in out:
                    out.append(s)
    return out


def walk(folder_id, path=""):
    """Every descendant as (relpath, id, name, is_folder), folders included."""
    out = []
    for f in list_children(folder_id):
        isdir = f["mimeType"] == FOLDER_MIME
        out.append((path + f["name"], f["id"], f["name"], isdir))
        if isdir:
            out += walk(f["id"], path + f["name"] + "/")
    return out


def find_folder(name):
    got = [f for f in list_children(CHAPTERS_PARENT)
           if f["mimeType"] == FOLDER_MIME and f["name"] == name]
    if len(got) != 1:
        sys.exit("ABORT: %d folder(s) named %r under Chapters — expected exactly 1."
                 % (len(got), name))
    return got[0]["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="old", required=True,
                    help="the old name as written INSIDE the files")
    ap.add_argument("--to", dest="new", required=True, help="new chapter name")
    ap.add_argument("--folder",
                    help="the chapter folder to operate on, when it is no longer "
                         "named --from (a half-finished rename: the folder was "
                         "renamed but its contents were not)")
    ap.add_argument("--slug-from", help="old Luma slug (only if the page has MOVED)")
    ap.add_argument("--slug-to", help="new Luma slug (only if the page has MOVED)")
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    a = ap.parse_args()
    if bool(a.slug_from) != bool(a.slug_to):
        ap.error("--slug-from and --slug-to go together.")
    if a.old == a.new and not a.slug_from:
        ap.error("nothing to rename.")

    tx = make_transform(a.old, a.new, a.slug_from, a.slug_to)
    folder_name = a.folder or a.old
    folder_id = find_folder(folder_name)
    print("Chapter folder %r -> %r  (id %s)" % (folder_name, a.new, folder_id))
    if a.slug_from:
        print("Luma slug      aaif-%s -> aaif-%s" % (a.slug_from, a.slug_to))
    else:
        print("Luma slug      UNCHANGED (pass --slug-from/--slug-to only after "
              "the page itself has moved)")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    work = os.path.join("backups", "rename-%s" % stamp)
    if a.write:
        os.makedirs(work, mode=0o700, exist_ok=True)

    renames, edits = [], []
    for relpath, fid, base, isdir in walk(folder_id):
        newbase = tx(base)
        if newbase != base:
            renames.append((relpath, fid, base, newbase))
        if isdir or not base.lower().endswith(OFFICE):
            continue
        local = os.path.join(work if a.write else
                             os.path.join(os.environ.get("TMPDIR", "/tmp"), "rename-scan"),
                             re.sub(r"[^\w.-]", "_", relpath))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        gws_download(fid, local)
        with open(local, "rb") as fh:
            raw = fh.read()
        changed = strings_changed(raw, tx)
        if changed:
            edits.append((relpath, fid, base, local, changed))

    print("\n%d file/folder name(s) to rename:" % len(renames))
    for relpath, _fid, base, newbase in renames:
        print("   %-56s %r -> %r" % (relpath[:56], base, newbase))
    print("\n%d file(s) with text to rewrite:" % len(edits))
    for relpath, _fid, _b, _l, changed in edits:
        print("   %-56s %d string(s)" % (relpath[:56], len(changed)))
        for s in changed[:4]:
            print("        %r" % (s[:88]))
        if len(changed) > 4:
            print("        … and %d more" % (len(changed) - 4))

    print("\nNOT touched here: the Chapters List row, the intake's city cells, and "
          "Slack channels.\n  The feed/intake must be updated for the sync engines "
          "to keep matching this\n  chapter to its folder; a channel rename needs "
          "its own per-channel consent.")

    if not a.write:
        print("\nReport only — nothing was changed. Re-run with --write to apply.")
        return 2 if (renames or edits) else 0

    print("\nRewriting %d file(s) (originals kept under %s/)..." % (len(edits), work))
    for relpath, fid, base, local, _c in edits:
        shutil.copy2(local, local + ".orig")
        n = _rewrite_zip(local, lambda pn, d: rename_part(pn, d, tx, bool(a.slug_from)))
        gws_upload(fid, local, MIME[os.path.splitext(base)[1].lower()])
        print("   rewrote %-52s %d part(s)" % (relpath[:52], n))

    print("\nRenaming %d file/folder(s)..." % len(renames))
    for relpath, fid, base, newbase in renames:
        rename_file(fid, newbase)
        print("   %r -> %r" % (base, newbase))

    if folder_name != a.new:
        rename_file(folder_id, a.new)
        print("\nChapter folder renamed %r -> %r" % (folder_name, a.new))

    print("\nRe-reading from Drive to verify no stale %r remains..." % a.old)
    left = []
    for relpath, fid, base, isdir in walk(folder_id):
        if tx(base) != base:
            left.append("%s (name)" % relpath)
        if isdir or not base.lower().endswith(OFFICE):
            continue
        p = os.path.join(work, "verify-" + re.sub(r"[^\w.-]", "_", relpath))
        gws_download(fid, p)
        with open(p, "rb") as fh:
            if strings_changed(fh.read(), tx):
                left.append("%s (text)" % relpath)
    if left:
        print("VERIFY FAILED — still carrying the old name:")
        for x in left:
            print("   " + x)
        return 1
    print("Verified: no file name or document text still says %r." % a.old)
    return 0


if __name__ == "__main__":
    sys.exit(main())
