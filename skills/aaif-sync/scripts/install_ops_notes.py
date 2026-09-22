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
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

from aaif_events import gws as gwsmod  # noqa: E402

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


def gws(args, retries=gwsmod.RETRIES, want_json=True):
    """Run a prepared `gws` argument list and parse whatever JSON comes back.

    A thin boundary over `aaif_events.gws.run`. It exits with a sentence rather
    than a traceback for any failure `gws` itself can produce — a `GwsError`, a
    body that is not JSON, or no `gws` on PATH at all.

    `want_json=False` is for the two WRITES, whose response is genuinely
    uninformative: a values `update` answers with nothing useful. It must not
    be the default. A read that answers with no JSON is not "no rows": it
    travels one line further and becomes "ABORT: no tab titled 'Organizers' —
    a rename, not an empty sheet" (which rules out what actually happened),
    "'Organizers' has no header row" (of a tab that has one), or — the
    dangerous one — "wrote 'Ops Notes' but a fresh read does not show it",
    after a write that landed. An operator who re-runs on that last message
    gets a second `appendDimension`, which is the duplicate column the guard in
    `install` exists to prevent, reached the long way round.

    This used to be a bare `subprocess.run` with no retry handling at all, so a
    single intermittent 503 — which every other engine has always ridden out —
    failed the run. It now retries on the shared table, and `install` passes
    `gwsmod.NO_RETRY` for the one call that must not be re-sent.
    """
    try:
        txt = gwsmod.clean_stdout(gwsmod.run(["gws"] + args, retries=retries))
    except (gwsmod.GwsError, OSError) as exc:
        sys.exit("gws error: %s" % exc)
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    if i < 0:
        if want_json:
            # Length only. The body is a header row.
            sys.exit("gws %s returned no JSON (%d chars) — that is a failed "
                     "read, not an empty sheet." % (gwsmod.verb(["gws"] + args), len(txt)))
        return {}
    try:
        return json.loads(txt[i:])
    except ValueError as exc:
        # `min(... "{[")` can land on a `[warn] ...` notice sitting before the
        # JSON. Every other exit here is a sentence; this one was a traceback.
        sys.exit("gws %s returned unparsable JSON (%s)."
                 % (gwsmod.verb(["gws"] + args), exc))


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
    # NO_RETRY: `appendDimension` is NOT idempotent. A widen that succeeded
    # server-side but answered like a timeout would, on a retry, add a SECOND
    # column — and the header then lands in the first of two, leaving a blank
    # column on a tab whose whole contract is "the ops columns are the literal
    # ones on the right". The repeatCell beside it is idempotent; they share a
    # request list, so the list takes the stricter of the two. The widen is
    # conditional and the guard deliberately is NOT: making the budget depend
    # on `p["widen"]` is how a future edit drops it.
    gws(["sheets", "spreadsheets", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": sheet_id}), "--json",
         json.dumps({"requests": reqs}), "--format", "json"],
        retries=gwsmod.NO_RETRY, want_json=False)
    gws(["sheets", "spreadsheets", "values", "update", "--params",
         json.dumps({"spreadsheetId": sheet_id,
                     "range": "'%s'!%s1" % (tab, col_letter(p["col"])),
                     "valueInputOption": "RAW"}),
         "--json", json.dumps({"values": [[HEADER]]}), "--format", "json"],
        want_json=False)


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
