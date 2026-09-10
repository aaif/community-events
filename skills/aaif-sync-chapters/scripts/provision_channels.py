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

## What it will and will not do

- **Archives only rooms a rename already retired** — the deprecated-room sweep
  (authorised 2026-08-17) closes `*-deprecated` rooms only, public ones only
  after a farewell pointer post lands, private ones only once every member is
  already in the successor. Nothing here deletes, and nothing archives a room
  under its working name.
- **Invites ops staff, and nobody else.** This script builds rooms and puts the
  workspace's own admins in them; it does not put *community members* in them.
  That stays `invite_organizers.py`'s job, deliberately separate: it needs
  different scopes, and it is less reversible than anything here. A channel can
  be archived; an invitation notification cannot be unsent.

  The carve-out, added 2026-09-10, is narrow on purpose. An audit that day found
  17 of 87 organizer channels — every room provisioned on 2026-08-21 and
  2026-08-31 — with neither ops account in them, because *nothing in the repo
  had ever put them there*: the 70 healthy rooms were populated by a one-off
  manual pass, so each new provisioning run silently reopened the same hole.
  Seeding an admin into a room this script just created is part of building the
  room, not a decision about who belongs in the community. Putting it here
  rather than in `invite_organizers.py` is what makes a room correct at birth
  instead of correct at the next sweep.

  The roster is expected to be a short list of workspace admins maintained by
  ops. Nothing here can verify "admin", so the `Slack Config` tab is a TRUSTED
  surface, and two guards bound the damage a bad row can do: an address outside
  `Staff email domain` is refused, and a roster longer than MAX_OPS aborts the
  phase. Both exist because an invitation cannot be unsent.

  Their addresses are NOT in this file. They are `Ops staff email` rows on the
  Chapters List `Slack Config` tab, read through `audit_organizers.load_config`
  — this repo is public and CLAUDE.md forbids committing PII. With no such row
  the phase reports that it is disarmed and seeds nothing; it never falls back
  to a hardcoded account.
