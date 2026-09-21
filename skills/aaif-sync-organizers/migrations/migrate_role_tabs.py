#!/usr/bin/env python3
"""One-shot: give every chapter CRM a per-role tab — Organizers, Speakers, Hosts.

Until now a chapter CRM held ONE `Attendees` tab, and `sync_crm.merge_people`
folded a person's rows ACROSS the three intake role tabs into a single row:
someone who applied to organize and to speak became one row reading
"Organizer/Speaker", with their expertise joined from both applications. That
was the only honest thing to do with one tab, and `sync_crm` says so itself when
it refuses a second-role row — "held until per-role CRM tabs exist to keep the
two applications separate rather than merged into one row". This script is that
prerequisite.

Two things the merge cost, which per-role tabs buy back:

  * **A role's own fields.** A speaker has a talk title and an abstract; a host
    has a venue, a capacity and a door policy. One tab could only cram whichever
    applied into `What brings you here?`. Three tabs give each role its own row.
  * **A second application.** Someone already accepted as an organizer who later
    pitches a talk is currently held back and reported, because writing them
    would overwrite the organizer row. With their own Speakers row there is
    nothing to overwrite.

WHAT THIS DOES NOT DO: it does not touch `Attendees`. That tab holds columns no
automation may author — `Signal` above all — plus notes, corrected spellings and
companies that chapter organizers typed by hand, and the Guide tab's dashboard
formulas point at it. It stays exactly as it is, as the merged historical view.
The role tabs are ADDITIVE. Nothing is moved, and nothing is deleted.

HOW THE TABS ARE BUILT: each is a CLONE of the workbook's own `Attendees` sheet
with its data rows cleared. Cloning rather than authoring a sheet from scratch
is the whole safety argument — the clone inherits that workbook's real header
row, its `Status` and `Interested in` dropdowns, its conditional formats, its
column widths and its pre-materialised blank rows, so a role tab cannot drift
from the Attendees tab it was cut from. Authoring one would mean re-deriving all
of that per workbook and getting it right ~100 times.

Rows are cleared through `sync_crm.Attendees.clear`, the same routine the sync
uses, rather than by stripping XML here: a second implementation of "blank a row
but keep its styles" is a second thing to get wrong.

    python3 migrate_role_tabs.py                 # report (default)
    python3 migrate_role_tabs.py --city Boston   # one chapter
    python3 migrate_role_tabs.py --write         # apply, after backing up

Idempotent: a workbook that already carries the three tabs is reported as done
and not rewritten, so a re-run over a migrated estate uploads nothing.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
from xml.etree import ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

import sync_crm as crm  # noqa: E402
from aaif_events.redact import add_redact_flag, set_redaction  # noqa: E402

#: The tabs this migration adds, in intake priority order — the same order
#: `sync_crm.ROLE_TABS` uses, so a reader comparing the two sees one sequence.
ROLE_TABS = ("Organizers", "Speakers", "Hosts")

SOURCE_TAB = "Attendees"

X = crm.X
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORKSHEET_CT = ("application/vnd.openxmlformats-officedocument."
                "spreadsheetml.worksheet+xml")


def next_free_sheet_part(parts):
    """`xl/worksheets/sheetN.xml` for the lowest N nothing already occupies.

    Sheet part numbering is independent of both sheet order and sheetId — the
    legacy CRMs prove it, being packed in a different order than they display —
    so the only safe N is one no part name uses.
    """
    used = set()
    for name in parts:
        m = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", name)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return "xl/worksheets/sheet%d.xml" % n


def next_free_rid(rels_root):
    """An `rIdN` this workbook's rels do not already use."""
    used = {rel.get("Id") for rel in rels_root}
    n = 1
    while ("rId%d" % n) in used:
        n += 1
    return "rId%d" % n


def next_free_sheet_id(wb_root):
    ids = []
    for s in wb_root.iter(X + "sheet"):
        try:
            ids.append(int(s.get("sheetId") or 0))
        except ValueError:
            pass
    return max(ids or [0]) + 1


def existing_tabs(parts):
    wb = ET.fromstring(parts["xl/workbook.xml"])
    return [s.get("name") for s in wb.iter(X + "sheet")]


def blank_clone(parts, source_part):
    """The source sheet's XML with every occupied row blanked.

    Returns bytes. `parts` is not modified: the clearing runs against a private
    copy, because the source tab must come through this migration byte-identical
    apart from what the caller deliberately changes.
    """
    scratch = dict(parts)
    sheet = crm.Attendees(scratch, source_part)
    for rownum in list(sheet.rows):
        if rownum == 1:
            continue                      # the header row is the point of cloning
        if sheet.occupied(rownum):
            sheet.clear(rownum)
    sheet.serialize()
    raw = scratch[source_part]

    # A hyperlink points at a cell we just blanked, so the relationship it
    # carries would dangle. Drop the block rather than clone the sheet's rels.
    root = ET.fromstring(raw)
    for tag in ("hyperlinks", "legacyDrawing", "tableParts"):
        for el in root.findall(X + tag):
            root.remove(el)
    crm.register_namespaces(raw)
    body = ET.tostring(root, encoding="UTF-8")
    at = body.find(b"<worksheet")
    if at < 0:
        raise ValueError("clone of %r did not serialize as a worksheet" % source_part)
    return (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body[at:])


