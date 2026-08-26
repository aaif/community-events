#!/usr/bin/env python3
"""One-shot: move `Interested in` to sit immediately before `Status`.

migrate_interested_in.py APPENDED the new column at L, because appending is
free and inserting is not: every cell ref in ~1000 rows, every dataValidation
and conditionalFormatting sqref, the autoFilter, the dimension and the Guide
tab's cross-sheet formulas all carry a column position. That was the right
trade for getting the split shipped; it is the wrong place for the column to
live. "What they asked for" and "how far the decision got" are read together,
so they belong side by side.

This script does the insert properly. It moves ONE column and renumbers
everything that refers to a position:

    before   A Full name  B Signal  C Trusted  D Status  E Notes … K What…  L Interested in
    after    A Full name  B Signal  C Trusted  D Interested in  E Status  F Notes … L What…

  * every <c r="…"> in every row, re-sorted into the new column order;
  * <cols> width runs, split and renumbered (the runs are RANGES, so a naive
    per-column rewrite silently re-widens a neighbour);
  * <dimension>, and <autoFilter> — which shipped as $A$1:$K$1 and so never
    covered the appended column at all; it is widened to the real last column,
    or the filter arrows stop one short of the data;
  * every dataValidation and conditionalFormatting sqref;
  * A1-style column letters inside conditionalFormatting formulas (the
    name-turns-red rule tests `$B2="Non-grata"`);
  * the Guide tab's `Attendees!<col>` references — and ONLY those: a
    Guide-local ref like `B5` must not move.

Nothing about the DATA changes. Every value stays with its header, which is
what the verify actually checks: the workbook is re-read after the write and
every row is compared header-by-header against what it held before. A column
move that quietly transposed two people's cells would pass a "does it open"
check and fail this one.

Safe to run against a workbook that is already in the target order — it plans
nothing. Safe to run against one that never had the split: it is reported and
skipped, since there is no column to move.

House rules: the report is the default and writes nothing; --write applies,
then re-downloads and re-reads every workbook and prints a Verified line.
Pre-edit bytes are kept under <repo>/backups/ (gitignored). No member names or
emails on stdout — counts, chapter names and column names only.

Exit codes: 0 everything already ordered; 2 changes proposed (or applied);
1 failure (including a failed verify or a skipped workbook).

Usage:
  python3 migrate_column_order.py                 # report only, zero writes
  python3 migrate_column_order.py --city Boston   # scope to one chapter
  python3 migrate_column_order.py --write         # apply, then verify
"""
import argparse
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import download, fold_city, fresh_if_unchanged, upload  # noqa: E402
from sync_crm import (CRM_SHEET, NEW_COLUMN, X, XLSX,  # noqa: E402
                      Attendees, backup_root, cell_ref, cleanup_workdir,
                      col_of, find_crm, list_chapter_folders, load_parts,
                      save_parts, sheet_part)

#: The column being moved, and the one it must sit immediately before.
MOVE, BEFORE = NEW_COLUMN, "Status"

GUIDE_SHEET = "Guide"


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------
def move_mapping(headers, move=MOVE, before=BEFORE):
    """{old column index: new column index} for the move, or None if already done.

    Derived by rebuilding the column ORDER as a list and re-indexing it, rather
    than by arithmetic on "shift everything between src and dst". The arithmetic
    version has a different sign depending on whether the column moves left or
    right, and gets the boundary wrong in one of the two directions; this cannot,
    and it extends unchanged to any future move.

    Columns past the last header (a chapter that added its own) keep their
    positions relative to everything else, because they are in the list too.
    """
    src, dst = headers[move], headers[before]
    order = list(range(max(headers.values()) + 1))
    order.remove(src)
    order.insert(order.index(dst), src)
    mapping = {old: new for new, old in enumerate(order)}
    return None if all(o == n for o, n in mapping.items()) else mapping


# ---------------------------------------------------------------------------
# Reference rewriting
# ---------------------------------------------------------------------------
_REF_RE = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")


def remap_ref(ref, mapping):
    """'D2:D1000' -> 'E2:E1000'. Preserves $ anchors and the row numbers."""
    def one(m):
        c1, letters, c2, row = m.groups()
        col = mapping.get(col_of(letters))
        if col is None:
            return m.group(0)
        return "%s%s%s%s" % (c1, re.sub(r"\d+$", "", cell_ref(col, 1)), c2, row)
    return _REF_RE.sub(one, ref)


