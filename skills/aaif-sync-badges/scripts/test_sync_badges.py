import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import sync_badges  # noqa: E402


class TestNeededFilenames(unittest.TestCase):
    def test_four_files_per_slug(self):
        got = sync_badges.needed_filenames("mexico_city")
        self.assertEqual(got, [
            "organizer_badge_mexico_city_colour.svg",
            "organizer_badge_mexico_city_white.svg",
            "organizer_badge_mexico_city_colour_1000.png",
            "organizer_badge_mexico_city_white_1000.png",
        ])


class TestCanonicalChapters(unittest.TestCase):
    def _fake_children(self, folder_id):
        self.assertEqual(folder_id, sync_badges.CHAPTERS_PARENT)
        return [
            {"name": "Mexico City", "mimeType": sync_badges.FOLDER},
            {"name": "Delhi NCR", "mimeType": sync_badges.FOLDER},
            {"name": "TemplateCity", "mimeType": sync_badges.FOLDER},
            {"name": "Event Tracker.docx", "mimeType": "application/vnd.google-apps.document"},
        ]

    def test_excludes_templatecity_and_non_folders(self):
        with mock.patch.object(sync_badges, "list_children", self._fake_children):
            chapters = sync_badges.canonical_chapters()
        self.assertEqual(chapters, {"mexico_city": "Mexico City", "delhi_ncr": "Delhi NCR"})

    def test_collision_aborts_rather_than_silently_dropping_a_chapter(self):
        def children(_folder_id):
            return [
                {"name": "Mexico City", "mimeType": sync_badges.FOLDER},
                {"name": "Mexico  City", "mimeType": sync_badges.FOLDER},  # slugifies the same
            ]
        with mock.patch.object(sync_badges, "list_children", children):
            with self.assertRaises(SystemExit) as e:
                sync_badges.canonical_chapters()
        self.assertIn("collide", str(e.exception))


class TestPlan(unittest.TestCase):
    def test_new_chapter_needs_folder_and_all_files(self):
        chapters = {"dublin": "Dublin"}
        folders, files = sync_badges.plan(chapters, badge_folders={}, regenerate=False)
        self.assertEqual(folders, {"dublin": "Dublin"})
        self.assertEqual(files["dublin"], (None, sync_badges.needed_filenames("dublin")))

    def test_existing_folder_only_uploads_missing_files(self):
        chapters = {"dublin": "Dublin"}
        badge_folders = {"dublin": {"id": "fid", "children": [
            {"name": "organizer_badge_dublin_colour.svg"},
            {"name": "organizer_badge_dublin_white.svg"},
        ]}}
        folders, files = sync_badges.plan(chapters, badge_folders, regenerate=False)
        self.assertEqual(folders, {})
        self.assertEqual(files["dublin"], ("fid", [
            "organizer_badge_dublin_colour_1000.png",
            "organizer_badge_dublin_white_1000.png",
        ]))

    def test_complete_chapter_is_left_alone(self):
        chapters = {"dublin": "Dublin"}
        badge_folders = {"dublin": {"id": "fid",
                                     "children": [{"name": n} for n in
                                                  sync_badges.needed_filenames("dublin")]}}
        folders, files = sync_badges.plan(chapters, badge_folders, regenerate=False)
        self.assertEqual(folders, {})
        self.assertEqual(files, {})

    def test_regenerate_reuploads_every_file_even_if_complete(self):
        chapters = {"dublin": "Dublin"}
        badge_folders = {"dublin": {"id": "fid",
                                     "children": [{"name": n} for n in
                                                  sync_badges.needed_filenames("dublin")]}}
        folders, files = sync_badges.plan(chapters, badge_folders, regenerate=True)
        self.assertEqual(files["dublin"], ("fid", sync_badges.needed_filenames("dublin")))


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
