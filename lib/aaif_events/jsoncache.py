"""Atomic, stamped JSON caches for the long-running audit pulls.

A directory pull can take twenty minutes, so the audits cache their raw API
results. That makes two failure modes worth engineering against:

* **A half-written file.** `json.dump` is not atomic; interrupt it and the next
  run gets a `JSONDecodeError` pointing at a byte offset, with no hint that the
  fix is to delete the file. Writes go to a temp path and are `os.replace`d, so
  a cache file is either the previous complete one or the new complete one.
* **A cache nobody can date.** The skill tells operators not to re-fetch unless
  the data is stale, while giving them no way to tell. Every file carries the
  UTC time it was written, and `age()` renders it for the progress line.

Files are written 0600: they hold member names, email addresses and 2FA flags.
"""

import datetime as dt
import json
import os

#: Bumped only if the envelope shape changes; a mismatch is treated as a miss
#: rather than an error, so an old cache is silently re-fetched.
FORMAT = 1


def write(path, payload):
    """Write `payload` atomically, stamped with the time and format version."""
    tmp = path + ".partial"
    envelope = {"format": FORMAT,
                "written_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "payload": payload}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def _envelope(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "Cache %s is corrupt (%s) — most likely a run killed mid-write. "
            "Delete it, or pass --refresh to re-fetch." % (path, exc))


def read(path, refresh=False):
    """Return the cached payload, or None to signal 'fetch it'.

    None rather than an exception so callers can write the ordinary
    `data = read(...) or fetch()` shape; a corrupt file still raises, because
    silently re-fetching it would hide a bug.
    """
    if refresh or not os.path.exists(path):
        return None
    envelope = _envelope(path)
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        return None  # older or foreign shape — re-fetch rather than guess
    return envelope.get("payload")


def age(path, now=None):
    """Human-readable age of a cache file, for progress output."""
    if not os.path.exists(path):
        return "absent"
    stamp = _envelope(path).get("written_utc")
    if not stamp:
        return "undated"
    written = dt.datetime.fromisoformat(stamp)
    days = ((now or dt.datetime.now(dt.timezone.utc)) - written).days
    if days <= 0:
        return "fetched today"
    if days == 1:
        return "fetched yesterday"
    return "fetched %d days ago%s" % (days, " — consider --refresh" if days > 14 else "")
