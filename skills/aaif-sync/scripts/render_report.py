#!/usr/bin/env python3
"""Draw report.html for one sync run from run.json and the step logs.

The runner records; this draws. `sync.py` leaves `run.json` (one entry per
step: phase, stage, gate, outcome, exit code, duration, which log) beside the
`<step>.log` files it captured, and this script composes them into one page:
the step table and RESULT line first, then every step's own report carried
whole, in run order, with the Slack audit page linked where the run made one.

It is a subprocess of the runner so the runner can stay free of `lib`, and it
is a script of its own so a run can be drawn again later from what it left:

    python3 render_report.py sync-reports/<stamp>/

Everything here is composition. No number is measured or estimated on this
page; each section is the engine's stdout, verbatim. The page names people —
every log does — so it is written 0600 into the run directory, which the
runner already refuses to use unless git ignores it.
"""

import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from aaif_events import report_style as rs  # noqa: E402

MANIFEST = "run.json"
PAGE = "report.html"

#: outcome -> pill tone. The strings are sync.py's; the two files agree by
#: the test that renders a manifest sync.py wrote.
TONE = {"in sync": "ok", "wrote+verified": "ok", "DRIFT": "warn",
        "PARTIAL": "warn", "skipped": "mute", "FAILED": "bad", "not run": "mute"}

GATE_NOTE = {"report-only": "report mode — never written by this runner",
             "human": "summary only — a human decides",
             "read-only": "read-only"}


def e(s):
    return html.escape(str(s if s is not None else ""))


def pill(outcome):
    return '<span class="pill pill-%s">%s</span>' % (TONE.get(outcome, "mute"), e(outcome))


def step_table(steps):
    rows = []
    for s in steps:
        note = GATE_NOTE.get(s.get("gate"), "")
        if s.get("outcome") == "skipped":
            note = "needs --i-have-approval"
        elif s.get("outcome") == "not run":
            note = "not selected this run"
        secs = s.get("seconds")
        rows.append(
            "<tr><td>%s</td><td>%s</td><td><a href=\"#%s\">%s</a></td><td>%s</td>"
            "<td class=\"n\">%s</td><td class=\"n\">%s</td><td>%s</td></tr>"
            % (e(s["phase"]), e(s["stage"]), e(s["step"]), e(s["step"]),
               pill(s.get("outcome")),
               e("" if s.get("exit") is None else s["exit"]),
               e("" if secs is None else "%.0fs" % secs), e(note)))
    return ('<div class="tablewrap"><table><thead><tr><th>phase</th><th>stage</th>'
            '<th>step</th><th>outcome</th><th>exit</th><th>time</th><th></th></tr>'
            '</thead><tbody>%s</tbody></table></div>' % "".join(rows))


def stats(steps):
    counts = {}
    for s in steps:
        counts[s.get("outcome")] = counts.get(s.get("outcome"), 0) + 1
    order = ["DRIFT", "wrote+verified", "PARTIAL", "FAILED", "skipped", "in sync",
             "not run"]
    tone = {"DRIFT": " s-warn", "PARTIAL": " s-warn", "FAILED": " s-bad"}
    cells = ['<div class="stat%s"><span class="v">%d</span><span class="k">%s</span></div>'
             % (tone.get(o, ""), counts[o], e(o)) for o in order if counts.get(o)]
    return '<div class="stats">%s</div>' % "".join(cells)


def read_log(run_dir, name):
    if not name:
        return ""
    try:
        with open(os.path.join(run_dir, name), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        return "(log not readable: %s)" % exc


def section(s, log_text):
    head = '<h2>%s <span class="mute">· %s / %s</span></h2>' % (
        e(s["step"]), e(s["phase"]), e(s["stage"]))
    meta = "<p>%s %s</p>" % (pill(s.get("outcome")), e(s.get("why", "")))
    link = ('<p><a href="%s">Open %s</a></p>' % (e(s["html"]), e(s["html"]))
            if s.get("html") else "")
    if log_text.strip():
        body = '<pre class="log">%s</pre>' % e(log_text)
    elif s.get("outcome") == "not run":
        body = ('<p class="caveat">Not selected this run. Run the phase, or '
                '<code>sync.py %s</code>, to measure it.</p>' % e(s["step"]))
    else:
        body = '<p class="caveat">This step did not run, so there is no log.</p>'
    return ('<section class="appendix" id="%s"><span class="tag">%s</span>%s%s%s%s</section>'
            % (e(s["step"]), e(s["phase"]), head, meta, link, body))


def render(manifest, logs):
    """The page, from a manifest dict and {log name: text}. Pure, for the test."""
    steps = manifest["steps"]
    mode = manifest.get("mode", "report")
    if manifest.get("unattended"):
        mode += ", unattended"
    scope = manifest.get("phases") or ["every phase"]
    ran = [s for s in steps if s.get("outcome") != "not run"]
    lede = ('<p class="lede">%s mode over %s — %d of %d step(s) ran, exit %s. The '
            'table is the whole pipeline; each section below is one step\'s own '
            'report, carried whole.</p>'
            % (e(mode), e(", ".join(scope)), len(ran), len(steps),
               e(manifest.get("exit"))))
    notes = "".join("<p><b>%s</b></p>" % e(n) for n in manifest.get("notes", []))
    audit = next((s for s in steps if s["step"] == "audit" and s.get("html")), None)
    audit_link = ('<p>The Slack audit as one page: <a href="%s">%s</a>.</p>'
                  % (e(audit["html"]), e(audit["html"]))) if audit else ""
    toc = ('<nav class="toc"><span class="tag">Steps</span><ul>%s</ul></nav>'
           % "".join('<li><a href="#%s">%s</a> %s</li>'
                     % (e(s["step"]), e(s["step"]), pill(s.get("outcome"))) for s in steps))
    body = (rs.masthead("AAIF estate sync", "sync · %s" % manifest.get("stamp", ""))
            + "<h1>Estate sync: %s</h1>" % e(manifest.get("stamp", ""))
            + lede + stats(steps) + step_table(steps) + notes + audit_link + toc
            + "".join(section(s, logs.get(s.get("log") or "", "")) for s in steps)
            + rs.closing(meta="aaif.io · sync-reports/%s" % e(manifest.get("stamp", ""))))
    return rs.page("AAIF estate sync %s" % manifest.get("stamp", ""), body)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        sys.exit("usage: render_report.py <run directory containing run.json>")
    run_dir = argv[0]
    path = os.path.join(run_dir, MANIFEST)
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.exit("ABORT: cannot read %s: %s" % (path, exc))
    out = os.path.join(run_dir, PAGE)
    rs.assert_git_ignored(out)
    logs = {s["log"]: read_log(run_dir, s["log"]) for s in manifest["steps"] if s.get("log")}
    rs.write_private(out, render(manifest, logs))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
