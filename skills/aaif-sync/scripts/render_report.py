#!/usr/bin/env python3
"""Draw report.html for one sync run: the state of the estate, then the logs.

The runner records; this draws. `sync.py` leaves `run.json` (one entry per
step: phase, stage, gate, outcome, exit code, duration, which log, which
findings file) beside the `<step>.log` files it captured and the `<step>.json`
findings each engine wrote. This script composes them into one page that
opens on **what is the state** — per subject, what each engine measured and
what it found, as stat tiles and finding tables — and carries every step's
text report behind that as an appendix, collapsed.

It is a subprocess of the runner so the runner can stay free of `lib`, and it
is a script of its own so a run can be drawn again later from what it left:

    python3 render_report.py sync-reports/<stamp>/

Everything here is composition. No number on this page is measured or
estimated here; every tile and row is what an engine wrote to its findings
file, and every appendix is the engine's stdout verbatim. The page names
people — the logs do — so it is written 0600 into the run directory, which
the runner already refuses to use unless git ignores it.
"""

import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from aaif_events import findings  # noqa: E402
from aaif_events import report_style as rs  # noqa: E402

MANIFEST = "run.json"
PAGE = "report.html"

#: outcome -> pill tone. The strings are sync.py's; the two files agree by
#: the test that renders a manifest sync.py wrote.
TONE = {"in sync": "ok", "wrote+verified": "ok", "DRIFT": "warn",
        "PARTIAL": "warn", "skipped": "mute", "FAILED": "bad", "not run": "mute"}
SEV_TONE = {"bad": "bad", "warn": "warn", "info": "mute"}
SEV_ORDER = {"bad": 0, "warn": 1, "info": 2}
#: Finding rows shown open per step; the rest sit behind a disclosure. The
#: worst rows come first, so what is hidden is never what needs a person most.
ROWS_OPEN = 40

GATE_NOTE = {"report-only": "report mode — never written by this runner",
             "human": "summary only — a human decides",
             "read-only": "read-only"}

#: What each phase is asking, for the section heading. Mirrors the table in
#: SKILL.md; a phase the runner adds without a line here still renders, with
#: its name alone.
PHASE_QUESTION = {
    "preflight": "is the source sound?",
    "chapters": "does the chapter exist — on the sheet, in Drive, in Slack?",
    "organizers": "who runs it, and can they reach their own things?",
    "events": "is every chapter's page live, and is it still running events?",
    "speakers": "what does the community talk about?",
    "hosts": "where does it meet?",
    "workspace": "what does an ordinary member see?",
}


def e(s):
    return html.escape(str(s if s is not None else ""))


def pill(text, tone):
    return '<span class="pill pill-%s">%s</span>' % (tone, e(text))


def outcome_pill(outcome):
    return pill(outcome, TONE.get(outcome, "mute"))


# ---------------------------------------------------------------------------
# The state: tiles and finding tables from each step's findings file
# ---------------------------------------------------------------------------

def tiles(measured):
    if not measured:
        return ""
    tone = {"warn": " s-warn", "bad": " s-bad", "ok": " s-ok"}
    cells = ['<div class="stat%s"><span class="v">%s</span><span class="k">%s</span></div>'
             % (tone.get(m.get("tone"), ""), e(m.get("value")), e(m.get("label")))
             for m in measured]
    return '<div class="stats">%s</div>' % "".join(cells)


