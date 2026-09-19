#!/usr/bin/env python3
"""Rank chapters by whether anything is actually happening in them. READ-ONLY.

Exists because of the 100-chapter cap (see sync_chapters.CHAPTER_CAP): growth
has to be paid for by retiring or coalescing an existing chapter, and that is a
decision somebody has to make about a named chapter. This is the evidence for
that decision, and deliberately nothing more — it proposes a `Status` and never
writes one, the same review-then-write shape as `resolve_slack_ids --suggest`.

## Two signals, because either one alone is wrong

  * **Events** — the `Past Events` tab. Nothing in this repo reads or writes
    that tab; it is maintained by hand, so a chapter with no rows there has not
    necessarily done nothing. As of 2026-09-18 it held 143 events across 22 of
    96 chapters.
  * **Slack** — the last HUMAN message in the chapter's public channel, from
    the `aaif-audit-slack` activity cache.

Ranking on events alone retires live chapters: 14 chapters have no recorded
event but an active public channel (Vancouver last posted 10 days ago, Pune 12,
Ahmedabad 11). Ranking on Slack alone misses a chapter that runs events and
coordinates elsewhere. So a chapter is ALIVE if either signal is inside the
window, and only chapters BOTH signals call quiet are proposed for review.

## Silence and ignorance are not the same thing

`last_human_ts` is absent from an activity record for several reasons and only
some of them are silence. `slack.history_activity` sets `last_human_unknown`
whenever a room has traffic but no HUMAN message in the scanned window — which
covers both a busy room whose scan hit `max_scan` before reaching one, and a
room whose only traffic is join notices. The first is the dangerous one:
reporting the workspace's busiest chapters as "silent all window" states the
reverse of the truth, and here it would sort them into a retirement queue. So an
UNKNOWN room is never counted as quiet. That is deliberately the same call
`audit_topics.dormancy()` makes for subject rooms — the rule is kept rather than
the import, because that helper wants a subject record shaped by
`attach_activity` and only one field is in play here.

The cost of the rule is that a join-only room is also held back from the queue.
That is the right direction to err for a report whose output is retirement
proposals, and such a chapter still surfaces under CANNOT SAY.

A chapter whose `Slack Channel` cell is the `NO_RESOURCE` sentinel has DECLARED
it has no room. Looking that up would miss, read as silence, and propose the
chapter for retirement on the strength of a channel it never claimed to have.

## What it will NOT tell you

Whether a quiet chapter is new or dead. The estate holds no chapter-founding
date: channel creation clusters on the bulk provisioning runs, and all 98 Drive
folders carry 9 distinct creation dates, the oldest younger than events already
on the Past Events tab. So this reports untriaged chapters as untriaged and
leaves `Provisioned` vs `Dormant` to a human who knows which were only just
stood up.

Usage:
    python3 chapter_health.py                 # ranked report
    python3 chapter_health.py --window 90     # stricter activity window
    python3 chapter_health.py --untriaged     # only rows with a BLANK Status
"""

import argparse
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import (CHAPTER_CAP, CHAPTERS_ID, H_MERGED_INTO,  # noqa: E402
                           NO_RESOURCE, cell, census_of, get_values,
                           header_index, read_chapters, unknown_statuses)
from aaif_events import jsoncache  # noqa: E402
from aaif_events.redact import add_redact_flag, set_redaction  # noqa: E402

PAST_EVENTS_TAB = "Past Events"
DEFAULT_CACHE = ".slack-audit-cache"
DEFAULT_WINDOW = 180

#: Epoch -> date in UTC, matching every other epoch conversion in this estate
#: (audit_activity, audit_members, audit_topics, fetch_local_champs). A local
#: conversion shifts every age by up to a day depending on where the operator
#: is sitting, which is how the same run reports different dormancy in two
#: offices.
UTC = datetime.timezone.utc


