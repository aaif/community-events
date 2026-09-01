#!/usr/bin/env python3
"""Self-tests for the country-directory-post logic. No network, no Slack, no gws.

What is covered is what would be dangerous or misleading if wrong: that an
existing post (any of the workspace's several real phrasings) is recognised
and never edited or duplicated, that a human-authored post is left alone, and
that the single-room and no-distinct-channel skips actually skip.
"""

import os
import sys
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import post_country_directory as pcd  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


SELF_ID = "USELF"


class _FakeApi:
    def __init__(self, history):
        self._history = history

    def ok(self, method, **kw):
        assert method == "auth.test"
        return {"user_id": SELF_ID}

    def paged(self, method, key, **kw):
        assert method == "conversations.history" and key == "messages"
        return self._history.get(kw["channel"], [])


def _run(chapters, chans, history):
    with _mock.patch.object(pcd, "read_grid", lambda: (None, None, chapters)), \
         _mock.patch.object(pcd.slackmod, "Slack", lambda token=None: _FakeApi(history)), \
         _mock.patch.object(pcd.slackmod, "channels", lambda api: chans):
        return pcd.collect()


def _chan(name, cid):
    return {"name": name, "id": cid, "is_private": False, "is_archived": False}


# --- a channel with no directory post at all -> create, with every mention ----
chapters = [{"city": "Nairobi", "country": "Kenya",
            "current": {"Country Channel": "kenya", "Slack Channel": "nairobi"}}]
chans = [_chan("kenya", "CKE"), _chan("nairobi", "CNR")]
rows, skipped = _run(chapters, chans, {})
check("no existing post -> create", (rows[0]["action"], rows[0]["mentions"]),
      ("create", ["<#CNR>"]))
check("nothing skipped", skipped, [])


# --- a self-authored post already covers everyone -> up to date ---------------
history = {"CIN": [{"user": SELF_ID, "text":
                    ":wave: Looking for your local AAIF community? This country "
                    "has 1 chapter — join your city's channel: <#CDE>"}]}
chapters = [{"city": "Berlin", "country": "Germany",
            "current": {"Country Channel": "germany", "Slack Channel": "berlin"}}]
chans = [_chan("germany", "CIN"), _chan("berlin", "CDE")]
rows, _ = _run(chapters, chans, history)
check("fully-covered self post -> up to date", rows[0]["action"], "up to date")


# --- once the union covers every wanted city, the history scan STOPS --------
# `paged()` yields lazily here; a poisoned second page raises if pulled at
# all, so this fails loudly if the early-break regresses into a full scan.
def _poison_after(first):
    yield first
    raise AssertionError("scanned past a fully-covering self-authored post")


class _LazyFakeApi(_FakeApi):
    def paged(self, method, key, **kw):
        assert method == "conversations.history" and key == "messages"
        first = self._history[kw["channel"]][0]
        return _poison_after(first)


with _mock.patch.object(pcd, "read_grid", lambda: (None, None, chapters)), \
     _mock.patch.object(pcd.slackmod, "Slack", lambda token=None: _LazyFakeApi(history)), \
     _mock.patch.object(pcd.slackmod, "channels", lambda api: chans):
    rows, _ = pcd.collect()
check("a fully-covering post stops the scan before a later page is pulled",
      rows[0]["action"], "up to date")


# --- a self-authored post is missing a NEW chapter -> add-on, never an edit ---
history = {"CIN": [{"user": SELF_ID, "text":
                    ":wave: Looking for your local AAIF community? This country "
                    "has 1 chapter — join your city's channel: <#CDE>"}]}
chapters = [
    {"city": "Berlin", "country": "Germany",
     "current": {"Country Channel": "germany", "Slack Channel": "berlin"}},
    {"city": "Stuttgart", "country": "Germany",
     "current": {"Country Channel": "germany", "Slack Channel": "stuttgart"}},
]
chans = [_chan("germany", "CIN"), _chan("berlin", "CDE"), _chan("stuttgart", "CST")]
rows, _ = _run(chapters, chans, history)
check("a new chapter -> add-on, not a rewrite", rows[0]["action"], "add-on")
check("the add-on names only the NEW mention", rows[0]["missing"], ["<#CST>"])
check("post_text is a short addendum, not the original template",
      rows[0]["post_text"].startswith(":wave: New chapter channel"), True)


# --- a human-authored post is left alone no matter what ------------------------
history = {"CIN": [{"user": "UHUMAN", "text":
                    ":wave: Looking for your local AAIF community? This "
                    "country has 1 chapter — join <#CDE>"}]}
chapters = [{"city": "Berlin", "country": "Germany",
            "current": {"Country Channel": "germany", "Slack Channel": "berlin"}}]
chans = [_chan("germany", "CIN"), _chan("berlin", "CDE")]
rows, _ = _run(chapters, chans, history)
check("a human's post is reported, not touched",
      rows[0]["action"], "human-authored — not touched")
check("no post_text is generated for it", rows[0]["post_text"], None)


# --- single-room variants are skipped, not posted to ---------------------------
chapters = [{"city": "Singapore", "country": "Singapore",
            "current": {"Country Channel": "singapore", "Slack Channel": "singapore"}}]
chans = [_chan("singapore", "CSG")]
rows, skipped = _run(chapters, chans, {})
check("a single-room channel produces no row", rows, [])
check("it is reported as skipped, with a reason",
      skipped, [("singapore", "single-room variant — nothing separate to "
                              "point members at")])


# --- two cities sharing one channel are mentioned ONCE, not twice -------------
chapters = [
    {"city": "San Francisco", "country": "United States",
     "current": {"Country Channel": "united-states", "Slack Channel": "bay-area"}},
    {"city": "Silicon Valley", "country": "United States",
     "current": {"Country Channel": "united-states", "Slack Channel": "bay-area"}},
]
chans = [_chan("united-states", "CUS"), _chan("bay-area", "CBA")]
rows, _ = _run(chapters, chans, {})
check("a shared channel is mentioned once", rows[0]["mentions"], ["<#CBA>"])


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\npost_country_directory: all checks passed")