def _table(rows):
    body = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (pill(f.get("severity", ""), SEV_TONE.get(f.get("severity"), "mute")),
           e(f.get("kind")), e(f.get("subject")), e(f.get("detail")), e(f.get("action")))
        for f in rows)
    return ('<div class="tablewrap"><table><thead><tr><th></th><th>finding</th>'
            '<th>subject</th><th>detail</th><th>next</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % body)


def findings_table(rows):
    if not rows:
        return ""
    rows = sorted(rows, key=lambda f: (SEV_ORDER.get(f.get("severity"), 9),
                                       f.get("kind", ""), f.get("subject", "")))
    head, rest = rows[:ROWS_OPEN], rows[ROWS_OPEN:]
    out = _table(head)
    if rest:
        out += ('<details><summary>%s</summary>%s</details>'
                % (e("and %d more, least severe last" % len(rest)), _table(rest)))
    return out


def step_state(s, doc):
    """One step inside its phase section: headline, tiles, findings."""
    head = ('<h3 id="%s">%s %s <span class="mute">· %s</span></h3>'
            % (e(s["step"]), e(s["step"]), outcome_pill(s.get("outcome")),
               e(s.get("why", ""))))
    outcome = s.get("outcome")
    if outcome == "not run":
        return head + ('<p class="caveat">Not selected this run. Run the phase, or '
                       '<code>sync.py %s</code>, to measure it.</p>' % e(s["step"]))
    if outcome == "skipped":
        return head + ('<p class="caveat">Did not run: it adds or notifies real '
                       'people and needs <code>--i-have-approval</code> from a '
                       'human at the terminal.</p>')
    if outcome == "FAILED":
        return head + ('<p class="caveat">Failed. The log in the appendix says why.</p>')
    parts = [head]
    if doc:
        if doc.get("summary"):
            parts.append("<p>%s%s</p>" % (e(doc["summary"]),
                         " <b>Applied and verified.</b>" if doc.get("written") else ""))
        parts.append(tiles(doc.get("measured", [])))
        rows = doc.get("findings", [])
        parts.append(findings_table(rows) if rows else
                     '<p class="mute">Nothing to act on.</p>')
    elif s.get("html"):
        parts.append('<p>This step renders its own page: <a href="%s">%s</a>.</p>'
                     % (e(s["html"]), e(s["html"])))
    else:
        parts.append('<p class="caveat">This step wrote no findings file; its report '
                     'is in the appendix.</p>')
    parts.append('<p class="mute"><a href="#log-%s">log</a></p>' % e(s["step"]))
    return "".join(parts)


def phase_sections(steps, docs):
    out = []
    by_phase = {}
    for s in steps:
        by_phase.setdefault(s["phase"], []).append(s)
    for phase, ss in by_phase.items():
        worst = min((SEV_ORDER.get(f.get("severity"), 9)
                     for s in ss for f in (docs.get(s["step"]) or {}).get("findings", [])),
                    default=9)
        n = sum(len((docs.get(s["step"]) or {}).get("findings", [])) for s in ss)
        badge = (pill("%d finding%s" % (n, "" if n == 1 else "s"),
                      {0: "bad", 1: "warn"}.get(worst, "mute")) if n else "")
        out.append('<section class="phase" id="phase-%s"><h2>%s <span class="mute">· %s</span> %s</h2>%s</section>'
                   % (e(phase), e(phase), e(PHASE_QUESTION.get(phase, "")), badge,
                      "".join(step_state(s, docs.get(s["step"])) for s in ss)))
    return "".join(out)


# ---------------------------------------------------------------------------
# The top: the run in numbers, and the RESULT
# ---------------------------------------------------------------------------

def overview(steps, docs):
    ran = [s for s in steps if s.get("outcome") not in ("not run",)]
    drift = [s for s in ran if s.get("outcome") in ("DRIFT", "PARTIAL")]
    wrote = [s for s in ran if s.get("outcome") == "wrote+verified"]
    failed = [s for s in ran if s.get("outcome") == "FAILED"]
    sev = {"bad": 0, "warn": 0, "info": 0}
    for d in docs.values():
        for f in (d or {}).get("findings", []):
            sev[f.get("severity", "info")] = sev.get(f.get("severity", "info"), 0) + 1
    cells = [("steps ran", "%d / %d" % (len(ran), len(steps)), ""),
             ("in sync", sum(1 for s in ran if s.get("outcome") == "in sync"), " s-ok"),
             ("with drift", len(drift), " s-warn" if drift else ""),
             ("wrote", len(wrote), " s-ok" if wrote else ""),
             ("failed", len(failed), " s-bad" if failed else ""),
             ("findings to act on", sev["bad"] + sev["warn"],
              " s-bad" if sev["bad"] else (" s-warn" if sev["warn"] else "")),
             ("worth seeing", sev["info"], "")]
    return '<div class="stats">%s</div>' % "".join(
        '<div class="stat%s"><span class="v">%s</span><span class="k">%s</span></div>'
        % (tone, e(v), e(k)) for k, v, tone in cells)


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
               outcome_pill(s.get("outcome")),
               e("" if s.get("exit") is None else s["exit"]),
               e("" if secs is None else "%.0fs" % secs), e(note)))
    return ('<div class="tablewrap"><table><thead><tr><th>phase</th><th>stage</th>'
            '<th>step</th><th>outcome</th><th>exit</th><th>time</th><th></th></tr>'
            '</thead><tbody>%s</tbody></table></div>' % "".join(rows))


