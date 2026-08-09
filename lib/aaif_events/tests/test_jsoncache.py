"""Tests for the atomic, stamped JSON cache.

This module is the persistence layer under both audit engines, and its whole
job is to answer one question honestly: is this cached payload safe to publish?
`read()` returning a payload means "trust this and put it in front of community
leadership". Every guard below exists to stop a corrupt, foreign or stale file
becoming that answer.
"""

import datetime as dt
import json
import os

import pytest

from aaif_events import jsoncache


def test_roundtrip(tmp_path):
    p = str(tmp_path / "c.json")
    jsoncache.write(p, {"a": [1, 2]})
    assert jsoncache.read(p) == {"a": [1, 2]}


def test_absent_and_refresh_are_misses(tmp_path):
    p = str(tmp_path / "c.json")
    assert jsoncache.read(p) is None
    jsoncache.write(p, [1])
    assert jsoncache.read(p, refresh=True) is None


def test_empty_payload_is_data_not_a_miss(tmp_path):
    """`read(...) or fetch()` would re-fetch these every run — hence `is None`."""
    for payload in ([], {}, 0):
        p = str(tmp_path / "c.json")
        jsoncache.write(p, payload)
        assert jsoncache.read(p) is not None
        assert jsoncache.read(p) == payload


def test_file_is_never_world_readable(tmp_path):
    """It holds the member directory — names, emails, has_2fa, is_admin."""
    p = str(tmp_path / "c.json")
    jsoncache.write(p, {"members": "sensitive"})
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_the_temp_file_is_never_world_readable_either(tmp_path, monkeypatch):
    """A run killed mid-write must not leave a readable fragment behind."""
    seen = {}
    real = os.replace

    def spy(src, dst):
        seen["mode"] = oct(os.stat(src).st_mode & 0o777)
        return real(src, dst)

    monkeypatch.setattr(jsoncache.os, "replace", spy)
    jsoncache.write(str(tmp_path / "c.json"), {"a": 1})
    assert seen["mode"] == "0o600"


def test_a_failed_write_leaves_the_previous_payload_intact(tmp_path, monkeypatch):
    """Atomicity: never a half-written cache at the real path."""
    p = str(tmp_path / "c.json")
    jsoncache.write(p, {"good": True})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(jsoncache.os, "replace", boom)
    with pytest.raises(OSError):
        jsoncache.write(p, {"bad": True})
    assert jsoncache.read(p) == {"good": True}
    assert [f for f in os.listdir(tmp_path) if "partial" in f] == []


def test_corrupt_file_raises_with_the_remedy(tmp_path):
    """Silently re-fetching would hide a bug; the message must name the fix."""
    p = tmp_path / "c.json"
    p.write_text('{"format": 2, "payload": [1, 2', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        jsoncache.read(str(p))
    assert "--refresh" in str(exc.value)


@pytest.mark.parametrize("body", ['[1, 2, 3]', '"a string"', '{"format": 1, "payload": 1}'])
def test_foreign_or_old_shapes_are_misses(tmp_path, body):
    p = tmp_path / "c.json"
    p.write_text(body, encoding="utf-8")
    assert jsoncache.read(str(p)) is None


def test_a_missing_payload_key_is_a_miss_not_none_data(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"format": jsoncache.FORMAT, "written_utc": "x"}), encoding="utf-8")
    assert jsoncache.read(str(p)) is None


def test_a_cache_from_another_workspace_is_refused(tmp_path):
    """Joining one workspace's channels to another's members yields a coherent,
    entirely wrong report — so a tenant mismatch must be a miss."""
    p = str(tmp_path / "c.json")
    jsoncache.write(p, {"chans": 1}, team_id="T_AAIF")
    assert jsoncache.read(p, team_id="T_AAIF") == {"chans": 1}
    assert jsoncache.read(p, team_id="T_OTHER") is None


def test_discarding_a_cache_is_announced(tmp_path):
    """Throwing away a 20-minute pull in silence is what this repo refuses to do."""
    p = tmp_path / "c.json"
    p.write_text('{"format": 0, "payload": []}', encoding="utf-8")
    said = []
    assert jsoncache.read(str(p), note=said.append) is None
    assert said and "discarding" in said[0]


def test_age_renders_and_flags_staleness(tmp_path):
    p = str(tmp_path / "c.json")
    jsoncache.write(p, {})
    now = dt.datetime.now(dt.timezone.utc)
    assert jsoncache.age(p, now) == "fetched today"
    assert jsoncache.age(p, now + dt.timedelta(days=1)) == "fetched yesterday"
    assert "consider --refresh" in jsoncache.age(p, now + dt.timedelta(days=30))
    assert "consider --refresh" not in jsoncache.age(p, now + dt.timedelta(days=3))


@pytest.mark.parametrize("body", ['[1, 2]', '{"written_utc": "not-a-date"}',
                                  '{"written_utc": 5}', '{}'])
def test_age_never_kills_a_run_over_a_progress_line(tmp_path, body):
    p = tmp_path / "c.json"
    p.write_text(body, encoding="utf-8")
    assert jsoncache.age(str(p)) == "undated"


def test_age_tolerates_a_naive_timestamp(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"format": jsoncache.FORMAT, "payload": [],
                             "written_utc": "2026-08-01T00:00:00"}), encoding="utf-8")
    assert "fetched" in jsoncache.age(str(p), dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc))


def test_age_of_an_absent_file(tmp_path):
    assert jsoncache.age(str(tmp_path / "nope.json")) == "absent"
