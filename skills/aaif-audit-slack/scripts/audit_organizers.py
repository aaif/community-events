#!/usr/bin/env python3
"""Audit the Slack workspace from the organizer's side.

For every chapter on the Chapters List: is there a public city channel, is there
a private organizers channel, and are the organizers we accepted actually in
them? Plus the full roster of every organizers channel, split by whether we ever
accepted that person.

Read-only on both sides — the sheets are only read, and the Slack client refuses
any non-read method.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import report_style as rs  # noqa: E402
from aaif_events.slack import Slack, channels, lookup_emails, members  # noqa: E402

CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"
CHAPTERS_TAB = "Chapters & Teams"
INTAKE_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
INTAKE_TAB = "Organizers"
#: Exact-string statuses that count as accepted. Matching a prefix like
#: "Existing" once missed all 23 MLOps rows — keep this exact.
ACCEPTED = ("Accepted", "Existing (from MLOps)")

e = html.escape


# --------------------------------------------------------------------------
# Sheets, via the gws CLI (the repo's only sanctioned Drive path)
# --------------------------------------------------------------------------

#: Same retry/JSON pattern as aaif-sync-chapters — the Sheets API returns
#: intermittent 500s, and a one-shot read turns a blip into a failed audit.
_TRANSIENT = ("timed out", "internalError", "Internal error", "HTTP request failed",
              "Connection reset", "Connection refused", "Connection aborted",
              "temporarily", "rateLimit", "userRateLimit", "backendError")
# Bare "500"/"502" as substrings match any range or quota id containing those
# digits ("A500:K500 exceeds grid limits"), so a permanent error would burn the
# full backoff. Match them only as standalone HTTP statuses.
_TRANSIENT_STATUS = re.compile(r"(?<![0-9])(?:429|500|502|503|504)(?![0-9])")


def _transient(msg):
    return any(k in msg for k in _TRANSIENT) or bool(_TRANSIENT_STATUS.search(msg))


def gws_values(sheet_id, rng, retries=5):
    """Read one A1 range through `gws`, returning a list of rows."""
    cmd = ["gws", "sheets", "spreadsheets", "values", "batchGet",
           "--params", json.dumps({"spreadsheetId": sheet_id, "ranges": [rng]})]
    for attempt in range(retries):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            break
        msg = (proc.stderr or "") + (proc.stdout or "")
        if attempt < retries - 1 and _transient(msg):
            # Announce it: a silent backoff looks like a hang, and a run that
            # succeeds on attempt 4 should still leave a trace the API was sick.
            print("  gws read failed (attempt %d/%d), retrying in %ds: %s"
                  % (attempt + 1, retries, 2 * (attempt + 1), msg.strip()[:120]),
                  file=sys.stderr)
            time.sleep(2 * (attempt + 1))
            continue
        raise SystemExit("gws failed reading %s!%s:\n%s" % (sheet_id, rng, msg.strip()[:400]))
    # Split on "\n" only — NOT splitlines(), which also splits on U+2028 and
    # friends INSIDE cell values, corrupting the JSON when rejoined.
    text = "\n".join(ln for ln in proc.stdout.split("\n")
                     if "keyring backend" not in ln).strip()
    if not text:
        raise SystemExit("gws produced no JSON reading %s!%s" % (sheet_id, rng))
    ranges = json.loads(text).get("valueRanges") or [{}]
    return ranges[0].get("values", [])


def cell(row, i):
    return (row[i] if i < len(row) else "").strip()


def header_index(headers, tab, *names):
    idx = {}
    for name in names:
        if headers.count(name) > 1:
            raise SystemExit("ABORT: %r appears twice in %s — reads would be ambiguous."
                             % (name, tab))
        if name not in headers:
            raise SystemExit("ABORT: %s has no %r column." % (tab, name))
        idx[name] = headers.index(name)
    return idx


def read_chapters():
    rows = gws_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        raise SystemExit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, CHAPTERS_TAB, "City", "Organizers")
    out = []
    for row in rows[1:]:
        city = cell(row, idx["City"])
        if city:
            out.append({"city": city, "organizers_cell": cell(row, idx["Organizers"])})
    return out


def read_intake():
    rows = gws_values(INTAKE_ID, "%s!A:U" % INTAKE_TAB)
    if not rows:
        raise SystemExit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    headers = [h.strip() for h in rows[0]]
    idx = header_index(headers, INTAKE_TAB, "Status", "Full name", "Email",
                       "City (Existing)", "City (New)", "Chapter")
    people, seen, dupes = [], set(), 0
    for row in rows[1:]:
        if cell(row, idx["Status"]) not in ACCEPTED:
            continue
        # Same precedence as sync_crm: the human's Chapter assignment wins.
        city = cell(row, idx["Chapter"]) or cell(row, idx["City (New)"])
        if not city:
            existing = cell(row, idx["City (Existing)"])
            city = "" if existing.startswith("Other") else existing
        email = cell(row, idx["Email"]).lower()
        key = (email, city.lower())
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        people.append({"name": cell(row, idx["Full name"]), "email": email,
                       "status": cell(row, idx["Status"]), "city": city})
    return people, dupes


# --------------------------------------------------------------------------
# City -> channel matching
# --------------------------------------------------------------------------

def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fold_tight(s):
    return fold(s).replace("-", "")


def variants(city):
    """Plausible channel slugs for a city name."""
    head = fold(city.split(",")[0])
    out = {fold(city), fold(city).replace("-", ""), head, head.replace("-", "")}
    return {v for v in out if v}


def match_channels(chapters, chans, cfg):
    """Resolve each chapter to its public and organizers channel.

    Conservative by design: an exact or configured hit is a match, anything
    weaker is reported as a candidate for a human, never as coverage.
    """
    by_name = {c["name"]: c for c in chans}
    live = [c for c in chans if not c["is_archived"]]
    suffixes = tuple(cfg["organizer_suffixes"])
    out = []

    for ch in chapters:
        city = ch["city"]
        vs = variants(city)

        pub, how, candidates = None, "", []
        if city in cfg["public"]:
            alias = cfg["public"][city]
            if alias and alias in by_name and not by_name[alias]["is_archived"]:
                pub, how = by_name[alias], "alias"
            else:
                how = "known-none" if alias is None else "alias-missing:%s" % alias
        if not pub:
            for c in live:
                if c["is_private"]:
                    continue
                if any(c["name"] == p + v for v in vs for p in cfg["public_prefixes"]):
                    pub, how = c, how or "exact"
                    break
        if not pub:
            for c in live:
                if c["is_private"] or c["name"].endswith(suffixes):
                    continue
                if vs & set(c["name"].split("-")):
                    candidates.append(c["name"])

        org, org_how = None, ""
        if city in cfg["organizers"]:
            alias = cfg["organizers"][city]
            if alias in by_name and not by_name[alias]["is_archived"]:
                org, org_how = by_name[alias], "alias"
        if not org:
            for c in live:
                if any(c["name"] == v + s for v in vs for s in suffixes):
                    org, org_how = c, org_how or "exact"
                    break

        regional = None
        if not pub:
            name = cfg["regional"].get(city)
            if name and name in by_name and not by_name[name]["is_archived"]:
                regional = name

        out.append({
            "city": city, "regional": regional, "public_how": how,
            "public": pub["name"] if pub else None,
            "public_id": pub["id"] if pub else None,
            "public_members": pub["num_members"] if pub else None,
            "public_candidates": candidates,
            "organizers_channel": org["name"] if org else None,
            "organizers_id": org["id"] if org else None,
            "organizers_channel_members": org["num_members"] if org else None,
            "organizers_private": org["is_private"] if org else None,
        })
    return out


def build_audit(rows, people, slack_ids, membership, directory, staff_domain):
    """Join the sheet data to the Slack data, per chapter."""
    lookup = {fold_tight(r["city"]): r["city"] for r in rows}
    by_city, orphans = defaultdict(list), defaultdict(list)
    for person in people:
        key = fold_tight(person["city"])
        (by_city[lookup[key]] if key in lookup else orphans[person["city"]]).append(person)

    out = []
    for r in rows:
        pub_ids = set(membership.get(r["public"], [])) if r["public"] else set()
        org_ids = set(membership.get(r["organizers_channel"], [])) if r["organizers_channel"] else set()

        accepted = []
        for person in by_city.get(r["city"], []):
            uid = (slack_ids.get(person["email"]) or {}).get("id")
            accepted.append({**person, "slack_id": uid, "slack_account": bool(uid),
                             "in_public": bool(uid and uid in pub_ids),
                             "in_organizers": bool(uid and uid in org_ids)})

        known = {p["slack_id"] for p in accepted if p["slack_id"]}
        extras = []
        for uid in sorted(org_ids - known):
            u = directory.get(uid, {})
            extras.append({"id": uid, "name": u.get("real_name") or u.get("name") or uid,
                           "email": u.get("email", ""),
                           "is_staff": (u.get("email") or "").endswith("@" + staff_domain)})
        out.append({**r, "accepted": accepted, "unaccounted": extras})
    return out, dict(orphans)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def render(audit, orphans, dupes, today):
    allp = [p for c in audit for p in c["accepted"]]
    S = {
        "chapters": len(audit),
        "own": sum(1 for c in audit if c["public"]),
        "regional": sum(1 for c in audit if not c["public"] and c["regional"]),
        "none": sum(1 for c in audit if not c["public"] and not c["regional"]),
        "org": sum(1 for c in audit if c["organizers_channel"]),
        "people": len(allp),
        "has_slack": sum(1 for p in allp if p["slack_account"]),
        "in_public": sum(1 for p in allp if p["in_public"]),
        "in_org": sum(1 for p in allp if p["in_organizers"]),
    }
    S["no_slack"] = S["people"] - S["has_slack"]

    funnel_rows = [("Accepted as organizers", S["people"]),
                   ("Have a Slack account", S["has_slack"]),
                   ("In their city's channel", S["in_public"]),
                   ("In an organizers channel", S["in_org"])]
    top = funnel_rows[0][1] or 1
    funnel = []
    for i, (lbl, v) in enumerate(funnel_rows):
        pct = 100 * v / top
        tone = "accent" if i == 0 else ("warn" if pct > 40 else "bad")
        drop = "" if i == 0 else '<span class="drop">&minus;%d</span>' % (funnel_rows[i - 1][1] - v)
        funnel.append('<div class="frow"><span class="flab">%s</span><span class="ftrack">'
                      '<span class="ffill t-%s" style="width:%s%%"></span></span>'
                      '<span class="fval">%d<span class="fpct">%.0f%%</span></span>%s</div>'
                      % (lbl, tone, pct, v, pct, drop))

    matrix = []
    for c in audit:
        state = "own" if c["public"] else ("regional" if c["regional"] else "none")
        n = len(c["accepted"])
        inp = sum(1 for p in c["accepted"] if p["in_public"])
        ino = sum(1 for p in c["accepted"] if p["in_organizers"])
        nos = sum(1 for p in c["accepted"] if not p["slack_account"])
        shown = c["public"] or c["regional"]
        matrix.append(
            '<tr data-state="%s" data-org="%s"><th scope="row">%s</th>'
            '<td>%s%s%s</td><td>%s%s</td><td class="n">%s</td><td class="n">%s</td>'
            '<td class="n">%s</td><td class="n">%s</td></tr>'
            % (state, "yes" if c["organizers_channel"] else "no", e(c["city"]),
               ('<code class="chan">#%s</code>' % e(shown)) if shown else '<span class="nil">none</span>',
               '<span class="tag tag-reg">regional</span>' if state == "regional" else "",
               ('<span class="num">%s</span>' % c["public_members"]) if c["public"] else "",
               ('<code class="chan">#%s</code>' % e(c["organizers_channel"]))
               if c["organizers_channel"] else '<span class="nil">none</span>',
               '<span class="tag tag-pub">public!</span>'
               if c["organizers_channel"] and not c["organizers_private"] else "",
               n or '<span class="nil">0</span>',
               ("%d/%d" % (inp, n)) if n else '<span class="nil">—</span>',
               ("%d/%d" % (ino, n)) if n and c["organizers_channel"] else '<span class="nil">—</span>',
               ('<span class="bad">%d</span>' % nos) if nos else '<span class="nil">0</span>'))

    def plist(items, extra=""):
        return ('<ul class="plist %s">%s</ul>' % (extra, "".join(
            '<li><span class="pname">%s</span><span class="pmail">%s</span></li>'
            % (e(x["name"]), e(x["email"]) or "—") for x in items)))

    rosters = []
    for c in sorted([x for x in audit if x["organizers_channel"]],
                    key=lambda x: -(x["organizers_channel_members"] or 0)):
        present = [p for p in c["accepted"] if p["in_organizers"]]
        absent = [p for p in c["accepted"] if not p["in_organizers"]]
        staff = [x for x in c["unaccounted"] if x["is_staff"]]
        others = [x for x in c["unaccounted"] if not x["is_staff"]]
        groups = []
        for items, pill, label, cls in (
                (present, "ok", "accepted organizer", ""),
                (staff, "mute", "staff", "plist-x"),
                (others, "warn", "not an accepted organizer", "plist-x"),
                (absent, "bad", "accepted but absent", "plist-x")):
            if items:
                groups.append('<div class="rgrp"><h4><span class="pill pill-%s">%s</span>'
                              '<span class="cnt">%d</span></h4>%s</div>'
                              % (pill, label, len(items), plist(items, cls)))
        rosters.append(
            '<section class="chap roster"><h3>#%s%s<span class="chapchans">'
            '<span class="nil">%s &middot; %s members</span></span></h3>'
            '<div class="rgrps">%s</div></section>'
            % (e(c["organizers_channel"]),
               "" if c["organizers_private"] else '<span class="tag tag-pub">public!</span>',
               e(c["city"]), c["organizers_channel_members"], "".join(groups)))

    detail = []
    for c in audit:
        if not c["accepted"] and not c["organizers_channel"]:
            continue
        items = []
        if not c["accepted"]:
            items.append('<li class="none">No accepted organizers for this chapter</li>')
        for p in c["accepted"]:
            if not p["slack_account"]:
                pills = '<span class="pill pill-bad">no Slack account</span>'
            elif c["public"]:
                pills = ('<span class="pill pill-%s">%s #%s</span>'
                         % ("ok" if p["in_public"] else "warn",
                            "in" if p["in_public"] else "not in", e(c["public"])))
            else:
                pills = '<span class="pill pill-mute">no city channel</span>'
            if p["slack_account"] and c["organizers_channel"]:
                pills += ('<span class="pill pill-%s">%sorganizers</span>'
                          % ("ok" if p["in_organizers"] else "warn",
                             "" if p["in_organizers"] else "not in "))
            items.append('<li><span class="pname">%s</span><span class="pmail">%s</span>'
                         '<span class="pills">%s</span></li>'
                         % (e(p["name"]), e(p["email"]), pills))
        chips = " ".join(x for x in [
            ('<code class="chan">#%s</code>' % e(c["public"])) if c["public"] else "",
            ('<code class="chan">#%s</code>' % e(c["organizers_channel"]))
            if c["organizers_channel"] else ""] if x)
        detail.append('<section class="chap"><h3>%s<span class="chapchans">%s</span></h3>'
                      '<ul class="plist">%s</ul></section>'
                      % (e(c["city"]), chips or '<span class="nil">no channels</span>',
                         "".join(items)))

    create = [c for c in audit if not c["public"] and len(c["accepted"]) >= 2]
    unreachable = [c for c in audit if c["accepted"]
                   and not any(p["slack_account"] for p in c["accepted"])]
    empty_room = [c for c in audit if c["public"] and c["accepted"]
                  and not any(p["in_public"] for p in c["accepted"])]
    public_org = [c for c in audit if c["organizers_channel"] and not c["organizers_private"]]
    seats = sum(len(c["unaccounted"]) for c in audit)
    distinct = len({x["id"] for c in audit for x in c["unaccounted"]})
    absent_total = sum(1 for c in audit for p in c["accepted"]
                       if c["organizers_channel"] and not p["in_organizers"])

    todo = [
        ("Make Slack part of accepting an organizer",
         "%d of %d accepted organizers have no Slack account under their intake email, and only "
         "%d are in their own city channel. Acceptance already triggers a Drive grant and a CRM "
         "row — the Slack invite and the channel joins belong on the same trigger."
         % (S["no_slack"], S["people"], S["in_public"]),
         "Half a day", "Ops", "now")]
    if public_org:
        todo.append((
            "Close the %d public &ldquo;organizers&rdquo; channel%s"
            % (len(public_org), "" if len(public_org) == 1 else "s"),
            " and ".join("#%s (%s members)" % (c["organizers_channel"],
                                               c["organizers_channel_members"])
                         for c in public_org)
            + " are public. Venue costs, budgets and speaker problems are readable by the whole "
              "workspace.", "10 min", "Workspace admin", "now"))
    todo.append((
        "Reconcile the organizer rosters",
        "The %d organizer channels hold %d people we never accepted (%d seats). Meanwhile %d "
        "accepted organizers are missing from their own channel. Decide who belongs, fix both sides."
        % (S["org"], distinct, seats, absent_total), "2 hours", "Community leadership", "now"))
    if create:
        todo.append((
            "Give %d chapters their own room" % len(create),
            "Two or more accepted organizers and no channel of their own: "
            + ", ".join(c["city"] for c in create)
            + ". The other channel-less chapters keep pointing at their regional channel — a room "
              "for every one of them would just add channels nobody staffs.",
            "1 hour", "Workspace admin", "next"))
    if unreachable:
        todo.append((
            "Chase the %d unreachable chapters another way" % len(unreachable),
            "No organizer reachable on Slack at all: " + ", ".join(c["city"] for c in unreachable)
            + ". Email is the only route left, and it is the address that already failed.",
            "Half a day", "Ops", "next"))

    notes = []
    if orphans:
        notes.append("<li><strong>%d intake cities matched no chapter row</strong>: %s. Fix the "
                     "intake city or add the chapter row, then re-run.</li>"
                     % (len(orphans), ", ".join(e(c) for c in orphans)))
    if dupes:
        notes.append("<li><strong>%d duplicate intake rows</strong> for the same person and city "
                     "were dropped (first wins).</li>" % dupes)
    cand = [c for c in audit if not c["public"] and c["public_candidates"]]
    if cand:
        notes.append("<li><strong>%d chapters have near-miss channels</strong> that were NOT "
                     "auto-matched: %s. Confirm by hand and add to channel_map.json.</li>"
                     % (len(cand), "; ".join("%s → %s" % (e(c["city"]),
                                                          ", ".join("#" + n for n in c["public_candidates"][:3]))
                                             for c in cand[:8])))

    stamp = today.strftime("%-d %B %Y")
    body = f"""
