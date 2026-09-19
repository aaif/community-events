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

    def require_scopes(self, *a):
        pass

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


# --- a single-room variant (Country Channel IS the chapter's own channel) is
# skipped via the generic "no distinct channel" check, not a hardcoded list ---
chapters = [{"city": "Singapore", "country": "Singapore",
            "current": {"Country Channel": "singapore", "Slack Channel": "singapore"}}]
chans = [_chan("singapore", "CSG")]
rows, skipped = _run(chapters, chans, {})
check("a single-room channel produces no row", rows, [])
check("it is reported as skipped, with a reason",
      skipped, [("singapore", "no distinct, live city channel to link")])

# --- ...and it stops being skipped the moment the country gets its own city
# room — this is exactly the drift a hardcoded skip list would have missed ----
chapters = [{"city": "Singapore", "country": "Singapore",
            "current": {"Country Channel": "singapore", "Slack Channel": "raffles"}}]
chans = [_chan("singapore", "CSG"), _chan("raffles", "CRF")]
rows, skipped = _run(chapters, chans, {})
check("a real city channel is no longer treated as single-room",
      (rows[0]["action"], rows[0]["mentions"]), ("create", ["<#CRF>"]))
check("and nothing is skipped", skipped, [])


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


# --- a brand-new post for a channel shared by several countries opens with
# the multi-country label, not the single-country "This country has..." ------
# All four Nordic countries: MULTI_COUNTRY_LABELS matches the exact set, not
# a subset — a channel serving only two of the four falls through to the
# generic "X / Y" join instead, which is exercised right after this.
chapters = [
    {"city": "Copenhagen", "country": "Denmark",
     "current": {"Country Channel": "nordics", "Slack Channel": "copenhagen"}},
    {"city": "Oslo", "country": "Norway",
     "current": {"Country Channel": "nordics", "Slack Channel": "oslo"}},
    {"city": "Helsinki", "country": "Finland",
     "current": {"Country Channel": "nordics", "Slack Channel": "helsinki"}},
    {"city": "Stockholm", "country": "Sweden",
     "current": {"Country Channel": "nordics", "Slack Channel": "stockholm"}},
]
chans = [_chan("nordics", "CNO"), _chan("copenhagen", "CCO"), _chan("oslo", "COS"),
        _chan("helsinki", "CHE"), _chan("stockholm", "CST")]
rows, _ = _run(chapters, chans, {})
check("a multi-country channel's post opens with the region label",
      rows[0]["post_text"].startswith(
          ":wave: Looking for your local AAIF community? "
          "the Nordic countries have 4 chapters"),
      True)

# A channel shared by countries with no MULTI_COUNTRY_LABELS entry falls back
# to joining the sorted country names.
chapters = [
    {"city": "Copenhagen", "country": "Denmark",
     "current": {"Country Channel": "benelux", "Slack Channel": "copenhagen"}},
    {"city": "Brussels", "country": "Belgium",
     "current": {"Country Channel": "benelux", "Slack Channel": "brussels"}},
]
chans = [_chan("benelux", "CBX"), _chan("copenhagen", "CCO"), _chan("brussels", "CBR")]
rows, _ = _run(chapters, chans, {})
check("an unmapped multi-country group falls back to a plain country join",
      rows[0]["post_text"].startswith(
          ":wave: Looking for your local AAIF community? "
          "Belgium / Denmark have 2 chapters"),
      True)

chapters = [{"city": "Nairobi", "country": "Kenya",
            "current": {"Country Channel": "kenya", "Slack Channel": "nairobi"}}]
chans = [_chan("kenya", "CKE"), _chan("nairobi", "CNR")]
rows, _ = _run(chapters, chans, {})
check("a single-country channel's post does not name the country",
      rows[0]["post_text"].startswith(
          ":wave: Looking for your local AAIF community? This country has 1 chapter"),
      True)


# --- hitting the scan cap with nothing found -> skipped, never "create" ------
# A directory post that exists but sits past HISTORY_SCAN_CAP messages back
# must never read as "none exists" — that would post a duplicate greeting.
_capped_history = {"CIN": [{"user": "UOTHER", "text": "unrelated message"}]
                   * pcd.HISTORY_SCAN_CAP}
chapters = [{"city": "Berlin", "country": "Germany",
            "current": {"Country Channel": "germany", "Slack Channel": "berlin"}}]
chans = [_chan("germany", "CIN"), _chan("berlin", "CDE")]
rows, skipped = _run(chapters, chans, _capped_history)
check("a capped scan with nothing found produces no row", rows, [])
check("it is reported as skipped, not silently treated as create",
      any(name == "germany" for name, _ in skipped), True)


# --- report() prints the exact text that --write would send, for the two
# actions that actually post something -----------------------------------------
import io as _io  # noqa: E402
import contextlib as _ctx  # noqa: E402

_create_row = {"channel": "kenya", "missing": [], "action": pcd.ACTION_CREATE,
              "post_text": "the create text"}
_addon_row = {"channel": "india", "missing": ["<#C1>"], "action": pcd.ACTION_ADD_ON,
             "post_text": "the add-on text"}
_uptodate_row = {"channel": "japan", "missing": [], "action": pcd.ACTION_UP_TO_DATE,
                "post_text": None}
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    pcd.report([_create_row, _addon_row, _uptodate_row], [])
_out = _buf.getvalue()
check("the create row's post text is printed", "the create text" in _out, True)
check("the add-on row's post text is printed", "the add-on text" in _out, True)
check("an up-to-date row prints no post text (there is none)",
      "None" in _out, False)


# --- apply(): a real join failure stops before posting; a benign race doesn't -
class _Recorder:
    def __init__(self, results):
        self.calls, self._results = [], results

    def __call__(self, token, method, **params):
        self.calls.append((method, params))
        return self._results.get(method, {"ok": True})


_todo = [{"channel": "kenya", "channel_id": "CKE", "action": pcd.ACTION_CREATE,
         "post_text": "hello"}]

_rec = _Recorder({"conversations.join": {"ok": False, "error": "not_authed"}})
_orig = pcd.call_write
pcd.call_write = _rec
try:
    done, failed = pcd.apply(_todo, "token")
finally:
    pcd.call_write = _orig
check("a real join failure is reported and nothing is posted",
      (done, failed, [c[0] for c in _rec.calls]),
      (0, ["kenya: join failed: not_authed"], ["conversations.join"]))

_rec = _Recorder({"conversations.join": {"ok": False, "error": "already_in_channel"}})
pcd.call_write = _rec
try:
    done, failed = pcd.apply(_todo, "token")
finally:
    pcd.call_write = _orig
check("a benign already-in-channel race still posts",
      (done, failed, [c[0] for c in _rec.calls]),
      (1, [], ["conversations.join", "chat.postMessage"]))


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\npost_country_directory: all checks passed")