def _date(text):
    """Parse a hand-typed `Date` cell, or None.

    Ambiguous day/month spellings are NOT guessed: `03/04/2026` could be either
    and the tab is filled in by organizers in ~90 cities. Only the unambiguous
    ISO form and a day > 12 resolve; anything else is reported unparsed rather
    than silently read as one of the two.
    """
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    parts = text.split("/")
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        a, b, year = (int(p) for p in parts)
        if a > 12 >= b:                      # unambiguous: a is the day
            return datetime.date(year, b, a)
        if b > 12 >= a:                      # unambiguous: b is the day
            return datetime.date(year, a, b)
    return None


def last_events():
    """{city: (last_date_or_None, count)}, plus the unparsed-date count."""
    rows = get_values(CHAPTERS_ID, "'%s'!A:Z" % PAST_EVENTS_TAB)
    if not rows:
        return {}, 0
    headers = [h.strip() for h in rows[0]]
    # header_index, not .index(): it aborts on a duplicated header rather than
    # silently resolving to the first. This tab is hand-maintained, which makes
    # it the likeliest in the estate to grow a second `Date` column.
    i_city, i_date = header_index(headers, PAST_EVENTS_TAB, "City", "Date")
    out, unparsed = {}, 0
    for row in rows[1:]:
        city = cell(row, i_city)
        if not city:
            continue
        raw = cell(row, i_date)
        when = _date(raw)
        if raw and when is None:
            unparsed += 1
        prev_when, prev_n = out.get(city, (None, 0))
        if when and (prev_when is None or when > prev_when):
            prev_when = when
        out[city] = (prev_when, prev_n + 1)
    return out, unparsed


def slack_activity(cache_dir, today):
    """{channel: (days|None, unknown, members)} or None when uncached.

    `unknown` True means the room's history could not be scanned far enough to
    find a human message — ignorance, not silence. Returning None for the whole
    map (rather than an empty one) keeps "no cache" distinguishable from "no
    activity", because the second would retire the entire estate.
    """
    act = jsoncache.read(os.path.join(cache_dir, "activity.json"))
    chans = jsoncache.read(os.path.join(cache_dir, "channels.json"))
    if act is None or chans is None:
        return None
    out = {}
    for c in chans:
        rec = act.get(c["id"]) or {}
        ts = rec.get("last_human_ts")
        days = (today - datetime.datetime.fromtimestamp(ts, UTC).date()).days if ts else None
        # Clamp: a message timestamped slightly ahead of the clock would
        # otherwise read as negative age and sort as impossibly fresh.
        if days is not None:
            days = max(0, days)
        out[c["name"]] = (days, bool(rec.get("last_human_unknown")),
                          c.get("num_members"))
    return out


