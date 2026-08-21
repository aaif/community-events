import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(__file__))
import intake  # noqa: E402


def _digest_of(rec):
    data = {"Organizers": [rec], "Hosts": [], "Speakers": []}
    buf = io.StringIO()
    with redirect_stdout(buf):
        intake.text_digest(data)
    return buf.getvalue()


BASE = {"row": 2, "status": "New", "Full name": "Ada", "Email": "ada@x.com"}


class TestDigestCity(unittest.TestCase):
    def test_shows_city_new_when_present(self):
        out = _digest_of({**BASE, "City (Existing)": "Other", "City (New)": "Berlin"})
        self.assertIn("Berlin", out)
        self.assertNotIn("Other", out)

    def test_falls_back_to_city_existing_when_new_blank(self):
        out = _digest_of({**BASE, "City (Existing)": "Paris", "City (New)": ""})
        self.assertIn("Paris", out)

    def test_city_not_double_printed_in_detail_block(self):
        out = _digest_of({**BASE, "City (Existing)": "Paris", "City (New)": ""})
        self.assertEqual(out.count("Paris"), 1)


class TestDigestLabel(unittest.TestCase):
    def test_headline_names_the_selected_population(self):
        # Under --all or --status Accepted, "awaiting review" would misdescribe
        # every count on the line; the label follows the active filter.
        data = {"Organizers": [dict(BASE)], "Hosts": [], "Speakers": []}
        buf = io.StringIO()
        with redirect_stdout(buf):
            intake.text_digest(data, "with status Accepted")
        self.assertIn("1 with status Accepted", buf.getvalue())
        self.assertNotIn("awaiting review", buf.getvalue())

    def test_default_label_is_awaiting_review(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            intake.text_digest({"Organizers": [], "Hosts": [], "Speakers": []})
        self.assertIn("0 awaiting review", buf.getvalue())


class TestLegacyAliases(unittest.TestCase):
    def test_new_headers_map_to_legacy(self):
        self.assertEqual(intake.LEGACY_ALIASES["City (Existing)"], "City")
        self.assertEqual(intake.LEGACY_ALIASES["City (New)"], "Resolved City")


class CollectBase(unittest.TestCase):
    """collect() is the selection logic the whole skill hangs on — drive it with
    fetch() stubbed per tab; Hosts/Speakers stay empty unless a test fills them."""

    def _collect(self, sheets, status_filter=None, show_all=False):
        orig = intake.fetch
        intake.fetch = lambda tab: sheets.get(tab, ([], []))
        try:
            return intake.collect(status_filter if status_filter is not None
                                  else intake.DEFAULT_NEEDS_REVIEW, show_all)
        finally:
            intake.fetch = orig

    HDR = ["Timestamp", "Status", "Full name", "Email"]

    def _org(self, rows, hdr=None):
        return {"Organizers": (hdr or self.HDR, rows)}


class TestCollectStatusFilter(CollectBase):
    ROWS = [["t1", "", "Ada", "a@x.com"],            # blank status = New
            ["t2", "New", "Grace", "g@x.com"],
            ["t3", "In progress", "Joan", "j@x.com"],
            ["t4", "Accepted", "Mary", "m@x.com"],
            ["", "New", "ghost", ""]]                # no Timestamp -> not a row

    def test_default_filter_takes_blank_new_and_in_progress(self):
        got = self._collect(self._org(self.ROWS))["Organizers"]
        self.assertEqual([r["Full name"] for r in got], ["Ada", "Grace", "Joan"])

    def test_blank_status_is_normalized_to_new(self):
        got = self._collect(self._org(self.ROWS))["Organizers"]
        self.assertEqual(got[0]["status"], "New")

    def test_a_custom_new_filter_still_includes_blank_status_rows(self):
        # The regression: filtering on "New" alone used to drop blank-status
        # rows, even though a blank Status IS New per the skill's status model.
        got = self._collect(self._org(self.ROWS), status_filter={"New"})["Organizers"]
        self.assertEqual([r["Full name"] for r in got], ["Ada", "Grace"])

    def test_a_custom_filter_without_new_excludes_blank_rows(self):
        got = self._collect(self._org(self.ROWS),
                            status_filter={"In progress"})["Organizers"]
        self.assertEqual([r["Full name"] for r in got], ["Joan"])

    def test_show_all_ignores_the_filter_but_not_the_timestamp_marker(self):
        got = self._collect(self._org(self.ROWS), show_all=True)["Organizers"]
        self.assertEqual(len(got), 4)                  # ghost row still skipped
        self.assertEqual(got[3]["status"], "Accepted")

    def test_explicit_blank_status_selects_blank_rows(self):
        # The regression: --status "" used to silently select zero rows, because
        # blank cells normalize to "New" before the filter ever sees them. The
        # filter must be normalized the same way.
        got = self._collect(self._org(self.ROWS),
                            status_filter=intake.normalize_filter([""]))["Organizers"]
        self.assertEqual([r["Full name"] for r in got], ["Ada", "Grace"])

    def test_normalize_filter_maps_blank_to_new_and_keeps_the_rest(self):
        self.assertEqual(intake.normalize_filter(["", "  ", "Accepted"]),
                         {"New", "Accepted"})

    def test_row_numbers_are_sheet_rows(self):
        got = self._collect(self._org(self.ROWS), status_filter={"Accepted"})["Organizers"]
        self.assertEqual([r["row"] for r in got], [5])  # row 2 = first data row


class TestCollectLegacyAliases(CollectBase):
    def test_falls_back_to_legacy_city_headers(self):
        hdr = ["Timestamp", "Status", "Full name", "Email", "City", "Resolved City"]
        rows = [["t", "New", "Ada", "a@x.com", "Other", "Boston"]]
        got = self._collect(self._org(rows, hdr))["Organizers"]
        self.assertEqual(got[0]["City (Existing)"], "Other")
        self.assertEqual(got[0]["City (New)"], "Boston")

    def test_new_headers_win_when_present(self):
        hdr = ["Timestamp", "Status", "Full name", "Email",
               "City (Existing)", "City (New)"]
        rows = [["t", "New", "Ada", "a@x.com", "Boston", ""]]
        got = self._collect(self._org(rows, hdr))["Organizers"]
        self.assertEqual(got[0]["City (Existing)"], "Boston")


class TestCollectAborts(CollectBase):
    def test_missing_timestamp_header_aborts(self):
        # A rename is not an empty queue; "0 awaiting review" must not print.
        hdr = ["When", "Status", "Full name", "Email"]
        with self.assertRaises(SystemExit) as e:
            self._collect(self._org([["t", "New", "Ada", "a@x.com"]], hdr))
        self.assertIn("Timestamp", str(e.exception))

    def test_missing_status_header_aborts_unless_show_all(self):
        hdr = ["Timestamp", "Full name", "Email"]
        sheets = self._org([["t", "Ada", "a@x.com"]], hdr)
        with self.assertRaises(SystemExit) as e:
            self._collect(sheets)
        self.assertIn("Status", str(e.exception))
        got = self._collect(sheets, show_all=True)["Organizers"]
        self.assertEqual(got[0]["status"], "New")      # no column reads as New

    def test_an_empty_tab_is_an_empty_queue_not_an_abort(self):
        self.assertEqual(self._collect({})["Organizers"], [])


if __name__ == "__main__":
    unittest.main()
