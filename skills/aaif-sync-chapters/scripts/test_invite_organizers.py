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
# 2026-08-10 when pruning was authorised; archive, join and postMessage joined
# on 2026-08-17 for the deprecated-room sweep (archive refuses non-`-deprecated`
# names; postMessage exists only for the farewell pointer). Deleting a room —
# the one truly unrecoverable act — stays out, as does leave.
for forbidden in ("conversations.leave", "conversations.delete"):
    check("%s stays out of the allowlist" % forbidden,
          forbidden in prov.WRITE_METHODS, False)
for sanctioned in ("conversations.archive", "chat.postMessage",
                   "conversations.join"):
    check("%s is reachable for the deprecated sweep" % sanctioned,
          sanctioned in prov.WRITE_METHODS, True)


def refuses(method):
    try:
        prov.call_write("token", method, channel="C1")
    except ValueError:
        return True
    return False


check("call_write refuses a method outside the allowlist",
      refuses("conversations.delete"), True)
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

# --- a failed batch retries singly: Slack fails the WHOLE batch on one bad ----
# invitee, so top-level already_in_channel means "at least one raced in", not
# "everyone did" — the other N-1 must each get their own call.
class _BatchThenSingle:
    """Fail any multi-user call; answer single-user calls from a script."""

    def __init__(self, singles):
        self.calls, self.singles = [], singles

    def __call__(self, token, method, **params):
        self.calls.append((method, params))
        users = params["users"]
        if "," in users:
            return {"ok": False, "error": "already_in_channel"}
        return self.singles.get(users, {"ok": True})


_rec = _BatchThenSingle({"U1": {"ok": False, "error": "already_in_channel"}})
inv.call_write = _rec
try:
    done, failed = inv.apply(ROWS, "token")
finally:
    inv.call_write = _orig
check("a raced batch falls back to one call per person",
      [c[1]["users"] for c in _rec.calls], ["U1,U2", "U1", "U2"])
check("the raced member and the real invite both count as done, no failures",
      (done, failed), (2, []))

# --- a real per-person error is collected, and does not abandon the rest ------
ROWS2 = ROWS + [{"city": "X", "channel": "x-organizers", "channel_id": "C3",
                 "is_private": True, "missing": [("E", "U5")], "present": [],
                 "unaccounted": []}]
_rec = _BatchThenSingle({"U1": {"ok": False, "error": "user_is_restricted"},
                         "U5": {"ok": True}})
inv.call_write = _rec
try:
    done, failed = inv.apply(ROWS2, "token")
finally:
    inv.call_write = _orig
check("a failing person is reported, and everyone else still lands",
      (done, len(failed)), (2, 1))
check("the failure names the person's id, not the whole channel",
      "U1" in failed[0], True)

# --- rename ordering: the London chain is the case that breaks naive order ----
# Alphabetically london-meetup-organizers sorts BEFORE london-organizers, so an
# unordered pass tries to take a name that is still occupied -> name_taken.
# A FIXED map (the London chain plus one free rename), not the production
# CHANNEL_RENAMES: that map changes several times a day, and the property
# under test — chain ordering — must not silently change with the data.
_chain = {"bangalore": "bengaluru",
          "london-organizers": "london-organizers-deprecated",
          "london-meetup-organizers": "london-organizers"}
_live = {"bangalore", "london-organizers", "london-meetup-organizers"}
_ordered, _blocked = prov.order_renames(_chain, _live)
check("nothing is blocked", _blocked, [])
check("every rename is scheduled exactly once", len(_ordered), len(_chain))
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


# --- plan(): applied-rename detection and create suppression -------------------
# Synthetic maps throughout: the production CHANNEL_RENAMES changes daily and
# these properties must not drift with it. Patch, test, restore.
def _with_maps(renames, merges, fn):
    saved = prov.CHANNEL_RENAMES, prov.CHANNEL_MERGES
    prov.CHANNEL_RENAMES, prov.CHANNEL_MERGES = renames, merges
    try:
        return fn()
    finally:
        prov.CHANNEL_RENAMES, prov.CHANNEL_MERGES = saved


_TABLES = {"public": {}, "organizers": {}, "regional": {}}

