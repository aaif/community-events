#!/usr/bin/env python3
"""Audit real message activity per channel: the number the other two can't see.

The organizer and member audits ran on the Slack CLI token, which has no
history scope, so both print "activity is unreadable" on their face. The AAIF
app token carries `channels:history` / `groups:history`, and this script is
the extension that note in audit_members.py asks for: for every live channel
the token can see, the date of the last *human* message and the volume of
conversation in a trailing window.

Everything here is read-only, and **no message text is retained** — the cache
and the report hold timestamps and counts only (see
`aaif_events.slack.history_activity`). The channel list is reused from the
shared audit cache.

Known floors, stated on the page rather than papered over:

- Thread replies are invisible to `conversations.history` (except broadcasts),
  so every count is a floor on real conversation.
- Private channels are limited to those the token owner belongs to.
- A channel whose scan hit the message cap before crossing the window is
  reported as truncated, not ranked as if fully measured.
"""

import argparse
import datetime as dt
import html
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events import report_style as rs  # noqa: E402
from aaif_events.slack import Slack, channels, history_activity  # noqa: E402

e = html.escape

#: Buckets for "days since the last human message". Order is render order.
AGE_BUCKETS = (("this week", 0, 7), ("this month", 8, 31),
               ("this quarter", 32, 92), ("this year", 93, 365),
               ("1-2 years", 366, 730), ("over 2 years", 731, 10 ** 9))


def cached_channels(api, cache_dir, refresh, team_id):
    path = os.path.join(cache_dir, "channels.json")
    data = jsoncache.read(path, refresh, team_id, note=print)
    if data is not None:
        print("  reusing channel list (%d, %s)" % (len(data), jsoncache.age(path)))
        return data
    print("  fetching channels ...")
    data = channels(api)
    jsoncache.write(path, data, team_id)
    return data


def chapter_channel_names(cache_dir, team_id):
    """{channel name: city} for channels the organizer audit tied to a chapter.

    Read from the organizer audit's own output, so the two reports can never
    disagree about which channel is whose. Empty when that audit hasn't run —
    the chapter section is then omitted rather than re-derived badly here.
    Stamped like every other read: an audit.json from a different workspace
    would otherwise join its chapter map into this one's activity table.
    """
    path = os.path.join(cache_dir, "audit.json")
    data = jsoncache.read(path, team_id=team_id)
    if data is None:
        return {}
    out = {}
    for c in data.get("chapters", []):
        for key in ("public", "organizers_channel", "regional"):
            if c.get(key):
                out.setdefault(c[key], c["city"])
    return out


def collect(api, live, cache_dir, days, refresh, team_id, now_ts):
    """Per-channel activity stats, resumable: partial pulls are cached.

    Activity data ages, so entries are reused only within the same UTC day
    unless --refresh forces a full re-pull — fresh enough for a report, cheap
    enough that an interrupted 15-minute sweep resumes instead of restarting.
    """
    path = os.path.join(cache_dir, "activity.json")
    stats = jsoncache.read(path, refresh, team_id, note=print) or {}
    today = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc).date().isoformat()
    # Same day AND same window: a 90-day count rendered under a --days 30
    # headline is a wrong number that looks fully measured.
    stats = {k: v for k, v in stats.items()
             if v.get("day") == today and v.get("window_days") == days}
    if stats:
        print("  resuming: %d channels already pulled today" % len(stats))

    oldest = now_ts - days * 86400
    todo = [c for c in live if c["id"] not in stats]
    for i, c in enumerate(todo, 1):
        # poster_ids feed audit_members' "people seen posting" union — ids
        # only, in the same 0600 cache that already holds the full directory.
        rec = history_activity(api, c["id"], oldest, include_posters=True)
        rec["day"] = today
        rec["window_days"] = days
        stats[c["id"]] = rec
        if i % 20 == 0 or i == len(todo):
            jsoncache.write(path, stats, team_id)
            print("    %d/%d channels ..." % (i, len(todo)), flush=True)
    jsoncache.write(path, stats, team_id)
    return stats


