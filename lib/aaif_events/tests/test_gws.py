"""Tests for the shared `gws` subprocess client.

No real subprocess runs here: `subprocess.run` is replaced, so these pin the
decisions the wrapper makes around the call — what counts as retryable, what
the environment carries, and how output is split before parsing.
"""

import json

import pytest

from aaif_events import gws


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def calls(monkeypatch):
    """Record every subprocess invocation and serve queued results."""
    class Recorder(list):
        queue = None

    recorded = Recorder()
    recorded.queue = []

    def fake_run(cmd, **kw):
        recorded.append({"cmd": cmd, **kw})
        return recorded.queue.pop(0) if recorded.queue else FakeProc(stdout="{}")

    monkeypatch.setattr(gws.subprocess, "run", fake_run)
    monkeypatch.setattr(gws.time, "sleep", lambda s: None)
    return recorded


class TestTransient:
    def test_the_sick_api_words_retry(self):
        for m in ("timed out", "internalError", "Internal error", "backendError",
                  "rateLimit", "userRateLimit", "Connection reset",
                  "Connection refused", "Connection aborted", "temporarily",
                  "HTTP request failed"):
            assert gws.transient(m), m

    def test_internal_error_retries_in_both_spellings(self):
        """The drift between the two old copies: one had this, one did not."""
        assert gws.transient("internalError")
        assert gws.transient("Internal error occurred")

    def test_standalone_http_statuses_retry(self):
        for m in ("HTTP 429", "got 500 back", "503 Service Unavailable"):
            assert gws.transient(m), m

    def test_digits_inside_a_range_do_not_retry(self):
        """`A500:K500 exceeds grid limits` is permanent, not a 500."""
        assert not gws.transient("A500:K500 exceeds grid limits")
        assert not gws.transient("quota id 5000 exceeded permanently")

    def test_a_permission_error_is_final(self):
        assert not gws.transient("The caller does not have permission")


