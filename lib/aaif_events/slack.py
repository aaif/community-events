"""Read-only Slack Web API client for the workspace audits.

Auth comes from the Slack CLI's own credentials (`slack auth login`), so no token
is ever stored in this repo or passed on a command line. The token is never
printed — callers get data, not credentials.

**This client is read-only by construction.** `call()` refuses any method outside
`ALLOWED_METHODS`; the audits inspect a community workspace, and a typo must not
be able to post, invite, or archive.

## What the audit token can and cannot do

The Slack CLI's user token carries `channels:read, groups:read, users:read,
users:read.email, team:read`. That is enough to enumerate channels, their
membership, and the user directory. It is **not** enough to read messages:
`conversations.history` and `search.messages` both return `missing_scope`, so
nothing built on this module can measure whether a channel is *active* — only
whether it exists, who is in it, and how it is described.

Two traps worth knowing, both verified against the live workspace:

* **`conversations.list` only returns private channels the token owner belongs
  to.** `users.conversations(user=...)` looks like a way around that, but its
  results are filtered to the caller's own visibility — probing 101 people
  returned exactly the caller's own 23 private channels and nothing more. There
  is no workspace-wide private channel listing without Enterprise Grid.
* **A channel's `updated` field is not activity.** It is a metadata stamp that
  a bulk migration reset in blocks (54 channels share one value), so it must
  never be used as a staleness signal. `topic.last_set` / `purpose.last_set` are
  genuine human edits and are exposed instead.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

CRED_PATH = os.path.expanduser("~/.slack/credentials.json")
API = "https://slack.com/api/"

#: Every method the audits are permitted to call. Read-only, no exceptions.
ALLOWED_METHODS = frozenset({
    "auth.test", "team.info",
    "conversations.list", "conversations.members", "conversations.info",
    "users.list", "users.info", "users.lookupByEmail", "users.conversations",
})

MAX_ATTEMPTS = 6


class SlackError(RuntimeError):
    """A Slack API call returned ok:false."""

    def __init__(self, method, error):
        super().__init__("%s: %s" % (method, error))
        self.method = method
        self.error = error


def _find_token(obj):
    """Return the first Slack token in the credentials blob, without logging it."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.startswith(("xoxp-", "xoxb-", "xoxe.xoxp-")):
            found.append(node)

    walk(obj)
    return found[0] if found else None


def load_token(path=CRED_PATH):
    """Read the Slack CLI credentials and return its token."""
    if not os.path.exists(path):
        raise SystemExit(
            "No Slack credentials at %s — run `slack auth login` first." % path)
    with open(path) as fh:
        token = _find_token(json.load(fh))
    if not token:
        raise SystemExit("No Slack token found in %s." % path)
    return token


