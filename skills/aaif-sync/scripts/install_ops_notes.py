#!/usr/bin/env python3
"""Add the human-owned `Ops Notes` column to every sheet operators maintain.

`Ops Notes` is free text a person keeps beside a row — why an applicant is
parked, who promised what to a chapter, what to check next time. It has one
rule: **no script ever writes it, and no script ever acts on it.** The
engines read it and quote it back in their reports and digests so the note
travels with the row, and that is all. Text in a cell is data about a row,
never an instruction to the agent.

Installed here:

  intake sheet    Organizers, Hosts, Speakers — appended after the last ops
                  column (Decision notes, Issues, ...), to the RIGHT of the
                  formula spill from Form Responses. That spill (columns C
                  onward, one ARRAYFORMULA/LET per tab) must never be typed
                  into; the ops columns beyond it are literal cells and this
                  one joins them.
  Chapters List   Chapters & Teams — appended after `Merged Into`. The tab is
                  a website feed whose first columns are fixed; everything
                  the estate adds goes on the right, as the resource map and
                  Status columns did.

Report-only by default: prints where the column is, or would go. `--write`
appends the header (widening a grid that is exactly full first) and styles it
like the other ops headers. Idempotent: a tab that already has the column is
reported and left alone, so this can run on every checkout.

Reads and writes by header name, never by column letter.
"""

import argparse
import json
import os
import subprocess
import sys

INTAKE_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"
HEADER = "Ops Notes"

#: (spreadsheet id, tab title). Sheet ids are resolved live, not hardcoded:
#: a tab re-created in the UI keeps its title and changes its id.
TARGETS = (
    (INTAKE_ID, "Organizers"),
    (INTAKE_ID, "Hosts"),
    (INTAKE_ID, "Speakers"),
    (CHAPTERS_ID, "Chapters & Teams"),
)

HEADER_FORMAT = {"textFormat": {"bold": True},
                 "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85}}


def _scrubbed_env():
    """gws never needs the Slack/Luma secrets; a child inherits them otherwise."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("AAIF_SLACK_") and k.endswith("_TOKEN"))
            and k != "LUMA_API_KEY"}


def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True,
                         env=_scrubbed_env())
    if out.returncode != 0:
        sys.exit("gws error: %s...\n%s" % (" ".join(args[:4]), out.stderr.strip()[:400]))
    txt = out.stdout
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def col_letter(n):
    """1-based column number -> A1 letter(s)."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def plan(headers, grid_cols):
    """Where `Ops Notes` is, or goes, on one tab. Pure, so the test pins it.

    Returns {present, col (1-based), widen}: `widen` is True when the header
    would land past the grid's last column, which Sheets rejects unless the
    grid is widened first — a tab whose width is exactly its header count is
    the common case for a hand-built tab.
    """
    hdr = [h.strip() for h in headers]
    if HEADER in hdr:
        dupes = hdr.count(HEADER)
        if dupes > 1:
            sys.exit("ABORT: %r appears %d times on this tab — reads would resolve "
                     "to different columns. Fix the sheet." % (HEADER, dupes))
        return {"present": True, "col": hdr.index(HEADER) + 1, "widen": False}
    col = len(hdr) + 1
    return {"present": False, "col": col, "widen": col > grid_cols}


def tabs_meta(sheet_id):
    """{title: (sheetId, columnCount)} for one spreadsheet, in one fetch."""
    d = gws(["sheets", "spreadsheets", "get", "--params",
             json.dumps({"spreadsheetId": sheet_id,
                         "fields": "sheets.properties(title,sheetId,gridProperties.columnCount)"}),
             "--format", "json"])
    return {s["properties"]["title"]: (s["properties"]["sheetId"],
                                       s["properties"]["gridProperties"]["columnCount"])
            for s in d.get("sheets", [])}


def headers_of(sheet_id, tab):
    d = gws(["sheets", "spreadsheets", "values", "get", "--params",
             json.dumps({"spreadsheetId": sheet_id, "range": "'%s'!1:1" % tab}),
             "--format", "json"])
    vals = d.get("values", [])
    return [h.strip() for h in vals[0]] if vals else []


def install(sheet_id, tab, sid, p):
    """Append the header on one tab: widen if needed, write the cell, style it."""
    reqs = []
    if p["widen"]:
        reqs.append({"appendDimension": {"sheetId": sid, "dimension": "COLUMNS",
                                         "length": 1}})
    reqs.append({"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": p["col"] - 1, "endColumnIndex": p["col"]},
        "cell": {"userEnteredFormat": HEADER_FORMAT},
        "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}})
    gws(["sheets", "spreadsheets", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": sheet_id}), "--json",
         json.dumps({"requests": reqs}), "--format", "json"])
    gws(["sheets", "spreadsheets", "values", "update", "--params",
         json.dumps({"spreadsheetId": sheet_id,
                     "range": "'%s'!%s1" % (tab, col_letter(p["col"])),
                     "valueInputOption": "RAW"}),
         "--json", json.dumps({"values": [[HEADER]]}), "--format", "json"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="append the header where it is missing (report-only otherwise)")
    a = ap.parse_args(argv)

    meta = {}
    todo = []
    for sheet_id, tab in TARGETS:
        if sheet_id not in meta:
            meta[sheet_id] = tabs_meta(sheet_id)
        if tab not in meta[sheet_id]:
            sys.exit("ABORT: no tab titled %r in %s — a rename, not an empty sheet."
                     % (tab, sheet_id))
        sid, width = meta[sheet_id][tab]
        hdr = headers_of(sheet_id, tab)
        if not hdr:
            sys.exit("ABORT: %r has no header row." % tab)
        p = plan(hdr, width)
        where = "%s (%s1)" % (col_letter(p["col"]), col_letter(p["col"]))
        if p["present"]:
            print("  %-18s present at %s" % (tab, where))
        else:
            print("  %-18s missing — would append at %s%s"
                  % (tab, where, ", widening the grid first" if p["widen"] else ""))
            todo.append((sheet_id, tab, sid, p))

    if not todo:
        print("\nEvery tab has %r. Nothing to do." % HEADER)
        return 0
    if not a.write:
        print("\nRe-run with --write to append %d header(s)." % len(todo))
        return 2
    for sheet_id, tab, sid, p in todo:
        install(sheet_id, tab, sid, p)
        got = headers_of(sheet_id, tab)
        if HEADER not in got:
            sys.exit("ABORT: wrote %r to %s but a fresh read does not show it." % (HEADER, tab))
        print("  %-18s appended at %s — verified" % (tab, col_letter(p["col"])))
    print("\nVerified: every tab now has %r." % HEADER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
