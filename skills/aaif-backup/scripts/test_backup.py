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


class TestDriveIdDispatch(unittest.TestCase):
    def test_id_shape_matches_but_paths_do_not(self):
        self.assertTrue(backup.DRIVE_ID_RE.match(backup.INTAKE_OPS_ID))
        self.assertIsNone(backup.DRIVE_ID_RE.match("./intake.xlsx"))
        self.assertIsNone(backup.DRIVE_ID_RE.match("intake.xlsx"))


if __name__ == "__main__":
    unittest.main()
