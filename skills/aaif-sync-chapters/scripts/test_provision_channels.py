#!/usr/bin/env python3
"""Self-tests for the ops-seed and folder-link phases. No network, no Slack, no gws.

What is covered is what would be dangerous or misleading if wrong: that neither
phase can silently widen past organizer channels, that a room whose state could
not be READ is never treated as a room that is empty or link-free, that a
disarmed roster seeds nobody rather than falling back to a hardcoded account,
and that the roster cannot buy someone standing membership in 87 private rooms.

The idempotency tests parameterise the `conversations.history` response on
purpose. The first version of this file hardcoded it to ok, and that single
choice is why a duplicate-post bug shipped: with pins:read granted and the
history scopes absent, plan_folder_pins posted a fresh copy into every
organizer room on every run.
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


def _seed2(*a, **k):
    """plan_ops_seed's first two returns, for tests that predate `n_read`."""
    seed, skipped, _ = _seed(*a, **k)
    return seed, skipped


# --- scope: organizer rooms only ---------------------------------------------
# A public chapter room and a country room are in `tables` and in `ids`, so a
# seed that widened to them would still "work" — it would just quietly put staff
# into public rooms. Asserting the channel NAMES, not just the count, is what
# catches that.
seed, unreadable = _seed2({"C1": [], "C2": [], "C3": [], "C4": []})
check("only organizer channels are seeded",
      sorted(n for n, _, _ in seed), ["austin-organizers", "boston-organizers"])
check("nothing is reported unreadable when every room reads", unreadable, [])

# --- a room already holding everyone is not re-invited ------------------------
seed, _ = _seed2({"C1": ["U1", "U2"], "C2": ["U1"], "C3": [], "C4": []})
check("a complete room drops out of the plan entirely",
      [n for n, _, _ in seed], ["austin-organizers"])
check("only the MISSING ops account is proposed",
      [label for _, _, m in seed for _, _, label in m], ["Bo"])

# --- an unreadable private room is reported, never treated as empty -----------
# This is the one that matters most: `conversations.members` answers
# channel_not_found for a private room the token is not in. Reading that as []
# would propose an invite into a room whose membership was never checked.
seed, unreadable = _seed2(
    {"C1": prov.slackmod.SlackError("conversations.members", "channel_not_found", ""),
     "C2": ["U1", "U2"], "C3": [], "C4": []})
check("an unreadable room is NOT proposed for seeding", seed, [])
check("an unreadable room is reported instead",
      [n for n, _ in unreadable], ["boston-organizers"])

# --- the sheet's `none` sentinel is not a channel name ------------------------
seed, _ = _seed2({"C1": [], "C2": []},
                tables=dict(TABLES, organizers={"Boston": "boston-organizers",
                                                "Nowhere": ao.NO_RESOURCE}))
check("the `none` sentinel is never looked up as a channel",
      [n for n, _, _ in seed], ["boston-organizers"])

# --- a disarmed roster seeds nobody -------------------------------------------
seed, _ = _seed2({"C1": [], "C2": []}, ops=[])
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
BOS_ID, AUS_ID = "1" + "A" * 32, "1" + "B" * 32
FOLDERS = {"Boston": "https://drive.google.com/drive/folders/" + BOS_ID,
           "Austin": "https://drive.google.com/drive/folders/" + AUS_ID}


def _pins(pins_by_id, history_by_id=None, folders=FOLDERS, tables=TABLES,
          history_error=None, can_read_pins=True):
    history_by_id = history_by_id or {}

    class _Api:
        def call(self, method, **params):
            cid = params["channel"]
            if method == "pins.list":
                got = pins_by_id[cid]
                return ({"ok": False, "error": got} if isinstance(got, str)
                        else {"ok": True, "items": [{"message": {"text": t}} for t in got]})
            if method == "conversations.history":
                if history_error:
                    return {"ok": False, "error": history_error}
                return {"ok": True, "messages": [{"text": t, "ts": ts}
                                                 for ts, t in history_by_id.get(cid, [])]}
            raise AssertionError("unexpected method %s" % method)
    return prov.plan_folder_pins(_Api(), tables, folders, IDS,
                                 can_read_pins=can_read_pins)


plan, skipped = _pins({"C1": [], "C2": []})
check("a channel with no pin is planned for a post",
      [(c, ts) for c, _, _, _, ts in plan], [("Austin", None), ("Boston", None)])

# The id, not the URL: the pinned copy here carries a `?usp=sharing` the sheet
# cell does not, and it is the same folder.
plan, _ = _pins({"C1": ["see https://drive.google.com/drive/folders/"
                        + BOS_ID + "?usp=sharing"], "C2": []})
check("a pin matching on folder ID (not exact URL) is left alone",
      [c for c, _, _, _, _ in plan], ["Austin"])

# The failure this design exists for: posted, but pins.add failed. A pins-only
# check would post a second copy.
plan, _ = _pins({"C1": [], "C2": []},
                history_by_id={"C1": [("111.222", "folder " + BOS_ID)]})
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

plan, _ = _pins_nr({}, history_by_id={"C1": [("111.222", "folder " + BOS_ID)]})
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


# --- THE regression: pins:read granted, history unreadable --------------------
# This is the bug that shipped. pins.list answers "is it pinned"; history
# answers "is it posted". An ok-but-empty pins list means UNPINNED, not absent,
# so an unreadable history must skip in EVERY mode. The old guard was
# `if not readable and not can_read_pins`, so with pins:read granted and
# groups:history absent this posted a fresh copy into every room, every run.
plan, skipped = _pins({"C1": [], "C2": []}, history_error="missing_scope")
check("pins:read + unreadable history NEVER posts", plan, [])
check("...every chapter is reported instead",
      sorted(c for c, _ in skipped), ["Austin", "Boston"])
