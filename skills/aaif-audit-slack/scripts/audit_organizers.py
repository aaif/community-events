#!/usr/bin/env python3
"""Audit the Slack workspace from the organizer's side.

For every chapter on the Chapters List: is there a public city channel, is there
a private organizers channel, and are the organizers we accepted actually in
them? Plus the full roster of every organizers channel, split by whether we ever
accepted that person.

Read-only on both sides — the sheets are only read, and the Slack client refuses
any non-read method.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events import report_style as rs  # noqa: E402
from aaif_events.slack import Slack, channels, lookup_emails, members  # noqa: E402

read_cache, write_cache, cache_age = jsoncache.read, jsoncache.write, jsoncache.age

#: The whole channel map now lives on the Chapters List — there is no JSON file.
#: The per-chapter part is three columns (see CHANNEL_COLUMNS); the workspace-wide
#: matching vocabularies are this tab.
SLACK_CONFIG_TAB = "Slack Config"

#: Row label on that tab -> the config key it fills. Labels are prose because
#: organizers read this tab; the keys stay snake_case because the matcher does.
CONFIG_LABELS = {
    "Public channel prefix": "public_prefixes",
    "Organizer channel suffix": "organizer_suffixes",
    "Staff email domain": "staff_email_domain",
}

#: Settings that take several values, in sheet row order. Order is load-bearing:
#: the prefixes are tried in the order listed, so the bare slug beats
#: "meetup-<slug>" deterministically rather than by whatever order the Slack API
#: happened to return channels in.
LIST_SETTINGS = ("public_prefixes", "organizer_suffixes")

#: A spreadsheet cannot hold an empty string distinguishably from an empty cell,
#: and the FIRST public prefix is exactly that — "" meaning "try the plain city
#: slug, no prefix at all". Same problem NO_RESOURCE solves for the channel
#: columns, same shape of answer: a visible sentinel.
EMPTY_VALUE = "(none)"

REQUIRED_CFG = ("public_prefixes", "organizer_suffixes", "staff_email_domain")


CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"
CHAPTERS_TAB = "Chapters & Teams"
INTAKE_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
INTAKE_TAB = "Organizers"
#: Exact-string statuses that count as accepted. Exact dropdown strings —
#: "Existing" alone would miss every MLOps row, so keep the full value.
ACCEPTED = ("Accepted", "Existing (from MLOps)")

e = html.escape


# --------------------------------------------------------------------------
# Sheets, via the gws CLI (the repo's only sanctioned Drive path)
# --------------------------------------------------------------------------

#: Same retry/JSON pattern as aaif-sync-chapters — the Sheets API returns
#: intermittent 500s, and a one-shot read turns a blip into a failed audit.
_TRANSIENT = ("timed out", "internalError", "Internal error", "HTTP request failed",
              "Connection reset", "Connection refused", "Connection aborted",
              "temporarily", "rateLimit", "userRateLimit", "backendError")
# Bare "500"/"502" as substrings match any range or quota id containing those
# digits ("A500:K500 exceeds grid limits"), so a permanent error would burn the
# full backoff. Match them only as standalone HTTP statuses.
_TRANSIENT_STATUS = re.compile(r"(?<![0-9])(?:429|500|502|503|504)(?![0-9])")


def _transient(msg):
    return any(k in msg for k in _TRANSIENT) or bool(_TRANSIENT_STATUS.search(msg))


def gws_values(sheet_id, rng, retries=5):
    """Read one A1 range through `gws`, returning a list of rows."""
    cmd = ["gws", "sheets", "spreadsheets", "values", "batchGet",
           "--params", json.dumps({"spreadsheetId": sheet_id, "ranges": [rng]})]
    for attempt in range(retries):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            break
        msg = (proc.stderr or "") + (proc.stdout or "")
        if attempt < retries - 1 and _transient(msg):
            # Announce it: a silent backoff looks like a hang, and a run that
            # succeeds on attempt 4 should still leave a trace the API was sick.
            print("  gws read failed (attempt %d/%d), retrying in %ds: %s"
                  % (attempt + 1, retries, 2 * (attempt + 1), msg.strip()[:120]),
                  file=sys.stderr)
            time.sleep(2 * (attempt + 1))
            continue
        raise SystemExit("gws failed reading %s!%s:\n%s" % (sheet_id, rng, msg.strip()[:400]))
    # Split on "\n" only — NOT splitlines(), which also splits on U+2028 and
    # friends INSIDE cell values, corrupting the JSON when rejoined.
    text = "\n".join(ln for ln in proc.stdout.split("\n")
                     if "keyring backend" not in ln).strip()
    if not text:
        raise SystemExit("gws produced no JSON reading %s!%s" % (sheet_id, rng))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Every other gws failure names the sheet and range; a stray non-JSON
        # line on stdout should not be the one that dies as a bare traceback.
        raise SystemExit("gws returned non-JSON reading %s!%s (%s). First 400 chars:\n%s"
                         % (sheet_id, rng, exc, text[:400]))
    ranges = parsed.get("valueRanges") or [{}]
    return ranges[0].get("values", [])


def cell(row, i):
    return (row[i] if i < len(row) else "").strip()


def header_index(headers, tab, *names):
    idx = {}
    for name in names:
        if headers.count(name) > 1:
            raise SystemExit("ABORT: %r appears twice in %s — reads would be ambiguous."
                             % (name, tab))
        if name not in headers:
            raise SystemExit("ABORT: %s has no %r column." % (tab, name))
        idx[name] = headers.index(name)
    return idx


def load_config(sheet_id=None):
    """Read the matching vocabularies off the Chapters List `Slack Config` tab.

    Checked here, before anything slow happens, so a renamed label costs a second
    rather than surfacing after the sheet reads, the channel pull and ~100 email
    lookups — `staff_email_domain` is not touched until the very last step.
    """
    rows = gws_values(sheet_id or CHAPTERS_ID, "'%s'!A:C" % SLACK_CONFIG_TAB)
    if not rows:
        raise SystemExit(
            "ABORT: no %r tab on the Chapters List (or it is empty). The channel "
            "matching config lives there now; run\n"
            "  python3 skills/aaif-sync-chapters/scripts/migrate_resource_columns.py "
            "--write\nto create it." % SLACK_CONFIG_TAB)

    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, SLACK_CONFIG_TAB, "Setting", "Value")

    cfg = {k: [] for k in LIST_SETTINGS}
    unknown = set()
    for row in rows[1:]:
        label = cell(row, idx["Setting"])
        if not label:
            continue
        key = CONFIG_LABELS.get(label)
        if key is None:
            unknown.add(label)
            continue
        value = cell(row, idx["Value"])
        # Only the sentinel becomes "". A genuinely blank cell is a half-typed
        # row, and silently reading it as the bare-slug prefix would quietly
        # widen the matcher — the one direction this repo never widens by
        # accident. Rejected HERE, at append time, in every position: the old
        # guard checked cfg[k][1:], so a blank FIRST row (a blanked organizer
        # suffix, say) read as the empty suffix and made the public channel
        # itself match as the private one.
        if key in LIST_SETTINGS:
            if value == "":
                raise SystemExit(
                    "ABORT: %s row labelled %r has a blank Value. A blank is a "
                    "half-typed row; write %r if you mean the bare slug."
                    % (SLACK_CONFIG_TAB, label, EMPTY_VALUE))
            cfg[key].append("" if value == EMPTY_VALUE else value)
        else:
            cfg[key] = value

    if unknown:
        raise SystemExit(
            "ABORT: %s has row(s) labelled %s, which name no setting. Known "
            "labels: %s. A typo'd label would silently drop a prefix and change "
            "which channels match."
            % (SLACK_CONFIG_TAB, ", ".join(map(repr, sorted(unknown))),
               ", ".join(sorted(CONFIG_LABELS))))

    missing = [k for k in REQUIRED_CFG if not cfg.get(k)]
    if missing:
        raise SystemExit(
            "ABORT: %s defines no value for: %s."
            % (SLACK_CONFIG_TAB,
               ", ".join(sorted(l for l, k in CONFIG_LABELS.items() if k in missing))))
    return cfg


#: Sheet column -> the config table it supplies. These three columns ARE the map
#: that channel_map.json used to hold before the map moved onto the sheet.
CHANNEL_COLUMNS = {"Slack Channel": "public",
                   "Organizer Channel": "organizers",
                   "Country Channel": "regional"}

#: The sheet's stand-in for a JSON `null`: a human checked and there is no such
#: channel. It must survive into the table as None, because that is what stops
#: _resolve_alias() falling through to a guess. Kept in sync with
#: sync_chapters.NO_RESOURCE by test_audit_organizers.
NO_RESOURCE = "none"


def read_chapters():
    """Chapters, plus the three channel tables their rows carry.

    Returns (chapters, tables). `tables` is keyed exactly like the old
    channel_map.json sections, so match_channels() consumes it unchanged — the
    migration moved where the map is stored, not what it means.

    A BLANK cell leaves the city out of the table entirely, which is what makes
    the matcher fall through to its prefix/suffix scan. The literal `none` puts
    the city in with a None value, which stops the scan. Collapsing the two would
    either re-guess a settled question forever or freeze every unfilled row.
    """
    rows = gws_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        raise SystemExit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, CHAPTERS_TAB, "City", *CHANNEL_COLUMNS)

    out, tables = [], {t: {} for t in CHANNEL_COLUMNS.values()}
    for row in rows[1:]:
        city = cell(row, idx["City"])
        if not city:
            continue
        out.append({"city": city})
        for column, table in CHANNEL_COLUMNS.items():
            value = cell(row, idx[column]).strip()
            if not value:
                continue
            # Channel names are stored bare; tolerate a leading '#' because a
            # human typing into a spreadsheet will write one about half the time,
            # and a stored "#berlin" would never match the channel "berlin".
            tables[table][city] = (None if value == NO_RESOURCE
                                   else value.lstrip("#").strip())
    if not out:
        raise SystemExit(
            "ABORT: %d rows in %s but none has a City value. The column was "
            "probably renamed or reordered." % (len(rows) - 1, CHAPTERS_TAB))
    return out, tables


def read_intake():
    rows = gws_values(INTAKE_ID, "%s!A:U" % INTAKE_TAB)
    if not rows:
        raise SystemExit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, INTAKE_TAB, "Status", "Full name", "Email",
                       "City (Existing)", "City (New)", "Chapter")
    people, seen, dupes, saw = [], set(), 0, set()
    for row in rows[1:]:
        status = cell(row, idx["Status"])
        if status:
            saw.add(status)
        if status not in ACCEPTED:
            continue
        # Same precedence as sync_crm: the human's Chapter assignment wins.
        city = cell(row, idx["Chapter"]) or cell(row, idx["City (New)"])
        if not city:
            existing = cell(row, idx["City (Existing)"])
            city = "" if existing.startswith("Other") else existing
        email = cell(row, idx["Email"]).lower()
        # Dedupe on email only when there IS one. Two accepted organizers in one
        # city who both have a blank Email cell are two people, not a duplicate
        # row — collapsing them would delete someone from the audit and then
        # mislabel the loss as a data-entry duplicate.
        key = (email, city.lower())
        if email and key in seen:
            dupes += 1
            continue
        seen.add(key)
        people.append({"name": cell(row, idx["Full name"]), "email": email,
                       "status": status, "city": city})
    if not people:
        # The exact-string filter is the known fragility here (see ACCEPTED), and
        # a zero match renders as "Every one of these 0 people was reviewed and
        # accepted" rather than as an error. Refuse to publish that.
        raise SystemExit(
            "ABORT: %d rows in %s but none matches a status in %r.\n"
            "Statuses actually present: %s\n"
            "The dropdown values were probably renamed — this filter is exact by "
            "design, so a trailing space or a new label breaks it."
            % (len(rows) - 1, INTAKE_TAB, list(ACCEPTED),
               ", ".join(sorted(saw)) or "(none)"))
    return people, dupes


# --------------------------------------------------------------------------
# City -> channel matching
# --------------------------------------------------------------------------

def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fold_tight(s):
    return fold(s).replace("-", "")


def variants(city):
    """Plausible channel slugs for a city name."""
    head = fold(city.split(",")[0])
    out = {fold(city), fold(city).replace("-", ""), head, head.replace("-", "")}
    return {v for v in out if v}


def _resolve_alias(city, table, by_name):
    """Apply a curated alias. Returns (channel, how).

    A non-empty `how` means the map spoke for this city, so the caller must NOT
    fall through to guessing — `if not channel and not how:`. Both a real alias
    and an explicit `null` decide the question; `null` means "a human checked
    and there is no channel", which is an answer, not a gap to fill with a guess.

    The two are returned as one value on purpose. They were briefly a
    (channel, how, decided) triple, where `decided` was exactly `bool(how)` and
    a future branch could have set them inconsistently — reintroducing the bug
    this function exists to prevent while the docstring still denied it.

    `how` is one of: "" (the map was silent), "known-none", "alias", or
    "alias-missing:<name>". Callers must run assert_aliases_resolve() to rule
    out the last, which is always a configuration bug — or, pre-provisioning,
    mark_planned_aliases() to downgrade it to "planned:<name>". match_channels()
    adds "alias-private:<name>" for a public alias held by a private room,
    policed by the same pair of callers.
    """
    if city not in table:
        return None, ""
    alias = table[city]
    if alias is None:
        return None, "known-none"
    channel = by_name.get(alias)
    if channel and not channel["is_archived"]:
        return channel, "alias"
    return None, "alias-missing:%s" % alias


def match_channels(chapters, chans, cfg):
    """Resolve each chapter to its public and organizers channel.

    Conservative by design: a configured or exact hit is a match, anything weaker
    is reported as a candidate for a human, never as coverage.

    Each returned record always carries every key below; `None` means absent.
    `regional` is non-None only when `public` is None. `public_members` /
    `organizers_channel_members` are None when the channel is unknown OR when
    Slack did not report a size — callers must not read them as zero.
    """
    by_name = {c["name"]: c for c in chans}
    live = [c for c in chans if not c["is_archived"]]
    suffixes = tuple(cfg["organizer_suffixes"])
    out = []

    for ch in chapters:
        city = ch["city"]
        vs = variants(city)

        pub, how = _resolve_alias(city, cfg["public"], by_name)
        if pub and pub["is_private"]:
            # The auto path refuses private channels; an alias must not be a way
            # around that, or a private room is reported as the city's home.
            # Recorded rather than raised so --planned-ok can downgrade the
            # pre-convert state (the sheet-named room exists but is still held
            # private); the default run aborts in assert_aliases_resolve().
            pub, how = None, "alias-private:%s" % pub["name"]
        candidates = []
        if not pub and not how:
            # Prefixes are tried in the order the config lists them, so an exact
            # slug beats "meetup-<slug>" deterministically. Iterating channels on
            # the outside instead would hand the decision to whatever order the
            # Slack API happened to return, making coverage non-reproducible.
            for prefix in cfg["public_prefixes"]:
                for v in sorted(vs):
                    c = by_name.get(prefix + v)
                    if c and not c["is_private"] and not c["is_archived"]:
                        pub, how = c, "exact"
                        break
                if pub:
                    break
        if not pub:
            # Candidates are the human's safety net, so err towards showing too
            # many. Comparing whole variants against single tokens missed the
            # likeliest real name: for "Cape Town" it flagged #capetown-x but not
            # #cape-town-ai. Match on the city's *tokens* being a subset instead.
            tokens = {t for t in fold(city).split("-") if t}
            for c in live:
                if c["is_private"] or c["name"].endswith(suffixes):
                    continue
                chan_tokens = set(c["name"].split("-"))
                if (tokens and tokens <= chan_tokens) or (vs & chan_tokens):
                    candidates.append(c["name"])

        org, org_how = _resolve_alias(city, cfg["organizers"], by_name)
        if not org and not org_how:
            # Suffixes outer, mirroring the public prefix loop: the Slack Config
            # tab lists them in a meaningful order, so "-organizers" must beat "-leads"
            # regardless of which city variant happens to sort first.
            for suffix in suffixes:
                for v in sorted(vs):
                    c = by_name.get(v + suffix)
                    if c and not c["is_archived"]:
                        org, org_how = c, "exact"
                        break
                if org:
                    break

        # The regional map gets the same treatment as the other two: a renamed or
        # archived target must not silently downgrade the chapter from "regional
        # only" to "no channel at all" and generate advice to build a room.
        regional, regional_how = None, ""
        if not pub:
            reg, regional_how = _resolve_alias(city, cfg["regional"], by_name)
            regional = reg["name"] if reg else None

        out.append({
            "city": city, "regional": regional,
            "public_how": how, "organizers_how": org_how,
            "regional_how": regional_how,
            "public": pub["name"] if pub else None,
            "public_id": pub["id"] if pub else None,
            "public_members": pub["num_members"] if pub else None,
            "public_candidates": candidates,
            "organizers_channel": org["name"] if org else None,
            "organizers_id": org["id"] if org else None,
            "organizers_channel_members": org["num_members"] if org else None,
            "organizers_private": org["is_private"] if org else None,
        })
    return out


def assert_aliases_resolve(rows, source=CHAPTERS_TAB):
    """Abort when a curated alias no longer points at a live channel.

    A human confirmed this mapping; if it stops resolving the channel was
    renamed or archived, which is a configuration bug. Left alone it silently
    downgrades the chapter to "no channel at all" and generates a "give them a
    room" action for a city that already has one.

    `source` names where to go and fix it. It is the Chapters List now, not
    channel_map.json — sending someone to edit a JSON file that no longer holds
    the entry is worse than not naming a file at all.
    """
    broken = [(r["city"], r[k].split(":", 1)[1])
              for r in rows for k in ("public_how", "organizers_how", "regional_how")
              if r[k].startswith("alias-missing:")]
    if broken:
        raise SystemExit(
            "ABORT: %d channel(s) named on %s no longer resolve to a live "
            "channel:\n  %s\nThe channel was probably renamed or archived. Fix "
            "the row (or set it to %r) — these chapters would otherwise be "
            "reported as having no channel at all."
            % (len(broken), source,
               "\n  ".join("%s -> #%s" % (c, n) for c, n in broken), NO_RESOURCE))
    held = [(r["city"], r["public_how"].split(":", 1)[1])
            for r in rows if r["public_how"].startswith("alias-private:")]
    if held:
        raise SystemExit(
            "ABORT: %d Slack Channel cell(s) on %s point at a PRIVATE channel:\n"
            "  %s\nA city's own channel must be public. Convert the room to "
            "public, or run with --planned-ok to report these chapters truthfully "
            "as having no public channel yet."
            % (len(held), source,
               "\n  ".join("%s -> #%s" % (c, n) for c, n in held)))


def mark_planned_aliases(rows):
    """Downgrade unresolvable aliases to "planned" instead of aborting.

    Before provision_channels.py has run, the Chapters List names the channel
    each chapter *will* have (`sync_resources.py --plan` fills it ahead of
    creation), so a name that does not resolve is the unexecuted plan, not a
    rename/archive bug. Only reachable via --planned-ok; the default abort
    stays, because once provisioning has run a missing alias IS a
    configuration bug again.

    The chapter is still reported as having no channel — that is the truthful
    current state — but the report's data-quality notes list these as planned
    rather than letting them blend into "nobody ever made a room".

    A `public` alias held by a PRIVATE room is the sibling pre-convert state
    (the name exists, the admin-UI convert has not happened) and is downgraded
    to "held-private:<name>" the same way, kept distinct so the report does not
    claim the channel is yet to be created.
    """
    planned, held = [], []
    for r in rows:
        for k in ("public_how", "organizers_how", "regional_how"):
            if r[k].startswith("alias-missing:"):
                name = r[k].split(":", 1)[1]
                r[k] = "planned:" + name
                planned.append((r["city"], name))
        if r["public_how"].startswith("alias-private:"):
            name = r["public_how"].split(":", 1)[1]
            r["public_how"] = "held-private:" + name
            held.append((r["city"], name))
    return planned, held


def check_membership_floor(membership, chans, chans_cached, refetch, note=print):
    """The free floor on a paged membership pull: no channel's pulled ids may
    number fewer than conversations.list said it has members. A short pull
    would render real members as "accepted but absent" pills, so it aborts.

    `membership` is {name: [ids]}; `chans` is the channel list those names came
    from; `refetch` re-pulls the list (and re-stamps its cache) and is called
    at most once, only when `chans_cached` — a CACHED size may simply predate
    someone leaving, and aborting on that with "re-run" advice loops forever
    because a plain re-run reuses the same cache.

    Two silences this function exists to keep loud:

    * only the channels short against the CACHED size are re-checked against
      the fresh list. A channel that passed the cached floor and gained a
      member between the membership pull and the re-fetch is the normal join
      race, not a dropped page — re-checking everything turned that race into
      a spurious abort about a pull that was complete.
    * a re-checked channel MISSING from the fresh list was renamed (or
      archived/deleted) mid-run. Its floor is unverifiable and everything
      matched against the old name is stale, so it aborts naming the rename —
      the alternative, re-pulling that one membership, would quietly verify a
      roster the rest of the report still files under a name that no longer
      exists.

    Returns the channel list the final check ran against.
    """
    def undersized(chans, only=None):
        sizes = {c["name"]: c["num_members"] for c in chans}
        return [(n, len(ids), sizes[n])
                for n, ids in sorted(membership.items())
                if (only is None or n in only)
                and sizes.get(n) is not None and len(ids) < sizes[n]]

    short = undersized(chans)
    if short and chans_cached:
        note("  %d channel(s) smaller than their cached size — re-fetching the "
             "channel list to tell a stale cache from a short pull ..."
             % len(short))
        chans = refetch()
        names = {c["name"] for c in chans}
        short_names = {n for n, _got, _want in short}
        gone = sorted(short_names - names)
        if gone:
            raise SystemExit(
                "ABORT: %d channel(s) whose membership pull came back short "
                "no longer appear in a fresh channel list under that name: %s."
                "\nThey were renamed, archived or deleted mid-run, so their "
                "membership floor cannot be verified — and every chapter match "
                "in this run still uses the old name. Re-run with --refresh."
                % (len(gone), ", ".join("#" + n for n in gone)))
        short = undersized(chans, only=short_names)
    if short:
        raise SystemExit(
            "ABORT: membership came back short for %d channel(s) against a "
            "fresh channel list: %s.\nPeople would be reported as absent from "
            "rooms they are in. Re-run with --refresh; if it persists, the "
            "members() pagination is dropping pages."
            % (len(short),
               ", ".join("#%s (%d of %d)" % s for s in short)))
    return chans


def build_audit(rows, people, slack_ids, membership, directory, staff_domain):
    """Join the sheet data to the Slack data, per chapter.

    Each chapter record gains `accepted` (people, each with `slack_id`,
    `slack_account`, `in_public`, `in_organizers`) and `unaccounted` (everyone in
    the organizers channel we never accepted, each with `is_staff` and
    `unresolved`). Every key from match_channels() survives unchanged.
    """
    lookup = {}
    for r in rows:
        key = fold_tight(r["city"])
        if key in lookup:
            # header_index aborts on a duplicate column for the same reason: a
            # silent collision here would attribute every organizer of one
            # chapter to the other, and leave the loser showing zero — which
            # then feeds "chapters we cannot reach" and "give them a room".
            raise SystemExit(
                "ABORT: chapter rows %r and %r reduce to the same key %r, so "
                "organizer attribution would be silently wrong. Merge or rename "
                "the rows on the Chapters List." % (lookup[key], r["city"], key))
        lookup[key] = r["city"]

    by_city, orphans = defaultdict(list), defaultdict(list)
    for person in people:
        key = fold_tight(person["city"])
        (by_city[lookup[key]] if key in lookup else orphans[person["city"]]).append(person)

    out = []
    for r in rows:
        # Index, don't .get: a channel we matched but never pulled membership for
        # would otherwise score every organizer as absent and surface the chapter
        # under "has a room and nobody in it".
        pub_ids = set(membership[r["public"]]) if r["public"] else set()
        org_ids = set(membership[r["organizers_channel"]]) if r["organizers_channel"] else set()

        accepted = []
        for person in by_city.get(r["city"], []):
            uid = (slack_ids.get(person["email"]) or {}).get("id")
            accepted.append({**person, "slack_id": uid, "slack_account": bool(uid),
                             "in_public": bool(uid and uid in pub_ids),
                             "in_organizers": bool(uid and uid in org_ids)})

        known = {p["slack_id"] for p in accepted if p["slack_id"]}
        extras = []
        for uid in sorted(org_ids - known):
            u = directory.get(uid)
            if u is None:
                # Naming failed for this member. Render them as unidentified
                # rather than filing them under a group whose label asserts a
                # judgement we never made about them.
                extras.append({"id": uid, "name": uid, "email": "",
                               "is_staff": False, "unresolved": True})
                continue
            extras.append({"id": uid,
                           "name": u.get("real_name") or u.get("name") or uid,
                           "email": u.get("email", ""),
                           "is_staff": (u.get("email") or "").endswith("@" + staff_domain),
                           "unresolved": False})
        out.append({**r, "accepted": accepted, "unaccounted": extras})
    return out, dict(orphans)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def rooms_to_create(audit):
    """The chapters the report may honestly tell an admin to build a room for.

    Uncovered plus two accepted organizers is not enough on its own: a
    public_how of "planned:<name>" or "held-private:<name>" means the room is
    already provisioned-or-pending (often behind a squatting name, where a
    create is guaranteed to fail), and "known-none" is a human's standing
    answer that there is no channel on purpose. All three are surfaced in the
    Data-quality notes; repeating them here as create advice would send an
    admin to fight Slack over a name that is already spoken for.
    """
    return [c for c in audit
            if not c["public"] and len(c["accepted"]) >= 2
            and c["public_how"] != "known-none"
            and not c["public_how"].startswith(("planned:", "held-private:"))]


def render(audit, orphans, dupes, today):
    allp = [p for c in audit for p in c["accepted"]]
    S = {
        "chapters": len(audit),
        "own": sum(1 for c in audit if c["public"]),
        "regional": sum(1 for c in audit if not c["public"] and c["regional"]),
        "none": sum(1 for c in audit if not c["public"] and not c["regional"]),
        "org": sum(1 for c in audit if c["organizers_channel"]),
        "people": len(allp),
        "has_slack": sum(1 for p in allp if p["slack_account"]),
        "in_public": sum(1 for p in allp if p["in_public"]),
        "in_org": sum(1 for p in allp if p["in_organizers"]),
    }
    S["no_slack"] = S["people"] - S["has_slack"]

    funnel_rows = [("Accepted as organizers", S["people"]),
                   ("Have a Slack account", S["has_slack"]),
                   ("In their city's channel", S["in_public"]),
                   ("In an organizers channel", S["in_org"])]
    top = funnel_rows[0][1] or 1
    funnel = []
    for i, (lbl, v) in enumerate(funnel_rows):
        pct = 100 * v / top
        tone = "accent" if i == 0 else ("warn" if pct > 40 else "bad")
        drop = "" if i == 0 else '<span class="drop">&minus;%d</span>' % (funnel_rows[i - 1][1] - v)
        funnel.append('<div class="frow"><span class="flab">%s</span><span class="ftrack">'
                      '<span class="ffill t-%s" style="width:%s%%"></span></span>'
                      '<span class="fval">%d<span class="fpct">%.0f%%</span></span>%s</div>'
                      % (e(lbl), tone, pct, v, pct, drop))

    matrix = []
    for c in audit:
        state = "own" if c["public"] else ("regional" if c["regional"] else "none")
        n = len(c["accepted"])
        inp = sum(1 for p in c["accepted"] if p["in_public"])
        ino = sum(1 for p in c["accepted"] if p["in_organizers"])
        nos = sum(1 for p in c["accepted"] if not p["slack_account"])
        shown = c["public"] or c["regional"]
        matrix.append(
            '<tr data-state="%s" data-org="%s"><th scope="row">%s</th>'
            '<td>%s%s%s</td><td>%s%s</td><td class="n">%s</td><td class="n">%s</td>'
            '<td class="n">%s</td><td class="n">%s</td></tr>'
            % (state, "yes" if c["organizers_channel"] else "no", e(c["city"]),
               ('<code class="chan">#%s</code>' % e(shown)) if shown else '<span class="nil">none</span>',
               '<span class="tag tag-reg">regional</span>' if state == "regional" else "",
               ('<span class="num">%s</span>'
                % ("?" if c["public_members"] is None else c["public_members"]))
               if c["public"] else "",
               ('<code class="chan">#%s</code>' % e(c["organizers_channel"]))
               if c["organizers_channel"] else '<span class="nil">none</span>',
               '<span class="tag tag-pub">public!</span>'
               if c["organizers_channel"] and not c["organizers_private"] else "",
               n or '<span class="nil">0</span>',
               ("%d/%d" % (inp, n)) if n else '<span class="nil">—</span>',
               ("%d/%d" % (ino, n)) if n and c["organizers_channel"] else '<span class="nil">—</span>',
               ('<span class="bad">%d</span>' % nos) if nos else '<span class="nil">0</span>'))

    def plist(items, extra=""):
        return ('<ul class="plist %s">%s</ul>' % (extra, "".join(
            '<li><span class="pname">%s</span><span class="pmail">%s</span></li>'
            % (e(x["name"]), e(x["email"]) or "—") for x in items)))

    rosters = []
    for c in sorted([x for x in audit if x["organizers_channel"]],
                    key=lambda x: -(x["organizers_channel_members"] or 0)):
        present = [p for p in c["accepted"] if p["in_organizers"]]
        absent = [p for p in c["accepted"] if not p["in_organizers"]]
        unresolved = [x for x in c["unaccounted"] if x["unresolved"]]
        staff = [x for x in c["unaccounted"] if not x["unresolved"] and x["is_staff"]]
        others = [x for x in c["unaccounted"]
                  if not x["unresolved"] and not x["is_staff"]]
        groups = []
        for items, pill, label, cls in (
                (present, "ok", "accepted organizer", ""),
                (staff, "mute", "staff", "plist-x"),
                (others, "warn", "not an accepted organizer", "plist-x"),
                (absent, "bad", "accepted but absent", "plist-x"),
                # Distinct from "not an accepted organizer": we could not look
                # these accounts up, so we know nothing about them. Saying so is
                # not the same as accusing them.
                (unresolved, "mute", "could not identify", "plist-x")):
            if items:
                groups.append('<div class="rgrp"><h4><span class="pill pill-%s">%s</span>'
                              '<span class="cnt">%d</span></h4>%s</div>'
                              % (pill, label, len(items), plist(items, cls)))
        rosters.append(
            '<section class="chap roster"><h3>#%s%s<span class="chapchans">'
            '<span class="nil">%s &middot; %s members</span></span></h3>'
            '<div class="rgrps">%s</div></section>'
            % (e(c["organizers_channel"]),
               "" if c["organizers_private"] else '<span class="tag tag-pub">public!</span>',
               e(c["city"]),
               "?" if c["organizers_channel_members"] is None
               else c["organizers_channel_members"], "".join(groups)))

    detail = []
    for c in audit:
        if not c["accepted"] and not c["organizers_channel"]:
            continue
        items = []
        if not c["accepted"]:
            items.append('<li class="none">No accepted organizers for this chapter</li>')
        for p in c["accepted"]:
            if not p["slack_account"]:
                pills = '<span class="pill pill-bad">no Slack account</span>'
            elif c["public"]:
                pills = ('<span class="pill pill-%s">%s #%s</span>'
                         % ("ok" if p["in_public"] else "warn",
                            "in" if p["in_public"] else "not in", e(c["public"])))
            else:
                pills = '<span class="pill pill-mute">no city channel</span>'
            if p["slack_account"] and c["organizers_channel"]:
                pills += ('<span class="pill pill-%s">%sorganizers</span>'
                          % ("ok" if p["in_organizers"] else "warn",
                             "" if p["in_organizers"] else "not in "))
            items.append('<li><span class="pname">%s</span><span class="pmail">%s</span>'
                         '<span class="pills">%s</span></li>'
                         % (e(p["name"]), e(p["email"]), pills))
        chips = " ".join(x for x in [
            ('<code class="chan">#%s</code>' % e(c["public"])) if c["public"] else "",
            ('<code class="chan">#%s</code>' % e(c["organizers_channel"]))
            if c["organizers_channel"] else ""] if x)
        detail.append('<section class="chap"><h3>%s<span class="chapchans">%s</span></h3>'
                      '<ul class="plist">%s</ul></section>'
                      % (e(c["city"]), chips or '<span class="nil">no channels</span>',
                         "".join(items)))

    create = rooms_to_create(audit)
    unreachable = [c for c in audit if c["accepted"]
                   and not any(p["slack_account"] for p in c["accepted"])]
    empty_room = [c for c in audit if c["public"] and c["accepted"]
                  and any(p["slack_account"] for p in c["accepted"])
                  and not any(p["in_public"] for p in c["accepted"])]
    public_org = [c for c in audit if c["organizers_channel"] and not c["organizers_private"]]
    # Everything below counts only people we actually identified. The
    # `unresolved` flag exists precisely so the report stops short of asserting
    # a judgement about accounts users.info would not resolve; counting them
    # here and then writing "people we never accepted" would undo that.
    named = [(c, x) for c in audit for x in c["unaccounted"] if not x["unresolved"]]
    seats = len(named)
    distinct = len({x["id"] for _, x in named})
    unidentified = len({x["id"] for c in audit for x in c["unaccounted"]
                        if x["unresolved"]})
    absent_total = sum(1 for c in audit for p in c["accepted"]
                       if c["organizers_channel"] and not p["in_organizers"])

    todo = [
        ("Make Slack part of accepting an organizer",
         "%d of %d accepted organizers have no Slack account under their intake email, and only "
         "%d are in their own city channel. Acceptance already triggers a Drive grant and a CRM "
         "row — the Slack invite and the channel joins belong on the same trigger."
         % (S["no_slack"], S["people"], S["in_public"]),
         "Half a day", "Ops", "now")]
    if public_org:
        # rs.actions() escapes every field, so these strings carry plain text and
        # real punctuation — an HTML entity here would render as its own source.
        todo.append((
            "Close the %d public “organizers” channel%s"
            % (len(public_org), "" if len(public_org) == 1 else "s"),
            " and ".join(
                "#%s (%s members)"
                % (c["organizers_channel"],
                   "?" if c["organizers_channel_members"] is None
                   else c["organizers_channel_members"])
                for c in public_org)
            + " are public. Venue costs, budgets and speaker problems are readable by the whole "
              "workspace.", "10 min", "Workspace admin", "now"))
    todo.append((
        "Reconcile the organizer rosters",
        "The %d organizer channels hold %d people we never accepted (%d seats). Meanwhile %d "
        "accepted organizers are missing from their own channel. Decide who belongs, fix both sides."
        % (S["org"], distinct, seats, absent_total), "2 hours", "Community leadership", "now"))
    if create:
        todo.append((
            "Give %d chapters their own room" % len(create),
            "Two or more accepted organizers and no channel of their own: "
            + ", ".join(c["city"] for c in create)
            + ". The other channel-less chapters keep pointing at their regional channel — a room "
              "for every one of them would just add channels nobody staffs.",
            "1 hour", "Workspace admin", "next"))
    if unreachable:
        todo.append((
            "Chase the %d unreachable chapters another way" % len(unreachable),
            "No organizer reachable on Slack at all: " + ", ".join(c["city"] for c in unreachable)
            + ". Email is the only route left, and it is the address that already failed.",
            "Half a day", "Ops", "next"))

    notes = []
    if orphans:
        notes.append("<li><strong>%d intake cities matched no chapter row</strong>: %s. Fix the "
                     "intake city or add the chapter row, then re-run.</li>"
                     % (len(orphans), ", ".join(e(c) for c in orphans)))
    if dupes:
        notes.append("<li><strong>%d duplicate intake rows</strong> for the same person and city "
                     "were dropped (first wins).</li>" % dupes)
    if unidentified:
        notes.append("<li><strong>%d organizer-channel members could not be "
                     "identified</strong> — users.info failed for them, so they are "
                     "listed as &ldquo;could not identify&rdquo; and excluded from every "
                     "count above. Re-run to retry them.</li>" % unidentified)
    known_none = [c["city"] for c in audit if c["public_how"] == "known-none"]
    if known_none:
        notes.append("<li><strong>%d chapters are recorded as having no channel on purpose</strong>"
                     " (their Slack Channel cell reads <code class=\"chan\">%s</code> on the "
                     "Chapters List): %s. The matcher did not guess for these — clear the "
                     "cell if a channel is created.</li>"
                     % (len(known_none), e(NO_RESOURCE),
                        ", ".join(e(c) for c in known_none)))
    aliased = [c for c in audit if c["public_how"] == "alias"
               or c["organizers_how"] == "alias"]
    if aliased:
        notes.append("<li><strong>%d chapters were matched by a curated alias</strong> rather than "
                     "by name, so their coverage is only as good as the Chapters List: %s.</li>"
                     % (len(aliased), ", ".join(e(c["city"]) for c in aliased)))
    planned = sorted({(c["city"], c[k].split(":", 1)[1])
                      for c in audit
                      for k in ("public_how", "organizers_how", "regional_how")
                      if c[k].startswith("planned:")})
    if planned:
        planned_cities = sorted({city for city, _ in planned})
        notes.append("<li><strong>%d channels named on the Chapters List do not exist yet</strong> "
                     "— they are the provisioning plan (run with --planned-ok), not renames. The "
                     "%d chapters counting on them are reported above as having no channel, which "
                     "is the current truth: %s. Re-run without the flag once "
                     "provision_channels.py has created them.</li>"
                     % (len(planned), len(planned_cities),
                        ", ".join(e(c) for c in planned_cities)))
    held = sorted((c["city"], c["public_how"].split(":", 1)[1])
                  for c in audit if c["public_how"].startswith("held-private:"))
    if held:
        notes.append("<li><strong>%d chapters' named channels exist but are still private</strong> "
                     "— awaiting an admin-UI convert to public, so they are reported above as "
                     "having no public channel, which is the current truth: %s.</li>"
                     % (len(held), ", ".join("%s (#%s)" % (e(c), e(n)) for c, n in held)))
    cand = [c for c in audit if not c["public"] and c["public_candidates"]]
    if cand:
        notes.append("<li><strong>%d chapters have near-miss channels</strong> that were NOT "
                     "auto-matched: %s. Confirm by hand, then fill the chapter's row.</li>"
                     % (len(cand), "; ".join("%s → %s" % (e(c["city"]),
                                                          ", ".join("#" + e(n) for n in c["public_candidates"][:3]))
                                             for c in cand[:8])))

    stamp = today.strftime("%-d %B %Y")
    body = f"""
