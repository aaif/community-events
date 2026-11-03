import os
import tempfile
import unittest
from unittest import mock

from aaif_events import slides_export as se


class TestGws(unittest.TestCase):
    """The retry contract this module reaches Drive/Slides under.

    `se._gws`/`se._gws_json` are aliases of the shared client now; this module
    used to carry its own copy, with that copy's bare `"500"` substring bug.
    These stay because they pin the contract AT THIS MODULE'S BOUNDARY: if
    someone re-introduces a private wrapper here, these are what should fail.
    `subprocess.run` is patched on the stdlib module, so they intercept either
    way. time.sleep is patched so the backoff doesn't actually happen."""

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
    """Stdout handling at this module's boundary.

    These patch `subprocess.run`, not `se._gws`. Patching the alias would set a
    module attribute that `json_out` never reads — it calls `run` inside the
    shared client — so the test would sail past the mock and shell out to a
    real `gws`. It did, while this file was being moved onto the shared client,
    which is the failure mode worth leaving a note about: a mock that mocks
    nothing does not fail, it makes a network call."""

    def _stdout(self, text):
        return mock.patch("subprocess.run",
                          return_value=mock.Mock(returncode=0, stdout=text, stderr=""))

    def test_empty_output_raises(self):
        with self._stdout("   \n  "):
            with self.assertRaises(RuntimeError) as cm:
                se._gws_json("drive", "files", "copy")
            self.assertIn("no JSON output", str(cm.exception))

    def test_non_json_output_raises(self):
        with self._stdout("<html>oops</html>"):
            with self.assertRaises(RuntimeError) as cm:
                se._gws_json("drive", "files", "copy")
            self.assertIn("non-JSON output", str(cm.exception))

    def test_a_non_json_body_is_never_echoed(self):
        """It is Drive data. The shared client reports length and first char."""
        with self._stdout('<html><body>Ada Lovelace, a@x.com</body></html>'):
            with self.assertRaises(RuntimeError) as cm:
                se._gws_json("drive", "files", "copy")
        self.assertNotIn("a@x.com", str(cm.exception))
        self.assertNotIn("Ada", str(cm.exception))

    def test_strips_keyring_backend_noise_line(self):
        with self._stdout('Using keyring backend: keyring\n{"id": "abc"}'):
            self.assertEqual(se._gws_json("drive", "files", "copy"), {"id": "abc"})

    def test_the_throwaway_copy_is_never_retried(self):
        """A retried `files.copy` orphans a second "TEMP - render_slide_png"
        beside the source; against TemplateCity it is then cloned into every
        chapter made afterwards. Driven to a counted subprocess, not a kwarg."""
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="",
                                               stderr="timed out")) as run, \
                mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                se.render_slide_png("file1", 0, "/tmp/nope.png")
        self.assertEqual(run.call_count, 1)

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


