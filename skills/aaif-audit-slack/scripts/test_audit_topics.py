#!/usr/bin/env python3
"""Self-tests for the topics audit's pure logic.

Standalone (not pytest) to match the other skills' script tests, which CI picks
up via `for t in skills/*/scripts/test_*.py`.

What is covered is deliberate. `dormancy()` is the centre of gravity: it is the
one place that decides whether a room is *dead* or merely *unlooked-at*, and
getting that wrong does not fail — it publishes a confident wrong recommendation
in a PDF that goes to community leadership and that nothing downstream
re-checks. It has already gone wrong twice: once labelling a measured silence
"not measured", and once counting never-swept rooms as quiet on the summary's
cover page while the appendix counted them correctly.

Also covered: `load_topics`' validation aborts (the guard that stops a
half-edited sheet from silently dropping a room out of every number),
`classify`'s live-channel join, and `chapter_claimed`'s None-vs-empty
distinction.

Not covered: the HTML f-strings (asserting on markup would break on every copy
edit) and the `gws`/Chrome subprocess paths.

Fixtures are synthetic per AGENTS.md — no real channel, person or workspace.
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_topics as at  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))
from aaif_events import jsoncache  # noqa: E402

FAILS = []
NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
TEAM = "T_SYNTH"


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))


def check_raises(label, fn, needle=""):
    try:
        fn()
    except SystemExit as exc:
        if needle and needle not in str(exc):
            FAILS.append("%s: aborted, but message lacked %r:\n     %s"
                         % (label, needle, exc))
        return
    FAILS.append("%s: expected SystemExit, none raised" % label)


def chan(name, members=10, private=False, archived=False, purpose=""):
    return {"id": "C_" + name, "name": name, "is_private": private,
            "is_archived": archived, "num_members": members,
            "purpose": purpose, "topic": ""}


def subject(name, act=None, members=10, theme="T", kind="topic"):
    s = {"name": name, "kind": kind, "theme": theme, "notes": "", "row": 2,
         "chan": chan(name, members)}
    at.attach_activity([s], {("C_" + name): act} if act else {}, NOW)
    return s


def ago(days):
    return (NOW - dt.timedelta(days=days)).timestamp()


# --------------------------------------------------------------------------
# dormancy — the invariant everything else rests on
# --------------------------------------------------------------------------

def test_dormancy_unmeasured_is_not_quiet():
    """A room the sweep never reached is UNMEASURED, never QUIET.

    This is the bug that shipped: the summary counted these as quiet, so the
    focus page and the topics appendix of the SAME PDF disagreed, and the
    inflated number was the one on the cover.
    """
    s = subject("a", act=None)
    check("no activity record -> unmeasured", at.dormancy(s),
          (at.UNMEASURED, None))
    check("state_of agrees", at.state_of(s), at.UNMEASURED)


def test_dormancy_scan_cap_is_not_silence():
    """last_human_unknown means the scan ran out, NOT that the room was silent.

    slack.history_activity sets it precisely so callers stop reporting a busy
    room as "silent all window" — the confident opposite of the truth.
    """
    s = subject("b", act={"last_human_ts": None, "last_human_unknown": True,
                          "window_complete": False, "human_msgs": 0})
    check("scan cap -> unknown", at.dormancy(s), (at.UNKNOWN, None))


def test_dormancy_measured_silence_is_never():
    s = subject("c", act={"last_human_ts": None, "last_human_unknown": False,
                          "window_complete": True, "human_msgs": 0})
    check("measured, nothing human -> never", at.dormancy(s), (at.NEVER, None))


def test_dormancy_quiet_and_live_split_on_threshold():
    old = subject("d", act={"last_human_ts": ago(at.QUIET_DAYS + 5),
                            "window_complete": True})
    new = subject("e", act={"last_human_ts": ago(3), "window_complete": True})
    check("older than threshold -> quiet", at.state_of(old), at.QUIET)
    check("recent -> live", at.state_of(new), at.LIVE)
    check("exactly at threshold counts as quiet",
          at.state_of(subject("f", act={"last_human_ts": ago(at.QUIET_DAYS),
                                        "window_complete": True})), at.QUIET)


def test_dormancy_days_only_for_dated_states():
    for s, want in ((subject("g"), None),
                    (subject("h", act={"last_human_unknown": True}), None)):
        check("undated state carries no day count", at.dormancy(s)[1], want)


def test_truncated_marks_floors_only():
    check("complete scan is not truncated",
          at.truncated(subject("i", act={"window_complete": True})), False)
    check("incomplete scan is truncated",
          at.truncated(subject("j", act={"window_complete": False})), True)
    check("no record is not truncated", at.truncated(subject("k")), False)


def test_members_of_preserves_unknown():
    """`num_members` None means Slack did not report it — never zero."""
    s = subject("l")
    s["chan"]["num_members"] = None
    check("unknown size stays None", at.members_of(s), None)
    check("known size passes through", at.members_of(subject("m", members=7)), 7)


# --------------------------------------------------------------------------
# load_topics — the guard on a hand-edited sheet
# --------------------------------------------------------------------------

def with_rows(rows):
    at.gws_values = lambda *_a, **_k: rows
    return at.load_topics


def test_load_topics_requires_headers():
    check_raises("missing Kind column", lambda: with_rows([["Channel"]])(),
                 "Kind")


def test_load_topics_empty_tab_aborts():
    check_raises("empty tab", lambda: with_rows([])(), "seed the")


def test_load_topics_rejects_bad_kind():
    rows = [["Channel", "Kind"], ["#a", "topic"], ["#b", ""], ["#c", "nonsense"]]
    check_raises("blank and unknown Kind both abort",
                 lambda: with_rows(rows)(), "row 3")


def test_load_topics_rejects_duplicates():
    rows = [["Channel", "Kind"], ["#a", "topic"], ["a", "vendor"]]
    check_raises("same channel twice", lambda: with_rows(rows)(), "twice")


def test_load_topics_flags_a_row_with_values_but_no_channel():
    """A half-finished edit must not vanish silently."""
    rows = [["Channel", "Kind", "Theme"], ["", "topic", "LLMs"]]
    check_raises("values but no Channel", lambda: with_rows(rows)(),
                 "no Channel")


def test_load_topics_skips_wholly_blank_rows():
    rows = [["Channel", "Kind"], ["#a", "topic"], ["", ""], []]
    check("blank rows are not errors", sorted(with_rows(rows)()), ["a"])


def test_load_topics_normalises_case_and_hash():
    """`#Kubernetes` must not later abort as 'renamed or archived'."""
    rows = [["Channel", "Kind", "Theme", "Notes"],
            ["  #Boston-Room ", " Topic ", " Infra ", "n"]]
    got = with_rows(rows)()
    check("channel lowercased and stripped", sorted(got), ["boston-room"])
    check("kind lowercased", got["boston-room"]["kind"], "topic")
    check("theme kept", got["boston-room"]["theme"], "Infra")


def test_load_topics_optional_columns_absent():
    rows = [["Channel", "Kind"], ["#a", "topic"]]
    check("absent Theme is empty, not an error",
          with_rows(rows)()["a"]["theme"], "")


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

def test_classify_aborts_on_unresolvable_channel():
    check_raises("row pointing at nothing live",
                 lambda: at.classify([chan("a")], {"b": {"name": "b",
                                                         "kind": "topic"}}),
                 "no longer resolve")


def test_classify_treats_archived_as_not_live():
    """The abort message promises 'renamed or archived' — archived must abort."""
    check_raises("archived room",
                 lambda: at.classify([chan("a", archived=True)],
                                     {"a": {"name": "a", "kind": "topic"}}),
                 "no longer resolve")


def test_classify_partitions_subject_from_filed_out():
    topics = {"a": {"name": "a", "kind": "topic"},
              "b": {"name": "b", "kind": "vendor"},
              "c": {"name": "c", "kind": "geo"},
              "d": {"name": "d", "kind": "community"}}
    chans = [chan(n) for n in "abcd"]
    subjects, filed_out, _ = at.classify(chans, topics)
    check("subject kinds", sorted(s["name"] for s in subjects), ["a", "b"])
    check("filed as not-a-topic", sorted(s["name"] for s in filed_out),
          ["c", "d"])


def test_classify_unfiled_excludes_private_archived_and_claimed():
    chans = [chan("a"), chan("secret", private=True), chan("gone", archived=True),
             chan("boston"), chan("loose")]
    _, _, unfiled = at.classify(chans, {"a": {"name": "a", "kind": "topic"}},
                                claimed={"boston"})
    check("only genuinely unfiled public live rooms",
          sorted(c["name"] for c in unfiled), ["loose"])


def test_classify_none_claimed_excludes_nothing():
    """None means 'could not exclude' — it must not behave like an empty set."""
    chans = [chan("a"), chan("boston")]
    _, _, unfiled = at.classify(chans, {"a": {"name": "a", "kind": "topic"}},
                                claimed=None)
    check("nothing excluded when audit.json was absent",
          sorted(c["name"] for c in unfiled), ["boston"])


# --------------------------------------------------------------------------
# chapter_claimed
# --------------------------------------------------------------------------

def test_chapter_claimed_absent_is_none_not_empty():
    with tempfile.TemporaryDirectory() as d:
        check("absent cache -> None (inflated-list caveat)",
              at.chapter_claimed(d, TEAM), None)


def test_chapter_claimed_unions_all_three_columns():
    with tempfile.TemporaryDirectory() as d:
        jsoncache.write(os.path.join(d, "audit.json"),
                        {"chapters": [{"public": "#boston",
                                       "organizers_channel": "boston-organizers",
                                       "regional": None},
                                      {"public": "ada", "organizers_channel": "",
                                       "regional": "#nordics"}]}, TEAM)
        check("union with # stripped, falsy skipped",
              sorted(at.chapter_claimed(d, TEAM)),
              ["ada", "boston", "boston-organizers", "nordics"])


# --------------------------------------------------------------------------
# near_duplicates
# --------------------------------------------------------------------------

def test_near_duplicates_token_subset_fires():
    subs = [subject("agents", members=100), subject("coding-agents", members=5)]
    check("one name's tokens contained in the other",
          [(a["name"], b["name"]) for a, b, _, _ in at.near_duplicates(subs)],
          [("agents", "coding-agents")])


def test_near_duplicates_shared_word_alone_does_not_fire():
    """The rule is token SUBSET, not 'shares a word'.

    A comment once claimed this exact pair as its worked example; it never
    fired. Pinned so the comment and the code cannot drift apart again.
    """
    subs = [subject("llm-security"), subject("security-n-privacy")]
    check("merely sharing a word is not enough", at.near_duplicates(subs), [])


def test_near_duplicates_unrelated_rooms_are_not_proposed():
    check("unrelated names",
          at.near_duplicates([subject("kubernetes"), subject("recipes")]), [])


def test_near_duplicates_ranks_by_membership():
    subs = [subject("aa", members=1), subject("aa-x", members=900),
            subject("bb", members=500), subject("bb-y", members=2)]
    first = at.near_duplicates(subs)[0][0]["name"]
    check("largest room first", first, "bb")


# --------------------------------------------------------------------------
# smoke: the body renders on the awkward inputs
# --------------------------------------------------------------------------

def test_build_body_renders_with_nothing_at_all():
    html = at.build_body([], [], [], NOW, 0, None, claimed_ok=False)
    check("empty report still renders", "Slack Topics Audit" in html, True)
    check("missing audit.json is disclosed", "Inflated" in html, True)


def test_build_body_separates_the_three_undated_states():
    subs = [subject("a"),
            subject("b", act={"last_human_unknown": True}),
            subject("c", act={"last_human_ts": None, "window_complete": True}),
            subject("d", act={"last_human_ts": ago(400), "window_complete": True})]
    html = at.build_body(subs, [], [], NOW, 3, {"age": "today", "days": 90})
    for needle in ("not measured", "scan cap reached", "silent all window"):
        check("body distinguishes %r" % needle, needle in html, True)


def main():
    MIN_TESTS = 26
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                ran += 1
            except BaseException as exc:   # SystemExit too: an unexpected abort is a failure
                FAILS.append("%s raised %s: %s" % (name, type(exc).__name__, exc))
    if ran < MIN_TESTS:
        print("FAIL: only %d tests ran, expected at least %d — did the "
              "collection break?" % (ran, MIN_TESTS))
        return 1
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("audit_topics: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
