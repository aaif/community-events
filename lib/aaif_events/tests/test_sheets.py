"""Tests for header-name sheet access.

The behaviours worth pinning are the refusals: this module's job is to make a
layout change loud, because the alternative is a write landing in the wrong
column and nothing saying so.
"""

import pytest

from aaif_events import sheets

HEADERS = ["Title", "City", "Country", "Status"]


class TestCell:
    def test_a_value_comes_back_stripped(self):
        assert sheets.cell(["  Boston  "], 0) == "Boston"

    def test_past_the_end_is_blank_not_an_error(self):
        """The API truncates trailing empty cells, so short rows are normal."""
        assert sheets.cell(["Boston"], 5) == ""
        assert sheets.cell([], 0) == ""

    def test_a_negative_index_is_blank_not_a_wraparound(self):
        assert sheets.cell(["a", "b"], -1) == ""

    def test_a_non_string_cell_reads_as_blank(self):
        """One old copy raised here. A report should say blank, not die."""
        assert sheets.cell([42], 0) == ""
        assert sheets.cell([True], 0) == ""
        assert sheets.cell([None], 0) == ""


class TestHeaderIndex:
    def test_indexes_come_back_in_the_order_asked(self):
        assert sheets.header_index(HEADERS, "Tab", "Country", "Title") == [2, 0]

    def test_a_missing_column_aborts(self):
        with pytest.raises(SystemExit, match="not found"):
            sheets.header_index(HEADERS, "Tab", "Nope")

    def test_a_duplicated_column_aborts(self):
        """The gap in one old copy: it silently took the first of the two."""
        with pytest.raises(SystemExit, match="appears twice"):
            sheets.header_index(["City", "City"], "Tab", "City")

    def test_the_abort_names_the_column_and_the_tab(self):
        with pytest.raises(SystemExit, match="'Nope'.*Chapters"):
            sheets.header_index(HEADERS, "Chapters", "Nope")


class TestHeaderMap:
    def test_the_same_lookup_keyed_by_name(self):
        assert sheets.header_map(HEADERS, "Tab", "City", "Status") == {"City": 1, "Status": 3}

    def test_it_refuses_a_duplicate_too(self):
        with pytest.raises(SystemExit, match="appears twice"):
            sheets.header_map(["City", "City"], "Tab", "City")


class TestColLetter:
    def test_single_letters(self):
        assert [sheets.col_letter(i) for i in (0, 1, 25)] == ["A", "B", "Z"]

    def test_the_wrap_into_two_letters(self):
        assert sheets.col_letter(26) == "AA"
        assert sheets.col_letter(27) == "AB"
        assert sheets.col_letter(51) == "AZ"
        assert sheets.col_letter(52) == "BA"

    def test_a_negative_index_raises_rather_than_returning_empty(self):
        """An empty string here would build the range `Tab!:` and read nothing."""
        with pytest.raises(ValueError):
            sheets.col_letter(-1)