def add_sheet(parts, name, sheet_xml):
    """Register `sheet_xml` as a new worksheet called `name`. Mutates `parts`."""
    part = next_free_sheet_part(parts)
    parts[part] = sheet_xml

    rels_raw = parts["xl/_rels/workbook.xml.rels"]
    ET.register_namespace("", PKG_REL_NS)
    rels = ET.fromstring(rels_raw)
    rid = next_free_rid(rels)
    ET.SubElement(rels, "{%s}Relationship" % PKG_REL_NS, {
        "Id": rid,
        "Type": "%s/worksheet" % R_NS,
        "Target": part[len("xl/"):],
    })
    parts["xl/_rels/workbook.xml.rels"] = ET.tostring(rels, encoding="UTF-8")

    crm.register_namespaces(parts["xl/workbook.xml"])
    wb = ET.fromstring(parts["xl/workbook.xml"])
    sheets = wb.find(X + "sheets")
    if sheets is None:
        raise ValueError("workbook.xml has no <sheets> element")
    ET.SubElement(sheets, X + "sheet", {
        "name": name,
        "sheetId": str(next_free_sheet_id(wb)),
        "{%s}id" % R_NS: rid,
    })
    body = ET.tostring(wb, encoding="UTF-8")
    at = body.find(b"<workbook")
    parts["xl/workbook.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body[at:])

    # [Content_Types].xml must declare the new part or Excel refuses the file.
    ct_raw = parts.get("[Content_Types].xml")
    if ct_raw:
        ET.register_namespace("", CT_NS)
        ct = ET.fromstring(ct_raw)
        have = {o.get("PartName") for o in ct}
        pn = "/" + part
        if pn not in have:
            ET.SubElement(ct, "{%s}Override" % CT_NS,
                          {"PartName": pn, "ContentType": WORKSHEET_CT})
        parts["[Content_Types].xml"] = ET.tostring(ct, encoding="UTF-8")

    # calcChain indexes formula cells by sheet position and goes stale the
    # moment sheets are added. Excel and Sheets both rebuild it on open; a
    # stale one is what makes a workbook "repair itself" on first launch.
    parts.pop("xl/calcChain.xml", None)
    return part


def plan_workbook(parts):
    """(tabs_to_add, reason_skipped). One of the two is always empty."""
    tabs = existing_tabs(parts)
    if SOURCE_TAB not in tabs:
        return [], "no %r tab to clone from" % SOURCE_TAB
    missing = [t for t in ROLE_TABS if t not in tabs]
    if not missing:
        return [], None                    # already migrated — nothing to do
    src = crm.sheet_part(parts, SOURCE_TAB)
    if src is None:
        return [], "%r resolves to no zip part" % SOURCE_TAB
    try:
        crm.Attendees(parts, src)
    except ValueError as exc:
        # The one thing Attendees raises for a sheet that is not a CRM (no
        # sheetData, no header row, a missing column). Anything else is a bug
        # in this script or a corrupt package, and propagates.
        return [], "%s is not a CRM sheet this migration understands (%s)" % (
            SOURCE_TAB, str(exc)[:120])
    return missing, None


def migrate_workbook(parts):
    """Add whatever role tabs are missing. Returns the names added."""
    missing, why = plan_workbook(parts)
    if why or not missing:
        return []
    src = crm.sheet_part(parts, SOURCE_TAB)
    clone = blank_clone(parts, src)
    for name in missing:
        add_sheet(parts, name, clone)
    return missing


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="apply (default: report only)")
    ap.add_argument("--city", help="limit to one chapter folder")
    add_redact_flag(ap, masks="nothing — this migration prints chapter and tab "
                              "names only, never a person")
    a = ap.parse_args()
    set_redaction(a.redact)

    folders = [f for f in crm.list_chapter_folders()
               if not a.city or a.city.lower() in f["name"].lower()]
    if not folders:
        raise SystemExit("! no chapter folder matched %r" % a.city)

    backup_dir = crm.backup_root("crm-role-tabs") if a.write else None
    if backup_dir:
        print("originals -> %s\n" % backup_dir)

    todo, done, skipped, failed = [], [], [], []
    workdir = tempfile.mkdtemp(prefix="aaif-role-tabs-")
    try:
        for f in folders:
            book, why = crm.open_crm(f, workdir)
            if book is None:
                skipped.append((f["name"], why))
                continue
            missing, why = plan_workbook(book.parts)
            if why:
                skipped.append((f["name"], why))
                continue
            if not missing:
                done.append(f["name"])
                continue
            todo.append((f["name"], missing))
            if not a.write:
                continue
            # The pre-edit bytes, before anything in this process touched them.
            shutil.copy2(book.path, os.path.join(
                backup_dir, os.path.basename(book.path)))
            added = migrate_workbook(book.parts)
            raw = crm.save_parts(book.names, book.parts)
            try:
                crm.upload(book.crm["id"], book.path, raw)
            except Exception as exc:       # noqa: BLE001
                failed.append((f["name"], "upload: %s" % str(exc)[:120]))
                continue
            print("  + %-28s %s" % (f["name"], ", ".join(added)))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print("%d chapter(s) already carry the role tabs" % len(done))
    if todo:
        print("%d chapter(s) %s:" % (
            len(todo), "migrated" if a.write else "WOULD be migrated"))
        for name, missing in todo:
            print("    %-28s + %s" % (name, ", ".join(missing)))
    for name, why in skipped:
        print("  skipped %-26s %s" % (name, why))
    for name, why in failed:
        print("  FAILED  %-26s %s" % (name, why))

    if failed:
        return 1
    if skipped:
        # A skipped workbook is a chapter the migration did not reach; a clean
        # exit here would let the estate read as migrated when it is not.
        print("\n%d chapter(s) skipped — not migrated; fix them and re-run."
              % len(skipped))
        return 2
    if todo and not a.write:
        print("\nRe-run with --write to apply. `Attendees` is never touched: the "
              "role tabs are additive and cloned from it, so they carry its "
              "dropdowns, formats and header row.")
        return 2
    if a.write and todo:
        print("\nVerified: each migrated workbook now carries %s."
              % ", ".join(ROLE_TABS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