class Slack:
    """Minimal read-only Web API client with retry and pagination."""

    def __init__(self, token=None, sleep=time.sleep):
        self._token = token or load_token()
        self._sleep = sleep

    def call(self, method, **params):
        """POST to a Slack method, retrying rate limits and truncated reads."""
        if method not in ALLOWED_METHODS:
            raise ValueError(
                "%s is not a read-only audit method; refusing to call it." % method)
        body = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}).encode()
        for attempt in range(MAX_ATTEMPTS):
            request = urllib.request.Request(
                API + method, data=body,
                headers={"Authorization": "Bearer " + self._token,
                         "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < MAX_ATTEMPTS - 1:
                    self._sleep(int(exc.headers.get("Retry-After", "5")))
                    continue
                raise
            except (http.client.IncompleteRead, urllib.error.URLError):
                # Slack truncates very large pages; back off and retry the same cursor.
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                self._sleep(2 * (attempt + 1))
                continue
            if not payload.get("ok") and payload.get("error") == "ratelimited":
                self._sleep(int(payload.get("retry_after", 5)))
                continue
            return payload
        raise SlackError(method, "gave up after %d rate-limited attempts" % MAX_ATTEMPTS)

    def ok(self, method, **params):
        """Like call(), but raise SlackError instead of returning ok:false."""
        payload = self.call(method, **params)
        if not payload.get("ok"):
            raise SlackError(method, payload.get("error", "unknown"))
        return payload

    def paged(self, method, key, **params):
        """Yield every item across a cursor-paginated method."""
        cursor = None
        while True:
            payload = self.ok(method, cursor=cursor, **params)
            for item in payload.get(key, []):
                yield item
            cursor = (payload.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return


# --------------------------------------------------------------------------
# Collectors — each returns plain dicts/lists, ready to cache as JSON.
# --------------------------------------------------------------------------

def channels(api):
    """Every conversation the token can see, archived ones included.

    Private channels are limited to those the token owner belongs to; see the
    module docstring. `updated_ms` is returned for completeness but must not be
    read as activity.
    """
    out = []
    for c in api.paged("conversations.list", "channels",
                       types="public_channel,private_channel",
                       exclude_archived="false", limit=1000):
        topic = c.get("topic") or {}
        purpose = c.get("purpose") or {}
        out.append({
            "id": c["id"], "name": c["name"],
            "is_private": c["is_private"], "is_archived": c.get("is_archived", False),
            "is_general": c.get("is_general", False),
            "is_member": c.get("is_member", False),
            "is_ext_shared": c.get("is_ext_shared", False),
            "num_members": c.get("num_members") or 0,
            "created": c.get("created"), "creator": c.get("creator", ""),
            "updated_ms": c.get("updated"),
            "topic": topic.get("value", ""), "topic_last_set": topic.get("last_set") or 0,
            "purpose": purpose.get("value", ""),
            "purpose_last_set": purpose.get("last_set") or 0,
        })
    return out


USER_FIELDS = ("id", "name", "deleted", "is_bot", "is_app_user", "is_admin", "is_owner",
               "is_primary_owner", "is_restricted", "is_ultra_restricted",
               "is_email_confirmed", "has_2fa", "tz", "updated")


def _user_record(u):
    profile = u.get("profile") or {}
    record = {f: u.get(f) for f in USER_FIELDS}
    record["real_name"] = u.get("real_name") or profile.get("real_name", "")
    record["email"] = (profile.get("email") or "").lower()
    record["title"] = profile.get("title", "")
    record["has_avatar"] = bool(profile.get("image_original"))
    return record


def users(api, progress=None):
    """The whole user directory. Slow on a large workspace — cache the result."""
    out = []
    for u in api.paged("users.list", "members", limit=200):
        out.append(_user_record(u))
        if progress and len(out) % 2000 == 0:
            progress(len(out))
    return out


def members(api, channel_id):
    """Member ids of one conversation."""
    return list(api.paged("conversations.members", "members",
                          channel=channel_id, limit=1000))


def lookup_emails(api, emails, progress=None):
    """Map each email to its Slack account, or to a miss.

    Returns ``{email: {"id": ..., "real_name": ...}}`` with ``id: None`` and an
    ``error`` key where the address matches nobody. A miss is not proof the
    person is absent — they may hold a Slack account under a different address.
    """
    resolved = {}
    for i, email in enumerate(sorted(set(emails)), 1):
        if not email:
            continue
        payload = api.call("users.lookupByEmail", email=email)
        if payload.get("ok"):
            user = payload["user"]
            profile = user.get("profile") or {}
            resolved[email] = {
                "id": user["id"], "name": user.get("name"),
                "real_name": user.get("real_name") or profile.get("real_name", ""),
                "deleted": user.get("deleted", False)}
        else:
            resolved[email] = {"id": None, "error": payload.get("error")}
        if progress:
            progress(i)
    return resolved


def scopes(token=None):
    """OAuth scopes on the token, read from a live response header."""
    request = urllib.request.Request(
        API + "auth.test", data=b"",
        headers={"Authorization": "Bearer " + (token or load_token())})
    with urllib.request.urlopen(request) as response:
        return (response.headers.get("x-oauth-scopes") or "").split(",")
