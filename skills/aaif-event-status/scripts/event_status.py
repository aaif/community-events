#!/usr/bin/env python3
"""Deterministic, local-file status digest for an Event Tracker.docx: overdue and
due-soon tasks grouped by owner. Operates on a docx the agent has ALREADY downloaded
with the gws CLI — this script never touches Drive. Pure-Python parsing."""
import argparse
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))
from aaif_events import office, tracker  # noqa: E402

DUE_SOON_DAYS = 7


def classify(tasks, anchor, today):
    overdue, due_soon = [], []
    for t in tasks:
        d = tracker.parse_due(t.due, anchor)
        if d is None or t.status == "Done":
            continue
        if d < today:
            overdue.append(t)
        elif (d - today).days <= DUE_SOON_DAYS:
            due_soon.append(t)
    return {"overdue": overdue, "due_soon": due_soon}


def _digest(ev, today):
    flat = [t for ph in ev["phases"] for t in ph["tasks"]]
    res = classify(flat, ev["date"] or today, today)
    header = "== %s ==" % ev["title"]
    if ev["date"] is None:
        header += "  (! DATE & TIME did not parse — status anchored to today)"
    lines = ["", header,
             "%d overdue, %d due within %d days"
             % (len(res["overdue"]), len(res["due_soon"]), DUE_SOON_DAYS)]
    for label in ("overdue", "due_soon"):
        if res[label]:
            lines.append("  %s:" % label.replace("_", "-"))
            for t in sorted(res[label], key=lambda x: x.owner):
                lines.append("    [%s] %s (due %s)" % (t.owner, t.task, t.due))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Status digest for a local Event Tracker.docx")
    ap.add_argument("docx", help="path to a tracker.docx already downloaded via gws")
    ap.add_argument("event", nargs="?", help="optional event title filter; default all")
    a = ap.parse_args()
    if not os.path.isfile(a.docx):
        sys.exit("! no tracker at %s — fetch it first with fetch_tracker.py "
                 "(it prints the path)." % a.docx)
    root = office.read_document(a.docx)
    refs = tracker.list_events(root)
    if a.event:
        refs = [e for e in refs if a.event.lower() in e["title"].lower()]
    today = dt.date.today()
    print("%s — %d event(s)" % (os.path.basename(a.docx), len(refs)))
    for ref in refs:
        print(_digest(tracker.view_event(ref), today))


if __name__ == "__main__":
    main()