<header>
  <div class="eyebrow">Organizer-side Slack audit &middot; {stamp}</div>
  <h1>The organizer side: coverage, rosters and the leak</h1>
  <p class="lede">For every chapter on the Chapters List: does it have a public city channel, does
  it have a private organizers channel, and are the organizers we accepted actually in them?
  Organizers are matched to Slack accounts by the email on their intake row.</p>
</header>

<div class="caveat"><strong>Read the organizers column carefully.</strong> A user token only sees
private channels its owner belongs to. A chapter shown without an organizers channel either has
none, or has one the audit account is not in — <code class="chan">users.conversations</code> does
not help, as it filters to the caller's own visibility. Public channels and their membership are
complete.</div>

<section>
  <div class="eyebrow">The numbers</div>
  <div class="stats">
    <div class="stat"><span class="v">{S['chapters']}</span><span class="k">Chapters</span></div>
    <div class="stat s-ok"><span class="v">{S['own']}</span><span class="k">Own city channel</span></div>
    <div class="stat s-warn"><span class="v">{S['regional']}</span><span class="k">Regional only</span></div>
    <div class="stat s-bad"><span class="v">{S['none']}</span><span class="k">No channel at all</span></div>
    <div class="stat s-bad"><span class="v">{S['org']}</span><span class="k">Organizers channel</span></div>
  </div>
