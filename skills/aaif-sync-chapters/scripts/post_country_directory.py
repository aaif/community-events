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

## Which channels are skipped, and why

A Country Channel that IS the chapter's own public channel (Singapore,
Luxembourg) or that has no separate city channel to point to at all
(Wellington's Slack Channel is `none`) has nothing to direct anyone to — the
country room already is the whole answer. These are never posted to.

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


#: Country Channels that ARE the one room they'd otherwise point at, or that
#: have no separate city channel to point to. Nothing to say here that the
#: channel itself doesn't already say.
SINGLE_ROOM_CHANNELS = {"singapore", "luxembourg", "new-zealand"}

#: Country -> the label to use in a brand-new post when several countries
#: share one channel. A channel with exactly one Country value uses that
#: value verbatim.
MULTI_COUNTRY_LABELS = {
    frozenset({"Denmark", "Finland", "Norway", "Sweden"}): "the Nordic countries",
}


def new_post_text(label, mentions):
    return ("%s This country has %d chapter%s — join your city's channel: %s\n"
            "Upcoming events for every chapter live at "
            "https://aaif.io/events?tab=community. No chapter near you yet? "
            "Ask here — we love helping new ones start."
            % (":wave: Looking for your local AAIF community?", len(mentions),
               "" if len(mentions) == 1 else "s", ", ".join(mentions)))


def addendum_text(mentions):
    return (":wave: New chapter channel%s for this country — join yours: %s"
            % ("s" if len(mentions) > 1 else "", ", ".join(mentions)))


def collect():
    """Return (rows, skipped) — what each live country channel needs.

    rows = [{channel, channel_id, mentions (ALL desired), missing (not yet
             mentioned anywhere), action, post_text}], action in
    ("create", "add-on", "up to date", "human-authored — not touched").
    `skipped` = [(channel, why)] for Country Channels never posted to at all.
    """
    token = os.environ.get("AAIF_SLACK_WRITE_TOKEN", "").strip()
    if not token:
        print("note: AAIF_SLACK_WRITE_TOKEN is not set — falling back to the "
              "Slack CLI credential, which on this estate is expired. If auth "
              "fails, export AAIF_SLACK_WRITE_TOKEN.", file=sys.stderr)
    api = slackmod.Slack(token=token or None)
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
        if name in SINGLE_ROOM_CHANNELS:
            skipped.append((name, "single-room variant — nothing separate to "
                                  "point members at"))
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
        mentioned, found_any = set(), False
        for m in api.paged("conversations.history", "messages", channel=chan["id"],
                           limit=200):
            text = m.get("text") or ""
            if not _looks_like_directory_post(text):
                continue
            found_any = True
            if m.get("user") == self_id:
                mentioned |= set(_MENTION_RE.findall(text))
                if set(wanted_ids) <= mentioned:
                    break

        # wanted_ids preserves city-name order (built from sorted(g["cities"])
        # above) — iterate it directly, not sorted(), or the message would
        # list channels by raw id instead of alphabetically by city.
        missing_ids = [cid for cid in wanted_ids if cid not in mentioned]
        all_mentions = ["<#%s>" % cid for cid in wanted_ids]
        missing_mentions = ["<#%s>" % cid for cid in missing_ids]

        if not found_any:
            label = MULTI_COUNTRY_LABELS.get(frozenset(g["countries"]))
            if not label:
                label = (sorted(g["countries"])[0] if len(g["countries"]) == 1
                         else " / ".join(sorted(g["countries"])))
            action, text = "create", new_post_text(label, all_mentions)
        elif not mentioned:
            # found_any is True but no self-authored mention landed: every
            # directory-shaped match belongs to someone else.
            action, text = "human-authored — not touched", None
        elif not missing_ids:
            action, text = "up to date", None
        else:
            action, text = "add-on", addendum_text(missing_mentions)

        rows.append({"channel": name, "channel_id": chan["id"],
                     "mentions": all_mentions, "missing": missing_mentions,
                     "action": action, "post_text": text})
    return rows, skipped


def report(rows, skipped):
    todo = [r for r in rows if r["action"] in ("create", "add-on")]
    print("Country-channel directory posts — %d live channel(s)\n" % len(rows))
    for r in sorted(rows, key=lambda x: x["channel"]):
        detail = " (%d missing)" % len(r["missing"]) if r["action"] == "add-on" else ""
        print("  #%-20s %s%s" % (r["channel"], r["action"], detail))
    if skipped:
        print("\nSkipped (%d):" % len(skipped))
        for name, why in sorted(skipped):
            print("  #%-20s %s" % (name, why))
    print("\nTo create: %d, to add-on: %d, already correct: %d, "
          "human-authored (not touched): %d"
          % (sum(1 for r in rows if r["action"] == "create"),
             sum(1 for r in rows if r["action"] == "add-on"),
             sum(1 for r in rows if r["action"] == "up to date"),
             sum(1 for r in rows if r["action"] == "human-authored — not touched")))
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
    assert "chat.postMessage" in WRITE_METHODS

    done, failed = apply(todo, token)
    print("\nPosted %d, %d failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