<header>
  <div class="eyebrow">Organizer-side Slack audit &middot; {stamp}</div>
  <h1>The organizer side: coverage, rosters and the leak</h1>
  <p class="lede">For every chapter on the Chapters List: does it have a public city channel, does
  it have a private organizers channel, and are the organizers we accepted actually in them?
  Organizers are matched to Slack accounts by the email on their intake row.</p>
</header>

<div class="caveat"><strong>Read the organizers column carefully.</strong> A user token only sees
private channels its owner belongs to. A chapter shown without an organizers channel either has
none, or has one the audit account is not in — <code class="chan">users.conversations</code> does
not help, as it filters to the caller's own visibility. Public channels and their membership are
complete.</div>

<section>
  <div class="eyebrow">The numbers</div>
  <div class="stats">
    <div class="stat"><span class="v">{S['chapters']}</span><span class="k">Chapters</span></div>
    <div class="stat s-ok"><span class="v">{S['own']}</span><span class="k">Own city channel</span></div>
    <div class="stat s-warn"><span class="v">{S['regional']}</span><span class="k">Regional only</span></div>
    <div class="stat s-bad"><span class="v">{S['none']}</span><span class="k">No channel at all</span></div>
    <div class="stat s-bad"><span class="v">{S['org']}</span><span class="k">Organizers channel</span></div>
  </div>
