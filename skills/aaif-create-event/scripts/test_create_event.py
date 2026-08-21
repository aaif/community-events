import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 "..", "..", "..", "lib")))
sys.path.insert(0, os.path.dirname(__file__))
import create_event  # noqa: E402
from aaif_events import office, tracker  # noqa: E402

FIX = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                   "lib", "aaif_events", "tests", "fixtures",
                                   "event_tracker_irl.docx"))


class TestCreateCore(unittest.TestCase):
    def test_apply_adds_event_to_local_docx(self):
        with tempfile.TemporaryDirectory() as d:
            local = os.path.join(d, "t.docx")
            shutil.copy(FIX, local)
            create_event.apply_local(local, {
                "EVENT TITLE": "Eval Night",
                "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
            }, dt.date(2026, 8, 12), office.read_document(local))
            root = office.read_document(local)
            titles = [e["title"] for e in tracker.list_events(root)]
            self.assertIn("Eval Night", titles)

    def test_bad_label_raises_not_silent(self):
        # PLATFORM is absent on the IRL tracker -> must raise (no silent drop).
        with tempfile.TemporaryDirectory() as d:
            local = os.path.join(d, "t.docx")
            shutil.copy(FIX, local)
            with self.assertRaises(LookupError):
                create_event.apply_local(local, {
                    "EVENT TITLE": "X",
                    "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
                    "PLATFORM": "Zoom",
                }, dt.date(2026, 8, 12), office.read_document(local))

    def test_title_exists_is_exact_not_substring(self):
        root = office.read_document(FIX)
        tracker.add_event(root, {
            "EVENT TITLE": "Eval Night · Builder Series",
            "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
        }, dt.date(2026, 8, 12))
        # exact existing title -> exists; a distinct shorter title -> does not
        self.assertTrue(create_event.title_exists(root, "Eval Night · Builder Series"))
        self.assertFalse(create_event.title_exists(root, "Eval Night"))


if __name__ == "__main__":
    unittest.main()
