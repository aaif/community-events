import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import sync_badges  # noqa: E402


class TestNeededFilenames(unittest.TestCase):
    def test_six_files_per_slug_across_both_styles(self):
        got = sync_badges.needed_filenames("mexico_city")
        self.assertEqual(got, [
            "organizer_badge_mexico_city_colour.svg",
            "organizer_badge_mexico_city_white.svg",
            "organizer_badge_mexico_city_colour_1000.png",
            "organizer_badge_mexico_city_white_1000.png",
            "organizer_badge_mexico_city_agent.svg",
            "organizer_badge_mexico_city_agent_1000.png",
        ])


class TestStylesNeededFor(unittest.TestCase):
    def test_only_the_relevant_module_is_selected(self):
        make_badges_style, agent_style = sync_badges.STYLES
        got = sync_badges.styles_needed_for(["organizer_badge_dublin_agent.svg"])
        self.assertEqual(got, [agent_style])

    def test_both_builders_run_when_both_kinds_are_missing(self):
        got = sync_badges.styles_needed_for(
            ["organizer_badge_dublin_colour.svg", "organizer_badge_dublin_agent_1000.png"])
        self.assertEqual(len(got), 2)


class TestCanonicalChapters(unittest.TestCase):
    def _fake_children(self, folder_id):
        self.assertEqual(folder_id, sync_badges.CHAPTERS_PARENT)
        return [
            {"id": "mc-id", "name": "Mexico City", "mimeType": sync_badges.FOLDER},
            {"id": "dn-id", "name": "Delhi NCR", "mimeType": sync_badges.FOLDER},
            {"id": "tc-id", "name": "TemplateCity", "mimeType": sync_badges.FOLDER},
            {"id": "et-id", "name": "Event Tracker.docx", "mimeType": "application/vnd.google-apps.document"},
        ]

    def test_excludes_templatecity_and_non_folders(self):
        with mock.patch.object(sync_badges, "list_children", self._fake_children):
            chapters = sync_badges.canonical_chapters()
        self.assertEqual(chapters, {
            "mexico_city": {"name": "Mexico City", "folder_id": "mc-id"},
            "delhi_ncr": {"name": "Delhi NCR", "folder_id": "dn-id"},
        })

    def test_collision_aborts_rather_than_silently_dropping_a_chapter(self):
        def children(_folder_id):
            return [
                {"id": "1", "name": "Mexico City", "mimeType": sync_badges.FOLDER},
                {"id": "2", "name": "Mexico  City", "mimeType": sync_badges.FOLDER},  # slugifies the same
            ]
        with mock.patch.object(sync_badges, "list_children", children):
            with self.assertRaises(SystemExit) as e:
                sync_badges.canonical_chapters()
        self.assertIn("collide", str(e.exception))


class TestFindBadgesSubfolder(unittest.TestCase):
    def test_finds_the_folder_named_badges(self):
        children = [
            {"name": "CRM.xlsx", "mimeType": "application/vnd.google-apps.spreadsheet"},
            {"id": "badges-id", "name": "Badges", "mimeType": sync_badges.FOLDER},
            {"id": "icons-id", "name": "Icons", "mimeType": sync_badges.FOLDER},
        ]
        with mock.patch.object(sync_badges, "list_children", return_value=children):
            self.assertEqual(sync_badges.find_badges_subfolder("chapter-id"), "badges-id")

    def test_returns_none_when_absent(self):
        children = [{"id": "icons-id", "name": "Icons", "mimeType": sync_badges.FOLDER}]
        with mock.patch.object(sync_badges, "list_children", return_value=children):
            self.assertIsNone(sync_badges.find_badges_subfolder("chapter-id"))


