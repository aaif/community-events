#!/usr/bin/env python3
"""Unit tests for luma_stats.py's error contracts (offline — luma and the
tracker layer are mocked; no network, no Drive).

What must hold: a bad URL or a Luma failure surfaces as a clean sys.exit
message, never a raw traceback, and a tracker digest with any errored event
exits 1 so a partial digest can't read as complete.

Run: python3 skills/aaif-event-status/scripts/test_luma_stats.py
"""
import contextlib
import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 "..", "..", "..", "lib")))
sys.path.insert(0, os.path.dirname(__file__))
import luma_stats  # noqa: E402
from aaif_events import luma  # noqa: E402

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


LIVE = {"name": "Eval Night", "url": "https://luma.com/evt", "start_at": "2026-09-01",
        "timezone": "UTC", "registration_open": True, "waitlist_status": "off",
        "guest_counts": {"approved": {"guests": 12}}}


def run_main(argv, **luma_attrs):
    """Run main() with sys.argv patched and the given luma attributes mocked
    (available() always True). Returns (SystemExit-or-None, stdout)."""
    buf = io.StringIO()
    patches = [mock.patch.object(sys, "argv", ["luma_stats.py"] + argv),
               mock.patch.object(luma, "available", lambda: True)]
    patches += [mock.patch.object(luma, k, v) for k, v in luma_attrs.items()]
    exc = None
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(buf))
        try:
            luma_stats.main()
        except SystemExit as e:
            exc = e
    return exc, buf.getvalue()


def _raise(e):
    def f(*_a, **_k):
        raise e
    return f


# ---------------------------------------------------------------------------
# Direct --url / --event-id paths: clean sys.exit messages, no tracebacks
# ---------------------------------------------------------------------------
def test_url_not_an_event_page_exits_cleanly():
    exc, _ = run_main(["--url", "https://luma.com/aaif-sanjose"],
                      resolve_event_id=_raise(luma.NotAnEventUrl("calendar link")))
    check("NotAnEventUrl exits via SystemExit", isinstance(exc, SystemExit), True)
    check("NotAnEventUrl message names the problem",
          "doesn't point to an event page" in str(exc.code), True)

def test_luma_error_exits_with_bang_message():
    exc, _ = run_main(["--event-id", "evt-x"],
                      get_event=_raise(luma.LumaError("HTTP 500 from Luma")))
    check("LumaError exits via SystemExit", isinstance(exc, SystemExit), True)
    check("LumaError message is the '!! ' form",
          str(exc.code).startswith("!! "), True)
    check("LumaError message carries the cause",
          "HTTP 500 from Luma" in str(exc.code), True)

def test_happy_direct_path_prints_stats_and_exits_zero():
    exc, out = run_main(["--event-id", "evt-x"], get_event=lambda _id: dict(LIVE))
    check("happy path does not exit", exc, None)
    check("happy path prints the going count", "going: 12" in out, True)


# ---------------------------------------------------------------------------
# stats_for_tracker: per-event errors count, partial digest never reads complete
# ---------------------------------------------------------------------------
def run_tracker(views, **luma_attrs):
    """Run stats_for_tracker over synthetic tracker views (title -> LUMA URL)."""
    refs = [{"title": t} for t in views]
    buf = io.StringIO()
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(luma_stats.office, "read_document",
                                           lambda _p: object()))
        st.enter_context(mock.patch.object(luma_stats.tracker, "list_events",
                                           lambda _r: refs))
        st.enter_context(mock.patch.object(
            luma_stats.tracker, "view_event",
            lambda ref: {"title": ref["title"],
                         "details": {"LUMA URL": views[ref["title"]]}}))
        for k, v in luma_attrs.items():
            st.enter_context(mock.patch.object(luma, k, v))
        st.enter_context(contextlib.redirect_stdout(buf))
        errors = luma_stats.stats_for_tracker("t.docx", None)
    return errors, buf.getvalue()

def test_tracker_luma_error_counts_and_flags():
    errors, out = run_tracker(
        {"A": "https://luma.com/a", "B": "https://luma.com/b"},
        resolve_event_id=lambda url: url,
        get_event=lambda url: dict(LIVE) if url.endswith("/a")
        else (_ for _ in ()).throw(luma.LumaError("timeout")))
    check("errored event counted", errors, 1)
    check("errored event printed with '!! '", "!! timeout" in out, True)
    check("digest is marked incomplete", "stats above are incomplete" in out, True)
    check("healthy event still printed", "going: 12" in out, True)

def test_tracker_not_an_event_url_is_not_an_error():
    errors, out = run_tracker(
        {"A": "https://luma.com/aaif-sanjose"},
        resolve_event_id=_raise(luma.NotAnEventUrl("calendar")))
    check("calendar-link cell is not an error", errors, 0)
    check("calendar-link cell explained", "not pushed to Luma yet" in out, True)

def test_main_exits_1_on_partial_tracker_digest():
    # the exit-1 contract: any errored event in the docx path exits nonzero.
    with mock.patch.object(luma_stats, "stats_for_tracker", lambda d, e: 1):
        exc, _ = run_main(["t.docx"])
    check("partial digest exits", isinstance(exc, SystemExit), True)
    check("partial digest exit code is 1", exc.code, 1)
    with mock.patch.object(luma_stats, "stats_for_tracker", lambda d, e: 0):
        exc, _ = run_main(["t.docx"])
    check("complete digest does not exit", exc, None)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
    if fails:
        sys.exit(1)
    print("all ok")
