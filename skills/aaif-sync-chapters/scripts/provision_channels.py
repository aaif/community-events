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

- **Archives only rooms a rename already retired** — the deprecated-room sweep
  (authorised 2026-08-17) closes `*-deprecated` rooms only, public ones only
  after a farewell pointer post lands, private ones only once every member is
  already in the successor. Nothing here deletes, and nothing archives a room
  under its working name.
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

1. A token with `channels:write` (public), `groups:write` (private) and
   `chat:write` (the deprecated sweep's farewell pointer), in
   `$AAIF_SLACK_WRITE_TOKEN`. `channels:join` is also worth requesting: the
   farewell post needs membership in the room being archived, and without the
   scope a join fails and that room's archive is skipped (reported, not
   silent). The Slack CLI's own token is read-only and its scopes cannot be
   widened, so this is a separate app token — run the script without it for
   the four setup steps. **Whoever's token it is joins every channel it
   creates.**
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
import time
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
#: allowlist discipline as the read-only client, for the same reason.
#: `conversations.invite` and `conversations.kick` are here rather than in the
#: scripts that use them because call_write() is the single chokepoint every
#: write goes through — an allowlist split across files is one that can
#: disagree with itself.
#:
#: `conversations.kick` was deliberately absent until 2026-08-10, when removing
#: non-organizers from organizer channels was authorised. It is the only entry
#: that removes a person, and it is reachable from exactly one script
#: (`prune_organizers.py`), which refuses to act on anything but an explicit
#: keep-list. Read that file before assuming it is safe to call from elsewhere.
#:
#: `conversations.archive` and `chat.postMessage` were deliberately absent
#: until 2026-08-17, when archiving retired rooms was authorised. Both are
#: reachable from exactly one place — the deprecated-room sweep in main() —
#: and under guards that keep the original fears fenced:
#:
#: - archive refuses any channel whose name does not end `-deprecated`, so the
#:   only rooms it can close are ones a rename already retired on purpose;
#: - postMessage is used solely to leave the farewell pointer in the room being
#:   archived (public rooms only — a pointer nobody can follow into a private
#:   room helps no one), never to speak anywhere else.
#:
#: `conversations.join` is here because a user token cannot post in a public
#: channel it has not joined; it is only ever called on a room about to be
#: archived, so the membership is momentary by construction.
WRITE_METHODS = frozenset({"conversations.create", "conversations.rename",
                           "conversations.invite", "conversations.kick",
                           "conversations.archive", "conversations.join",
                           "chat.postMessage"})

