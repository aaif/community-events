"""Atomic, stamped JSON caches for the long-running audit pulls.

A directory pull can take twenty minutes, so the audits cache their raw API
results. `read()` returning a payload means "trust this and publish it", so
every guard here exists to stop a corrupt, foreign or stale file becoming that
answer:

* **A half-written file.** `json.dump` is not atomic; interrupt it and the next
  run gets a `JSONDecodeError` pointing at a byte offset, with no hint that the
  fix is to delete the file. Writes go to a unique temp path and are
  `os.replace`d, so a cache file is either the previous complete one or the new
  complete one — never a fragment, and never two concurrent runs interleaved.
* **A cache nobody can date.** The skill tells operators not to re-fetch unless
  the data is stale, while giving them no way to tell. Every file carries the
  UTC time it was written, and `age()` renders it for the progress line.
* **A cache from the wrong workspace.** The Slack CLI holds one token per
  authenticated team and `slack._find_token` takes the first; a reordered
  credentials file can point a later run at a different tenant. Joining one
  workspace's channels to another's members yields a coherent, entirely wrong
  report, so the team id is stamped in and a mismatch is a miss.

Files are created 0600 — never merely chmod'd to it afterwards, which would
leave the member directory world-readable for the length of the write and
permanently if the run were killed in between.
"""

import datetime as dt
import json
import os
import tempfile

#: Bump when the envelope shape changes **or when any cached payload's shape
#: changes** — the payload is the part that actually varies (this module has
#: already seen `num_members` become tri-state and a user record restructured).
#: Versioning only the stable wrapper would let a stale-shaped payload load
#: cleanly and be reported on. A mismatch is a miss, not an error.
FORMAT = 2

#: How long a cached pull may be trusted. Caching locally is the point of this
#: module — a directory pull takes twenty minutes — but a cache with no expiry
#: is how a report comes to describe last week's workspace in the present tense,
#: and a stale measurement reads exactly like a fresh one.
#:
#: A day is chosen because that is the granularity the estate actually moves at:
#: channels are provisioned in bulk passes, organizers are triaged in sittings,
#: and nothing here is minute-to-minute. Past it, `read()` discards and the
#: caller re-fetches — announced through `note`, never silently.
#:
#: Pass `max_age=None` to opt out. The scripts that only CONSUME a cache
#: another audit produces (chapter_health, audit_topics, audit_members reading
#: activity.json) do: they cannot refetch, and each dates the sweep itself, so
#: discarding would turn a dated finding into no finding at all.
MAX_AGE = dt.timedelta(days=1)

_MISS = object()   # distinguishes "no payload key" from a stored None


def write(path, payload, team_id=None):
    """Write `payload` atomically, stamped with time, format and workspace.

    A caller that passes `team_id` to read() must also pass it here: read()
    discards an unstamped cache, so skipping the stamp on write means every
    run discards and re-fetches, forever.
    """
    envelope = {"format": FORMAT,
                "written_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "team_id": team_id,
                "payload": payload}
    # mkstemp: 0600 from creation, and a unique name so two concurrent runs
    # against one cache directory cannot truncate each other's partial file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               prefix=os.path.basename(path) + ".", suffix=".partial")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)
            fh.flush()
            os.fsync(fh.fileno())  # data on disk before the rename is; power
            #                        loss must not leave an empty renamed cache
        os.replace(tmp, path)      # atomic; the 0600 mode travels with the file
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)         # never leave a fragment behind
        raise
    return path


def _envelope(path):
    """Parse a cache file into an envelope dict, or None if it isn't one."""
    try:
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "Cache %s is corrupt (%s) — most likely a run killed mid-write. "
            "Delete it, or pass --refresh to re-fetch." % (path, exc))
    return envelope if isinstance(envelope, dict) else None


def written_at(path):
    """The UTC datetime a cache was written, or None if it cannot be dated.

    Shared by `read()` (which enforces MAX_AGE) and `age()` (which renders it),
    so the staleness decision and the line describing it cannot disagree.
    """
    if not os.path.exists(path):
        return None
    envelope = _envelope(path)
    stamp = envelope.get("written_utc") if envelope else None
    if not isinstance(stamp, str):
        return None
    try:
        written = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return written.replace(tzinfo=dt.timezone.utc) if written.tzinfo is None else written


def read(path, refresh=False, team_id=None, note=None, max_age=MAX_AGE, now=None):
    """Return the cached payload, or None to signal 'fetch it'.

    Test the result with `is None`, never for truthiness: an empty list is a
    legitimate payload, and `read(...) or fetch()` would re-fetch it every run.

    A discarded cache is announced through `note` (a print-like callable) rather
    than dropped in silence — throwing away a twenty-minute pull is exactly the
    kind of event this codebase refuses to hide. A corrupt file still raises,
    since silently re-fetching it would mask a bug.
    """
    if refresh or not os.path.exists(path):
        return None
    envelope = _envelope(path)

    def discard(why):
        if note:
            note("  discarding cache %s (%s) — re-fetching" % (path, why))
        return None

    if envelope is None:
        return discard("not a cache envelope")
    if envelope.get("format") != FORMAT:
        return discard("format %r, expected %d" % (envelope.get("format"), FORMAT))
    stamped = envelope.get("team_id")
    if team_id and stamped and stamped != team_id:
        return discard("workspace %s, expected %s" % (stamped, team_id))
    if team_id and not stamped:
        # The caller knows which workspace it is auditing; an unstamped cache
        # cannot prove it matches, and a wrong-tenant join reads as a coherent,
        # entirely wrong report.
        return discard("no workspace stamp, expected %s" % team_id)
    if max_age is not None:
        written = written_at(path)
        if written is None:
            # An undated cache cannot be shown to be within MAX_AGE, and this
            # module's whole claim is that a reused payload is recent enough to
            # publish. Undatable is treated as too old, not as "probably fine".
            return discard("undated, so its age cannot be checked")
        old = (now or dt.datetime.now(dt.timezone.utc)) - written
        if old > max_age:
            return discard("written %s ago, older than the %s limit"
                           % (_duration(old), _duration(max_age)))
    payload = envelope.get("payload", _MISS)
    return None if payload is _MISS else payload


def _duration(delta):
    """`2d 3h` / `5h` / `12m` — enough to judge a cache, short enough for a line."""
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return "%dd %dh" % (days, hours) if hours else "%dd" % days
    if hours:
        return "%dh" % hours
    return "%dm" % max(1, rem // 60)


def age(path, now=None):
    """Human-readable age of a cache file, for progress output.

    Never raises for a cache `read()` would have tolerated: this feeds a
    progress line, and a cosmetic field must not kill a run.
    """
    if not os.path.exists(path):
        return "absent"
    envelope = _envelope(path)
    stamp = envelope.get("written_utc") if envelope else None
    if not isinstance(stamp, str):
        return "undated"
    try:
        written = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return "undated"
    if written.tzinfo is None:
        written = written.replace(tzinfo=dt.timezone.utc)
    days = ((now or dt.datetime.now(dt.timezone.utc)) - written).days
    if days <= 0:
        return "fetched today"
    if days == 1:
        return "fetched yesterday"
    return "fetched %d days ago%s" % (days, " — consider --refresh" if days > 14 else "")
