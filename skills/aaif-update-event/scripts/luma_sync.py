#!/usr/bin/env python3
"""Sync a tracker event's details to its live Luma event — diff by default, LIVE
write only with --apply (which the agent runs only after the user approves the
printed diff). Reads a tracker.docx the agent already downloaded via gws.

The Luma event is found from the tracker's LUMA URL field (the event page URL
that aaif-create-event's luma_push wrote back), or --event-id. The diff shows
every field that would change, live -> desired; --apply pushes exactly those
fields via events/update and re-verifies. Luma notifies guests of changes
unless --quiet (suppress_notifications) is passed. Cancellation is deliberately
NOT implemented here — it is irreversible and stays a manual Luma action.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))
from aaif_events import luma, office, tracker  # noqa: E402


def find_event_id(view, override):
    if override:
        return override
    cell = (view["details"].get("LUMA URL") or "").strip()
    if not cell:
        sys.exit("ABORT: the tracker's LUMA URL field is empty — push the event first "
                 "(aaif-create-event) or pass --event-id.")
    # No slug heuristic here: event pages may use aaif- slugs too, so let the
    # entity lookup decide what the URL points at (only called when connected).
    try:
        return luma.resolve_event_id(cell)
    except luma.NotAnEventUrl:
        sys.exit("ABORT: LUMA URL doesn't point to an event page (%r) — likely the "
                 "chapter calendar link. Pass --event-id or the event's own URL." % cell)
    except luma.LumaError as e:
        sys.exit("ABORT: couldn't resolve the LUMA URL %r (%s). Retry, or pass "
                 "--event-id." % (cell, e))


def fmt(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else repr(v)


def main():
    ap = argparse.ArgumentParser(description="Diff a tracker event against Luma; --apply to push")
    ap.add_argument("docx", help="tracker.docx already downloaded via gws")
    ap.add_argument("event", help="event title (case-insensitive substring), or 'next'/'latest'")
    ap.add_argument("--timezone", required=True,
                    help="IANA timezone of the event city, e.g. America/Los_Angeles")
    ap.add_argument("--duration-hours", type=float, default=3.0)
    ap.add_argument("--event-id", help="evt- id override (else resolved from LUMA URL)")
    ap.add_argument("--description-file",
                    help="markdown file to REPLACE the page copy (omit to leave it alone)")
    ap.add_argument("--cover", help="new cover image to upload and set (omit to leave it alone)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress Luma's guest notifications for this update")
    ap.add_argument("--apply", action="store_true",
                    help="LIVE WRITE: push the diff to Luma (only after user approval)")
    a = ap.parse_args()

    root = office.read_document(a.docx)
    view = tracker.read_event(root, a.event)
    description = open(a.description_file, encoding="utf-8").read() if a.description_file else None
    desired = luma.event_payload(view, a.timezone, a.duration_hours,
                                 description_md=description)
    desired.pop("visibility", None)   # never flip visibility from a sync

    if not luma.available():
        # Not connected -> can't diff or push; hand the user the manual checklist.
        print("Luma is NOT connected (no API key for this calendar) — skipping the "
              "automated sync. Ask the user to update the Luma page manually to:")
        for k in sorted(desired):
            print("  %-18s %s" % (k + ":", fmt(desired[k])))
        if a.cover:
            print("  %-18s upload %s" % ("cover:", a.cover))
        print("(Or set up the key — see aaif-create-event's SKILL.md — and re-run.)")
        return

    event_id = find_event_id(view, a.event_id)
    live = luma.get_event(event_id)
    changes = luma.diff_payload(live, desired)
    if a.cover:
        changes["cover_url"] = (live.get("cover_url"), "(upload %s)" % a.cover)

    print("Luma event: %s (%s)" % (live.get("url", "?"), event_id))
    if not changes:
        print("No changes — Luma already matches the tracker.")
        return
    print("Proposed update (live -> desired):")
    for k in sorted(changes):
        have, want = changes[k]
        print("  %-18s %s\n  %18s -> %s" % (k + ":", fmt(have), "", fmt(want)))
    if not a.apply:
        print("\n[dry-run] Nothing sent. Review with the user; re-run with --apply "
              "ONLY after they explicitly approve this diff. Guests %s notified."
              % ("will NOT be" if a.quiet else "WILL be"))
        return

    print("\nLIVE: updating the event on Luma%s..."
          % (" (notifications suppressed)" if a.quiet else ""))
    body = {"event_id": event_id}
    if a.quiet:
        body["suppress_notifications"] = True
    for k, (_, want) in changes.items():
        body[k] = luma.upload_image(a.cover) if k == "cover_url" and a.cover else want
    luma.update_event(body)

    # verify: re-fetch and re-diff. description_md is excluded — Luma's Spark
    # round-trip isn't byte-stable, so it would always look dirty right after a
    # push. cover_url needs no such pop: it is never part of `desired` (it only
    # enters `changes` when --cover is given), so the CDN rewriting the uploaded
    # URL can't make the verify look dirty either.
    desired.pop("description_md", None)
    still = luma.diff_payload(luma.get_event(event_id), desired)
    if still:
        print("VERIFY FAILED — fields still differ after update:")
        for k, (have, want) in still.items():
            print("  %s: %s -> %s" % (k, fmt(have), fmt(want)))
        sys.exit(1)
    print("Done — re-fetched the event; it now matches the tracker.")


if __name__ == "__main__":
    main()