</section>

<section>
  <div class="eyebrow">The leak</div>
  <h2>Accepting an organizer does not put them anywhere</h2>
  <p class="lede">Every one of these {S['people']} people was reviewed and accepted. The drop at
  each step is not attrition — nobody quit. No step connects an accepted organizer to the place
  their chapter lives.</p>
  <div class="funnel">{''.join(funnel)}</div>
  <div class="two" style="margin-top:24px">
    <div class="card"><h3>{len(unreachable)} chapters we cannot reach at all</h3>
      <p class="sub">No accepted organizer has a Slack account on their intake email</p>
      <div class="chips">{''.join('<span class="chip">%s · %d</span>' % (e(c["city"]), len(c["accepted"])) for c in unreachable)}</div>
      <p class="note">They may be in Slack under a different address — which is itself the finding,
      because nothing ties the intake identity to the Slack one.</p></div>
    <div class="card"><h3>{len(empty_room)} chapters have a room and nobody in it</h3>
      <p class="sub">Channel exists; zero accepted organizers are members</p>
      <div class="chips">{''.join('<span class="chip">#%s</span>' % e(c["public"]) for c in empty_room)}</div>
      <p class="note">The cheapest fixes here — the channel already exists and already has
      members.</p></div>
  </div>
</section>

<section>
  <div class="eyebrow">Coverage matrix</div>
  <h2>Every chapter, both channels</h2>
  <div class="controls" style="margin:16px 0 12px">
    <span class="lbl">Show</span>
    <button class="f" data-filter="all" aria-pressed="true">All {S['chapters']}</button>
    <button class="f" data-filter="own" aria-pressed="false">Own channel {S['own']}</button>
    <button class="f" data-filter="regional" aria-pressed="false">Regional only {S['regional']}</button>
    <button class="f" data-filter="none" aria-pressed="false">No channel {S['none']}</button>
    <button class="f" data-filter="noorg" aria-pressed="false">No organizers channel {S['chapters'] - S['org']}</button>
  </div>
  <div class="tablewrap"><table>
    <thead><tr><th>Chapter</th><th>Public channel</th><th>Organizers channel</th>
    <th class="n">Accepted</th><th class="n">In public</th><th class="n">In organizers</th>
    <th class="n">No Slack</th></tr></thead>
    <tbody>{''.join(matrix)}</tbody></table></div>
