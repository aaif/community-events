#!/usr/bin/env python3
"""render_report's composition. Plain script; exit 1 on failure. No network.

Two properties. Every step in the manifest reaches the page — the table row,
the index entry and its own section with the log carried whole — and nothing
from a log is interpreted: a log line that looks like markup is shown as
text, never rendered. Synthetic names only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render_report as rr  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


MANIFEST = {
    "stamp": "2026-01-02T030405Z", "mode": "report", "unattended": False,
    "phases": None, "stages": None, "exit": 2,
    "notes": ["RESULT: drift — re-run the flagged step(s) with --write after review."],
    "steps": [
        {"phase": "preflight", "stage": "gather", "step": "clean", "gate": "read-only",
         "why": "unresolved cities", "log": "clean.log", "html": None,
         "outcome": "in sync", "exit": 0, "seconds": 2.0},
        {"phase": "chapters", "stage": "plan", "step": "chapters", "gate": "open",
         "why": "feed rows", "log": "chapters.log", "html": None,
         "outcome": "DRIFT", "exit": 2, "seconds": 1.4},
        {"phase": "chapters", "stage": "execute", "step": "provision", "gate": "approval",
         "why": "rooms", "log": None, "html": None,
         "outcome": "skipped", "exit": None, "seconds": 0},
        {"phase": "events", "stage": "gather", "step": "luma", "gate": "read-only",
         "why": "pages", "log": None, "html": None,
         "outcome": "not run", "exit": None, "seconds": None},
        {"phase": "workspace", "stage": "gather", "step": "audit", "gate": "read-only",
         "why": "one page", "log": "audit.log", "html": "audit.html",
         "outcome": "in sync", "exit": 0, "seconds": 3.0},
    ],
}
LOGS = {"clean.log": "$ python clean.py scan\n\nall cities resolved\n",
        "chapters.log": "$ python sync_chapters.py\n\nBoston: 1 new <b>row</b> for Ada\n",
        "audit.log": "wrote audit.html\n"}

page = rr.render(MANIFEST, LOGS)

check("it is a complete document", page.startswith("<!doctype html>"), True)
check("every step has a section", all('id="%s"' % s["step"] in page
                                       for s in MANIFEST["steps"]), True)
check("every step is in the index", page.count('<li><a href="#'), 5)
check("a step not selected this run is on the page and says so",
      ('id="luma"' in page, "Not selected this run" in page), (True, True))
check("the lede counts what ran against the whole pipeline",
      "4 of 5 step(s) ran" in page, True)
check("the RESULT note is carried", "RESULT: drift" in page, True)
check("a log is shown as text, never as markup",
      ("&lt;b&gt;row&lt;/b&gt;" in page, "<b>row</b>" in page), (True, False))
check("the log is carried whole, command line included",
      "$ python sync_chapters.py" in page, True)
check("a skipped step says so instead of showing an empty log",
      "did not run, so there is no log" in page, True)
check("the audit page is linked from the top", 'href="audit.html"' in page, True)
check("outcomes get their pill",
      [page.count("pill-%s" % t) > 0 for t in ("ok", "warn", "mute")], [True] * 3)
check("a FAILED step would get the bad pill", rr.pill("FAILED"),
      '<span class="pill pill-bad">FAILED</span>')
check("the stat row counts outcomes", ">1</span><span class=\"k\">DRIFT<" in page, True)
check("the design system is embedded (tokens, not a fallback note)",
      "AAIF design tokens were not available" in page, False)

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("render_report: all checks passed")