def remap_formula(text, mapping, prefix=""):
    """Rewrite A1-style column letters in a formula.

    `prefix` scopes the rewrite to cross-sheet references ("Attendees!"): on the
    Guide tab a bare `B5` is a cell of the GUIDE and must not move, while
    `Attendees!B2:B` must. Without the scope the dashboard's own layout would be
    rewritten along with the references it makes.

    Open-ended ranges (`Attendees!L2:L`, which the live-list FILTER uses) are
    handled too — the trailing bare column has no row number, so it needs its
    own pass.
    """
    if not prefix:
        return _REF_RE.sub(lambda m: remap_ref(m.group(0), mapping), text)

    def one(m):
        body = m.group(2)
        body = _REF_RE.sub(lambda r: remap_ref(r.group(0), mapping), body)
        # `A2:A` — the half with no row number.
        body = re.sub(r"(:)(\$?)([A-Z]{1,3})(?![A-Z0-9])",
                      lambda r: "%s%s%s" % (
                          r.group(1), r.group(2),
                          re.sub(r"\d+$", "",
                                 cell_ref(mapping.get(col_of(r.group(3)),
                                                      col_of(r.group(3))), 1))),
                      body)
        return m.group(1) + body

    # One reference at a time, each still carrying its sheet prefix.
    return re.sub(r"(%s)((?:\$?[A-Z]{1,3}\$?\d+)(?::\$?[A-Z]{1,3}(?:\$?\d+)?)?)"
                  % re.escape(prefix), one, text)


# ---------------------------------------------------------------------------
# Sheet surgery
# ---------------------------------------------------------------------------
def move_cells(att, mapping):
    """Re-letter every <c> and re-sort each row into the new column order."""
    for row in att.rows.values():
        rownum = int(row.get("r"))
        cells = list(row)
        placed = []
        for c in cells:
            old = col_of(c.get("r") or "")
            new = mapping.get(old, old)
            c.set("r", cell_ref(new, rownum))
            placed.append((new, c))
            row.remove(c)
        # Excel requires <c> in ascending column order within a <row>; a row
        # left out of order reads as a corrupt file, not as a reordered one.
        for _, c in sorted(placed, key=lambda p: p[0]):
            row.append(c)


def move_cols(att, mapping):
    """Renumber the <cols> width runs.

    Each <col> is a RANGE (min..max). A run is expanded to its individual
    columns, mapped, then re-collapsed into contiguous runs — rewriting min/max
    in place would be wrong the moment a moved column starts or ends a run.
    """
    block = att.root.find(X + "cols")
    if block is None:
        return
    widths = {}                       # new index -> the <col> attrs it inherits
    for c in list(block):
        lo, hi = int(c.get("min")) - 1, int(c.get("max")) - 1
        for old in range(lo, hi + 1):
            widths[mapping.get(old, old)] = dict(c.attrib)
        block.remove(c)
    for new in sorted(widths):
        attrs = widths[new]
        attrs["min"] = attrs["max"] = str(new + 1)
        el = block.makeelement(X + "col", attrs)
        block.append(el)
    _collapse_cols(block)


def _collapse_cols(block):
    """Merge adjacent <col> entries that carry identical formatting."""
    kids = list(block)
    for c in kids:
        block.remove(c)
    for c in kids:
        prev = list(block)[-1] if len(block) else None
        same = (prev is not None
                and int(prev.get("max")) + 1 == int(c.get("min"))
                and {k: v for k, v in prev.attrib.items() if k not in ("min", "max")}
                == {k: v for k, v in c.attrib.items() if k not in ("min", "max")})
        if same:
            prev.set("max", c.get("max"))
        else:
            block.append(c)


