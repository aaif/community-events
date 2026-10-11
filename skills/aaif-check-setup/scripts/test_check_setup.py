#!/usr/bin/env python3
"""Tests for check_setup.py — synthetic values only. Run: python3 test_check_setup.py"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_setup as cs  # noqa: E402


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class TestScrub(unittest.TestCase):
    def test_credentials_never_reach_the_output(self):
        line = cs.scrub("Using keyring backend: keyring\nbad token xoxp-1-abc ya29.a0Zz")
        self.assertNotIn("xoxp-1-abc", line)
        self.assertNotIn("ya29.a0Zz", line)
        self.assertNotIn("keyring backend", line)


class TestFindSecrets(unittest.TestCase):
    PAIRS = (("AAIF_SLACK_READ_TOKEN", "read-svc"), ("AAIF_SLACK_WRITE_TOKEN", "write-svc"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.path.join(self.tmp.name, ".env")
        for p in (mock.patch.object(cs, "DOTENV", self.env),
                  mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for var, _ in self.PAIRS:
            os.environ.pop(var, None)
        self.addCleanup(self.tmp.cleanup)

    def test_environment_then_dotenv_then_keychain(self):
        one = (("AAIF_SLACK_READ_TOKEN", "svc"),)
        with mock.patch.object(cs, "keychain", lambda s: ("from-keychain", None)):
            self.assertEqual(cs.find_secrets(one)[1], "the Mac keychain")
            with open(self.env, "w") as fh:
                fh.write("export AAIF_SLACK_READ_TOKEN='from-dotenv'\n")
            self.assertEqual(cs.find_secrets(one), ("from-dotenv", "the .env file", None))
            os.environ["AAIF_SLACK_READ_TOKEN"] = "from-env"
            self.assertEqual(cs.find_secrets(one), ("from-env", "the environment", None))

    def test_one_source_at_a_time_like_the_engines(self):
        """READ in the keychain, WRITE in .env: the engines use WRITE."""
        with open(self.env, "w") as fh:
            fh.write("AAIF_SLACK_WRITE_TOKEN=write-in-dotenv\n")
        with mock.patch.object(cs, "keychain", lambda s: ("read-in-keychain", None)):
            self.assertEqual(cs.find_secrets(self.PAIRS)[0], "write-in-dotenv")

    def test_nothing_anywhere(self):
        with mock.patch.object(cs, "keychain", lambda s: (None, None)):
            self.assertEqual(cs.find_secrets(self.PAIRS), (None, None, None))

    def test_a_refusing_keychain_is_not_a_missing_key(self):
        """A locked keychain must not send the operator to create a second item."""
        refused = mock.patch.object(cs.subprocess, "run", lambda *a, **k: done(
            51, err="security: User interaction is not allowed."))
        with refused:
            status, line, fix = cs.check_slack()
        self.assertEqual(status, cs.NEEDED)
        self.assertNotIn("No Slack token", line)
        self.assertIn("keychain would not hand over", line)
        self.assertIn("Do not create a second item", fix)
        cs_luma = mock.patch.object(cs.subprocess, "run", lambda *a, **k: done(
            36, err="security: The user name or passphrase you entered is not correct."))
        with cs_luma:
            status, line, _ = cs.check_luma()
        self.assertEqual(status, cs.NEEDED)
        self.assertNotIn("No Luma key", line)

    def test_a_missing_item_is_still_optional(self):
        with mock.patch.object(cs.subprocess, "run", lambda *a, **k: done(
                44, err="security: The specified item could not be found in the keychain.")):
            self.assertEqual(cs.check_slack()[0], cs.OPTIONAL)


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestNetworkChecks(unittest.TestCase):
    """Slack and Luma answers, with a synthetic token that must never print."""

    TOKEN = "xoxp-test-123"

    def secret(self):
        return mock.patch.object(cs, "find_secrets",
                                 lambda pairs: (self.TOKEN, "the environment", None))

    def test_slack_ok_names_the_source(self):
        with self.secret(), mock.patch.object(cs.urllib.request, "urlopen",
                                              lambda *a, **k: _Resp(b'{"ok": true, "team": "T"}')):
            status, line, _ = cs.check_slack()
        self.assertEqual(status, cs.OK)
        self.assertIn("the environment", line)

    def test_slack_refused_token(self):
        with self.secret(), mock.patch.object(
                cs.urllib.request, "urlopen",
                lambda *a, **k: _Resp(b'{"ok": false, "error": "invalid_auth"}')):
            status, line, _ = cs.check_slack()
        self.assertEqual((status, "invalid_auth" in line), (cs.NEEDED, True))

    def test_slack_unreachable(self):
        def fail(*a, **k):
            raise cs.urllib.error.URLError("offline")
        with self.secret(), mock.patch.object(cs.urllib.request, "urlopen", fail):
            self.assertIn("could not be reached", cs.check_slack()[1])

    def luma(self, code):
        def fail(*a, **k):
            raise cs.urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))
        with self.secret(), mock.patch.object(cs.urllib.request, "urlopen", fail):
            return cs.check_luma()

    def test_luma_401_is_a_refused_key(self):
        self.assertIn("was refused", self.luma(401)[1])

    def test_luma_outage_is_not_a_bad_key(self):
        """A 503 must not send the operator to rotate a working key."""
        status, line, fix = self.luma(503)
        self.assertIn("not answering", line)
        self.assertNotIn("rotated", fix)


class TestMain(unittest.TestCase):
    def run_main(self, results):
        checks = tuple((lambda r=r: r) for r in results)
        out = []
        with mock.patch.object(cs, "CHECKS", checks), \
                mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            code = cs.main()
        return code, "\n".join(out)

    def test_needed_exits_1_and_optional_does_not(self):
        self.assertEqual(self.run_main([(cs.OK, "a", None), (cs.OPTIONAL, "b", "x")])[0], 0)
        self.assertEqual(self.run_main([(cs.NEEDED, "c", "y")])[0], 1)

    def test_the_sign_in_command_follows_only_a_sign_in_line(self):
        _, text = self.run_main([(cs.NEEDED, cs.SIGN_IN_NONE, "sign in")])
        self.assertIn(cs.LOGIN, text)
        _, text = self.run_main([(cs.NEEDED, "The `gws` Google Workspace tool is not installed.", "x")])
        self.assertNotIn(cs.LOGIN, text)

    def test_a_token_never_reaches_the_output(self):
        token = "xoxp-test-123"
        with mock.patch.dict(os.environ, {"AAIF_SLACK_READ_TOKEN": token}), \
                mock.patch.object(cs.urllib.request, "urlopen",
                                  lambda *a, **k: _Resp(b'{"ok": true, "team": "T"}')):
            _, text = self.run_main([cs.check_slack()])
        self.assertNotIn(token, text)


class TestGoogle(unittest.TestCase):
    def run_with(self, result):
        with mock.patch.object(cs, "find_gws", lambda: ("/x/gws", True)), \
                mock.patch.object(cs.subprocess, "run", lambda *a, **k: result):
            return cs.check_google()

    def test_editor(self):
        status, _, _ = self.run_with(done(out='{"capabilities": {"canEdit": true}}'))
        self.assertEqual(status, cs.OK)

    def test_view_only_is_not_ready(self):
        status, line, _ = self.run_with(done(out='{"capabilities": {"canEdit": false}}'))
        self.assertEqual(status, cs.NEEDED)
        self.assertIn("VIEW", line)

    def test_signed_out_points_at_login(self):
        status, _, fix = self.run_with(done(1, err="error: no credentials found"))
        self.assertEqual(status, cs.NEEDED)
        self.assertIn("sign you in", fix)

    def test_login_names_every_scope_and_no_picker(self):
        """`-s` opens an interactive picker the assistant cannot answer."""
        self.assertIn("--scopes", cs.LOGIN)
        self.assertNotIn(" -s ", cs.LOGIN)
        for scope in ("drive", "spreadsheets", "documents", "presentations"):
            self.assertIn("https://www.googleapis.com/auth/" + scope, cs.LOGIN)

    def test_unreadable_token_cache_says_logout_first(self):
        with mock.patch.object(cs, "stray_gws_key", lambda: False):
            status, line, fix = self.run_with(done(1, err="warning: failed to decrypt "
                                                          "token cache\nerror: 401"))
        self.assertEqual(status, cs.NEEDED)
        self.assertIn("gws auth logout", fix)
        # Sign-out leaves the keychain keys; a mismatched key survives it.
        self.assertIn("delete every entry", fix)
        self.assertNotIn("stray", line)

    def test_a_key_minted_without_user_is_named(self):
        with mock.patch.object(cs, "stray_gws_key", lambda: True):
            _, line, _ = self.run_with(done(1, err="failed to decrypt token cache"))
        self.assertIn("stray", line)

    def test_stray_key_probe_never_reads_the_secret(self):
        seen = []
        with mock.patch.object(cs.subprocess, "run",
                               lambda argv, **kw: seen.append(argv) or done(44)):
            self.assertFalse(cs.stray_gws_key())
        self.assertNotIn("-w", seen[0])
        self.assertIn("unknown-user", seen[0])

    def test_missing_scope_is_not_called_signed_out(self):
        status, line, fix = self.run_with(done(1, err="403 Request had insufficient "
                                                      "authentication scopes."))
        self.assertIn("without permission", line)
        self.assertIn("allow everything", fix)

    def test_no_access_is_not_called_signed_out(self):
        status, line, _ = self.run_with(done(1, err="error[api]: 404 File not found: 1AbC401xyz"))
        self.assertEqual(line, cs.SIGN_IN_WRONG_ACCOUNT)

    def test_an_unreadable_reply_is_not_a_permissions_finding(self):
        status, line, fix = self.run_with(done(out="not json at all"))
        self.assertEqual(status, cs.NEEDED)
        self.assertNotIn("VIEW", line)
        self.assertNotIn("admin", fix)

    def test_gws_off_path_is_reported_not_run(self):
        with mock.patch.object(cs, "find_gws", lambda: ("/opt/homebrew/bin/gws", False)):
            status, line, _ = cs.check_gws()
        self.assertEqual(status, cs.NEEDED)
        self.assertIn("cannot see it", line)


class TestKeychainSteps(unittest.TestCase):
    def test_no_terminal_prompt_and_no_chat_paste(self):
        """The assistant's shell has no keyboard; a chat paste is kept."""
        steps = cs.keychain_steps("aaif-slack-write-token", "token")
        self.assertIn("Keychain Access", steps)
        self.assertIn("Never paste it into the chat", steps)
        self.assertNotIn("security add-generic-password", steps)


if __name__ == "__main__":
    unittest.main()
