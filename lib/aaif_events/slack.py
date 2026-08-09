"""Read-only Slack Web API client for the workspace audits.

Auth comes from the Slack CLI's own credentials (`slack auth login`), so no token
is ever stored in this repo or passed on a command line. The token is never
printed — callers get data, not credentials.

**This client is read-only.** `call()` refuses any method outside
`ALLOWED_METHODS`; the audits inspect a community workspace, and a typo must not
be able to post, invite, or archive. `Slack.scopes()` is the one request that
does not go through `call()` — it needs the response *headers* — and it is
hardcoded to `auth.test`. Keep it that way: it is the only sanctioned exception,
and it is not a precedent for adding more.

## What the audit token can and cannot do

The Slack CLI's user token carries `channels:read, groups:read, users:read,
users:read.email, team:read` (as observed 2026-08-08; call `Slack.scopes()` for
the live list rather than trusting this line). That is enough to enumerate
channels, their membership, and the user directory. It is **not** enough to read
messages: `conversations.history` and `search.messages` both return
`missing_scope`, so nothing built on this module can measure whether a channel is
*active* — only whether it exists, who is in it, and how it is described.

Two traps worth knowing, both verified against the live workspace on 2026-08-08.
The dates matter: the counts are one-shot observations and will drift, but the
mechanisms they demonstrate are stable.

* **`conversations.list` only returns private channels the token owner belongs
  to.** `users.conversations(user=...)` looks like a way around that, but its
  results are filtered to the caller's own visibility — probing 101 people
  returned exactly the caller's own 23 private channels and nothing more. There
  is no workspace-wide private channel listing without Enterprise Grid.
* **A channel's `updated` field is not activity.** It is a metadata stamp that a
  bulk migration reset in blocks (large groups of channels share one identical
  value), so it must never be used as a staleness signal. `topic.last_set` /
  `purpose.last_set` are genuine human edits and are exposed instead.
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

#: The exact set of methods this repo calls. This is deliberately *not* "every
#: read-only method" — narrowing it to actual callers is a stronger, self-
#: maintaining invariant. Adding an entry is a real decision, not a formality:
#: `conversations.history` is read-only and would still falsify the "no message
#: data" caveat both reports print on their face.
ALLOWED_METHODS = frozenset({
    "auth.test",
    "conversations.list", "conversations.members",
    "users.list", "users.info", "users.lookupByEmail",
})

#: Error codes this module raises itself, as opposed to relaying from Slack.
#: Kept as a set so the vocabulary is greppable from one place.
MODULE_ERRORS = frozenset({
    "retry_exhausted",     # burnt the retry budget; detail names the last cause
    "transport_failed",    # socket/TLS/DNS failure on the final attempt
    "malformed_page",      # ok:true but the collection key was absent
    "lookup_failed",       # a lookup failed for API reasons, not absence
})

MAX_ATTEMPTS = 6
#: Slack's own read timeout is generous; ours bounds a half-open socket so a
#: 20-minute directory pull cannot hang forever with no output.
TIMEOUT_S = 30

#: `users.lookupByEmail` errors that genuinely mean "nobody has this address".
#: Everything else means the audit is broken, not that the person is absent —
#: see lookup_emails().
BENIGN_LOOKUP_MISSES = frozenset({"users_not_found"})


class SlackError(RuntimeError):
    """A Slack API call failed.

    `error` carries a machine-readable code so callers can branch on it: either
    Slack's own (`missing_scope`, `users_not_found`, …) or one of this module's,
    listed in MODULE_ERRORS. Prose belongs in the message, not in `error`.
    """

    def __init__(self, method, error, detail=""):
        super().__init__("%s: %s%s" % (method, error, (" — " + detail) if detail else ""))
        self.method = method
        self.error = error
        self.detail = detail


def _find_token(obj):
    """Return the first Slack token in the credentials blob, without logging it.

    The *first*: `~/.slack/credentials.json` holds one entry per authenticated
    workspace, and being logged into several is the normal state for the Slack
    CLI. Whichever the walk reaches first wins, which may not be the one you
    meant — that is why every entry point prints the workspace from `auth.test`
    before fetching anything. Do not remove those prints; they are the check.
    """
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
    with open(path, encoding="utf-8") as fh:
        token = _find_token(json.load(fh))
    if not token:
        raise SystemExit("No Slack token found in %s." % path)
    return token


class Slack:
    """Minimal read-only Web API client with retry and pagination."""

    def __init__(self, token=None, sleep=time.sleep):
        self._token = token or load_token()
        self._sleep = sleep

    def _request(self, method, data=b""):
        return urllib.request.Request(
            API + method, data=data,
            headers={"Authorization": "Bearer " + self._token,
                     "Content-Type": "application/x-www-form-urlencoded"})

    def call(self, method, **params):
        """POST to a Slack method, retrying rate limits and truncated reads."""
        if method not in ALLOWED_METHODS:
            raise ValueError(
                "%s is not a read-only audit method; refusing to call it." % method)
        body = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}).encode()
        last = "unknown"
        for attempt in range(MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(self._request(method, body),
                                            timeout=TIMEOUT_S) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < MAX_ATTEMPTS - 1:
                    last = "http_429"
                    self._sleep(int(exc.headers.get("Retry-After", "5")))
                    continue
                # One vocabulary out of call(): a caller writing `except
                # SlackError` must not miss the final 429 or a 5xx.
                raise SlackError(method, "http_%d" % exc.code, exc.reason or "")
            except (http.client.HTTPException, urllib.error.URLError,
                    ConnectionError, TimeoutError) as exc:
                # Slack truncates very large pages (IncompleteRead) and long
                # pulls meet resets and disconnects; back off and retry the same
                # cursor. URLError also covers DNS and TLS failures, which never
                # recover — they exhaust the budget and surface with the cause
                # named rather than as a bare traceback mid-pull.
                if attempt == MAX_ATTEMPTS - 1:
                    raise SlackError(method, "transport_failed", repr(exc))
                last = type(exc).__name__
                self._sleep(2 * (attempt + 1))
                continue
            if not payload.get("ok") and payload.get("error") == "ratelimited":
                last = "ratelimited"
                if attempt == MAX_ATTEMPTS - 1:
                    break          # no point sleeping before giving up
                self._sleep(int(payload.get("retry_after", 5)))
                continue
            return payload
        raise SlackError(method, "retry_exhausted",
                         "%d attempts, last failure: %s" % (MAX_ATTEMPTS, last))

    def ok(self, method, **params):
        """Like call(), but raise SlackError instead of returning ok:false."""
        payload = self.call(method, **params)
        if not payload.get("ok"):
            raise SlackError(method, payload.get("error", "unknown"))
        return payload

    def paged(self, method, key, **params):
        """Yield every item across a cursor-paginated method.

        Refuses to treat a malformed page as an empty one: an `ok:true` response
        missing the collection key would otherwise end the stream silently, and a
        short pull is indistinguishable from a complete one once it is cached.
        """
        cursor = None
        while True:
            payload = self.ok(method, cursor=cursor, **params)
            if key not in payload:
                raise SlackError(method, "malformed_page",
                                 "ok:true but no %r key — cannot distinguish an "
                                 "empty page from a lost one" % key)
            for item in payload[key]:
                yield item
            cursor = (payload.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    def scopes(self):
        """OAuth scopes on this client's token, read from a live response header.

        The one request that bypasses `call()` — it needs the response headers,
        which `call()` discards. Hardcoded to `auth.test`; see the module
        docstring.
        """
        with urllib.request.urlopen(self._request("auth.test"),
                                    timeout=TIMEOUT_S) as response:
            raw = response.headers.get("x-oauth-scopes") or ""
            body = json.loads(response.read())
        if not body.get("ok"):
            # Without this, a dead token yields no scope header, an empty set,
            # and a "missing scopes" message that sends the operator hunting
            # through the app config for a problem that is really the token.
            raise SlackError("auth.test", body.get("error", "unknown"),
                             "the token is not usable; run `slack auth login`")
        return {s.strip() for s in raw.split(",") if s.strip()}

    def require_scopes(self, *needed):
        """Abort unless the token carries every scope in `needed`.

        Called before any collection, so a missing scope fails in the first
        second rather than being rendered as a finding twenty minutes later.
        """
        have = self.scopes()
        missing = [s for s in needed if s not in have]
        if missing:
            raise SystemExit(
                "Token is missing the %s scope(s). The audit would report the "
                "resulting failures as real findings, so it will not run.\n"
                "Re-authenticate with `slack auth login`." % ", ".join(missing))
        return have


# --------------------------------------------------------------------------
# Collectors — each returns plain dicts/lists, ready to cache as JSON.
# --------------------------------------------------------------------------

def channels(api):
    """Every conversation the token can see, archived ones included.

    Private channels are limited to those the token owner belongs to; see the
    module docstring. `updated_ms` is returned for completeness but must not be
    read as activity.

    Field policy, deliberately three-tier: `id`/`name`/`is_private` are indexed
    (a Slack response omitting them is a broken assumption worth a KeyError);
    flags default to False; and `num_members`/`created` stay **None when the API
    does not report them** — "not reported" is not "zero", and a consumer that
    conflates them publishes an unknown-size channel as empty.
    """
    out = []
    for c in api.paged("conversations.list", "channels",
                       types="public_channel,private_channel",
                       exclude_archived="false", limit=1000):
        topic = c.get("topic") or {}
        purpose = c.get("purpose") or {}
        out.append({
            "id": c["id"], "name": c["name"],
            "is_private": c["is_private"],
            "is_archived": c.get("is_archived", False),
            "is_general": c.get("is_general", False),
            "is_member": c.get("is_member", False),
            "is_ext_shared": c.get("is_ext_shared", False),
            "num_members": c.get("num_members"),
            "created": c.get("created"), "creator": c.get("creator", ""),
            "updated_ms": c.get("updated"),
            "topic": topic.get("value", ""), "topic_last_set": topic.get("last_set") or 0,
            "purpose": purpose.get("value", ""),
            "purpose_last_set": purpose.get("last_set") or 0,
        })
    return out


#: Fields copied verbatim off a user record. Booleans are defaulted in
#: _user_record so a consumer can treat them as booleans.
USER_FLAGS = ("deleted", "is_bot", "is_app_user", "is_admin", "is_owner",
              "is_primary_owner", "is_restricted", "is_ultra_restricted")


def _user_record(u):
    profile = u.get("profile") or {}
    # These are the fields Slack omits *when false*, so defaulting is lossless
    # and consumers may treat them as plain booleans.
    record = {f: bool(u.get(f, False)) for f in USER_FLAGS}
    record["id"] = u["id"]
    record["name"] = u.get("name", "")
    # Tri-state on purpose: None means Slack did not report it, which is not
    # the same as "unconfirmed" — the member report counts only explicit False
    # and shows the unknowns separately. `updated` is an optional scalar, not a
    # boolean, and is absent for accounts that never changed a setting.
    record["is_email_confirmed"] = u.get("is_email_confirmed")
    record["updated"] = u.get("updated")
    # has_2fa and tz are deliberately NOT collected. No report renders them, and
    # an unread security flag sitting in a cache file is pure carrying cost
    # against the argument for locking that file down in the first place.
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
    """Map each email to its Slack account, or to a genuine miss.

    Returns ``{email: {"id": ..., "real_name": ...}}`` with ``id: None`` where
    the address matches nobody. A miss is not proof the person is absent — they
    may hold a Slack account under a different address.

    **Blank addresses are skipped entirely** and do not appear in the result, so
    a caller computing a resolution rate must count them separately rather than
    dividing by ``len(result)``.

    Raises rather than returning data when a lookup fails for any reason other
    than `users_not_found`. A `missing_scope` or `invalid_auth` would otherwise
    be recorded as "this person has no Slack account" — turning a broken token
    into the audit's headline finding, stated with total confidence.
    """
    resolved, failures = {}, []
    for i, email in enumerate(sorted(set(e for e in emails if e)), 1):
        payload = api.call("users.lookupByEmail", email=email)
        if payload.get("ok"):
            user = payload["user"]
            profile = user.get("profile") or {}
            resolved[email] = {
                "id": user["id"], "name": user.get("name"),
                "real_name": user.get("real_name") or profile.get("real_name", ""),
                "deleted": user.get("deleted", False)}
        else:
            error = payload.get("error", "unknown")
            resolved[email] = {"id": None, "error": error}
            if error not in BENIGN_LOOKUP_MISSES:
                failures.append(error)
        if progress:
            progress(i)
    if failures:
        raise SlackError(
            "users.lookupByEmail", "lookup_failed",
            "%d of %d lookups failed for API reasons rather than absence (%s). "
            "Refusing to report these people as having no Slack account."
            % (len(failures), len(resolved), ", ".join(sorted(set(failures)))))
    return resolved


def scopes(token=None):
    """OAuth scopes for a token, loading one from disk if not given.

    Prefer `Slack.scopes()` when you already hold a client — this function reads
    the credentials a second time and could therefore answer for a different
    token than the one your client is using.
    """
    return Slack(token=token).scopes()
