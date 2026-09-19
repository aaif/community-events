"""Unit tests for fetch_tracker: the pure logic only — no Drive, no network.

The Drive half (`find_folder`, `find_tracker`, `download`) is a thin shell over
`aaif_events.gws`, which has its own tests. What is worth pinning here is the
part that decides what reaches the agent's context: the query escaping, and the
contact-field withholding that keeps an address out of a post.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..", "..", "..", "lib")))
sys.path.insert(0, os.path.dirname(__file__))
import fetch_tracker  # noqa: E402
from aaif_events import redact  # noqa: E402


class TestQuoting(unittest.TestCase):
    def test_apostrophe_is_escaped(self):
        # A real chapter name with an apostrophe would otherwise close the
        # Drive `q` string literal and change the query's meaning.
        self.assertEqual(fetch_tracker._quote("Val d'Or"), "Val d\\'Or")

    def test_backslash_is_escaped_first(self):
        self.assertEqual(fetch_tracker._quote("a\\b"), "a\\\\b")

    def test_plain_name_is_untouched(self):
        self.assertEqual(fetch_tracker._quote("New York City"), "New York City")


class TestDriveQueries(unittest.TestCase):
    """Two params decide whether a lookup can see a shared drive at all, and a
    listing that comes back empty is indistinguishable from `no such folder` —
    it would report a chapter that exists as missing."""

    def _capture(self, files):
        seen = {}

        def fake(*args, **kw):
            seen["args"], seen["params"] = args, kw.get("params")
            return {"files": files}

        return fake, seen

    def setUp(self):
        self._json_out = fetch_tracker.gws.json_out

    def tearDown(self):
        fetch_tracker.gws.json_out = self._json_out

    def test_folder_lookup_scopes_to_shared_drives_and_folders(self):
        fake, seen = self._capture([{"id": "F1", "name": "Boston"}])
        fetch_tracker.gws.json_out = fake
        fid, mode = fetch_tracker.find_folder("Boston")
        self.assertEqual((fid, mode), ("F1", "chapter"))
        self.assertTrue(seen["params"]["supportsAllDrives"])
        self.assertTrue(seen["params"]["includeItemsFromAllDrives"])
        # A file sharing the folder's name must not be returned as the folder.
        self.assertIn(fetch_tracker.FOLDER_MIME, seen["params"]["q"])

    def test_tracker_lookup_scopes_to_shared_drives(self):
        fake, seen = self._capture([{"id": "T1", "name": "Event Tracker.docx"}])
        fetch_tracker.gws.json_out = fake
        self.assertEqual(fetch_tracker.find_tracker("F1"), "T1")
        self.assertTrue(seen["params"]["supportsAllDrives"])

    def test_two_folders_of_one_name_abort_rather_than_pick(self):
        fake, _ = self._capture([{"id": "A"}, {"id": "B"}])
        fetch_tracker.gws.json_out = fake
        with self.assertRaises(SystemExit):
            fetch_tracker.find_folder("Boston")

    def test_missing_folder_names_the_fix(self):
        fake, _ = self._capture([])
        fetch_tracker.gws.json_out = fake
        with self.assertRaises(SystemExit) as cm:
            fetch_tracker.find_folder("NYC")
        self.assertIn("exactly", str(cm.exception))


class TestEventFields(unittest.TestCase):
    """`event_fields` is exercised through its own filtering logic by stubbing
    the two library calls it makes; parsing a .docx is `aaif_events.office`'s
    job and is tested there."""

    def setUp(self):
        self._read_document = fetch_tracker.office.read_document
        self._read_event = fetch_tracker.tracker.read_event
        fetch_tracker.office.read_document = lambda p: "ROOT"

    def tearDown(self):
        fetch_tracker.office.read_document = self._read_document
        fetch_tracker.tracker.read_event = self._read_event

    def _stub(self, details, title="Agentic AI Night", date=None):
        fetch_tracker.tracker.read_event = lambda root, ev: {
            "title": title, "details": details, "phases": [], "date": date}

    def test_requested_fields_are_returned_and_blanks_are_kept(self):
        self._stub({"EVENT TITLE": "Agentic AI Night", "VENUE": "Somewhere"})
        got = fetch_tracker.event_fields("x.docx", "next")
        self.assertEqual(got["VENUE"], "Somewhere")
        # A field the tracker does not carry reports blank rather than vanishing,
        # so the agent can see it was asked for and is missing.
        self.assertEqual(got["THEME"], "")

    def test_contact_fields_are_withheld_and_named(self):
        self._stub({"EVENT TITLE": "T", "SPEAKER EMAIL": "a@x.com",
                    "DOOR CODE": "1234"})
        got = fetch_tracker.event_fields("x.docx", "next")
        self.assertNotIn("SPEAKER EMAIL", got)
        self.assertNotIn("DOOR CODE", got)
        # Withheld, but *named*: an agent that needs one knows to ask a human
        # rather than concluding the tracker is empty.
        self.assertEqual(got["_withheld"], ["DOOR CODE", "SPEAKER EMAIL"])

    def test_blank_contact_field_is_not_reported_as_withheld(self):
        self._stub({"EVENT TITLE": "T", "SPEAKER EMAIL": ""})
        self.assertNotIn("_withheld", fetch_tracker.event_fields("x.docx", "next"))

    def test_all_fields_includes_contact_details_and_no_withheld_note(self):
        self._stub({"EVENT TITLE": "T", "SPEAKER EMAIL": "a@x.com"})
        got = fetch_tracker.event_fields("x.docx", "next", all_fields=True)
        self.assertEqual(got["SPEAKER EMAIL"], "a@x.com")
        self.assertNotIn("_withheld", got)


class TestMask(unittest.TestCase):
    def setUp(self):
        self._was = redact.REDACT
        redact.REDACT = True

    def tearDown(self):
        redact.REDACT = self._was

    def test_speaker_names_are_masked(self):
        out = fetch_tracker._mask({"SPEAKERS": "Ada Lovelace"})
        self.assertNotIn("Lovelace", out["SPEAKERS"])

    def test_address_is_masked(self):
        out = fetch_tracker._mask({"CONTACT": "a@x.com"})
        self.assertNotIn("a@x.com", out["CONTACT"])

    def test_non_string_values_survive(self):
        out = fetch_tracker._mask({"date": None, "_withheld": ["DOOR CODE"]})
        self.assertIsNone(out["date"])
        self.assertEqual(out["_withheld"], ["DOOR CODE"])


if __name__ == "__main__":
    unittest.main()
