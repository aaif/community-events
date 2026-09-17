"""Tests for the shared redaction helpers.

The masking rules themselves are simple; the test that matters is
`test_flag_and_helpers_are_one_module`, which pins the invariant the old
per-script copies existed to protect. See the module docstring in `redact.py`
for the bug that invariant came from.
"""

import argparse

import pytest

from aaif_events import redact


@pytest.fixture(autouse=True)
def _flag_off():
    """Leave the module-global flag as we found it, whatever a test does."""
    before = redact.REDACT
    redact.REDACT = False
    yield
    redact.REDACT = before


def test_off_by_default_everything_passes_through():
    assert redact.redact_email("ada@example.com") == "ada@example.com"
    assert redact.redact_name("Ada Lovelace") == "Ada Lovelace"
    assert redact.redact_id("U0AAAAAAAAA") == "U0AAAAAAAAA"
    assert redact.redact_text("a sentence") == "a sentence"
    assert redact.redacting() is False


def test_email_keeps_one_char_and_the_tld():
    redact.set_redaction(True)
    assert redact.redact_email("ada@example.com") == "a***@***.com"
    assert redact.redact_email("ada@y.example") == "a***@***.example"


def test_email_without_a_dotted_domain_loses_the_tld_too():
    redact.set_redaction(True)
    assert redact.redact_email("ada@localhost") == "a***@***.***"


def test_name_is_a_single_upper_cased_initial():
    redact.set_redaction(True)
    assert redact.redact_name("ada lovelace") == "A."
    assert redact.redact_name("  ada  ") == "A."


def test_empty_values_survive_every_helper():
    """A blank cell is not an identifier; masking it would invent a finding."""
    redact.set_redaction(True)
    for fn in (redact.redact_email, redact.redact_name, redact.redact_id,
               redact.redact_text, redact.redact_names_cell):
        assert fn("") == ""
        assert fn(None) is None


def test_id_keeps_its_shape_so_two_ids_stay_distinguishable():
    redact.set_redaction(True)
    assert redact.redact_id("U0AAAAAAAAA") == "U0…AA"
    assert redact.redact_id("U02BBBBBBBB") == "U0…BB"
    assert redact.redact_id("U0AAAAAAAAA") != redact.redact_id("W0AAAAAAAAA")


def test_short_id_is_masked_whole():
    """Two chars of a five-char id is most of it, so nothing is kept."""
    redact.set_redaction(True)
    assert redact.redact_id("U0AAA") == "***"


def test_free_text_is_replaced_not_trimmed():
    """A name can sit anywhere in a form answer, so no prefix is safe."""
    redact.set_redaction(True)
    assert redact.redact_text("Ada Lovelace, Boston") == "[redacted]"
    assert "Ada" not in redact.redact_text("Ada Lovelace, Boston")


def test_doc_text_keeps_only_a_length():
    redact.set_redaction(True)
    assert redact.redact_doc_text("AAIF Boston") == "<11 chars>"
    assert "Ada" not in redact.redact_doc_text("Ada Lovelace")


def test_doc_text_reprs_when_masking_is_off():
    """Its caller prints the value inline, so quoting is the off-state."""
    assert redact.redact_doc_text("AAIF Boston") == "'AAIF Boston'"


def test_names_cell_masks_every_name_in_the_list():
    redact.set_redaction(True)
    assert redact.redact_names_cell("Ada Lovelace; Grace Hopper") == "A.; G."


def test_flag_and_helpers_are_one_module():
    """The regression this module exists to prevent.

    A consumer that imports the helpers must get the flag that governs them.
    Importing `redact_email` here while `set_redaction` lived elsewhere is how
    an address once reached a public CI log.
    """
    from aaif_events.redact import redact_email, set_redaction

    set_redaction(True)
    assert redact_email("ada@example.com") == "a***@***.com"
    set_redaction(False)
    assert redact_email("ada@example.com") == "ada@example.com"


def test_set_redaction_announces_itself_on_stderr(capsys):
    """Silent masking reads as "nothing sensitive here"; it must be visible."""
    redact.set_redaction(True)
    assert "redaction ON" in capsys.readouterr().err


def test_set_redaction_off_says_nothing():
    assert redact.redacting() is False


def test_add_redact_flag_defaults_to_the_ci_value():
    ap = argparse.ArgumentParser()
    redact.add_redact_flag(ap)
    assert ap.parse_args([]).redact is redact.CI_REDACT_DEFAULT
    assert ap.parse_args(["--redact"]).redact is True
    assert ap.parse_args(["--no-redact"]).redact is False


def test_help_names_only_what_this_script_masks():
    """A script that never prints an address should not promise to mask one."""
    ap = argparse.ArgumentParser()
    redact.add_redact_flag(ap, masks="names (first initial)")
    assert "names (first initial)" in ap.format_help()
    assert "a***@***.tld" not in ap.format_help()