- **Posts each chapter's Drive folder link in its ORGANIZER room, and pins it
  where it can** (2026-09-10) — never in the public chapter room. The folder
  holds the CRM, budgets and trackers and is shared per-organizer; posting its
  link publicly would not leak anything (Drive still refuses everyone else) but
  it would generate a steady stream of Request Access clicks from members who
  cannot have it, already a recurring support load here.

  Pinning needs `pins:write`, which this estate's token does not carry as of
  2026-09-10. Rather than abort, the phase POSTS UNPINNED and says so on every
  run. Pinning that backlog later needs `pins:read` **and** `pins:write`: without
  `pins:read` a run cannot tell a pinned message from an unpinned one, so it
  leaves an existing post alone rather than re-pinning something already pinned.
  With both, the next run pins the message it already posted — never a second
  copy.

  Idempotency is by folder ID. With `pins:read` it checks the pins and the
  recent history (the second catches a post whose `pins.add` failed, which a
  pins-only check would repost). Without it, history alone — sufficient,
  because a pinned message is still a message, but bounded to one page, which
  is why `pins:read` is still worth having.
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
   `chat:write` (the deprecated sweep's farewell pointer and the folder link),
   in `$AAIF_SLACK_WRITE_TOKEN`. `pins:read`/`pins:write` are OPTIONAL and
   listed in OPTIONAL_SCOPES: without them the folder link is posted unpinned
   and every run says so, rather than the run refusing. `channels:join` is also
   worth requesting: the
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
import http.client
import json
import os
import re
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
# sync_resources is the module that WRITES `Chapter Folder` cells and names the
# erstwhile column, so it owns how both are read. Imported at module scope since
# 2026-09-10: it was previously imported inside main() on the belief that it
# "pulls in the whole CRM engine" and would break the unit tests, which import
# this module with no sheet to read. That is no longer true — the tests import
# it top-level and pass in 0.04s — and two import sites for one module is how
# they drift.
from sync_resources import (ERSTWHILE_COLUMN, FOLDER_URL,  # noqa: E402
                            folder_id as sync_folder_id, read_erstwhile)

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
#: until 2026-08-17, when archiving retired rooms was authorised, and are
#: fenced by guards that keep the original fears in place:
#:
#: - archive refuses any channel whose name does not end `-deprecated`, so the
#:   only rooms it can close are ones a rename already retired on purpose, and
#:   it is reachable from exactly one place — the deprecated-room sweep below;
#: - postMessage has THREE call sites, all of the same shape: one standing
#:   message with fixed wording, in a room the caller is already acting on. The
#:   farewell pointer in a room being archived (public rooms only — a pointer
#:   nobody can follow into a private room helps no one); the standing directory
#:   message in post_country_directory.py; and, since 2026-09-10, the folder
#:   link. This allowlist is shared, so the count includes the other script.
#:   It is still never used to speak into a room for any other reason.
#:
#: `conversations.join` is here because a user token cannot post in a public
#: channel it has not joined. Two call sites, both join immediately before
#: posting: the deprecated-room sweep below (archived right after, so the
#: membership is momentary) and post_country_directory.py (posts a standing
#: directory message and stays joined — the room is staying live, not closing).
#: `pins.add` joined on 2026-09-10 for the folder-pin phase. `pins.remove` did
#: NOT: this phase only ever adds a pin nobody has, and unpinning is how a
#: human says "that is no longer the thing to read first". A script that can
#: unpin can silently undo that, one room at a time, with no record.
WRITE_METHODS = frozenset({"conversations.create", "conversations.rename",
                           "conversations.invite", "conversations.kick",
                           "conversations.archive", "conversations.join",
                           "chat.postMessage", "pins.add"})

#: Scopes each write needs. Checked up front so a missing scope fails before the
#: first channel rather than half way through the batch.
# `channels:write` is the USER-token scope for conversations.create/rename;
# `channels:manage` is its bot-token sibling and never appears on a user token.
# `chat:write` covers the deprecated-sweep's farewell pointer and the folder link.
#
# `pins:read`/`pins:write` are deliberately NOT here. The folder-link phase
# degrades without them — it posts the link and reports that it could not pin —
# and making them required would abort renames, creates and the ops seed over a
# capability none of those need. OPTIONAL_SCOPES is what the phase checks.
NEEDED_SCOPES = ("channels:write", "groups:write", "chat:write")

#: Capabilities that change what a phase can do without stopping the run.
#: PRINTED WHEN ABSENT, so an operator can see which capability a phase is
#: running without — a phase that silently did less than its name suggests is
#: the failure this printout exists to prevent. This dict is the REPORT, not
#: the check: behaviour keys off the scope sets directly, because the read and
#: write halves of a run can hold different tokens.
OPTIONAL_SCOPES = {
    "pins:read": "tell a pinned link from an unpinned one (needed to pin a backlog)",
    "pins:write": "pin the folder link (else it is posted unpinned)",
    "channels:history": "read a public room's messages for the folder-link check",
    "groups:history": "read a PRIVATE organizer room's messages for the same check",
    "channels:join": "join a public room before posting the deprecated-sweep farewell",
}

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
    # rename was DROPPED with the #austin plan; on 2026-08-22 the qualified
    # convention then renamed the pair by hand: #austin-area -> #austin-tx and
    # #austin-area-organizers -> #austin-tx-organizers (see the block below).
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
    # (NOT meetup-barcelona-organizers -> barcelona-organizers: its "squatter"
    # turned out to be the REAL organizer room — 2024, 16 members — so the
    # freshly provisioned 3-member room merges into it instead; see
    # CHANNEL_MERGES.)
    # 2026-08-22 (user-decided, "fewest divergences"): squat-affected organizer
    # rooms take the QUALIFIED convention `<city>-<state|countrycode>-organizers`
    # instead of waiting on their invisible squatters — the suffix stays, only
    # the city slug gains a code, mirroring the austin-tx / charlotte-nc /
    # dallas-tx city rooms. The qualified targets have NO squatter, and every
    # rename below was APPLIED BY HAND the same day (kept here as the record;
    # plan() classifies them applied because the old names are no longer live).
    # Also applied by hand, no entries needed: the seven rooms that briefly
    # carried reversed organizers-<city> names (Toronto/Melbourne/Milan/Oslo/
    # Seoul/Sydney/Vancouver) were renamed to their qualified forms, and
    # #austin-area / #austin-area-organizers became #austin-tx /
    # #austin-tx-organizers.
    "meetup-seattle-organizers": "seattle-wa-organizers",
    # APPLIED 2026-09-03 and removed from this map (same hazard as the block
    # above: a junk #seattle-chapter-leads recreated under the freed old name
    # would re-plan this rename against a room that genuinely holds the
    # target, and block the whole run): seattle-chapter-leads ->
    # seattle-wa-organizers. #seattle-wa-organizers (the room the rename above
    # landed on) turned out to be the wrong one — #seattle-chapter-leads
    # (2026, 14 members, active) was the real organizer room;
    # #seattle-wa-organizers sat unused and was archived, freeing its name
    # (archived rooms don't hold their name against order_renames' `taken`
    # set) for the real room to reclaim. #seattle-organizers (unqualified) is
    # an invisible private squatter — unreachable by this token — which is
    # why the qualified name exists at all.
    "washington-dc-the-capital": "washington-dc",
    # Austin, full history: #austin is squatted with no path to free it on the
    # Pro plan. 2026-08-21 parked the chapter on its historical #austin-area
    # (junk room archived as #austin-area-junk); 2026-08-22's qualified
    # convention superseded that with #austin-tx (the sheet's Slack Channel
    # cell now says `austin-tx`, KEPT_NON_CONVENTIONAL carries it, and the
    # squatted `austin`/`austin-organizers` names live in the sheet's
    # Erstwhile Channels column). Do NOT re-plan #austin even if the squatter
    # is someday cleared.
    "portland-oregon-organizers": "portland-or-organizers",
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
    # members) stays the city room; the organizer room is
    # #sydney-au-organizers (created 2026-08-22 under the qualified
    # convention — the squatted #sydney-organizers is recorded as erstwhile).
    # munchen -> munich DROPPED 2026-08-22 (user-decided): #munich is squatted
    # by an invisible private room, so #munchen (107 members) keeps its native
    # name — the #españa / #deutschland precedent. The sheet's Slack Channel
    # cell says `munchen` and KEPT_NON_CONVENTIONAL carries it; do NOT re-plan
    # #munich even if the squatter is someday cleared. (The organizer room is
    # unaffected: #munich-organizers was adopted 2026-08-17 and is live.)
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
#: takes a SEPARATE token from `AAIF_SLACK_WRITE_TOKEN`. Since 2026-08-22 the
#: READ path here (and in invite_organizers) prefers the same env token: the
#: CLI credential expired for good that month and cannot be re-scoped, so the
#: old "reads never touch the write token" separation is retired for these two
#: scripts — the remaining guarantees are call-level, not token-level:
#: call_write() is the only door to WRITE_METHODS, and every write still sits
#: behind `--write --i-have-approval`. (On a machine with live CLI creds and
#: no env token, reads still work read-only off the CLI credential.)
WRITE_TOKEN_ENV = "AAIF_SLACK_WRITE_TOKEN"


def write_token(required=True):
    """The token for write calls, or a SystemExit explaining how to get one.

    `required=False` returns None instead of exiting — that is how the REPORT
    reads this token's scopes without demanding one. Report mode must still run
    on a machine that has no write token at all; it just cannot say whether
    pinning is available, and says that instead of guessing.

    Environment then the repo-root `.env` — the same two SOURCES, in the same
    order, that `slackmod.load_token()` searches. Not the same resolution:
    load_token sweeps BOTH token vars through the environment before touching
    `.env`, and prefers AAIF_SLACK_READ_TOKEN. So the two can land on different
    credentials, which is exactly why scope questions about a WRITE call must be
    asked of this token and never of the read client. Before the `.env` fallback existed, a machine holding the token
    only in `.env` (which is how this estate stores it, gitignored) read the
    plan fine and then failed at the write with "No write token", and the
    obvious workaround was `export AAIF_SLACK_WRITE_TOKEN=...` on the command
    line — which CLAUDE.md forbids precisely because shell history and terminal
    transcripts both keep it forever.
    """
    token = os.environ.get(WRITE_TOKEN_ENV, "").strip()
    if not token:
        token = slackmod._dotenv_token(slackmod.DOTENV_PATH, WRITE_TOKEN_ENV) or ""
        token = token.strip()
    if not token:
        if not required:
            return None
        raise SystemExit(
            "No write token. The Slack CLI token is read-only and its scopes "
            "cannot be widened, so writes use a separate app token:\n"
            "  1. api.slack.com/apps -> Create New App -> From scratch\n"
            "  2. OAuth & Permissions -> User Token Scopes: channels:write, "
            "groups:write,\n     chat:write, channels:join, "
            "channels:write.invites, groups:write.invites\n"
            "  3. Install to Workspace, copy the User OAuth Token (xoxp-...)\n"
            "  4. put %s=xoxp-... in the gitignored repo-root .env\n"
            "     (NOT `export` on the command line — shell history and the\n"
            "     terminal transcript both keep it; see CLAUDE.md)\n\n"
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
            # The lib's opener refuses redirects: the default one would re-send
            # this request — WRITE token and all — to whatever host a 3xx names.
            with slackmod._urlopen(req, 30) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                time.sleep(_retry_secs(exc.headers.get("Retry-After")))
                continue
            return {"ok": False, "error": "http_%d" % exc.code}
        except (http.client.HTTPException, urllib.error.URLError, ConnectionError,
                TimeoutError, json.JSONDecodeError) as exc:
            # The read client handles these; this path did not, so a timeout or
            # an HTML outage page mid-batch propagated out of main() as a
            # traceback — after an arbitrary number of channels had been
            # mutated, and taking the applied/failed summary with it. That
            # summary is the operator's only record of what to redo.
            if attempt < 4:
                time.sleep(2 * (attempt + 1))
                continue
            return {"ok": False, "error": "transport_failed: %r" % (exc,)}
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


def plan(tables, live, all_names=None, forbidden=frozenset()):
    """Return (creates, renames, blocked, merges, already, applied, refused).

    `forbidden` is the erstwhile-name set (sync_resources.read_erstwhile):
    creates and rename TARGETS matching it are moved to `refused` — inside
    plan(), not at the call site, so no future caller can obtain an unfiltered
    plan. The filter runs BEFORE rename ordering, so a refused rename can
    never leave order_renames() counting on a name it would have freed.

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
    creates, wanted_pairs, refused = forbid_erstwhile(
        creates, sorted(wanted.items()), forbidden)
    wanted = dict(wanted_pairs)
    renames, blocked = order_renames(wanted, live)
    merges = [(o, m) for o, m in sorted(CHANNEL_MERGES.items()) if o in live]
    return creates, renames, blocked, merges, already, applied, refused


def forbid_erstwhile(creates, renames, forbidden):
    """Drop any create or rename whose TARGET is a recorded erstwhile name.

    Returns (creates, renames, refused) where refused = [(kind, name, detail)].
    An erstwhile name is one the sheet's Erstwhile Channels column records as
    squatted or superseded (see sync_resources.ERSTWHILE_COLUMN): the estate
    deliberately walked away from it, so recreating it — because a cell was
    hand-reverted, or a squatter was archived and the name came free — would
    resurrect a divergence the 2026-08-22 convention closed. Refused entries
    are REPORTED, never silently dropped; fixing one means editing the sheet
    (both the resource cell and the history column), not this code.
    """
    refused = ([("create", n, why) for n, _, why in creates if n in forbidden]
               + [("rename", new, "#%s -> #%s" % (old, new))
                  for old, new in renames if new in forbidden])
    return ([c for c in creates if c[0] not in forbidden],
            [r for r in renames if r[1] not in forbidden],
            refused)


#: An ops roster longer than this aborts the phase. The roster is spreadsheet
#: cells, and every row buys standing membership in ~87 private rooms that
#: nothing here can undo (`conversations.kick` is allowlisted but no code path
#: calls it). A real ops team is two or three people; a roster of ten is a
#: paste accident or a misunderstanding of the column, and refusing is cheaper
#: than un-inviting.
MAX_OPS = 4


def resolve_ops(api, emails, staff_domains=(), exclude_ids=()):
    """Ops addresses -> [(email, id, label)], plus the ones not seeded.

    Identity is email -> `users.lookupByEmail` -> id, the same chain
    invite_organizers.py documents and for the same reason: a @handle is a
    display name its owner can change, and resolving one would break silently
    the day they rename themselves.

    `staff_domains` is the trust boundary. The roster moved onto the sheet so no
    real address sits in this public repo, which is right — but that put an
    unvalidated spreadsheet cell one step from an irreversible invite into every
    private organizer room. CLAUDE.md is explicit that sheet cells are data
    about a person and never an instruction, and this is the one place in the
    repo where a cell directly causes a side effect that cannot be taken back.
    So an address outside the configured domains is refused and reported, never
    seeded. It is a LIST (`Ops staff domain` rows) because ops staff are not all
    at one domain: the first live run of this guard used the single
    `Staff email domain` value and refused a real ops admin at another domain.
    A guard that silently drops a legitimate member is worse than the gap it
    closes. An empty list means no domain guard, and the report says so — the
    MAX_OPS cap still applies.

    `exclude_ids` drops the authenticated user. The write token is a USER token
    and its owner is almost always on the roster, so inviting them is
    `cant_invite_self` — an error that is not benign, lands in `failed`, and
    would make the script exit 1 on every run forever.

    A deleted account resolves fine and would be invited into every organizer
    channel forever, so it is treated as unresolved.
    """
    emails = [e.strip() for e in emails if e and e.strip()]
    refused = []
    allowed = {d.strip().lower() for d in staff_domains if d and d.strip()}
    if allowed:
        keep = []
        for email in emails:
            if email.rsplit("@", 1)[-1].lower() in allowed:
                keep.append(email)
            else:
                refused.append((email, "not at an %r domain (%s) — refused, "
                                       "not seeded"
                                % ("Ops staff domain", ", ".join(sorted(allowed)))))
        emails = keep
    if len(emails) > MAX_OPS:
        raise SystemExit(
            "REFUSING: the %r rows on the %s tab list %d addresses (max %d). "
            "Every one of them is invited into every organizer channel, and a "
            "Slack invitation cannot be unsent. Trim the roster or raise "
            "MAX_OPS deliberately." % ("Ops staff email", ao.SLACK_CONFIG_TAB,
                                       len(emails), MAX_OPS))
    found = slackmod.lookup_emails(api, emails) if emails else {}
    ops, unresolved = [], list(refused)
    for email in sorted(set(emails)):
        rec = found.get(email) or {"id": None, "error": "not_looked_up"}
        if not rec.get("id"):
            unresolved.append((email, rec.get("error", "unknown")))
        elif rec.get("deleted"):
            unresolved.append((email, "deleted account"))
        elif rec["id"] in exclude_ids:
            unresolved.append((email, "this run's own token owner — Slack "
                                      "refuses cant_invite_self; already in "
                                      "every room this token created"))
        else:
            ops.append((email, rec["id"], rec.get("real_name") or rec.get("name") or email))
    return ops, unresolved


#: A Drive folder id must look like one before it can be posted as a link.
#: Extraction itself is `sync_resources.folder_id` — the module that WRITES
#: these cells — rather than a second parser here. A reader that disagrees with
#: the writer is how a format drifts, and the first version of this phase did
#: exactly that.
#:
#: What this adds on top is a shape check. `sync_resources.folder_id` returns
#: the cell unchanged when it holds no "/", so a note or a placeholder comes
#: back as an "id". Without this, such a cell becomes a pinned standing message
#: pointing at nothing.
#:
#: The floor is 25, and the two numbers either side of it are both real. Every
#: `Chapter Folder` id on this sheet is 33 characters; the junk that has to be
#: rejected includes `not-a-drive-url` (15) and `TODO_ask_rahul_2026` (19) —
#: both legal charset, which is why the charset alone is not enough and why an
#: earlier 19 floor let the second one through into the plan. 25 clears every
#: real id by 8 and every placeholder anyone has actually typed by 6.
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{25,}$")


def folder_id(cell):
    """The Drive folder id in a `Chapter Folder` cell, or None.

    None means "this cell does not name a folder", and the caller reports the
    chapter rather than posting — a cell holding a note, a placeholder or a
    half-pasted URL must never become a pinned message.
    """
    fid = sync_folder_id(cell)
    return fid if fid and _DRIVE_ID.match(fid) else None


#: The pinned message. `%s` is the folder URL, which is always REBUILT from the
#: validated id via sync_resources.FOLDER_URL and never the sheet cell itself.
#: That is a security boundary, not tidiness: the cell is spreadsheet text an
#: editor controls, Slack renders `<url|anchor text>` as a link and `<!channel>`
#: as a notification, and this message goes out under an ops admin's own user
#: token into every organizer room. Posting the cell verbatim would let a sheet
#: edit publish an arbitrary link with arbitrary anchor text, in a message whose
#: own words tell the reader to trust it. The city is deliberately absent — the
#: room already says which chapter it is, and a name here is one more thing to
#: go stale after a rename.
FOLDER_PIN = (
    ":file_folder: *Your chapter's Drive folder* — templates, the CRM, banners "
    "and event assets all live here:\n%s\n\n"
    "_Access is granted per organizer. If Drive asks you to *request access*, "
    "you are almost certainly signed into a different Google account — check "
    "that first, and ask here rather than clicking Request Access._")


def read_chapter_folders():
    """({city: folder URL}, column_present) off the Chapters List.

    The flag is not decoration. Returning a bare {} made "the sheet has no
    `Chapter Folder` column" and "every chapter already has its link" print the
    identical `0 chapter(s)` line, with no third state — so renaming that column
    would switch the phase off permanently while every run reported success.
    The erstwhile guard below exists for exactly this reason and says so ("this
    sheet has silently broken a reader through restructuring once already");
    this reader now behaves the same way.

    Read here rather than added to `audit_organizers.read_chapters()` on
    purpose: that function is shared with the audits, and its `header_index`
    call ABORTS on a missing column. Teaching it a fifth required column would
    make every audit fail on a sheet that simply has no folder column yet, to
    serve one phase of one script.
    """
    rows = ao.gws_values(ao.CHAPTERS_ID, "'%s'!A:AZ" % ao.CHAPTERS_TAB)
    # `rows[0]` on an empty read is a bare IndexError. In the --write path this
    # function is called AFTER the creates and the ops seed have applied, so an
    # unguarded traceback there would kill main() before the applied/failed
    # summary printed — destroying the operator's only record of what just
    # happened to Slack. Every other reader in this skill guards the same way.
    if not rows:
        return {}, False
    headers = [h.strip() for h in rows[0]]
    if "Chapter Folder" not in headers:
        return {}, False
    idx = ao.header_index(headers, ao.CHAPTERS_TAB, "City", "Chapter Folder")
    out = {}
    for row in rows[1:]:
        city = ao.cell(row, idx["City"])
        folder = ao.cell(row, idx["Chapter Folder"]).strip()
        # NO_RESOURCE is live here (unlike in the channel tables, where
        # read_chapters has already turned it into None) because this reads the
        # raw cell.
        if city and folder and folder != NO_RESOURCE:
            out[city] = folder
    return out, True


def plan_folder_pins(api, tables, folders, channel_ids, can_read_pins=True):
    """Which organizer channels are missing a link to their Drive folder.

    Returns ([(city, channel, channel_id, folder_id, existing_ts)], skipped).
    `existing_ts` is None when a post is needed and a ts when a message is
    already there and only needs pinning. `skipped` is [(city, why)] for a
    chapter this phase passed over — a chapter absent from BOTH lists would be
    indistinguishable from one that is fine, so every path out of the loop
    lands in one of them.

    The plan carries the validated folder ID, never the sheet cell. The message
    URL is rebuilt from it (see FOLDER_PIN) so a spreadsheet edit cannot publish
    arbitrary Slack markup under an ops admin's token.

    The ORGANIZER room, never the public chapter room. The folder holds the CRM,
    budgets and trackers and is shared only with accepted organizers, so the
    link belongs where those people are. Posting it in a public room would not
    leak the contents — Drive still refuses everyone else — but it would
    generate a steady stream of Request Access clicks from members who cannot
    have it, which is already a recurring support load on this estate.

    Idempotency is by folder ID and needs BOTH signals, in both modes:

    * `pins.list` answers "is it pinned", which is NOT the same question as "is
      it posted". An ok-but-empty pins list means unpinned, not absent.
    * the channel history answers "is it posted", which is the one that decides
      whether to send another message.

    So an unreadable history is fatal to the decision in EVERY mode, and the
    guard below is unconditional. It used to read `if not readable and not
    can_read_pins`, on the reasoning that "with pins:read the pins already
    answered" — they had not. With `pins:read` granted and `groups:history`
    absent (private organizer rooms need it, and nothing here required it),
    every run found empty pins, an unreadable history, and posted a fresh copy
    into ~80 private rooms, cumulatively, reported as ordinary success. Granting
    `pins:read` — which this file's own prose recommends — was what turned the
    safe degraded mode into that. Reported, never silent, and never a post.
    """
    plan, skipped = [], []
    for city in sorted(folders):
        channel = tables["organizers"].get(city)
        # `read_chapters` stores None for the sheet's `none` sentinel, never the
        # literal string, so falsiness is the whole check here.
        if not channel:
            skipped.append((city, "no organizer channel on the sheet"))
            continue
        cid = channel_ids.get(channel)
        if not cid:
            skipped.append((city, "#%s is not a live channel this token can see" % channel))
            continue
        fid = folder_id(folders[city])
        if not fid:
            skipped.append((city, "Chapter Folder cell holds no Drive id: %r"
                            % folders[city][:40]))
            continue
        if can_read_pins:
            got = api.call("pins.list", channel=cid)
            if not got.get("ok"):
                skipped.append((city, "pins.list: %s" % got.get("error")))
                continue
            if any(fid in ((item.get("message") or {}).get("text", ""))
                   for item in got.get("items", [])):
                continue
        existing_ts, readable = _existing_link_ts(api, cid, fid)
        if not readable:
            skipped.append((city, "conversations.history unreadable — cannot rule "
                                  "out a link already in the room, so nothing "
                                  "posted (needs channels:history / groups:history)"))
            continue
        if existing_ts is not None and not can_read_pins:
            # The message is there. Whether it is PINNED is unknowable without
            # pins:read, so leave it alone rather than re-pinning something that
            # may already be pinned.
            continue
        plan.append((city, channel, cid, fid, existing_ts))
    return plan, skipped


def _existing_link_ts(api, cid, fid):
    """(ts of a message already carrying this folder id or None, history readable?).

    The second element matters: `(None, True)` means "checked, not there" and
    `(None, False)` means "could not check". Those are opposite decisions —
    post, versus refuse to guess — and collapsing them is how the phase used to
    double-post.

    The parameter is `fid`, not `folder_id`: that name is a module-level
    function, and shadowing it here would make any future normalisation inside
    this helper raise `TypeError: 'str' object is not callable` in a write path.

    Bounded to one page. In pins:read mode that is ample — this catches a
    message THIS phase posted and failed to pin, which is recent. Without
    pins:read the page IS the whole window; organizer rooms are the quietest on
    the estate (1-7 members), so it does not bind, but it is the reason
    pins:read is still worth having.
    """
    got = api.call("conversations.history", channel=cid, limit=200)
    if not got.get("ok"):
        return None, False
    for message in got.get("messages", []):
        # A match with no ts would return (None, True) = "checked, not there"
        # and repost. Slack always sends ts; the guard is free.
        if fid in message.get("text", "") and message.get("ts"):
            return message["ts"], True
    return None, True


def token_scopes(token, which):
    """The scope set for a token, or a SystemExit naming which token failed.

    NOT a bare `except`. `Slack.scopes()` raises SlackError for a dead token, a
    transport failure, or an outage page, and swallowing that into an empty set
    made every optional capability read as absent — printing a fabricated
    capability report as fact, which is precisely what OPTIONAL_SCOPES exists to
    prevent. This runs during planning, before any write, so failing loud is
    free.
    """
    try:
        return set(slackmod.Slack(token=token).scopes())
    except slackmod.SlackError as exc:
        raise SystemExit("Could not read the %s token's scopes (%s). Refusing to "
                         "guess which capabilities this run has — that guess "
                         "decides whether the folder phase posts or skips."
                         % (which, exc))


def plan_ops_seed(api, tables, ops, channel_ids):
    """Which organizer channels are missing which ops accounts.

    Returns ([(channel_name, channel_id, [(email, id, label)])], skipped, n_read).
    `skipped` is [(channel, why)] and `n_read` is how many rooms were actually
    examined — the count the report must use, because "all N channels already
    hold every ops account" is a claim about rooms whose membership was read,
    and it used to be printed over a denominator that included the ones that
    were not.

    Scope is `tables["organizers"]` — the sheet's Organizer Channel column, and
    only rooms this run can resolve to an id. Public chapter rooms and country
    rooms are deliberately out: they are public, anyone may join, and putting
    staff in them is not part of building the room.

    Two ways a room can fail to be checked, and BOTH are reported. A room the
    token cannot enumerate used to `continue` silently, dropping out of the
    plan, the skip list and the denominator at once — invisible. That is the
    worst possible handling here: the rooms a token cannot see are exactly the
    invisible private rooms this estate has ~38 of, and they are the rooms most
    likely to be missing an ops account. The audit that motivated this phase
    would have repeated, undetected.
    """
    seed, skipped, n_read = [], [], 0
    for name in sorted({n for n in tables["organizers"].values() if n}):
        cid = channel_ids.get(name)
        if not cid:
            skipped.append((name, "not a live channel this token can see — "
                                  "membership never checked"))
            continue
        try:
            members = set(slackmod.members(api, cid))
        except slackmod.SlackError as exc:
            skipped.append((name, str(exc)))
            continue
        n_read += 1
        missing = [o for o in ops if o[1] not in members]
        if missing:
            seed.append((name, cid, missing))
    return seed, skipped, n_read


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; see the module docstring")
    a = ap.parse_args()

    # This estate's CLI credential expired for good in 2026-08 and cannot be
    # re-scoped, so reads run on the same env token the writes use — it
    # carries the read scopes (the audits run on it). load_token now resolves
    # that token itself (environment, then ./.env, then the CLI credential)
    # and its error names it first, so this manual env read is belt-and-braces
    # rather than a correction — kept so the note below can warn about the
    # expired CLI fallback before any network call is made.
    token = os.environ.get(WRITE_TOKEN_ENV, "").strip()
    if not token:
        print("note: %s is not set — falling back to AAIF_SLACK_READ_TOKEN / the "
              "repo-root .env / the Slack CLI credential (expired on this "
              "estate). If auth fails, set %s in the environment or in the "
              "repo-root .env." % (WRITE_TOKEN_ENV, WRITE_TOKEN_ENV), file=sys.stderr)
        token = slackmod.load_token()
    read_token = token
    api = slackmod.Slack(token=read_token)
    who = api.ok("auth.test")
    print("workspace: %s (%s)\n" % (who.get("team"), who.get("team_id")))

    chans = slackmod.channels(api)
    live = {c["name"] for c in chans if not c["is_archived"]}
    by_name = {c["name"]: c for c in chans}
    _, tables = ao.read_chapters()
    forbidden, unparsed, col_present = read_erstwhile()
    creates, renames, blocked, merges, already, applied, refused = plan(
        tables, live, set(by_name), forbidden=forbidden)
    # Only rooms ALREADY deprecated at plan time: one this run retires still
    # has its people, so it waits for the next run — by which point they have
    # had the pointer, or (private) have been invited across.
    archives = plan_archives(api, by_name, live, who.get("user_id"))

    channel_ids = {n: c["id"] for n, c in by_name.items() if not c["is_archived"]}

    # Scopes, up front, and asked of the RIGHT token each time. `api` is the
    # READ client — load_token() prefers AAIF_SLACK_READ_TOKEN and only then
    # falls through — while every write runs under write_token(), which prefers
    # the write var. The two resolvers sweep the same two sources in different
    # orders, so on a .env holding both they land on different credentials.
    # Deriving `can_pin` from the read client therefore answered a question
    # about a token that will never carry pins:write, and every run posted
    # unpinned while advising the operator to add a scope they already had.
    read_scopes = token_scopes(read_token, "read")
    wtok = write_token(required=False)
    write_scopes = token_scopes(wtok, "write") if wtok else None
    # pins.list goes through `api`, so its scope question belongs to the read
    # token. pins.add goes through call_write, so its does not.
    can_read_pins = "pins:read" in read_scopes
    can_pin = write_scopes is not None and "pins:write" in write_scopes
    can_read_history = bool({"channels:history", "groups:history"} & read_scopes)

    # Ops roster off the sheet, never out of this file (public repo — CLAUDE.md).
    cfg = ao.load_config()
    ops_emails = cfg.get("ops_staff_emails") or []
    # A `(none)` sentinel row becomes "" in a list setting, so a roster of one
    # blank cell is [""] — truthy, and it used to skip the DISARMED line while
    # seeding nobody, telling the operator a different story than the empty case.
    ops_emails = [e for e in ops_emails if e and e.strip()]
    ops, ops_unresolved = [], []
    if ops_emails:
        try:
            ops, ops_unresolved = resolve_ops(
                api, ops_emails, staff_domains=cfg.get("ops_staff_domains") or (),
                exclude_ids={who.get("user_id")} - {None})
        except slackmod.SlackError as exc:
            # lookup_emails raises on anything but users_not_found — a missing
            # users:read.email scope, say. Failing loud is right, but not by
            # aborting renames, creates and the archive sweep, which have
            # nothing to do with this phase. Same reasoning that kept pins:* out
            # of NEEDED_SCOPES.
            ops_unresolved = [("(roster)", "lookup failed: %s — OPS SEED "
                                           "DISARMED for this run" % exc)]
    seed, seed_skipped, seed_read = (plan_ops_seed(api, tables, ops, channel_ids)
                                     if ops else ([], [], 0))
    folders, folder_col = read_chapter_folders()
    pins, pins_skipped = plan_folder_pins(api, tables, folders, channel_ids,
                                          can_read_pins=can_read_pins)

    # The guard's state prints on EVERY run, active or not: a run with the
    # column missing/renamed must not be indistinguishable from a run where
    # the guard loaded and had nothing to refuse — this sheet has silently
    # broken a reader through restructuring once already.
    if not col_present:
        print("WARNING: erstwhile guard INACTIVE — column %r is not on the "
              "sheet. On the live tab it has existed since 2026-08-22, so its "
              "absence means a rename/restructure disarmed the guard."
              % ERSTWHILE_COLUMN)
    else:
        print("erstwhile guard: %d name(s) protected%s"
              % (len(forbidden),
                 "; %d unparsable token(s) NOT protected: %s"
                 % (len(unparsed), ", ".join(sorted(set(unparsed))))
                 if unparsed else ""))
    print("Already exist: %d" % len(already))
    if refused:
        print("\nREFUSED — erstwhile name(s) the estate walked away from "
              "(fix the sheet, not the plan):")
        for kind, name, detail in refused:
            print("  #%-28s %-8s %s" % (name, kind, detail))
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

    # Ops seed. Planned against the rooms that exist NOW; rooms this run
    # creates are re-planned after the creates below, so a new room is seeded
    # in the same run that builds it — the whole point of the phase.
    print("\nOps seed — %s in every organizer channel:" % (
        ", ".join(label for _, _, label in ops) if ops else "(nobody)"))
    if not ops_emails:
        print("  DISARMED: no %r row on the %s tab. Nothing is seeded, and no "
              "account is assumed." % ("Ops staff email", ao.SLACK_CONFIG_TAB))
    for email, why in ops_unresolved:
        print("  NOT SEEDED %-34s %s" % (email, why))
    for name, why in seed_skipped:
        print("  UNCHECKED  #%-33s %s" % (name, why))
    if ops_emails and not ops:
        # The old code printed "all N channels already hold every ops account"
        # here, which was a claim about rooms nobody looked at.
        print("  NOTHING SEEDED: 0 of %d roster address(es) resolved to an "
              "account to seed." % len(ops_emails))
    elif ops and not seed:
        print("  All %d organizer channel(s) whose membership was READ already "
              "hold every ops account." % seed_read)
    for name, _, missing in seed:
        print("  #%-28s + %s" % (name, ", ".join(label for _, _, label in missing)))

    print("\nDrive folder link — %d chapter(s) to post, %d already posted and "
          "needing only a pin:"
          % (sum(1 for p in pins if p[4] is None),
             sum(1 for p in pins if p[4] is not None)))
    if not folder_col:
        print("  WARNING: folder-link phase INACTIVE — no 'Chapter Folder' "
              "column on the %s tab (or the tab read empty). Nothing is posted, "
              "and this is NOT the same as 'every chapter already has its "
              "link'." % ao.CHAPTERS_TAB)
    for scope, what in sorted(OPTIONAL_SCOPES.items()):
        if scope not in (read_scopes | (write_scopes or set())):
            print("  no %-18s -> cannot %s" % (scope, what))
    if not can_read_history:
        print("  no channels/groups:history -> the link check cannot read a room's "
              "messages, so every chapter is SKIPPED rather than risk a second post")
    if not can_pin:
        print("  Links will be POSTED UNPINNED. Pinning the backlog later needs "
              "BOTH pins:read and pins:write — with pins:write alone a run "
              "cannot tell a pinned message from an unpinned one, so it leaves "
              "existing posts alone and never pins them.")
    for city, channel, _, fid, existing_ts in pins:
        print("  %-22s -> #%-28s %s%s"
              % (city, channel, FOLDER_URL % fid,
                 "" if existing_ts is None else "  (already posted — pin only)"))
    for city, why in pins_skipped:
        print("  SKIPPED %-18s %s" % (city, why))
    if creates:
        print("\nNOTE: both phases above were planned against the estate as it is "
              "NOW. Rooms created by this run are re-planned after the creates, "
              "so --write may also seed and post into the %d room(s) listed under "
              "CREATE." % len(creates))

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
            # Keep the id under the NEW name. The sheet's Organizer Channel
            # column already holds the post-rename name, so without this a room
            # renamed by this run is invisible to the ops seed and the folder
            # link — reported as "not a live channel this token can see", which
            # is both wrong and confusing seconds after renaming it.
            if old in by_name:
                channel_ids[new] = by_name[old]["id"]
                channel_ids.pop(old, None)
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
            # Keep the new id: the ops seed below runs on this same pass, and a
            # room created here is exactly the room that would otherwise be born
            # without an admin in it.
            new_id = (r.get("channel") or {}).get("id")
            if new_id:
                channel_ids[name] = new_id
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

    # Ops seed, re-planned so rooms created a moment ago are included. The
    # report above was computed before the creates and would miss exactly the
    # rooms this phase exists to catch.
    if ops:
        seed, seed_skipped, _ = plan_ops_seed(api, tables, ops, channel_ids)
        for name, why in seed_skipped:
            failed.append("ops seed %s: %s — NOT seeded" % (name, why))
        for name, cid, missing in seed:
            # One call per room, not per person: Slack takes a comma-separated
            # list, and a single invite means one join notification in the room
            # instead of three.
            want = {uid for _, uid, _ in missing}
            r = call_write(token, "conversations.invite", channel=cid,
                           users=",".join(sorted(want)))
            # A multi-user invite reports per-user outcomes in `errors`, and the
            # top-level error alone says nothing about the rest of the batch.
            # `already_in_channel` used to be swallowed whole, so a room where
            # one account raced a manual invite and another genuinely failed
            # printed NOTHING and read as fine.
            if r.get("errors"):
                failed.append("ops seed %s: partial — %s"
                              % (name, r["errors"]))
                continue
            if r.get("ok"):
                done += 1
                print("seeded #%s with %s"
                      % (name, ", ".join(label for _, _, label in missing)))
            elif r.get("error") == "already_in_channel":
                # Benign only if it is TRUE of everyone we meant to add, and the
                # plan already filtered to accounts the membership read said were
                # missing — so this means the two disagree. Verify rather than
                # assume.
                try:
                    still = want - set(slackmod.members(api, cid))
                except slackmod.SlackError as exc:
                    failed.append("ops seed %s: already_in_channel, and the "
                                  "re-check failed (%s)" % (name, exc))
                    continue
                if still:
                    failed.append("ops seed %s: already_in_channel but %d account(s) "
                                  "are still not members" % (name, len(still)))
                else:
                    print("seeded #%s — already present (raced a manual invite)" % name)
            else:
                failed.append("ops seed %s: %s" % (name, r.get("error")))

    # Folder links, re-planned for the same reason. The skip list is CAPTURED:
    # discarding it meant a room created seconds earlier whose folder cell was a
    # placeholder, or whose pins could not be read, got no link and appeared in
    # no list anywhere — not the report (it did not exist yet), not `failed`,
    # not stdout. The run said "created #x" and exited 0 on a half-built room.
    folders, folder_col = read_chapter_folders()
    if not folder_col:
        failed.append("folder link: no 'Chapter Folder' column on %s — the "
                      "phase did nothing" % ao.CHAPTERS_TAB)
    pins, pins_skipped = plan_folder_pins(api, tables, folders, channel_ids,
                                          can_read_pins=can_read_pins)
    for city, why in pins_skipped:
        failed.append("folder link %s: %s" % (city, why))
    for city, channel, cid, fid, existing_ts in pins:
        ts = existing_ts
        if ts is None:
            # FOLDER_URL % fid, never the sheet cell — see FOLDER_PIN.
            posted = call_write(token, "chat.postMessage", channel=cid,
                                text=FOLDER_PIN % (FOLDER_URL % fid),
                                unfurl_links="false")
            if not posted.get("ok"):
                failed.append("folder link %s: chat.postMessage: %s"
                              % (channel, posted.get("error")))
                continue
            ts = posted.get("ts")
            if not ts:
                failed.append("folder link %s: posted but Slack returned no ts — "
                              "cannot pin it; check the room by hand" % channel)
                continue
        if not can_pin:
            if existing_ts is None:
                done += 1
                print("posted the %s folder link in #%s (UNPINNED — no pins:write)"
                      % (city, channel))
            continue
        if existing_ts is not None:
            # This message exists and is not pinned. Either an earlier pins.add
            # failed, or a human unpinned it on purpose — and this phase cannot
            # tell those apart. Say so: the file refuses to allowlist
            # pins.remove precisely because unpinning is a human's statement,
            # and silently re-pinning every run undoes it just as effectively.
            print("  note: #%s has the link unpinned — re-pinning. If someone "
                  "unpinned it deliberately, this run just undid that." % channel)
        pinned = call_write(token, "pins.add", channel=cid, timestamp=ts)
        if pinned.get("ok") or pinned.get("error") == "already_pinned":
            done += 1
            print("pinned the %s folder link in #%s%s"
                  % (city, channel, "" if existing_ts is None else " (existing message)"))
        else:
            failed.append(
                "folder link %s: the message is in the room but NOT pinned (%s). "
                "Pin it by hand — the next run finds it in history and pins it "
                "rather than posting a second copy." % (channel, pinned.get("error")))

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
    print("\nNo COMMUNITY member was invited to anything — only the ops accounts "
          "above, into organizer rooms. `Organizer Handles` on the Chapters List "
          "says who belongs in each organizer channel; inviting them is "
          "invite_organizers.py's job, still separate and still a human's call.")
    # The return code is the ONLY signal a caller or && chain gets — a run
    # where every rename failed must not read as success.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