class TestPlan(unittest.TestCase):
    def test_new_chapter_needs_folder_and_all_files(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "cid"}}
        folders, files = sync_badges.plan(chapters, badges_folders={}, children_by_slug={}, regenerate=False)
        self.assertEqual(folders, {"dublin": "Dublin"})
        self.assertEqual(files["dublin"], (None, sync_badges.needed_filenames("dublin")))

    def test_existing_folder_only_uploads_missing_files(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "cid"}}
        badges_folders = {"dublin": "badges-fid"}
        children_by_slug = {"dublin": [
            {"name": "organizer_badge_dublin_colour.svg"},
            {"name": "organizer_badge_dublin_white.svg"},
            {"name": "organizer_badge_dublin_agent.svg"},
            {"name": "organizer_badge_dublin_agent_1000.png"},
        ]}
        folders, files = sync_badges.plan(chapters, badges_folders, children_by_slug, regenerate=False)
        self.assertEqual(folders, {})
        self.assertEqual(files["dublin"], ("badges-fid", [
            "organizer_badge_dublin_colour_1000.png",
            "organizer_badge_dublin_white_1000.png",
        ]))

    def test_complete_chapter_is_left_alone(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "cid"}}
        badges_folders = {"dublin": "badges-fid"}
        children_by_slug = {"dublin": [{"name": n} for n in sync_badges.needed_filenames("dublin")]}
        folders, files = sync_badges.plan(chapters, badges_folders, children_by_slug, regenerate=False)
        self.assertEqual(folders, {})
        self.assertEqual(files, {})

    def test_regenerate_reuploads_every_file_even_if_complete(self):
        chapters = {"dublin": {"name": "Dublin", "folder_id": "cid"}}
        badges_folders = {"dublin": "badges-fid"}
        children_by_slug = {"dublin": [{"name": n} for n in sync_badges.needed_filenames("dublin")]}
        folders, files = sync_badges.plan(chapters, badges_folders, children_by_slug, regenerate=True)
        self.assertEqual(files["dublin"], ("badges-fid", sync_badges.needed_filenames("dublin")))


class TestListChildrenPagination(unittest.TestCase):
    def test_follows_next_page_token_to_the_end(self):
        pages = [
            {"nextPageToken": "p2", "files": [{"id": "1", "name": "a", "mimeType": sync_badges.FOLDER}]},
            {"files": [{"id": "2", "name": "b", "mimeType": sync_badges.FOLDER}]},
        ]
        calls = []

        def fake_gws_json(*_args, params=None, **_kwargs):
            calls.append(params.get("pageToken"))
            return pages.pop(0)

        with mock.patch.object(sync_badges, "gws_json", fake_gws_json):
            out = sync_badges.list_children("folder123")
        self.assertEqual([f["id"] for f in out], ["1", "2"])
        self.assertEqual(calls, [None, "p2"])  # second call carries the token from page 1


class TestGwsRetry(unittest.TestCase):
    def test_retries_transient_failure_then_succeeds(self):
        results = [
            mock.Mock(returncode=1, stdout="", stderr="503 Service Unavailable"),
            mock.Mock(returncode=0, stdout="ok", stderr=""),
        ]
        with mock.patch.object(sync_badges.subprocess, "run", side_effect=results), \
             mock.patch.object(sync_badges.time, "sleep"):
            out = sync_badges._gws(["gws", "noop"])
        self.assertEqual(out, "ok")

    def test_non_transient_failure_raises_immediately(self):
        result = mock.Mock(returncode=1, stdout="", stderr="permission denied")
        with mock.patch.object(sync_badges.subprocess, "run", return_value=result) as run:
            with self.assertRaises(RuntimeError):
                sync_badges._gws(["gws", "noop"])
        self.assertEqual(run.call_count, 1)  # no retry on a non-transient error

    def test_retries_1_means_no_retry_even_on_transient_error(self):
        # Non-idempotent writes (create_folder, upload_new) pass retries=1 so a
        # timed-out `files.create` is never blindly retried into a duplicate.
        result = mock.Mock(returncode=1, stdout="", stderr="timed out")
        with mock.patch.object(sync_badges.subprocess, "run", return_value=result) as run:
            with self.assertRaises(RuntimeError):
                sync_badges._gws(["gws", "noop"], retries=1)
        self.assertEqual(run.call_count, 1)


