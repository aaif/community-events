#!/usr/bin/env python3
"""Put each chapter's accepted organizers into a Slack channel they belong in.

Answers two questions, and the first is useful on its own:

1. **Who is missing?** For every chapter, who the intake says is an organizer,
   who is actually in the target channel, and the difference. Read-only, and
   the report is the default.
2. **Add them.** Behind `--write --i-have-approval`, and nothing else.

## Identity comes from the intake, not from the sheet's handles

`Organizer Handles` on the Chapters List is the human-readable mirror; the
authoritative chain is **intake row -> email -> `users.lookupByEmail` -> user id**,
the same chain that produced the column. A Slack handle is a display name a person
can change at any time, so resolving `@someone` back to an account would break
silently the day they rename themselves — and "silently" here means an organizer
quietly stops being invited to their own chapter's room.

## Scope: `--scope organizer` (default), `--scope country`, or `--scope both`

- **`organizer`** targets each chapter's private `Organizer Channel` only. The
  public chapter channel is for anyone to join when they choose; being an
  organizer is not consent to be placed in a public room — that policy is
  unchanged and is the default.
- **`country`** targets `Country Channel` instead — also a *public* room, and
  several chapters usually share one (`#españa` serves Madrid, Barcelona and
  Bilbao). This is the same class of override Rahul made 2026-08-25 for public
  city channels (being an organizer of your own chapter's public room is part
  of the role, the room is public anyway, leaving is one click) — extended
  here to the country room, and built as a standing `--scope` rather than a
  throwaway script so it doesn't need rebuilding each time. Roster for a
  shared country channel is the union of every contributing chapter's accepted
  organizers, deduplicated by email.
- **`both`** runs both passes in one report/apply.
- **Adds, never removes**, in every scope. Someone in the channel the intake
  has never heard of is *reported* and left alone — that is what an audit
  needs to see, and `aaif-audit-slack` reports the same set from the other
  direction.
- **Never touches a channel the sheet does not name**, and skips any channel
  that does not exist yet with a reason (run `provision_channels.py` first).

## Why this is gated harder than a sheet write

An invitation is a notification to a real person, and a hundred of them arriving
at once reads as a phishing wave — the same reasoning that keeps `sync_access.py`
from mailing share notices by default. `--write` alone is not enough;
`--i-have-approval` must be passed too, and the report must have been read.

Usage:
    python3 invite_organizers.py                         # who is missing (organizer channels)
    python3 invite_organizers.py --scope country
    python3 invite_organizers.py --scope both --city Berlin
    python3 invite_organizers.py --scope both --write --i-have-approval
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import NO_RESOURCE, fold_city  # noqa: E402
from sync_resources import read_grid  # noqa: E402
from provision_channels import (call_write, write_token, WRITE_METHODS,  # noqa: E402
                                WRITE_TOKEN_ENV)

import audit_organizers as ao  # noqa: E402
from aaif_events import slack as slackmod  # noqa: E402

# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks names as a first initial
# in every printed line. Each standalone script carries its own copy of this
# flag and these helpers.
REDACT = False
CI_REDACT_DEFAULT = os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")


def redact_name(n):
    if not REDACT or not n or not n.strip():
        return n
    return n.strip()[0].upper() + "."


def add_redact_flag(ap):
    ap.add_argument("--redact", action=argparse.BooleanOptionalAction,
                    default=CI_REDACT_DEFAULT,
                    help="mask names (first initial) on stdout; default on when CI is set")


def set_redaction(on):
    """Apply the parsed flag; one stderr line says so when masking is on."""
    global REDACT
    REDACT = bool(on)
    if REDACT:
        print("redaction ON (CI set; pass --no-redact to disable)"
              if CI_REDACT_DEFAULT else "redaction ON (--redact)", file=sys.stderr)


#: Inviting to a PRIVATE channel needs the groups scope; the public one is here
#: because a chapter may have put its organizer room in a public channel and the
#: run should not half-fail on the first one it meets.
NEEDED_SCOPES = ("groups:write.invites", "channels:write.invites")

#: conversations.invite takes a comma-separated list, up to 1000 ids. Batching per
#: channel keeps this to one call per chapter rather than one per person — which
#: matters for the notification too: Slack renders a single batched invite as one
#: event in the channel instead of N join lines.
MAX_PER_CALL = 1000


def fetch(city_filter=None):
    """One Slack/Sheets round trip: (api, chapters, chans, by_city, resolved).

    Split out of collect() so a multi-column run (`--scope both`) fetches this
    once, not once per column — the grid read, live-channel list, full intake
    read and email-resolution batch are identical between an Organizer Channel
    pass and a Country Channel pass; only the per-column grouping differs.
    """
    # Same read-token fallback as provision_channels.main(): this estate's CLI
    # credential expired for good in 2026-08, and the env write token carries
    # the read scopes the audits already run on. Name the real fix when the
    # env var is absent, or the eventual auth error blames the wrong credential.
    token = os.environ.get(WRITE_TOKEN_ENV, "").strip()
    if not token:
        print("note: %s is not set — falling back to the Slack CLI credential, "
              "which on this estate is expired. If auth fails, export %s."
              % (WRITE_TOKEN_ENV, WRITE_TOKEN_ENV), file=sys.stderr)
    api = slackmod.Slack(token=token or None)
    api.require_scopes("channels:read", "groups:read",
                       "users:read", "users:read.email")

    _, _, chapters = read_grid(city_filter)
    chans = {c["name"]: c for c in slackmod.channels(api) if not c["is_archived"]}

    people, _, _ = ao.read_intake()
    by_city = {}
    for p in people:
        if p["city"]:
            by_city.setdefault(fold_city(p["city"]), []).append(p)

    resolved = slackmod.lookup_emails(
        api, {p["email"] for p in people if p["email"]})
    return api, chapters, chans, by_city, resolved


def collect(city_filter=None, column="Organizer Channel", fetched=None):
    """Return (rows, unresolved, no_channel) — who is missing from where.

    rows = [{city, channel, channel_id, missing: [(name, id)], present: [...],
             unaccounted: [ids]}].

    Grouped by the target *channel*, not by chapter row: an `Organizer Channel`
    is normally 1:1 with a city, but a `Country Channel` is routinely shared by
    several chapters, and inviting per-chapter would send the same person two
    separate `conversations.invite` calls for the one channel they're both
    pointed at. `city` on the returned row is the sorted, comma-joined list of
    every chapter that names this channel — one city in the common case.

    `fetched` reuses a prior fetch() call (see there) instead of hitting Slack
    and the sheet again — pass it when collecting more than one column.
    """
    api, chapters, chans, by_city, resolved = fetched or fetch(city_filter)

    # Group chapters by the channel NAME they name in `column`, not by row:
    # several chapters can point at the same Country Channel.
    rows, unresolved, no_channel = [], [], []
    groups = {}
    for ch in chapters:
        if not by_city.get(fold_city(ch["city"])):
            continue                      # nobody accepted yet; nothing to do
        name = ch["current"][column]
        if not name or name == NO_RESOURCE:
            no_channel.append((ch["city"], "no %s on the sheet" % column))
            continue
        groups.setdefault(name, []).append(ch["city"])

    for name, cities in sorted(groups.items()):
        label = ", ".join(sorted(cities))
        chan = chans.get(name)
        if not chan:
            # Two indistinguishable causes: the channel genuinely doesn't exist,
            # or it is private and this token's user is not a member —
            # conversations.list hides those (see lib/aaif_events/slack.py).
            no_channel.append(
                (label, "#%s not visible — not created yet (run "
                 "provision_channels.py), or private and this token's user "
                 "is not in it" % name))
            continue

        roster, seen_emails = [], set()
        for city in cities:
            for p in by_city.get(fold_city(city), []):
                if p["email"] and p["email"] in seen_emails:
                    continue               # same person via >1 contributing chapter
                if p["email"]:
                    seen_emails.add(p["email"])
                roster.append(p)

        members = set(slackmod.members(api, chan["id"]))
        missing, present, seen_uids = [], [], set()
        for p in roster:
            hit = resolved.get(p["email"]) or {}
            uid = hit.get("id")
            if not uid:
                # Not a failure of this script: they have no account under the
                # address the intake holds. Reported, never silently dropped.
                unresolved.append((label, p["name"]))
            elif uid in seen_uids:
                continue          # same account via a second email on file
            elif uid in members:
                seen_uids.add(uid)
                present.append((p["name"], uid))
            else:
                seen_uids.add(uid)
                missing.append((p["name"], uid))

        known = {uid for _, uid in missing + present}
        rows.append({"city": label, "channel": name,
                     "channel_id": chan["id"], "is_private": chan["is_private"],
                     "missing": missing, "present": present,
                     "unaccounted": sorted(members - known)})
    return rows, unresolved, no_channel


def report(rows, unresolved, no_channel, label="Organizer channel"):
    total = sum(len(r["missing"]) for r in rows)
    print("%s membership — %d live channel(s)\n" % (label, len(rows)))
    for r in sorted(rows, key=lambda x: x["city"]):
        if not r["missing"]:
            print("  %-18s #%-28s all %d in" % (r["city"], r["channel"],
                                                len(r["present"])))
            continue
        print("  %-18s #%-28s %d to add, %d already in"
              % (r["city"], r["channel"], len(r["missing"]), len(r["present"])))
        for name, uid in r["missing"]:
            print("      + %s (%s)" % (redact_name(name), uid))

    if unresolved:
        print("\nAccepted organizers with no Slack account (%d) — cannot be "
              "invited:" % len(unresolved))
        for city, name in unresolved:
            print("  %-18s %s" % (city, redact_name(name)))

    if no_channel:
        print("\nChapters skipped (%d):" % len(no_channel))
        for city, why in no_channel:
            print("  %-18s %s" % (city, why))

    extra = sum(len(r["unaccounted"]) for r in rows)
    if extra:
        print("\n%d person(s) in a channel the intake does not list them for. "
              "NOT removed —\nthat is an audit finding, not a cleanup task; see "
              "aaif-audit-slack." % extra)
    print("\nTotal to add: %d" % total)
    return total


def apply(rows, token):
    """Invite the missing organizers, one batched call per channel.

    Slack fails the WHOLE batch when any single invitee can't be invited: a
    top-level `already_in_channel` means "at least one raced in", not
    "everyone did", and the other N-1 were NOT invited. So a failed batch
    falls back to one call per person, where that error really is per-person
    and benign. Batches are chunked, never truncated — capping at
    MAX_PER_CALL silently would report the overflow as invited.
    """
    done, failed = 0, []
    for r in sorted(rows, key=lambda x: x["city"]):
        if not r["missing"]:
            continue
        ids = [uid for _, uid in r["missing"]]
        added = 0
        for at in range(0, len(ids), MAX_PER_CALL):
            chunk = ids[at:at + MAX_PER_CALL]
            res = call_write(token, "conversations.invite",
                             channel=r["channel_id"], users=",".join(chunk))
            if res.get("ok"):
                added += len(chunk)
                continue
            for uid in chunk:
                one = call_write(token, "conversations.invite",
                                 channel=r["channel_id"], users=uid)
                if one.get("ok") or one.get("error") == "already_in_channel":
                    added += 1        # in the room either way — the goal state
                else:
                    failed.append("%s: %s (%s)" % (
                        r["channel"], one.get("error", "unknown"), uid))
        done += added
        print("  #%-28s added %d of %d" % (r["channel"], added, len(ids)))
    return done, failed


#: What each --scope value targets, and the label used in the report header.
SCOPE_COLUMNS = {
    "organizer": [("Organizer Channel", "Organizer channel")],
    "country": [("Country Channel", "Country channel")],
    "both": [("Organizer Channel", "Organizer channel"),
             ("Country Channel", "Country channel")],
}


def run_scope(scope, city_filter=None):
    """Collect and report every column `scope` targets, off ONE fetch.

    Returns (all_rows, total) — `all_rows` concatenates every column's rows
    (for `apply()`), `total` sums every column's missing-invite count (the
    write gate below asks "how many people", not "how many columns").
    Factored out of main() so a --scope both regression (e.g. `total =`
    silently replacing `total +=`) fails a unit test, not a live run.
    """
    fetched = fetch(city_filter)
    all_rows, total = [], 0
    for column, label in SCOPE_COLUMNS[scope]:
        rows, unresolved, no_channel = collect(city_filter, column=column, fetched=fetched)
        total += report(rows, unresolved, no_channel, label=label)
        all_rows += rows
        print()
    return all_rows, total


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; an invitation is a "
                         "notification to a real person and cannot be unsent")
    ap.add_argument("--city", help="limit to one chapter")
    ap.add_argument("--scope", choices=sorted(SCOPE_COLUMNS), default="organizer",
                    help="which channel to target: the private Organizer "
                         "Channel (default), the public Country Channel, or "
                         "both")
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)

    all_rows, total = run_scope(a.scope, a.city)

    if not a.write:
        print("Report only. Nobody was invited to anything.")
        return 0
    if not total:
        print("Nothing to do.")
        return 0
    if not a.i_have_approval:
        sys.exit("REFUSING: --write needs --i-have-approval too. This sends a "
                 "Slack notification to %d real people." % total)

    token = write_token()
    have = slackmod.Slack(token=token).scopes()
    missing = [s for s in NEEDED_SCOPES if s not in have]
    if missing:
        sys.exit("REFUSING: the write token lacks %s." % ", ".join(missing))

    assert "conversations.invite" in WRITE_METHODS, (
        "conversations.invite must be in the write allowlist")

    done, failed = apply(all_rows, token)
    print("\nInvited %d, %d invite(s) failed." % (done, len(failed)))
    for f in failed:
        print("  %s" % f)
    # The return code is the ONLY signal a caller or && chain gets — a run
    # where every invite failed must not read as success.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