# ---------------------------------------------------------------------------
# The appendix: every log, verbatim, collapsed
# ---------------------------------------------------------------------------

def log_section(s, log_text):
    head = '<h3 id="log-%s">%s <span class="mute">· %s / %s</span> %s</h3>' % (
        e(s["step"]), e(s["step"]), e(s["phase"]), e(s["stage"]),
        outcome_pill(s.get("outcome")))
    if log_text.strip():
        body = ('<details><summary>%s</summary><pre class="log">%s</pre></details>'
                % (e("the engine's report, verbatim"), e(log_text)))
    elif s.get("outcome") == "not run":
        body = '<p class="caveat">Not selected this run.</p>'
    else:
        body = '<p class="caveat">This step did not run, so there is no log.</p>'
    return head + body


def read_log(run_dir, name):
    if not name:
        return ""
    try:
        with open(os.path.join(run_dir, name), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        return "(log not readable: %s)" % exc


# ---------------------------------------------------------------------------

def render(manifest, logs, docs):
    """The page, from a manifest dict, {log name: text} and {step: findings dict}.
    Pure, for the test."""
    steps = manifest["steps"]
    mode = manifest.get("mode", "report")
    if manifest.get("unattended"):
        mode += ", unattended"
    scope = manifest.get("phases") or ["every phase"]
    ran = [s for s in steps if s.get("outcome") != "not run"]
    lede = ('<p class="lede">%s mode over %s — %d of %d step(s) ran, exit %s. '
            'Each subject below shows what its engines measured and what they '
            'found; the logs are in the appendix.</p>'
            % (e(mode), e(", ".join(scope)), len(ran), len(steps),
               e(manifest.get("exit"))))
    notes = "".join("<p><b>%s</b></p>" % e(n) for n in manifest.get("notes", []))
    audit = next((s for s in steps if s["step"] == "audit" and s.get("html")), None)
    audit_link = ('<p>The Slack audit as one page: <a href="%s">%s</a>.</p>'
                  % (e(audit["html"]), e(audit["html"]))) if audit else ""
    stamp = manifest.get("stamp", "")
    body = (rs.masthead("AAIF estate sync", "sync · %s" % stamp)
            + "<h1>Estate sync: %s</h1>" % e(stamp)
            + lede + overview(steps, docs) + notes + audit_link
            + phase_sections(steps, docs)
            + '<section class="appendix" id="appendix"><span class="tag">appendix</span>'
            + "<h2>The run</h2>" + step_table(steps)
            + "<h2>Every step's report, verbatim</h2>"
            + "".join(log_section(s, logs.get(s.get("log") or "", "")) for s in steps)
            + "</section>"
            + rs.closing(meta="aaif.io · sync-reports/%s" % e(stamp)))
    return rs.page("AAIF estate sync %s" % stamp, body)


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
    steps = manifest["steps"]
    logs = {s["log"]: read_log(run_dir, s["log"]) for s in steps if s.get("log")}
    docs = {s["step"]: findings.read(os.path.join(run_dir, s["findings"]))
            for s in steps if s.get("findings")}
    rs.write_private(out, render(manifest, logs, docs))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
