#!/usr/bin/env python3
"""Tests for track_drive_email.py — the `Drive Email` column.

Plain script, exit 1 on failure, same shape as the other skill tests.

This file exists because the script shipped without one. Ruff is pyflakes-only
and does not resolve imports (CLAUDE.md), so nothing in CI was importing the
module at all: a rename in `sync_crm` or `sync_access` would have shipped green
and died on the first live run against real Drive data.

What is pinned here is what the column MEANS. `(no grant)` is the finding — the
person cannot open their chapter folder — and a blank is not, because a blank is
indistinguishable from "this script has not run for that row yet". Every check
below is a way of getting that distinction wrong.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import track_drive_email as t  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print("ok   %s" % label)
    else:
        FAILS.append("%s: got %r, want %r" % (label, got, want))
        print("FAIL %s: got %r, want %r" % (label, got, want))


HDR = ["Full name", "Email", "Drive Email"]


def rows(*emails):
    return [["Someone", e, ""] for e in emails]


# --- plan(): what lands in the column ---------------------------------------

def test_the_acl_spelling_is_written_when_it_differs():
    """The Gmail-dot case is the whole reason the column exists: the grant is
    real, it is just spelled differently from the intake row."""
    granted = {"ab@gmail.com": ("Boston", "ab@gmail.com")}
    out = t.plan(HDR, rows("a.b@gmail.com"), granted, {})
    check("one row planned", len(out), 1)
    row, value, kind = out[0]
    check("the ACL spelling is written, not the intake one", value, "ab@gmail.com")
    check("and it is marked as a different spelling", kind, "Boston/differs")


def test_the_same_spelling_is_marked_same():
    granted = {"ada@x.com": ("Boston", "ada@x.com")}
    (_, value, kind) = t.plan(HDR, rows("ada@x.com"), granted, {})[0]
    check("the address is written unchanged", value, "ada@x.com")
    check("and marked as matching", kind, "Boston/same")


def test_no_matching_grant_writes_the_sentinel_not_a_blank():
    """A blank would be indistinguishable from "not run yet" — which is exactly
    why NO_GRANT exists. The module docstring once said the opposite."""
    granted = {"ada@x.com": ("Boston", None)}
    (_, value, kind) = t.plan(HDR, rows("ada@x.com"), granted, {})[0]
    check("the sentinel is written", value, t.NO_GRANT)
    check("it is not a blank", value != "", True)
    check("and names the chapter", kind, "Boston/missing")


def test_a_chapter_with_no_folder_yet_is_also_the_sentinel():
    out = t.plan(HDR, rows("ada@x.com"), {}, {"ada@x.com": "Atlantis"})
    (_, value, kind) = out[0]
    check("no folder yet still reports the sentinel", value, t.NO_GRANT)
    check("and says why", kind, "Atlantis/no folder yet")


def test_a_row_with_no_email_is_skipped_not_crashed():
    check("blank email plans nothing", t.plan(HDR, rows(""), {}, {}), [])
    check("whitespace-only email plans nothing",
          t.plan(HDR, rows("   "), {}, {}), [])


def test_an_unknown_person_is_left_alone():
    """Not every Form Responses row is an accepted organizer. A row we know
    nothing about must not be stamped "(no grant)" — that would invent a
    finding about someone this script never looked up."""
    check("an unmapped row plans nothing",
          t.plan(HDR, rows("stranger@x.com"), {}, {}), [])


# --- grant_by_person(): one entry per person, across chapters ---------------

def _fake_folders():
    return [{"id": "F1", "name": "Boston"}, {"id": "F2", "name": "Chicago"}]


def _run_grant_by_person(perms_by_folder, want_by_folder):
    with mock.patch.object(t, "list_chapter_folders", _fake_folders), \
         mock.patch.object(t, "read_role_tab", lambda tab, x: ([], None, None)), \
         mock.patch.object(t, "merge_people", lambda p: p), \
         mock.patch.object(t, "match_chapters",
                           lambda people, folders: (want_by_folder, [], [])), \
         mock.patch.object(t, "perms", lambda fid: perms_by_folder.get(fid, [])):
        return t.grant_by_person()


def test_a_grant_on_either_chapter_beats_no_grant_on_the_other():
    """One person, two chapters. Keyed by person alone, the last folder
    iterated used to win — so a real grant on Boston was overwritten by the
    absence of one on Chicago, and the person was reported as locked out of a
    folder they can open."""
    person = [{"email": "ada@x.com"}]
    granted, _ = _run_grant_by_person(
        perms_by_folder={"F1": [{"type": "user", "inherited": False,
                                 "role": "writer", "emailAddress": "ada@x.com"}],
                         "F2": []},
        want_by_folder={"F1": person, "F2": person})
    chapter, addr = granted["ada@x.com"]
    check("the folder that actually grants access wins", addr, "ada@x.com")
    check("and it is the chapter reported", chapter, "Boston")


def test_a_person_granted_on_neither_chapter_is_still_the_finding():
    person = [{"email": "ada@x.com"}]
    granted, _ = _run_grant_by_person(
        perms_by_folder={"F1": [], "F2": []},
        want_by_folder={"F1": person, "F2": person})
    check("no grant anywhere stays no grant", granted["ada@x.com"][1], None)


def test_an_inherited_permission_is_not_a_direct_grant():
    """The parent folder is locked down; an inherited entry does not mean this
    person was granted their chapter's folder."""
    person = [{"email": "ada@x.com"}]
    granted, _ = _run_grant_by_person(
        perms_by_folder={"F1": [{"type": "user", "inherited": True,
                                 "role": "writer", "emailAddress": "ada@x.com"}]},
        want_by_folder={"F1": person})
    check("an inherited grant does not count", granted["ada@x.com"][1], None)


# --- ensure_column(): nothing changes without a write flag ------------------

def test_ensure_column_touches_nothing_when_not_creating():
    """The repo-wide rule: nothing changes without a write flag. `gws` is
    stubbed to fail loudly, so a stray call cannot pass unnoticed."""
    def _boom(*a, **k):
        raise AssertionError("ensure_column called gws without --write")

    with mock.patch.object(t, "gws", _boom):
        check("an existing column is found without any call",
              t.ensure_column(list(HDR), create=False), HDR.index(t.H_DRIVE_EMAIL))
        check("a MISSING column still makes no call when not creating",
              t.ensure_column(["Full name", "Email"], create=False), 2)


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
    print("track_drive_email: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
