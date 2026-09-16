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

Native Google Docs/Sheets/Slides are RENAMED but their contents are never read —
they are not zips. They are listed in the report so nobody mistakes the verify
line for a claim about them. Member data (the chapter CRM, the event tracker) is
reported and skipped unless `--include-member-data` is passed, matching
create_chapter's refusal to rebrand those files.

**The Luma slug is NOT renamed unless you ask.** It is a separate identity that
lives on luma.com, and the page does not move just because a chapter was
renamed — a renamed chapter routinely keeps serving from its original slug.
Rewriting those links to match the new name would point every organizer at a
404, the opposite of the bug this fixes. Pass `--slug-from/--slug-to` only once
the page itself has actually been moved. (Observed during the 2026-09 capital-
city cleanup: two of the three renamed chapters still served from their old
slug; check the live page rather than assuming either way.)

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
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from create_chapter import (CHAPTERS_PARENT, _process_paragraphs,  # noqa: E402
                            _rewrite_zip, gws_download, gws_upload,
                            is_member_data, list_children, rename_file)

#: A Luma slug token, INCLUDING its hyphenated tail. Matched so the
#: city-name pass can be kept out of it. `[a-z0-9]+` alone stopped at the
#: first hyphen, so only `AAIF-SALT` was shielded in `AAIF-SALT-LAKE-CITY`
#: and renaming a city called `City` rewrote the slug's own tail — the
#: exact 404 this guard exists to prevent.
SLUG_TOKEN = re.compile(r"(?i)\baaif-[a-z0-9]+(?:-[a-z0-9]+)*")

# --- stdout redaction -------------------------------------------------------
# This script prints CRM cell text (see strings_changed), so it needs the same
# masking every sibling engine has: a CI log on this public repo is a
# publication. Its own copy of the flag and helpers, because a helper imported
# from a sibling reads THAT module's REDACT, not this one's.
REDACT = False
CI_REDACT_DEFAULT = os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")


def redact_text(t):
    """Mask a document string. Under --redact only its shape survives — the
    strings this prints are document text, and a chapter CRM's are member rows."""
    if not REDACT or not t:
        return repr(t)
    return "<%d chars>" % len(t)


def add_redact_flag(ap):
    ap.add_argument("--redact", action=argparse.BooleanOptionalAction,
                    default=CI_REDACT_DEFAULT,
                    help="mask document text on stdout; default on when CI is set")


def set_redaction(on):
    global REDACT
    REDACT = bool(on)
    if REDACT:
        print("redaction ON (CI set; pass --no-redact to disable)"
              if CI_REDACT_DEFAULT else "redaction ON (--redact)", file=sys.stderr)


#: Native Google formats have no bytes this script can rewrite — Drive stores
#: them as a document id, not a zip — so they are renamed and REPORTED, never
#: silently counted as verified.
NATIVE_HINT = (".gdoc", ".gsheet", ".gslides")


def is_native(name, relpath=""):
    """True when a child is (or looks like) a native Google file.

    Drive's file list gives a mimeType, but `walk()` keeps only names; anything
    without an Office extension that also has no extension at all is a native
    doc in this tree, which is how the template's About/Tracker appear once a
    chapter converts them.
    """
    n = name.strip().lower()
    return n.endswith(NATIVE_HINT) or "." not in n


#: Parts whose bytes could not be decoded during this run. A verify that
#: cannot read a part cannot vouch for it.
UNDECODABLE = set()

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


def rename_part(part_name, data, tx, slug_tx=None):
    """Rebranded bytes for one OOXML part, or the original when unchanged.

    Mirrors create_chapter.rebrand_part's part routing on purpose: the same
    paragraph-aware rewrite, so text split across runs is handled and formatting
    survives.

    `.rels` gets `slug_tx` — the slug substitution ALONE — and never the city
    name pass, which is what create_chapter does too. Running the full transform
    there rewrote relationship TARGETS: a `Target="../embeddings/Utah_Data.xlsx"`
    became `Salt Lake City_Data.xlsx` while the zip member kept its old name, so
    the relationship dangled and the document opened as corrupt. `testzip()`
    checks CRCs, not relationships, so nothing downstream noticed.
    """
    try:
        xml = data.decode("utf-8")
    except UnicodeDecodeError:
        # Not silent: a part that cannot be decoded is a part we did not
        # inspect, and the caller records it so a run can never claim to have
        # verified a document whose text it never read.
        if part_name.endswith((".xml", ".rels")):
            UNDECODABLE.add(part_name)
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
    elif part_name.endswith(".rels") and slug_tx:
        xml = slug_tx(xml)
    else:
        return data
    # A rewritten part must still parse. The repack validates CRCs, not XML, so
    # without this a transform that produced malformed markup would upload
    # cleanly and only fail when a human opened the file.
    #
    # Only checked for a part carrying an XML declaration, which every real
    # OOXML part does. A bare fragment has no namespace declarations, so
    # ET refuses it for "unbound prefix" whatever the transform did — the check
    # would fail on correct input and say nothing about correctness.
    if xml.lstrip().startswith("<?xml"):
        try:
            ET.fromstring(xml)
        except ET.ParseError as e:
            raise RuntimeError("rewriting %s produced malformed XML: %s"
                               % (part_name, e))
    return xml.encode("utf-8")


