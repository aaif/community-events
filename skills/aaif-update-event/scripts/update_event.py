#!/usr/bin/env python3
"""Deterministic, local-file event updater: edit detail fields and, when the date
moves, recompute every phase due-date. Operates on a docx the agent has ALREADY
downloaded via the gws CLI — this script never touches Drive. Pure-Python docx edit."""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))
from aaif_events import office, tracker  # noqa: E402

STALE_ON_DATE = ["square banner", "Luma cover", "announcement post",
                 "carousel", "day-of slides", "attendee reminder"]
STALE_ON_SPEAKER = ["speaker bio", "announcement post", "carousel", "day-of slides"]
STALE_ON_VENUE = ["announcement post", "day-of slides", "attendee reminder"]


def apply_changes(root, event, set_pairs, date_str):
    """Mutate root in place. set_pairs is a list of 'LABEL=VALUE' strings.
    If date_str is given, recompute all due-dates from the *original* date first,
    then write the authoritative new DATE & TIME string. Returns the stale-asset set."""
    stale = set()
    for pair in set_pairs:
        if "=" not in pair:
            raise ValueError("--set must be LABEL=VALUE (got %r)" % pair)
        label, _, value = pair.partition("=")
        lab = label.strip().upper()
        if lab == "DATE & TIME":
            # A bare field write would skip the due-date recompute (which must run
            # against the ORIGINAL date) — that's exactly what --date exists for.
            raise ValueError('--set cannot write DATE & TIME - use --date "..." '
                             "so every phase due-date is recomputed from the "
                             "original date.")
        tracker.set_field(root, event, label.strip(), value.strip())
        if "SPEAKER" in lab:
            stale.update(STALE_ON_SPEAKER)
        if "VENUE" in lab or "LOCATION" in lab:
            stale.update(STALE_ON_VENUE)
    if date_str:
        # restamp DUE cells using the original date still in the doc, THEN overwrite
        # DATE & TIME with the user's full string (so weekday/time are exactly as given).
        tracker.set_due_dates(root, event, tracker.parse_event_date(date_str))
        tracker.set_field(root, event, "DATE & TIME", date_str)
        stale.update(STALE_ON_DATE)
    return stale


def main():
    ap = argparse.ArgumentParser(description="Update an event in a local Event Tracker.docx")
    ap.add_argument("docx", help="path to a tracker.docx already downloaded via gws")
    ap.add_argument("event", help="event title (case-insensitive substring), or 'next'/'latest'")
    ap.add_argument("--set", action="append", default=[],
                    metavar="LABEL=VALUE", help='e.g. --set "SPEAKER(S)=Jane Doe"')
    ap.add_argument("--date", help="new DATE & TIME value; triggers due-date recompute")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the field diff and stale-asset list; write nothing")
    a = ap.parse_args()

    root = office.read_document(a.docx)
    before = dict(tracker.read_event(root, a.event)["details"]) if a.dry_run else None
    stale = apply_changes(root, a.event, a.set, a.date)
    if a.dry_run:
        after = tracker.read_event(root, a.event)["details"]
        print("[dry-run] would update %r in %s:" % (a.event, a.docx))
        for label in sorted(set(before) | set(after)):
            if before.get(label) != after.get(label):
                print("  %-18s %r -> %r" % (label + ":", before.get(label), after.get(label)))
        if a.date:
            print("  (all phase due-dates recomputed from the original date)")
        if stale:
            print("[dry-run] now stale — re-run these skills: " + ", ".join(sorted(stale)))
        print("[dry-run] nothing written; re-run without --dry-run to apply.")
        return
    office.save_document(a.docx, root, a.docx)
    print("Updated %r in %s." % (a.event, a.docx))
    if stale:
        print("Now stale — re-run these skills: " + ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
