#!/usr/bin/env python3
"""The deliverable: ONE PDF, opening on **where should we focus?**

Not a fourth audit. It ranks what the three engines already measured — biggest
cost first, with the evidence and the effort attached — and then carries the
three full reports behind it as appendices, so the whole audit is a single file
to hand over rather than four to keep in step with each other.

Every number here is carried through from an engine's own cache. Nothing is
re-measured and nothing is estimated: if an engine did not measure something,
this page says so rather than filling the gap.
"""

import argparse
import datetime as dt
import html
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events import report_style as rs  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_members  # noqa: E402
import audit_organizers  # noqa: E402
import audit_topics  # noqa: E402
from audit_topics import (NEVER, QUIET, QUIET_DAYS, UNKNOWN,  # noqa: E402
                          UNMEASURED, load_topics, members_of,
                          near_duplicates, state_of)

e = html.escape


def need(cache, name, engine):
    """One engine's cache, or an abort naming the engine that writes it.

    `note=print` matters: without it jsoncache swallows its own reason for
    discarding a cache (wrong format, wrong workspace), and the abort below
    reports a file as *missing* while it sits plainly on disk — sending the
    operator after a filesystem problem that isn't there.
    """
    path = os.path.join(cache, name)
    data = jsoncache.read(path, note=print)
    if data is None:
        raise SystemExit(
            "ABORT: %s is missing (or was discarded) from %s. Run %s first — "
            "this page ranks what the engines measured and must never estimate "
            "a number they did not." % (name, cache, engine))
    if not data:
        # Empty is not None, so the check above lets it through — and then a
        # max() over it raises a bare ValueError traceback instead of this.
        raise SystemExit(
            "ABORT: %s is empty. Re-run %s — an empty cache cannot support any "
            "number on this page." % (name, engine))
    return data


def same_workspace(cache, names):
    """Every cache must carry the same workspace stamp.

    This is the ONE script that joins four caches, and the only one that makes
    no Slack call — so it cannot pass a live `team_id` to jsoncache and its
    per-read workspace check is a no-op. jsoncache's own docstring names the
    consequence: "Joining one workspace's channels to another's members yields a
    coherent, entirely wrong report." Comparing the four stamps to each other
    needs no API call and closes the whole class.
    """
    stamps = {}
    for name in names:
        try:
            with open(os.path.join(cache, name), encoding="utf-8") as fh:
                stamps[name] = json.load(fh).get("team_id")
        except (OSError, ValueError) as exc:
            raise SystemExit("ABORT: cannot read %s to check its workspace "
                             "stamp: %s" % (name, exc))
    missing = sorted(n for n, t in stamps.items() if not t)
    if missing:
        raise SystemExit(
            "ABORT: %s carry no workspace stamp, so they cannot be shown to "
            "describe the same Slack. Re-run the engines that write them."
            % ", ".join(missing))
    if len(set(stamps.values())) > 1:
        raise SystemExit(
            "ABORT: these caches come from different workspaces — joining them "
            "would produce a coherent and entirely wrong report:\n  %s"
            % "\n  ".join("%s: %s" % kv for kv in sorted(stamps.items())))
    return next(iter(stamps.values()))


def org_findings(audit):
    ch = audit["chapters"]
    pub_org = [c for c in ch if c["organizers_channel"] and not c["organizers_private"]]
    no_pub = [c for c in ch if not c["public"]]
    no_org = [c for c in ch if not c["organizers_channel"]]
    zero = [c for c in ch if c["organizers_channel"] and not c["accepted"]]
    gaps = []
    for c in ch:
        acc = c["accepted"] or []
        if acc and c["organizers_channel"]:
            out = [a for a in acc if not a.get("in_organizers")]
            if out:
                gaps.append((c["city"], len(out), len(acc)))
    gaps.sort(key=lambda g: -g[1])
    # `unresolved` records are members whose directory lookup FAILED. The
    # organizer engine reports them as "could not identify", explicitly not as
    # unaccounted people, because filing them under "nobody reviewed them"
    # asserts a judgement nobody made. Counting them here would do exactly that.
    no_row = [(city, u) for c in ch for u in (c["unaccounted"] or [])
              if not u["is_staff"] and not u.get("unresolved")
              and not u["intake_status"]
              for city in (c["city"],)]
    return {"chapters": len(ch), "pub_org": pub_org, "no_pub": no_pub,
            "no_org": no_org, "zero": zero, "gaps": gaps, "no_row": no_row}


