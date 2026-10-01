import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import check_frontmatter as chk  # noqa: E402


def write_skill(root: Path, name: str, frontmatter: str, body: str = "Do the work.\n") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


class TestFrontmatter(unittest.TestCase):
    def test_valid_portable_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(
                Path(tmp),
                "example-skill",
                "name: example-skill\n"
                "description: Do a specific task when the user asks for it.\n"
                "compatibility: Requires Python 3.\n"
                "metadata:\n"
                "  com.example.argument-hint: '[input]'",
            )
            self.assertEqual(chk.check(str(path)), [])

    def test_client_field_must_live_in_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(
                Path(tmp),
                "example-skill",
                "name: example-skill\n"
                "description: Do a task when asked.\n"
                "compatibility: Requires Python 3.\n"
                "argument-hint: '[input]'",
            )
            self.assertTrue(any("unsupported" in error for error in chk.check(str(path))))

    def test_compatibility_is_required_by_repo_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(
                Path(tmp), "example-skill",
                "name: example-skill\ndescription: Do a task when asked.",
            )
            self.assertTrue(any("compatibility" in error for error in chk.check(str(path))))

    def test_name_must_match_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(
                Path(tmp), "example-skill",
                "name: another-skill\n"
                "description: Do a task when asked.\n"
                "compatibility: Requires Python 3.",
            )
            self.assertTrue(any("parent directory" in error for error in chk.check(str(path))))

    def test_metadata_values_must_be_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_skill(
                Path(tmp), "example-skill",
                "name: example-skill\n"
                "description: Do a task when asked.\n"
                "compatibility: Requires Python 3.\n"
                "metadata:\n  version: 1",
            )
            self.assertTrue(any("must all be strings" in error for error in chk.check(str(path))))


if __name__ == "__main__":
    unittest.main()