#: Scopes each write needs. Checked up front so a missing scope fails before the
#: first channel rather than half way through the batch.
# `channels:write` is the USER-token scope for conversations.create/rename;
# `channels:manage` is its bot-token sibling and never appears on a user token.
# `chat:write` is for the deprecated-sweep's farewell pointer, nothing else.
NEEDED_SCOPES = ("channels:write", "groups:write", "chat:write")

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
    # 2026-08-21: the once-queued #austin-area-organizers -> #austin-organizers
    # rename was DROPPED along with the #austin plan below — the chapter keeps
    # the austin-area name, so its organizer room already matches.
    # 2026-08-17 naming-convention sweep (user-decided): city channels take the
    # plain city name, organizer rooms take <city>-organizers. #bay-area is a
    # deliberate keep.
    # NOT "meetup-seattle": "seattle" — a live #seattle (135 members, 2022)
    # already IS the city room; #meetup-seattle (41, 2023) was merged into it
    # (applied 2026-08-17 and removed; see the note in CHANNEL_MERGES).
    # APPLIED 2026-08-17 and removed from this map (empty junk rooms were
    # recreated under the freed old names before the sheet caught up, and
    # re-planning an applied rename against a junk-held old name refuses the
    # whole run): meetup-barcelona→barcelona, colorado→denver,
    # frankfurt_main→frankfurt(+-organizers), nyc→new-york,
    # nyc-chapter-leads→new-york-organizers, portland-oregon→portland,
    # washington-dc-the-capital-organizers→washington-dc-organizers.
    # STILL PENDING below — each target name is held by an INVISIBLE private
    # room, so they fail with name_taken (non-fatal) until those are
    # deprecated via the admin UI. (NOT meetup-barcelona-organizers ->
    # barcelona-organizers: its "squatter" turned out to be the REAL organizer
    # room — 2024, 16 members — so the freshly provisioned 3-member room merges
    # into it instead; see CHANNEL_MERGES.)
    "meetup-seattle-organizers": "seattle-organizers",
    "washington-dc-the-capital": "washington-dc",
    # Austin RESOLVED 2026-08-21 (user-decided): #austin stays squatted by an
    # invisible private room with no path to free it on the Pro plan, so the
    # chapter keeps its historical name instead — the real room (76 members,
    # 2023; parked as #austin-area-deprecated on 2026-08-17) was renamed BACK
    # to #austin-area, the sheet's Slack Channel cell now says `austin-area`,
    # and the 2026-08-18 junk room was renamed to #austin-area-junk and
    # re-archived to free the name. A deliberate keep like #bay-area — do NOT
    # re-plan #austin even if the squatter is someday cleared.
    "portland-oregon-organizers": "portland-organizers",
    # 2026-08-19 (user-decided, revised same day): country-named chapters
    # become capital-city chapters — but #switzerland (80) and #scotland (71)
    # STAY as country rooms; fresh #bern and #edinburgh city rooms are created
    # from the sheet instead, and only the ORGANIZER rooms take city names.
    # Utah is a state, not a country room worth keeping: the room itself
    # renames. Scotland/Utah's organizer rooms are invisible squatters, so
    # their -organizers rooms are fresh creates from the sheet, not renames.
    "switzerland-organizers": "bern-organizers",
    "utah": "salt-lake-city",
    # 2026-08-17 round two (user-decided): ASCII beats the accent for
    # typability (the #españa precedent notwithstanding), and Delhi NCR is
    # the chapter's actual name. Their organizer-room twins are invisible
    # squatters, so fresh rooms are created via the sheet instead.
    "medellín": "medellin",
    "delhi": "delhi-ncr",
    # Sydney needs NO rename, after two wrong answers in one day (2026-08-17).
    # #aaifsydney looked mergeable (small, meetup-era name), then looked like
    # the real chapter room (all the recent activity) — its own message history
    # settled it: a TEMPORARY event-coordination room with external sponsor
    # folk in it, to be left alone under its own name. #sydney (2022, 57
    # members) stays the city room; #sydney-organizers is planned from the
    # sheet and currently blocked by an invisible private squatter.
    # 2026-08-17 (user-decided): the last local-language keep falls too —
    # English/ASCII wins for the same typability reason as medellin. The
    # organizer room was deprecated by hand in the admin UI
    # (#munchen-organizers-deprecated, 4 members to invite into the fresh
    # #munich-organizers the sheet now plans).
    "munchen": "munich",
}

#: Merges: the room is retired and its chapter moves to another room. Distinct
#: from a rename because the members do NOT come with it — they are invited
#: across by invite_organizers.py, which is why `into` must already exist (or be
#: the target of a rename in the same run) before the retirement lands.
CHANNEL_MERGES = {
    "southbay-chapter-leads": {"into": "bay-area-organizers",
                               "retire_as": "southbay-chapter-leads-deprecated"},
    # meetup-seattle -> seattle APPLIED and removed 2026-08-17: the retired room
    # is #meetup-seattle-deprecated, and a junk #meetup-seattle recreated under
    # the freed name (since archived) was matching this entry and would have
    # been "retired" into a name_taken failure.
    # The aaifsydney -> sydney merge that used to sit here was REVERSED
    # 2026-08-17: #aaifsydney is the active room (see CHANNEL_RENAMES), so the
    # merge became a name swap and the entry had to go — left in place it would
    # have re-retired the room the swap just promoted, under its old id.
    # Provisioning created this 2026-08-16 while Barcelona's real organizer
    # room (#barcelona-organizers, 2024, 16 members) was an invisible private
    # channel. Once revealed, the real room wins; the fresh room's 3 members
    # are invited across.
    "meetup-barcelona-organizers": {
        "into": "barcelona-organizers",
        "retire_as": "meetup-barcelona-organizers-deprecated"},
}

#: Where each retired room's people should go — the deprecated-room sweep
#: refuses to archive a room it cannot leave a forwarding address for. Public
#: rooms point at the chapter's PUBLIC channel (a pointer into a private room
#: is a door nobody can open); private rooms name the private successor, used
#: for the membership-coverage check rather than a post.
DEPRECATED_POINTERS = {
    "london-organizers-deprecated": "london",
    "meetup-seattle-deprecated": "seattle",
    "southbay-chapter-leads-deprecated": "bay-area-organizers",
    "luxembourg-organizers-deprecated": "luxembourg-organizers",
    "munchen-organizers-deprecated": "munich-organizers",
    "meetup-barcelona-organizers-deprecated": "barcelona-organizers",
}

