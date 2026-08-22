import os
import tempfile
import unittest
from unittest import mock

from aaif_events import slides_export as se


class TestGws(unittest.TestCase):
    """_gws's retry contract, mirroring TestCall in test_luma.py: transient
    errors retry, everything else fails fast. time.sleep is patched so the
    backoff doesn't actually happen."""

    def setUp(self):
        p = mock.patch("time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def _result(self, returncode, stdout="", stderr=""):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_retries_transient_then_succeeds(self):
        with mock.patch("subprocess.run",
                        side_effect=[self._result(1, stderr="503 backendError"),
                                     self._result(0, stdout="ok")]) as run:
            self.assertEqual(se._gws(["gws", "x"]), "ok")
            self.assertEqual(run.call_count, 2)

    def test_non_transient_fails_immediately(self):
        with mock.patch("subprocess.run",
                        return_value=self._result(1, stderr="permission denied")) as run:
            with self.assertRaises(RuntimeError) as cm:
                se._gws(["gws", "drive", "files", "get"])
            self.assertEqual(run.call_count, 1)
            self.assertIn("gws drive files get", str(cm.exception))

    def test_retries_exhausted_raises_with_cmd_context(self):
        with mock.patch("subprocess.run",
                        return_value=self._result(1, stderr="500 internalError")) as run:
            with self.assertRaises(RuntimeError) as cm:
                se._gws(["gws", "slides", "presentations", "get"], retries=2)
            self.assertEqual(run.call_count, 2)
            self.assertIn("slides presentations get", str(cm.exception))


    def test_failure_output_is_redacted_and_bounded(self):
        noise = "x" * 1000 + ' {"access_token": "ya29.leak-me"} tail'
        with mock.patch("subprocess.run", return_value=self._result(1, stderr=noise)):
            with self.assertRaises(RuntimeError) as cm:
                se._gws(["gws", "x"])
        text = str(cm.exception)
        self.assertNotIn("ya29.leak-me", text)
        self.assertLess(len(text), 700)

    def test_gws_runs_with_the_secrets_scrubbed(self):
        with mock.patch.dict("os.environ", {"AAIF_SLACK_WRITE_TOKEN": "xoxb-w", "KEEP": "1"}), \
                mock.patch("subprocess.run", return_value=self._result(0, stdout="ok")) as run:
            se._gws(["gws", "x"])
        env = run.call_args.kwargs["env"]
        self.assertNotIn("AAIF_SLACK_WRITE_TOKEN", env)
        self.assertEqual(env["KEEP"], "1")


class TestGwsJson(unittest.TestCase):
    def test_empty_output_raises(self):
        with mock.patch.object(se, "_gws", return_value="   \n  "):
            with self.assertRaises(RuntimeError) as cm:
                se._gws_json("drive", "files", "copy")
            self.assertIn("no JSON output", str(cm.exception))

    def test_non_json_output_raises(self):
        with mock.patch.object(se, "_gws", return_value="<html>oops</html>"):
            with self.assertRaises(RuntimeError) as cm:
                se._gws_json("drive", "files", "copy")
            self.assertIn("non-JSON output", str(cm.exception))

    def test_strips_keyring_backend_noise_line(self):
        with mock.patch.object(se, "_gws", return_value='Using keyring backend: keyring\n{"id": "abc"}'):
            self.assertEqual(se._gws_json("drive", "files", "copy"), {"id": "abc"})

    def test_params_and_body_become_cli_flags(self):
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="{}", stderr="")) as run:
            se._gws_json("drive", "files", "copy", params={"fileId": "f1"}, body={"name": "n"})
            cmd = run.call_args[0][0]
            self.assertIn("--params", cmd)
            self.assertIn("--json", cmd)


