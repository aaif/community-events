#!/usr/bin/env python3
"""Self-tests for the ops-seed phase. No network, no Slack, no gws.

What is covered is what would be dangerous or misleading if wrong: that the
seed cannot silently widen past organizer channels, that an unreadable private
room is never mistaken for an empty one, and that a disarmed roster seeds
nobody rather than falling back to a hardcoded account.
"""

import os
import sys
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_organizers as ao  # noqa: E402
import provision_channels as prov  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


TABLES = {"organizers": {"Boston": "boston-organizers", "Austin": "austin-organizers"},
          "public": {"Boston": "boston", "Austin": "austin"},
          "regional": {"Boston": "united-states", "Austin": "united-states"}}
OPS = [("a@x.com", "U1", "Ada"), ("b@x.com", "U2", "Bo")]
IDS = {"boston-organizers": "C1", "austin-organizers": "C2",
       "boston": "C3", "united-states": "C4"}


def _seed(members_by_id, tables=TABLES, ops=OPS, ids=IDS):
    def members(api, cid):
        got = members_by_id[cid]
        if isinstance(got, Exception):
            raise got
        return got
    with _mock.patch.object(prov.slackmod, "members", members):
        return prov.plan_ops_seed(None, tables, ops, ids)


# --- scope: organizer rooms only ---------------------------------------------
# A public chapter room and a country room are in `tables` and in `ids`, so a
# seed that widened to them would still "work" — it would just quietly put staff
# into public rooms. Asserting the channel NAMES, not just the count, is what
# catches that.
seed, unreadable = _seed({"C1": [], "C2": [], "C3": [], "C4": []})
check("only organizer channels are seeded",
      sorted(n for n, _, _ in seed), ["austin-organizers", "boston-organizers"])
check("nothing is reported unreadable when every room reads", unreadable, [])

# --- a room already holding everyone is not re-invited ------------------------
seed, _ = _seed({"C1": ["U1", "U2"], "C2": ["U1"], "C3": [], "C4": []})
check("a complete room drops out of the plan entirely",
      [n for n, _, _ in seed], ["austin-organizers"])
check("only the MISSING ops account is proposed",
      [label for _, _, m in seed for _, _, label in m], ["Bo"])

# --- an unreadable private room is reported, never treated as empty -----------
# This is the one that matters most: `conversations.members` answers
# channel_not_found for a private room the token is not in. Reading that as []
# would propose an invite into a room whose membership was never checked.
seed, unreadable = _seed(
    {"C1": prov.slackmod.SlackError("conversations.members", "channel_not_found", ""),
     "C2": ["U1", "U2"], "C3": [], "C4": []})
check("an unreadable room is NOT proposed for seeding", seed, [])
check("an unreadable room is reported instead",
      [n for n, _ in unreadable], ["boston-organizers"])

# --- the sheet's `none` sentinel is not a channel name ------------------------
seed, _ = _seed({"C1": [], "C2": []},
                tables=dict(TABLES, organizers={"Boston": "boston-organizers",
                                                "Nowhere": ao.NO_RESOURCE}))
check("the `none` sentinel is never looked up as a channel",
      [n for n, _, _ in seed], ["boston-organizers"])

# --- a disarmed roster seeds nobody -------------------------------------------
seed, _ = _seed({"C1": [], "C2": []}, ops=[])
check("no ops roster seeds nobody, rather than a default account", seed, [])

# --- identity comes from email, and a deleted account is not an identity ------
_LOOKUP = {"a@x.com": {"id": "U1", "real_name": "Ada"},
           "gone@x.com": {"id": "U9", "real_name": "Gone", "deleted": True},
           "ghost@x.com": {"id": None, "error": "users_not_found"}}
with _mock.patch.object(prov.slackmod, "lookup_emails", lambda api, e: _LOOKUP):
    ops, unresolved = prov.resolve_ops(None, list(_LOOKUP))
check("a live account resolves to (email, id, label)", ops, [("a@x.com", "U1", "Ada")])
check("a deleted account is unresolved, not invited into 87 rooms",
      [e for e, _ in unresolved if e == "gone@x.com"], ["gone@x.com"])
check("an address with no account is reported unresolved",
      [e for e, _ in unresolved if e == "ghost@x.com"], ["ghost@x.com"])

# --- folder pins: idempotency is the whole game -------------------------------
FOLDERS = {"Boston": "https://drive.google.com/drive/folders/1AAAAAAAAAAAAAAAAAA",
           "Austin": "https://drive.google.com/drive/folders/1BBBBBBBBBBBBBBBBBB"}


def _pins(pins_by_id, history_by_id=None, folders=FOLDERS, tables=TABLES):
    history_by_id = history_by_id or {}

    class _Api:
        def call(self, method, **params):
            cid = params["channel"]
            if method == "pins.list":
                got = pins_by_id[cid]
                return ({"ok": False, "error": got} if isinstance(got, str)
                        else {"ok": True, "items": [{"message": {"text": t}} for t in got]})
            if method == "conversations.history":
                return {"ok": True, "messages": [{"text": t, "ts": ts}
                                                 for ts, t in history_by_id.get(cid, [])]}
            raise AssertionError("unexpected method %s" % method)
    return prov.plan_folder_pins(_Api(), tables, folders, IDS)


plan, skipped = _pins({"C1": [], "C2": []})
check("a channel with no pin is planned for a post",
      [(c, ts) for c, _, _, _, ts in plan], [("Austin", None), ("Boston", None)])

