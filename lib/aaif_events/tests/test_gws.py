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

    def test_no_retry_sends_a_transient_failure_exactly_once(self, calls):
        """The guard callers pass for a non-idempotent write.

        `timed out` is the case that matters: the request may already have
        landed, so the one thing this must not do is send it again.
        """
        calls.queue += [FakeProc(1, stderr="timed out"), FakeProc(0, stdout="ok")]
        with pytest.raises(gws.GwsError):
            gws.run(["gws", "x"], retries=gws.NO_RETRY)
        assert len(calls) == 1

    def test_the_verb_is_named_and_the_argv_blobs_are_not(self, calls):
        """"gws failed (1)" out of a script issuing a dozen calls says nothing.

        The verb is safe to print; the rest of the argv is `--params`/`--json`
        holding sheet rows and form answers.
        """
        calls.queue.append(FakeProc(1, stderr="does not have permission"))
        with pytest.raises(gws.GwsError) as exc:
            gws.run(["gws", "sheets", "spreadsheets", "values", "get",
                     "--params", '{"range": "Ada Lovelace"}'])
        assert "sheets spreadsheets values get" in str(exc.value)
        assert "Ada Lovelace" not in str(exc.value)

    def test_a_streamed_response_body_is_not_shown_on_failure(self, calls):
        """Some gws builds print the error on stdout, so stdout decides the
        retry. It must not decide what the operator SEES: a `values.get` that
        streams half its rows and then exits nonzero puts those rows here."""
        calls.queue.append(FakeProc(1, stdout='{"values": [["Ada", "a@x.com"]]}'))
        with pytest.raises(gws.GwsError) as exc:
            gws.run(["gws", "sheets", "spreadsheets", "values", "get"])
        assert "a@x.com" not in str(exc.value)
        assert "Ada" not in str(exc.value)
        assert "on stdout" in str(exc.value)   # says there WAS output

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


class TestJsonOutHonoursTheRetryBudget:
    """`json_out` is where three scripts' `retries=NO_RETRY` actually has to
    land. Asserting the keyword at the call site cannot see this: dropping the
    `retries=retries` pass-through here leaves the guard inert everywhere while
    every call-site test still passes. Verified — that mutation survived the
    whole suite before this class existed."""

    def test_no_retry_reaches_the_subprocess_as_one_attempt(self, calls):
        calls.queue += [FakeProc(1, stderr="timed out"), FakeProc(0, stdout="{}")]
        with pytest.raises(gws.GwsError):
            gws.json_out("drive", "files", "create", retries=gws.NO_RETRY)
        assert len(calls) == 1

    def test_the_default_still_retries(self, calls):
        calls.queue += [FakeProc(1, stderr="timed out"), FakeProc(0, stdout="{}")]
        assert gws.json_out("drive", "files", "list") == {}
        assert len(calls) == 2

    def test_values_passes_its_budget_down_too(self, calls):
        calls.queue += [FakeProc(1, stderr="timed out"), FakeProc(0, stdout="{}")]
        with pytest.raises(gws.GwsError):
            gws.values("sheet1", "A1:B2", retries=gws.NO_RETRY)
        assert len(calls) == 1


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


class TestReadMemo:
    """The sync runner's per-run memo of Google reads (report runs only)."""

    GET = ["gws", "sheets", "spreadsheets", "values", "batchGet",
           "--params", json.dumps({"spreadsheetId": "S", "ranges": ["A:B"]})]

    def test_off_unless_the_runner_sets_it(self, calls, monkeypatch):
        monkeypatch.delenv(gws.READ_MEMO_ENV, raising=False)
        gws.run(self.GET)
        gws.run(self.GET)
        assert len(calls) == 2

    def test_a_repeat_read_is_answered_from_the_file(self, calls, monkeypatch, tmp_path):
        memo = tmp_path / "gws-memo.jsonl"
        monkeypatch.setenv(gws.READ_MEMO_ENV, str(memo))
        calls.queue = [FakeProc(stdout='{"valueRanges": [{"values": [["Ada"]]}]}')]
        assert gws.values("S", "A:B") == [["Ada"]]
        monkeypatch.setattr(gws, "_memos", {})   # a later step: a new process
        assert gws.values("S", "A:B") == [["Ada"]]
        assert len(calls) == 1
        assert oct(memo.stat().st_mode & 0o777) == "0o600"

    def test_writes_and_downloads_always_go_to_google(self, calls, monkeypatch, tmp_path):
        monkeypatch.setenv(gws.READ_MEMO_ENV, str(tmp_path / "m.jsonl"))
        update = ["gws", "sheets", "spreadsheets", "values", "update", "--params", "{}"]
        media = ["gws", "drive", "files", "get", "--params",
                 json.dumps({"fileId": "F", "alt": "media"})]
        for cmd in (update, update, media, media):
            gws.run(cmd)
        gws.run(["gws", "drive", "files", "get", "--output", "f"], cwd=str(tmp_path))
        gws.run(["gws", "drive", "files", "get", "--output", "f"], cwd=str(tmp_path))
        assert len(calls) == 6

    def test_a_failed_read_is_never_remembered(self, calls, monkeypatch, tmp_path):
        monkeypatch.setenv(gws.READ_MEMO_ENV, str(tmp_path / "m.jsonl"))
        calls.queue = [FakeProc(returncode=1, stderr="permission denied"),
                       FakeProc(stdout="{}")]
        with pytest.raises(gws.GwsError):
            gws.run(self.GET)
        gws.run(self.GET)
        assert len(calls) == 2

    def test_a_torn_last_line_is_a_miss_not_a_crash(self, calls, monkeypatch, tmp_path):
        memo = tmp_path / "m.jsonl"
        monkeypatch.setenv(gws.READ_MEMO_ENV, str(memo))
        gws.run(self.GET)
        with open(memo, "a") as fh:
            fh.write('["half')
        monkeypatch.setattr(gws, "_memos", {})
        gws.run(self.GET)
        assert len(calls) == 1
