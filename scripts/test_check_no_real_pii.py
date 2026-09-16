#!/usr/bin/env python3
"""Tests for check_no_real_pii.py. Plain script, exit 1 on failure.

The first four cases are the values that actually leaked, in the shapes they
leaked in. A guard written after an incident that cannot catch that incident is
theatre, so they are pinned here first and by name.

The false-positive cases matter just as much: this runs on every commit, and a
check that flags `WARNING` or `a@x.com` gets switched off within a week, which
leaves the repo exactly as exposed as it was before.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_no_real_pii as c  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print("ok   %s" % label)
    else:
        FAILS.append("%s: got %r, want %r" % (label, got, want))
        print("FAIL %s: got %r, want %r" % (label, got, want))


def findings(text):
    return c.check_text("t.py", text)


def kinds(text):
    return sorted({k for _, k, _, _ in findings(text)})


# --- the incident ------------------------------------------------------------
# Shapes only. The real values are not written here, for the obvious reason.

def test_a_real_looking_gmail_address_is_refused():
    check("a dotted real-looking address is caught",
          kinds("live: `given.family.extra@gmail.com` -> users_not_found"),
          ["email"])
    check("and its dotless spelling too",
          kinds("`givenfamilyextra@gmail.com` -> ok, same human"), ["email"])


def test_a_real_looking_slack_id_is_refused():
    """High-entropy ids in the shape Slack issues."""
    for uid in ("U0BJ1G2SWFK", "U02HG3DF68G", "W01LT585W8Z"):
        check("%s is caught" % uid, kinds('for good in ("%s",):' % uid),
              ["Slack id"])


def test_a_real_looking_handle_is_not_an_id_but_the_address_beside_it_is():
    """The handle itself is not id-shaped — this documents the limit rather
    than pretending otherwise. What the guard catches is the id it travelled
    with, which is how the incident presented."""
    check("a bare handle is not flagged", findings("@somepersonhandle"), [])
    check("the id beside it is", kinds("@somepersonhandle U0BJ1G2SWFK"),
          ["Slack id"])


# --- false positives, which are how a check gets disabled --------------------

def test_ordinary_capitalised_words_are_not_ids():
    for word in ("WARNING", "UNKNOWN", "UNMEASURED", "UNRESOLVED", "WITHOUT",
                 "WHOLESALE", "WEBMAIL", "UPGRADE"):
        check("%s is not an id" % word, findings("raise %s" % word), [])


def test_the_synthetic_fixtures_this_repo_uses_are_free():
    for addr in ("a@x.com", "ada@x.io", "b@x.com", "ravi@vendor.co",
                 "sam@example.com", "ops@aaif.test", "a.b@gmail.com",
                 "ab@gmail.com", "s@mlops.community"):
        check("%s is allowed" % addr, findings('"%s"' % addr), [])
    for uid in sorted(c.ALLOWED_SLACK_IDS):
        check("%s is allowed" % uid, findings('"%s"' % uid), [])


def test_a_luma_url_and_a_version_are_not_identifiers():
    check("a URL is clean", findings("https://luma.com/e/1234567890123"), [])
    check("a version is clean", findings("gws 0.22.5"), [])


# --- the rules themselves ----------------------------------------------------

def test_a_real_domain_needs_a_synthetic_local():
    check("a stand-in at the staff domain is fine",
          findings("ops@mlops.community"), [])
    check("a name at the staff domain is not",
          kinds("firstname.lastname@mlops.community"), ["email"])


def test_an_unknown_domain_is_refused_outright():
    check("an address at a domain nobody vetted is caught",
          kinds("someone@randomcompany.io"), ["email"])


def test_case_is_not_a_way_around_it():
    check("uppercase domain is still checked",
          kinds("Given.Family@GMAIL.COM"), ["email"])


def test_the_message_says_what_to_do():
    (_, _, _, why) = findings("given.family@gmail.com")[0]
    check("the reason names the problem", "local part" in why, True)


def main():
    MIN_TESTS = 10
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                ran += 1
            except BaseException as exc:
                FAILS.append("%s raised %s: %s" % (name, type(exc).__name__, exc))
    if ran < MIN_TESTS:
        print("FAIL: only %d tests ran, expected at least %d" % (ran, MIN_TESTS))
        return 1
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("check_no_real_pii: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
