#!/usr/bin/env python3
"""Audit the Slack workspace from the member's side: channels and accounts.

Collects channel metadata and the full user directory, then renders a
self-contained HTML report and (unless --no-pdf) a PDF beside it.

Everything here is read-only. Nothing measures message activity — the audit
token has no history or search scope — and the report says so on its face
rather than dressing up a proxy as engagement.
"""

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import report_style as rs  # noqa: E402
from aaif_events.slack import Slack, channels, scopes, users  # noqa: E402

# Channels every new member is auto-joined to. Membership in these measures
# signup, not participation, so they are reported separately from elective joins.
DEFAULT_JOIN = ("general", "questions-answered", "introduce-yourself",
                "be-shameless", "job-posts")

WEBMAIL = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "yahoo.com",
           "icloud.com", "protonmail.com", "proton.me", "live.com", "me.com", "aol.com"}


def cached(path, build, refresh=False, label=""):
    """Load a JSON cache, or build and store it. The user pull is slow — reuse it."""
    if os.path.exists(path) and not refresh:
        with open(path) as fh:
            data = json.load(fh)
        print("  reusing %s (%d records)" % (os.path.basename(path), len(data)))
        return data
    print("  fetching %s ..." % (label or os.path.basename(path)))
    data = build()
    with open(path, "w") as fh:
        json.dump(data, fh)
    print("  fetched %d records" % len(data))
    return data


