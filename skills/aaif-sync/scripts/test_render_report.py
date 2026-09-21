#!/usr/bin/env python3
"""render_report's composition. Plain script; exit 1 on failure. No network.

Three properties. The page opens on STATE: every step's measured tiles and
findings reach its phase section, ranked by severity. Every step reaches the
page whether or not it ran, and its log is carried whole in the appendix.
And nothing from a log or a finding is interpreted: text that looks like
markup is shown as text, never rendered. Synthetic names only.
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
         "findings": "clean.json", "outcome": "in sync", "exit": 0, "seconds": 2.0},
        {"phase": "chapters", "stage": "plan", "step": "chapters", "gate": "open",
         "why": "feed rows", "log": "chapters.log", "html": None,
         "findings": "chapters.json", "outcome": "DRIFT", "exit": 2, "seconds": 1.4},
        {"phase": "chapters", "stage": "execute", "step": "provision", "gate": "approval",
         "why": "rooms", "log": None, "html": None, "findings": None,
         "outcome": "skipped", "exit": None, "seconds": 0},
        {"phase": "events", "stage": "gather", "step": "luma", "gate": "read-only",
         "why": "pages", "log": None, "html": None, "findings": None,
         "outcome": "not run", "exit": None, "seconds": None},
        {"phase": "workspace", "stage": "gather", "step": "audit", "gate": "read-only",
         "why": "one page", "log": "audit.log", "html": "audit.html",
         "findings": None, "outcome": "in sync", "exit": 0, "seconds": 3.0},
    ],
}
LOGS = {"clean.log": "$ python clean.py scan\n\nall cities resolved\n",
        "chapters.log": "$ python sync_chapters.py\n\nBoston: 1 new <b>row</b> for Ada\n",
        "audit.log": "wrote audit.html\n"}
DOCS = {
    "clean": {"format": 1, "step": "clean", "mode": "report",
              "summary": "0 fixes, 0 flags", "written": False,
              "measured": [{"label": "proposed fixes", "value": 0, "tone": "ok"}],
              "findings": []},
    "chapters": {"format": 1, "step": "chapters", "mode": "report",
                 "summary": "1 add across 1 chapter", "written": False,
                 "measured": [{"label": "adds", "value": 1, "tone": "warn"},
                              {"label": "chapter rows", "value": 96}],
                 "findings": [
                     {"kind": "add", "subject": "Boston", "detail": "Ada",
                      "severity": "warn", "action": "apply with --write"},
                     {"kind": "malformed <i>text</i>", "subject": "row 41",
                      "detail": "", "severity": "bad", "action": "fix the cell"},
                     {"kind": "held", "subject": "Austin", "detail": "fewer than 4",
                      "severity": "info", "action": ""}]},
}

page = rr.render(MANIFEST, LOGS, DOCS)

check("it is a complete document", page.startswith("<!doctype html>"), True)
check("every phase in the manifest gets a section",
      all('id="phase-%s"' % p in page for p in ("preflight", "chapters", "events", "workspace")),
      True)
check("every step has a state entry and a log entry",
      all(('id="%s"' % s["step"] in page, 'id="log-%s"' % s["step"] in page) == (True, True)
          for s in MANIFEST["steps"]), True)
check("a step's summary and tiles are on the page",
      ("1 add across 1 chapter" in page, ">96</span>" in page), (True, True))
check("findings are ranked bad, warn, info",
      [page.index(x) for x in ("fix the cell", "apply with --write", "fewer than 4")]
      == sorted(page.index(x) for x in ("fix the cell", "apply with --write", "fewer than 4")),
      True)
check("the phase badge counts its findings", "3 findings" in page, True)
check("a finding's text is shown, never rendered",
      ("&lt;i&gt;text&lt;/i&gt;" in page, "<i>text</i>" in page), (True, False))
check("a log's text is shown, never rendered",
      ("&lt;b&gt;row&lt;/b&gt;" in page, "<b>row</b>" in page), (True, False))
check("the log is carried whole, command line included, in the appendix",
      "$ python sync_chapters.py" in page, True)
check("a step with no findings says nothing to act on",
      "Nothing to act on" in page, True)
check("a skipped step says why", "needs <code>--i-have-approval</code>" in page, True)
check("a step not selected this run is on the page and says so",
      ('id="luma"' in page, "Not selected this run" in page), (True, True))
check("an audit step links its own page instead of a findings file",
      "renders its own page" in page and 'href="audit.html"' in page, True)
check("the lede counts what ran against the whole pipeline",
      "4 of 5 step(s) ran" in page, True)
check("the RESULT note is carried", "RESULT: drift" in page, True)
check("the overview counts findings to act on (bad + warn)",
      ">2</span><span class=\"k\">findings to act on<" in page, True)
# A step with hundreds of findings shows the worst ROWS_OPEN open and folds the
# rest, so a long list never buries the next subject.
_many = dict(DOCS["chapters"])
_many["findings"] = ([{"kind": "bad one", "subject": "row 1", "detail": "", "severity": "bad",
                       "action": ""}]
                     + [{"kind": "tidy", "subject": "row %d" % i, "detail": "",
                         "severity": "info", "action": ""} for i in range(60)])
_long = rr.render(MANIFEST, LOGS, dict(DOCS, chapters=_many))
check("a long finding list folds past ROWS_OPEN",
      ("and %d more" % (61 - rr.ROWS_OPEN) in _long, _long.count("<td>tidy</td>")), (True, 60))
check("...and the worst row stays open, above the fold",
      _long.index("bad one") < _long.index("and %d more" % (61 - rr.ROWS_OPEN)), True)
check("the design system is embedded (tokens, not a fallback note)",
      "AAIF design tokens were not available" in page, False)

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("render_report: all checks passed")