check("...and the reason names the missing scopes",
      all("history" in why for _, why in skipped), True)

# The contrast case: history readable and genuinely empty -> post is correct.
plan, _ = _pins({"C1": [], "C2": []})
check("pins:read + readable empty history DOES plan a post",
      sorted(c for c, _, _, _, _ in plan), ["Austin", "Boston"])

# --- the plan carries the validated ID, never the raw cell --------------------
# A cell an editor controls must never reach chat.postMessage: Slack renders
# <url|anchor> as a link and <!channel> as a notification, under an ops admin's
# own token.
EVIL = ("<https://evil.example|Open your chapter folder> <!channel> "
        "https://drive.google.com/drive/folders/" + BOS_ID)
plan, _ = _pins({"C1": [], "C2": []}, folders={"Boston": EVIL})
check("a crafted cell yields the bare folder id, not the cell",
      [fid for _, _, _, fid, _ in plan], [BOS_ID])
check("the posted URL is rebuilt and contains no injected markup",
      "<!channel>" in (prov.FOLDER_URL % plan[0][3]), False)

# --- folder_id rejects junk that merely looks long enough ---------------------
for junk in ("not-a-drive-url", "TODO_ask_rahul_2026", "see the shared drive",
             "", "   ", "none"):
    check("folder_id refuses %r" % junk, prov.folder_id(junk), None)
check("folder_id accepts a real /folders/ URL",
      prov.folder_id("https://drive.google.com/drive/folders/%s?usp=sharing" % BOS_ID),
      BOS_ID)
check("folder_id accepts a bare id of real length", prov.folder_id(BOS_ID), BOS_ID)

# --- the roster is a trust boundary -------------------------------------------
_ROSTER = {"ops@aaif.test": {"id": "U1", "real_name": "Ada"},
           "outsider@gmail.com": {"id": "U2", "real_name": "Bo"}}
with _mock.patch.object(prov.slackmod, "lookup_emails", lambda api, e: _ROSTER):
    ops, unresolved = prov.resolve_ops(None, list(_ROSTER), staff_domains=["aaif.test"])
check("an address outside the ops domains is refused",
      [e for e, _ in unresolved], ["outsider@gmail.com"])
check("...and never reaches the seed list", [e for e, _, _ in ops], ["ops@aaif.test"])

# The guard is a LIST because ops staff span domains. A single-domain version
# refused a real ops admin on the first live run — the regression this pins.
_TWO = {"a@one.test": {"id": "U1", "real_name": "Ada"},
        "b@two.test": {"id": "U2", "real_name": "Bo"}}
with _mock.patch.object(prov.slackmod, "lookup_emails", lambda api, e: _TWO):
    ops, unresolved = prov.resolve_ops(None, list(_TWO),
                                       staff_domains=["one.test", "two.test"])
check("ops staff at DIFFERENT domains are both kept",
      sorted(e for e, _, _ in ops), ["a@one.test", "b@two.test"])
check("...with nothing refused", unresolved, [])

with _mock.patch.object(prov.slackmod, "lookup_emails", lambda api, e: _TWO):
    ops, _u = prov.resolve_ops(None, list(_TWO), staff_domains=[])
check("an empty domain list means no domain guard, not refuse-everything",
      len(ops), 2)

with _mock.patch.object(prov.slackmod, "lookup_emails", lambda api, e: _ROSTER):
    ops, unresolved = prov.resolve_ops(None, ["ops@aaif.test"],
                                       staff_domains=["aaif.test"], exclude_ids={"U1"})
check("the run's own token owner is excluded (cant_invite_self -> exit 1 forever)",
      ops, [])
check("...and is reported, not dropped", len(unresolved), 1)

try:
    prov.resolve_ops(None, ["a%d@x.test" % i for i in range(prov.MAX_OPS + 1)])
    _capped = False
except SystemExit:
    _capped = True
check("an over-long roster ABORTS rather than inviting into 87 private rooms",
      _capped, True)

# --- read_chapter_folders distinguishes "no column" from "all done" -----------
with _mock.patch.object(prov.ao, "gws_values", lambda *a, **k: []):
    check("an empty sheet read returns column_present=False, not IndexError",
          prov.read_chapter_folders(), ({}, False))
with _mock.patch.object(prov.ao, "gws_values",
                        lambda *a, **k: [["City", "Slack Channel"], ["Boston", "x"]]):
    check("a missing Chapter Folder column is reported, not silently empty",
          prov.read_chapter_folders(), ({}, False))
with _mock.patch.object(prov.ao, "gws_values",
                        lambda *a, **k: [["City", "Chapter Folder"],
                                         ["Boston", "u"], ["Nowhere", "none"]]):
    check("the none sentinel is not a folder", prov.read_chapter_folders(),
          ({"Boston": "u"}, True))

# --- plan_ops_seed reports rooms it could not see -----------------------------
seed, skipped, n_read = _seed({"C2": []},
                              ids={"austin-organizers": "C2"})
check("an organizer room with no resolvable id is REPORTED, not dropped",
      [n for n, _ in skipped], ["boston-organizers"])
check("...and is not counted as read", n_read, 1)


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
check("the ops domain allowlist is a known Slack Config label",
      ao.CONFIG_LABELS.get("Ops staff domain"), "ops_staff_domains")
check("the domain allowlist is a list setting, so staff can span domains",
      "ops_staff_domains" in ao.LIST_SETTINGS, True)
check("the domain label agrees with the migration's spelling",
      mig.CONFIG_LABELS.get("ops_staff_domains"), "Ops staff domain")
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