FAREWELL = ("This channel is retired. The community now lives in <#%s> — "
            "see you there! (This room will be archived; its history stays "
            "searchable.)")


def plan_archives(api, by_name, live, self_id):
    """What the deprecated-room sweep would do: [(name, action, detail)].

    action is 'archive' (public: post pointer first), 'blocked' or 'skip'.
    The rules differ by room type, deliberately:

    - **Public**: post the farewell pointer, then archive. Members chose a
      public room and can follow a pointer with one click; mass-inviting them
      anywhere is not this script's call.
    - **Private**: archive only when every member is already in the successor
      room (the token's own account excepted). A private room's members were
      curated, so archiving out from under someone who has not been moved yet
      would cut an organizer off from their chapter. Stragglers are counted
      and named as the reason, and inviting them stays invite_organizers.py's
      intake-gated job.
    """
    plans = []
    for name in sorted(live):
        if not name.endswith("-deprecated"):
            continue
        if name in CHANNEL_RENAMES:
            # Queued to be renamed back into service (e.g. Austin's real room,
            # parked at -deprecated while a squatter holds its city name) —
            # not a room to close, whatever its name says today.
            plans.append((name, "skip", "pending rename to #%s — not retired"
                          % CHANNEL_RENAMES[name]))
            continue
        target = DEPRECATED_POINTERS.get(name)
        if not target:
            plans.append((name, "blocked", "no recorded successor — add it to "
                          "DEPRECATED_POINTERS or archive by hand"))
            continue
        if target not in by_name or by_name[target]["is_archived"]:
            plans.append((name, "blocked", "successor #%s is not live" % target))
            continue
        room = by_name[name]
        if not room.get("is_private"):
            plans.append((name, "archive", "public; pointer post -> #%s, then "
                          "archive" % target))
            continue
        members = set(slackmod.members(api, room["id"])) - {self_id}
        stragglers = members - set(slackmod.members(api, by_name[target]["id"]))
        if stragglers:
            plans.append((name, "blocked", "%d member(s) not yet in #%s — "
                          "invite them across first" % (len(stragglers), target)))
        else:
            plans.append((name, "archive", "private; all members already in "
                          "#%s" % target))
    return plans


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
            "  2. OAuth & Permissions -> User Token Scopes: channels:write, "
            "groups:write,\n     chat:write, channels:join, "
            "channels:write.invites, groups:write.invites\n"
            "  3. Install to Workspace, copy the User OAuth Token (xoxp-...)\n"
            "  4. export %s='xoxp-...'\n\n"
            "Use a USER token, not a bot token: the creator of a channel joins "
            "it, and\na bot sitting in 128 chapter rooms is noise. Note the "
            "same applies to you —\nwhoever's token this is will be a member of "
            "every channel it creates." % WRITE_TOKEN_ENV)
    return token


