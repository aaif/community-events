#!/usr/bin/env python3
"""Remove non-organizers from organizer channels. The only script that removes people.

Driven by an explicit **keep-list**, never by a heuristic. That is the whole
design: the obvious implementation — "remove anyone whose Slack email is not on
the intake" — would eject real organizers, because matching people by email is
the documented weak link in this estate. Someone who joined Slack with a personal
address and applied with a work one reads as a stranger, and there is no signal
in the data that distinguishes them from an actual stranger.

So the script produces three buckets and only ever acts on the third:

1. **KEEP — on the keep-list.** A human wrote them down. Never touched.
2. **REVIEW — name-matches an intake row under a different email.** Almost
   certainly a real organizer. Never removed; reported so they can be added to
   the keep-list (or corrected on the intake, which is the better fix).
3. **REMOVE — everyone else.** Only reached with `--write --i-have-approval`.

## Why these channels filled up with strangers

Four organizer channels were **public** — `#frankfurt_main-organizers`,
`#montreal-organizers`, `#switzerland-organizers`, `#london-organizers`. Ordinary
members joined them because they could. Of 81 people not matched to an accepted
organizer, the ones in several channels were in exactly those four.

Removing them treats the symptom. The cause is the visibility, and converting a
public channel to private is admin-only and irreversible, so it is **not**
scripted here — do it in Slack first, or the same people rejoin.

Converting to private does **not** remove existing members; that is what this
script is for, and why the two steps are separate.

## The keep-list lives on the sheet

Tab `Organizer Keeplist`, columns `Slack Handle | Why`. On the sheet rather than
in git for the same reason the channel map moved there — the people who know who
belongs can edit a spreadsheet. **Handles, not emails**: that sheet is
world-readable, and a roster of email addresses does not belong on it.

Usage:
    python3 prune_organizers.py                        # the three buckets
    python3 prune_organizers.py --city Montreal
    python3 prune_organizers.py --write --i-have-approval
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import NO_RESOURCE, fold  # noqa: E402
from sync_resources import read_grid  # noqa: E402
from provision_channels import call_write, write_token  # noqa: E402

import audit_organizers as ao  # noqa: E402
from aaif_events import slack as slackmod  # noqa: E402

KEEPLIST_TAB = "Organizer Keeplist"
NEEDED_SCOPES = ("channels:manage", "groups:write")

#: Channels that were public and therefore accumulated general members. Recorded
#: so the report can say *why* a channel has 30 strangers in it, rather than
#: leaving someone to conclude the organizers added them.
WAS_PUBLIC = ("frankfurt_main-organizers", "montreal-organizers",
              "switzerland-organizers", "london-organizers")


def read_keeplist():
    """Handles a human has said may stay. Absent tab = empty list, not an error.

    An empty keep-list is a legitimate starting state — you run the report, see
    who is there, and fill it in. Aborting would make the first run impossible.
    """
    try:
        rows = ao.gws_values(ao.CHAPTERS_ID, "'%s'!A:B" % KEEPLIST_TAB)
    except SystemExit:
        return set(), False
    if not rows:
        return set(), False
    headers = [h.strip() for h in rows[0]]
    if "Slack Handle" not in headers:
        sys.exit("ABORT: %s has no 'Slack Handle' column." % KEEPLIST_TAB)
    i = headers.index("Slack Handle")
    keep = {r[i].strip().lstrip("@").lower()
            for r in rows[1:] if len(r) > i and r[i].strip()}
    return keep, True


def classify(city_filter=None):
    """Return (rows, had_keeplist). Each row buckets one channel's members."""
    api = slackmod.Slack()
    api.require_scopes("channels:read", "groups:read",
                       "users:read", "users:read.email")

    keep, had = read_keeplist()
    _, _, chapters = read_grid(city_filter)
    chans = {c["name"]: c for c in slackmod.channels(api) if not c["is_archived"]}

    people, _ = ao.read_intake()
    emails = {p["email"].lower() for p in people if p["email"]}
    # Name index for the cross-check. Folded the same way every other engine
    # folds, so "Médéric Hurier" and "Mederic Hurier" are one person.
    by_name = {}
    for p in people:
        if p["name"]:
            by_name.setdefault(fold(p["name"]), []).append(p)

    seen, rows = {}, []
    for ch in chapters:
        name = ch["current"]["Organizer Channel"]
        chan = chans.get(name)
        if not name or name == NO_RESOURCE or not chan:
            continue
        organizers, kept, review, remove = [], [], [], []
        for uid in slackmod.members(api, chan["id"]):
            if uid not in seen:
                seen[uid] = api.ok("users.info", user=uid)["user"]
            u = seen[uid]
            profile = u.get("profile") or {}
            email = (profile.get("email") or "").lower()
            handle = (u.get("name") or "").lower()
            real = u.get("real_name") or profile.get("real_name") or ""
            entry = (real or handle, handle, uid)

            if u.get("is_bot") or u.get("deleted"):
                kept.append(entry + ("bot or deactivated",))
            elif email and email in emails:
                organizers.append(entry)
            elif handle in keep:
                kept.append(entry + ("on the keep-list",))
            elif fold(real) in by_name:
                # The bucket this script exists to protect: their name is on the
                # intake, their Slack address is not. Almost certainly a real
                # organizer who signed up twice. Never removed.
                match = by_name[fold(real)][0]
                review.append(entry + ("name matches intake row for %s"
                                       % (match["city"] or "?"),))
            else:
                remove.append(entry)
        rows.append({"city": ch["city"], "channel": name,
                     "was_public": name in WAS_PUBLIC,
                     "channel_id": chan["id"], "organizers": organizers,
                     "kept": kept, "review": review, "remove": remove})
    return rows, had


