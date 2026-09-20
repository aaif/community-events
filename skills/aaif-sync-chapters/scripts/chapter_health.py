#!/usr/bin/env python3
"""Rank chapters by whether anything is actually happening in them. READ-ONLY.

Exists because of the 100-chapter cap (see sync_chapters.CHAPTER_CAP): growth
has to be paid for by retiring or coalescing an existing chapter, and that is a
decision somebody has to make about a named chapter. This is the evidence for
that decision, and deliberately nothing more — it proposes a `Status` and never
writes one, the same review-then-write shape as `resolve_slack_ids --suggest`.

## Two signals, because either one alone is wrong

  * **Events** — the `Past Events` tab. Nothing in this repo WRITES that tab
    (this module is its only reader); it is maintained by hand, so a chapter
    with no rows there has not necessarily done nothing. Observed 2026-09-18:
    143 events across 22 of the 96 rows on the chapters tab.
  * **Slack** — the last HUMAN message in the chapter's public channel, from
    the `aaif-audit-slack` activity cache.

Ranking on events alone retires live chapters: observed 2026-09-18, 14 chapters
had no recorded event but a public channel posted in within days. Ranking on
Slack alone misses a chapter that runs events and coordinates elsewhere. So a
chapter is ALIVE if either signal is inside the window.

The two signals are NOT symmetric, and the report says so rather than claiming
"quiet on both". Absence of events is weak evidence — the tab is hand-kept — so
it can never by itself put a chapter in the queue. Only a POSITIVE Slack
measurement (a human message, dated, older than the window) does that. A
chapter with undated events is held out entirely.

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
import collections
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import (CHAPTER_CAP, CHAPTERS_ID, H_MERGED_INTO,  # noqa: E402
                           NO_RESOURCE, cell, census_of, fold_city, get_values,
                           header_index, read_chapters, unknown_statuses)
from aaif_events import jsoncache  # noqa: E402
from aaif_events.redact import (add_redact_flag, redact_name, redact_text,  # noqa: E402
                                set_redaction)

#: One verdict per chapter, so "a row is in exactly one bucket" is a property of
#: the data rather than an emergent one of two booleans that could both be set.
ALIVE, QUIET, CANNOT_SAY = "alive", "quiet", "cannot-say"

#: A USABLE activity record. Built only for a record this run may trust; every
#: other case is None (ignorance), so the mutual exclusion of days/unknown lives
#: in one constructor rather than in each reader.
Act = collections.namedtuple("Act", "days unknown members archived")

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
    """Parse a hand-typed `Date` cell, or None when it cannot be trusted.

    Accepts the ISO form, the two month-name forms, and a slash form ONLY when
    the day is unambiguous (> 12). `03/04/2026` could be March or April and the
    tab is filled in by organizers in ~90 cities, so it is refused rather than
    guessed.

    Returns None rather than raising. A typo like `32/1/2026` used to reach
    datetime and take the whole report down, and a two-digit year was read as
    year 26 AD — which sorted that chapter as maximally dead.
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
        if len(parts[2].strip()) != 4:
            return None                      # `26` is not a year we will guess
        day, month = (a, b) if a > 12 >= b else (b, a) if b > 12 >= a else (0, 0)
        if day:
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None                  # 32/1/2026 and friends
    return None