</section>

<section>
  <div class="eyebrow">Organizer channel rosters</div>
  <h2>Who is actually in each organizers channel</h2>
  <p class="lede">The full membership of all {S['org']} organizer channels, split by whether we ever
  accepted that person. {distinct} distinct people hold {seats} of these seats without having been
  accepted — some are staff who belong there, others are legacy leads nobody has reviewed.</p>
  <div class="grid-rosters" style="margin-top:20px">{''.join(rosters)}</div>
</section>

<section>
  <div class="eyebrow">What to do</div>
  <h2>Ranked by what it unblocks, not by effort</h2>
  {rs.actions(todo)}
</section>

<section>
  <div class="eyebrow">Person by person</div>
  <h2>Accepted organizers and where they actually are</h2>
  <div class="grid-chaps" style="margin-top:20px">{''.join(detail)}</div>
</section>

{('<section><div class="eyebrow">Data quality</div><h2>Fix these, then re-run</h2>'
  '<ul class="plain">' + ''.join(notes) + '</ul></section>') if notes else ''}

<footer>Sources: AAIF Community Chapters List (Chapters &amp; Teams), AAIF Community Intake Ops
(Organizers, status {' or '.join(ACCEPTED)}), and the Slack Web API. Both sheets are only ever
read. Channel matching uses the curated alias map in
<code class="chan">channel_map.json</code>.</footer>
"""
    js = """
