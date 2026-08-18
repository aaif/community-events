"""Tests for the read-only Slack audit client."""

import json
import urllib.error

import pytest

from aaif_events import slack


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def client(monkeypatch, responses, sleeps=None):
    """A Slack client whose urlopen replays `responses` (payload or exception)."""
    calls = []
    queue = list(responses)

    def fake_urlopen(request, *a, **kw):
        calls.append(request.full_url.rsplit("/", 1)[-1])
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return FakeResponse(nxt)

    monkeypatch.setattr(slack.urllib.request, "urlopen", fake_urlopen)
    api = slack.Slack(token="xoxp-test", sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return api, calls


def test_refuses_any_method_outside_the_allowlist(monkeypatch):
    api, _ = client(monkeypatch, [])
    with pytest.raises(ValueError, match="read-only"):
        api.call("chat.postMessage", channel="C1", text="hi")
    with pytest.raises(ValueError):
        api.call("conversations.archive", channel="C1")


def test_call_returns_payload(monkeypatch):
    api, calls = client(monkeypatch, [{"ok": True, "team": "T"}])
    assert api.call("auth.test")["team"] == "T"
    assert calls == ["auth.test"]


def test_ok_raises_on_error_payload(monkeypatch):
    api, _ = client(monkeypatch, [{"ok": False, "error": "missing_scope"}])
    with pytest.raises(slack.SlackError) as exc:
        api.ok("conversations.list")
    assert exc.value.error == "missing_scope"


def test_retries_rate_limit_then_succeeds(monkeypatch):
    sleeps = []
    api, calls = client(
        monkeypatch,
        [{"ok": False, "error": "ratelimited", "retry_after": 3}, {"ok": True, "members": []}],
        sleeps)
    assert api.ok("conversations.members", channel="C1")["ok"] is True
    assert sleeps == [3]
    assert len(calls) == 2


def test_retries_truncated_read(monkeypatch):
    """Slack truncates very large pages; the same cursor must be retried."""
    import http.client
    sleeps = []
    api, calls = client(
        monkeypatch,
        [http.client.IncompleteRead(b"partial"), {"ok": True, "members": ["U1"]}],
        sleeps)
    assert api.ok("users.list")["members"] == ["U1"]
    assert len(calls) == 2 and sleeps


def test_http_error_other_than_429_becomes_a_slackerror(monkeypatch):
    """One exception vocabulary out of call(): `except SlackError` must catch
    a 5xx and a final 429, not just this module's own failures."""
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    api, _ = client(monkeypatch, [err])
    with pytest.raises(slack.SlackError) as exc:
        api.call("auth.test")
    assert exc.value.error == "http_500"


def test_rate_limit_does_not_sleep_on_the_attempt_it_gives_up_on(monkeypatch):
    sleeps = []
    api, _ = client(monkeypatch,
                    [{"ok": False, "error": "ratelimited", "retry_after": 7}] * slack.MAX_ATTEMPTS,
                    sleeps)
    with pytest.raises(slack.SlackError) as exc:
        api.call("auth.test")
    assert exc.value.error == "retry_exhausted"
    # One sleep fewer than attempts: the last failure aborts instead of waiting.
    assert len(sleeps) == slack.MAX_ATTEMPTS - 1


def test_connection_reset_mid_pull_is_retried(monkeypatch):
    """A reset during the 20-minute users.list pull must not lose every page."""
    api, calls = client(monkeypatch,
                        [ConnectionResetError("reset"), {"ok": True, "members": ["U1"]}],
                        [])
    assert api.ok("users.list")["members"] == ["U1"]
    assert len(calls) == 2


def test_scopes_reports_a_dead_token_as_a_token_problem(monkeypatch):
    """Not as 'missing scopes', which sends people hunting the app config."""
    api, _ = client(monkeypatch, [{"ok": False, "error": "invalid_auth"}])
    with pytest.raises(slack.SlackError) as exc:
        api.scopes()
    assert exc.value.error == "invalid_auth"
    assert "slack auth login" in str(exc.value)


def test_paged_follows_cursor_and_stops(monkeypatch):
    api, calls = client(monkeypatch, [
        {"ok": True, "channels": [{"id": "C1"}], "response_metadata": {"next_cursor": "abc"}},
        {"ok": True, "channels": [{"id": "C2"}], "response_metadata": {"next_cursor": ""}},
    ])
    assert [c["id"] for c in api.paged("conversations.list", "channels")] == ["C1", "C2"]
    assert len(calls) == 2


def test_channels_flattens_topic_and_purpose(monkeypatch):
    api, _ = client(monkeypatch, [{"ok": True, "channels": [{
        "id": "C1", "name": "boston", "is_private": False, "num_members": 188,
        "created": 1600000000, "updated": 1749642761491,
        "topic": {"value": "t", "last_set": 111},
        "purpose": {"value": "", "last_set": 0}}]}])
    (row,) = slack.channels(api)
    assert row["topic"] == "t" and row["topic_last_set"] == 111
    assert row["purpose"] == "" and row["purpose_last_set"] == 0
    # updated is carried but is a migration stamp, never an activity signal
    assert row["updated_ms"] == 1749642761491


def test_channels_keeps_unreported_size_as_none(monkeypatch):
    """'Not reported' must not become 0 — that publishes an unknown as empty."""
    api, _ = client(monkeypatch, [{"ok": True, "channels": [
        {"id": "C1", "name": "bare", "is_private": True}]}])
    (row,) = slack.channels(api)
    assert row["num_members"] is None
    assert row["is_archived"] is False and row["topic_last_set"] == 0


def test_paged_refuses_a_page_missing_its_collection_key(monkeypatch):
    """Otherwise a malformed page ends the stream and a short pull looks complete."""
    api, _ = client(monkeypatch, [{"ok": True}])
    with pytest.raises(slack.SlackError) as exc:
        list(api.paged("users.list", "members"))
    assert exc.value.error == "malformed_page"


def test_lookup_emails_raises_when_the_api_fails_rather_than_the_person_missing(monkeypatch):
    """missing_scope must not be recorded as 'this person has no Slack account'."""
    api, _ = client(monkeypatch, [{"ok": False, "error": "missing_scope"}])
    with pytest.raises(slack.SlackError) as exc:
        slack.lookup_emails(api, ["a@x.com"])
    assert exc.value.error == "lookup_failed"
    assert "missing_scope" in str(exc.value)


def test_require_scopes_aborts_on_a_missing_scope(monkeypatch):
    api, _ = client(monkeypatch, [])
    monkeypatch.setattr(slack.Slack, "scopes", lambda self: {"channels:read"})
    api.require_scopes("channels:read")            # present — no raise
    with pytest.raises(SystemExit) as exc:
        api.require_scopes("channels:read", "users:read.email")
    assert "users:read.email" in str(exc.value)


def test_user_record_defaults_flags_to_booleans(monkeypatch):
    """Consumers treat these as bools; a None would read as False by accident."""
    api, _ = client(monkeypatch, [{"ok": True, "members": [
        {"id": "U1", "profile": {}}]}])
    (row,) = slack.users(api)
    assert row["deleted"] is False and row["is_bot"] is False
    assert row["is_restricted"] is False
    # Tri-state fields stay None: unknown is not the same as False.
    assert row["is_email_confirmed"] is None


def test_users_extracts_profile_fields(monkeypatch):
    api, _ = client(monkeypatch, [{"ok": True, "members": [{
        "id": "U1", "name": "rahul", "deleted": False, "is_bot": False,
        "profile": {"email": "R@Example.COM", "real_name": "Rahul",
                    "image_original": "https://x/y.png"}}]}])
    (row,) = slack.users(api)
    assert row["email"] == "r@example.com"   # lowercased for joining
    assert row["real_name"] == "Rahul" and row["has_avatar"] is True


def test_lookup_emails_records_misses_without_raising(monkeypatch):
    api, _ = client(monkeypatch, [
        {"ok": True, "user": {"id": "U1", "real_name": "A", "profile": {}}},
        {"ok": False, "error": "users_not_found"},
    ])
    out = slack.lookup_emails(api, ["a@x.com", "b@x.com"])
    assert out["a@x.com"]["id"] == "U1"
    assert out["b@x.com"]["id"] is None and out["b@x.com"]["error"] == "users_not_found"


def test_lookup_emails_dedupes_and_skips_blanks(monkeypatch):
    api, calls = client(monkeypatch, [{"ok": True, "user": {"id": "U1", "profile": {}}}])
    slack.lookup_emails(api, ["a@x.com", "a@x.com", ""])
    assert len(calls) == 1


@pytest.mark.parametrize("blob,expected", [
    ({"a": {"token": "xoxp-1"}}, "xoxp-1"),
    ({"list": [{"t": "xoxb-2"}]}, "xoxb-2"),
    ({"nested": {"deep": {"k": "xoxe.xoxp-3"}}}, "xoxe.xoxp-3"),
    ({"a": "not-a-token"}, None),
])
def test_find_token_walks_arbitrary_shapes(blob, expected):
    assert slack._find_token(blob) == expected


# --- history_activity: the classification every activity verdict rests on -----
class _HistoryApi:
    """A fake api whose paged() replays canned history messages."""

    def __init__(self, messages):
        self._messages = messages

    def paged(self, method, key, **params):
        assert method == "conversations.history"
        yield from self._messages


def _msg(ts, user=None, subtype=None, bot_id=None):
    m = {"ts": str(ts)}
    if user:
        m["user"] = user
    if subtype:
        m["subtype"] = subtype
    if bot_id:
        m["bot_id"] = bot_id
    return m


def test_history_activity_classifies_humans_bots_and_joins():
    api = _HistoryApi([
        _msg(100, user="U1"),                       # human
        _msg(90, user="U2", subtype="thread_broadcast"),  # human
        _msg(80, user="U1", bot_id="B1"),           # bot even with "user"
        _msg(70, subtype="bot_message", bot_id="B1"),
        _msg(60, user="U3", subtype="channel_join"),
        _msg(50, user="U3", subtype="channel_topic"),  # plumbing: no bucket
    ])
    out = slack.history_activity(api, "C1", oldest=10, include_posters=True)
    assert (out["human_msgs"], out["bot_msgs"], out["joins"]) == (2, 2, 1)
    assert out["posters"] == 2
    assert out["poster_ids"] == ["U1", "U2"]
    assert out["last_human_ts"] == 100.0
    assert out["window_complete"] is True


def test_history_activity_dead_channel_reports_last_human_beyond_window():
    api = _HistoryApi([
        _msg(30, subtype="bot_message", bot_id="B1"),   # in window
        _msg(5, user="U1"),                             # human, BEFORE oldest
    ])
    out = slack.history_activity(api, "C1", oldest=10)
    assert out["human_msgs"] == 0                       # window is truthful
    assert out["last_human_ts"] == 5.0                  # but the answer exists
    assert out["last_human_unknown"] is False
    assert out["window_complete"] is True


def test_history_activity_truncation_is_a_floor_not_a_measurement():
    api = _HistoryApi([_msg(100 - i, user="U1") for i in range(10)])
    out = slack.history_activity(api, "C1", oldest=1, max_scan=3)
    assert out["scanned"] == 3
    assert out["window_complete"] is False
    assert out["human_msgs"] == 3


def test_history_activity_empty_channel():
    out = slack.history_activity(_HistoryApi([]), "C1", oldest=10)
    assert out["last_ts"] is None
    assert out["last_human_unknown"] is False
    assert "poster_ids" not in out


def test_retry_secs_never_raises():
    assert [slack._retry_secs(v) for v in
            ("Fri, 21 Aug 2026 07:28:00 GMT", None, "", "2.5", "30", -3)] \
        == [5, 5, 5, 2, 30, 1]
