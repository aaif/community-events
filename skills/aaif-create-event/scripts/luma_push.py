#!/usr/bin/env python3
"""Create the Luma event page for a tracker event — proposal by default, LIVE
write only with --create (which the agent runs only after the user approves the
printed proposal). Operates on a tracker.docx the agent already downloaded via
gws; the Luma API key is per-calendar, so events land on that chapter/series
calendar (see the SKILL.md for key setup).

Without --create: prints the full events/create payload, the hosts to add, and
which account/calendar the API key belongs to. No live writes are sent (when
connected, the dry-run still makes read-only calls: the LUMA URL entity lookup
and get_calendar).

With --create: uploads the cover (if given), creates the event (private unless
--visibility public — flip it public on Luma after review), adds hosts,
writes the event URL into the tracker's LUMA URL field (local file — re-upload
it to Drive afterwards), and prints the live URL.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))
from aaif_events import luma, office, tracker  # noqa: E402


def parse_host(spec):
    """email[:manager|check-in][:Display Name] -> (email, access_level, name)."""
    parts = spec.split(":", 2)
    email, level, name = parts[0].strip(), None, None
    for p in parts[1:]:
        p = p.strip()
        if p.lower() in ("manager", "check-in"):
            level = p.lower()
        elif p:
            name = p
    if "@" not in email:
        raise ValueError("--host needs an email, got %r" % spec)
    return email, level, name


def already_pushed(view, *, connected=False):
    """The LUMA URL cell holds an event page link (not the chapter calendar link).

    The template pre-fills the chapter calendar link (an aaif- slug), but event
    pages may use aaif- slugs too (--slug aaif-sf-evalnight). When connected,
    the entity lookup decides — and a failed lookup raises LumaError rather than
    guessing, so --create can't proceed on an unverified URL. Offline (no key),
    the slug heuristic decides; safe, because --create requires the key.
    """
    cell = (view["details"].get("LUMA URL") or "").strip()
    if not cell or not ("luma.com/" in cell.lower() or "lu.ma/" in cell.lower()):
        return None
    if connected:
        try:
            luma.resolve_event_id(cell)
            return cell
        except luma.NotAnEventUrl:
            return None
    return None if "aaif-" in cell.lower() else cell


def show_proposal(payload, hosts, cover, key_ok):
    print("Proposed Luma event (events/create) — visibility: %s%s"
          % (payload.get("visibility", "private"),
             "" if payload.get("visibility") == "public"
             else " (flip to public on Luma after review)"))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if cover:
        print("Cover : %s (uploaded to the Luma CDN on --create)" % cover)
    for email, level, name in hosts:
        print("Host  : %s (%s%s)" % (email, level or "manager", ", " + name if name else ""))
    if key_ok:
        try:
            cal = luma.get_calendar()
            print("Target: calendar %r — events created by this key land THERE."
                  % (cal.get("name") or cal))
        except luma.LumaError as e:
            print("Target: could not read the calendar for this key (%s)" % e)
    else:
        print("Target: Luma NOT connected — no API key for this calendar.")


def main():
    ap = argparse.ArgumentParser(description="Push a tracker event to Luma (propose, then --create)")
    ap.add_argument("docx", help="tracker.docx already downloaded via gws")
    ap.add_argument("event", help="event title (case-insensitive substring), or 'next'/'latest'")
    ap.add_argument("--timezone", required=True,
                    help="IANA timezone of the event city, e.g. America/Los_Angeles")
    ap.add_argument("--duration-hours", type=float, default=3.0,
                    help="event length when DATE & TIME has no end time (default 3)")
    ap.add_argument("--description-file", help="markdown file for the page copy "
                    "(write it with the aaif-luma-description skill)")
    ap.add_argument("--cover", help="local cover image (export the event banner to PNG)")
    ap.add_argument("--slug", help="optional event URL slug")
    ap.add_argument("--visibility", choices=("public", "private"), default="private",
                    help="page visibility on create (default private: review the page "
                         "on Luma, then flip it public)")
    ap.add_argument("--host", action="append", default=[], metavar="EMAIL[:LEVEL][:NAME]",
                    help="host to add (repeatable); LEVEL = manager (default) or check-in")
    ap.add_argument("--create", action="store_true",
                    help="LIVE WRITE: create the event on Luma (only after user approval)")
    ap.add_argument("--force", action="store_true",
                    help="create even though the tracker already has an event URL")
    a = ap.parse_args()

    root = office.read_document(a.docx)
    view = tracker.read_event(root, a.event)
    description = open(a.description_file, encoding="utf-8").read() if a.description_file else None
    payload = luma.event_payload(view, a.timezone, a.duration_hours,
                                 description_md=description, slug=a.slug,
                                 visibility=a.visibility)
    hosts = [parse_host(h) for h in a.host]

    key_ok = luma.available()
    try:
        pushed = already_pushed(view, connected=key_ok)
    except luma.LumaError as e:
        if not a.force:
            sys.exit("ABORT: couldn't verify whether the LUMA URL is already an event "
                     "page (%s). Retry, or pass --force to skip the check." % e)
        pushed = None
        print("WARNING: couldn't verify the LUMA URL (%s) — continuing due to --force." % e)
    if pushed and not a.force:
        sys.exit("ABORT: this event's LUMA URL is already %r — it looks pushed. "
                 "Use aaif-update-event to change it, or --force to create anyway." % pushed)

    show_proposal(payload, hosts, a.cover, key_ok)
    if not a.create:
        if key_ok:
            print("\n[dry-run] No live writes sent. Review with the user; re-run with "
                  "--create ONLY after they explicitly approve this proposal.")
        else:
            print("\n[dry-run] Luma is NOT connected (no API key for this calendar). "
                  "SKIP the automated push: ask the user to create the event manually "
                  "at luma.com with the details above, then record its URL in the "
                  "tracker's LUMA URL field. (Or set up the key — see SKILL.md — and re-run.)")
        return
    if not key_ok:
        sys.exit("ABORT: --create needs an API key; Luma is not connected. Create the "
                 "event manually instead (details above) or set up the key per SKILL.md.")

    print("\nLIVE: creating the event on Luma...")
    if a.cover:
        payload["cover_url"] = luma.upload_image(a.cover)
        print("  cover uploaded: %s" % payload["cover_url"])
    res = luma.create_event(payload)
    event_id = res.get("id") or res.get("api_id")   # same variance resolve_event_id handles
    if not event_id:
        sys.exit("Event was likely created, but the response had no id (keys: %s) — "
                 "check the calendar, then record the page URL in the tracker's "
                 "LUMA URL field manually." % ", ".join(sorted(res)))
    try:
        live = luma.get_event(event_id)
    except luma.LumaError as e:
        live = {}
        print("  !! created %s but couldn't re-fetch it: %s" % (event_id, e))
    url = live.get("url") or res.get("url")
    print("  created: %s (%s)" % (url or "(no url in response)", event_id))

    failed = []
    for email, level, name in hosts:
        try:
            luma.add_host(event_id, email, name=name, access_level=level)
            print("  host added: %s" % email)
        except luma.LumaError as e:
            failed.append((email, str(e)))
            print("  !! host FAILED: %s — %s" % (email, e))

    if url:
        tracker.set_field(root, a.event, "LUMA URL", url)
        office.save_document(a.docx, root, a.docx)
        print("  tracker LUMA URL set in %s — re-upload it to Drive via gws." % a.docx)
    else:
        # never write a guessed URL — it would poison later sync/stats resolution
        print("  !! no event URL in the API response — open the calendar, find the "
              "event, and record its page URL in the tracker's LUMA URL field manually.")
    if failed:
        sys.exit("Event created, but %d host(s) failed — add them on the Luma page "
                 "or re-run add-host." % len(failed))
    print("Done. Verify the page: %s" % url)


if __name__ == "__main__":
    main()
