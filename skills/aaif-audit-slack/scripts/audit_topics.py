#!/usr/bin/env python3
"""Audit the workspace's TOPIC channels: the subject-matter rooms.

The organizer engine asks whether each chapter has a home. The member engine
asks what a newcomer sees. This one asks the third question: **are the subjects
this community organises around still alive, and can a newcomer find them?**

A topic channel is not inferred here. Which rooms are subjects (#kubernetes,
#coding-agents, #mcp) and which are plumbing (#general, #random, #job-posts) is
a human judgement, so it is read off a `Topics` tab on the Chapters List —
exactly where the channel map and the matching vocabularies already live, and
for the same reason: the people who can say are the people with the spreadsheet,
not the people with the pull request.

Everything here is read-only. No message text is read or retained — dormancy and
volume come from audit_activity.py's cache, which stores timestamps and counts.
"""

import argparse
import datetime as dt
import difflib
import html
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events import report_style as rs  # noqa: E402
from aaif_events.slack import Slack, channels  # noqa: E402

# Same skill, ships together, so the sheet plumbing is imported rather than
# copied. The `lib` coupling is already paid for by the import above; a fourth
# copy of gws_values would only be a fourth thing to fix.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_organizers import CHAPTERS_ID, cell, gws_values, header_index  # noqa: E402

e = html.escape

TOPICS_TAB = "Topics"

#: `Kind` values the tab may carry. The first three are subject rooms and are
#: what the report measures; the rest are recorded so that "not a topic" is a
#: filed human decision rather than an absence, exactly as `none` does in the
#: channel map. A row is never silently ignored.
SUBJECT_KINDS = ("topic", "vendor", "cloud")
OTHER_KINDS = ("geo", "community", "ops")
KINDS = SUBJECT_KINDS + OTHER_KINDS

#: Dormancy thresholds in days, applied to the LAST HUMAN message.
QUIET_DAYS = 90
DEAD_DAYS = 365

#: Name-similarity floor for proposing that two rooms overlap. Deliberately
#: high: this proposes merges to a human, and a list padded with false pairs
#: gets skimmed and then ignored.
SIMILAR = 0.72


def load_topics(sheet_id=None):
    """Read the `Topics` tab: one row per classified channel.

    Columns by header name, never by letter — the layouts change (repo rule).
    """
    rows = gws_values(sheet_id or CHAPTERS_ID, "'%s'!A:D" % TOPICS_TAB)
    if not rows:
        raise SystemExit(
            "ABORT: no %r tab on the Chapters List (or it is empty).\n"
            "This engine does not guess which channels are subjects — seed the "
            "tab first (see the skill's Topics section)." % TOPICS_TAB)

    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, TOPICS_TAB, "Channel", "Kind")
    theme_i = headers.index("Theme") if "Theme" in headers else None
    notes_i = headers.index("Notes") if "Notes" in headers else None

    out, seen, bad = {}, set(), []
    for n, row in enumerate(rows[1:], start=2):
        name = cell(row, idx["Channel"]).lstrip("#").strip()
        if not name:
            continue
        kind = cell(row, idx["Kind"]).strip().lower()
        if kind not in KINDS:
            bad.append("row %d: %s has Kind %r" % (n, name, kind or "(blank)"))
            continue
        if name in seen:
            bad.append("row %d: %s listed twice" % (n, name))
            continue
        seen.add(name)
        out[name] = {
            "name": name,
            "kind": kind,
            "theme": cell(row, theme_i).strip() if theme_i is not None else "",
            "notes": cell(row, notes_i).strip() if notes_i is not None else "",
            "row": n,
        }
    if bad:
        # A blank or misspelled Kind would silently drop a room out of every
        # number on the page. Same class of failure as an unknown Slack Config
        # label, same answer: abort in the first second.
        raise SystemExit(
            "ABORT: %d unusable row(s) on the %r tab — every row needs a "
            "Channel and a Kind in {%s}:\n  %s"
            % (len(bad), TOPICS_TAB, ", ".join(KINDS), "\n  ".join(bad)))
    return out


