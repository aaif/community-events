#!/usr/bin/env python3
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import luma_sync  # noqa: E402


def view(url):
    return {"details": {"LUMA URL": url}}


class TestFindEventId(unittest.TestCase):
    def test_override_wins_without_lookup(self):
        with mock.patch.object(luma_sync.luma, "resolve_event_id") as r:
            self.assertEqual(luma_sync.find_event_id(view("luma.com/x"), "evt-9"), "evt-9")
            r.assert_not_called()

    def test_empty_cell_aborts_with_guidance(self):
        with self.assertRaises(SystemExit):
            luma_sync.find_event_id(view(""), None)

    def test_event_url_resolves(self):
        with mock.patch.object(luma_sync.luma, "resolve_event_id", return_value="evt-1"):
            self.assertEqual(luma_sync.find_event_id(view("https://luma.com/x"), None),
                             "evt-1")

    def test_calendar_link_aborts(self):
        with mock.patch.object(luma_sync.luma, "resolve_event_id",
                               side_effect=luma_sync.luma.NotAnEventUrl("calendar")):
            with self.assertRaises(SystemExit):
                luma_sync.find_event_id(view("luma.com/aaif-sanfrancisco"), None)

    def test_lookup_failure_aborts_cleanly_not_traceback(self):
        with mock.patch.object(luma_sync.luma, "resolve_event_id",
                               side_effect=luma_sync.luma.LumaError("HTTP 404")):
            with self.assertRaises(SystemExit):
                luma_sync.find_event_id(view("https://luma.com/gone"), None)


class TestUpdateBody(unittest.TestCase):
    def test_notifications_suppressed_by_default(self):
        self.assertEqual(luma_sync.update_body("evt-1"),
                         {"event_id": "evt-1", "suppress_notifications": True})

    def test_notify_guests_is_opt_in(self):
        self.assertEqual(luma_sync.update_body("evt-1", notify_guests=True),
                         {"event_id": "evt-1"})


class TestMainNotifyGuests(unittest.TestCase):
    def test_notify_guests_flag_reaches_update_body(self):
        # Everything outside the parser is faked: the point is that the CLI
        # flag is wired through to update_body (and hence to the request body).
        L = luma_sync.luma
        with mock.patch.object(sys, "argv", ["x", "t.docx", "next", "--timezone", "UTC",
                                             "--event-id", "evt-1", "--apply",
                                             "--notify-guests"]), \
                mock.patch.object(luma_sync.office, "read_document", return_value=None), \
                mock.patch.object(luma_sync.tracker, "read_event", return_value=view("")), \
                mock.patch.object(L, "event_payload", return_value={"name": "B"}), \
                mock.patch.object(L, "available", return_value=True), \
                mock.patch.object(L, "get_event", return_value={"name": "A", "url": "u"}), \
                mock.patch.object(L, "diff_payload", side_effect=[{"name": ("A", "B")}, {}]), \
                mock.patch.object(L, "update_event") as upd, \
                mock.patch.object(luma_sync, "update_body", wraps=luma_sync.update_body) as ub, \
                mock.patch("sys.stdout"):
            luma_sync.main()
        ub.assert_called_once_with("evt-1", True)
        self.assertEqual(upd.call_args[0][0], {"event_id": "evt-1", "name": "B"})


if __name__ == "__main__":
    unittest.main()