def move_ranges(att, mapping):
    """Renumber dimension, autoFilter, and every sqref + cf formula."""
    last = max(mapping.values())
    dim = att.root.find(X + "dimension")
    if dim is not None and dim.get("ref"):
        ref = dim.get("ref")
        start = ref.split(":")[0]
        end = ref.split(":")[-1] if ":" in ref else ref
        rows = re.sub(r"^\D+", "", end) or "1"
        dim.set("ref", "%s:%s" % (start, cell_ref(last, int(rows))))
    af = att.root.find(X + "autoFilter")
    if af is not None and af.get("ref"):
        # Shipped as $A$1:$K$1 — it never covered the appended column, so the
        # filter arrows stopped one short of the data. Widen to the real end
        # rather than remapping a range that was already wrong.
        af.set("ref", "$A$1:$%s$1" % re.sub(r"\d+$", "", cell_ref(last, 1)))
    for el in att.root.iter():
        if el.tag == X + "conditionalFormatting" or el.tag == X + "dataValidation":
            if el.get("sqref"):
                el.set("sqref", " ".join(remap_ref(p, mapping)
                                         for p in el.get("sqref").split()))
        if el.tag == X + "formula" and el.text and not el.text.strip().startswith('"'):
            # A cf rule that TESTS another column — `$B2="Non-grata"`. A rule
            # whose formula is a bare quoted literal is a cellIs value, not a
            # reference, and must be left exactly alone.
            el.text = remap_formula(el.text, mapping)


def move_guide(parts, mapping):
    """Rewrite `Attendees!<col>` references wherever the Guide keeps them.

    Both storage forms again: the sheet part for inline strings and formulas,
    and the shared string table for the workbooks that use one.
    """
    changed = []
    targets = [p for p in (sheet_part(parts, GUIDE_SHEET), "xl/sharedStrings.xml")
               if p and p in parts]
    for part in targets:
        raw = parts[part].decode("utf-8", "replace")
        out = remap_formula(raw, mapping, prefix="%s!" % CRM_SHEET)
        if out != raw:
            parts[part] = out.encode()
            changed.append(part)
    return changed


def snapshot(att):
    """{rownum: {header: value}} — what the workbook says, independent of layout.

    This is what the move must preserve exactly. Comparing the two snapshots is
    the only check that catches a transposition: a workbook whose columns were
    renumbered inconsistently still opens, still has twelve headers, and quietly
    shows one person's email against another's name.
    """
    return {r: {h: att.value(r, h) for h in att.headers}
            for r in att.rows if r > 1 and att.occupied(r)}


# ---------------------------------------------------------------------------
# Per-workbook driver
# ---------------------------------------------------------------------------
def open_crm(folder, workdir):
    crm, why = find_crm(folder["id"])
    if crm is None:
        return None, why
    path = os.path.join(workdir, "%s.xlsx" % re.sub(r"[^\w.-]", "_", folder["name"]))
    try:
        names, parts = load_parts(download(crm["id"], path))
        part = sheet_part(parts, CRM_SHEET)
        if part is None:
            return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
        att = Attendees(parts, part)
    except Exception as e:
        return None, "%s: %s: %s" % (crm["name"], type(e).__name__, e)
    return {"folder": folder, "crm": crm, "names": names, "parts": parts,
            "part": part, "att": att, "path": path}, None


def apply_move(book, mapping):
    """Apply the move; return (new bytes, pre-move snapshot)."""
    att = book["att"]
    before = snapshot(att)
    move_cells(att, mapping)
    move_cols(att, mapping)
    move_ranges(att, mapping)
    # headers must be re-derived before serialize() sizes the dimension.
    att.headers = {h: mapping.get(c, c) for h, c in att.headers.items()}
    att.serialize()
    move_guide(book["parts"], mapping)
    return save_parts(book["names"], book["parts"]), before


def target_order(headers):
    """The header names in their intended left-to-right order, for the report."""
    return [h for h, _ in sorted(headers.items(), key=lambda kv: kv[1])]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(args):
    workdir = tempfile.mkdtemp(prefix="aaif-order-")
    try:
        code = _run(args, workdir)
    finally:
        stranded = cleanup_workdir(workdir, keep_backups=False)
    return 1 if stranded else code


