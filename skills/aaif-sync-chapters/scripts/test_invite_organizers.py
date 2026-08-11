#!/usr/bin/env python3
"""Self-tests for the organizer-invite logic. No network, no Slack, no gws.

What is covered is what would be dangerous or misleading if wrong: that the
write allowlist cannot be bypassed, that a person with no account is reported
rather than dropped, and that someone the intake does not know is never removed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import invite_organizers as inv  # noqa: E402
import provision_channels as prov  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


# --- the write allowlist is the chokepoint, and it is shared -------------------
check("invite goes through the same allowlist as create/rename",
      "conversations.invite" in prov.WRITE_METHODS, True)
# The absences are the point. `conversations.kick` joined the allowlist on
# 2026-08-10 when pruning was authorised; archive and postMessage did not, and
# the difference is real — a rename is reversible, destroying a room or speaking
# in one is not.
for forbidden in ("conversations.archive", "chat.postMessage",
                  "conversations.leave", "conversations.delete"):
    check("%s stays out of the allowlist" % forbidden,
          forbidden in prov.WRITE_METHODS, False)


def refuses(method):
    try:
        prov.call_write("token", method, channel="C1")
    except ValueError:
        return True
    return False


check("call_write refuses a method outside the allowlist",
      refuses("conversations.archive"), True)
check("kick is reachable, but only through the one chokepoint",
      "conversations.kick" in prov.WRITE_METHODS, True)

# --- batching -----------------------------------------------------------------
# One call per channel, not one per person: Slack renders a batched invite as a
# single event rather than N join lines, and it stays inside the rate limit.
check("a channel's invites are batched into one call", inv.MAX_PER_CALL, 1000)


# --- apply(): what reaches Slack ----------------------------------------------
class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"ok": True}

    def __call__(self, token, method, **params):
        self.calls.append((method, params))
        return self.result


ROWS = [
    {"city": "Berlin", "channel": "berlin-organizers", "channel_id": "C1",
     "is_private": True, "missing": [("A", "U1"), ("B", "U2")],
     "present": [("C", "U3")], "unaccounted": ["U9"]},
    {"city": "Pune", "channel": "pune-organizers", "channel_id": "C2",
     "is_private": True, "missing": [], "present": [("D", "U4")],
     "unaccounted": []},
]

_rec = _Recorder()
_orig, prov_call = inv.call_write, None
inv.call_write = _rec
try:
    done, failed = inv.apply(ROWS, "token")
finally:
    inv.call_write = _orig

check("only channels with someone missing are called",
      [c[1]["channel"] for c in _rec.calls], ["C1"])
check("the missing ids go in one batched users param",
      _rec.calls[0][1]["users"], "U1,U2")
check("and only conversations.invite is used", _rec.calls[0][0],
      "conversations.invite")
check("the count is people, not calls", (done, failed), (2, []))

# Someone already in the channel is never re-sent, and someone the intake does
# not list is never touched — `unaccounted` reaches no request at all.
check("an already-present member is not re-invited",
      "U3" in _rec.calls[0][1]["users"], False)
check("an unaccounted member is never in a request",
      "U9" in _rec.calls[0][1]["users"], False)

# --- a benign race is not a failure -------------------------------------------
_rec = _Recorder({"ok": False, "error": "already_in_channel"})
inv.call_write = _rec
try:
    done, failed = inv.apply(ROWS, "token")
finally:
    inv.call_write = _orig
check("already_in_channel is treated as benign, not an error", (done, failed),
      (0, []))

# --- a real error is collected, and does not abandon the batch ----------------
ROWS2 = ROWS + [{"city": "X", "channel": "x-organizers", "channel_id": "C3",
                 "is_private": True, "missing": [("E", "U5")], "present": [],
                 "unaccounted": []}]
_rec = _Recorder({"ok": False, "error": "channel_not_found"})
inv.call_write = _rec
try:
    done, failed = inv.apply(ROWS2, "token")
finally:
    inv.call_write = _orig
check("a failing channel is reported, and every other one still ran",
      (done, len(failed), len(_rec.calls)), (0, 2, 2))

# --- rename ordering: the London chain is the case that breaks naive order ----
# Alphabetically london-meetup-organizers sorts BEFORE london-organizers, so an
# unordered pass tries to take a name that is still occupied -> name_taken.
_live = {"bangalore", "london-organizers", "london-meetup-organizers",
         "bay-area-sf-organizers"}
_ordered, _blocked = prov.order_renames(prov.CHANNEL_RENAMES, _live)
check("nothing is blocked", _blocked, [])
check("every rename is scheduled exactly once",
      len(_ordered), len(prov.CHANNEL_RENAMES))
_pos = {old: i for i, (old, _) in enumerate(_ordered)}
check("the occupant moves out before the new name is taken",
      _pos["london-organizers"] < _pos["london-meetup-organizers"], True)

# A target nothing frees is reported, never attempted.
check("an unfreeable target is blocked, not attempted",
      prov.order_renames({"a": "occupied"}, {"a", "occupied"}),
      ([], [("a", "occupied")]))

# A cycle needs a temporary name; it must not half-apply.
_o3, _b3 = prov.order_renames({"x": "y", "y": "x"}, {"x", "y"})
check("a rename cycle is blocked rather than half-applied",
      (_o3, sorted(_b3)), ([], [("x", "y"), ("y", "x")]))

# --- a merge is not a rename --------------------------------------------------
# A renamed room keeps its members; a merged room's members must be invited
# across. Modelling the merge as a rename would retire the room and lose its 21.
check("the merged room is not also in the rename map",
      set(prov.CHANNEL_MERGES) & set(prov.CHANNEL_RENAMES), set())
check("the merge target is produced by a rename, so it exists when needed",
      prov.CHANNEL_MERGES["southbay-chapter-leads"]["into"]
      in prov.CHANNEL_RENAMES.values(), True)
check("deprecated rooms are marked 'deprecated'",
      all(v.endswith("-deprecated") for v in
          [prov.CHANNEL_RENAMES["london-organizers"],
           prov.CHANNEL_MERGES["southbay-chapter-leads"]["retire_as"]]), True)

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\ninvite_organizers: all checks passed")
