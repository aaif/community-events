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


def test_http_error_other_than_429_propagates(monkeypatch):
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    api, _ = client(monkeypatch, [err])
    with pytest.raises(urllib.error.HTTPError):
        api.call("auth.test")


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


def test_channels_defaults_missing_fields(monkeypatch):
    api, _ = client(monkeypatch, [{"ok": True, "channels": [
        {"id": "C1", "name": "bare", "is_private": True}]}])
    (row,) = slack.channels(api)
    assert row["num_members"] == 0 and row["is_archived"] is False
    assert row["topic_last_set"] == 0


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
