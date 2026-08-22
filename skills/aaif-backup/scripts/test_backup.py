import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import backup  # noqa: E402


class TestSlugify(unittest.TestCase):
    def test_normalizes_name(self):
        self.assertEqual(backup.slugify("AAIF Community Intake Ops"),
                         "aaif-community-intake-ops")

    def test_collapses_runs_and_strips_edges(self):
        self.assertEqual(backup.slugify("  --Foo!!Bar--  "), "foo-bar")

    def test_empty_or_garbage_falls_back(self):
        self.assertEqual(backup.slugify("   "), "backup")
        self.assertEqual(backup.slugify("!!!"), "backup")


class TestTimestamp(unittest.TestCase):
    def test_utc_filename_safe_format(self):
        self.assertRegex(backup.timestamp(), r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")


class TestSnapshotPath(unittest.TestCase):
    def test_never_overwrites_within_same_second(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = backup.snapshot_path(d, "s", "xlsx")
            open(p1, "w").close()
            p2 = backup.snapshot_path(d, "s", "xlsx")
            self.assertNotEqual(p1, p2)
            self.assertFalse(os.path.exists(p2))


class TestBackupLocal(unittest.TestCase):
    def test_copies_and_keeps_extension(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "thing.xlsx")
            with open(src, "w") as f:
                f.write("data")
            out = backup.backup_local(src, os.path.join(d, "bk"))
            self.assertTrue(out.endswith(".xlsx"))
            self.assertTrue(os.path.isfile(out))

    def test_no_extension_becomes_bin(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "noext")
            with open(src, "w") as f:
                f.write("x")
            out = backup.backup_local(src, os.path.join(d, "bk"))
            self.assertTrue(out.endswith(".bin"))

    def test_missing_file_exits_not_silent(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                backup.backup_local(os.path.join(d, "nope.xlsx"), d)


class TestAssertDestGitSafe(unittest.TestCase):
    """The committable-path guard: snapshots are the full applicant export, and
    the repo is public, so an unignored --dest must refuse before any fetch."""

    def _repo(self, d):
        subprocess.run(["git", "init", "-q", d], check=True, capture_output=True)
        return d

    def test_refuses_an_unignored_dest_inside_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            with self.assertRaises(SystemExit) as e:
                backup.assert_dest_git_safe(os.path.join(d, "backups"))
            self.assertIn("committable", str(e.exception))

    def test_allows_an_ignored_dest_even_before_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("backups/\n")
            backup.assert_dest_git_safe(os.path.join(d, "backups"))  # must not raise

    def test_allows_a_dest_outside_any_repo(self):
        with tempfile.TemporaryDirectory() as d:
            # check-ignore exits 128 out here; that must read as "safe", since
            # moving --dest outside the repo is the guard's own recommendation.
            backup.assert_dest_git_safe(os.path.join(d, "backups"))

    def test_a_git_failure_that_is_not_outside_a_repo_aborts(self):
        """Pins the deliberate copy of report_style._repo_root's guard: a git
        failure other than "not a git repository" (here a corrupt .git; dubious
        ownership behaves the same) must abort, not read as "outside any repo"
        and silently disengage the PII guard."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".git"), "w") as f:
                f.write("not a gitfile\n")     # exit 128: invalid gitfile format
            with self.assertRaises(SystemExit) as e:
                backup.assert_dest_git_safe(os.path.join(d, "backups"))
            self.assertIn("REFUSING TO RUN", str(e.exception))

    def test_refuses_a_dest_holding_tracked_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            bk = os.path.join(d, "backups")
            os.makedirs(bk)
            with open(os.path.join(bk, "old.xlsx"), "w") as f:
                f.write("x")
            subprocess.run(["git", "-C", d, "add", "backups/old.xlsx"],
                           check=True, capture_output=True)
            # ignored NOW, but the earlier commit-era file still rides `git add -A`
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("backups/\n")
            with self.assertRaises(SystemExit) as e:
                backup.assert_dest_git_safe(bk)
            self.assertIn("TRACKED", str(e.exception))

    def test_default_dest_with_only_readme_tracked_is_allowed(self):
        """Regression: the repo ships backups/README.md (tracked, re-included)
        while `backups/*` is ignored. `ls-files --error-unmatch backups` matched
        that README and refused the default dest on every run."""
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            bk = os.path.join(d, "backups")
            os.makedirs(bk)
            with open(os.path.join(bk, "README.md"), "w") as f:
                f.write("why this folder exists\n")
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("backups/*\n!backups/README.md\n")
            subprocess.run(["git", "-C", d, "add", "backups/README.md"],
                           check=True, capture_output=True)
            backup.assert_dest_git_safe(bk)  # must not raise
            # and the final snapshot path is ignored too
            p = backup.snapshot_path(bk, "intake", "xlsx")
            self.assertTrue(p.startswith(bk))

    def test_tracked_readme_in_a_subfolder_still_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            sub = os.path.join(d, "backups", "intake")
            os.makedirs(sub)
            with open(os.path.join(sub, "README.md"), "w") as f:
                f.write("x")
            subprocess.run(["git", "-C", d, "add", "backups/intake/README.md"],
                           check=True, capture_output=True)
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("backups/*\n")
            with self.assertRaises(SystemExit) as e:
                backup.assert_dest_git_safe(os.path.join(d, "backups"))
            self.assertIn("TRACKED", str(e.exception))

    def test_snapshot_path_refuses_a_reincluded_file(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            bk = os.path.join(d, "backups")
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("backups/*\n!backups/intake/\n")
            backup.assert_dest_git_safe(bk)  # folder-level probe passes
            with self.assertRaises(SystemExit) as e:
                backup.snapshot_path(bk, "intake", "xlsx")
            self.assertIn("REFUSING TO WRITE", str(e.exception))
            # the refusal happens before makedirs: no empty slug folder is left
            self.assertFalse(os.path.exists(os.path.join(bk, "intake")))

    def test_snapshot_path_outside_any_repo_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            p = backup.snapshot_path(os.path.join(d, "bk"), "intake", "xlsx")
            self.assertTrue(os.path.isdir(os.path.dirname(p)))
            self.assertFalse(os.path.exists(p))


class TestScrubbedEnv(unittest.TestCase):
    def test_drops_slack_and_luma_secrets_only(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"AAIF_SLACK_WRITE_TOKEN": "x",
                                          "AAIF_SLACK_READ_TOKEN": "y",
                                          "LUMA_API_KEY": "z", "HOME_KEEP": "1"}):
            env = backup._scrubbed_env()
        self.assertNotIn("AAIF_SLACK_WRITE_TOKEN", env)
        self.assertNotIn("AAIF_SLACK_READ_TOKEN", env)
        self.assertNotIn("LUMA_API_KEY", env)
        self.assertEqual(env["HOME_KEEP"], "1")


class TestDriveIdDispatch(unittest.TestCase):
    def test_id_shape_matches_but_paths_do_not(self):
        self.assertTrue(backup.DRIVE_ID_RE.match(backup.INTAKE_OPS_ID))
        self.assertIsNone(backup.DRIVE_ID_RE.match("./intake.xlsx"))
        self.assertIsNone(backup.DRIVE_ID_RE.match("intake.xlsx"))


if __name__ == "__main__":
    unittest.main()