def topic_findings(subjects, today):
    """Select from the records `audit_topics` already built. Derive nothing.

    This function used to build its own parallel subject shape and re-read the
    Topics tab, and it drifted immediately: it counted rooms the sweep never
    reached as "quiet", so the focus page and Appendix B of the SAME PDF
    reported different numbers and the inflated one was on the cover. Selecting
    from `audit_topics`' own records through its own `state_of` makes that
    divergence unwriteable.
    """
    by_state = {}
    for s in subjects:
        by_state.setdefault(state_of(s), []).append(s)
    quiet = by_state.get(QUIET, [])
    never = by_state.get(NEVER, [])
    unmeasured = by_state.get(UNMEASURED, []) + by_state.get(UNKNOWN, [])

    # Stranded membership counts MEASURED silence only, and only rooms whose
    # size Slack actually reported — "not reported" is not "zero".
    dormant = quiet + never
    sizes = [members_of(x) for x in dormant]
    return {"subj": subjects,
            "quiet": dormant,
            "alive": by_state.get("live", []),
            "unmeasured": unmeasured,
            "nopurp": [x for x in subjects
                       if not (x["chan"].get("purpose") or "").strip()],
            "dups": near_duplicates(subjects),
            "stranded": sum(m for m in sizes if m is not None),
            "stranded_unknown": sum(1 for m in sizes if m is None)}