def chapter_claimed(cache):
    """Channel names the organizer engine already accounts for.

    Without this, every chapter city room lands in "unclassified" and the
    Topics tab looks 80 rows short of complete — a made-up backlog that would
    send someone off to file rooms another engine already owns. Absent
    audit.json, nothing is excluded and the report SAYS the list is inflated
    rather than quietly under-reporting.
    """
    audit = jsoncache.read(os.path.join(cache, "audit.json"), note=None)
    if not audit:
        return None
    return {c[k].lstrip("#") for c in audit.get("chapters", ())
            for k in ("public", "organizers_channel", "regional") if c.get(k)}


def classify(chans, topics, claimed=None):
    """Join the tab against live Slack. Returns (subjects, filed_out, unfiled)."""
    by_name = {c["name"]: c for c in chans}
    missing = sorted(n for n in topics if n not in by_name)
    if missing:
        raise SystemExit(
            "ABORT: %d channel(s) on the %r tab no longer resolve to a live "
            "channel:\n  %s\nThe channel was renamed or archived. Fix the row — "
            "left alone, the topic silently drops off the report."
            % (len(missing), TOPICS_TAB, "\n  ".join("#" + n for n in missing)))

    subjects, filed_out = [], []
    for name, rec in topics.items():
        c = by_name[name]
        row = dict(rec, chan=c)
        (subjects if rec["kind"] in SUBJECT_KINDS else filed_out).append(row)

    # Anything live, public and unfiled. NOT treated as a topic — reported as
    # "nobody has looked", which is an answer; guessing would not be.
    unfiled = [c for c in chans
               if not c["is_archived"] and not c["is_private"]
               and c["name"] not in topics
               and c["name"] not in (claimed or ())]
    return subjects, filed_out, unfiled


def attach_activity(subjects, activity, today):
    """Fold the activity cache onto each subject room. Absent = unmeasured."""
    measured = 0
    for s in subjects:
        rec = (activity or {}).get(s["chan"]["id"])
        s["act"] = rec
        s["quiet_days"] = None
        if not rec:
            continue
        measured += 1
        ts = rec.get("last_human_ts")
        if ts:
            s["quiet_days"] = (today - dt.datetime.fromtimestamp(
                ts, dt.timezone.utc)).days
    return measured


def near_duplicates(subjects):
    """Propose overlapping rooms — same Theme, or closely similar names.

    Proposals only. Merging two channels destroys history and splits a
    membership; the report says which pair and why, and a human decides.
    """
    pairs = []
    for i, a in enumerate(subjects):
        for b in subjects[i + 1:]:
            an, bn = a["name"], b["name"]
            ratio = difflib.SequenceMatcher(None, an, bn).ratio()
            at, bt = set(an.split("-")), set(bn.split("-"))
            shared = at & bt
            # A shared word is the stronger signal (#llm-security vs
            # #security-n-privacy); raw string similarity catches the rest.
            if ratio >= SIMILAR or (shared and len(shared) >= min(len(at), len(bt))):
                pairs.append((a, b, ratio, sorted(shared)))
    pairs.sort(key=lambda p: -(p[0]["chan"]["num_members"] or 0))
    return pairs