def build(window, cache_dir):
    """(rows, chapters, slack_seen, unparsed) — one read of each source."""
    chapters, _last_row, _layout = read_chapters()
    events, unparsed = last_events()
    today = datetime.datetime.now(UTC).date()
    slack = slack_activity(cache_dir, today)

    rows = []
    for ch in chapters:
        when, n = events.get(ch["city"], (None, 0))
        e_days = (today - when).days if when else None
        pub = (ch.get("public") or "").lstrip("#")
        declared_none = (not pub) or pub == NO_RESOURCE
        s_days, s_unknown, members = (None, False, None)
        not_in_cache = False
        if slack is not None and not declared_none:
            if pub in slack:
                s_days, s_unknown, members = slack[pub]
            else:
                # The room is named on the sheet but absent from the cache: it
                # was created after the last refresh, or renamed, or is private
                # and unreadable. Absence from a snapshot is ignorance, not
                # silence — the eight chapters provisioned on 2026-09-18 all
                # landed here against a cache refreshed hours earlier.
                not_in_cache = True
        # Quiet requires EVIDENCE of quiet. An unknown room, a chapter with no
        # room, and a missing cache are all "cannot say", and none of them put
        # a chapter into the retirement queue.
        slack_quiet = (s_days is not None and s_days > window)
        slack_says_nothing = (s_unknown or declared_none or slack is None
                              or not_in_cache)
        alive = ((e_days is not None and e_days <= window)
                 or (s_days is not None and s_days <= window))
        quiet = (not alive) and (slack_quiet or not slack_says_nothing)
        rows.append({"city": ch["city"], "status": ch.get("status", ""),
                     "merged_into": ch.get("merged_into", ""),
                     "events": n, "event_days": e_days, "slack_days": s_days,
                     "slack_unknown": s_unknown, "no_room": declared_none,
                     "not_in_cache": not_in_cache,
                     "members": members, "alive": alive, "quiet": quiet})
    return rows, chapters, slack is not None, unparsed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help="days within which a signal counts as activity "
                         "(default %d)" % DEFAULT_WINDOW)
    ap.add_argument("--untriaged", action="store_true",
                    help="only chapters whose Status cell is blank")
    ap.add_argument("--cache", default=DEFAULT_CACHE,
                    help="aaif-audit-slack cache dir (default %s)" % DEFAULT_CACHE)
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)

    rows, chapters, have_slack, unparsed = build(a.window, a.cache)
    if not have_slack:
        print("WARNING: no usable cache in %s — the Slack signal is MISSING, "
              "not silent. Run aaif-audit-slack's audit_activity.py first; "
              "until then no chapter is proposed for review on Slack evidence."
              % a.cache, file=sys.stderr)
    if unparsed:
        print("WARNING: %d %s row(s) have a Date this cannot parse "
              "unambiguously; those events are counted but not dated."
              % (unparsed, PAST_EVENTS_TAB), file=sys.stderr)

    live, retired, by_status = census_of(chapters)
    print("%d live chapter(s), %d retired — cap is %d, headroom %d"
          % (live, retired, CHAPTER_CAP, max(0, CHAPTER_CAP - live)))
    print("Status: " + ", ".join("%s=%d" % (k or "(blank)", v)
                                 for k, v in sorted(by_status.items())))
    odd = unknown_statuses(by_status)
    if odd:
        print("NOT in the vocabulary, counted as LIVE: "
              + ", ".join("%r (%d)" % (st, n) for st, n in odd))
    print()

    shown = [r for r in rows if not a.untriaged or not r["status"]]
    quiet = sorted((r for r in shown if r["quiet"]),
                   key=lambda r: (r["members"] or 0, r["city"]))
    awake = [r for r in shown if r["alive"]]
    cannot_say = [r for r in shown if not r["alive"] and not r["quiet"]]

    print("QUIET on BOTH signals (%d) — the review queue, NOT a decision."
          % len(quiet))
    print("A blank Status here means UNTRIAGED, not dead: this report cannot "
          "tell a chapter\nstood up last week from one that died a year ago "
          "(see the module docstring).")
    print("  %-22s %-7s %-11s %-11s %-8s %-13s %s"
          % ("chapter", "events", "last event", "last slack", "members",
             "Status", H_MERGED_INTO))
    for r in quiet:
        print("  %-22s %-7d %-11s %-11s %-8s %-13s %s"
              % (r["city"], r["events"],
                 ("%dd" % r["event_days"]) if r["event_days"] is not None else "never",
                 ("%dd" % r["slack_days"]) if r["slack_days"] is not None else "never",
                 r["members"] if r["members"] is not None else "-",
                 r["status"] or "(untriaged)", r["merged_into"] or ""))

    if cannot_say:
        print("\nCANNOT SAY (%d) — no activity found, but the evidence is "
              "missing rather than\nnegative, so these are NOT proposed for "
              "review:" % len(cannot_say))
        for r in sorted(cannot_say, key=lambda x: x["city"]):
            why = ("declares no Slack room" if r["no_room"] else
                   "traffic but no human message in the scanned window "
                   "(a capped scan on a busy room, or a room whose only "
                   "traffic is joins)" if r["slack_unknown"] else
                   "channel not in the activity cache (newer than the last "
                   "refresh, renamed, or private)" if r["not_in_cache"] else
                   "no activity cache")
            print("  %-22s %s" % (r["city"], why))

    print("\nACTIVE on at least one signal (%d): %s"
          % (len(awake), ", ".join(sorted(r["city"] for r in awake))))
    print("\nReport only — no Status cell was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