# The London shape after the chain applied AND the sweep archived the parked
# room: old name live again (held by the promoted room), target name exists
# only among ARCHIVED names. Re-planning it would rename the real room away.
_c, _r, _b, _m, _a, _applied, _ref0 = _with_maps(
    {"old-organizers": "old-organizers-deprecated",
     "meetup-organizers": "old-organizers"}, {},
    lambda: prov.plan(_TABLES, live={"old-organizers"},
                      all_names={"old-organizers", "old-organizers-deprecated"}))
check("an applied chain is not re-planned against the real room", _r, [])
check("and nothing is blocked by it", _b, [])
check("the applied classification is reported, not swallowed",
      _applied, [("old-organizers", "old-organizers-deprecated")])

# A genuine squatter: the old name is nobody's rename target, so target-exists
# means blocked — never silently treated as applied.
_c, _r, _b, _m, _a, _applied, _ref0 = _with_maps(
    {"utah": "salt-lake-city"}, {},
    lambda: prov.plan(_TABLES, live={"utah", "salt-lake-city"},
                      all_names={"utah", "salt-lake-city"}))
check("a squatted target still blocks", _b, [("utah", "salt-lake-city")])
check("a blocked rename is not reported as applied", _applied, [])

# A name a pending rename frees INTO is satisfied by the rename, not created.
_tables2 = {"public": {"Bern": "bern"}, "organizers": {}, "regional": {}}
_c, _r, _b, _m, _a, _applied, _ref0 = _with_maps(
    {"old-bern": "bern"}, {},
    lambda: prov.plan(_tables2, live={"old-bern"}, all_names={"old-bern"}))
check("a rename-freed name is not also created", _c, [])
check("it counts as already satisfied", [n for n, _p, _w in _a], ["bern"])
check("and the rename itself is planned", _r, [("old-bern", "bern")])

# all_names defaults to live — pre-existing behavior unchanged.
_c, _r, _b, _m, _a, _applied, _ref0 = _with_maps(
    {"a": "b"}, {}, lambda: prov.plan(_TABLES, live={"a"}))
check("all_names defaults to live", _r, [("a", "b")])


# --- forbid_erstwhile(): recorded-history names are never planned again --------
_creates = [("boston-organizers", True, "organizer channel for Boston"),
            ("austin", False, "chapter channel for Austin")]
_renames = [("x-organizers", "y-organizers"), ("old", "munich")]
_fc, _fr, _ref = prov.forbid_erstwhile(_creates, _renames,
                                       {"austin", "munich", "germany"})
check("an erstwhile create is refused", _fc, [_creates[0]])
check("an erstwhile rename TARGET is refused", _fr, [("x-organizers", "y-organizers")])
check("refusals are reported, not dropped",
      sorted((k, n) for k, n, _d in _ref),
      [("create", "austin"), ("rename", "munich")])
check("an empty forbidden set changes nothing",
      prov.forbid_erstwhile(_creates, _renames, frozenset()),
      (_creates, _renames, []))

# The guard lives INSIDE plan(): no caller can obtain an unfiltered plan.
_c, _r, _b, _m, _a, _applied, _ref = _with_maps(
    {"old-x": "munich"}, {},
    lambda: prov.plan({"public": {"Austin": "austin"}, "organizers": {},
                       "regional": {}},
                      live={"old-x"}, forbidden={"austin", "munich"}))
check("plan() itself refuses erstwhile creates and rename targets",
      (_c, _r, sorted(k for k, _n, _d in _ref)), ([], [], ["create", "rename"]))
check("plan() without forbidden refuses nothing", _ref0, [])


# --- plan_archives(): the only gate before a room is closed --------------------
def _chan(name, private=False, archived=False, members=()):
    return {"name": name, "id": "C-" + name, "is_private": private,
            "is_archived": archived, "_members": list(members)}