def build_body(subjects, filed_out, unfiled, today, measured, act_meta,
               claimed_ok=True):
    def num(c):
        return c["chan"]["num_members"] or 0

    subjects = sorted(subjects, key=lambda s: -num(s))
    total_members = sum(num(s) for s in subjects)

    quiet = [s for s in subjects if s["quiet_days"] is not None
             and s["quiet_days"] >= QUIET_DAYS]
    dead = [s for s in subjects if s["quiet_days"] is not None
            and s["quiet_days"] >= DEAD_DAYS]
    never = [s for s in subjects if s["act"] and not s["act"].get("last_human_ts")]
    unmeasured = [s for s in subjects if not s["act"]]
    no_purpose = [s for s in subjects if not (s["chan"].get("purpose") or "").strip()]
    dups = near_duplicates(subjects)

    # Concentration: how few people carry each room. A topic held up by one
    # poster is one person's departure away from dead.
    thin = [s for s in subjects
            if s["act"] and s["act"].get("human_msgs") and s["act"].get("posters", 0) <= 2]

    by_theme = defaultdict(list)
    for s in subjects:
        by_theme[s["theme"] or "(no theme)"].append(s)

    kinds = Counter(s["kind"] for s in subjects)

    def chan_row(s, extra=""):
        p = (s["chan"].get("purpose") or "").strip()
        return ('<tr><td><b>#%s</b></td><td>%s</td><td class="n">%s</td>'
                '<td>%s</td><td class="mute">%s</td></tr>'
                % (e(s["name"]), e(s["theme"] or "—"), format(num(s), ","),
                   extra, e(rs.redact(p, 90)) if p else "<i>no purpose set</i>"))

    def quiet_label(s):
        # Order matters: quiet_days is None BOTH when the sweep never reached
        # the channel and when it reached it and found no human message. Test
        # the record first, or a measured silence gets reported as a failure to
        # measure — and the room drops off the list someone acts on.
        if not s["act"]:
            return "not measured"
        if not s["act"].get("last_human_ts"):
            return "silent all window"
        return "%d days ago" % s["quiet_days"]

    theme_rows = sorted(((t, sum(num(x) for x in v)) for t, v in by_theme.items()),
                        key=lambda r: -r[1])

    dup_html = "".join(
        '<tr><td><b>#%s</b> <span class="mute">(%s)</span></td>'
        '<td><b>#%s</b> <span class="mute">(%s)</span></td><td>%s</td></tr>'
        % (e(a["name"]), format(num(a), ","), e(b["name"]), format(num(b), ","),
           e("shares “%s”" % ", ".join(shared) if shared
             else "%d%% name similarity" % round(ratio * 100)))
        for a, b, ratio, shared in dups[:25]) or \
        '<tr><td colspan="3" class="mute">No overlapping pairs proposed.</td></tr>'

    act_foot = ("Dormancy and volume come from the activity sweep of %s "
                "(%d of %d subject rooms measured, %d-day window)."
                % (e(act_meta["age"]), measured, len(subjects), act_meta["days"])
                ) if act_meta else (
        "<b>No activity cache.</b> Nothing on this page measures whether a topic "
        "is alive — run audit_activity.py first.")

    body = """
<h1>Slack Topics Audit</h1>
<p class="lede">The subject-matter rooms: what this community organises around,
which of those subjects have gone quiet, and what a newcomer browsing the
channel list actually sees. Chapter, organizer and country rooms are the
organizer engine's business and are excluded here.</p>

<div class="stats">
  <div class="stat"><span class="v">%(n_sub)s</span><span class="k">subject channels</span></div>
  <div class="stat"><span class="v">%(members)s</span><span class="k">memberships across them</span></div>
  <div class="stat s-warn"><span class="v">%(n_quiet)s</span><span class="k">quiet %(qd)s+ days</span></div>
  <div class="stat s-bad"><span class="v">%(n_purpose)s</span><span class="k">no purpose set</span></div>
  <div class="stat"><span class="v">%(n_unfiled)s</span><span class="k">unclassified rooms</span></div>
</div>

<h2>Where the subjects sit</h2>
%(themes)s
<p class="mute">%(kinds)s</p>

<h2>Quiet and dead topics</h2>
<p>Last <i>human</i> message, bots and join noise excluded. A quiet room is not
automatically a room to archive — it is a room to either revive with a prompt or
retire deliberately. %(dead_note)s</p>
<table><thead><tr><th>Channel</th><th>Theme</th><th class="n">Members</th>
<th>Last human message</th><th>Purpose</th></tr></thead><tbody>%(quiet_rows)s</tbody></table>

<h2>Possible overlaps</h2>
<p>Proposed, never acted on. Merging splits a membership and destroys history, so
each pair is for a human to judge.</p>
<table><thead><tr><th>Channel</th><th>Overlaps with</th><th>Why</th></tr></thead>
<tbody>%(dups)s</tbody></table>

<h2>Carried by one or two people</h2>
<p>Rooms with posting activity in the window but two or fewer distinct posters.
One person's departure from dead.</p>
<table><thead><tr><th>Channel</th><th>Theme</th><th class="n">Members</th>
<th>Posters / messages</th><th>Purpose</th></tr></thead><tbody>%(thin_rows)s</tbody></table>

<h2>No purpose set</h2>
<p>What a newcomer sees in the channel browser is the purpose line. These rooms
show nothing, so joining them is a guess.</p>
<table><thead><tr><th>Channel</th><th>Theme</th><th class="n">Members</th>
<th>Last human message</th><th>Purpose</th></tr></thead><tbody>%(purpose_rows)s</tbody></table>

<h2>Unclassified</h2>
<p>%(unfiled_caveat)sLive public rooms with no row on the <code>%(tab)s</code> tab. They are
<b>not</b> counted as topics anywhere above — nobody has said what they are.
File each one (or mark it <code>community</code>/<code>ops</code>) and re-run.</p>
<table><thead><tr><th>Channel</th><th class="n">Members</th><th>Purpose</th></tr></thead>
<tbody>%(unfiled_rows)s</tbody></table>

<h2>What this page cannot see</h2>
<ul>
<li><b>Every message count is a floor.</b> Thread replies are invisible to
<code>conversations.history</code> except broadcasts, so a room that lives in
threads under-reports.</li>
<li><b>Private rooms are absent.</b> Only channels the token owner belongs to are
listed by the API at all.</li>
<li><b>Membership is not readership.</b> A 6,000-member room with three posters
is three people talking, not six thousand.</li>
<li><b>The classification is human.</b> Every subject on this page is a subject
because someone wrote it on the %(tab)s tab; the engine never inferred one.</li>
</ul>
<footer>%(act_foot)s Generated %(today)s. Read-only: no message text was retained.</footer>
""" % {
        "n_sub": format(len(subjects), ","),
        "members": format(total_members, ","),
        "n_quiet": format(len(quiet) + len(never), ","),
        "qd": QUIET_DAYS,
        "n_purpose": format(len(no_purpose), ","),
        "n_unfiled": format(len(unfiled), ","),
        "themes": rs.bars(theme_rows[:14]),
        "kinds": e("Kinds on the tab: " + ", ".join(
            "%s %d" % (k, n) for k, n in kinds.most_common())
            + " · filed as not-a-topic: %d · unmeasured for activity: %d"
            % (len(filed_out), len(unmeasured))),
        "dead_note": e("%d have been silent for a year or more." % len(dead))
                     if dead else "",
        "quiet_rows": "".join(chan_row(s, e(quiet_label(s)))
                              for s in sorted(quiet + never,
                                              key=lambda x: -(x["quiet_days"] or 10**6))
                              ) or '<tr><td colspan="5" class="mute">Nothing quiet.</td></tr>',
        "dups": dup_html,
        "thin_rows": "".join(
            chan_row(s, e("%d poster(s), %d msgs" % (s["act"]["posters"],
                                                     s["act"]["human_msgs"])))
            for s in sorted(thin, key=lambda x: -num(x))
        ) or '<tr><td colspan="5" class="mute">None.</td></tr>',
        "purpose_rows": "".join(chan_row(s, e(quiet_label(s)))
                                for s in no_purpose
                                ) or '<tr><td colspan="5" class="mute">All set.</td></tr>',
        "unfiled_rows": "".join(
            '<tr><td><b>#%s</b></td><td class="n">%s</td><td class="mute">%s</td></tr>'
            % (e(c["name"]), format(c["num_members"] or 0, ","),
               e(rs.redact((c.get("purpose") or "").strip(), 90)) or "<i>none</i>")
            for c in sorted(unfiled, key=lambda c: -(c["num_members"] or 0))
        ) or '<tr><td colspan="3" class="mute">Everything is filed.</td></tr>',
        "tab": e(TOPICS_TAB),
        "unfiled_caveat": ("" if claimed_ok else
                           "<b>Inflated:</b> the organizer engine's audit.json was "
                           "absent, so chapter and country rooms could not be "
                           "excluded from this list. "),
        "act_foot": act_foot,
        "today": e(today.strftime("%Y-%m-%d")),
    }
    return body


