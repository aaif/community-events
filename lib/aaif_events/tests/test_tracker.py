import datetime as dt
import os
import unittest

from aaif_events import office, tracker

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "event_tracker_irl.docx")
FIX_ONLINE = os.path.join(os.path.dirname(__file__), "fixtures", "event_tracker_online.docx")


class TestDates(unittest.TestCase):
    def test_parse_event_date(self):
        self.assertEqual(
            tracker.parse_event_date("Tue · June 24, 2026 · 17:30 — late"),
            dt.date(2026, 6, 24))

    def test_parse_due_infers_year(self):
        anchor = dt.date(2026, 6, 24)
        self.assertEqual(tracker.parse_due("May 27", anchor), dt.date(2026, 5, 27))
        self.assertEqual(tracker.parse_due("Jun 3", anchor), dt.date(2026, 6, 3))

    def test_parse_due_skips_clock_and_blank(self):
        anchor = dt.date(2026, 6, 24)
        self.assertIsNone(tracker.parse_due("16:00", anchor))
        self.assertIsNone(tracker.parse_due("", anchor))

    def test_restamp_shifts_dates_keeps_clock(self):
        old, new = dt.date(2026, 6, 24), dt.date(2026, 7, 8)  # +14 days
        self.assertEqual(tracker.restamp("May 27", old, new), "Jun 10")
        self.assertEqual(tracker.restamp("16:00", old, new), "16:00")
        self.assertEqual(tracker.restamp("", old, new), "")


class TestEventModel(unittest.TestCase):
    def setUp(self):
        self.root = office.read_document(FIX)

    def test_list_events_finds_the_example(self):
        evs = tracker.list_events(self.root)
        self.assertEqual(len(evs), 1)
        self.assertIn("Agentic AI Night", evs[0]["title"])
        # 4wk,3wk,2wk,1wk,day-before,day-of,next-day,follow-ups
        self.assertEqual(len(evs[0]["phase_tables"]), 8)

    def test_read_event_details_and_tasks(self):
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["details"]["DATE & TIME"], "Tue · June 24, 2026 · 17:30 — late")
        self.assertEqual(ev["date"], dt.date(2026, 6, 24))
        self.assertEqual(ev["phases"][0]["tasks"][0].status, "Done")

    def test_read_event_next(self):
        ev = tracker.read_event(self.root, "next")
        self.assertIn("Agentic AI Night", ev["title"])

    def test_select_refuses_duplicate_identical_titles(self):
        # Two events carrying the very same title: an exact match is still
        # ambiguous, and a write must never silently land on the wrong one.
        events = [{"title": "Agentic AI Night", "date": dt.date(2026, 6, 24)},
                  {"title": "Agentic AI Night", "date": dt.date(2026, 9, 15)}]
        with self.assertRaises(LookupError) as cm:
            tracker._select(events, "Agentic AI Night")
        self.assertIn("identical titles", str(cm.exception))


class TestWrites(unittest.TestCase):
    def setUp(self):
        self.root = office.read_document(FIX)

    def test_set_field(self):
        tracker.set_field(self.root, "Agentic AI Night", "SPEAKER(S)", "Jane Doe (Infra)")
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["details"]["SPEAKER(S)"], "Jane Doe (Infra)")

    def test_set_due_dates_shifts_two_weeks(self):
        # original 4-weeks-out task due "May 27"; +14 days -> "Jun 10"
        changed = tracker.set_due_dates(self.root, "Agentic AI Night", dt.date(2026, 7, 8))
        self.assertGreater(changed, 0)
        ev = tracker.read_event(self.root, "Agentic AI Night")
        self.assertEqual(ev["phases"][0]["tasks"][0].due, "Jun 10")
        # day-of clock times unchanged
        dayof = ev["phases"][5]["tasks"][0].due
        self.assertRegex(dayof, r"^\d{1,2}:\d{2}$")


