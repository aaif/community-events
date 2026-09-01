#!/usr/bin/env python3
"""Pull recent human messages from #local-champs for drafting the Pulse post.

Read-only: uses only `Slack.paged()`/`Slack.ok()` over methods already in
`slack.ALLOWED_METHODS` (`conversations.list`, `conversations.history`,
`users.info`). Unlike the audit engines, this DOES retain message text — the
Pulse is written from what organizers and admins actually said, not just
activity counts. Thread replies are invisible to `conversations.history`
(broadcasts excepted), so this is a floor on what was actually discussed —
say so if the channel looks quieter than expected.

That is exactly why the output is never committed: it holds real names and
real message content from a private-ish leadership channel. Written 0600 to
`.pulse-cache/`, which is `.gitignore`d; delete it once the Pulse draft is
done.

Usage: fetch_local_champs.py [--days N] [--out PATH]
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))

from aaif_events.slack import Slack, SlackError  # noqa: E402

CHANNEL_NAME = "local-champs"
#: `.pulse-cache/` is the only directory this script is ever allowed to write
#: into — real Slack names and message text belong nowhere else, and nothing
#: else in this repo gitignores an arbitrary `--out` path.
CACHE_DIR_NAME = ".pulse-cache"


def find_channel(api, name):
    for c in api.paged("conversations.list", "channels",
                        types="public_channel,private_channel",
                        exclude_archived="true", limit=200):
        if c["name"] == name:
            return c["id"]
    return None


def fetch_messages(api, channel_id, oldest):
    out = []
    for m in api.paged("conversations.history", "messages",
                        channel=channel_id, oldest=oldest, limit=200):
        if m.get("subtype") or m.get("bot_id"):
            continue  # joins/leaves/bot posts aren't organizer updates
        out.append({"ts": m["ts"], "user": m.get("user", ""), "text": m.get("text", "")})
    return out


def resolve_names(api, user_ids):
    names = {}
    for uid in user_ids:
        try:
            info = api.ok("users.info", user=uid)["user"]
        except SlackError:
            names[uid] = uid
            continue
        names[uid] = info.get("real_name") or info.get("name") or uid
    return names


def write_0600(path, payload):
    """Write `payload` as 0600 JSON, refusing to follow or reuse anything at `path`.

    `path` must resolve inside `.pulse-cache/` — the one directory this repo
    gitignores for skill-generated PII — so a stray `--out` can't smuggle real
    Slack content somewhere `git add -A` would pick up. The write itself goes
    through `tempfile.mkstemp` (0600 from creation, same as
    `aaif_events.jsoncache.write`) plus `os.replace`, which replaces whatever
    is at the destination path — including a symlink, atomically, without ever
    opening through it — rather than truncating-in-place, which would silently
    follow a symlink or inherit an existing file's looser permissions.
    """
    resolved = pathlib.Path(path).resolve()
    cache_root = (pathlib.Path.cwd() / CACHE_DIR_NAME).resolve()
    if cache_root != resolved and cache_root not in resolved.parents:
        raise ValueError("refusing to write outside %s/: %s" % (CACHE_DIR_NAME, resolved))

    dirpath = str(resolved.parent)
    os.makedirs(dirpath, mode=0o700, exist_ok=True)
    os.chmod(dirpath, 0o700)  # enforce even if the directory already existed looser
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=resolved.name + ".", suffix=".partial")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, resolved)
    except BaseException:
        os.unlink(tmp)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14,
                     help="how far back to read (default 14, matching the biweekly cadence)")
    ap.add_argument("--out", default=os.path.join(CACHE_DIR_NAME, "local-champs.json"))
    args = ap.parse_args()

    api = Slack()
    api.require_scopes("channels:read", "groups:read", "channels:history", "groups:history")
    oldest = time.time() - args.days * 86400
    print("resolving #%s ..." % CHANNEL_NAME, file=sys.stderr)
    channel_id = find_channel(api, CHANNEL_NAME)
    if not channel_id:
        print("ERROR: #%s not found or not visible to this token" % CHANNEL_NAME,
              file=sys.stderr)
        sys.exit(1)

    print("fetching messages since %s ..." %
          dt.datetime.fromtimestamp(oldest, dt.timezone.utc).date(), file=sys.stderr)
    messages = fetch_messages(api, channel_id, oldest)
    names = resolve_names(api, sorted({m["user"] for m in messages if m["user"]}))
    for m in messages:
        m["name"] = names.get(m["user"], m["user"])

    write_0600(args.out, {
        "channel": CHANNEL_NAME,
        "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "since": dt.datetime.fromtimestamp(oldest, dt.timezone.utc).isoformat(),
        "messages": sorted(messages, key=lambda m: float(m["ts"])),
    })
    print("wrote %d messages to %s (0600 — PII, never commit; delete when done)" %
          (len(messages), args.out), file=sys.stderr)


if __name__ == "__main__":
    main()