def build_report(live, stats, chapter_of, days, today):
    def age_days(ts):
        # `today` is stamped before the sweep, so a message posted while the
        # sweep runs is newer than it; a negative age would fall outside every
        # AGE_BUCKET and trip the histogram check. It is simply "this week".
        return max(0, int((today.timestamp() - ts) // 86400))

    rows = []
    for c in live:
        s = stats[c["id"]]
        rows.append({**c, **s,
                     "age": age_days(s["last_human_ts"]) if s["last_human_ts"] else None,
                     "city": chapter_of.get(c["name"])})

    talked = [r for r in rows if r["human_msgs"]]
    silent_window = [r for r in rows if not r["human_msgs"] and r["window_complete"]]
    truncated = [r for r in rows if not r["window_complete"]]
    never_seen = [r for r in rows if r["last_human_ts"] is None]

    age_rows = [(lbl, sum(1 for r in rows if r["age"] is not None and lo <= r["age"] <= hi))
                for lbl, lo, hi in AGE_BUCKETS]
    age_rows.append(("no human message found", len(never_seen)))
    got = sum(n for _, n in age_rows)
    if got != len(rows):
        raise SystemExit("ABORT: the last-message histogram accounts for %d of %d "
                         "channels — the buckets are wrong." % (got, len(rows)))

    busiest = sorted(talked, key=lambda r: -r["human_msgs"])[:15]
    busy_html = "".join(
        '<tr><td><code class="chan">#%s</code>%s</td><td>%s</td>'
        '<td>%d</td><td>%d</td></tr>'
        % (e(r["name"]), " <span class=\"pill pill-mute\">%s</span>" % e(r["city"]) if r["city"] else "",
           format(r["human_msgs"], ",") + ("+" if not r["window_complete"] else ""),
           r["posters"], r["num_members"] or 0)
        for r in busiest)

    chap = sorted((r for r in rows if r["city"]), key=lambda r: (r["age"] is None, r["age"] or 0))
    chap_html = "".join(
        '<tr><td>%s</td><td><code class="chan">#%s</code></td><td>%s</td>'
        '<td>%d</td><td>%d</td></tr>'
        % (e(r["city"]), e(r["name"]),
           ("no human message found" if r["age"] is None
            else "today" if r["age"] == 0 else "%d days ago" % r["age"]),
           r["human_msgs"], r["posters"])
        for r in chap)

    quiet_chap = [r for r in chap if r["age"] is None or r["age"] > 92]
    todo = []
    if quiet_chap:
        todo.append((
            "Check on %d chapter channels silent for a quarter or more" % len(quiet_chap),
            "No human has posted in over 90 days in: "
            + ", ".join("#%s" % r["name"] for r in quiet_chap[:12])
            + (" and more" if len(quiet_chap) > 12 else "")
            + ". A silent room greets every newcomer the chapter sends there.",
            "1 hour", "Community leadership", "now"))
    old = [r for r in rows if r["age"] is not None and r["age"] > 730 and not r["city"]]
    if old:
        todo.append((
            "Review %d non-chapter channels with no human message in 2+ years" % len(old),
            "Candidates for archiving; each one adds noise to channel browsing "
            "for every member. This report names them; archiving stays a human call.",
            "2 hours", "Workspace admin", "next"))

    stamp = today.strftime("%-d %B %Y")
    pct = lambda n: "%d%%" % round(100 * n / (len(rows) or 1))  # noqa: E731
    body = f"""
<header>
  <div class="eyebrow">Channel-activity Slack audit &middot; {stamp}</div>
  <h1>Who is actually talking, where</h1>
  <p class="lede">For every live channel the audit token can see: when a person last posted, and how
  much conversation the last {days} days held. This is the measurement the other two audits could
  not make — their token had no history scope. No message text was retained.</p>
</header>

<div class="caveat"><strong>Every count is a floor.</strong> Thread replies are invisible to
<code class="chan">conversations.history</code>, so a channel that lives in threads under-counts
here. Private channels are limited to the {sum(1 for r in rows if r["is_private"])} the token
owner belongs to. {len(truncated)} very busy channel(s) hit the scan cap and are marked
&ldquo;+&rdquo;.</div>

<section>
  <div class="eyebrow">The numbers</div>
  <div class="stats">
    <div class="stat"><span class="v">{len(rows)}</span><span class="k">Live channels measured</span></div>
    <div class="stat s-ok"><span class="v">{pct(len(talked))}</span><span class="k">Had a human message in {days} days</span></div>
    <div class="stat s-warn"><span class="v">{pct(len(silent_window))}</span><span class="k">Silent all window</span></div>
    <div class="stat s-bad"><span class="v">{len(never_seen)}</span><span class="k">No human message found at all</span></div>
  </div>
</section>

<section>
  <div class="eyebrow">Last human message</div>
  {rs.bars(age_rows)}
</section>

<section>
  <div class="eyebrow">Busiest channels, last {days} days</div>
  <div class="tablewrap"><table><thead><tr><th>Channel</th><th class="n">Human msgs</th>
  <th class="n">People</th><th class="n">Members</th></tr></thead>{busy_html}</table></div>
</section>

{('<section><div class="eyebrow">Chapter channels</div>'
  '<div class="tablewrap"><table><thead><tr><th>Chapter</th><th>Channel</th>'
  '<th>Last human message</th><th class="n">Msgs (%d d)</th><th class="n">People</th>'
  '</tr></thead>%s</table></div></section>' % (days, chap_html))
 if chap else ''}

<section>
  <div class="eyebrow">What to do</div>
  {rs.actions(todo) if todo else '<p>Nothing urgent: no chapter channel has gone quiet.</p>'}
</section>
"""
    return rs.page("Slack channel activity — %s" % stamp, body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-activity-audit")
    ap.add_argument("--cache", default=".slack-audit-cache")
    ap.add_argument("--days", type=int, default=90,
                    help="trailing window for message counts (default 90)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull every channel even if pulled today")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    # The report names channels and how dead they are; the cache holds only
    # counts. Same public-repo rule as the other two audits regardless.
    rs.assert_git_ignored(args.cache + os.sep, args.out + ".html", args.out + ".pdf")
    os.makedirs(args.cache, exist_ok=True)
    os.chmod(args.cache, 0o700)

    api = Slack()
    api.require_scopes("channels:read", "groups:read",
                       "channels:history", "groups:history")
    who = api.ok("auth.test")
    team_id = who.get("team_id")
    print("workspace: %s (%s)" % (who.get("team"), team_id))

    chans = cached_channels(api, args.cache, args.refresh, team_id)
    live = sorted((c for c in chans if not c["is_archived"]),
                  key=lambda c: -(c["num_members"] or 0))
    print("  %d live channels to measure (~3s each on first pull)" % len(live))

    now = dt.datetime.now(dt.timezone.utc)
    stats = collect(api, live, args.cache, args.days, args.refresh,
                    team_id, now.timestamp())

    html_doc = build_report(live, stats, chapter_channel_names(args.cache, team_id),
                            args.days, now)
    html_path = args.out + ".html"
    rs.write_private(html_path, html_doc)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path),
                                     os.path.abspath(args.out + ".pdf")))


if __name__ == "__main__":
    main()
