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
