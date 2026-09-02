import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import migrate_legacy_badges as mlb  # noqa: E402
import sync_badges as sb  # noqa: E402


class TestPlanLegacyFolders(unittest.TestCase):
    def test_matches_slug_folder_to_its_chapter(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}

        def children(folder_id):
            if folder_id == mlb.LEGACY_BADGES_PARENT:
                return [{"id": "legacy-dublin-fid", "name": "dublin", "mimeType": sb.FOLDER},
                        {"name": ".DS_Store", "mimeType": "application/octet-stream"}]
            if folder_id == "legacy-dublin-fid":
                return [{"id": "f1", "name": "organizer_badge_dublin_colour.svg"}]
            raise AssertionError(f"unexpected list_children({folder_id!r})")

        with mock.patch.object(sb, "list_children", side_effect=children):
            legacy, all_legacy, orphans = mlb.plan_legacy_folders(chapters)
        self.assertEqual(set(legacy), {"dublin"})
        self.assertEqual(legacy["dublin"]["id"], "legacy-dublin-fid")
        self.assertEqual(all_legacy, {"dublin": "legacy-dublin-fid"})
        self.assertEqual(orphans, [])

    def test_folder_matching_no_chapter_is_an_orphan_not_touched(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}

        def children(folder_id):
            if folder_id == mlb.LEGACY_BADGES_PARENT:
                return [{"id": "stray-fid", "name": "Neverland", "mimeType": sb.FOLDER}]
            if folder_id == "stray-fid":
                return [{"id": "f1", "name": "organizer_badge_neverland_colour.svg"}]
            raise AssertionError(f"unexpected list_children({folder_id!r})")

        with mock.patch.object(sb, "list_children", side_effect=children):
            legacy, all_legacy, orphans = mlb.plan_legacy_folders(chapters)
        self.assertEqual(legacy, {})
        self.assertEqual(all_legacy, {})  # Neverland matches no chapter -- not tracked at all
        self.assertEqual(orphans, [("Neverland", ["organizer_badge_neverland_colour.svg"])])

    def test_empty_legacy_folder_has_nothing_to_move_but_is_still_tracked_for_trashing(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}

        def children(folder_id):
            if folder_id == mlb.LEGACY_BADGES_PARENT:
                return [{"id": "legacy-dublin-fid", "name": "dublin", "mimeType": sb.FOLDER}]
            return []  # already fully migrated -- nothing left to move

        with mock.patch.object(sb, "list_children", side_effect=children):
            legacy, all_legacy, orphans = mlb.plan_legacy_folders(chapters)
        self.assertEqual(legacy, {})
        # Still tracked (not an orphan, has no files) so --trash-empty can act on it
        # even on a run where there was nothing left to migrate.
        self.assertEqual(all_legacy, {"dublin": "legacy-dublin-fid"})
        self.assertEqual(orphans, [])