class TestAddEvent(unittest.TestCase):
    def setUp(self):
        self.root = office.read_document(FIX)

    def test_add_event_appends_section(self):
        before = len(tracker.list_events(self.root))
        tracker.add_event(self.root, {
            "EVENT TITLE": "Eval Night · Builder Series",
            "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
            "SPEAKER(S)": "TBD",
        }, dt.date(2026, 8, 12))
        evs = tracker.list_events(self.root)
        self.assertEqual(len(evs), before + 1)
        new = tracker.read_event(self.root, "Eval Night")
        self.assertEqual(new["details"]["EVENT TITLE"], "Eval Night · Builder Series")
        # statuses reset
        self.assertTrue(all(t.status == "Not started"
                            for ph in new["phases"] for t in ph["tasks"]))
        # dates restamped to the new event date (4-wks-out is ~28 days before Aug 12)
        self.assertNotEqual(new["phases"][0]["tasks"][0].due, "May 27")
        # the appended section survives a save -> reload (no corruption)
        import tempfile
        with tempfile.TemporaryDirectory() as dd:
            out = os.path.join(dd, "out.docx")
            office.save_document(FIX, self.root, out)
            reloaded = office.read_document(out)
            self.assertEqual(len(tracker.list_events(reloaded)), before + 1)


def _two_event_root():
    """The IRL fixture plus a second event titled exactly 'AI Night'."""
    root = office.read_document(FIX)
    tracker.add_event(root, {"EVENT TITLE": "AI Night",
                             "DATE & TIME": "Wed · September 9, 2026 · 18:00 — late"},
                      dt.date(2026, 9, 9))
    return root


class TestSelection(unittest.TestCase):
    def test_exact_match_beats_substring(self):
        # 'AI Night' is a substring of 'Agentic AI Night · Launch Series', but an
        # exact title match must win.
        ev = tracker.read_event(_two_event_root(), "AI Night")
        self.assertEqual(ev["details"]["EVENT TITLE"], "AI Night")

    def test_ambiguous_substring_raises(self):
        # 'night' matches both titles -> must raise, never silently pick one.
        with self.assertRaises(LookupError):
            tracker.read_event(_two_event_root(), "night")

    def test_latest_picks_max_date(self):
        ev = tracker.read_event(_two_event_root(), "latest")
        self.assertEqual(ev["date"], dt.date(2026, 9, 9))

    def test_unknown_event_raises(self):
        with self.assertRaises(LookupError):
            tracker.read_event(office.read_document(FIX), "no such event")


class TestDateEdges(unittest.TestCase):
    def test_parse_event_date_requires_year(self):
        with self.assertRaises(ValueError):
            tracker.parse_event_date("January 15")

    def test_parse_due_crosses_year_boundary(self):
        self.assertEqual(tracker.parse_due("Jan 2", dt.date(2025, 12, 28)),
                         dt.date(2026, 1, 2))
        self.assertEqual(tracker.parse_due("Dec 30", dt.date(2026, 1, 3)),
                         dt.date(2025, 12, 30))

    def test_restamp_crosses_year_boundary(self):
        self.assertEqual(
            tracker.restamp("Dec 30", dt.date(2025, 12, 31), dt.date(2026, 1, 5)),
            "Jan 4")


class TestWriteGuards(unittest.TestCase):
    def test_set_field_missing_label_raises(self):
        with self.assertRaises(LookupError):
            tracker.set_field(office.read_document(FIX), "Agentic AI Night",
                              "NO SUCH LABEL", "x")

    def test_add_event_unmatched_label_raises(self):
        # VENUE does not exist on the online tracker -> must raise, not drop silently.
        root = office.read_document(FIX_ONLINE)
        with self.assertRaises(LookupError):
            tracker.add_event(root, {"EVENT TITLE": "X",
                                     "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
                                     "VENUE": "Nowhere"}, dt.date(2026, 8, 12))


class TestAddEventHeading(unittest.TestCase):
    def test_heading_rewritten_and_caption_dropped(self):
        root = office.read_document(FIX)
        tracker.add_event(root, {"EVENT TITLE": "Eval Night",
                                 "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late"},
                          dt.date(2026, 8, 12))
        paras = [office.para_text(p) for p in root.iter(office.W + "p")]
        # new heading present (upper-cased title + new date)
        self.assertTrue(any("EVAL NIGHT" in t and "August 12, 2026" in t for t in paras))
        # the example caption was NOT duplicated into the new section (still exactly one)
        captions = [t for t in paras if "duplicate this whole section" in t.lower()]
        self.assertEqual(len(captions), 1)


class TestOnlineFixture(unittest.TestCase):
    def test_online_detail_labels(self):
        ev = tracker.read_event(office.read_document(FIX_ONLINE), "Agentic AI Night")
        self.assertIn("PLATFORM", ev["details"])
        self.assertIn("STREAM / JOIN LINK", ev["details"])
        self.assertNotIn("VENUE", ev["details"])