def _plan_archives(chans, pointers, renames=None):
    by_name = {c["name"]: c for c in chans}
    live = {c["name"] for c in chans if not c["is_archived"]}
    saved = (prov.DEPRECATED_POINTERS, prov.CHANNEL_RENAMES,
             prov.slackmod.members)
    prov.DEPRECATED_POINTERS = pointers
    prov.CHANNEL_RENAMES = renames or {}
    prov.slackmod.members = lambda api, cid: next(
        c["_members"] for c in chans if c["id"] == cid)
    try:
        return prov.plan_archives(None, by_name, live, "U-ME")
    finally:
        (prov.DEPRECATED_POINTERS, prov.CHANNEL_RENAMES,
         prov.slackmod.members) = saved


check("a room not named -deprecated is never planned",
      _plan_archives([_chan("x-old"), _chan("x")], {"x-old": "x"}), [])
check("a -deprecated room queued for a rename is skipped, not archived",
      _plan_archives([_chan("y-deprecated"), _chan("y")],
                     {"y-deprecated": "y"}, renames={"y-deprecated": "y2"}),
      [("y-deprecated", "skip", "pending rename to #y2 — not retired")])
check("no recorded successor blocks",
      [p[:2] for p in _plan_archives([_chan("z-deprecated")], {})],
      [("z-deprecated", "blocked")])
check("an archived successor blocks",
      [p[:2] for p in _plan_archives(
          [_chan("w-deprecated"), _chan("w", archived=True)],
          {"w-deprecated": "w"})],
      [("w-deprecated", "blocked")])
check("a public room archives with a pointer",
      [p[:2] for p in _plan_archives(
          [_chan("p-deprecated", members=["U1"]), _chan("p")],
          {"p-deprecated": "p"})],
      [("p-deprecated", "archive")])
check("a private room with a straggler blocks",
      [p[:2] for p in _plan_archives(
          [_chan("q-deprecated", private=True, members=["U1", "U2"]),
           _chan("q", private=True, members=["U1"])],
          {"q-deprecated": "q"})],
      [("q-deprecated", "blocked")])
check("a covered private room archives, with the token's own seat excused",
      [p[:2] for p in _plan_archives(
          [_chan("s-deprecated", private=True, members=["U1", "U-ME"]),
           _chan("s", private=True, members=["U1"])],
          {"s-deprecated": "s"})],
      [("s-deprecated", "archive")])


# --- call_write: retry only what fixes itself ----------------------------------
check("a malformed Retry-After falls back instead of crashing",
      [prov._retry_secs(v) for v in
       ("Fri, 21 Aug 2026 07:28:00 GMT", None, "", "1.5", "30", -3)],
      [10, 10, 10, 1, 30, 1])


def _fake_urlopen_seq(responses):
    """Each call pops the next payload; records how many calls were made."""
    import io, json as _json  # noqa: E401

    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        calls.append(req)
        return _Resp(_json.dumps(responses[min(len(calls) - 1,
                                               len(responses) - 1)]).encode())
    return opener, calls


_saved_urlopen, _saved_sleep = prov.urllib.request.urlopen, prov.time.sleep
try:
    _sleeps = []
    prov.time.sleep = _sleeps.append
    _open, _calls = _fake_urlopen_seq([{"ok": False, "error": "ratelimited",
                                        "retry_after": 2},
                                       {"ok": True}])
    prov.urllib.request.urlopen = _open
    check("ratelimited retries and honours retry_after",
          (prov.call_write("t", "conversations.rename", channel="C1",
                           name="n"), len(_calls), _sleeps),
          ({"ok": True}, 2, [2]))

    _open, _calls = _fake_urlopen_seq([{"ok": False, "error": "name_taken"}])
    prov.urllib.request.urlopen = _open
    check("any other error returns to the caller without a retry",
          (prov.call_write("t", "conversations.rename", channel="C1",
                           name="n"), len(_calls)),
          ({"ok": False, "error": "name_taken"}, 1))

    _open, _calls = _fake_urlopen_seq([{"ok": False, "error": "ratelimited",
                                        "retry_after": 1}])
    prov.urllib.request.urlopen = _open
    check("a permanent rate limit gives up after five attempts",
          (prov.call_write("t", "conversations.rename", channel="C1",
                           name="n").get("error"), len(_calls)),
          ("ratelimited", 5))
finally:
    prov.urllib.request.urlopen = _saved_urlopen
    prov.time.sleep = _saved_sleep

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\ninvite_organizers: all checks passed")
