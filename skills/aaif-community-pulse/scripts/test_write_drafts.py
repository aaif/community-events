#!/usr/bin/env python3
"""Tests for write_drafts.py — the three-audience Pulse draft writer.

Plain script, exit 1 on failure, same shape as the other skill tests.

What matters here is not the file writing (that is `write_0600_text`, tested
next door) but the two refusals: a draft carrying contact details must never
reach a file that exists to be pasted in public, and a typo'd audience key must
never look like a successful run that silently wrote one post fewer.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = str(HERE / "write_drafts.py")
sys.path.insert(0, str(HERE))

import write_drafts as wd  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print("ok   %s" % label)
    else:
        FAILS.append("%s: got %r, want %r" % (label, got, want))
        print("FAIL %s: got %r, want %r" % (label, got, want))


def run(payload, cwd):
    return subprocess.run([sys.executable, SCRIPT], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=cwd)


# --- the contact-details guard, which is why this script exists --------------

def test_email_and_phone_are_found():
    check("a real address is caught",
          wd.contact_details("mail ada@example.com please"),
          [("email address", "ada@example.com")])
    check("a phone number is caught",
          [k for k, _ in wd.contact_details("call +1 415 555 0134 today")],
          ["phone number"])


def test_the_guard_does_not_fire_on_ordinary_post_text():
    """False positives are not free: they would push the writer to reword a
    correct post, or to stop trusting the check."""
    ordinary = ("AAIF Community Update (September 15, 2026)\n"
                "Aug 14 — Hops & Flops: New York, hosted by @michael and Lahari.\n"
                "3,413 members across the chapters. Everything else: "
                "https://luma.com/user/aaif and https://luma.com/kylt79cf\n"
                "Next up 9:00 AM, Tuesday.")
    check("a normal draft is clean", wd.contact_details(ordinary), [])
    check("a Slack handle is not an address", wd.contact_details("ping @ada"), [])


def test_a_long_url_is_not_a_phone_number():
    check("digits inside a URL are ignored",
          wd.contact_details("https://luma.com/e/1234567890123"), [])


# --- end to end -------------------------------------------------------------

def test_writes_one_file_per_audience_0600():
    with tempfile.TemporaryDirectory() as tmp:
        r = run({"local_champs": "organizer text", "general": "member text",
                 "social": "public text"}, tmp)
        check("exit 0", r.returncode, 0)
        for name in ("pulse-local-champs.txt", "pulse-general.txt",
                     "pulse-social.txt"):
            path = os.path.join(tmp, ".pulse-cache", name)
            check("wrote %s" % name, os.path.exists(path), True)
            check("%s is 0600" % name,
                  oct(os.stat(path).st_mode & 0o777), oct(0o600))
        with open(os.path.join(tmp, ".pulse-cache", "pulse-general.txt")) as fh:
            check("content round-trips", fh.read(), "member text\n")


def test_a_partial_set_is_allowed():
    """Re-wording one post must not blank the other two."""
    with tempfile.TemporaryDirectory() as tmp:
        run({"general": "first"}, tmp)
        r = run({"social": "public"}, tmp)
        check("exit 0", r.returncode, 0)
        check("the untouched draft survives",
              os.path.exists(os.path.join(tmp, ".pulse-cache", "pulse-general.txt")),
              True)


def test_contact_details_refuse_the_whole_run():
    with tempfile.TemporaryDirectory() as tmp:
        r = run({"general": "clean copy", "social": "write ada@example.com"}, tmp)
        check("exit non-zero", r.returncode != 0, True)
        check("says what it found", "email address" in r.stderr, True)
        # The clean draft is refused too: a half-written set is how the
        # unchecked file gets pasted.
        check("nothing at all is written",
              os.path.isdir(os.path.join(tmp, ".pulse-cache")), False)


def test_unknown_audience_key_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        r = run({"twitter": "hi"}, tmp)
        check("exit non-zero", r.returncode != 0, True)
        check("names the bad key", "twitter" in r.stderr, True)


def test_empty_draft_is_refused_not_written_blank():
    with tempfile.TemporaryDirectory() as tmp:
        r = run({"general": "   "}, tmp)
        check("exit non-zero", r.returncode != 0, True)
        check("explains the fix", "omit the key" in r.stderr, True)


def test_outdir_outside_the_cache_is_refused():
    """The path guard lives in write_0600_text; this is the wiring test."""
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable, SCRIPT, "--outdir", tmp],
                           input=json.dumps({"general": "x"}),
                           capture_output=True, text=True, cwd=tmp)
        check("exit non-zero", r.returncode != 0, True)
        check("says where drafts may go", ".pulse-cache" in r.stderr, True)


def main():
    MIN_TESTS = 9
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
    print("write_drafts: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
