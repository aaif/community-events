import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import check_no_local_redaction as chk  # noqa: E402


def _names(src):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
    try:
        return [n for _, n, _ in chk.offenders(f.name)]
    finally:
        os.unlink(f.name)


class TestIsReserved(unittest.TestCase):
    def test_the_flag_and_its_writers_are_reserved(self):
        for n in ("REDACT", "CI_REDACT_DEFAULT", "set_redaction",
                  "add_redact_flag", "redacting"):
            self.assertTrue(chk.is_reserved(n), n)

    def test_every_masker_is_reserved_by_prefix(self):
        for n in ("redact_email", "redact_name", "redact_id", "redact_text",
                  "redact_doc_text", "redact_names_cell", "redact_anything_new"):
            self.assertTrue(chk.is_reserved(n), n)

    def test_unrelated_names_pass(self):
        for n in ("REDACTED_NOTE", "unredact", "SHOWN_UNDER_REDACT",
                  "redactionist", "mask_sets"):
            self.assertFalse(chk.is_reserved(n), n)


class TestOffenders(unittest.TestCase):
    def test_a_local_flag_is_caught(self):
        self.assertEqual(_names("REDACT = False\n"), ["REDACT"])

    def test_a_local_helper_is_caught(self):
        self.assertEqual(_names("def redact_email(e):\n    return e\n"),
                         ["redact_email"])

    def test_the_whole_old_block_is_caught(self):
        src = ("import os\n"
               "REDACT = False\n"
               "CI_REDACT_DEFAULT = False\n"
               "def redact_email(e):\n    return e\n"
               "def redact_name(n):\n    return n\n"
               "def add_redact_flag(ap):\n    pass\n"
               "def set_redaction(on):\n    pass\n")
        self.assertEqual(len(_names(src)), 6)

    def test_importing_from_the_shared_module_is_the_fix(self):
        src = ("from aaif_events.redact import (add_redact_flag, redact_email,\n"
               "                                redact_name, set_redaction)\n"
               "print(redact_email('a@x.com'))\n")
        self.assertEqual(_names(src), [])

    def test_calling_a_helper_is_not_defining_one(self):
        self.assertEqual(_names("print(redact_name('Ada'))\nset_redaction(True)\n"), [])

    def test_a_nested_name_is_not_module_level(self):
        """A local variable inside a function is not the shared flag."""
        src = ("def report(rows):\n"
               "    REDACT = True\n"
               "    return REDACT\n")
        self.assertEqual(_names(src), [])

    def test_prose_about_the_old_design_does_not_trip_it(self):
        """The AST pass is why the docstrings explaining this can stay."""
        src = ('"""Each script used to carry REDACT and set_redaction itself."""\n'
               "# def redact_email(e): ...  (moved to aaif_events.redact)\n"
               "x = 1\n")
        self.assertEqual(_names(src), [])

    def test_the_canonical_module_is_allowed_to_define_them(self):
        path = os.path.join("lib", "aaif_events", "redact.py")
        self.assertEqual(chk.offenders(path), [])

    def test_a_syntax_error_is_reported_not_swallowed(self):
        """An unparsable file is a file this guard could not clear."""
        self.assertEqual(_names("def broken(:\n"), ["<syntax error>"])


class TestRepo(unittest.TestCase):
    def test_the_tracked_tree_is_clean(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cwd = os.getcwd()
        os.chdir(root)
        try:
            self.assertEqual(chk.main(["check_no_local_redaction.py"]), 0)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
