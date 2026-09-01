#!/usr/bin/env python3
"""Make sure every chapter has a channel mention in its country channel.

A country channel that serves more than one room (or serves a room different
from itself) carries a standing post pointing members at their own city's
channel. As chapters are added to a country — a new sheet row, a Country
Channel that used to be blank — the post falls out of date unless something
adds the new mentions.

Answers two questions, and the first is useful on its own:

1. **What would change?** For every live Country Channel, which chapter
   channels its existing directory-style post does not yet mention, or
   whether it has never had one at all. Read-only, and the report is the
   default.
2. **Add what's missing.** Behind `--write --i-have-approval`, and nothing
   else.

## Additive only — never edits or replaces an existing post

The workspace already carries at least three different hand-written phrasings
of this post (a plain "This country has N chapters" template, a "The Nordics
have N chapters" variant, and a "we've opened a dedicated city channel for
the X chapter" announcement for a room that just grew its first second city).
Picking one canonical wording and overwriting the other two would replace
text a human chose on purpose with no functional gain — the mentions inside
it are what matters, not the sentence around them.

So this script never calls `chat.update`. It only:

- **Posts a brand-new message** in a country channel that has never had any
  directory-style post at all (found: none). This uses the standard wording.
- **Posts a short add-on message** in a channel whose existing post (any of
  the phrasings above, self-authored) is missing one or more chapters that
  now exist. The original post is left exactly as it was.

A directory-style post authored by someone else (not this token) is reported
and never touched — same reasoning as `invite_organizers.py` leaving people
the intake doesn't list alone.

Detection reads the word "chapter" — a channel where a human wrote the post
in another language (Spanish, Portuguese, German — several of these rooms are
named in that language) could miss it and get a second, English post. Nothing
today does, but this is a known gap, not a guarantee. `HISTORY_SCAN_CAP`
bounds the other side of the same risk: if nothing directory-shaped turns up
in the first 2000 messages, the channel is reported for a human to check by
hand rather than assumed empty — defaulting to "create" on an unconfirmed
absence is exactly the duplicate-post failure this design exists to avoid.

## Which channels are skipped, and why

A Country Channel with no distinct, live city channel among its chapters has
nothing to direct anyone to — the country room already is the whole answer.
This covers a chapter whose Country Channel IS its own public channel
(Singapore, Luxembourg) and one with no separate city channel at all
(Wellington's Slack Channel is `none`) through the same check, not a
hardcoded list of countries: naming specific countries here would stop being
true the day one of them opens a real city room.

## Why this is gated harder than a sheet write

A channel post is a notification to everyone in it, same reasoning as
`invite_organizers.py`. `--write` alone is not enough; `--i-have-approval`
must be passed too, and the report must have been read.

Usage:
    python3 post_country_directory.py                  # what would change
    python3 post_country_directory.py --write --i-have-approval
"""

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import NO_RESOURCE  # noqa: E402
from sync_resources import read_grid  # noqa: E402
from provision_channels import call_write, write_token, WRITE_METHODS  # noqa: E402

from aaif_events import slack as slackmod  # noqa: E402

#: A directory-style post is recognised by mentioning at least one channel
#: (`<#C...>`) and using the word "chapter" — loose on purpose: the workspace
#: already has three different phrasings (see the module docstring) and a
#: tighter match would treat two of them as "never posted" and duplicate them.
_MENTION_RE = re.compile(r"<#(C[A-Z0-9]+)")


def _looks_like_directory_post(text):
    return bool(text) and "chapter" in text.lower() and _MENTION_RE.search(text)


#: Country -> the label to use in a brand-new post when several countries
#: share one channel. A channel with exactly one Country value uses that
#: value verbatim.
MULTI_COUNTRY_LABELS = {
    frozenset({"Denmark", "Finland", "Norway", "Sweden"}): "the Nordic countries",
}

#: The four things a row can need, and the only four values `action` ever
#: holds — compared with `==`/`in` at several sites below, so a typo in any
#: one of those comparisons must fail an import, not silently miscount a
#: bucket forever.
ACTION_CREATE = "create"
ACTION_ADD_ON = "add-on"
ACTION_UP_TO_DATE = "up to date"
ACTION_HUMAN_AUTHORED = "human-authored — not touched"

MARKER = ":wave: Looking for your local AAIF community?"

#: How much history a channel with NO conclusive finding yet gets scanned
#: before this run gives up rather than guesses. Matches the order of
#: magnitude of `history_activity()`'s own default (5000) in
#: lib/aaif_events/slack.py — a directory post lives near the top of a
#: channel's history by construction (it is posted once, early), so this is
#: a generous bound, not a tight one.
HISTORY_SCAN_CAP = 2000


