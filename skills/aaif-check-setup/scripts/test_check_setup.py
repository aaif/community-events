#!/usr/bin/env python3
"""Tests for check_setup.py — synthetic values only. Run: python3 test_check_setup.py"""

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


class TestFindSecret(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.path.join(self.tmp.name, ".env")
        patches = [mock.patch.object(cs, "DOTENV", self.env),
                   mock.patch.dict(os.environ, {}, clear=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("AAIF_SLACK_READ_TOKEN", None)
        self.addCleanup(self.tmp.cleanup)

    def test_environment_then_dotenv_then_keychain(self):
        with mock.patch.object(cs, "keychain", lambda s: "from-keychain"):
            self.assertEqual(cs.find_secret("AAIF_SLACK_READ_TOKEN", "svc")[1],
                             "the Mac keychain")
            with open(self.env, "w") as fh:
                fh.write("export AAIF_SLACK_READ_TOKEN='from-dotenv'\n")
            self.assertEqual(cs.find_secret("AAIF_SLACK_READ_TOKEN", "svc"),
                             ("from-dotenv", "the .env file"))
            os.environ["AAIF_SLACK_READ_TOKEN"] = "from-env"
            self.assertEqual(cs.find_secret("AAIF_SLACK_READ_TOKEN", "svc"),
                             ("from-env", "the environment"))

    def test_nothing_anywhere(self):
        with mock.patch.object(cs, "keychain", lambda s: None):
            self.assertEqual(cs.find_secret("AAIF_SLACK_READ_TOKEN", "svc"), (None, None))


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
