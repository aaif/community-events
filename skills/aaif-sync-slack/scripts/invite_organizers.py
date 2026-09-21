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

## Scope: `--scope organizer` (default), `country`, `both`, or `champs`

- **`organizer`** targets each chapter's private `Organizer Channel` **and the
  one workspace-wide `#local-champs` room**. The public chapter channel is for
  anyone to join when they choose; being an organizer is not consent to be
  placed in a public room — that policy is unchanged and is the default.

  `#local-champs` joined the default scope on 2026-09-18, when the policy
  changed to "every accepted organizer belongs in it". It used to be a curated
  leadership room that nothing in this repo wrote to, which is exactly why it
  drifted: 28 of 155 accepted organizers with a Slack account were missing from
  it. A room kept in step by hand is a room that is out of step. Because it is
  one room rather than a per-chapter column, it has no Chapters List cell — see
  CHAMPS_COLUMN — and its roster is the union of every chapter's accepted
  organizers, deduplicated by email, exactly as a shared Country Channel is.
- **`champs`** targets that room alone, for when only it needs topping up.
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
    python3 invite_organizers.py                         # who is missing (organizer + champs)
    python3 invite_organizers.py --scope champs          # the champs room alone
    python3 invite_organizers.py --scope country
    python3 invite_organizers.py --scope both --city Berlin
    python3 invite_organizers.py --scope both --write --i-have-approval
    python3 invite_organizers.py --json-out sync-reports/<stamp>/invite.json  # the report as data too (a gitignored path)
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-sync-organizers", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import NO_RESOURCE, fold_city  # noqa: E402
from sync_resources import read_grid  # noqa: E402
from provision_channels import (call_write, write_token, WRITE_METHODS,  # noqa: E402
                                WRITE_TOKEN_ENV)

import audit_organizers as ao  # noqa: E402
import resolve_slack_ids as rsi  # noqa: E402
from aaif_events import findings, report_style as rs, slack as slackmod  # noqa: E402
# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks them in every printed line.
# The flag and the helpers it governs come from ONE module on purpose: a helper
# that reads a different module's flag is a helper this `--redact` does not
# actually govern, which is how an address once reached a public CI log.
from aaif_events.redact import (add_redact_flag, redact_email, redact_name, set_redaction)  # noqa: E402


#: Inviting to a PRIVATE channel needs the groups scope; the public one is here
#: because a chapter may have put its organizer room in a public channel and the
#: run should not half-fail on the first one it meets.
NEEDED_SCOPES = ("groups:write.invites", "channels:write.invites")

#: conversations.invite takes a comma-separated list, up to 1000 ids. Batching per
#: channel keeps this to one call per chapter rather than one per person — which
#: matters for the notification too: Slack renders a single batched invite as one
#: event in the channel instead of N join lines.
MAX_PER_CALL = 1000

#: `--scope champs` targets ONE workspace-wide room, not a per-chapter channel,
#: so unlike every other scope it has no Chapters List column to read. This
#: sentinel stands in for a column name in SCOPE_COLUMNS, and collect() resolves
#: it to a constant channel instead of a cell. It is deliberately not a legal
#: column name, so a sheet that ever grows a real column cannot collide with it.
#: The room's NAME comes from audit_organizers rather than being spelled again
#: here: two definitions of it would let the audit and the invite path disagree
#: about which room they mean, which is the whole class of bug that put the
#: `--redact` flag and the gws retry table into one module each.
CHAMPS_COLUMN = "*local-champs"