def build_report(chans, directory, today):
    def age(epoch):
        if not epoch:
            return None
        return (today - dt.datetime.fromtimestamp(epoch, dt.timezone.utc)).days

    live = [c for c in chans if not c["is_archived"]]
    arch = [c for c in chans if c["is_archived"]]
    pub = [c for c in live if not c["is_private"]]
    priv = [c for c in live if c["is_private"]]

    humans = [u for u in directory
              if not u["is_bot"] and not u.get("is_app_user") and u["id"] != "USLACKBOT"]
    active = [u for u in humans if not u["deleted"]]
    deact = [u for u in humans if u["deleted"]]
    bots = [u for u in directory if u["is_bot"] or u.get("is_app_user")]

    size_rows = [(lbl, sum(1 for c in live if lo <= c["num_members"] <= hi))
                 for lbl, lo, hi in [("1-2", 1, 2), ("3-5", 3, 5), ("6-20", 6, 20),
                                     ("21-100", 21, 100), ("101-1,000", 101, 1000),
                                     ("1,000+", 1001, 10 ** 9)]]

    # Human topic/purpose edits only. The channel `updated` field is a bulk
    # migration stamp and must never be used as a staleness signal.
    edit_rows = [(lbl, sum(1 for c in live
                           if max(c["topic_last_set"], c["purpose_last_set"])
                           and lo <= age(max(c["topic_last_set"], c["purpose_last_set"])) <= hi))
                 for lbl, lo, hi in [("under 1 year", 0, 365), ("1-2 years", 366, 730),
                                     ("2-3 years", 731, 1095), ("3-5 years", 1096, 1825),
                                     ("over 5 years", 1826, 10 ** 9)]]
    nodesc = [c for c in live if not c["topic"] and not c["purpose"]]
    edit_rows.append(("never set", len(nodesc)))

    prof_rows = [(lbl, sum(1 for u in active
                           if age(u.get("updated")) is not None
                           and lo <= age(u.get("updated")) <= hi))
                 for lbl, lo, hi in [("under 3 months", 0, 90), ("3-12 months", 91, 365),
                                     ("1-2 years", 366, 730), ("2-3 years", 731, 1095),
                                     ("3-5 years", 1096, 1825), ("over 5 years", 1826, 10 ** 9)]]

    years = sorted(Counter(dt.datetime.fromtimestamp(c["created"], dt.timezone.utc).year
                           for c in chans if c["created"]).items())
    by_name = {c["name"]: c for c in pub}
    total_mem = sum(c["num_members"] for c in pub)
    default_mem = sum(by_name[n]["num_members"] for n in DEFAULT_JOIN if n in by_name)
    elective = total_mem - default_mem
    top10 = sorted(pub, key=lambda c: -c["num_members"])[:10]
    small = sorted([c for c in live if c["num_members"] <= 8], key=lambda c: c["num_members"])

    domains = Counter(u["email"].split("@")[-1] for u in active if u["email"])
    webmail_share = 100 * sum(domains[d] for d in WEBMAIL) // max(len(active), 1)
    top_corp = next(((d, n) for d, n in domains.most_common(60)
                     if d not in WEBMAIL and not d.endswith((".edu", ".ac.uk"))), ("—", 0))

    no_photo = sum(1 for u in active if not u["has_avatar"])
    unconf = sum(1 for u in active if u.get("is_email_confirmed") is False)
    guests = sum(1 for u in active if u["is_restricted"] or u["is_ultra_restricted"])

    stamp = today.strftime("%-d %B %Y")
    body = f"""
<header>
  <div class="eyebrow">Member-side workspace audit &middot; {stamp}</div>
  <h1>The member side: the workspace they actually meet</h1>
  <p class="lede">{len(chans)} channels and {len(directory):,} accounts, read through a
  read-only token. How channels are sized, aged and described, and how many accounts
  behind the {len(active):,}-member headline are real, active people.</p>
</header>

<div class="caveat"><strong>What this cannot tell you: message activity.</strong>
The audit token has no <code class="chan">channels:history</code> and no
<code class="chan">search:read</code>, so <em>last message posted</em> is unreadable and no
figure below is derived from one. The channel <code class="chan">updated</code> field is not
a substitute — a bulk migration reset it in blocks. For real activity use the admin
<strong>Analytics &rarr; Channels / Members</strong> CSV export.</div>

<section>
  <div class="eyebrow">Channels</div>
  <h2>{len(chans)} channels, {len(arch)} of them retired</h2>
  <div class="stats" style="margin-top:18px">
    <div class="stat"><span class="v">{len(live)}</span><span class="k">Live channels</span></div>
    <div class="stat"><span class="v">{len(pub)}</span><span class="k">Public</span></div>
    <div class="stat"><span class="v">{len(priv)}</span><span class="k">Private (visible)</span></div>
    <div class="stat s-warn"><span class="v">{len(arch)}</span><span class="k">Archived</span></div>
    <div class="stat"><span class="v">{sum(1 for c in live if c['is_ext_shared'])}</span><span class="k">Externally shared</span></div>
  </div>
  <div class="two" style="margin-top:22px">
    <div class="card"><h3>How big channels are</h3>
      <p class="sub">Live channels by member count</p>{rs.bars(size_rows)}</div>
    <div class="card"><h3>When someone last described a channel</h3>
      <p class="sub">Age of the newest topic or purpose edit</p>{rs.bars(edit_rows, "warn")}
      <p class="note">{len(nodesc)} live channels ({100 * len(nodesc) // max(len(live), 1)}%) have
      never had a topic or purpose set. A newcomer landing there is told nothing about what the
      channel is for.</p></div>
  </div>
</section>

<section>
  <div class="eyebrow">Concentration</div>
  <h2>{len(DEFAULT_JOIN)} channels hold {100 * default_mem // max(total_mem, 1)}% of all memberships</h2>
  <p class="lede">Public-channel memberships total {total_mem:,} across {len(pub)} channels. The
  ones every new member is joined to on signup account for {default_mem:,} of them, so membership
  measures <em>signup</em>, not participation.</p>
  <div class="stack">
    <div class="seg" style="background:var(--accent); width:{100 * default_mem / max(total_mem, 1)}%">auto-join {100 * default_mem // max(total_mem, 1)}%</div>
    <div class="seg" style="background:var(--ink-faint); width:{100 * elective / max(total_mem, 1)}%">elective {100 * elective // max(total_mem, 1)}%</div>
  </div>
  <div class="legend">
    <span><span class="sw" style="background:var(--accent)"></span>{default_mem:,} in the auto-join channels</span>
    <span><span class="sw" style="background:var(--ink-faint)"></span>{elective:,} chosen — about {elective / max(len(active), 1):.1f} per active member</span>
  </div>
  <div class="two" style="margin-top:22px">
    <div class="card"><h3>The ten largest channels</h3>
      <p class="sub">Share of all public memberships</p>
      {rs.bars([("#" + c["name"], c["num_members"]) for c in top10])}</div>
    <div class="card"><h3>Small live channels</h3>
      <p class="sub">{len(small)} channels with 8 or fewer members</p>
      <div class="chips">{''.join('<span class="chip">#%s · %d</span>' % (c["name"], c["num_members"]) for c in small)}</div></div>
  </div>
</section>

<section>
  <div class="eyebrow">Lifecycle</div>
  <h2>Channels created per year</h2>
  <div class="card" style="margin-top:16px">{rs.bars([(str(y), n) for y, n in years])}</div>
</section>

<section>
  <div class="eyebrow">Accounts</div>
  <h2>{len(active):,} active people — and what's behind that number</h2>
  <div class="stats" style="margin-top:18px">
    <div class="stat"><span class="v">{len(directory):,}</span><span class="k">Total accounts</span></div>
    <div class="stat s-ok"><span class="v">{len(active):,}</span><span class="k">Active humans</span></div>
    <div class="stat s-warn"><span class="v">{len(deact):,}</span><span class="k">Deactivated</span></div>
    <div class="stat"><span class="v">{len(bots)}</span><span class="k">Bots &amp; apps</span></div>
    <div class="stat"><span class="v">{sum(1 for u in active if u['is_admin'])}</span><span class="k">Admins</span></div>
  </div>
  <div class="two" style="margin-top:22px">
    <div class="card"><h3>How real the roster is</h3>
      <p class="sub">Among the {len(active):,} active humans</p>
      {rs.bars([("no profile photo", no_photo), ("email unverified", unconf),
                ("deactivated", len(deact)), ("guest accounts", guests)], "bad")}
      <p class="note">{len(deact):,} accounts ({100 * len(deact) / max(len(humans), 1):.1f}%) have
      ever been deactivated. {unconf:,} people signed up and never confirmed their email.</p></div>
    <div class="card"><h3>How long since a profile was touched</h3>
      <p class="sub">Active humans, by age of last profile change</p>{rs.bars(prof_rows, "warn")}
      <p class="note">The only per-person recency field this token can read. It moves on any
      settings change, so a departed member and a content lurker look identical — a floor on
      staleness, never a read on engagement.</p></div>
  </div>
</section>

<section>
  <div class="eyebrow">Who they are</div>
  <h2>Personal addresses, overwhelmingly</h2>
  <div class="card" style="margin-top:16px">
    {rs.bars(list(domains.most_common(12)))}
    <p class="note">{webmail_share}% of active members signed up with consumer webmail. The largest
    employer domain, {top_corp[0]}, accounts for {top_corp[1]} people — an individual-practitioner
    community, not a set of corporate delegations. There is no employer to route through, and no
    company address to reach someone at once they move on.</p>
  </div>
</section>

<section>
  <div class="eyebrow">Honest limits</div>
  <h2>What this report is not built on</h2>
  <ul class="plain">
    <li><strong>No message data.</strong> Nothing here measures whether a channel is
    <em>talking</em> — only whether it exists, who is in it, and how it is described.</li>
    <li><strong>Private channels are undercounted.</strong> Only the {len(priv)} the audit account
    belongs to were visible. Probing other people's memberships does not help:
    <code class="chan">users.conversations</code> filters results to the caller's own visibility.
    A workspace-wide list needs Enterprise Grid.</li>
    <li><strong>Profile-update age is not engagement.</strong> It moves on any settings change.</li>
  </ul>
</section>

<footer>Collected from the Slack Web API: <code class="chan">conversations.list</code>
({len(chans)} conversations, public and private, archived included),
<code class="chan">users.list</code> ({len(directory):,} accounts) and
<code class="chan">team.info</code>. No message content was read, and none could be.</footer>
"""
    return rs.page("Slack Members Audit", body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-members-audit",
                    help="output basename (default: slack-members-audit)")
    ap.add_argument("--cache", default=".slack-audit-cache",
                    help="directory for raw API pulls")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--no-pdf", action="store_true", help="write HTML only")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)
    api = Slack()
    who = api.ok("auth.test")
    print("workspace: %s (%s)" % (who.get("team"), who.get("team_id")))
    have = {s.strip() for s in scopes()}
    for needed in ("channels:read", "users:read"):
        if needed not in have:
            raise SystemExit("token is missing the %s scope — re-run `slack auth login`." % needed)
    if "channels:history" in have:
        print("  note: this token HAS channels:history — the report's 'no message data'")
        print("        caveat is now false and the script should be extended.")

    chans = cached(os.path.join(args.cache, "channels.json"),
                   lambda: channels(api), args.refresh, "channel list")
    directory = cached(
        os.path.join(args.cache, "users.json"),
        lambda: users(api, progress=lambda n: print("    %d users..." % n, flush=True)),
        args.refresh, "user directory (slow on a large workspace)")

    html = build_report(chans, directory, dt.datetime.now(dt.timezone.utc))
    html_path = args.out + ".html"
    with open(html_path, "w") as fh:
        fh.write(html)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path), os.path.abspath(args.out + ".pdf")))


if __name__ == "__main__":
    main()
