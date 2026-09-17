#!/usr/bin/env python3
"""Self-tests for the member engine's pure logic. No network, no gws.

This report goes to community leadership as a description of the workspace, so
the covered behaviours are the ones where a wrong page still looks like a right
one: the completeness cross-check that refuses a short user pull, and the
histogram guard that refuses a chart whose bars do not account for everybody.
Both are `SystemExit` by design — `python -O` strips asserts.
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_members as am  # noqa: E402
from aaif_events import jsoncache  # noqa: E402

FAILS = []
TEAM = "T0AAAAAAA"
TODAY = dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc)


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


def _user(uid, bot=False, app=False, deleted=False):
    return {"id": uid, "is_bot": bot, "is_app_user": app, "deleted": deleted}


def _chan(name, members, general=False):
    return {"id": "C" + name, "name": name, "num_members": members,
            "is_general": general, "is_private": False}


# --- check_buckets: a chart must account for everyone it claims to describe ---
check("buckets that sum to the population pass",
      am.check_buckets([("a", 3), ("b", 7)], 10, "test"), None)
check("buckets that lose records abort",
      _raises(lambda: am.check_buckets([("a", 3)], 10, "test")), "SystemExit")
check("buckets that double-count abort",
      _raises(lambda: am.check_buckets([("a", 8), ("b", 7)], 10, "test")), "SystemExit")
check("an empty population with empty buckets is fine",
      am.check_buckets([], 0, "test"), None)


# --- assert_directory_complete: a short user pull must not be published -------
_HUMANS = [_user("U0AAAAAAA"), _user("U0BBBBBBB"), _user("U0DDDDDDD")]


def _complete(chans, directory):
    return _raises(lambda: am.assert_directory_complete(chans, directory))


check("a directory matching the default channel passes",
      _complete([_chan("general", 3, general=True)], _HUMANS), None)
check("a materially short pull aborts",
      _complete([_chan("general", 30, general=True)], _HUMANS), "SystemExit")

# The 0.9 tolerance, pinned on BOTH sides. The case this replaces compared the
# same three humans against num_members=3 twice — an exact match either way, so
# the tolerance was never on the boundary and could be changed to 1.0 (or
# deleted) with the suite still green. A tolerance nobody tests is a nightly
# run that starts aborting whenever one person deactivates mid-pull, or one
# that publishes a directory missing 40% of the workspace.
# assert_directory_complete counts RECORDS, not distinct identities, so the
# id repeats here rather than inventing nine more — every Slack id in this repo
# has to be one the PII guard knows is synthetic.
_TEN = [_user("U0AAAAAAA") for _ in range(10)]
check("9 humans against a 10-member channel is inside the tolerance",
      _complete([_chan("general", 10, general=True)], _TEN[:9]), None)
check("8 against 10 is outside it and aborts",
      _complete([_chan("general", 10, general=True)], _TEN[:8]), "SystemExit")

# The channel is found by `is_general`, never by name: #general can be renamed,
# and a name lookup then silently skipped the whole cross-check.
check("the default channel is found by flag, not by the name 'general'",
      _complete([_chan("lobby", 30, general=True)], _HUMANS), "SystemExit")
check("a channel merely NAMED general is not the default channel",
      _complete([_chan("general", 30)], _HUMANS), None)

# Like compared with like: num_members counts ACTIVE members only, so bots,
# app users and deactivated accounts must not pad the directory side.
_PADDED = _HUMANS + [_user("U0GONEGONE", bot=True), _user("U0LIVELIVE", app=True),
                     _user("U02BBBBBBBB", deleted=True), _user("USLACKBOT")]
check("bots, app users, deactivated accounts and Slackbot do not pad the count",
      _complete([_chan("general", 30, general=True)], _PADDED), "SystemExit")


# --- "cannot check" is said out loud, never skipped in silence ----------------
def _note_for(chans):
    said = []
    am.assert_directory_complete(chans, _HUMANS, note=said.append)
    return said


check("no default channel at all is reported as uncheckable",
      "could not run" in " ".join(_note_for([_chan("random", 5)])), True)
check("a default channel with no reported size is reported as uncheckable",
      "could not run" in " ".join(_note_for([_chan("general", None, general=True)])), True)
check("a successful check says nothing",
      _note_for([_chan("general", 3, general=True)]), [])


# --- schema drift must surface, not be absorbed ------------------------------
# `is_app_user` is read by bracket access on purpose: a `.get()` default once
# counted app users as active humans, inflating the very number the tripwire
# compares.
check("a directory record missing is_app_user raises rather than miscounting",
      _raises(lambda: am.assert_directory_complete(
          [_chan("general", 3, general=True)],
          [{"id": "U0AAAAAAA", "is_bot": False, "deleted": False}])),
      "KeyError")


# --- cached(): the slow user pull is reused, but only when it is this one -----
def _cached_with(stored, refresh=False, team=TEAM):
    built = []

    def build():
        built.append(1)
        return [_user("U0AAAAAAA")]

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "users.json")
        if stored is not None:
            jsoncache.write(p, stored, team)
        data = am.cached(p, build, refresh=refresh, label="users", team_id=TEAM)
    return data, bool(built)


_, _built = _cached_with(None)
check("with no cache the pull runs", _built, True)
_, _built = _cached_with([_user("U0BBBBBBB")])
check("a stamped cache is reused", _built, False)
_, _built = _cached_with([_user("U0BBBBBBB")], refresh=True)
check("--refresh re-pulls", _built, True)
_, _built = _cached_with([_user("U0BBBBBBB")], team="T0BBBBBBB")
check("a cache from another workspace is re-pulled, not joined in", _built, True)


# --- the default-join list is membership-as-signup, not participation ---------
check("the auto-join channels are named, so they can be reported separately",
      "general" in am.DEFAULT_JOIN, True)
check("webmail domains are classified, so a work domain is not read as personal",
      ("gmail.com" in am.WEBMAIL, "aaif.io" in am.WEBMAIL), (True, False))


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\naudit_members: all checks passed")