def build(o, t, directory, today, ages):
    humans = [u for u in directory
              if not u["is_bot"] and not u["is_app_user"] and u["id"] != "USLACKBOT"]
    active = [u for u in humans if not u["deleted"]]

    top_quiet = sorted(t["quiet"], key=lambda s: -(members_of(s) or 0))[:8]
    top_gaps = o["gaps"][:6]

    acts = []
    if o["pub_org"]:
        acts.append((
            "Make %s private" % ", ".join("#" + c["organizers_channel"] for c in o["pub_org"]),
            "Organizer coordination — venue costs, budgets, speaker problems — is "
            "readable by all %s workspace members. It is the only finding here "
            "that is a live confidentiality breach rather than a backlog."
            % format(len(active), ","),
            "minutes", "workspace admin", "now"))
    if o["no_row"]:
        acts.append((
            "Review %d unreviewed people sitting in organizers channels" % len(o["no_row"]),
            "They hold no intake row at all, so nobody ever decided they should be "
            "there. Private organizer rooms are the one place in this workspace "
            "where membership is supposed to mean a decision was made.",
            "a few hours", "ops", "now"))
    if t["quiet"]:
        acts.append((
            "Decide the fate of %d quiet topic channels" % len(t["quiet"]),
            "%s of %s subject rooms have had no human message in %d+ days, holding "
            "%s memberships between them. Every one of those is a person who "
            "joined a subject and now sees nothing happen in it."
            % (len(t["quiet"]), len(t["subj"]), QUIET_DAYS,
               format(t["stranded"], ",")),
            "a session, then a sweep", "community lead", "now"))
    if o["gaps"]:
        acts.append((
            "Get %d accepted organizers into their own rooms"
            % sum(g[1] for g in o["gaps"]),
            "Across %d chapters, people we accepted as organizers are not in the "
            "channel where their chapter is run. Worst: %s."
            % (len(o["gaps"]),
               "; ".join("%s (%d of %d)" % g for g in top_gaps[:3])),
            "an afternoon", "ops", "next"))
    if o["no_org"]:
        acts.append((
            "Provision %d missing organizer channels" % len(o["no_org"]),
            "These chapters have a public city room but nowhere private to run it: "
            "%s." % ", ".join(c["city"] for c in o["no_org"][:8]),
            "an afternoon", "workspace admin", "next"))
    if o["zero"]:
        acts.append((
            "Audit %d organizer rooms with no accepted organizer at all" % len(o["zero"]),
            "Their entire roster is people nobody reviewed: %s."
            % ", ".join(c["city"] for c in o["zero"][:8]),
            "a few hours", "ops", "next"))
    if t["nopurp"]:
        acts.append((
            "Write a purpose line for %d subject channels" % len(t["nopurp"]),
            "The purpose line is the whole of what a newcomer sees in the channel "
            "browser. These show nothing, so joining them is a guess — the "
            "cheapest newcomer-experience fix available.",
            "an hour", "community lead", "next"))
    if t["dups"]:
        acts.append((
            "Judge %d proposed channel overlaps" % len(t["dups"]),
            "Pairs that look like the same subject in two rooms, splitting the "
            "audience of both. Proposals only — merging destroys history.",
            "a session", "community lead", "later"))
    if o["no_pub"]:
        acts.append((
            "Give %d chapters a public room" % len(o["no_pub"]),
            "%s have no public city channel, so members in those cities have "
            "nowhere local to land."
            % ", ".join(c["city"] for c in o["no_pub"]),
            "minutes each", "workspace admin", "later"))

    def row(cells):
        return "<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in cells)

    quiet_rows = "".join(row([
        "<b>#%s</b>" % e(s["name"]), e(s["theme"]),
        '<span class="n">%s</span>' % (
            "?" if members_of(s) is None else format(members_of(s), ",")),
        e({UNMEASURED: "not measured", UNKNOWN: "scan cap reached",
           NEVER: "silent all window"}.get(state_of(s),
                                           "%s days" % s["quiet_days"])),
    ]) for s in top_quiet)

    gap_rows = "".join(row([
        e(city), '<span class="n">%d of %d</span>' % (out, tot),
    ]) for city, out, tot in top_gaps)

    alive_rows = "".join(row([
        "<b>#%s</b>" % e(s["name"]),
        '<span class="n">%s</span>' % (
            "?" if members_of(s) is None else format(members_of(s), ",")),
        # "+" marks a floor: the scan ran out before covering the window, so
        # the count is a slice and must not be ranked as a total.
        '<span class="n">%s%s</span>' % (
            format(s["act"].get("human_msgs", 0), ","),
            "+" if audit_topics.truncated(s) else ""),
        '<span class="n">%s</span>' % format(s["act"].get("posters", 0), ","),
    ]) for s in sorted(t["alive"], key=lambda s: -s["act"].get("human_msgs", 0))[:8])

    body = """
<h1>Slack: where to focus</h1>
<p class="lede">One page over three audits — organizers, topics and members —
ranked by what it costs to leave alone. Every figure is carried through from the
engine that measured it; nothing on this page is estimated.</p>

<div class="stats">
  <div class="stat s-bad"><span class="v">%(pub_org)s</span><span class="k">public organizer rooms</span></div>
  <div class="stat s-warn"><span class="v">%(quiet)s / %(subj)s</span><span class="k">topic rooms gone quiet</span></div>
  <div class="stat s-warn"><span class="v">%(gapn)s</span><span class="k">organizers outside their room</span></div>
  <div class="stat"><span class="v">%(norow)s</span><span class="k">unreviewed people in organizer rooms</span></div>
  <div class="stat"><span class="v">%(chapters)s</span><span class="k">chapters audited</span></div>
</div>

<h2>Do these, in this order</h2>
%(actions)s

<h2>The three things behind that ranking</h2>

<h3>1. %(pubhead)s</h3>
<p>%(pubtext)s</p>

<h3>2. The topic map has gone quiet under its own membership</h3>
<p>%(quiet)s of %(subj)s subject channels have seen no human message in
%(qd)d days or more, and %(stranded)s memberships sit inside them. This is the
largest single finding in the audit and the least visible: nothing looks broken,
the rooms simply do not move. %(unmeasured_note)s
Ranked by how many people are stranded:</p>
<table><thead><tr><th>Channel</th><th>Theme</th><th class="n">Members</th>
<th>Last human message</th></tr></thead><tbody>%(quiet_rows)s</tbody></table>
<p>For contrast, the rooms that <i>are</i> working — this is what a live subject
looks like, and how few of them there are:</p>
<table><thead><tr><th>Channel</th><th class="n">Members</th>
<th class="n">Messages</th><th class="n">Posters</th></tr></thead>
<tbody>%(alive_rows)s</tbody></table>

<h3>3. Accepted organizers are not where the work happens</h3>
<p>Across %(ngaps)d chapters, people we accepted are missing from their own
organizers channel. They were reviewed, approved, and then never landed:</p>
<table><thead><tr><th>Chapter</th><th class="n">Missing</th></tr></thead>
<tbody>%(gap_rows)s</tbody></table>

<h2>What this page is not telling you</h2>
<ul>
<li><b>Message counts are floors.</b> Thread replies are invisible to the API
except broadcasts, so a room that lives in threads looks quieter than it is.
Treat "quiet" as a prompt to look, not a verdict.</li>
<li><b>Private rooms are undercounted everywhere.</b> Slack lists only the
private channels the audit's own token belongs to.</li>
<li><b>Organizer identity is joined by email only.</b> "No Slack account" means
no account under the address we hold — an upper bound on the gap, not a fact
about a person.</li>
<li><b>The topic classification is human.</b> Every subject counted here is a
subject because someone wrote it on the Topics tab.</li>
<li><b>Nothing here measures lurkers.</b> Poster counts are writers only.</li>
</ul>
<footer>Built %(today)s from: organizer audit (%(a_org)s), activity sweep
(%(a_act)s), channel list (%(a_chan)s), user directory (%(a_users)s).
Read-only throughout; no message text was retained.</footer>
""" % {
        "pub_org": len(o["pub_org"]),
        "quiet": len(t["quiet"]),
        "subj": len(t["subj"]),
        "gapn": sum(g[1] for g in o["gaps"]),
        "norow": len(o["no_row"]),
        "chapters": o["chapters"],
        "actions": rs.actions(acts),
        # "?" not %d: organizers_channel_members is None when Slack did not
        # report a size, and %d on None raises TypeError — which would kill the
        # entire four-part render at the last step. audit_organizers guards the
        # same field the same way.
        "pubtext": e(
            "#%s is a public channel with %s members. Everything organizers say "
            "there — venue costs, budgets, speaker problems, and anything said "
            "about a person — is readable by the entire workspace. Every other "
            "organizer room in the audit is private."
            % (o["pub_org"][0]["organizers_channel"],
               "an unreported number of"
               if o["pub_org"][0]["organizers_channel_members"] is None
               else format(o["pub_org"][0]["organizers_channel_members"], ",")))
        if o["pub_org"] else "No organizer channel is public. This was the "
                             "highest-severity class of finding and it is clear.",
        "pubhead": e(
            "No organizer room is public" if not o["pub_org"]
            else ("One room is a live confidentiality problem"
                  if len(o["pub_org"]) == 1
                  else "%d rooms are a live confidentiality problem"
                       % len(o["pub_org"]))),
        "qd": QUIET_DAYS,
        "stranded": format(t["stranded"], ",") + (
            " (plus %d room(s) whose size Slack did not report)"
            % t["stranded_unknown"] if t["stranded_unknown"] else ""),
        "unmeasured_note": e(
            "%d further subject room(s) could not be measured at all and are "
            "excluded from every figure above." % len(t["unmeasured"])
        ) if t["unmeasured"] else "",
        "quiet_rows": quiet_rows,
        "alive_rows": alive_rows,
        "ngaps": len(o["gaps"]),
        "gap_rows": gap_rows,
        "today": e(today.strftime("%Y-%m-%d")),
        "a_org": e(ages["audit"]), "a_act": e(ages["activity"]),
        "a_chan": e(ages["channels"]), "a_users": e(ages["users"]),
    }
    return body


