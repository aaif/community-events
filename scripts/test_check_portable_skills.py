import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import check_portable_skills as chk  # noqa: E402

CAVEAT = """- **claude.ai** — zip a skill folder and upload it. **Caveat:** skills whose
  scripts import the shared `lib/aaif_events` package do **not** work zipped
  standalone. Those are `aaif-audit-slack` and `aaif-sync-chapters`.

Next paragraph, naming `aaif-unrelated`, must not be read as part of the list.
"""


class TestDocumentedSkills(unittest.TestCase):
    def test_the_named_skills_are_found(self):
        self.assertEqual(chk.documented_skills(CAVEAT),
                         {"aaif-audit-slack", "aaif-sync-chapters"})

    def test_the_list_stops_at_the_paragraph_end(self):
        """A skill named further down the README is not part of the caveat."""
        self.assertNotIn("aaif-unrelated", chk.documented_skills(CAVEAT))

    def test_a_missing_caveat_aborts_rather_than_passing_vacuously(self):
        """An empty documented set would silently "agree" with nothing."""
        with self.assertRaises(SystemExit):
            chk.documented_skills("A README with no caveat in it at all.")

    def test_a_reflowed_paragraph_still_matches(self):
        """The marker is prose; prose gets re-wrapped. That is not a failure."""
        wrapped = CAVEAT.replace("work zipped\n  standalone", "work\n  zipped standalone")
        self.assertEqual(chk.documented_skills(wrapped),
                         {"aaif-audit-slack", "aaif-sync-chapters"})


class TestCompare(unittest.TestCase):
    """The decision itself, driven with synthetic sets.

    Without these, both error branches were unreachable: `main()` could be
    replaced with `return 0` and all eight tests passed.
    """

    def test_agreement_is_no_findings(self):
        self.assertEqual(chk.compare({"a", "b"}, {"a", "b"}), ([], []))

    def test_an_undocumented_import_is_reported(self):
        self.assertEqual(chk.compare({"a", "b"}, {"a"}), (["b"], []))

    def test_a_skill_that_stopped_importing_is_reported(self):
        self.assertEqual(chk.compare({"a"}, {"a", "b"}), ([], ["b"]))

    def test_both_directions_at_once(self):
        self.assertEqual(chk.compare({"a", "c"}, {"a", "b"}), (["c"], ["b"]))

    def test_empty_on_both_sides_is_agreement(self):
        self.assertEqual(chk.compare(set(), set()), ([], []))


class TestRepo(unittest.TestCase):
    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)

    def test_coupled_skills_are_detected_from_the_scripts(self):
        found = chk.coupled_skills()
        self.assertIn("aaif-sync-chapters", found)
        self.assertIn("aaif-audit-slack", found)

    def test_a_skill_with_no_scripts_is_not_coupled(self):
        self.assertNotIn("aaif-speaker-bio", chk.coupled_skills())

    def test_tests_do_not_make_a_skill_coupled(self):
        """A test already runs from the checkout, so its import costs nothing."""
        self.assertNotIn("aaif-triage-intake", chk.coupled_skills())

    def test_the_readme_and_the_code_agree(self):
        self.assertEqual(chk.main(), 0)


if __name__ == "__main__":
    unittest.main()