class TestRenderSlidePng(unittest.TestCase):
    """render_slide_png's orchestration, with _gws_json faked per gws subcommand
    so no real network/CLI call happens."""

    def _fake_gws_json(self, responses):
        def fn(*args, **kwargs):
            key = args
            if key not in responses:
                raise AssertionError("unexpected gws call: %r" % (args,))
            result = responses[key]
            if isinstance(result, Exception):
                raise result
            return result
        return fn

    def test_cleanup_runs_even_when_body_raises(self):
        responses = {
            ("drive", "files", "copy"): {"id": "pres1"},
            ("slides", "presentations", "get"): {"slides": [{"objectId": "p1"}]},
            ("slides", "presentations", "pages", "getThumbnail"): {"contentUrl": "https://example.invalid/x.png"},
            ("drive", "files", "update"): {"id": "pres1"},
        }
        trash_calls = []
        def fake(*args, **kwargs):
            if args == ("drive", "files", "update"):
                trash_calls.append(kwargs.get("params", {}).get("fileId"))
            return self._fake_gws_json(responses)(*args, **kwargs)

        with mock.patch.object(se, "_gws_json", side_effect=fake), \
                mock.patch.object(se, "_download",
                                  side_effect=RuntimeError("rendered thumbnail suspiciously small")):
            with self.assertRaises(RuntimeError) as cm:
                se.render_slide_png("file1", "/tmp/out.png")
            self.assertIn("suspiciously small", str(cm.exception))

        # cleanup (trash) must still have run against the copy it made, even
        # though the function body raised.
        self.assertEqual(trash_calls, ["pres1"])

    def test_no_cleanup_attempted_when_copy_itself_fails(self):
        with mock.patch.object(se, "_gws_json", side_effect=RuntimeError("copy failed")) as gj:
            with self.assertRaises(RuntimeError):
                se.render_slide_png("file1", "/tmp/out.png")
            # only the failed copy call - no trash call, since there is no
            # presentation_id to trash.
            self.assertEqual(gj.call_count, 1)

    def test_cleanup_failure_does_not_mask_original_exception(self):
        responses = {
            ("drive", "files", "copy"): {"id": "pres1"},
            ("slides", "presentations", "get"): RuntimeError("get failed"),
            ("drive", "files", "update"): RuntimeError("trash also failed"),
        }
        with mock.patch.object(se, "_gws_json", side_effect=self._fake_gws_json(responses)):
            with self.assertRaises(RuntimeError) as cm:
                se.render_slide_png("file1", "/tmp/out.png")
            # the ORIGINAL failure propagates, not the cleanup failure
            self.assertIn("get failed", str(cm.exception))

    def test_successful_render_returns_out_path_and_trashes_copy(self):
        responses = {
            ("drive", "files", "copy"): {"id": "pres1"},
            ("slides", "presentations", "get"): {"slides": [{"objectId": "p1"}, {"objectId": "p2"}]},
            ("slides", "presentations", "pages", "getThumbnail"): {"contentUrl": "https://example.invalid/x.png"},
            ("drive", "files", "update"): {"id": "pres1"},
        }
        with mock.patch.object(se, "_gws_json", side_effect=self._fake_gws_json(responses)), \
                mock.patch.object(se, "_download"):
            out = se.render_slide_png("file1", "/tmp/out.png", slide_index=1)
            self.assertEqual(out, "/tmp/out.png")


class TestDownload(unittest.TestCase):
    """_download must never leave a partial PNG behind, whatever failed."""

    def _resp(self, data):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = data
        return r

    def test_success_writes_the_file_with_a_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with mock.patch("urllib.request.urlopen",
                            return_value=self._resp(b"p" * 2000)) as uo:
                self.assertIsNone(se._download("https://example.invalid/x.png", out))
            self.assertEqual(os.path.getsize(out), 2000)
            self.assertEqual(uo.call_args.kwargs.get("timeout"), 30)

    def test_a_too_small_render_is_deleted_not_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with mock.patch("urllib.request.urlopen", return_value=self._resp(b"tiny")):
                with self.assertRaises(RuntimeError) as cm:
                    se._download("https://example.invalid/x.png", out)
            self.assertIn("suspiciously small", str(cm.exception))
            self.assertFalse(os.path.exists(out))

    def test_a_read_failure_deletes_the_partial_file(self):
        r = self._resp(b"")
        r.read.side_effect = TimeoutError("timed out")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with mock.patch("urllib.request.urlopen", return_value=r):
                with self.assertRaises(TimeoutError):
                    se._download("https://example.invalid/x.png", out)
            self.assertFalse(os.path.exists(out))

    def test_an_early_network_failure_leaves_a_preexisting_file_alone(self):
        """A re-render whose fetch dies before any byte lands must not delete
        the good PNG a previous call produced."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with open(out, "wb") as f:
                f.write(b"g" * 2000)
            with mock.patch("urllib.request.urlopen",
                            side_effect=OSError("connection refused")):
                with self.assertRaises(OSError):
                    se._download("https://example.invalid/x.png", out)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), b"g" * 2000)

    def test_a_too_small_rerender_leaves_the_previous_good_file_alone(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with open(out, "wb") as f:
                f.write(b"g" * 2000)
            with mock.patch("urllib.request.urlopen", return_value=self._resp(b"tiny")):
                with self.assertRaises(RuntimeError):
                    se._download("https://example.invalid/x.png", out)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), b"g" * 2000)
            self.assertEqual(os.listdir(d), ["x.png"])   # no .partial left


if __name__ == "__main__":
    unittest.main()