const btns=[...document.querySelectorAll('button.f')];
btns.forEach(b=>b.addEventListener('click',()=>{
  btns.forEach(o=>o.setAttribute('aria-pressed',String(o===b)));
  const f=b.dataset.filter;
  document.querySelectorAll('tbody tr').forEach(r=>{
    r.hidden = f!=='all' && !(f==='noorg' ? r.dataset.org==='no' : r.dataset.state===f);
  });
}));
"""
    return rs.page("Slack Organizers Audit", body, script=js)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slack-organizers-audit")
    ap.add_argument("--cache", default=".slack-audit-cache")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--map", default=os.path.join(os.path.dirname(__file__), "channel_map.json"))
    args = ap.parse_args()

    with open(args.map) as fh:
        cfg = json.load(fh)
    os.makedirs(args.cache, exist_ok=True)
    api = Slack()
    who = api.ok("auth.test")
    print("workspace: %s (%s)" % (who.get("team"), who.get("team_id")))

    print("reading the sheets ...")
    chapters = read_chapters()
    people, dupes = read_intake()
    print("  %d chapters, %d accepted organizers (%d duplicate rows dropped)"
          % (len(chapters), len(people), dupes))

    chan_path = os.path.join(args.cache, "channels.json")
    if os.path.exists(chan_path) and not args.refresh:
        with open(chan_path) as fh:
            chans = json.load(fh)
        print("  reusing cached channel list (%d)" % len(chans))
    else:
        print("  fetching channels ...")
        chans = channels(api)
        with open(chan_path, "w") as fh:
            json.dump(chans, fh)

    rows = match_channels(chapters, chans, cfg)
    print("  matched: %d own channel, %d organizers channel"
          % (sum(1 for r in rows if r["public"]),
             sum(1 for r in rows if r["organizers_channel"])))

    ids_path = os.path.join(args.cache, "organizer_ids.json")
    if os.path.exists(ids_path) and not args.refresh:
        with open(ids_path) as fh:
            slack_ids = json.load(fh)
    else:
        print("  resolving %d organizer emails (about 1.5s each) ..."
              % len({p["email"] for p in people if p["email"]}))
        slack_ids = lookup_emails(api, [p["email"] for p in people])
        with open(ids_path, "w") as fh:
            json.dump(slack_ids, fh)
    print("  %d/%d organizers resolved to a Slack account"
          % (sum(1 for v in slack_ids.values() if v.get("id")), len(slack_ids)))

    targets = {}
    for r in rows:
        for name, cid in ((r["public"], r["public_id"]),
                          (r["organizers_channel"], r["organizers_id"])):
            if name:
                targets[name] = cid
    print("  pulling membership for %d channels ..." % len(targets))
    membership = {}
    for name, cid in sorted(targets.items()):
        membership[name] = members(api, cid)

    # Only the organizer-channel members need naming, so resolve those ids
    # individually rather than pulling a 30k-row directory.
    needed = {uid for r in rows if r["organizers_channel"]
              for uid in membership.get(r["organizers_channel"], [])}
    dir_path = os.path.join(args.cache, "org_members.json")
    directory = {}
    if os.path.exists(dir_path) and not args.refresh:
        with open(dir_path) as fh:
            directory = json.load(fh)
    missing = needed - set(directory)
    if missing:
        print("  naming %d organizer-channel members ..." % len(missing))
        for uid in sorted(missing):
            payload = api.call("users.info", user=uid)
            if payload.get("ok"):
                u, prof = payload["user"], (payload["user"].get("profile") or {})
                directory[uid] = {"real_name": u.get("real_name") or prof.get("real_name", ""),
                                  "name": u.get("name", ""),
                                  "email": (prof.get("email") or "").lower()}
        with open(dir_path, "w") as fh:
            json.dump(directory, fh)

    audit, orphans = build_audit(rows, people, slack_ids, membership, directory,
                                 cfg["staff_email_domain"])
    with open(os.path.join(args.cache, "audit.json"), "w") as fh:
        json.dump({"chapters": audit, "orphan_cities": orphans, "duplicates": dupes}, fh, indent=1)

    html_doc = render(audit, orphans, dupes, dt.datetime.now(dt.timezone.utc))
    html_path = args.out + ".html"
    with open(html_path, "w") as fh:
        fh.write(html_doc)
    print("wrote %s" % html_path)
    if not args.no_pdf:
        print("wrote %s" % rs.to_pdf(os.path.abspath(html_path),
                                     os.path.abspath(args.out + ".pdf")))


if __name__ == "__main__":
    main()
