import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 "..", "..", "..", "lib")))
sys.path.insert(0, os.path.dirname(__file__))
import update_event  # noqa: E402
from aaif_events import office, tracker  # noqa: E402

FIX = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                   "lib", "aaif_events", "tests", "fixtures",
                                   "event_tracker_irl.docx"))


class TestApplyChanges(unittest.TestCase):
    def setUp(self):
        self.root = office.read_document(FIX)

    def test_set_speaker_flags_stale(self):
        stale = update_event.apply_changes(
            self.root, "Agentic AI Night", ["SPEAKER(S)=Jane Doe (Infra)"], None)
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["details"]["SPEAKER(S)"], "Jane Doe (Infra)")
        self.assertIn("speaker bio", stale)

    def test_date_move_restamps_using_original_date(self):
        # original 4-weeks-out due "May 27"; move +14d to Jul 8 -> "Jun 10"
        stale = update_event.apply_changes(
            self.root, "Agentic AI Night", [], "Wed · July 8, 2026 · 17:30 — late")
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["phases"][0]["tasks"][0].due, "Jun 10")
        self.assertEqual(ev["details"]["DATE & TIME"], "Wed · July 8, 2026 · 17:30 — late")
        self.assertEqual(ev["date"], dt.date(2026, 7, 8))
        self.assertIn("square banner", stale)

    def test_set_and_date_together(self):
        stale = update_event.apply_changes(
            self.root, "Agentic AI Night",
            ["SPEAKER(S)=Jane Doe"], "Wed · July 8, 2026 · 17:30 — late")
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["details"]["SPEAKER(S)"], "Jane Doe")
        self.assertEqual(ev["phases"][0]["tasks"][0].due, "Jun 10")
        # stale set is the union of speaker- and date-driven assets
        self.assertIn("speaker bio", stale)
        self.assertIn("Luma cover", stale)

    def test_set_without_equals_raises(self):
        with self.assertRaises(ValueError):
            update_event.apply_changes(self.root, "Agentic AI Night", ["SPEAKER(S)"], None)

    def test_set_venue_flags_stale(self):
        stale = update_event.apply_changes(
            self.root, "Agentic AI Night", ["VENUE=The Foundry, 2nd floor"], None)
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["details"]["VENUE"], "The Foundry, 2nd floor")
        self.assertIn("announcement post", stale)
        self.assertIn("attendee reminder", stale)

    def test_set_date_field_is_refused(self):
        # a bare DATE & TIME write would silently skip the due-date recompute —
        # it must abort and point at --date instead.
        with self.assertRaises(ValueError) as ctx:
            update_event.apply_changes(
                self.root, "Agentic AI Night",
                ["DATE & TIME=Wed · July 8, 2026 · 17:30 — late"], None)
        self.assertIn("--date", str(ctx.exception))
        # nothing was written
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertNotIn("July 8", ev["details"]["DATE & TIME"])


if __name__ == "__main__":
    unittest.main()
