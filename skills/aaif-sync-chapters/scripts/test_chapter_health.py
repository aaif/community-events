#!/usr/bin/env python3
"""chapter_health's classifier. Plain script; exit 1 on failure.

Every check here is about ONE property: a chapter is only ever proposed for
retirement on POSITIVE evidence of quiet. The report's output is a list of
chapters to delete, so each way of not-knowing gets its own case — and each of
these cases was, at some point in this module's history, silently scored as
silence instead.
"""
import datetime
import os
import sys
import unittest.mock as _mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

import chapter_health as chp  # noqa: E402

FAILS = []
TODAY = datetime.datetime.now(chp.UTC).date()


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def _rec(days_ago=None, unknown=False, window=90, age=0):
    """One activity.json record as audit_activity would write it."""
    ts = None
    if days_ago is not None:
        ts = datetime.datetime.combine(
            TODAY - datetime.timedelta(days=days_ago),
            datetime.time(12, 0), chp.UTC).timestamp()
    return {"last_human_ts": ts, "last_human_unknown": unknown,
            "day": (TODAY - datetime.timedelta(days=age)).isoformat(),
            "window_days": window}


def run(chapters, events=(), act=None, chans=None, window=180):
    """Drive build() with every I/O boundary mocked. Returns {city: verdict}."""
    ev = {chp.fold_city(c): (d, n) for c, d, n in events}
    undated = {chp.fold_city(c) for c, d, n in events if d is None and n}
    caches = {"activity.json": act, "channels.json": chans}

    def fake_cache(path, **kw):
        return caches[os.path.basename(path)]

    with _mock.patch.object(chp, "read_chapters",
                            lambda: (chapters, len(chapters) + 1, {})), \
         _mock.patch.object(chp, "last_events", lambda: (ev, 0, undated)), \
         _mock.patch.object(chp.jsoncache, "read", fake_cache):
        rows, _chaps, seen, _unparsed, _meta = chp.build(window, ".cache")
    return {r["city"]: r["verdict"] for r in rows}, {r["city"]: r for r in rows}


CH = [{"city": "Boston", "status": "", "merged_into": "", "public": "boston",
       "organizers_raw": "", "row": 2}]
CHANS = [{"name": "boston", "id": "C1", "num_members": 10, "is_archived": False}]

# --- the whole point: ignorance never reaches the queue ----------------------

v, _ = run(CH, act={}, chans=CHANS)
check("a channel in channels.json with NO activity record is CANNOT SAY",
      v["Boston"], chp.CANNOT_SAY)

v, _ = run(CH, act={"C1": _rec(days_ago=None, unknown=True)}, chans=CHANS)
check("traffic but no human message (capped scan / joins only) is CANNOT SAY",
      v["Boston"], chp.CANNOT_SAY)

v, _ = run(CH, act=None, chans=None)
check("no activity cache at all is CANNOT SAY, never a queue full of silence",
      v["Boston"], chp.CANNOT_SAY)

v, _ = run([dict(CH[0], public=chp.NO_RESOURCE)], act={}, chans=CHANS)
check("a chapter that DECLARES no Slack room is CANNOT SAY",
      v["Boston"], chp.CANNOT_SAY)

v, rows = run([dict(CH[0], public="")], act={}, chans=CHANS)
check("a BLANK Slack Channel is CANNOT SAY too", v["Boston"], chp.CANNOT_SAY)
check("...and is reported as unset, not as a declaration",
      "nobody has looked" in rows["Boston"]["why"], True)

v, _ = run(CH, act={"C1": _rec(days_ago=None)}, chans=[
    {"name": "boston", "id": "C1", "num_members": 10, "is_archived": True}])
check("an ARCHIVED channel is never swept, so it is CANNOT SAY",
      v["Boston"], chp.CANNOT_SAY)

# Absence only proves as far back as the sweep looked, minus how stale it is.
v, _ = run(CH, act={"C1": _rec(days_ago=None, window=90)}, chans=CHANS, window=180)
check("no message found, but a 90-day sweep cannot answer a 180-day window",
      v["Boston"], chp.CANNOT_SAY)
v, _ = run(CH, act={"C1": _rec(days_ago=None, window=200)}, chans=CHANS, window=180)
check("no message found and a 200-day sweep CAN answer it — that is quiet",
      v["Boston"], chp.QUIET)
v, _ = run(CH, act={"C1": _rec(days_ago=None, window=200, age=30)}, chans=CHANS,
           window=180)
check("...but not once the sweep is 30 days stale (200-30 < 180)",
      v["Boston"], chp.CANNOT_SAY)

# --- a FOUND message is a measurement, whatever the horizon ------------------

v, _ = run(CH, act={"C1": _rec(days_ago=128, window=90)}, chans=CHANS, window=180)
check("a message found 128d ago is ALIVE at window 180, even on a 90d sweep",
      v["Boston"], chp.ALIVE)
v, _ = run(CH, act={"C1": _rec(days_ago=252, window=90)}, chans=CHANS, window=180)
check("a message found 252d ago is QUIET at window 180", v["Boston"], chp.QUIET)

# --- either signal alone keeps a chapter alive ------------------------------