class TestMainDispatch(unittest.TestCase):
    """main()'s move/skip/create decisions, fully mocked -- no live Drive."""

    def _run_main(self, argv, chapters, legacy_children, dest_children):
        def list_children(folder_id):
            if folder_id in legacy_children:
                return legacy_children[folder_id]
            if folder_id in dest_children:
                return dest_children[folder_id]
            return []

        with mock.patch.object(sys, "argv", ["migrate_legacy_badges.py"] + argv), \
             mock.patch.object(sb, "canonical_chapters", return_value=chapters), \
             mock.patch.object(sb, "list_children", side_effect=list_children), \
             mock.patch.object(sb, "find_badges_subfolder",
                                side_effect=lambda cid: "existing-badges-fid" if cid == "has-badges-fid" else None), \
             mock.patch.object(sb, "create_folder") as create_folder, \
             mock.patch.object(mlb, "move_file") as move_file, \
             mock.patch.object(mlb, "trash_folder") as trash_folder:
            create_folder.return_value = "new-badges-fid"
            mlb.main()
        return create_folder, move_file, trash_folder

    def test_moves_files_and_creates_badges_folder_when_missing(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "no-badges-fid"}}
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [{"id": "legacy-fid", "name": "dublin", "mimeType": sb.FOLDER}],
            "legacy-fid": [{"id": "f1", "name": "organizer_badge_dublin_colour.svg"}],
        }
        create_folder, move_file, trash_folder = self._run_main(
            ["--write"], chapters, legacy_children, dest_children={})

        create_folder.assert_called_once_with(sb.BADGES_SUBFOLDER, "no-badges-fid")
        move_file.assert_called_once_with("f1", "new-badges-fid", "legacy-fid")
        trash_folder.assert_not_called()  # --trash-empty not passed

    def test_file_already_present_at_destination_is_skipped(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "has-badges-fid"}}
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [{"id": "legacy-fid", "name": "dublin", "mimeType": sb.FOLDER}],
            "legacy-fid": [{"id": "f1", "name": "organizer_badge_dublin_colour.svg"}],
        }
        dest_children = {"existing-badges-fid": [{"id": "f1-dup", "name": "organizer_badge_dublin_colour.svg"}]}
        create_folder, move_file, trash_folder = self._run_main(
            ["--write"], chapters, legacy_children, dest_children)

        create_folder.assert_not_called()
        move_file.assert_not_called()

    def test_trash_empty_only_trashes_folders_left_empty_by_the_move(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "has-badges-fid"}}
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [{"id": "legacy-fid", "name": "dublin", "mimeType": sb.FOLDER}],
            "legacy-fid": [{"id": "f1", "name": "organizer_badge_dublin_colour.svg"}],
        }

        def list_children(folder_id):
            if folder_id in legacy_children:
                return legacy_children[folder_id]
            return []

        def move_file_side_effect(_file_id, _new_parent, old_parent):
            legacy_children[old_parent] = []  # a real move empties the source folder

        with mock.patch.object(sys, "argv", ["migrate_legacy_badges.py", "--write", "--trash-empty"]), \
             mock.patch.object(sb, "canonical_chapters", return_value=chapters), \
             mock.patch.object(sb, "list_children", side_effect=list_children), \
             mock.patch.object(sb, "find_badges_subfolder", return_value="existing-badges-fid"), \
             mock.patch.object(sb, "create_folder"), \
             mock.patch.object(mlb, "move_file", side_effect=move_file_side_effect) as move_file, \
             mock.patch.object(mlb, "trash_folder") as trash_folder:
            mlb.main()

        move_file.assert_called_once()
        trash_folder.assert_called_once_with("legacy-fid")

    def test_trash_empty_still_runs_when_nothing_left_to_migrate(self):
        # Regression: a legacy folder already emptied by a PRIOR run has no
        # entry in `legacy` (nothing to move), but --trash-empty must still
        # reach and trash it -- it must not bail out at "Nothing to migrate."
        chapters = {"dublin": {"name": "Dublin", "folder_id": "has-badges-fid"}}
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [{"id": "legacy-fid", "name": "dublin", "mimeType": sb.FOLDER}],
            "legacy-fid": [],  # already empty -- nothing to move this run
        }
        create_folder, move_file, trash_folder = self._run_main(
            ["--write", "--trash-empty"], chapters, legacy_children, dest_children={})

        move_file.assert_not_called()
        trash_folder.assert_called_once_with("legacy-fid")

    def test_one_folders_trash_failure_does_not_block_the_others(self):
        chapters = {
            "dublin": {"name": "Dublin", "folder_id": "has-badges-fid"},
            "oslo": {"name": "Oslo", "folder_id": "has-badges-fid"},
        }
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [
                {"id": "legacy-dublin-fid", "name": "dublin", "mimeType": sb.FOLDER},
                {"id": "legacy-oslo-fid", "name": "oslo", "mimeType": sb.FOLDER},
            ],
            "legacy-dublin-fid": [],
            "legacy-oslo-fid": [],
        }

        def list_children(folder_id):
            return legacy_children.get(folder_id, [])

        def trash_side_effect(folder_id):
            if folder_id == "legacy-dublin-fid":
                raise RuntimeError("gws failed (1): permission denied")

        with mock.patch.object(sys, "argv", ["migrate_legacy_badges.py", "--write", "--trash-empty"]), \
             mock.patch.object(sb, "canonical_chapters", return_value=chapters), \
             mock.patch.object(sb, "list_children", side_effect=list_children), \
             mock.patch.object(sb, "find_badges_subfolder", return_value="existing-badges-fid"), \
             mock.patch.object(sb, "create_folder"), \
             mock.patch.object(mlb, "move_file"), \
             mock.patch.object(mlb, "trash_folder", side_effect=trash_side_effect) as trash_folder:
            mlb.main()  # must not raise -- the dublin failure is caught and reported

        self.assertEqual(trash_folder.call_count, 2)
        trash_folder.assert_any_call("legacy-oslo-fid")

    def test_trash_empty_without_write_aborts(self):
        with mock.patch.object(sys, "argv", ["migrate_legacy_badges.py", "--trash-empty"]):
            with self.assertRaises(SystemExit) as e:
                mlb.main()
        self.assertIn("--write", str(e.exception))

    def test_plan_only_never_touches_drive(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "no-badges-fid"}}
        legacy_children = {
            mlb.LEGACY_BADGES_PARENT: [{"id": "legacy-fid", "name": "dublin", "mimeType": sb.FOLDER}],
            "legacy-fid": [{"id": "f1", "name": "organizer_badge_dublin_colour.svg"}],
        }
        create_folder, move_file, trash_folder = self._run_main(
            [], chapters, legacy_children, dest_children={})  # no --write

        create_folder.assert_not_called()
        move_file.assert_not_called()
        trash_folder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