def last_events():
    """({folded city: (last_date|None, count)}, unparsed_count, undated_cities).

    Keyed by fold_city, like every other cross-tab city join in this skill. The
    Past Events tab is hand-maintained; a row typed "Washington, DC" against a
    feed row of "Washington DC" would otherwise zero that chapter's entire event
    history and push it toward the retirement queue on a string mismatch.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:Z" % PAST_EVENTS_TAB)
    if not rows:
        # read_chapters aborts on exactly this condition, and for the same
        # reason: an empty read here silently disables one of the two signals
        # that keep a chapter OUT of the retirement queue.
        sys.exit("ABORT: %r came back empty. That would disable the events "
                 "signal and propose live chapters for retirement — refusing "
                 "rather than reporting half the evidence." % PAST_EVENTS_TAB)
    headers = [h.strip() for h in rows[0]]
    # header_index, not .index(): it aborts on a duplicated header rather than
    # silently resolving to the first. This tab is hand-maintained, which makes
    # it the likeliest in the estate to grow a second `Date` column.
    i_city, i_date = header_index(headers, PAST_EVENTS_TAB, "City", "Date")
    out, unparsed, undated = {}, 0, set()
    for row in rows[1:]:
        city = cell(row, i_city)
        if not city:
            continue
        key = fold_city(city)
        raw = cell(row, i_date)
        when = _date(raw)
        if raw and when is None:
            unparsed += 1
            undated.add(key)
        prev_when, prev_n = out.get(key, (None, 0))
        if when and (prev_when is None or when > prev_when):
            prev_when = when
        out[key] = (prev_when, prev_n + 1)
    return out, unparsed, undated


def slack_activity(cache_dir, today, window):
    """(records, meta) — {channel: Act|None}, plus what the sweep can answer.

    `records` is None when there is no usable cache at all. Per channel, None
    means IGNORANCE: `activity.json` is partial by construction — audit_activity
    flushes every 20 channels and is documented as resumable, and it only sweeps
    un-archived rooms — so a channel can sit in channels.json with no record
    here at all. Scoring that as silence files the chapter into a retirement
    queue, which is the thing this module exists to avoid.

    `meta` carries the sweep's own horizon: how many days back it looked
    (`window_days`) and how old it is (`age_days`). A sweep cannot answer a
    question wider than it looked, so the caller clamps to it rather than
    reporting an unanswerable window as silence.
    """
    # max_age=None: this script only CONSUMES the cache and cannot refetch, and
    # it dates the sweep itself from the per-record `day` stamps below — an
    # old sweep is clamped and announced, never mistaken for a fresh one. The
    # shared one-day expiry is for producers, which discard and re-pull.
    act = jsoncache.read(os.path.join(cache_dir, "activity.json"), note=print,
                         max_age=None)
    chans = jsoncache.read(os.path.join(cache_dir, "channels.json"), note=print,
                           max_age=None)
    if act is None or chans is None:
        return None, {}
    swept = [r for r in act.values() if isinstance(r, dict) and r.get("day")]
    # The NARROWEST window and the OLDEST day across the sweep: a mixed cache is
    # only as good as its weakest record, and claiming otherwise is how a
    # partial refresh reads as a full one.
    horizon = min((r.get("window_days") or 0) for r in swept) if swept else 0
    days = [datetime.date.fromisoformat(r["day"]) for r in swept]
    age = (today - min(days)).days if days else None
    meta = {"window_days": horizon, "age_days": age}

    out = {}
    for c in chans:
        rec = act.get(c["id"])
        if not rec:
            out[c["name"]] = None          # in the channel list, never swept
            continue
        ts = rec.get("last_human_ts")
        d = None
        if ts:
            # max(0, ...): a timestamp slightly ahead of the clock would read as
            # negative age and sort as impossibly fresh.
            d = max(0, (today - datetime.datetime.fromtimestamp(ts, UTC).date()).days)
        out[c["name"]] = Act(d, bool(rec.get("last_human_unknown")),
                             c.get("num_members"), bool(c.get("is_archived")))
    return out, meta


def build(window, cache_dir):
    """(rows, chapters, slack_seen, unparsed) — one read of each source."""
    chapters, _last_row, _layout = read_chapters()
    events, unparsed, undated = last_events()
    today = datetime.datetime.now(UTC).date()
    slack, meta = slack_activity(cache_dir, today, window)
    # A sweep that looked back 90 days cannot answer a 180-day question, and a
    # sweep N days old has not seen the last N days. Clamp to what the cache can
    # actually support and report the clamp, rather than letting an
    # unanswerable window turn into a queue full of false silence.
    # The horizon bounds what ABSENCE can prove, not what a found message says.
    # A sweep that looked back 90 days and FOUND a human message 128 days ago
    # has measured 128 days exactly; clamping that to 90 would report a known
    # date as unknown. But the same sweep finding NOTHING proves only "quiet for
    # at least 90 days" — it cannot answer a 180-day question.
    horizon = meta.get("window_days") or 0
    age = meta.get("age_days") or 0

    rows = []
    for ch in chapters:
        key = fold_city(ch["city"])
        when, n = events.get(key, (None, 0))
        e_days = (today - when).days if when else None
        pub = (ch.get("public") or "").lstrip("#")
        # Blank and NO_RESOURCE are OPPOSITES, and sync_chapters turns on the
        # distinction: blank means nobody has looked, `none` means a human
        # checked and said there is no room. Both stay out of the queue, for
        # different reasons, and the report says which.
        room = ("declared-none" if pub == NO_RESOURCE else
                "unset" if not pub else "named")
        s_days, s_unknown, members, archived, measured = (None, False, None, False, False)
        if slack is not None and room == "named":
            rec = slack.get(pub)
            if rec is None:
                pass                      # in the sheet, not in the sweep
            else:
                s_days, s_unknown = rec.days, rec.unknown
                members, archived = rec.members, rec.archived
                measured = True

        # Absence only proves quiet as far back as the sweep looked, offset by
        # how long ago it ran: the last `age` days were never observed at all.
        absence_proves = max(0, horizon - age) if measured else 0
        absence_is_enough = (s_days is None and not s_unknown
                             and absence_proves >= window)

        # Every way of NOT knowing, enumerated. `quiet` requires that none hold.
        if slack is None:
            unknown_why = "no activity cache at all"
        elif measured and s_days is None and not s_unknown and not absence_is_enough:
            unknown_why = ("found no human message, but the sweep only proves "
                           "%d day(s) of quiet (looked back %d, ran %d day(s) "
                           "ago) — short of the %d-day window"
                           % (absence_proves, horizon, age, window))
        elif room == "declared-none":
            unknown_why = "declares no Slack room"
        elif room == "unset":
            unknown_why = "no Slack Channel on the sheet — nobody has looked yet"
        elif not measured:
            unknown_why = ("in the channel list but not in today's activity sweep "
                           "(interrupted, stale, or a narrower --days window)")
        elif archived:
            unknown_why = "channel is archived — never swept"
        elif s_unknown:
            unknown_why = ("traffic but no human message in the scanned window "
                           "(a capped scan on a busy room, or joins only)")
        elif n and e_days is None:
            unknown_why = "%d event(s) on record but none with a parsable date" % n
        else:
            unknown_why = None

        alive = ((e_days is not None and e_days <= window)
                 or (s_days is not None and s_days <= window))
        if alive:
            verdict = ALIVE
        elif unknown_why is not None:
            verdict = CANNOT_SAY
        else:
            verdict = QUIET
        rows.append({"city": ch["city"], "status": ch.get("status", ""),
                     "merged_into": ch.get("merged_into", ""),
                     "ops_notes": ch.get("ops_notes", ""),
                     "events": n, "event_days": e_days, "slack_days": s_days,
                     "members": members, "verdict": verdict,
                     "why": unknown_why, "undated": key in undated})
    return rows, chapters, slack is not None, unparsed, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help="days within which a signal counts as activity "
                         "(default %d). A cache swept with a NARROWER --days "
                         "than this cannot answer the question, and those "
                         "chapters are reported as unknown." % DEFAULT_WINDOW)
    ap.add_argument("--untriaged", action="store_true",
                    help="only chapters whose Status cell is blank")
    ap.add_argument("--cache", default=DEFAULT_CACHE,
                    help="aaif-audit-slack cache dir (default %s)" % DEFAULT_CACHE)
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)

    rows, chapters, have_slack, unparsed, meta = build(a.window, a.cache)
    if have_slack:
        proves = max(0, (meta.get("window_days") or 0) - (meta.get("age_days") or 0))
        print("NOTE: activity sweep looked back %d day(s), ran %d day(s) ago — "
              "silence in it proves %d day(s) of quiet%s."
              % (meta.get("window_days") or 0, meta.get("age_days") or 0, proves,
                 "" if proves >= a.window
                 else "; short of --window %d, so chapters with no message found "
                      "are reported as unknown rather than quiet" % a.window),
              file=sys.stderr)
    if not have_slack:
        print("WARNING: no usable cache in %s — the Slack signal is MISSING, "
              "not silent. Run aaif-audit-slack's audit_activity.py first; "
              "until then no chapter is proposed for review on Slack evidence."
              % a.cache, file=sys.stderr)
    if unparsed:
        print("WARNING: %d %s row(s) have a Date this cannot parse "
              "unambiguously. Those chapters are reported as unknown, never "
              "quiet." % (unparsed, PAST_EVENTS_TAB), file=sys.stderr)

    live, retired, by_status = census_of(chapters)
    print("%d live chapter(s), %d retired — cap is %d, headroom %d"
          % (live, retired, CHAPTER_CAP, max(0, CHAPTER_CAP - live)))
    print("Status: " + ", ".join("%s=%d" % (k or "(blank)", v)
                                 for k, v in sorted(by_status.items())))
    odd = unknown_statuses(by_status)
    if odd:
        # redact_name, not the raw cell: Status is hand-edited free text with no
        # vocabulary constraint, so whatever an operator typed ends up here — and
        # this report gets pasted into tickets.
        print("NOT in the vocabulary, counted as LIVE: "
              + ", ".join("%s (%d)" % (redact_name(st), n) for st, n in odd))
    print()

    shown = [r for r in rows if not a.untriaged or not r["status"]]
    # Unknown member count sorts LAST, not as 0: this list is ordered
    # smallest-room-first ("retire the emptiest"), and sorting an unmeasurable
    # room to the top presents the one chapter nobody could measure as the most
    # obviously dead.
    quiet = sorted((r for r in shown if r["verdict"] == QUIET),
                   key=lambda r: (r["members"] is None,
                                  r["members"] if r["members"] is not None else 0,
                                  r["city"]))
    awake = [r for r in shown if r["verdict"] == ALIVE]
    cannot_say = [r for r in shown if r["verdict"] == CANNOT_SAY]

    print("QUIET on the Slack signal, with no dated event (%d) — the review "
          "queue, NOT a decision." % len(quiet))
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
                 r["status"] or "(untriaged)", redact_name(r["merged_into"]) if r["merged_into"] else ""))
        # The operator's own note on the row, quoted so it travels with the
        # verdict. Free text: it is shown, never acted on.
        if r["ops_notes"]:
            print("  %-22s   ops notes: %s" % ("", redact_text(r["ops_notes"])))

    if cannot_say:
        print("\nCANNOT SAY (%d) — no activity found, but the evidence is "
              "missing rather than\nnegative, so these are NOT proposed for "
              "review:" % len(cannot_say))
        for r in sorted(cannot_say, key=lambda x: x["city"]):
            print("  %-22s %s" % (r["city"], r["why"]))

    print("\nACTIVE on at least one signal (%d): %s"
          % (len(awake), ", ".join(sorted(r["city"] for r in awake))))
    print("\nReport only — no Status cell was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