def build_report(subjects, filed_out, unfiled, today, measured, act_meta,
                 claimed_ok=True):
    """The standalone document; `build_body` is the seam the combined summary
    composes from — see summarize_audits.py."""
    return rs.page("Slack Topics Audit",
                   build_body(subjects, filed_out, unfiled, today, measured,
                              act_meta, claimed_ok))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-topics-audit",
                    help="output basename (default: slack-topics-audit)")
    ap.add_argument("--cache", default=".slack-audit-cache",
                    help="directory for raw API pulls")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--no-pdf", action="store_true", help="write HTML only")
    args = ap.parse_args()

    # Before any collection — this repo is public and the cache holds the
    # directory. Same gate as the other two engines.
    rs.assert_git_ignored(args.cache + os.sep, args.out + ".html", args.out + ".pdf")
    os.makedirs(args.cache, exist_ok=True)
    os.chmod(args.cache, 0o700)

    api = Slack()
    api.require_scopes("channels:read", "groups:read")
    who = api.ok("auth.test")
    team_id = who.get("team_id")
    print("workspace: %s (%s)" % (who.get("team"), team_id))

    print("reading the %s tab ..." % TOPICS_TAB)
    topics = load_topics()
    print("  %d classified channel(s) on the sheet" % len(topics))

    chan_path = os.path.join(args.cache, "channels.json")
    chans = jsoncache.read(chan_path, args.refresh, team_id, note=print)
    if chans is None:
        print("  fetching channels ...")
        chans = channels(api)
        jsoncache.write(chan_path, chans, team_id)
    else:
        print("  reusing cached channel list (%d, %s)"
              % (len(chans), jsoncache.age(chan_path)))

    claimed = chapter_claimed(args.cache)
    if claimed is None:
        print("  NOTE: no audit.json — chapter rooms cannot be excluded, so the "
              "unclassified list is inflated. Run audit_organizers.py first.")
    subjects, filed_out, unfiled = classify(chans, topics, claimed)
    print("  %d subject room(s), %d filed as not-a-topic, %d unclassified"
          % (len(subjects), len(filed_out), len(unfiled)))

    today = dt.datetime.now(dt.timezone.utc)
    act_path = os.path.join(args.cache, "activity.json")
    act = jsoncache.read(act_path, team_id=team_id, note=print)
    act_meta = None
    if act:
        act_meta = {"age": jsoncache.age(act_path),
                    "days": max(r.get("window_days", 90) for r in act.values())}
    measured = attach_activity(subjects, act, today)
    if act_meta:
        print("  activity: %d/%d subject rooms measured (%s)"
              % (measured, len(subjects), act_meta["age"]))
    else:
        # Never report "0 quiet channels" when the truth is "nothing measured".
        print("  NOTE: no activity cache — dormancy is unmeasured and the page "
              "says so. Run audit_activity.py first.")

    html_doc = build_report(subjects, filed_out, unfiled, today, measured,
                            act_meta, claimed_ok=claimed is not None)
    html_path = args.out + ".html"
    rs.write_private(html_path, html_doc)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path),
                                     os.path.abspath(args.out + ".pdf")))


if __name__ == "__main__":
    main()
