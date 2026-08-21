#!/usr/bin/env python3
"""One-shot: open the resource columns on the Chapters List and seed them.

This is a **structural migration of a live, world-readable sheet**, not a sync.
It runs once. After it has run and been verified, `sync_resources.py` maintains
the columns and this script has no further job — keep it as the record of how the
layout changed, but do not re-run it (it refuses to run twice; see below).

What it does, in one `spreadsheets.batchUpdate`:

1. `insertDimension` the RESOURCE_COLUMNS block after `Country` (the original
   2026-08-10 run inserted four; `Organizer Handles` joined the block later via
   plan_missing_columns(), so a fresh run inserts all five), so the map sits next
   to the city it describes: `Chapter Folder`, `Slack Channel`,
   `Organizer Channel`, `Country Channel`.
2. Writes the header cells.
3. Backfills the three channel columns from `aaif-audit-slack`'s
   `channel_map.json` — `public` -> Slack Channel, `organizers` -> Organizer
   Channel, `regional` -> Country Channel. Those three tables are exactly these
   three columns, which is why the migration is a move rather than a reshape.

`Chapter Folder` is deliberately **not** backfilled here. It is derived from
Drive, it is verifiable, and `sync_resources.py` proposes it under the usual
report/approve gate. Seeding it from a second source would just be a slower way
of getting the same answer with no one having checked it.

## Why the insert is safe here, and what it would break elsewhere

Everything from the old column D onward shifts right by the block width. That is safe for
this repo because all four in-repo readers (`sync_chapters`, `sync_access`,
`clean.py`, `audit_organizers`) read `A:AZ` and resolve every column by header
name. Verified before writing: the spreadsheet has no named ranges, no protected
ranges, no conditional formats, no charts, no formulas in either tab, and no
bound Apps Script. `Past Events` is keyed on City with its own columns and does
not reference this tab.

What this cannot verify is a consumer outside Drive — a Sanity import, a saved
query, anything reading the feed by column position. Those do not move when
Sheets shifts a column. Confirm before running.

## The map being migrated is UNCONFIRMED

`channel_map.json` carries a `_provenance` block saying its entries were inferred
by an agent from channel names during the first audit and never checked with
anyone who runs these chapters. Moving them into the sheet does not make them
true — it makes them *visible to the organizers who can correct them*, which is
the actual argument for the move. The report prints the count so nobody mistakes
a seeded cell for a confirmed one.

Usage:
    python3 migrate_resource_columns.py            # report only
    python3 migrate_resource_columns.py --write    # apply, then verify
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync_chapters import (CHAPTERS_ID, CHAPTERS_TAB, NO_RESOURCE,  # noqa: E402
                           RESOURCE_COLUMNS, cell, col_letter, fold_city,
                           get_values, gws_json)

#: The column the new block is inserted *after*. Anchored on a header name, not
#: an index, so a future layout change makes this abort instead of quietly
#: slicing the block into the middle of something else.
ANCHOR_COLUMN = "Country"

#: Where the matching vocabularies land. Must equal audit_organizers.SLACK_CONFIG_TAB.
CONFIG_TAB = "Slack Config"

#: channel_map.json table -> the sheet column it becomes. The audit's three
#: per-city tables and these three columns are the same data; `public_prefixes`,
#: `organizer_suffixes` and `staff_email_domain` stay in the JSON because they
#: are matching *config*, not a per-chapter map.
TABLE_TO_COLUMN = {
    "public": "Slack Channel",
    "organizers": "Organizer Channel",
    "regional": "Country Channel",
}

CHANNEL_MAP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "aaif-audit-slack", "scripts", "channel_map.json")


def read_grid():
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    return rows, [h.strip() for h in rows[0]]


def sheet_id():
    """Numeric sheetId for the tab — insertDimension addresses grids, not names."""
    meta = gws_json("sheets", "spreadsheets", "get",
                    params={"spreadsheetId": CHAPTERS_ID,
                            "fields": "sheets(properties(sheetId,title))"})
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == CHAPTERS_TAB:
            return s["properties"]["sheetId"]
    sys.exit("ABORT: no tab named %r in the spreadsheet." % CHAPTERS_TAB)


def load_channel_map(path):
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        sys.exit("ABORT: no channel map at %s — nothing to seed from." % path)
    except json.JSONDecodeError as exc:
        sys.exit("ABORT: %s is not valid JSON (%s)." % (path, exc))
    missing = [t for t in TABLE_TO_COLUMN if t not in cfg]
    if missing:
        sys.exit("ABORT: %s is missing table(s): %s." % (path, ", ".join(missing)))
    return cfg


def plan(rows, headers, cfg):
    """Return (anchor_index, seeds, unmatched_keys).

    `seeds` is [{row, city, values: {column -> value}}]; only cities present on
    the sheet get one. `unmatched_keys` are channel_map cities with no row —
    reported, never invented, because a key that matches nothing is either a
    stale chapter or a spelling that never fired in the audit either.
    """
    if all(c in headers for c in RESOURCE_COLUMNS):
        sys.exit("ABORT: every resource column is already on the sheet. This "
                 "migration is one-shot; use sync_resources.py to maintain them.")
    if ANCHOR_COLUMN not in headers:
        sys.exit("ABORT: no %r column to anchor the insert after — the layout "
                 "changed. Re-read the sheet before editing this script."
                 % ANCHOR_COLUMN)
    if "City" not in headers:
        sys.exit("ABORT: no 'City' column; cannot key the seed values.")

    anchor = headers.index(ANCHOR_COLUMN)
    i_city = headers.index("City")

    # City -> row, folded the same way every other engine folds, so 'Washington,
    # DC' and 'Washington DC' are one city here too.
    by_city = {}
    for rownum, row in enumerate(rows[1:], start=2):
        city = cell(row, i_city)
        if city:
            by_city.setdefault(fold_city(city), (rownum, city))

    seeds, unmatched = {}, []
    for table, column in TABLE_TO_COLUMN.items():
        for city, channel in cfg[table].items():
            hit = by_city.get(fold_city(city))
            if not hit:
                unmatched.append("%s -> %s (%s)" % (city, channel, table))
                continue
            rownum, sheet_city = hit
            # JSON null means "confirmed: there is no channel". A spreadsheet has
            # no null, so it becomes the NO_RESOURCE sentinel — dropping it would
            # turn a settled answer back into an open question every run.
            seeds.setdefault(rownum, {"row": rownum, "city": sheet_city,
                                      "values": {}})
            seeds[rownum]["values"][column] = (
                NO_RESOURCE if channel is None else channel)

    return anchor, [seeds[r] for r in sorted(seeds)], sorted(unmatched)


def plan_missing_columns(headers):
    """Resource columns absent from the sheet, and where each one goes.

    Returns [(insert_at, name)] in application order. Separate from plan() on
    purpose: that one is welded to seeding from channel_map.json, which no longer
    exists, so it can never run again. This path is idempotent and is how a
    resource column added later (Organizer Handles was) reaches the sheet.

    New columns land after the last resource column already present, keeping the
    block contiguous — the whole reason it was inserted next to City in the first
    place. Falls back to the anchor when none is present yet.
    """
    if ANCHOR_COLUMN not in headers:
        sys.exit("ABORT: no %r column to anchor the block after." % ANCHOR_COLUMN)
    present = [c for c in RESOURCE_COLUMNS if c in headers]
    at = (max(headers.index(c) for c in present) if present
          else headers.index(ANCHOR_COLUMN))
    out = []
    for name in RESOURCE_COLUMNS:
        if name in headers:
            continue
        at += 1
        out.append((at, name))
    return out


def add_missing_columns(plan_cols, gid):
    """Insert each missing column and write its header, one batch, left to right.

    Applied in ascending index order so each insert's position is still correct
    after the previous one — the indices come from plan_missing_columns() already
    accounting for the columns inserted before them.
    """
    requests = []
    for at, name in plan_cols:
        requests.append({"insertDimension": {
            "range": {"sheetId": gid, "dimension": "COLUMNS",
                      "startIndex": at, "endIndex": at + 1},
            "inheritFromBefore": False}})
        requests.append({"updateCells": {
            "start": {"sheetId": gid, "rowIndex": 0, "columnIndex": at},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": name}}]}],
            "fields": "userEnteredValue"}})
    gws_json("sheets", "spreadsheets", "batchUpdate",
             params={"spreadsheetId": CHAPTERS_ID},
             body={"requests": requests})

    _, headers = read_grid()
    missing = [n for _, n in plan_cols if n not in headers]
    if missing:
        sys.exit("VERIFY FAILED: %s absent after the insert." % ", ".join(missing))
    print("Added %d column(s): %s"
          % (len(plan_cols), ", ".join("%s at %s" % (n, col_letter(a))
                                       for a, n in plan_cols)))


def report(anchor, headers, seeds, unmatched):
    print("Chapters List resource-column migration (report only)\n")
    shifted = [h for h in headers[anchor + 1:] if h]
    print("Insert %d columns after %r (column %s):"
          % (len(RESOURCE_COLUMNS), ANCHOR_COLUMN, col_letter(anchor)))
    for i, name in enumerate(RESOURCE_COLUMNS):
        print("    %s  %s" % (col_letter(anchor + 1 + i), name))
    if shifted:
        print("  shifting right by %d: %s"
              % (len(RESOURCE_COLUMNS), ", ".join(shifted)))
        print("  (%s -> %s ... %s -> %s)"
              % (col_letter(anchor + 1), col_letter(anchor + 1 + len(RESOURCE_COLUMNS)),
                 col_letter(anchor + len(shifted)),
                 col_letter(anchor + len(shifted) + len(RESOURCE_COLUMNS))))

    print("\nSeeded from channel_map.json (%d chapters):" % len(seeds))
    for s in seeds:
        pairs = ", ".join("%s=%s" % (k.replace(" Channel", ""), v)
                          for k, v in sorted(s["values"].items()))
        print("  row %-3d %-18s %s" % (s["row"], s["city"], pairs))

    if unmatched:
        print("\nchannel_map entries with NO row on the sheet (%d) — not written:"
              % len(unmatched))
        for u in unmatched:
            print("  %s" % u)

    print("\n  'Chapter Folder' is left blank for sync_resources.py to derive from Drive.")
    print("  Every seeded value is UNCONFIRMED (see channel_map.json _provenance)"
          " — it was\n  inferred from channel names, never checked with an organizer.")


def apply(anchor, seeds, gid):
    """Insert the columns, write the headers, and seed — one batchUpdate.

    One request list, so a failure cannot leave the sheet with four unnamed
    columns and no values in them. The value writes are expressed as
    `updateCells` inside the same batch rather than a separate
    `values.batchUpdate`, because the A1 ranges they would need do not exist
    until the insert in this very batch has happened.
    """
    requests = [{
        "insertDimension": {
            "range": {"sheetId": gid, "dimension": "COLUMNS",
                      "startIndex": anchor + 1,
                      "endIndex": anchor + 1 + len(RESOURCE_COLUMNS)},
            "inheritFromBefore": False,
        }
    }, {
        "updateCells": {
            "start": {"sheetId": gid, "rowIndex": 0, "columnIndex": anchor + 1},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": h}}
                                 for h in RESOURCE_COLUMNS]}],
            "fields": "userEnteredValue",
        }
    }]

    for s in seeds:
        # One request per contiguous run would be tighter, but the block is four
        # columns wide and the runs are sparse; writing the whole block per row
        # with explicit blanks is simpler and cannot smear a value sideways.
        #
        # An unseeded column is an explicit empty string, NOT a bare `{}`. gws
        # drops empty JSON objects on the way to the API, and a dropped element
        # here would not leave a gap — it would shift every later value one
        # column LEFT, filing Pune's organizer channel under its country. The
        # columns are brand new and empty, so writing "" into them costs nothing.
        values = [{"userEnteredValue":
                   {"stringValue": s["values"].get(c, "")}}
                  for c in RESOURCE_COLUMNS]
        requests.append({"updateCells": {
            "start": {"sheetId": gid, "rowIndex": s["row"] - 1,
                      "columnIndex": anchor + 1},
            "rows": [{"values": values}],
            "fields": "userEnteredValue",
        }})

    gws_json("sheets", "spreadsheets", "batchUpdate",
             params={"spreadsheetId": CHAPTERS_ID},
             body={"requests": requests})
    return len(requests)


def verify(seeds):
    """Re-read and confirm the headers landed and every seed is in its cell."""
    rows, headers = read_grid()
    missing = [c for c in RESOURCE_COLUMNS if c not in headers]
    if missing:
        sys.exit("VERIFY FAILED: %s absent after the write." % ", ".join(missing))
    idx = {c: headers.index(c) for c in RESOURCE_COLUMNS}
    i_city = headers.index("City")

    bad = []
    for s in seeds:
        row = rows[s["row"] - 1] if s["row"] <= len(rows) else []
        if fold_city(cell(row, i_city)) != fold_city(s["city"]):
            bad.append("row %d is now %r, expected %r"
                       % (s["row"], cell(row, i_city), s["city"]))
            continue
        for column, want in s["values"].items():
            got = cell(row, idx[column])
            if got != want:
                bad.append("row %d %s: %r, expected %r"
                           % (s["row"], column, got, want))
    if bad:
        sys.exit("VERIFY FAILED:\n  " + "\n  ".join(bad))
    print("\nVerified: the resource columns exist and all %d seeded rows read back "
          "correctly." % len(seeds))


#: The `Slack Config` tab's own header row, and the prose label each JSON key
#: becomes. Prose because organizers read this tab; the matcher keeps the
#: snake_case keys. Kept in step with audit_organizers.CONFIG_LABELS by the tests.
CONFIG_HEADERS = ["Setting", "Value", "Notes"]
CONFIG_LABELS = {"public_prefixes": "Public channel prefix",
                 "organizer_suffixes": "Organizer channel suffix",
                 "staff_email_domain": "Staff email domain"}
CONFIG_NOTES = {
    "public_prefixes": "Tried in this order when matching a chapter's own channel. "
                       "'(none)' means the plain city slug, no prefix.",
    "organizer_suffixes": "Tried in this order when matching a private organizer "
                          "channel: <city><suffix>.",
    "staff_email_domain": "Addresses at this domain are counted as staff, not as "
                          "unaccounted members of an organizer channel.",
}
#: A sheet cell cannot hold an empty string distinguishably from a blank cell,
#: and the first public prefix is exactly that. Must equal
#: audit_organizers.EMPTY_VALUE.
EMPTY_VALUE = "(none)"


def config_rows(cfg):
    """The `Slack Config` grid, header first, one row per value in order."""
    rows = [CONFIG_HEADERS]
    for key in ("public_prefixes", "organizer_suffixes", "staff_email_domain"):
        values = cfg[key] if isinstance(cfg[key], list) else [cfg[key]]
        for i, v in enumerate(values):
            rows.append([CONFIG_LABELS[key], v if v != "" else EMPTY_VALUE,
                         CONFIG_NOTES[key] if i == 0 else ""])
    return rows


def migrate_config(path):
    """Move the matching vocabularies onto a `Slack Config` tab, then delete the JSON.

    The file goes entirely. Leaving a config-only remnant was the half-measure
    this replaces: two places to look for "how does matching work", one of which
    nothing reads.
    """
    if not os.path.exists(path):
        print("channel_map.json already gone; config tab assumed migrated.")
        return
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    rows = config_rows(cfg)
    meta = gws_json("sheets", "spreadsheets", "get",
                    params={"spreadsheetId": CHAPTERS_ID,
                            "fields": "sheets(properties(title))"})
    exists = any(s["properties"]["title"] == CONFIG_TAB for s in meta.get("sheets", []))
    if not exists:
        gws_json("sheets", "spreadsheets", "batchUpdate",
                 params={"spreadsheetId": CHAPTERS_ID},
                 body={"requests": [{"addSheet": {"properties": {
                     "title": CONFIG_TAB,
                     # Straight after the feed, before Past Events: it is read
                     # every time the audit runs, so it is not an appendix.
                     "index": 1,
                     "gridProperties": {"rowCount": max(len(rows) + 10, 20),
                                        "columnCount": len(CONFIG_HEADERS)}}}}]})
    gws_json("sheets", "spreadsheets", "values", "batchUpdate",
             params={"spreadsheetId": CHAPTERS_ID},
             body={"valueInputOption": "RAW",
                   "data": [{"range": "'%s'!A1" % CONFIG_TAB, "values": rows}]})

    # Compare cell-wise, padding both sides: Sheets drops trailing empty cells on
    # read, so a row whose Notes column is blank comes back short. A length-wise
    # comparison fails on data that is in fact correct.
    back = get_values(CHAPTERS_ID, "'%s'!A:C" % CONFIG_TAB)
    width = len(CONFIG_HEADERS)

    def pad(grid):
        return [[cell(r, i) for i in range(width)] for r in grid]

    if pad(back) != pad(rows):
        sys.exit("VERIFY FAILED: %r reads back as %r, expected %r"
                 % (CONFIG_TAB, pad(back), pad(rows)))
    os.unlink(path)
    print("Created the %r tab (%d settings rows) and deleted %s — the channel "
          "map and its matching config now live entirely on the sheet."
          % (CONFIG_TAB, len(rows) - 1, os.path.basename(path)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="apply the migration (default: report only)")
    ap.add_argument("--map", default=CHANNEL_MAP,
                    help="alternate channel_map.json to seed from")
    a = ap.parse_args()

    # Two phases, guarded independently. Phase 1 moves the per-city tables into
    # columns; phase 2 moves the matching vocabularies onto their own tab and
    # deletes the file. They are separate because phase 1 landed first and phase 2
    # was added after — so a re-run must be able to skip a finished phase rather
    # than abort the whole script and strand the other one.
    rows, headers = read_grid()
    columns_done = all(c in headers for c in RESOURCE_COLUMNS)
    config_done = not os.path.exists(a.map)
    seeded_before = any(c in headers for c in RESOURCE_COLUMNS)

    # A resource column added after the original migration (Organizer Handles
    # was) reaches the sheet through this path, not through the seeding phase —
    # that one needs channel_map.json, which the migration itself deleted.
    new_cols = plan_missing_columns(headers) if seeded_before and not columns_done else []
    if new_cols:
        print("Phase 0 (new resource columns): insert %s\n"
              % ", ".join("%s at %s" % (n, col_letter(at)) for at, n in new_cols))

    if columns_done and config_done:
        print("Nothing to do: the resource columns exist and %s is gone. The "
              "channel map lives entirely on the sheet."
              % os.path.basename(a.map))
        return

    anchor = seeds = None
    if columns_done or seeded_before:
        print("Phase 1 (seed from channel_map.json): already migrated, skipping.\n")
    else:
        cfg = load_channel_map(a.map)
        anchor, seeds, unmatched = plan(rows, headers, cfg)
        report(anchor, headers, seeds, unmatched)

    if not config_done:
        print("\nPhase 2 (matching config): move %s onto a %r tab, then delete it."
              % (os.path.basename(a.map), CONFIG_TAB))
        for row in config_rows(json.load(open(a.map, encoding="utf-8")))[1:]:
            print("    %-26s %-22s" % (row[0], row[1]))

    if not a.write:
        print("\nReport only. Re-run with --write to apply.")
        return

    if new_cols:
        _, headers_now = read_grid()
        if plan_missing_columns(headers_now) != new_cols:
            sys.exit("ABORT: the layout changed while the proposal was being "
                     "read. Nothing was written; re-run.")
        add_missing_columns(new_cols, sheet_id())

    if not columns_done and not seeded_before:
        # Recompute from a fresh read: the report above is the human-reading
        # window, and an insert applied against a shifted layout would be
        # unrecoverable.
        rows, headers = read_grid()
        anchor2, seeds2, _ = plan(rows, headers, load_channel_map(a.map))
        if anchor2 != anchor or len(seeds2) != len(seeds):
            sys.exit("ABORT: the sheet changed while the proposal was being read. "
                     "Nothing was written; re-run.")
        n = apply(anchor2, seeds2, sheet_id())
        print("\nApplied %d requests." % n)
        verify(seeds2)

    if not config_done:
        # Only after phase 1 is written AND verified. Deleting the file earlier
        # would destroy the seed source while the migration could still fail — so
        # the states this can leave behind are "not migrated", "columns only" and
        # "done", never a sheet seeded from a file that no longer exists.
        migrate_config(a.map)


if __name__ == "__main__":
    main()