class TestWritePathDispatch(unittest.TestCase):
    """main()'s create-vs-update dispatch, fully mocked -- no live Drive, no Chrome."""

    def _run_main(self, argv, all_chapters, children_by_folder_id):
        with mock.patch.object(sys, "argv", ["sync_badges.py"] + argv), \
             mock.patch.object(sync_badges, "canonical_chapters", return_value=all_chapters), \
             mock.patch.object(sync_badges, "list_children",
                                side_effect=lambda fid: children_by_folder_id.get(fid, [])), \
             mock.patch.object(sync_badges, "create_folder") as create_folder, \
             mock.patch.object(sync_badges, "upload_new") as upload_new, \
             mock.patch.object(sync_badges, "upload_update") as upload_update, \
             mock.patch("make_badges.build") as badges_build, \
             mock.patch("make_agent_badge.build") as agent_build:
            create_folder.return_value = "new-folder-id"
            sync_badges.main()
        return create_folder, upload_new, upload_update, badges_build, agent_build

    def test_new_chapter_creates_folder_and_uploads_every_file(self):
        all_chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}
        children_by_folder_id = {"chapter-fid": []}  # no Badges/ subfolder yet
        create_folder, upload_new, upload_update, badges_build, agent_build = self._run_main(
            ["--write", "--chapter", "Dublin"], all_chapters, children_by_folder_id)

        create_folder.assert_called_once_with(sync_badges.BADGES_SUBFOLDER, "chapter-fid")
        badges_build.assert_called_once_with("Dublin", mock.ANY, "dublin")
        agent_build.assert_called_once_with("Dublin", mock.ANY, "dublin")
        self.assertEqual(upload_new.call_count, 6)
        upload_update.assert_not_called()

    def test_existing_folder_only_uploads_the_files_actually_missing(self):
        all_chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}
        children_by_folder_id = {
            "chapter-fid": [{"id": "badges-fid", "name": "Badges", "mimeType": sync_badges.FOLDER}],
            "badges-fid": [{"id": "existing-svg", "name": "organizer_badge_dublin_colour.svg"}],
        }
        create_folder, upload_new, upload_update, badges_build, agent_build = self._run_main(
            ["--write", "--chapter", "Dublin"], all_chapters, children_by_folder_id)

        # The already-present file is left untouched (neither uploaded nor
        # updated) -- only the 5 genuinely missing files are created.
        create_folder.assert_not_called()
        upload_update.assert_not_called()
        self.assertEqual(upload_new.call_count, 5)
        badges_build.assert_called_once()
        agent_build.assert_called_once()

    def test_only_agent_builder_runs_when_only_agent_files_are_missing(self):
        all_chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}
        children_by_folder_id = {
            "chapter-fid": [{"id": "badges-fid", "name": "Badges", "mimeType": sync_badges.FOLDER}],
            "badges-fid": [{"id": n, "name": n} for n in [
                "organizer_badge_dublin_colour.svg", "organizer_badge_dublin_white.svg",
                "organizer_badge_dublin_colour_1000.png", "organizer_badge_dublin_white_1000.png",
            ]],
        }
        create_folder, upload_new, upload_update, badges_build, agent_build = self._run_main(
            ["--write", "--chapter", "Dublin"], all_chapters, children_by_folder_id)

        badges_build.assert_not_called()
        agent_build.assert_called_once()
        self.assertEqual(upload_new.call_count, 2)

    def test_regenerate_updates_the_existing_file_in_place(self):
        all_chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}
        children_by_folder_id = {
            "chapter-fid": [{"id": "badges-fid", "name": "Badges", "mimeType": sync_badges.FOLDER}],
            "badges-fid": [{"id": "existing-svg", "name": "organizer_badge_dublin_colour.svg"}],
        }
        create_folder, upload_new, upload_update, badges_build, agent_build = self._run_main(
            ["--write", "--regenerate", "--chapter", "Dublin"], all_chapters, children_by_folder_id)

        create_folder.assert_not_called()
        upload_update.assert_called_once()
        self.assertEqual(upload_update.call_args.args[0], "existing-svg")
        self.assertEqual(upload_new.call_count, 5)  # the other 5 needed files

    def test_plan_only_never_touches_drive(self):
        all_chapters = {"dublin": {"name": "Dublin", "folder_id": "chapter-fid"}}
        children_by_folder_id = {"chapter-fid": []}
        create_folder, upload_new, upload_update, badges_build, agent_build = self._run_main(
            ["--chapter", "Dublin"], all_chapters, children_by_folder_id)  # no --write

        create_folder.assert_not_called()
        upload_new.assert_not_called()
        upload_update.assert_not_called()
        badges_build.assert_not_called()
        agent_build.assert_not_called()


class TestScrubbedEnv(unittest.TestCase):
    def test_drops_slack_and_luma_secrets_only(self):
        with mock.patch.dict(os.environ, {"AAIF_SLACK_WRITE_TOKEN": "x",
                                          "AAIF_SLACK_READ_TOKEN": "y",
                                          "LUMA_API_KEY": "z", "HOME_KEEP": "1"}):
            env = sync_badges._scrubbed_env()
        self.assertNotIn("AAIF_SLACK_WRITE_TOKEN", env)
        self.assertNotIn("AAIF_SLACK_READ_TOKEN", env)
        self.assertNotIn("LUMA_API_KEY", env)
        self.assertEqual(env["HOME_KEEP"], "1")


if __name__ == "__main__":
    unittest.main()