</section>

<section>
  <div class="eyebrow">The leak</div>
  <h2>Accepting an organizer does not put them anywhere</h2>
  <p class="lede">Every one of these {S['people']} people was reviewed and accepted. The drop at
  each step is not attrition — nobody quit. No step connects an accepted organizer to the place
  their chapter lives.</p>
  <div class="funnel">{''.join(funnel)}</div>
  <div class="two" style="margin-top:24px">
    <div class="card"><h3>{len(unreachable)} chapters we cannot reach at all</h3>
      <p class="sub">No accepted organizer has a Slack account on their intake email</p>
      <div class="chips">{''.join('<span class="chip">%s · %d</span>' % (e(c["city"]), len(c["accepted"])) for c in unreachable)}</div>
      <p class="note">They may be in Slack under a different address — which is itself the finding,
      because nothing ties the intake identity to the Slack one.</p></div>
    <div class="card"><h3>{len(empty_room)} chapters have a room and nobody in it</h3>
      <p class="sub">Channel exists; zero accepted organizers are members</p>
      <div class="chips">{''.join('<span class="chip">#%s</span>' % e(c["public"]) for c in empty_room)}</div>
      <p class="note">The cheapest fixes here — the channel already exists and already has
      members.</p></div>
  </div>
</section>

<section>
  <div class="eyebrow">Coverage matrix</div>
  <h2>Every chapter, both channels</h2>
  <div class="controls" style="margin:16px 0 12px">
    <span class="lbl">Show</span>
    <button class="f" data-filter="all" aria-pressed="true">All {S['chapters']}</button>
    <button class="f" data-filter="own" aria-pressed="false">Own channel {S['own']}</button>
    <button class="f" data-filter="regional" aria-pressed="false">Regional only {S['regional']}</button>
    <button class="f" data-filter="none" aria-pressed="false">No channel {S['none']}</button>
    <button class="f" data-filter="noorg" aria-pressed="false">No organizers channel {S['chapters'] - S['org']}</button>
  </div>
  <div class="tablewrap"><table>
    <thead><tr><th>Chapter</th><th>Public channel</th><th>Organizers channel</th>
    <th class="n">Accepted</th><th class="n">In public</th><th class="n">In organizers</th>
    <th class="n">No Slack</th></tr></thead>
    <tbody>{''.join(matrix)}</tbody></table></div>
