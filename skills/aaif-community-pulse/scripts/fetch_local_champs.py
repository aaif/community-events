#!/usr/bin/env python3
"""Pull recent human messages from #local-champs for drafting the Pulse post.

Read-only: uses only `Slack.call()` methods already in `slack.ALLOWED_METHODS`
(`conversations.list`, `conversations.history`, `users.info`). Unlike the
audit engines, this DOES retain message text — the Pulse is written from what
organizers and admins actually said, not just activity counts. That is exactly
why the output is never committed: it holds real names and real message
content from a private-ish leadership channel. Written 0600 to
`.pulse-cache/`, which is `.gitignore`d; delete it once the Pulse draft is done.

Usage: fetch_local_champs.py [--days N] [--out PATH]
"""
import argparse
import datetime as dt
import json
import os
import stat
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events.slack import ALLOWED_METHODS, Slack, SlackError  # noqa: E402

CHANNEL_NAME = "local-champs"


def find_channel(api, name):
    cursor = None
    while True:
        resp = api.call("conversations.list", types="public_channel,private_channel",
                         exclude_archived="true", limit=200, cursor=cursor)
        for c in resp.get("channels", []):
            if c["name"] == name:
                return c["id"]
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return None


def fetch_messages(api, channel_id, oldest):
    assert "conversations.history" in ALLOWED_METHODS
    out = []
    cursor = None
    while True:
        resp = api.call("conversations.history", channel=channel_id,
                         oldest=oldest, limit=200, cursor=cursor)
        for m in resp.get("messages", []):
            if m.get("subtype") or m.get("bot_id"):
                continue  # joins/leaves/bot posts aren't organizer updates
            out.append({"ts": m["ts"], "user": m.get("user", ""), "text": m.get("text", "")})
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def resolve_names(api, user_ids):
    names = {}
    for uid in user_ids:
        try:
            info = api.call("users.info", user=uid)["user"]
        except SlackError:
            names[uid] = uid
            continue
        names[uid] = info.get("real_name") or info.get("name") or uid
    return names


def write_0600(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14,
                     help="how far back to read (default 14, matching the biweekly cadence)")
    ap.add_argument("--out", default=".pulse-cache/local-champs.json")
    args = ap.parse_args()

    api = Slack()
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