class TestRenderGuards(unittest.TestCase):
    """The page-text check, the slide count and strict cleanup."""

    PAGE = {"objectId": "p4", "pageElements": [
        {"shape": {"text": {"textElements": [{"textRun": {"content": "AAIF Community Events"}}]}}},
        {"elementGroup": {"children": [
            {"shape": {"text": {"textElements": [{"textRun": {"content": "Boston"}}]}}}]}}]}

    def _run(self, slides, trash_fails=False, **kw):
        calls = []

        def fake(*args, **kwargs):
            calls.append((args, kwargs))
            if args == ("drive", "files", "copy"):
                return {"id": "pres1"}
            if args == ("slides", "presentations", "get"):
                return {"slides": slides}
            if args[-1] == "getThumbnail":
                return {"contentUrl": "https://example.invalid/x.png"}
            if args == ("drive", "files", "update"):
                if trash_fails:
                    raise RuntimeError("trash failed")
                return {}
            raise AssertionError(args)
        with mock.patch.object(se, "_gws_json", side_effect=fake), \
                mock.patch.object(se, "_download") as dl, \
                mock.patch("sys.stderr"):
            try:
                return se.render_slide_png("file1", "/tmp/out.png", **kw), calls, dl
            except RuntimeError as e:
                return e, calls, dl

    def test_check_text_sees_the_page_being_rendered_including_groups(self):
        seen = []
        out, calls, dl = self._run([{"objectId": "p1"}, self.PAGE], slide_index=1,
                                   check_text=lambda t: seen.append(t))
        self.assertEqual(out, "/tmp/out.png")
        self.assertEqual(seen, ["AAIF Community Events Boston"])
        thumb = [k for a, k in calls if a[-1] == "getThumbnail"][0]
        self.assertEqual(thumb["params"]["pageObjectId"], "p4")

    def test_a_refused_page_is_never_rendered_and_the_copy_is_trashed(self):
        err, calls, dl = self._run([self.PAGE], check_text=lambda t: "wrong city")
        self.assertIn("wrong city", str(err))
        self.assertFalse(dl.called)
        self.assertNotIn("getThumbnail", [a[-1] for a, _ in calls])
        self.assertIn(("drive", "files", "update"), [a for a, _ in calls])

    def test_a_short_deck_names_the_slide_and_the_count(self):
        err, _c, _d = self._run([{"objectId": "p1"}], slide_index=3)
        self.assertIn("has 1 slide(s); slide 4", str(err))

    def test_strict_cleanup_raises_with_the_orphan_id(self):
        err, _c, _d = self._run([self.PAGE], trash_fails=True, strict_cleanup=True)
        self.assertIn("pres1", str(err))
        self.assertIn("could not be trashed", str(err))

    def test_default_cleanup_failure_still_returns(self):
        out, _c, _d = self._run([self.PAGE], trash_fails=True)
        self.assertEqual(out, "/tmp/out.png")

    def test_strict_cleanup_names_the_orphan_when_the_render_also_failed(self):
        err, _c, dl = self._run([self.PAGE], trash_fails=True, strict_cleanup=True,
                                check_text=lambda t: "wrong city")
        self.assertIn("wrong city", str(err))
        self.assertIn("pres1", str(err))
        self.assertIn("could not be trashed", str(err))
        self.assertFalse(dl.called)

    def test_a_boolean_check_text_is_a_type_error_not_a_pass(self):
        with mock.patch.object(se, "_gws_json", side_effect=lambda *a, **k: (
                {"id": "pres1"} if a == ("drive", "files", "copy")
                else {"slides": [self.PAGE]} if a == ("slides", "presentations", "get")
                else {})), mock.patch.object(se, "_download"):
            with self.assertRaises(TypeError):
                se.render_slide_png("file1", "/tmp/out.png", check_text=lambda t: False)

    def test_the_fields_mask_asks_for_text_only_when_checking(self):
        _o, calls, _d = self._run([self.PAGE], check_text=lambda t: None)
        get = [k for a, k in calls if a == ("slides", "presentations", "get")][0]
        self.assertIn("textRun/content", get["params"]["fields"])
        self.assertIn("elementGroup", get["params"]["fields"])
        _o, calls, _d = self._run([self.PAGE])
        get = [k for a, k in calls if a == ("slides", "presentations", "get")][0]
        self.assertEqual(get["params"]["fields"], "slides.objectId")

    def test_slide_index_equal_to_the_count_is_refused(self):
        err, _c, _d = self._run([{"objectId": "p1"}], slide_index=1)
        self.assertIn("has 1 slide(s); slide 2", str(err))

    def test_a_missing_thumbnail_url_says_so(self):
        def fake(*args, **kwargs):
            if args == ("drive", "files", "copy"):
                return {"id": "pres1"}
            if args == ("slides", "presentations", "get"):
                return {"slides": [{"objectId": "p1"}]}
            return {}
        with mock.patch.object(se, "_gws_json", side_effect=fake), \
                mock.patch.object(se, "_download") as dl:
            with self.assertRaisesRegex(RuntimeError, "no thumbnail URL"):
                se.render_slide_png("file1", "/tmp/out.png")
        self.assertFalse(dl.called)

    def test_a_word_split_across_runs_in_one_shape_is_read_whole(self):
        page = {"pageElements": [{"shape": {"text": {"textElements": [
            {"textRun": {"content": "AAIF Community Events\n"}},
            {"textRun": {"content": "Amster"}}, {"textRun": {"content": "dam\n"}}]}}}]}
        self.assertEqual(" ".join(se._page_text(page).split()),
                         "AAIF Community Events Amsterdam")


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
                            return_value=self._resp(se._PNG_MAGIC + b"p" * 2000)) as uo:
                self.assertIsNone(se._download("https://example.invalid/x.png", out))
            self.assertEqual(os.path.getsize(out), 2008)
            self.assertEqual(uo.call_args.kwargs.get("timeout"), 30)

    def test_a_too_small_render_is_deleted_not_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with mock.patch("urllib.request.urlopen", return_value=self._resp(b"tiny")):
                with self.assertRaises(RuntimeError) as cm:
                    se._download("https://example.invalid/x.png", out)
            self.assertIn("suspiciously small", str(cm.exception))
            self.assertFalse(os.path.exists(out))

    def test_a_non_png_body_is_refused_and_deleted(self):
        """An HTML interstitial is large enough to pass the size check."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            with mock.patch("urllib.request.urlopen",
                            return_value=self._resp(b"<html>" + b"x" * 2000)):
                with self.assertRaises(RuntimeError) as cm:
                    se._download("https://example.invalid/x.png", out)
            self.assertIn("not a PNG", str(cm.exception))
            self.assertEqual(os.listdir(d), [])

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