</section>

<section>
  <div class="eyebrow">Organizer channel rosters</div>
  <h2>Who is actually in each organizers channel</h2>
  <p class="lede">The full membership of all {S['org']} organizer channels, split by whether we ever
  accepted that person. {distinct} distinct people hold {seats} of these seats without having been
  accepted — some are staff who belong there, others are legacy leads nobody has reviewed.</p>
  <div class="grid-rosters" style="margin-top:20px">{''.join(rosters)}</div>
</section>

<section>
  <div class="eyebrow">What to do</div>
  <h2>Ranked by what it unblocks, not by effort</h2>
  {rs.actions(todo)}
</section>

<section>
  <div class="eyebrow">Person by person</div>
  <h2>Accepted organizers and where they actually are</h2>
  <div class="grid-chaps" style="margin-top:20px">{''.join(detail)}</div>
</section>

{('<section><div class="eyebrow">Data quality</div><h2>Fix these, then re-run</h2>'
  '<ul class="plain">' + ''.join(notes) + '</ul></section>') if notes else ''}

<footer>Sources: AAIF Community Chapters List (Chapters &amp; Teams), AAIF Community Intake Ops
(Organizers, status {' or '.join(ACCEPTED)}), and the Slack Web API. Both sheets are only ever
read. Channel matching uses the curated alias map in
<code class="chan">Chapters List</code>.</footer>
"""
    js = """