v, _ = run(CH, events=[("Boston", TODAY - datetime.timedelta(days=10), 1)],
           act={"C1": _rec(days_ago=900, window=1000)}, chans=CHANS)
check("a recent EVENT alone is enough to be alive", v["Boston"], chp.ALIVE)
v, _ = run(CH, act={"C1": _rec(days_ago=5)}, chans=CHANS)
check("a recent SLACK message alone is enough to be alive", v["Boston"], chp.ALIVE)

# --- the window boundary ----------------------------------------------------

v, _ = run(CH, act={"C1": _rec(days_ago=180, window=200)}, chans=CHANS, window=180)
check("exactly at the window is alive", v["Boston"], chp.ALIVE)
v, _ = run(CH, act={"C1": _rec(days_ago=181, window=200)}, chans=CHANS, window=180)
check("one day past it is quiet", v["Boston"], chp.QUIET)

# --- events with no parsable date never push a chapter into the queue -------

v, rows = run(CH, events=[("Boston", None, 3)],
              act={"C1": _rec(days_ago=400, window=500)}, chans=CHANS)
check("3 events with no parsable date hold the chapter OUT of the queue",
      v["Boston"], chp.CANNOT_SAY)
check("...and the reason names the undated events",
      "none with a parsable date" in rows["Boston"]["why"], True)

# --- the city join folds, like every other cross-tab join in this skill -----

v, _ = run(CH, events=[("boston ", TODAY - datetime.timedelta(days=3), 1)],
           act={"C1": _rec(days_ago=900, window=1000)}, chans=CHANS)
check("a Past Events city spelled differently still credits the chapter",
      v["Boston"], chp.ALIVE)

# --- the date parser refuses rather than guessing or crashing ---------------

check("an out-of-range day is None, not an exception", chp._date("32/1/2026"), None)
check("a 2-digit year is refused, not read as year 26", chp._date("13/4/26"), None)
check("an ambiguous slash date stays ambiguous", chp._date("03/04/2026"), None)
check("an unambiguous slash date resolves",
      chp._date("25/12/2026"), datetime.date(2026, 12, 25))
check("the ISO form resolves", chp._date("2026-09-14"), datetime.date(2026, 9, 14))
check("a month-name form resolves", chp._date("Sep 3, 2026"), datetime.date(2026, 9, 3))
check("junk is None", chp._date("whenever"), None)

# --- a row lands in exactly one bucket --------------------------------------

_, rows = run(CH, act={"C1": _rec(days_ago=5)}, chans=CHANS)
check("verdict is a single value, not two booleans that could both be set",
      rows["Boston"]["verdict"] in (chp.ALIVE, chp.QUIET, chp.CANNOT_SAY), True)

# --- --json-out: the same report as data, for the sync runner's page --------
import json  # noqa: E402
import tempfile  # noqa: E402
from aaif_events import findings  # noqa: E402

_, rows = run([CH[0], dict(CH[0], city="Pune", public="pune"),
               dict(CH[0], city="Oslo", public="oslo")],
              # One horizon across the sweep: the cache's window is its NARROWEST
              # record, so a 90-day record beside a 200-day one would turn Boston's
              # silence into CANNOT SAY.
              act={"C1": _rec(days_ago=None, window=200),
                   "C2": _rec(days_ago=5, window=200),
                   "C3": _rec(days_ago=None, unknown=True, window=200)},
              chans=CHANS + [{"name": "pune", "id": "C2", "num_members": 4, "is_archived": False},
                             {"name": "oslo", "id": "C3", "num_members": 7, "is_archived": False}])
rep = chp.build_findings(
    findings.Report("health"),
    quiet=[r for r in rows.values() if r["verdict"] == chp.QUIET],
    awake=[r for r in rows.values() if r["verdict"] == chp.ALIVE],
    cannot_say=[r for r in rows.values() if r["verdict"] == chp.CANNOT_SAY],
    untriaged=3)
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "health.json")
    rep.write(path)
    doc = json.load(open(path, encoding="utf-8"))
check("health JSON has the contract's format, step and mode",
      (doc["format"], doc["step"], doc["mode"], doc["written"]), (1, "health", "report", False))
check("health summary is the counts", doc["summary"], "1 quiet, 1 cannot say, 1 active; 3 untriaged")
check("health tiles are quiet / active / cannot say / untriaged",
      [(m["label"], m["value"]) for m in doc["measured"]],
      [("quiet", 1), ("active", 1), ("cannot say", 1), ("untriaged", 3)])
by_kind = {f["kind"]: f for f in doc["findings"]}
check("the QUIET chapter is a warn finding with the table's cells",
      (by_kind["quiet"]["subject"], by_kind["quiet"]["severity"], by_kind["quiet"]["detail"]),
      ("Boston", "warn", "events 0 / last event never / last slack never / members 10"))
check("the CANNOT SAY chapter is an info finding carrying its why",
      (by_kind["cannot say"]["subject"], by_kind["cannot say"]["severity"],
       "no human message" in by_kind["cannot say"]["detail"]), ("Oslo", "info", True))
check("an ACTIVE chapter is a tile, not a finding", "Pune" in json.dumps(doc["findings"]), False)

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nchapter_health: all checks passed")
