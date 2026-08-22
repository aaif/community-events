import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import check_no_secret_args as chk  # noqa: E402


def _lines(src):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
    try:
        return [n for n, _ in chk.offenders(f.name)]
    finally:
        os.unlink(f.name)


class TestIsSecretFlag(unittest.TestCase):
    def test_whole_words_hit(self):
        for n in ("--token", "--api-key", "--api_key", "--apikey", "--secret",
                  "--password", "--passwd", "--credentials", "--auth", "--bearer",
                  "--xoxb", "--slack-token", "--secret-sauce-mode", "token"):
            self.assertTrue(chk.is_secret_flag(n), n)

    def test_substrings_do_not_hit(self):
        for n in ("--tokenize", "--keyboard", "--author", "--keys-dir", "--write",
                  "--passthrough", "--secretary"):
            self.assertFalse(chk.is_secret_flag(n), n)


class TestOffenders(unittest.TestCase):
    def test_single_line_token(self):
        self.assertEqual(_lines('import argparse\nap = argparse.ArgumentParser()\n'
                                'ap.add_argument("--token")\n'), [3])

    def test_short_flag_first(self):
        self.assertEqual(_lines('ap.add_argument("-t", "--api-key", help="x")\n'), [1])

    def test_dest_keyword(self):
        self.assertEqual(_lines('ap.add_argument("--t", dest="token")\n'), [1])

    def test_multi_line_call(self):
        src = ('ap.add_argument(\n'
               '    "--slack",\n'
               '    "--secret",\n'
               '    help="x")\n')
        self.assertEqual(_lines(src), [1])

    def test_tokenize_is_clean(self):
        self.assertEqual(_lines('ap.add_argument("--tokenize", action="store_true")\n'), [])

    def test_clean_file_returns_zero(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write('import argparse\nap = argparse.ArgumentParser()\n'
                    'ap.add_argument("--write", action="store_true")\n')
        try:
            self.assertEqual(chk.main(["x", f.name]), 0)
        finally:
            os.unlink(f.name)

    def test_dirty_file_returns_one(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write('ap.add_argument("--password")\n')
        try:
            self.assertEqual(chk.main(["x", f.name]), 1)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
