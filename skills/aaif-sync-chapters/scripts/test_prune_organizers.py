#!/usr/bin/env python3
"""Self-tests for the prune buckets. No network, no Slack, no gws.

classify() is the highest-blast-radius logic in the skill: a member lands in
REMOVE purely by falling through the other branches, so a broken email
comparison or name fold silently moves a real organizer into the kick list.
Every bucket boundary is pinned here, with the precedence order explicit.
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import prune_organizers as pr  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


# --- fixtures: one channel, one member per bucket boundary ---------------------
USERS = {
    "U_EMAIL":  {"id": "U_EMAIL", "name": "ada", "real_name": "Ada Lovelace",
                 "profile": {"email": "Ada@X.com"}},        # case-differing email
    "U_NAME":   {"id": "U_NAME", "name": "padme", "real_name": "Padmé Naberrie",
                 "profile": {"email": "other@else.com"}},   # folded-name match
    "U_BOT":    {"id": "U_BOT", "name": "bot", "real_name": "Beep",
                 "is_bot": True, "profile": {"email": "ada@x.com"}},
    "U_GONE":   {"id": "U_GONE", "name": "gone", "real_name": "Ada Lovelace",
                 "deleted": True, "profile": {"email": "ada@x.com"}},
    "U_HANDLE": {"id": "U_HANDLE", "name": "KeptOne", "real_name": "Kept One",
                 "profile": {}},
    "U_KEEPID": {"id": "U_KEEPID", "name": "whoever", "real_name": "Who Ever",
                 "profile": {}},
    "U_STRANGE": {"id": "U_STRANGE", "name": "rando", "real_name": "Total Stranger",
                  "profile": {"email": "rando@nowhere.com"}},
}
INTAKE = [{"email": "ada@x.com", "name": "Ada Lovelace", "city": "Boston"},
          {"email": "padme@x.com", "name": "Padme Naberrie", "city": "Naboo"}]


class _FakeApi:
    def require_scopes(self, *a):
        pass

    def ok(self, method, **params):
        assert method == "users.info"
        return {"user": USERS[params["user"]]}


def run_classify(members, keep=frozenset(), keep_ids=frozenset(),
                 chapters=None, chans=None):
    chapters = chapters if chapters is not None else [
        {"city": "Boston", "current": {"Organizer Channel": "boston-organizers"}}]
    chans = chans if chans is not None else [
        {"name": "boston-organizers", "id": "C1", "is_archived": False}]
    with mock.patch.object(pr.slackmod, "Slack", _FakeApi), \
         mock.patch.object(pr.slackmod, "channels", lambda api: chans), \
         mock.patch.object(pr.slackmod, "members", lambda api, cid: members), \
         mock.patch.object(pr, "read_keeplist",
                           lambda: (set(keep), set(keep_ids), True)), \
         mock.patch.object(pr, "read_grid", lambda c: (None, None, chapters)), \
         mock.patch.object(pr.ao, "read_intake", lambda: (INTAKE, 0)):
        return pr.classify()


def buckets(row):
    return {"organizers": [e[2] for e in row["organizers"]],
            "kept": [e[2] for e in row["kept"]],
            "review": [e[2] for e in row["review"]],
            "remove": [e[2] for e in row["remove"]]}


rows, had, skipped = run_classify(
    ["U_EMAIL", "U_NAME", "U_BOT", "U_GONE", "U_HANDLE", "U_KEEPID", "U_STRANGE"],
    keep={"keptone"}, keep_ids={"U_KEEPID"})
b = buckets(rows[0])
check("case-differing email match -> organizers", b["organizers"], ["U_EMAIL"])
check("folded display-name match -> review, never remove",
      b["review"], ["U_NAME"])
check("the review reason says the match is self-asserted",
      "self-asserted" in rows[0]["review"][0][3], True)
check("bot and deactivated -> kept, even with a matching email",
      set(b["kept"]) >= {"U_BOT", "U_GONE"}, True)
check("keep-list by immutable user id -> kept", "U_KEEPID" in b["kept"], True)
check("keep-list by handle (case-folded) -> kept", "U_HANDLE" in b["kept"], True)
check("everyone else -> remove", b["remove"], ["U_STRANGE"])
check("nothing was skipped when the channel resolves", skipped, [])

# --- precedence: deleted beats email; email beats keep-list ---------------------
rows, _, _ = run_classify(["U_GONE"])
check("a deactivated account with a matching email stays kept ('bot or "
      "deactivated'), not organizers", buckets(rows[0])["kept"], ["U_GONE"])

# --- rows the sheet cannot resolve are reported, not silently dropped ----------
rows, _, skipped = run_classify(
    ["U_EMAIL"],
    chapters=[
        {"city": "Boston", "current": {"Organizer Channel": "boston-organizers"}},
        {"city": "Pune", "current": {"Organizer Channel": "pune-organizers"}},
        {"city": "Oslo", "current": {"Organizer Channel": "none"}},
        {"city": "Lille", "current": {"Organizer Channel": ""}}])
check("a named channel Slack doesn't show lands in skipped",
      skipped, [("Pune", "pune-organizers")])
check("blank and 'none' cells are not skips — nothing was recorded",
      [r["city"] for r in rows], ["Boston"])

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nprune_organizers: all checks passed")
