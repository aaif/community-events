#!/usr/bin/env python3
"""Self-tests for the country-directory-post logic. No network, no Slack, no gws.

What is covered is what would be dangerous or misleading if wrong: that an
existing post (any of the workspace's several real phrasings) is recognised
and never edited or duplicated, that a human-authored post is left alone, and
that the single-room and no-distinct-channel skips actually skip.
"""

import json as _json
import os
import sys
import tempfile as _tempfile
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


# --- --json-out: the text report as data, same buckets, subject = channel ----
# build_findings() is pure over what collect() returned, so the page can
# never show a count the log did not print.
_rows = [
    {"channel": "kenya", "channel_id": "CKE", "mentions": ["<#CNR>"],
     "missing": ["<#CNR>"], "action": pcd.ACTION_CREATE, "post_text": "hello"},
    {"channel": "brasil", "channel_id": "CBR", "mentions": ["<#C1>", "<#C2>"],
     "missing": ["<#C2>"], "action": pcd.ACTION_ADD_ON, "post_text": "hi"},
    {"channel": "india", "channel_id": "CIN", "mentions": ["<#C3>"],
     "missing": [], "action": pcd.ACTION_UP_TO_DATE, "post_text": None},
    {"channel": "peru", "channel_id": "CPE", "mentions": ["<#C4>"],
     "missing": [], "action": pcd.ACTION_HUMAN_AUTHORED, "post_text": None},
]
_skipped = [("singapore", pcd.SKIP_NO_CITY_ROOM),
            ("germany", "scanned 2000 messages with no directory post found "
                        "and no more messages read; check by hand")]
_rep = pcd.build_findings(_rows, _skipped, "report")
with _tempfile.TemporaryDirectory() as _d:
    _out = os.path.join(_d, "directory.json")
    _rep.write(_out)
    with open(_out, encoding="utf-8") as _fh:
        _doc = _json.load(_fh)
    check("the JSON is landed 0600", os.stat(_out).st_mode & 0o777, 0o600)
check("the JSON carries the contract's format and this step's name",
      (_doc["format"], _doc["step"], _doc["mode"]), (1, "directory", "report"))
check("the summary is the one-line headline, counts only",
      _doc["summary"],
      "4 live country channel(s): 1 to create, 1 to add-on, 1 already correct, "
      "1 human-authored; 2 skipped")
check("the tiles are the buckets the text report prints",
      [(m["label"], m["value"]) for m in _doc["measured"]],
      [("up to date", 1), ("to create", 1), ("to add-on", 1),
       ("human-authored, not touched", 1), ("skipped", 2)])
check("a channel needing a post is a warn finding on the CHANNEL",
      [(f["kind"], f["subject"], f["severity"]) for f in _doc["findings"]
       if f["kind"] in ("create", "add-on")],
      [("add-on", "#brasil", "warn"), ("create", "#kenya", "warn")])
check("the post text itself stays out of the JSON",
      "hello" in _json.dumps(_doc), False)
check("an up-to-date channel is not a finding at all",
      any(f["subject"] == "#india" for f in _doc["findings"]), False)
check("a human-authored post is info: seen, not a task",
      [f["severity"] for f in _doc["findings"] if f["subject"] == "#peru"], ["info"])
check("a benign skip is info; a scan that could not conclude is warn",
      [(f["subject"], f["severity"]) for f in _doc["findings"] if f["kind"] == "skipped"],
      [("#germany", "warn"), ("#singapore", "info")])
check("nothing was written, so the report says so", _doc["written"], False)
check("Report.write() is a no-op without --json-out", _rep.write(None), None)
check("the skip constant is the string collect() really emits",
      pcd.SKIP_NO_CITY_ROOM, "no distinct, live city channel to link")


# --- main() lands the findings file on a normal exit, never on a refusal -------
# The runner reads `written` from this file as its "wrote" signal, so the file
# has to land on exit 0 and on the exit-1 "some posts failed" path, and must
# NOT land when main() refuses. collect()/report()/apply() are the module's
# own boundaries and are mocked whole; the gate logic in between is what runs.
from aaif_events import findings as _findings  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", ".."))
_todo = [r for r in _rows if r["action"] in (pcd.ACTION_CREATE, pcd.ACTION_ADD_ON)]


def _main_with(argv, todo=(), apply_result=(0, []), json_out=None):
    """Run main() with the estate read mocked; (exit code or message, doc, #applied)."""
    applied = []

    def fake_apply(todo_, token):
        applied.append(len(todo_))
        return apply_result

    with _tempfile.TemporaryDirectory() as d:
        path = json_out or os.path.join(d, "directory.json")
        with _mock.patch.object(pcd, "collect", lambda: (list(_rows), list(_skipped))), \
             _mock.patch.object(pcd, "report", lambda rows, skipped: list(todo)), \
             _mock.patch.object(pcd, "write_token", lambda *a, **k: "xoxp-test"), \
             _mock.patch.object(pcd, "apply", fake_apply), \
             _mock.patch.object(sys, "argv",
                                ["post_country_directory.py", "--json-out", path] + argv), \
             _ctx.redirect_stdout(_io.StringIO()):
            try:
                code = pcd.main()
            except SystemExit as exc:
                code = exc.code
        return code, _findings.read(path), applied


_code, _doc, _applied = _main_with([], todo=_todo)
check("main: report mode lands the JSON on exit 0, nothing posted",
      (_code, _doc["step"], _doc["mode"], _doc["written"], _applied),
      (0, "directory", "report", False, []))
_code, _doc, _applied = _main_with(["--write"], todo=[])
check("main: --write with nothing to do lands the JSON, written=False",
      (_code, _doc["mode"], _doc["written"], _applied), (0, "write", False, []))
_code, _doc, _applied = _main_with(["--write", "--i-have-approval"], todo=_todo,
                                   apply_result=(2, []))
check("main: an applied post lands written=True on exit 0",
      (_code, _doc["written"], _applied,
       [m["value"] for m in _doc["measured"] if m["label"] == "posted"]),
      (0, True, [2], [2]))
_code, _doc, _applied = _main_with(["--write", "--i-have-approval"], todo=_todo,
                                   apply_result=(1, ["kenya: not_authed"]))
check("main: a failed post still lands the JSON on exit 1, as a bad row on the channel",
      (_code, _doc["written"],
       [(f["subject"], f["detail"], f["severity"]) for f in _doc["findings"]
        if f["kind"] == "post failed"]),
      (1, True, [("#kenya", "not_authed", "bad")]))
_code, _doc, _applied = _main_with(["--write"], todo=_todo)
check("main: --write without approval REFUSES, posts nothing and lands no JSON",
      (isinstance(_code, str) and "REFUSING" in _code, _doc, _applied), (True, None, []))
# The findings file names channels: like every --out, it must be gitignored
# before anything runs. A path in this (public) repo is refused before collect().
_probe = os.path.join(_REPO, "directory-findings-probe.json")
with _mock.patch.object(pcd, "collect",
                        lambda: (_ for _ in ()).throw(AssertionError("collected"))):
    _code, _doc, _applied = _main_with([], json_out=_probe)
check("main: a committable --json-out is refused before any work",
      (isinstance(_code, str) and "REFUSING TO RUN" in _code, os.path.exists(_probe)),
      (True, False))

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\npost_country_directory: all checks passed")
