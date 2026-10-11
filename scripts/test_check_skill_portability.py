import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import check_skill_portability as chk  # noqa: E402


class TestSkillPortability(unittest.TestCase):
    def test_portable_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "# Portable\n\n"
                "Paths in this skill are relative to this skill directory. "
                "Resolve `<skill-root>`\nfrom the loaded `SKILL.md`; it is a placeholder.\n\n"
                "Run `python3 <skill-root>/scripts/run.py`.\n",
                encoding="utf-8",
            )
            self.assertEqual(chk.check_skill(skill), [])

    def test_claude_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "Run `${CLAUDE_SKILL_DIR}/scripts/run.py`.\n", encoding="utf-8"
            )
            self.assertTrue(any("CLAUDE_SKILL_DIR" in error
                                for error in chk.check_skill(skill)))

    def test_repo_root_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "Run `python3 skills/other/scripts/run.py`.\n", encoding="utf-8"
            )
            self.assertTrue(any("repo-root" in error
                                for error in chk.check_skill(skill)))

    def test_repo_root_pythonpath_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "Run `PYTHONPATH=lib python3 -c 'import aaif_events'`.\n",
                encoding="utf-8")
            self.assertTrue(any("repo-root" in error
                                for error in chk.check_skill(skill)))

    def test_sibling_skill_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "Paths in this skill are relative to this skill directory. "
                "Resolve `<skill-root>`\n\n"
                "Run `python3 <skill-root>/../other/scripts/run.py` and "
                "`PYTHONPATH=<skill-root>/../../lib python3 x.py`.\n",
                encoding="utf-8",
            )
            self.assertEqual(chk.check_skill(skill), [])

    def test_placeholder_must_be_defined_in_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "portable"
            (skill / "references").mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Portable\n", encoding="utf-8")
            (skill / "references" / "WORKFLOW.md").write_text(
                "Run `<skill-root>/scripts/run.py`.\n", encoding="utf-8"
            )
            self.assertTrue(any("define `<skill-root>`" in error
                                for error in chk.check_skill(skill)))


if __name__ == "__main__":
    unittest.main()