const btns=[...document.querySelectorAll('button.f')];
btns.forEach(b=>b.addEventListener('click',()=>{
  btns.forEach(o=>o.setAttribute('aria-pressed',String(o===b)));
  const f=b.dataset.filter;
  document.querySelectorAll('tbody tr').forEach(r=>{
    r.hidden = f!=='all' && !(f==='noorg' ? r.dataset.org==='no' : r.dataset.state===f);
  });
}));
"""
    return rs.page("Slack Organizers Audit", body, script=js)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-organizers-audit")
    ap.add_argument("--cache", default=".slack-audit-cache")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--planned-ok", action="store_true",
                    help="pre-provisioning: treat sheet-named channels that do "
                         "not exist yet as planned instead of aborting")
    args = ap.parse_args()

    cfg = load_config()
    # Before any collection: these paths will hold organizer names, email
    # addresses and private-channel rosters, and this repo is public.
    rs.assert_git_ignored(args.cache + os.sep, args.out + ".html", args.out + ".pdf")
    os.makedirs(args.cache, exist_ok=True)
    os.chmod(args.cache, 0o700)

    api = Slack()
    # users:read.email is what the organizer→Slack join runs on. Without it every
    # lookup fails and the report's headline becomes "nobody has a Slack account".
    api.require_scopes("channels:read", "groups:read", "users:read", "users:read.email")
    who = api.ok("auth.test")
    team_id = who.get("team_id")
    # Stamped into every cache below. The Slack CLI holds one token per
    # authenticated workspace and _find_token takes the first, so a reordered
    # credentials file can silently point a later run at a different tenant —
    # joining one workspace's channels to another's members produces a coherent,
    # entirely wrong report. A stamped cache turns that into a miss, not a lie.
    print("workspace: %s (%s)" % (who.get("team"), team_id))

    print("reading the sheets ...")
    # The three per-city tables come off the Chapters List, not the JSON, and are
    # merged into cfg here so match_channels() keeps taking one config object.
    # The sheet is the only source for them — cfg.update, not setdefault, so a
    # reintroduced JSON table could not shadow what the chapters' own rows say.
    chapters, tables = read_chapters()
    cfg.update(tables)
    people, dupes = read_intake()
    print("  %d chapters, %d accepted organizers (%d duplicate rows dropped)"
          % (len(chapters), len(people), dupes))
    print("  channel map from the sheet: %d own, %d organizers, %d regional"
          % (len(tables["public"]), len(tables["organizers"]),
             len(tables["regional"])))

    chan_path = os.path.join(args.cache, "channels.json")
    chans = read_cache(chan_path, args.refresh, team_id, note=print)
    chans_cached = chans is not None      # the short-check below needs to know
    if chans is None:
        print("  fetching channels ...")
        chans = channels(api)
        write_cache(chan_path, chans, team_id)
    else:
        print("  reusing cached channel list (%d, %s)"
              % (len(chans), cache_age(chan_path)))

    rows = match_channels(chapters, chans, cfg)
    if args.planned_ok:
        planned, held = mark_planned_aliases(rows)
        if planned:
            print("  %d sheet-named channel(s) do not exist yet — treated as "
                  "planned, not as renames" % len(planned))
        if held:
            print("  %d sheet-named channel(s) exist but are still PRIVATE — "
                  "reported as no public channel yet" % len(held))
    else:
        assert_aliases_resolve(rows)
    print("  matched: %d own channel, %d organizers channel"
          % (sum(1 for r in rows if r["public"]),
             sum(1 for r in rows if r["organizers_channel"])))

    # Reconcile rather than reuse wholesale: an organizer accepted since the last
    # run is absent from the cache, and a bare .get would report them as having
    # no Slack account — silently, and about the newest people in the pipeline.
    wanted = {p["email"] for p in people if p["email"]}
    ids_path = os.path.join(args.cache, "organizer_ids.json")
    cached_ids = read_cache(ids_path, args.refresh, team_id, note=print)
    slack_ids = cached_ids if cached_ids is not None else {}
    if cached_ids is not None:
        print("  reusing %d cached organizer lookups (%s)"
              % (len(cached_ids), cache_age(ids_path)))
    # Re-check misses as well as absences. A cached {"id": None} answers "not in
    # Slack *then*"; someone who has since joined would otherwise stay in the
    # "cannot reach at all" card and its ranked action permanently.
    resolved_before = {k for k, v in slack_ids.items() if (v or {}).get("id")}
    outstanding = wanted - resolved_before
    if outstanding:
        print("  resolving %d organizer emails, %d already cached (about 1.5s each) ..."
              % (len(outstanding), len(slack_ids)))
        slack_ids.update(lookup_emails(api, sorted(outstanding)))
        write_cache(ids_path, slack_ids, team_id)
    resolved = sum(1 for email in wanted if (slack_ids.get(email) or {}).get("id"))
    blank = sum(1 for p in people if not p["email"])
    print("  %d/%d organizers resolved to a Slack account%s"
          % (resolved, len(wanted),
             " (%d more have no email on file)" % blank if blank else ""))

    targets = {}
    for r in rows:
        for name, cid in ((r["public"], r["public_id"]),
                          (r["organizers_channel"], r["organizers_id"])):
            if name:
                targets[name] = cid
    print("  pulling membership for %d channels ..." % len(targets))
    membership = {name: members(api, cid) for name, cid in sorted(targets.items())}
    # conversations.list already told us how big each channel is; comparing
    # that against what conversations.members returned is a free floor on a
    # short paged pull, which would otherwise render as "accepted but absent"
    # pills. The stale-cache / join-race / renamed-channel reasoning lives on
    # check_membership_floor. `rows` keeps the earlier snapshot — the
    # membership ids above were pulled for exactly those channels.
    def refetch():
        fresh = channels(api)
        write_cache(chan_path, fresh, team_id)
        return fresh
    chans = check_membership_floor(membership, chans, chans_cached, refetch)

    # Only the organizer-channel members need naming, so resolve those ids
    # individually rather than pulling a 30k-row directory.
    needed = {uid for r in rows if r["organizers_channel"]
              for uid in membership[r["organizers_channel"]]}
    dir_path = os.path.join(args.cache, "org_members.json")
    cached_dir = read_cache(dir_path, args.refresh, team_id, note=print)
    directory = cached_dir if cached_dir is not None else {}
    if cached_dir is not None:
        print("  reusing %d cached member names (%s)"
              % (len(cached_dir), cache_age(dir_path)))
    missing = needed - set(directory)
    if missing:
        print("  naming %d organizer-channel members ..." % len(missing))
        failed = []
        for uid in sorted(missing):
            payload = api.call("users.info", user=uid)
            if payload.get("ok"):
                u, prof = payload["user"], (payload["user"].get("profile") or {})
                directory[uid] = {"real_name": u.get("real_name") or prof.get("real_name", ""),
                                  "name": u.get("name", ""),
                                  "email": (prof.get("email") or "").lower()}
            else:
                failed.append((uid, payload.get("error", "unknown")))
        write_cache(dir_path, directory, team_id)
        if failed:
            # Not fatal — a genuinely deleted account is a real answer — but it
            # must be visible, because an unnamed member renders as a raw Slack
            # ID and would otherwise be filed under a group whose label asserts
            # a judgement nobody made about them.
            print("  WARNING: could not name %d of %d members (%s). They are "
                  "reported as 'could not identify', not as unaccounted people."
                  % (len(failed), len(missing),
                     ", ".join(sorted({err for _, err in failed}))), file=sys.stderr)

    audit, orphans = build_audit(rows, people, slack_ids, membership, directory,
                                 cfg["staff_email_domain"])
    write_cache(os.path.join(args.cache, "audit.json"),
                {"chapters": audit, "orphan_cities": orphans, "duplicates": dupes},
                team_id)

    html_doc = render(audit, orphans, dupes, dt.datetime.now(dt.timezone.utc))
    html_path = args.out + ".html"
    rs.write_private(html_path, html_doc)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path),
                                     os.path.abspath(args.out + ".pdf")))


if __name__ == "__main__":
    main()
