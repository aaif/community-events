#!/usr/bin/env python3
"""Self-tests for fetch_local_champs.py. No network, no Slack.

Covers what would be dangerous or misleading if wrong: an `ok:false` response
must raise rather than read as "the channel was quiet", a malformed page must
not be mistaken for the end of the channel, `write_0600` must refuse a path
outside `.pulse-cache/`, and a rewrite of an already-existing cache file must
still end up 0600 rather than keeping whatever mode the old file had.
"""
import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import fetch_local_champs as flc  # noqa: E402
from aaif_events.slack import SlackError  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


class _FakeApi:
    def __init__(self, pages=None, users=None, scopes_ok=True):
        self._pages = pages or {}
        self._users = users or {}
        self._scopes_ok = scopes_ok

    def require_scopes(self, *needed):
        if not self._scopes_ok:
            raise SystemExit("missing scopes: %s" % ", ".join(needed))

    def paged(self, method, key, **kw):
        pages = self._pages.get(method)
        if pages is None:
            raise AssertionError("unexpected method %r" % method)
        if pages == "malformed":
            raise SlackError(method, "malformed_page", "ok:true but no %r key" % key)
        for item in pages:
            yield item

    def ok(self, method, **kw):
        if method == "users.info":
            uid = kw["user"]
            if uid not in self._users:
                raise SlackError(method, "user_not_found")
            return {"user": self._users[uid]}
        raise AssertionError("unexpected method %r" % method)


# --- find_channel: matches by name, ignores others -------------------------
api = _FakeApi(pages={"conversations.list": [
    {"id": "C1", "name": "general"}, {"id": "C2", "name": "local-champs"},
]})
check("find_channel matches by name", flc.find_channel(api, "local-champs"), "C2")
check("find_channel misses cleanly", flc.find_channel(api, "nope"), None)

# --- fetch_messages: drops bot/subtype messages, keeps human ones ----------
api = _FakeApi(pages={"conversations.history": [
    {"ts": "1.1", "user": "U1", "text": "hello"},
    {"ts": "1.2", "user": "U2", "text": "joined", "subtype": "channel_join"},
    {"ts": "1.3", "bot_id": "B1", "text": "beep"},
]})
msgs = flc.fetch_messages(api, "C2", 0)
check("fetch_messages keeps only human messages",
      [m["ts"] for m in msgs], ["1.1"])

# --- fetch_messages: a malformed page raises instead of reading as empty ---
api = _FakeApi(pages={"conversations.history": "malformed"})
try:
    flc.fetch_messages(api, "C2", 0)
    check("malformed page raises SlackError", "no exception", "SlackError")
except SlackError:
    check("malformed page raises SlackError", "raised", "raised")

# --- resolve_names: known user resolves, unknown user falls back to id -----
api = _FakeApi(users={"U1": {"real_name": "Ada Lovelace"}})
names = flc.resolve_names(api, ["U1", "U9"])
check("resolve_names resolves a known user", names["U1"], "Ada Lovelace")
check("resolve_names falls back to the id for an unknown user", names["U9"], "U9")

# --- write_0600: refuses a path outside .pulse-cache/ -----------------------
with tempfile.TemporaryDirectory() as tmp:
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        try:
            flc.write_0600("../escape.json", {"x": 1})
            check("write_0600 refuses an outside path", "wrote", "ValueError")
        except ValueError:
            check("write_0600 refuses an outside path", "raised", "raised")

        # --- write_0600: writes 0600 inside .pulse-cache/, dir is 0700 -----
        target = os.path.join(".pulse-cache", "local-champs.json")
        flc.write_0600(target, {"messages": []})
        mode = stat.S_IMODE(os.stat(target).st_mode)
        check("write_0600 writes the file 0600", oct(mode), oct(0o600))
        dir_mode = stat.S_IMODE(os.stat(".pulse-cache").st_mode)
        check("write_0600 sets the directory 0700", oct(dir_mode), oct(0o700))
        with open(target, encoding="utf-8") as fh:
            check("write_0600 writes valid JSON", json.load(fh), {"messages": []})

        # --- write_0600: a pre-existing looser-mode file still ends up 0600 -
        os.chmod(target, 0o644)
        flc.write_0600(target, {"messages": ["again"]})
        mode = stat.S_IMODE(os.stat(target).st_mode)
        check("write_0600 re-tightens an existing file to 0600", oct(mode), oct(0o600))
    finally:
        os.chdir(cwd)

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nfetch_local_champs: all checks passed")