def report(rows, had_keeplist):
    total = sum(len(r["remove"]) for r in rows)
    review = sum(len(r["review"]) for r in rows)
    print("Organizer channel membership — %d channel(s)\n" % len(rows))
    for r in sorted(rows, key=lambda x: x["city"]):
        note = "  (was public)" if r["was_public"] else ""
        print("  %-16s #%-30s %d organizers, %d keep, %d review, %d to REMOVE%s"
              % (r["city"], r["channel"], len(r["organizers"]), len(r["kept"]),
                 len(r["review"]), len(r["remove"]), note))
        for real, handle, _uid, why in r["review"]:
            print("      ? %-26s @%-20s %s" % (real[:26], handle[:20], why))
        for real, handle, _uid in r["remove"]:
            print("      - %-26s @%s" % (real[:26], handle))

    if not had_keeplist:
        print("\nNo %r tab found — the keep-list is empty, so everyone unmatched "
              "is in\nthe REMOVE bucket. Create the tab (Slack Handle | Why) "
              "before writing." % KEEPLIST_TAB)
    if review:
        print("\n%d person(s) in REVIEW: their NAME is on the intake but their "
              "Slack address\nis not. They are never removed. Add them to the "
              "keep-list, or better, fix\ntheir email on the intake so they stop "
              "reading as strangers." % review)
    if any(r["was_public"] for r in rows):
        print("\nChannels marked '(was public)' filled up because anyone could "
              "join. Convert\nthem to private in Slack first (admin-only, "
              "irreversible, not scripted here)\nor the same people rejoin.")
    print("\nTotal to remove: %d" % total)
    return total


def apply(rows, token):
    """Remove one person per call — conversations.kick takes a single user."""
    done, failed = 0, []
    for r in sorted(rows, key=lambda x: x["city"]):
        for real, handle, uid in r["remove"]:
            res = call_write(token, "conversations.kick",
                             channel=r["channel_id"], user=uid)
            if res.get("ok"):
                done += 1
                print("  removed @%s from #%s" % (handle, r["channel"]))
            else:
                err = res.get("error", "unknown")
                # not_in_channel is benign and racy: they may have left already.
                if err == "not_in_channel":
                    print("  @%s already gone from #%s" % (handle, r["channel"]))
                else:
                    failed.append("%s from %s: %s" % (handle, r["channel"], err))
    return done, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; this removes real people "
                         "from channels they are currently in")
    ap.add_argument("--city", help="limit to one chapter")
    a = ap.parse_args()

    rows, had = classify(a.city)
    total = report(rows, had)

    if not a.write:
        print("\nReport only. Nobody was removed.")
        return
    if not total:
        print("\nNothing to do.")
        return
    if not had:
        sys.exit("\nREFUSING: there is no keep-list. Removing %d people with "
                 "nobody marked as\nbelonging is almost certainly not what you "
                 "meant — create the %r tab first."
                 % (total, KEEPLIST_TAB))
    if not a.i_have_approval:
        sys.exit("\nREFUSING: --write needs --i-have-approval too. This removes "
                 "%d real people from channels." % total)

    token = write_token()
    have = slackmod.Slack(token=token).scopes()
    missing = [s for s in NEEDED_SCOPES if s not in have]
    if missing:
        sys.exit("\nREFUSING: the write token lacks %s." % ", ".join(missing))

    done, failed = apply(rows, token)
    print("\nRemoved %d, %d failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)


if __name__ == "__main__":
    main()