#: Appendices carry their own <h1>, so each starts a fresh printed page and the
#: PDF reads as four documents in one file rather than one long scroll.
APPENDIX_CSS = """
.appendix{break-before:page;page-break-before:always;border-top:2px solid var(--line-hard,#CFCFC9);
  margin-top:3rem;padding-top:2rem}
.appendix > .tag{display:inline-block;font-size:.72rem;letter-spacing:.08em;
  text-transform:uppercase;opacity:.6;margin-bottom:.4rem}
"""


def appendix(label, body):
    return ('<section class="appendix"><span class="tag">%s</span>%s</section>'
            % (e(label), body))


def build_document(focus_body, appendices):
    """Focus page + the three full reports, as one self-contained document.

    The organizer report's filter buttons are deliberately dropped: its script
    hides rows by a global `tbody tr` query, which in a combined document would
    reach into the other two appendices. Nothing is lost — this output is a PDF,
    where the buttons were never clickable anyway.
    """
    parts = [focus_body] + [appendix(lbl, b) for lbl, b in appendices]
    return rs.page("AAIF Slack Audit", "".join(parts), extra_css=APPENDIX_CSS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-full-audit",
                    help="output basename (default: slack-full-audit)")
    ap.add_argument("--cache", default=".slack-audit-cache", help="cache directory")
    ap.add_argument("--no-pdf", action="store_true", help="write HTML only")
    ap.add_argument("--keep-html", action="store_true",
                    help="keep the intermediate .html beside the PDF")
    args = ap.parse_args()

    rs.assert_git_ignored(args.cache + os.sep, args.out + ".html", args.out + ".pdf")

    audit = need(args.cache, "audit.json", "audit_organizers.py")
    act = need(args.cache, "activity.json", "audit_activity.py")
    chans = need(args.cache, "channels.json", "audit_organizers.py")
    directory = need(args.cache, "users.json", "audit_members.py")
    ages = {n: jsoncache.age(os.path.join(args.cache, n + ".json"))
            for n in ("audit", "activity", "channels", "users")}
    team_id = same_workspace(args.cache, ("audit.json", "activity.json",
                                          "channels.json", "users.json"))

    today = dt.datetime.now(dt.timezone.utc)
    # load_topics() is a LIVE Sheets read. Doing it once matters twice over:
    # two reads cost two round-trips, and they can straddle an edit, so the
    # focus page and Appendix B could describe two different classifications.
    claimed = audit_topics.chapter_claimed(args.cache, team_id)
    subjects, filed_out, unfiled = audit_topics.classify(
        chans, load_topics(), claimed)
    measured = audit_topics.attach_activity(subjects, act, today)
    o = org_findings(audit)
    t = topic_findings(subjects, today)
    print("  %d chapters, %d subject rooms (%d quiet), %d organizer gaps"
          % (o["chapters"], len(t["subj"]), len(t["quiet"]), len(o["gaps"])))

    print("  composing the appendices ...")
    org_body, _ = audit_organizers.render_body(
        audit["chapters"], audit.get("orphan_cities") or {},
        audit.get("duplicates") or 0, today)
    # The organizer body ships filter buttons driven by a script we deliberately
    # drop (its `tbody tr` query is global and would reach into the other two
    # appendices). Dropping the script alone left five dead controls in the PDF
    # that look interactive; strip the markup too, so "nothing is lost" is true.
    org_body = audit_organizers.strip_controls(org_body)

    # window_days is INDEXED, not .get(...,90): a record without the key is
    # schema drift, and defaulting would mint the very number the page then
    # prints as the measured window.
    window = max(r["window_days"] for r in act.values())
    top_body = audit_topics.build_body(
        subjects, filed_out, unfiled, today, measured,
        {"age": ages["activity"], "days": window},
        claimed_ok=claimed is not None)

    ids = {u for r in act.values() for u in r.get("poster_ids", ())}
    activity = {"poster_ids": ids, "days": window,
                "channels": len(act), "age": ages["activity"]} if ids else None
    # The same completeness tripwire audit_members.main() runs. Calling
    # build_body directly would publish a short user pull with no guard, and
    # every count on Appendix C plus the member total in action #1 would
    # understate without saying so.
    audit_members.assert_directory_complete(chans, directory, note=print)
    mem_body = audit_members.build_body(chans, directory, today, activity)

    doc = build_document(build(o, t, directory, today, ages),
                         [("Appendix A — organizers", org_body),
                          ("Appendix B — topics", top_body),
                          ("Appendix C — members", mem_body)])
    html_path = args.out + ".html"
    rs.write_private(html_path, doc)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path),
                                     os.path.abspath(args.out + ".pdf")))
        # One PDF is the deliverable. The HTML is scaffolding, and leaving it
        # behind means a second copy of the same member names and addresses
        # sitting in the working directory.
        if not args.keep_html:
            os.remove(html_path)
            print("removed %s (--keep-html to retain it)" % html_path)


if __name__ == "__main__":
    main()
