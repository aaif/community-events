"""Tests for jsoncache.RunMemo, the per-run memo the Slack and gws clients share."""

import os
import threading

from aaif_events.jsoncache import RunMemo


def test_answers_survive_into_a_new_instance(tmp_path):
    path = str(tmp_path / "m.jsonl")
    RunMemo(path).put("k", {"name": "Ada"})
    assert RunMemo(path).get("k") == {"name": "Ada"}
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_a_miss_is_none(tmp_path):
    assert RunMemo(str(tmp_path / "absent.jsonl")).get("k") is None


def test_concurrent_puts_leave_every_line_readable(tmp_path):
    """sync_crm and sync_about call the clients from a pool of six."""
    path = str(tmp_path / "m.jsonl")
    memo = RunMemo(path)
    threads = [threading.Thread(target=lambda n=n: [memo.put("k%d-%d" % (n, i), "v" * 5000)
                                                    for i in range(50)])
               for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fresh = RunMemo(path)
    assert all(fresh.get("k%d-%d" % (n, i)) == "v" * 5000
               for n in range(6) for i in range(50))


def test_an_unwritable_memo_does_not_fail_the_caller(tmp_path, capsys):
    """The caller already holds a good answer; failing to remember it costs a
    later re-ask, never this step's run."""
    memo = RunMemo(str(tmp_path / "gone" / "m.jsonl"))   # directory absent
    memo.put("k", "v")
    assert memo.get("k") == "v"                          # still served in-process
    assert "could not record" in capsys.readouterr().err
