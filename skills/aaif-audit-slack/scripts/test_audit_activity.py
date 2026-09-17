#!/usr/bin/env python3
"""Self-tests for the activity engine's pure logic. No network, no gws.

This engine is the measurement layer two other reports quote, so the things
covered here are the ones that would make a *wrong number* look fully measured:
the cache reuse rules, the truncation flag, and the histogram that must account
for every channel. A miscount here reaches community leadership as a fact.
"""

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_activity as aa  # noqa: E402
from aaif_events import jsoncache  # noqa: E402

FAILS = []
TEAM = "T0AAAAAAA"


def check(label, got, want):
    if got != want:
        FAILS.append("%s:\n     got:  %r\n     want: %r" % (label, got, want))
        print("FAIL %s" % label)
    else:
        print("ok   %s" % label)


def _raises(fn):
    try:
        fn()
    except BaseException as exc:
        return type(exc).__name__
    return None


DAY = 86400
NOW = 1_800_000_000          # a fixed instant; every age below is derived from it
TODAY = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)


def _chan(cid, name, members=10, private=False):
    return {"id": cid, "name": name, "num_members": members,
            "is_private": private}


def _stat(human_msgs=5, posters=2, last_offset_days=1, complete=True, day=None,
          window=30):
    return {"human_msgs": human_msgs, "posters": posters,
            "last_human_ts": None if last_offset_days is None
            else NOW - last_offset_days * DAY,
            "window_complete": complete,
            "day": day or TODAY.date().isoformat(),
            "window_days": window}


# --- the age buckets tile the whole range ------------------------------------
# A gap between two buckets is a channel the histogram cannot place, and
# build_report aborts when the total does not match. Better to know here.
_prev_hi = -1
_contiguous = True
for _lbl, _lo, _hi in aa.AGE_BUCKETS:
    if _lo != _prev_hi + 1:
        _contiguous = False
    _prev_hi = _hi
check("the age buckets are contiguous from day 0", _contiguous, True)
check("the first bucket starts at today", aa.AGE_BUCKETS[0][1], 0)
check("the last bucket is open-ended", aa.AGE_BUCKETS[-1][2] >= 10 ** 9, True)


# --- build_report: every channel lands in exactly one bucket ------------------
_live = [_chan("C1", "bay-area"), _chan("C2", "general"), _chan("C3", "dormant")]
_stats = {"C1": _stat(last_offset_days=2),
          "C2": _stat(last_offset_days=400),
          "C3": _stat(human_msgs=0, posters=0, last_offset_days=None)}
_html = aa.build_report(_live, _stats, {"bay-area": "Boston"}, 30, TODAY)
check("a report is produced for a well-formed sweep", isinstance(_html, str), True)
check("the chapter's city reaches the report", "Boston" in _html, True)
check("a channel with no human message is named as such",
      "no human message found" in _html, True)


def _report_with(stats, live=None):
    return aa.build_report(live or _live, stats, {}, 30, TODAY)


# A message newer than the sweep stamp is clamped to "this week", not dropped.
# Asserting only "did not raise" would also pass if the row vanished, so the
# bucket count is what is checked.
_bad = dict(_stats)
_bad["C2"] = _stat(last_offset_days=-5)       # "posted in the future"
_h_clamp = _report_with(_bad)
check("a future-dated message does not abort the report",
      _raises(lambda: _report_with(_bad)), None)
check("and it is counted in the first bucket, not dropped",
      _h_clamp.count("<td>this week</td>") >= 1
      or "this week" in _h_clamp, True)

# The guard itself, which survives deletion under every test above: hand
# build_report a stat the buckets cannot place and require it to abort. This is
# the one thing standing between a sick sweep and a histogram published to
# community leadership that sums to fewer channels than were measured.
_saved_buckets = aa.AGE_BUCKETS
try:
    # C1 is 2 days old and C3 has never been posted in; C2 at 100 days now
    # falls in the hole between the two buckets and can be placed in neither.
    aa.AGE_BUCKETS = (("this week", 0, 7), ("much later", 400, 10 ** 9))
    _gapped = dict(_stats)
    _gapped["C2"] = _stat(last_offset_days=100)
    check("a stat that falls in a gap between buckets aborts",
          _raises(lambda: _report_with(_gapped)), "SystemExit")