def call_write(token, method, **params):
    """POST a write method. Refuses anything outside WRITE_METHODS.

    Retries rate limits (and only rate limits): a 130-create burst trips
    Slack's limiter partway through, and without this the tail of the batch
    fails for a reason that fixes itself. Honouring Retry-After is what the
    read client does too; every other error still returns to the caller.
    """
    if method not in WRITE_METHODS:
        raise ValueError("%s is not a sanctioned write method." % method)
    for attempt in range(5):
        req = urllib.request.Request(
            API + method, data=urllib.parse.urlencode(params).encode(),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                time.sleep(_retry_secs(exc.headers.get("Retry-After")))
                continue
            return {"ok": False, "error": "http_%d" % exc.code}
        if not payload.get("ok") and payload.get("error") == "ratelimited" and attempt < 4:
            time.sleep(_retry_secs(payload.get("retry_after")))
            continue
        return payload
    return {"ok": False, "error": "ratelimited"}


def _retry_secs(value, default=10):
    """A usable sleep out of whatever Retry-After held.

    The header is allowed to be an HTTP-date, and fractional strings appear in
    the wild; a ValueError here would abort a mutation batch partway through
    and take the applied/failed summary with it — the exact loss the retry
    exists to prevent.
    """
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return default


def plan(tables, live, all_names=None):
    """Return (creates, renames, blocked, merges, already, applied).

    `all_names` is every channel name INCLUDING archived ones (defaults to
    `live`). Applied-rename detection must use it: once the deprecated-room
    sweep archives #london-organizers-deprecated, that name stops being live,
    and a live-only check would re-plan the London chain's first step —
    renaming the REAL organizer room to -deprecated.

    `creates` is [(name, is_private, why)]. A name is planned exactly once even
    when several chapters share it — #india serves six, and asking Slack to
    create it six times would be five errors and one channel.
    """
    creates, already, seen = [], [], set()
    # A name a rename will free INTO is satisfied by that rename, not by a
    # create: renames run first, so `conversations.create` would answer
    # name_taken — the right room exists, but the run reads as failed.
    rename_targets = {n for o, n in CHANNEL_RENAMES.items() if o in live}

    def want(name, private, why):
        if not name or name == NO_RESOURCE or name in seen:
            return
        seen.add(name)
        (already if name in live or name in rename_targets
         else creates).append((name, private, why))

    for table, private, label in (("public", False, "chapter channel"),
                                  ("organizers", True, "organizer channel"),
                                  ("regional", False, "country channel")):
        for city, name in sorted(tables[table].items()):
            want(name, private, "%s for %s" % (label, city))

    # Only rename what is actually there: a rename already applied is a no-op,
    # not an error to re-attempt every run. One subtlety makes that harder than
    # `o in live`: after a CHAIN applies (#london-organizers moved away, then
    # #london-meetup-organizers took its name), the old name is live AGAIN —
    # held by the right room. Re-planning it would report the chain as blocked
    # and refuse the whole run. So a rename whose target exists is treated as
    # applied when its old name is another rename's target (the map itself
    # re-occupied it); a target squatted by anything else still blocks, because
    # that genuinely is a room the plan cannot account for.
    # The target-exists check runs against ALL names, not just live ones: an
    # archived room holding the target is the same evidence of "applied" (the
    # sweep archives retired rooms), and a not-yet-applied rename into an
    # archived name would only ever answer name_taken anyway.
    if all_names is None:
        all_names = live
    retaken = set(CHANNEL_RENAMES.values())
    wanted = {o: n for o, n in CHANNEL_RENAMES.items()
              if o in live and not (n in all_names and o in retaken)}
    # What the filter above classified as applied is REPORTED, never silently
    # dropped: the classification is an inference, and an entry it swallows by
    # mistake would otherwise vanish from the plan with no trace — the chain
    # step that was meant to free a name simply never appearing anywhere.
    applied = sorted((o, n) for o, n in CHANNEL_RENAMES.items()
                     if o in live and o not in wanted)
    renames, blocked = order_renames(wanted, live)
    merges = [(o, m) for o, m in sorted(CHANNEL_MERGES.items()) if o in live]
    return creates, renames, blocked, merges, already, applied


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; see the module docstring")
    a = ap.parse_args()

    # The CLI credential expired for good in 2026-08 and cannot be re-scoped,
    # so reads fall back to the same env token the writes use — it carries the
    # read scopes (the audits run on it), and a run that has no env token still
    # works read-only off live CLI creds where those exist.
    token = os.environ.get(WRITE_TOKEN_ENV, "").strip() or slackmod.load_token()
    api = slackmod.Slack(token=token)
    who = api.ok("auth.test")
    print("workspace: %s (%s)\n" % (who.get("team"), who.get("team_id")))

    chans = slackmod.channels(api)
    live = {c["name"] for c in chans if not c["is_archived"]}
    by_name = {c["name"]: c for c in chans}
    _, tables = ao.read_chapters()
    creates, renames, blocked, merges, already, applied = plan(
        tables, live, set(by_name))
    # Only rooms ALREADY deprecated at plan time: one this run retires still
    # has its people, so it waits for the next run — by which point they have
    # had the pointer, or (private) have been invited across.
    archives = plan_archives(api, by_name, live, who.get("user_id"))

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
    if applied:
        print("\n  Classified as already applied (old name re-occupied by the "
              "map itself, %d):" % len(applied))
        for old, new in applied:
            print("    #%s -> #%s" % (old, new))
    print("\nTo MERGE (%d) — the room is retired, members are invited across by"
          "\ninvite_organizers.py (a rename would NOT carry them):" % len(merges))
    for old, m in merges:
        print("  #%s -> into #%s, retired as #%s"
              % (old, m["into"], m["retire_as"]))
    print("\nDeprecated-room sweep (%d):" % len(archives))
    for name, action, detail in archives:
        print("  #%-38s %-8s %s" % (name, action.upper(), detail))

    if not a.write:
        print("\nReport only. Nothing was sent to Slack.")
        return 0
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

    created_names = set()
    for name, private, why in creates:
        r = call_write(token, "conversations.create", name=name,
                       is_private="true" if private else "false")
        if r.get("ok"):
            done += 1
            created_names.add(name)
            print("created #%s" % name)
        else:
            # One bad name must not abandon the batch — a public form feeds these
            # city names, so a rejected slug is normal input, not a crash.
            err = r.get("error")
            if err == "name_taken":
                # The plan only sees private channels the token owner is in, so
                # name_taken on a "missing" channel means the room EXISTS as a
                # private channel this token cannot see. The fix is an invite
                # for the token owner, never a create — say so, or every rerun
                # reads as the same mysterious failure.
                failed.append("create %s: name_taken — exists as a private "
                              "channel this token is not in; needs an invite, "
                              "not a create" % name)
            else:
                failed.append("create %s: %s" % (name, err))

    # Retirements LAST, and only after their target VERIFIABLY exists: the
    # members of a retired room have not been invited across yet (that is
    # invite_organizers' job), so retiring a room whose target rename or
    # create failed above would strand them in a -deprecated room pointing at
    # nothing. "The comment says only-after-target-exists" is not a check —
    # this set is.
    renamed_to = {new for old, new in renames
                  if not any(f.startswith("rename %s:" % old) for f in failed)}
    # `live`, not `set(by_name)`: an ARCHIVED room holding the target name is
    # not a room members can be pointed at, and archived junk under a wanted
    # name has already happened twice on this estate.
    live_names = live | renamed_to | created_names
    for old, m in merges:
        if m["into"] not in live_names:
            failed.append("retire %s: target #%s does not exist (its rename or "
                          "create failed above) — NOT retired" % (old, m["into"]))
            continue
        r = call_write(token, "conversations.rename",
                       channel=by_name[old]["id"], name=m["retire_as"])
        if r.get("ok"):
            done += 1
            print("retired #%s -> #%s (run invite_organizers.py to move its "
                  "members into #%s)" % (old, m["retire_as"], m["into"]))
        else:
            failed.append("retire %s: %s" % (old, r.get("error")))

    for name, action, detail in archives:
        if action != "archive":
            continue
        # Belt to the planner's braces: this is the only call site of
        # conversations.archive, and it must stay impossible to point at a
        # room a rename did not first retire on purpose. A hard raise, not an
        # assert — `python -O` strips asserts, and this is the one guard
        # between a planner bug and closing a live room.
        if not name.endswith("-deprecated"):
            raise RuntimeError("archive sweep reached a non-deprecated room: "
                               "#%s" % name)
        room = by_name[name]
        if not room.get("is_private"):
            # `is_member` reflects the READ token's account; the write token
            # may differ, and joining an already-joined room is a harmless
            # `already_in_channel`. So always join, and treat only a real
            # failure as one — otherwise a missing `channels:join` scope
            # surfaces later as a baffling not_in_channel on the farewell.
            j = call_write(token, "conversations.join", channel=room["id"])
            if not j.get("ok") and j.get("error") not in (
                    "already_in_channel", "method_not_supported_for_channel_type"):
                failed.append("join %s: %s — cannot post the farewell, NOT "
                              "archived" % (name, j.get("error")))
                continue
            p = call_write(token, "chat.postMessage", channel=room["id"],
                           text=FAREWELL % by_name[DEPRECATED_POINTERS[name]]["id"])
            if not p.get("ok"):
                failed.append("farewell post in %s: %s — NOT archived (a room "
                              "must not close without its forwarding address)"
                              % (name, p.get("error")))
                continue
        r = call_write(token, "conversations.archive", channel=room["id"])
        if r.get("ok"):
            done += 1
            print("archived #%s" % name)
        else:
            failed.append("archive %s: %s" % (name, r.get("error")))

    print("\n%d applied, %d failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)
    print("\nNobody was invited to anything. `Organizer Handles` on the Chapters "
          "List says who belongs in each organizer channel; inviting them is a "
          "human's job.")
    # The return code is the ONLY signal a caller or && chain gets — a run
    # where every rename failed must not read as success.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