def new_post_text(label, mentions, is_multi):
    """The brand-new-channel post. `is_multi` picks the opening sentence.

    A single-country channel never names the country (`#kenya`'s post already
    says "Kenya" the moment someone sees the channel) — matching the wording
    of every existing single-country post in the workspace. A channel shared
    by several countries (the Nordics today) has no such implicit name, so it
    opens with `label` instead ("The Nordic countries have N chapters").
    """
    opening = (("%s have %d chapter%s" % (label, len(mentions),
                                          "" if len(mentions) == 1 else "s"))
               if is_multi else
               ("This country has %d chapter%s"
                % (len(mentions), "" if len(mentions) == 1 else "s")))
    return ("%s %s — join your city's channel: %s\n"
            "Upcoming events for every chapter live at "
            "https://aaif.io/events?tab=community. No chapter near you yet? "
            "Ask here — we love helping new ones start."
            % (MARKER, opening, ", ".join(mentions)))


def addendum_text(mentions):
    return (":wave: New chapter channel%s for this country — join yours: %s"
            % ("s" if len(mentions) > 1 else "", ", ".join(mentions)))


def collect():
    """Return (rows, skipped) — what each live country channel needs.

    rows = [{channel, channel_id, mentions (ALL desired), missing (not yet
             mentioned anywhere), action, post_text}], action one of the
    ACTION_* constants above.
    `skipped` = [(channel, why)] for Country Channels never posted to at all.
    """
    token = os.environ.get("AAIF_SLACK_WRITE_TOKEN", "").strip()
    if not token:
        print("note: AAIF_SLACK_WRITE_TOKEN is not set — falling back to the "
              "Slack CLI credential, which on this estate is expired. If auth "
              "fails, export AAIF_SLACK_WRITE_TOKEN.", file=sys.stderr)
    api = slackmod.Slack(token=token or None)
    # Before any collection, same as every other script here that reads
    # history — a missing scope must fail in the first second, not partway
    # through a sweep across 30+ country channels.
    api.require_scopes("channels:read", "groups:read",
                       "channels:history", "groups:history")
    self_id = api.ok("auth.test").get("user_id")

    _, _, chapters = read_grid()
    chans = {c["name"]: c for c in slackmod.channels(api) if not c["is_archived"]}

    groups = {}
    for ch in chapters:
        name = ch["current"]["Country Channel"]
        if not name or name == NO_RESOURCE:
            continue
        groups.setdefault(name, {"cities": [], "countries": set()})
        groups[name]["cities"].append((ch["city"], ch["current"]["Slack Channel"]))
        if ch["country"]:
            groups[name]["countries"].add(ch["country"])

    rows, skipped = [], []
    for name, g in sorted(groups.items()):
        chan = chans.get(name)
        if not chan:
            skipped.append((name, "not visible on Slack"))
            continue

        # Ordered by city name, de-duplicated by channel id (San Francisco AND
        # Silicon Valley both point at #bay-area — one mention, not two).
        wanted_ids = {}
        for city, slack_col in sorted(g["cities"]):
            if not slack_col or slack_col == NO_RESOURCE or slack_col == name:
                continue                  # no distinct city room to link
            c = chans.get(slack_col)
            if c and c["id"] not in wanted_ids:
                wanted_ids[c["id"]] = city
        if not wanted_ids:
            skipped.append((name, "no distinct, live city channel to link"))
            continue

        # Scan every self-authored, directory-shaped post (not just the
        # first) and union their mentions — the same channel is not
        # re-announced just because an earlier post already named it. Once
        # that union already covers every wanted city, no older message can
        # change the outcome ("up to date"), so stop paging through history —
        # the common case on every run after the first, and the one that
        # would otherwise re-scan a busy channel's full history for nothing.
        mentioned, found_any, scanned, hit_cap = set(), False, 0, False
        for m in api.paged("conversations.history", "messages", channel=chan["id"],
                           limit=200):
            scanned += 1
            text = m.get("text") or ""
            if _looks_like_directory_post(text):
                found_any = True
                if m.get("user") == self_id:
                    mentioned |= set(_MENTION_RE.findall(text))
                    if set(wanted_ids) <= mentioned:
                        break
            if scanned >= HISTORY_SCAN_CAP:
                hit_cap = True
                break

        # wanted_ids preserves city-name order (built from sorted(g["cities"])
        # above) — iterate it directly, not sorted(), or the message would
        # list channels by raw id instead of alphabetically by city.
        missing_ids = [cid for cid in wanted_ids if cid not in mentioned]
        all_mentions = ["<#%s>" % cid for cid in wanted_ids]
        missing_mentions = ["<#%s>" % cid for cid in missing_ids]

        if not found_any and hit_cap:
            # The one case a cap can't resolve safely: nothing directory-shaped
            # turned up in the first HISTORY_SCAN_CAP messages, but there might
            # still be one further back that this run never reached. Defaulting
            # to "create" here is exactly the failure mode the additive-only
            # design exists to prevent — a duplicate greeting in a public room.
            skipped.append((name, "scanned %d messages with no directory post "
                                  "found and no more messages read — can't "
                                  "confirm one doesn't exist further back; "
                                  "check by hand" % scanned))
            continue
        if not found_any:
            is_multi = len(g["countries"]) > 1
            label = (MULTI_COUNTRY_LABELS.get(frozenset(g["countries"]))
                     or " / ".join(sorted(g["countries"]))) if is_multi else None
            action = ACTION_CREATE
            text = new_post_text(label, all_mentions, is_multi)
        elif not mentioned:
            # found_any is True but no self-authored mention landed: every
            # directory-shaped match belongs to someone else.
            action, text = ACTION_HUMAN_AUTHORED, None
        elif not missing_ids:
            action, text = ACTION_UP_TO_DATE, None
        else:
            action, text = ACTION_ADD_ON, addendum_text(missing_mentions)

        rows.append({"channel": name, "channel_id": chan["id"],
                     "mentions": all_mentions, "missing": missing_mentions,
                     "action": action, "post_text": text})
    return rows, skipped