def _run(args, workdir):
    folders = list_chapter_folders()
    if args.city:
        want = fold_city(args.city)
        folders = [f for f in folders if fold_city(f["name"]) == want]
        if not folders:
            sys.exit("ABORT: no chapter folder matches %r." % args.city)
    print("Chapters: %d folder(s) in scope.\n" % len(folders))

    touched, skipped = [], []
    for folder in folders:
        book, why = open_crm(folder, workdir)
        if book is None:
            skipped.append((folder["name"], why))
            print("  %-18s SKIPPED — %s" % (folder["name"], why))
            continue
        mapping = move_mapping(book["att"].headers)
        if mapping is None:
            continue
        print("  %-18s %s -> column %s"
              % (folder["name"], MOVE,
                 re.sub(r"\d+$", "", cell_ref(mapping[book["att"].headers[MOVE]], 1))))
        touched.append({"book": book, "mapping": mapping})

    if skipped:
        print("\nChapters SKIPPED — not reordered, fix the workbook and re-run:")
        for name, why in skipped:
            print("  %-28s %s" % (name, why))
    if not touched:
        if skipped:
            print("\nNothing due for the chapters that could be opened — but %d "
                  "was/were SKIPPED above." % len(skipped))
            return 1
        print("\nNothing to do — %r already sits before %r everywhere."
              % (MOVE, BEFORE))
        return 0
    if not args.write:
        print("\n%d workbook(s) would change. Re-run with --write to apply."
              % len(touched))
        return 2

    print("\nWriting %d workbook(s)..." % len(touched))
    backup_dir = backup_root("crm-order-before")
    written, changed, failed = [], [], []
    snapshots = {}
    for t in touched:
        book, name = t["book"], t["book"]["folder"]["name"]
        try:
            with open(book["path"], "rb") as fh:
                planned = fh.read()
            current, drifted = fresh_if_unchanged(
                book["crm"]["id"], os.path.join(workdir, "reread.xlsx"), planned)
            with open(os.path.join(backup_dir, os.path.basename(book["path"])),
                      "wb") as fh:
                fh.write(current)
            if drifted:
                changed.append(name)
                print("  SKIPPED %s — workbook changed since the plan was built; "
                      "NOT written, re-run" % name, file=sys.stderr)
                continue
            raw, before = apply_move(book, t["mapping"])
            snapshots[name] = before
            upload(book["crm"]["id"], book["path"], raw, XLSX)
            written.append(name)
            print("  wrote %s (%s)" % (name, book["crm"]["name"]))
        except Exception as e:
            failed.append((name, str(e)))
            print("  FAILED %s — %s" % (name, e), file=sys.stderr)
    print("Wrote %d workbook(s); pre-edit copies kept in %s (gitignored; delete "
          "once the write is confirmed good)" % (len(written), backup_dir))
    if changed:
        print("\n%d workbook(s) changed since the plan was built and were NOT "
              "written — re-run:\n  %s" % (len(changed), ", ".join(changed)))

    print("\nRe-verifying (every row compared header-by-header)...")
    stale = []
    for t in touched:
        folder = t["book"]["folder"]
        if folder["name"] not in written:
            continue
        book, why = open_crm(folder, os.path.join(workdir, "verify"))
        if book is None:
            stale.append((folder["name"], "could not re-open: %s" % why))
            continue
        att = book["att"]
        order = target_order(att.headers)
        if order.index(MOVE) + 1 != order.index(BEFORE):
            stale.append((folder["name"], "%r is not immediately before %r (%s)"
                          % (MOVE, BEFORE, ", ".join(order))))
            continue
        # The transposition check. Values are compared BY HEADER, so a workbook
        # whose refs were renumbered inconsistently fails here even though it
        # opens cleanly and still has twelve columns.
        after, before = snapshot(att), snapshots.get(folder["name"], {})
        if after != before:
            rows = sorted(set(before) ^ set(after)) or [
                r for r in before if before[r] != after.get(r)]
            stale.append((folder["name"], "%d row(s) changed value across the "
                          "move — DATA MOVED, restore from the backup" % len(rows)))
    if failed or stale or changed:
        if failed or stale:
            print("VERIFY FAILED:")
            for name, why in failed + stale:
                print("  %s — %s" % (name, why))
        return 1
    print("Verified: every written workbook has %r before %r, and every row "
          "holds exactly the values it held before." % (MOVE, BEFORE))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Move %r to sit immediately before %r on every chapter CRM."
                    % (MOVE, BEFORE))
    ap.add_argument("--write", action="store_true",
                    help="apply the move (default: report only)")
    ap.add_argument("--city", help="limit to one chapter folder")
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