def fetch(city_filter=None):
    """One Slack/Sheets round trip: (api, chapters, chans, by_city, resolved, conflicts).

    `conflicts` is the number of `Slack ID` cells that disagreed with the live
    lookup (each is already printed to stderr here); it rides along so the
    JSON report can count them without re-running the overlay.

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
    # Then the reviewed `Slack ID` column, for the people no email lookup can
    # reach because they joined Slack under an address the intake never saw.
    # Without this they are reported as "no Slack account" forever and are never
    # invited to their own chapter's room — which is the whole failure the
    # column was added to end.
    filled, conflicts = rsi.overlay_known(api, resolved)
    if filled:
        print("  %d organizer(s) resolved from the %r column rather than an "
              "email lookup." % (filled, rsi.H_SLACK_ID))
    for email, live, col in conflicts:
        print("  CONFLICT: %s resolves live to %s but the %r column says %s — "
              "the live answer is used; fix the column."
              % (redact_email(email), live, rsi.H_SLACK_ID, col), file=sys.stderr)
    return api, chapters, chans, by_city, resolved, len(conflicts)


def collect(city_filter=None, column="Organizer Channel", fetched=None, cfg=None):
    """Return (rows, unresolved, no_channel) — who is missing from where.

    rows = [{city, channel, channel_id, is_private,
             missing: [(name, id)], present: [(name, id)],
             strangers: [(name, id)], staff_in_room: n, undescribed: n}].

    `strangers` are the members the intake never put in this room, in the
    same (name, id) shape as `missing` and `present`; `staff_in_room` is how
    many AAIF ops accounts were set aside before that list was made, and
    `undescribed` how many of the strangers Slack could not describe (a
    deleted account — the id stands in for the name).

    Grouped by the target *channel*, not by chapter row: an `Organizer Channel`
    is normally 1:1 with a city, but a `Country Channel` is routinely shared by
    several chapters, and inviting per-chapter would send the same person two
    separate `conversations.invite` calls for the one channel they're both
    pointed at. `city` on the returned row is the sorted, comma-joined list of
    every chapter that names this channel — one city in the common case.

    `fetched` reuses a prior fetch() call (see there) instead of hitting Slack
    and the sheet again — pass it when collecting more than one column.
    """
    api, chapters, chans, by_city, resolved, _ = fetched or fetch(city_filter)
    # Once, not at each of the three places this used to be re-spelled: three
    # comparisons against a magic string are three chances to typo the constant
    # and get a silently-wrong branch.
    is_champs = column == CHAMPS_COLUMN

    # Group chapters by the channel NAME they name in `column`, not by row:
    # several chapters can point at the same Country Channel.
    rows, unresolved, no_channel = [], [], []
    groups = {}
    for ch in chapters:
        if not by_city.get(fold_city(ch["city"])):
            continue                      # nobody accepted yet; nothing to do
        name = (ao.LOCAL_CHAMPS_CHANNEL if is_champs
                else ch["current"][column])
        if not name or name == NO_RESOURCE:
            no_channel.append((ch["city"], "no %s on the sheet" % column))
            continue
        groups.setdefault(name, []).append(ch["city"])

    for name, cities in sorted(groups.items()):
        # Every chapter points at the champs room, so the comma-joined city list
        # would be all ~90 of them on one line. The room is workspace-wide; the
        # cities are not the useful fact about it.
        label = ("all chapters" if is_champs
                 else ", ".join(sorted(cities)))
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
        # `unaccounted` means "in this room although the intake never put them
        # here", which is a finding for a chapter's own organizer channel. It is
        # NOT one for the champs room: that room legitimately holds people who
        # are not accepted organizers at all (as of 2026-09-18, 135 of its 290
        # members), and folding them into the report's "the intake does not list
        # them" tally would turn a real signal into noise the reader must learn
        # to ignore. Adds-never-removes is unchanged either way; this only
        # governs what gets reported.
        unaccounted = [] if is_champs else sorted(members - known)
        # Who they are, and which of them are AAIF ops. Ops staff sit in every
        # organizer room by the repo's own doing (provision_channels seeds
        # them), so counting them as strangers would report the automation's
        # footprint as a finding about a person, on every row. The config is
        # read only when a room actually has someone to describe.
        strangers, staff_n, undescribed = [], 0, 0
        if unaccounted:
            cfg = cfg or ao.load_config()
            strangers, staff_n, undescribed = describe_strangers(api, unaccounted, cfg)
        rows.append({"city": label, "channel": name,
                     "channel_id": chan["id"], "is_private": chan["is_private"],
                     "missing": missing, "present": present,
                     "strangers": strangers, "staff_in_room": staff_n,
                     "undescribed": undescribed})
    return rows, unresolved, no_channel


#: The only `users.info` errors that mean "this member exists but Slack will
#: not describe them" — a deleted account is still on the room's member list.
#: Anything else (`invalid_auth`, `token_revoked`, `missing_scope`,
#: `account_inactive`, a final `ratelimited`) is the RUN failing, not the
#: member, and swallowing it once reported every stranger in a room as their
#: own id while the report went on printing as if it had looked.
UNDESCRIBABLE = ("user_not_found", "users_not_found")


def describe_strangers(api, uids, cfg):
    """Name each unaccounted member, and set the ops staff aside.

    Returns ([(name, uid)] for the real strangers, in the order given — the
    same shape `missing` and `present` use — the number of ops accounts
    dropped, and the number of members Slack could not describe). Staff is
    decided the way the audit decides it — `ao._is_staff` over the `Staff
    email domain`, `Ops staff domain` and `Ops staff email` settings on the
    Slack Config tab — so the two reports can never disagree about who is
    ops. A member Slack cannot describe (UNDESCRIBABLE) keeps their id as the
    name rather than vanishing from the count; any other `ok:false` aborts,
    and a transport failure (`SlackError`) propagates — both are real
    failures of this run, never a fact about the member.
    """
    domains = [cfg.get("staff_email_domain", "")] + list(cfg.get("ops_staff_domains") or ())
    domains = [d.strip().lower().lstrip("@") for d in domains if d and d.strip()]
    ops = {e.strip().lower() for e in (cfg.get("ops_staff_emails") or ()) if e and e.strip()}
    strangers, staff, undescribed = [], 0, 0
    for uid in uids:
        payload = api.call("users.info", user=uid)
        if not payload.get("ok"):
            error = payload.get("error", "unknown")
            if error not in UNDESCRIBABLE:
                sys.exit("ABORT: users.info %s failed: %s — this is the run "
                         "failing (token, scope or rate limit), not a fact "
                         "about the member; fix it and re-run." % (uid, error))
            strangers.append((uid, uid))     # deleted account: still a member
            undescribed += 1
            continue
        rec = slackmod._user_record(payload["user"])
        if ao._is_staff(rec.get("email", ""), domains, ops):
            staff += 1
            continue
        strangers.append((rec.get("real_name") or rec.get("name") or uid, uid))
    return strangers, staff, undescribed


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

    extra = sum(len(r["strangers"]) for r in rows)
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
#: Each target named ONCE. `both` used to re-spell the other scopes' pairs
#: verbatim, so adding the champs target meant editing two entries and the next
#: target would have the same trap — which is why there is a test asserting
#: `both` still covers all three.
_ORG = ("Organizer Channel", "Organizer channel")
_COUNTRY = ("Country Channel", "Country channel")
_CHAMPS = (CHAMPS_COLUMN, "Local champs")

SCOPE_COLUMNS = {
    # The champs room rides along with the DEFAULT scope (decided 2026-09-18):
    # every accepted organizer belongs in it, so keeping it opt-in would mean it
    # drifted out of date between the runs someone remembered to pass a flag to.
    # It costs no extra fetch — run_scope() collects every column off one
    # fetch(), measured: one additional conversations.members page — and it can
    # only ever add ACCEPTED organizers, because ao.read_intake() filters to
    # ACCEPTED before this module sees anyone.
    "organizer": [_ORG, _CHAMPS],
    "country": [_COUNTRY],
    "both": [_ORG, _COUNTRY, _CHAMPS],
    "champs": [_CHAMPS],
}


def run_scope(scope, city_filter=None, mode="report"):
    """Collect and report every column `scope` targets, off ONE fetch.

    Returns (all_rows, total, report) — `all_rows` concatenates every column's
    rows (for `apply()`), `total` sums every column's missing-invite count (the
    write gate below asks "how many people", not "how many columns"), and
    `report` is the same text report as data (see build_findings), for
    `--json-out`. Factored out of main() so a --scope both regression (e.g.
    `total =` silently replacing `total +=`) fails a unit test, not a live run.
    """
    fetched = fetch(city_filter)
    all_rows, total, passes = [], 0, []
    for column, label in SCOPE_COLUMNS[scope]:
        rows, unresolved, no_channel = collect(city_filter, column=column, fetched=fetched)
        total += report(rows, unresolved, no_channel, label=label)
        all_rows += rows
        passes.append((label, rows, unresolved, no_channel))
        print()
    return all_rows, total, build_findings(passes, fetched[-1], mode)


def build_findings(passes, conflicts, mode="report"):
    """The text report as data: a `findings.Report` for step `invite`.

    `passes` = [(label, rows, unresolved, no_channel)] — one per column
    `run_scope()` reported, in report order — and `conflicts` is the count
    fetch() returned. Pure: every number here is one report() already
    printed, summed across passes the way the write gate sums them, so the
    page never shows a figure the log cannot back.

    The findings contract: `subject` is never a person — it is the channel
    (`#name`), the pass label, the chapter city, or the `Slack ID` column
    name. `detail` MAY name a person where the text report already prints
    them, through the same `--redact` (`redact_name`); an address never
    lands here (the report prints none), and nothing from a sheet or form
    cell does.
    """
    rep = findings.Report("invite", mode)
    to_add = sum(len(r["missing"]) for _, rows, _, _ in passes for r in rows)
    present = sum(len(r["present"]) for _, rows, _, _ in passes for r in rows)
    no_account = sum(len(u) for _, _, u, _ in passes)
    channels = sum(len(rows) for _, rows, _, _ in passes)
    rep.summary = ("%d to invite across %d live channel(s); %d already in; "
                   "%d accepted organizer(s) with no Slack account"
                   % (to_add, channels, present, no_account))
    rep.measure("to invite", to_add, "warn" if to_add else "ok")
    rep.measure("already in", present, "ok")
    rep.measure("no Slack account", no_account, "warn" if no_account else None)
    rep.measure("Slack ID column conflicts", conflicts, "bad" if conflicts else None)
    undescribed = sum(r["undescribed"] for _, rows, _, _ in passes for r in rows)
    if undescribed:
        rep.measure("members Slack could not describe", undescribed, "warn")

    gate = "apply with --write --i-have-approval"
    for label, rows, unresolved, no_channel in passes:
        for r in sorted(rows, key=lambda x: x["city"]):
            for name, _uid in r["missing"]:
                rep.find("invite", "#" + r["channel"], redact_name(name),
                         "warn", gate)
            if r["strangers"]:
                names = findings.named(redact_name(n) for n, _u in r["strangers"])
                rep.find("not on the intake", "#" + r["channel"],
                         "%d member(s) the intake does not list for this room: %s"
                         % (len(r["strangers"]), names),
                         "info", "audit finding; see aaif-audit-slack")
        if unresolved:
            # One row per pass with the count AND the names: there is nothing
            # to do per person from here (they have to join Slack), but a
            # finding that says "3 cannot be invited" without saying who
            # sends the reader back to the log to find out.
            rep.find("no Slack account", label,
                     "%d accepted organizer(s) cannot be invited: %s"
                     % (len(unresolved),
                        findings.named(redact_name(n) for _c, n in unresolved)),
                     "warn", "ask them to join Slack; then resolve_slack_ids.py")
        for city, why in no_channel:
            rep.find("channel skipped", city, why, "warn",
                     "provision_channels.py, or fix the chapter row")
    if conflicts:
        rep.find("Slack ID conflict", rsi.H_SLACK_ID,
                 "%d cell(s) disagree with the live lookup; the live answer "
                 "was used" % conflicts, "warn", "fix the column (see the log)")
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --write; an invitation is a "
                         "notification to a real person and cannot be unsent")
    ap.add_argument("--city", help="limit to one chapter")
    ap.add_argument("--scope", choices=sorted(SCOPE_COLUMNS), default="organizer",
                    help="which rooms to target. 'organizer' (default) is "
                         "each chapter's private Organizer Channel AND the "
                         "workspace-wide #local-champs; 'country' is the public "
                         "Country Channel; 'both' is all three; 'champs' is "
                         "#local-champs alone.")
    add_redact_flag(ap)
    # The "--json-out" flag comes from lib: same shape for every engine, and
    # the sync runner reads the file this writes.
    findings.add_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)
    # The findings file names people. Same guard every `--out` gets, before
    # any work: a committable path is refused, not written.
    if a.json_out:
        rs.assert_git_ignored(a.json_out)

    all_rows, total, rep = run_scope(a.scope, a.city,
                                     mode="write" if a.write else "report")

    if not a.write:
        print("Report only. Nobody was invited to anything.")
        rep.write(a.json_out)
        return 0
    if not total:
        print("Nothing to do.")
        rep.write(a.json_out)
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
    # `written` means Slack acknowledged at least one invite; the failures go
    # on the page too, as rows, so a half-applied run reads as one.
    rep.written = done > 0
    rep.measure("invited", done, "ok" if done else None)
    rep.measure("invites failed", len(failed), "bad" if failed else None)
    for f in failed:
        channel, _, why = f.partition(": ")
        rep.find("invite failed", "#" + channel, why, "bad", "retry, or invite by hand")
    rep.write(a.json_out)
    # The return code is the ONLY signal a caller or && chain gets — a run
    # where every invite failed must not read as success.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