def report(rows, skipped):
    todo = [r for r in rows if r["action"] in (ACTION_CREATE, ACTION_ADD_ON)]
    print("Country-channel directory posts — %d live channel(s)\n" % len(rows))
    for r in sorted(rows, key=lambda x: x["channel"]):
        detail = " (%d missing)" % len(r["missing"]) if r["action"] == ACTION_ADD_ON else ""
        print("  #%-20s %s%s" % (r["channel"], r["action"], detail))
        # The approval gate below only means anything if the text it's
        # gating is visible here — print exactly what --write would send.
        if r["action"] in (ACTION_CREATE, ACTION_ADD_ON):
            for line in r["post_text"].splitlines():
                print("      %s" % line)
    if skipped:
        print("\nSkipped (%d):" % len(skipped))
        for name, why in sorted(skipped):
            print("  #%-20s %s" % (name, why))
    print("\nTo create: %d, to add-on: %d, already correct: %d, "
          "human-authored (not touched): %d"
          % (sum(1 for r in rows if r["action"] == ACTION_CREATE),
             sum(1 for r in rows if r["action"] == ACTION_ADD_ON),
             sum(1 for r in rows if r["action"] == ACTION_UP_TO_DATE),
             sum(1 for r in rows if r["action"] == ACTION_HUMAN_AUTHORED)))
    return todo


def apply(todo, token):
    """Join (if needed), then post, one call per channel."""
    done, failed = 0, []
    for r in sorted(todo, key=lambda x: x["channel"]):
        j = call_write(token, "conversations.join", channel=r["channel_id"])
        if not j.get("ok") and j.get("error") not in (
                "already_in_channel", "method_not_supported_for_channel_type"):
            failed.append("%s: join failed: %s" % (r["channel"], j.get("error")))
            continue
        res = call_write(token, "chat.postMessage", channel=r["channel_id"],
                         text=r["post_text"])
        if res.get("ok"):
            done += 1
            print("  #%-20s %s" % (r["channel"], r["action"]))
        else:
            failed.append("%s: %s" % (r["channel"], res.get("error", "unknown")))
    return done, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; a channel post is a "
                         "notification to everyone in it and cannot be unsent")
    a = ap.parse_args()

    rows, skipped = collect()
    todo = report(rows, skipped)

    if not a.write:
        print("\nReport only. Nothing was posted.")
        return 0
    if not todo:
        print("\nNothing to do.")
        return 0
    if not a.i_have_approval:
        sys.exit("REFUSING: --write needs --i-have-approval too. This posts "
                 "to %d channel(s), visible to everyone in them." % len(todo))

    token = write_token()
    assert "chat.postMessage" in WRITE_METHODS and "conversations.join" in WRITE_METHODS

    done, failed = apply(todo, token)
    print("\nPosted %d, %d failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