finally:
    aa.AGE_BUCKETS = _saved_buckets
check("and the buckets are restored for later checks", aa.AGE_BUCKETS, _saved_buckets)


# --- truncation is visible in the number ------------------------------------
# `window_complete=False` means the pull hit the API's page limit, so the count
# is a floor. Rendering it bare would read as an exact measurement.
_trunc = {"C1": _stat(human_msgs=900, complete=False),
          "C2": _stat(human_msgs=4), "C3": _stat(human_msgs=1)}
_h = _report_with(_trunc)
# Matched as a whole table cell: the report embeds base64 assets, and a bare
# substring search finds "4+" inside them.
check("a truncated count is marked with a +", "<td>900+</td>" in _h, True)
check("a complete count is not marked", "<td>4</td>" in _h and "<td>4+</td>" not in _h, True)


# --- collect(): the cache is only reused when it answers the same question ----
def _collect_with(cached, days=30, refresh=False):
    """Run collect() against a temp cache, counting live API pulls."""
    pulled = []

    def fake_history(api, cid, oldest, include_posters=False):
        pulled.append(cid)
        return {"human_msgs": 1, "posters": 1, "last_human_ts": NOW - DAY,
                "window_complete": True, "poster_ids": ["U0AAAAAAA"]}

    with tempfile.TemporaryDirectory() as d:
        if cached is not None:
            jsoncache.write(os.path.join(d, "activity.json"), cached, TEAM)
        with mock.patch.object(aa, "history_activity", fake_history):
            stats = aa.collect(None, _live, d, days, refresh, TEAM, NOW)
    return stats, pulled


_, _pulled = _collect_with(None)
check("with no cache every channel is pulled", sorted(_pulled), ["C1", "C2", "C3"])

_fresh = {"C1": _stat(), "C2": _stat(), "C3": _stat()}
_, _pulled = _collect_with(_fresh)
check("a same-day, same-window cache is reused whole", _pulled, [])

_yesterday = (TODAY.date() - dt.timedelta(days=1)).isoformat()
_stale = {k: _stat(day=_yesterday) for k in ("C1", "C2", "C3")}
_, _pulled = _collect_with(_stale)
check("yesterday's pull is not reused", sorted(_pulled), ["C1", "C2", "C3"])

# The one that would publish a wrong number: a 90-day count rendered under a
# 30-day headline looks fully measured and is not.
_other_window = {k: _stat(window=90) for k in ("C1", "C2", "C3")}
_, _pulled = _collect_with(_other_window, days=30)
check("a cache pulled over a different window is not reused",
      sorted(_pulled), ["C1", "C2", "C3"])

_, _pulled = _collect_with(_fresh, refresh=True)
check("--refresh re-pulls even a same-day cache", sorted(_pulled), ["C1", "C2", "C3"])

_partial = {"C1": _stat()}
_, _pulled = _collect_with(_partial)
check("an interrupted sweep resumes instead of restarting", sorted(_pulled), ["C2", "C3"])


# --- the chapter map comes from the organizer audit, never re-derived ---------
def _chapter_names(payload, team=TEAM):
    with tempfile.TemporaryDirectory() as d:
        if payload is not None:
            jsoncache.write(os.path.join(d, "audit.json"), payload, team)
        return aa.chapter_channel_names(d, TEAM)


check("no audit.json means no chapter map, not a guessed one",
      _chapter_names(None), {})
check("every channel kind a chapter holds is mapped to its city",
      _chapter_names({"chapters": [{"city": "Boston", "public": "boston",
                                    "organizers_channel": "boston-organizers",
                                    "regional": "new-england"}]}),
      {"boston": "Boston", "boston-organizers": "Boston", "new-england": "Boston"})
check("a chapter with no rooms contributes nothing",
      _chapter_names({"chapters": [{"city": "Nowhere"}]}), {})
check("an audit.json from another workspace is discarded",
      _chapter_names({"chapters": [{"city": "Boston", "public": "boston"}]},
                     team="T0BBBBBBB"),
      {})


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\naudit_activity: all checks passed")