def parts_changed(raw, tx, slug_tx=None):
    """Names of the parts `rename_part` would actually rewrite in this file.

    THE selection test, and also the verification test — deliberately the same
    function, because the two disagreeing is what made the first version of this
    script claim success over files it never opened. `strings_changed` (below)
    only ever looked at `<w:t>/<a:t>/<t>` in members ending `.xml`, while
    rename_part also rewrites `docProps/*` and `.rels`; a document whose only
    stale text was its metadata title, or whose Luma link lived only as a
    relationship target, was never queued — and the verify, using that same
    blind predicate, printed "Verified" over it.

    Asking rename_part itself removes the possibility of that class of bug: a
    part is "changed" iff the code that does the changing would change it.
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            data = z.read(n)
            if rename_part(n, data, tx, slug_tx) != data:
                out.append(n)
    return out


def strings_changed(raw, tx):
    """The distinct pieces of VISIBLE text this rename would alter.

    A preview for a human, never the selection test — use `parts_changed` for
    that. It is shown because a chapter's CRM is member data: the transform is
    aimed at a title cell, and a row whose own text happens to contain the old
    city name would be rewritten too. That is a judgement a human makes, so the
    strings are shown rather than silently applied.

    Visible text only, so it says nothing about `docProps` or `.rels`; a file
    can therefore be queued for rewriting with nothing to preview, which the
    report prints as a part-name list instead.
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
    ap.add_argument("--include-member-data", action="store_true",
                    help="also rewrite the chapter CRM and event tracker, which "
                         "hold attendee data (default: report them and skip)")
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)
    if bool(a.slug_from) != bool(a.slug_to):
        ap.error("--slug-from and --slug-to go together.")
    if a.old == a.new and not a.slug_from:
        ap.error("nothing to rename.")
    # A substring relationship makes the transform non-idempotent: "York" ->
    # "New York" turns an ALREADY-renamed "New York" into "New New York". The
    # verify re-applies the transform, so a fully successful run reports
    # VERIFY FAILED, and the natural response — re-running — corrupts the real
    # documents. Refuse rather than half-support it.
    if a.old != a.new and (a.old in a.new or a.new in a.old):
        ap.error("%r and %r contain one another, so the rename cannot be applied "
                 "twice safely (a re-run would double-apply it). Rename via an "
                 "intermediate name, or rename by hand." % (a.old, a.new))

    tx = make_transform(a.old, a.new, a.slug_from, a.slug_to)
    slug_tx = (make_transform(a.old, a.old, a.slug_from, a.slug_to)
               if a.slug_from else None)
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

    renames, edits, skipped, native = [], [], [], []
    scan = work if a.write else tempfile.mkdtemp(prefix="rename-scan-")
    os.makedirs(scan, mode=0o700, exist_ok=True)
    try:
        for relpath, fid, base, isdir in walk(folder_id):
            newbase = tx(base)
            if newbase != base:
                renames.append((relpath, fid, base, newbase))
            if isdir:
                continue
            if not base.lower().endswith(OFFICE):
                # A native Google Doc/Sheet/Slides is renamed above but its
                # CONTENTS are never read or rewritten by this script. Saying so
                # is the difference between a verified rename and one that just
                # did not look.
                if is_native(base, relpath):
                    native.append(relpath)
                continue
            if is_member_data(base) and not a.include_member_data:
                skipped.append(relpath)
                continue
            # Real subdirectories: flattening `/` to `_` let `Assets/Map.pptx`
            # and a top-level `Assets_Map.pptx` collide on one local path, so
            # the second download overwrote the first and the write loop
            # uploaded the wrong bytes to the first file's id.
            local = os.path.join(scan, *[re.sub(r"[^\w.-]", "_", x)
                                         for x in relpath.split("/")])
            os.makedirs(os.path.dirname(local), exist_ok=True)
            gws_download(fid, local)
            with open(local, "rb") as fh:
                raw = fh.read()
            parts = parts_changed(raw, tx, slug_tx)
            if parts:
                edits.append((relpath, fid, base, local, strings_changed(raw, tx), parts))

        print("\n%d file/folder name(s) to rename:" % len(renames))
        for relpath, _fid, base, newbase in renames:
            print("   %-56s %r -> %r" % (relpath[:56], base, newbase))
        print("\n%d file(s) to rewrite:" % len(edits))
        for relpath, _fid, _b, _l, changed, parts in edits:
            print("   %-56s %d part(s), %d string(s)"
                  % (relpath[:56], len(parts), len(changed)))
            for x in changed[:4]:
                print("        %s" % redact_text(x[:88]))
            if len(changed) > 4:
                print("        … and %d more" % (len(changed) - 4))
            if not changed:
                print("        (no visible text — %s)" % ", ".join(parts[:3]))
        if skipped:
            print("\n%d member-data file(s) NOT rewritten (pass "
                  "--include-member-data to include them):" % len(skipped))
            for x in skipped:
                print("   " + x)
        if native:
            print("\n%d native Google file(s) — renamed if their NAME carries the "
                  "old city, but their contents are never read by this script. "
                  "Check them by hand:" % len(native))
            for x in native:
                print("   " + x)

        print("\nNOT touched here: the Chapters List row, the intake's city cells, "
              "and Slack channels.\n  The feed/intake must be updated for the sync "
              "engines to keep matching this\n  chapter to its folder; a channel "
              "rename needs its own per-channel consent.")

        if not a.write:
            print("\nReport only — nothing was changed. Re-run with --write to apply.")
            return 2 if (renames or edits) else 0

        print("\nRewriting %d file(s) (originals kept under %s/)..." % (len(edits), work))
        done = []
        try:
            for relpath, fid, base, local, changed, parts in edits:
                shutil.copy2(local, local + ".orig")
                n = _rewrite_zip(local, lambda pn, d: rename_part(pn, d, tx, slug_tx))
                gws_upload(fid, local, MIME[os.path.splitext(base)[1].lower()])
                done.append(relpath)
                print("   rewrote %-52s %d part(s)" % (relpath[:52], n))
        except Exception as e:
            # The sibling engines all say explicitly what landed before a
            # failure; a bare traceback here reads as "nothing happened" while
            # half the chapter has been renamed.
            sys.exit("PARTIAL RENAME — %d of %d file(s) were uploaded before this "
                     "failed: %s\nNo file or folder NAMES were changed, and nothing "
                     "was verified. Originals are under %s/ (*.orig).\nRe-running is "
                     "safe: the transform is refused unless it is idempotent."
                     % (len(done), len(edits), e, work))

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
            if is_member_data(base) and not a.include_member_data:
                continue
            vp = os.path.join(scan, "verify", *[re.sub(r"[^\w.-]", "_", x)
                                                for x in relpath.split("/")])
            os.makedirs(os.path.dirname(vp), exist_ok=True)
            gws_download(fid, vp)
            with open(vp, "rb") as fh:
                if parts_changed(fh.read(), tx, slug_tx):
                    left.append("%s (content)" % relpath)
        if UNDECODABLE:
            # A part we could not read is a part we cannot vouch for, and the
            # verifier shares the rewriter's decoder — so without this a
            # never-rewritten part would pass verification by being invisible
            # to both.
            left += ["%s (could not decode — not inspected)" % x
                     for x in sorted(UNDECODABLE)]
        if left:
            print("VERIFY FAILED — still carrying the old name, or not inspected:")
            for x in left:
                print("   " + x)
            return 1
        print("Verified: no file name, and no text in any %s, still says %r%s."
              % ("/".join(OFFICE), a.old,
                 " (native Google files and member data were not inspected)"
                 if (native or skipped) else ""))
        return 0
    finally:
        # Member data on disk, cleaned up. The report path used to leave every
        # chapter workbook in a predictable world-readable /tmp directory.
        if not a.write:
            shutil.rmtree(scan, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