class TestRun:
    def test_success_returns_stdout_without_retrying(self, calls):
        calls.queue.append(FakeProc(stdout="hello"))
        assert gws.run(["gws", "x"]) == "hello"
        assert len(calls) == 1

    def test_a_transient_failure_is_retried_then_succeeds(self, calls):
        calls.queue += [FakeProc(1, stderr="503 Service Unavailable"),
                        FakeProc(0, stdout="ok")]
        assert gws.run(["gws", "x"]) == "ok"
        assert len(calls) == 2

    def test_a_permanent_failure_raises_at_once(self, calls):
        calls.queue.append(FakeProc(1, stderr="does not have permission"))
        with pytest.raises(gws.GwsError, match="permission"):
            gws.run(["gws", "x"])
        assert len(calls) == 1

    def test_retries_are_bounded(self, calls):
        calls.queue += [FakeProc(1, stderr="timed out")] * 5
        with pytest.raises(gws.GwsError):
            gws.run(["gws", "x"], retries=5)
        assert len(calls) == 5

    def test_retries_zero_still_calls_once_and_raises(self, calls):
        """`retries=0` must not silently return None, as an early copy could."""
        calls.queue.append(FakeProc(1, stderr="timed out"))
        with pytest.raises(gws.GwsError):
            gws.run(["gws", "x"], retries=0)
        assert len(calls) == 1

    def test_a_retry_announces_itself_on_stderr(self, calls, capsys):
        """A silent 30-second backoff is indistinguishable from a hang."""
        calls.queue += [FakeProc(1, stderr="timed out"), FakeProc(0, stdout="ok")]
        gws.run(["gws", "x"])
        assert "retrying" in capsys.readouterr().err

    def test_the_subprocess_never_inherits_a_token(self, calls, monkeypatch):
        monkeypatch.setenv("AAIF_SLACK_WRITE_TOKEN", "xoxb-secret")
        monkeypatch.setenv("LUMA_API_KEY", "luma-secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        gws.run(["gws", "x"])
        env = calls[0]["env"]
        assert "AAIF_SLACK_WRITE_TOKEN" not in env
        assert "LUMA_API_KEY" not in env
        assert env["PATH"] == "/usr/bin"

    def test_gws_keeps_its_own_configuration(self, calls, monkeypatch):
        """`gws` reads its client id and token paths from the environment."""
        monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_CLIENT_ID", "id")
        gws.run(["gws", "x"])
        assert calls[0]["env"]["GOOGLE_WORKSPACE_CLI_CLIENT_ID"] == "id"

    def test_cwd_is_passed_through(self, calls):
        gws.run(["gws", "x"], cwd="/tmp")
        assert calls[0]["cwd"] == "/tmp"


class TestCleanStdout:
    def test_the_keyring_notice_is_dropped(self):
        assert gws.clean_stdout("keyring backend nonsense\n{}") == "{}"

    def test_a_line_separator_inside_a_value_survives(self):
        """`splitlines()` would split on U+2028 and corrupt the JSON."""
        raw = '{"v": "line one line two"}'
        assert gws.clean_stdout(raw) == raw
        assert json.loads(gws.clean_stdout(raw))["v"] == "line one line two"


class TestJsonOut:
    def test_params_and_body_are_json_encoded_flags(self, calls):
        calls.queue.append(FakeProc(stdout='{"ok": true}'))
        assert gws.json_out("drive", "files", "get", params={"fileId": "f"},
                            body={"name": "n"}) == {"ok": True}
        cmd = calls[0]["cmd"]
        assert cmd[:4] == ["gws", "drive", "files", "get"]
        assert json.loads(cmd[cmd.index("--params") + 1]) == {"fileId": "f"}
        assert json.loads(cmd[cmd.index("--json") + 1]) == {"name": "n"}

    def test_empty_output_is_an_error_not_an_empty_dict(self, calls):
        calls.queue.append(FakeProc(stdout="   "))
        with pytest.raises(gws.GwsError, match="no JSON"):
            gws.json_out("sheets", "get")

    def test_non_json_output_names_what_was_being_read(self, calls):
        calls.queue.append(FakeProc(stdout="<html>login</html>"))
        with pytest.raises(gws.GwsError, match="my-sheet"):
            gws.json_out("sheets", "get", what="my-sheet")

    def test_a_non_json_body_is_not_dumped_into_the_error(self, calls):
        """The body is sheet data. A run with --redact on must not print raw
        intake rows because one read came back wrong."""
        calls.queue.append(FakeProc(
            stdout='rows: Ada Lovelace, ada@x.com, Boston, '
                   '"my friend told me about this"' * 5))
        with pytest.raises(gws.GwsError) as exc:
            gws.json_out("sheets", "get", what="Intake")
        assert "Lovelace" not in str(exc.value)
        assert "ada@x.com" not in str(exc.value)
        assert "chars" in str(exc.value)          # the shape still reaches the reader
        assert "Boston" not in str(exc.value)


class TestSecretsNeverReachTheReader:
    def test_a_credential_in_stderr_is_scrubbed_from_the_exception(self, calls):
        calls.queue.append(FakeProc(
            1, stderr='GOOGLE_WORKSPACE_CLI_CLIENT_SECRET=abc123notarealsecret'))
        with pytest.raises(gws.GwsError) as exc:
            gws.run(["gws", "x"])
        assert "abc123notarealsecret" not in str(exc.value)
        assert "<redacted>" in str(exc.value)

    def test_a_refresh_token_in_a_retry_banner_is_scrubbed(self, calls, capsys):
        calls.queue += [FakeProc(1, stderr='timed out "refresh_token": '
                                           '"1//0aaaaaaaaaaaaaaaaaaaaaaaaaa"'),
                        FakeProc(0, stdout="ok")]
        gws.run(["gws", "x"])
        assert "1//0aaaaaaaaaaaaaaaaaaaaaaaaaa" not in capsys.readouterr().err


class TestValues:
    def test_rows_come_back_from_the_first_range(self, calls):
        calls.queue.append(FakeProc(
            stdout=json.dumps({"valueRanges": [{"values": [["a", "b"], ["c"]]}]})))
        assert gws.values("sheet", "Tab!A:Z") == [["a", "b"], ["c"]]

    def test_an_empty_range_is_no_rows_not_an_error(self, calls):
        calls.queue.append(FakeProc(stdout=json.dumps({"valueRanges": [{}]})))
        assert gws.values("sheet", "Tab!A:Z") == []

    def test_a_missing_valueranges_key_raises_rather_than_reading_as_empty(self, calls):
        """The one that matters. A caller acts on "no rows": the keep-list
        reader treats it as "this chapter keeps nobody" and reports every
        keep-listed organizer for removal. A failed read must not be able to
        produce that answer."""
        calls.queue.append(FakeProc(stdout="{}"))
        with pytest.raises(gws.GwsError, match="valueRanges"):
            gws.values("sheet", "Tab!A:Z")

    def test_an_error_envelope_raises_too(self, calls):
        calls.queue.append(FakeProc(stdout=json.dumps({"error": {"code": 403}})))
        with pytest.raises(gws.GwsError, match="valueRanges"):
            gws.values("sheet", "Tab!A:Z")

    def test_an_empty_valueranges_list_is_still_no_rows(self, calls):
        """`valueRanges: []` IS a batchGet response; it just held nothing."""
        calls.queue.append(FakeProc(stdout=json.dumps({"valueRanges": []})))
        assert gws.values("sheet", "Tab!A:Z") == []

    def test_the_error_names_the_sheet_and_range(self, calls):
        calls.queue.append(FakeProc(stdout=""))
        with pytest.raises(gws.GwsError, match="Tab!A:Z"):
            gws.values("sheet", "Tab!A:Z")


class TestFiles:
    def test_download_runs_in_the_files_own_directory(self, calls, monkeypatch,
                                                      tmp_path):
        target = tmp_path / "sub" / "f.docx"

        def fake_run(cmd, **kw):
            calls.append({"cmd": cmd, **kw})
            target.write_bytes(b"PK\x03\x04")
            return FakeProc(stdout="")

        # Through monkeypatch, not a bare assignment: `gws.subprocess` is the
        # global module, so an unrestored write hands every later test in the
        # session a fake `subprocess.run`. That is how this suite briefly broke
        # four tests in report_style, which shells out to git.
        monkeypatch.setattr(gws.subprocess, "run", fake_run)
        assert gws.download("fid", str(target)) == b"PK\x03\x04"
        assert calls[0]["cwd"] == str(tmp_path / "sub")
        assert "f.docx" in calls[0]["cmd"]          # a basename, never a full path

    def test_upload_stages_the_bytes_then_sends_them(self, calls, tmp_path):
        target = tmp_path / "f.xlsx"
        gws.upload("fid", str(target), b"bytes", "application/x")
        assert target.read_bytes() == b"bytes"
        cmd = calls[0]["cmd"]
        assert cmd[cmd.index("--upload") + 1] == "f.xlsx"
        assert cmd[cmd.index("--upload-content-type") + 1] == "application/x"
