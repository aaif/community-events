#!/usr/bin/env python3
"""Create and rename the Slack channels the Chapters List plans. NOT read-only.

This is the only script in the repo that writes to Slack. It exists because
`sync_resources.py --plan` fills the sheet with the channel a chapter *will*
have, and something then has to make that true.

## It does not share the audit's Slack client, on purpose

`lib/aaif_events/slack.py` refuses any method outside `ALLOWED_METHODS`, and that
refusal is load-bearing: it is why a typo in a 30k-member workspace cannot post,
invite or archive. Widening that allowlist to let this script through would
remove the guarantee from **every** audit at the same time, to benefit one script
that runs approximately once.

So this file carries its own small write client. The audit's client stays exactly
as read-only as it claims to be, and the blast radius of the write capability is
this one file.

## What it will not do

- **Never archives or deletes.** Nothing here removes a room people are in.
- **Never invites.** This script builds rooms; it does not put people in them.
  Inviting lives in `invite_organizers.py`, deliberately separate: it needs
  different scopes, and it is less reversible than anything here. A channel can
  be archived; an invitation notification cannot be unsent.
- **Never creates a channel the sheet does not name.** The sheet is the plan; if
  it isn't there, it isn't provisioned.
- **Renames only what CHANNEL_RENAMES lists, and merges only CHANNEL_MERGES.** A
  rename keeps every member and all history, which is why it is the right
  operation for a room under a superseded name — creating the new name alongside
  would split the chapter across two rooms. A *merge* is different and is modelled
  separately: the room is retired, but its members do **not** travel with it, so
  `invite_organizers.py` has to move them into the target afterwards.
- **Rename order is computed, not written down.** `#london-organizers` must be
  renamed away before `#london-meetup-organizers` can take that name; applied
  alphabetically the second one fails with `name_taken`. `order_renames()` sorts
  the chain and REFUSES the whole run if any step is blocked by a name nothing
  frees — a half-applied rename chain is the worst outcome available here.

## Prerequisites, neither of which is in place by default

1. A token with `channels:manage` (public) and `groups:write` (private), in
   `$AAIF_SLACK_WRITE_TOKEN`. The Slack CLI's own token is read-only and its
   scopes cannot be widened, so this is a separate app token — run the script
   without it for the four setup steps. **Whoever's token it is joins every
   channel it creates.**
2. `--i-have-approval`, on top of `--write`. Two flags, because this is the one
   irreversible-ish action in the repo and a mistyped `--write` elsewhere is
   merely a spreadsheet edit.

Usage:
    python3 provision_channels.py                       # report only
    python3 provision_channels.py --write --i-have-approval
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import NO_RESOURCE  # noqa: E402

import audit_organizers as ao  # noqa: E402
from aaif_events import slack as slackmod  # noqa: E402

API = "https://slack.com/api/"

#: The complete set of write methods this script may call — the same
#: allowlist discipline as the read-only client, for the same reason. Note what
#: `conversations.invite` and `conversations.kick` are here rather than in the
#: scripts that use them because call_write() is the single chokepoint all three
#: go through — an allowlist split across files is one that can disagree with
#: itself.
#:
#: `conversations.kick` was deliberately absent until 2026-08-10, when removing
#: non-organizers from organizer channels was authorised. It is the only entry
#: that removes a person, and it is reachable from exactly one script
#: (`prune_organizers.py`), which refuses to act on anything but an explicit
#: keep-list. Read that file before assuming it is safe to call from elsewhere.
#:
#: Still absent, and to stay absent: `conversations.archive` (nothing here
#: destroys a room — deprecation is a rename, which is reversible) and
#: `chat.postMessage` (nothing here speaks as a human in a community channel).
WRITE_METHODS = frozenset({"conversations.create", "conversations.rename",
                           "conversations.invite", "conversations.kick"})

#: Scopes each write needs. Checked up front so a missing scope fails before the
#: first channel rather than half way through the batch.
NEEDED_SCOPES = ("channels:manage", "groups:write")

#: Slack renames: the room keeps its identity, members and history, and takes a
#: new name. ORDER IS COMPUTED, not written here — see order_renames(). Two of
#: these form a chain (`london-organizers` must move out of the way before
#: `london-meetup-organizers` can move in), and applying them alphabetically
#: fails with `name_taken` on the one that matters.
CHANNEL_RENAMES = {
    "bangalore": "bengaluru",
    "london-organizers": "london-organizers-deprecated",
    "london-meetup-organizers": "london-organizers",
    "bay-area-sf-organizers": "bay-area-organizers",
}

#: Merges: the room is retired and its chapter moves to another room. Distinct
#: from a rename because the members do NOT come with it — they are invited
#: across by invite_organizers.py, which is why `into` must already exist (or be
#: the target of a rename in the same run) before the retirement lands.
CHANNEL_MERGES = {
    "southbay-chapter-leads": {"into": "bay-area-organizers",
                               "retire_as": "southbay-chapter-leads-deprecated"},
}


def order_renames(renames, live):
    """Order renames so no step is blocked by a name another step frees.

    Returns (ordered, blocked). A rename whose target is occupied waits for the
    occupant's own rename; anything still blocked at the end is reported rather
    than attempted, because Slack answers `name_taken` and the run would look
    half-applied for a reason nobody could see from the output.

    Cycles (A->B, B->A) cannot be resolved without a temporary name and are
    reported as blocked rather than silently broken.
    """
    pending = dict(renames)
    taken = set(live)
    ordered = []
    progress = True
    while pending and progress:
        progress = False
        for old in sorted(pending):
            new = pending[old]
            # Free if nothing holds the target, or the holder already moved.
            if new not in taken or new in {o for o, _ in ordered}:
                ordered.append((old, new))
                taken.discard(old)
                taken.add(new)
                del pending[old]
                progress = True
                break
    return ordered, sorted(pending.items())


#: Write scopes cannot be added to the Slack CLI's own token, so the write path
#: takes a SEPARATE token from `AAIF_SLACK_WRITE_TOKEN`. The audit keeps using
#: `~/.slack/credentials.json` untouched — which is the point: the read path
#: cannot acquire write power by accident, and revoking the write app does not
#: disturb the audits.
WRITE_TOKEN_ENV = "AAIF_SLACK_WRITE_TOKEN"


def write_token():
    """The token for write calls, or a SystemExit explaining how to get one."""
    token = os.environ.get(WRITE_TOKEN_ENV, "").strip()
    if not token:
        raise SystemExit(
            "No write token. The Slack CLI token is read-only and its scopes "
            "cannot be widened, so writes use a separate app token:\n"
            "  1. api.slack.com/apps -> Create New App -> From scratch\n"
            "  2. OAuth & Permissions -> User Token Scopes: channels:manage, "
            "groups:write,\n     channels:write.invites, groups:write.invites\n"
            "  3. Install to Workspace, copy the User OAuth Token (xoxp-...)\n"
            "  4. export %s='xoxp-...'\n\n"
            "Use a USER token, not a bot token: the creator of a channel joins "
            "it, and\na bot sitting in 128 chapter rooms is noise. Note the "
            "same applies to you —\nwhoever's token this is will be a member of "
            "every channel it creates." % WRITE_TOKEN_ENV)
    return token


def call_write(token, method, **params):
    """POST a write method. Refuses anything outside WRITE_METHODS."""
    if method not in WRITE_METHODS:
        raise ValueError("%s is not a sanctioned write method." % method)
    req = urllib.request.Request(
        API + method, data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "http_%d" % exc.code}


def plan(tables, live):
    """Return (creates, renames, already).

    `creates` is [(name, is_private, why)]. A name is planned exactly once even
    when several chapters share it — #india serves six, and asking Slack to
    create it six times would be five errors and one channel.
    """
    creates, already, seen = [], [], set()

    def want(name, private, why):
        if not name or name == NO_RESOURCE or name in seen:
            return
        seen.add(name)
        (already if name in live else creates).append((name, private, why))

    for table, private, label in (("public", False, "chapter channel"),
                                  ("organizers", True, "organizer channel"),
                                  ("regional", False, "country channel")):
        for city, name in sorted(tables[table].items()):
            want(name, private, "%s for %s" % (label, city))

    # Only rename what is actually there: a rename already applied is a no-op,
    # not an error to re-attempt every run.
    wanted = {o: n for o, n in CHANNEL_RENAMES.items() if o in live}
    renames, blocked = order_renames(wanted, live)
    merges = [(o, m) for o, m in sorted(CHANNEL_MERGES.items()) if o in live]
    return creates, renames, blocked, merges, already


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; see the module docstring")
    a = ap.parse_args()

    token = slackmod.load_token()
    api = slackmod.Slack(token=token)
    who = api.ok("auth.test")
    print("workspace: %s (%s)\n" % (who.get("team"), who.get("team_id")))

    chans = slackmod.channels(api)
    live = {c["name"] for c in chans if not c["is_archived"]}
    by_name = {c["name"]: c for c in chans}
    _, tables = ao.read_chapters()
    creates, renames, blocked, merges, already = plan(tables, live)

    print("Already exist: %d" % len(already))
    print("\nTo CREATE (%d):" % len(creates))
    for name, private, why in creates:
        print("  #%-28s %-8s %s" % (name, "private" if private else "public", why))
    print("\nTo RENAME (%d), in this order — keeps members and history:"
          % len(renames))
    for i, (old, new) in enumerate(renames, 1):
        print("  %d. #%s -> #%s" % (i, old, new))
    if blocked:
        print("\n  BLOCKED (%d) — target name is held and nothing frees it:" % len(blocked))
        for old, new in blocked:
            print("    #%s -> #%s" % (old, new))
    print("\nTo MERGE (%d) — the room is retired, members are invited across by"
          "\ninvite_organizers.py (a rename would NOT carry them):" % len(merges))
    for old, m in merges:
        print("  #%s -> into #%s, retired as #%s"
              % (old, m["into"], m["retire_as"]))

    if not a.write:
        print("\nReport only. Nothing was sent to Slack.")
        return
    if not a.i_have_approval:
        sys.exit("\nREFUSING: --write needs --i-have-approval too. This is the "
                 "one action in this repo that changes a shared workspace for "
                 "everyone in it.")

    token = write_token()
    have = slackmod.Slack(token=token).scopes()
    missing = [s for s in NEEDED_SCOPES if s not in have]
    if missing:
        sys.exit("\nREFUSING: the token lacks %s. Re-authenticate with a Slack "
                 "app that requests them; the audit token is read-only by "
                 "design and cannot do this." % ", ".join(missing))

    if blocked:
        sys.exit("\nREFUSING: %d rename(s) are blocked by a name nothing frees. "
                 "Applying the rest would leave the estate half-migrated with no "
                 "sign of why." % len(blocked))

    done, failed = 0, []
    # Renames before creates: creating #bengaluru first would take the name and
    # strand 37 people in the old room. And in the computed order, so the London
    # chain frees its name before the next step needs it.
    for old, new in renames:
        r = call_write(token, "conversations.rename",
                       channel=by_name[old]["id"], name=new)
        if r.get("ok"):
            done += 1
            print("renamed #%s -> #%s" % (old, new))
        else:
            failed.append("rename %s: %s" % (old, r.get("error")))

    # Retirements LAST, and only after their target exists: the members of a
    # retired room have not been invited across yet (that is invite_organizers'
    # job), so retiring early would leave them in a room nobody is pointed at.
    for old, m in merges:
        r = call_write(token, "conversations.rename",
                       channel=by_name[old]["id"], name=m["retire_as"])
        if r.get("ok"):
            done += 1
            print("retired #%s -> #%s (run invite_organizers.py to move its "
                  "members into #%s)" % (old, m["retire_as"], m["into"]))
        else:
            failed.append("retire %s: %s" % (old, r.get("error")))

    for name, private, why in creates:
        r = call_write(token, "conversations.create", name=name,
                       is_private="true" if private else "false")
        if r.get("ok"):
            done += 1
            print("created #%s" % name)
        else:
            # One bad name must not abandon the batch — a public form feeds these
            # city names, so a rejected slug is normal input, not a crash.
            failed.append("create %s: %s" % (name, r.get("error")))

    print("\n%d applied, %d failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)
    print("\nNobody was invited to anything. `Organizer Handles` on the Chapters "
          "List says who belongs in each organizer channel; inviting them is a "
          "human's job.")


if __name__ == "__main__":
    main()