# The id, not the URL: the pinned copy here carries a `?usp=sharing` the sheet
# cell does not, and it is the same folder.
plan, _ = _pins({"C1": ["see https://drive.google.com/drive/folders/"
                        "1AAAAAAAAAAAAAAAAAA?usp=sharing"], "C2": []})
check("a pin matching on folder ID (not exact URL) is left alone",
      [c for c, _, _, _, _ in plan], ["Austin"])

# The failure this design exists for: posted, but pins.add failed. A pins-only
# check would post a second copy.
plan, _ = _pins({"C1": [], "C2": []},
                history_by_id={"C1": [("111.222", "folder 1AAAAAAAAAAAAAAAAAA")]})
check("an unpinned message already carrying the link is pinned, not reposted",
      sorted((c, ts) for c, _, _, _, ts in plan), [("Austin", None), ("Boston", "111.222")])

plan, skipped = _pins({"C1": "channel_not_found", "C2": []})
check("a channel whose pins cannot be read is NOT posted into",
      [c for c, _, _, _, _ in plan], ["Austin"])
check("...it is reported instead", [c for c, _ in skipped], ["Boston"])

plan, skipped = _pins({"C2": []}, folders=dict(FOLDERS, Nowhere="https://x/folders/1CCCCCCCCCCCCCCCCCC"),
                      tables=dict(TABLES, organizers={"Austin": "austin-organizers"}))
check("a chapter with no organizer channel is skipped, not guessed at",
      sorted(c for c, _ in skipped), ["Boston", "Nowhere"])

plan, skipped = _pins({"C1": [], "C2": []}, folders={"Boston": "not-a-drive-url"})
check("a folder cell with no Drive id is reported, never posted",
      (plan, [c for c, _ in skipped]), ([], ["Boston"]))

check("the public chapter room is never a pin target",
      "boston" in [c for _, c, _, _, _ in _pins({"C1": [], "C2": []})[0]], False)

# --- history-only mode (no pins:read) — what this estate actually runs --------
def _pins_nr(pins_by_id, history_by_id=None, folders=FOLDERS, tables=TABLES):
    history_by_id = history_by_id or {}

    class _Api:
        def call(self, method, **params):
            if method == "pins.list":
                raise AssertionError("pins.list must NOT be called without pins:read")
            return {"ok": True, "messages": [{"text": t, "ts": ts} for ts, t
                                             in history_by_id.get(params["channel"], [])]}
    return prov.plan_folder_pins(_Api(), tables, folders, IDS, can_read_pins=False)


plan, _ = _pins_nr({})
check("history-only: an empty room is planned for a post",
      sorted(c for c, _, _, _, _ in plan), ["Austin", "Boston"])

plan, _ = _pins_nr({}, history_by_id={"C1": [("111.222", "folder 1AAAAAAAAAAAAAAAAAA")]})
check("history-only: a room already holding the link is left completely alone",
      [c for c, _, _, _, _ in plan], ["Austin"])


# Without pins:read we cannot tell pinned from unpinned, so a message that is
# there must NOT be re-pinned or reposted — it drops out entirely. That is the
# difference from pins:read mode, where the same input yields a pin-only entry.
def _unreadable(pins_by_id=None, **kw):
    class _Api:
        def call(self, method, **params):
            return {"ok": False, "error": "missing_scope"}
    return prov.plan_folder_pins(_Api(), TABLES, FOLDERS, IDS, can_read_pins=False)


plan, skipped = _unreadable()
check("history-only: a room whose history ALSO cannot be read is not posted into",
      plan, [])
check("...and is reported", sorted(c for c, _ in skipped), ["Austin", "Boston"])

check("pins:read/pins:write are optional, not required",
      [s for s in ("pins:read", "pins:write") if s in prov.NEEDED_SCOPES], [])
check("...and both are declared as optional capabilities",
      all(s in prov.OPTIONAL_SCOPES for s in ("pins:read", "pins:write")), True)
check("chat:write IS required — the phase cannot post without it",
      "chat:write" in prov.NEEDED_SCOPES, True)


# --- pins.add is allowed; pins.remove is deliberately not ---------------------
check("pins.add is on the write allowlist", "pins.add" in prov.WRITE_METHODS, True)
check("pins.remove stays off it", "pins.remove" in prov.WRITE_METHODS, False)
check("pins.list is a READ, so it lives on the read allowlist",
      "pins.list" in prov.slackmod.ALLOWED_METHODS, True)
check("the read allowlist still cannot post", "chat.postMessage" in prov.slackmod.ALLOWED_METHODS, False)


# --- the config key exists on both sides of the sheet contract ----------------
import migrate_resource_columns as mig  # noqa: E402
check("the ops roster is a known Slack Config label",
      ao.CONFIG_LABELS.get("Ops staff email"), "ops_staff_emails")
check("the label agrees with the migration's spelling",
      mig.CONFIG_LABELS.get("ops_staff_emails"), "Ops staff email")
check("the roster is a list setting, so several people can be listed",
      "ops_staff_emails" in ao.LIST_SETTINGS, True)
# NOT required: only provision_channels reads it, and that phase refuses on its
# own when the list is empty. Requiring it here would take every audit down over
# a setting they never touch.
check("the roster is not required config", "ops_staff_emails" in ao.REQUIRED_CFG, False)

# --- no real address is committed to this public repo -------------------------
_SOURCE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "provision_channels.py")).read()
for domain in ("@mlops.community", "@aihero.studio", "@linuxfoundation.org"):
    check("no %s address is hardcoded in provision_channels.py" % domain,
          domain in _SOURCE, False)

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nprovision_channels: all checks passed")
