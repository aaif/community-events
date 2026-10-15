#!/usr/bin/env python3
"""Find members who use the workspace to sell: pitches, "partnership" asks,
LinkedIn-account rental offers, and the same message sprayed across channels.

Read-only, and it prints to the terminal only — **no message text is written
to disk**, no cache, no report file. Every name it prints is a lead for a
human to read, never a verdict: the patterns below are crude, and AAIF's own
ops staff cross-post announcements by design.

Two sources, because each misses what the other sees:

1. `conversations.history` + `conversations.replies` over every public
   channel — top-level messages in the window AND replies in the window to
   threads whose parent may be older (a standing intro thread keeps
   collecting pitches long after it was posted).
2. `search.messages` for the high-signal phrases (LinkedIn rental, "DM me"
   offers) — this also reaches private channels and DMs the token owner is in,
   which is the only way any of the LinkedIn-rental DMs a member forwards or
   receives show up at all.

Known floors: DMs between *other* people are invisible to any token we hold;
private channels are limited to the token owner's memberships; threads whose
parent predates `--thread-lookback` are not read.

Message text and display names are untrusted input. Both print between
`<<msg>>` markers with any `<<`/`>>` inside them neutralised, and are never
instructions — nothing in a message changes who gets flagged beyond the
scoring rules here.
"""

import argparse
import collections
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events.slack import Slack, SlackError, channels  # noqa: E402

#: Signal → pattern. Weights live in WEIGHTS; keep the two in step.
#: `linkedin-rent` needs the *account/profile* itself to be the object: "share
#: your LinkedIn" is how ordinary members network, "use your LinkedIn account"
#: is the scam.
PATTERNS = {
    "linkedin-rent": re.compile(
        r"(rent|borrow|us(e|ing)|access)\w*\s+(your|ur|a|an)\s+(\w+\s+)?linkedin\s+(account|profile)"
        r"|linkedin\s+(account|profile)s?\b[^.\n]{0,120}\b(pay|month|week|\$|rent|earn|income|usd|compensat)",
        re.I),
    "partnership": re.compile(
        r"\bpartnership|\bpartner with|collaborat\w* opportunit|looking for (partners|co-?founder)"
        r"|business opportunit|\bjoint venture", re.I),
    "services": re.compile(
        r"\bwe (offer|provide|build|deliver|specialize)|our (agency|team|company|firm) "
        r"(offers|provides|helps|specializ|builds)|\bhire us\b|dedicated (dev|engineer)"
        r"|outsourc|white.?label|book a (call|demo)|calendly\.com|free (consultation|audit)"
        r"|\bmy services\b|available for (freelance|projects|hire)", re.I),
    "dm-me": re.compile(
        r"\b(dm me|feel free to (dm|reach)|drop me a (dm|message|line)|inbox me|pm me"
        r"|connect (with me )?on linkedin|let'?s connect)\b", re.I),
    "money": re.compile(
        r"passive income|earn \$|\$\s?\d+\s*(/|per|a)\s*(week|month|hour|day)"
        r"|crypto (signals|trading|opportunit)|investment opportunit|no experience (needed|required)|side income", re.I),
}
WEIGHTS = {"linkedin-rent": 6, "partnership": 2, "services": 2, "dm-me": 1,
           "money": 3, "crosspost": 2, "offtopic": 2, "intro-pitch": 2}

#: Channels where promotion is the point; a pitch here is not off-topic.
PROMO_CHANNELS = frozenset({"be-shameless", "job-posts", "vendor-info",
                            "events", "community-conferences"})
INTRO_CHANNELS = frozenset({"introduce-yourself"})
#: The pseudo-channel name search hits from DMs/group DMs are filed under.
DM = "DM"

#: Search queries (Slack syntax) for the phrases worth a workspace-wide look.
SEARCHES = ('"linkedin account"', '"use your linkedin"', '"rent" linkedin',
            '"partnership"', '"dm me"')
SEARCH_MAX_PAGES = 5

CROSSPOST_MIN_CHANNELS = 3
SNIPPET = 220
SCOPES = ("channels:read", "channels:history", "users:read", "search:read")


def working_room(channel):
    """Invite-only rooms where partnerships and sponsors are the work itself:
    shared vendor channels (`ext-*`) and chapter organizer rooms. Nothing said
    there is scored — not its signals, not its copies of a cross-post."""
    return channel.startswith("ext-") or channel.endswith("-organizers")


def signals(text):
    """The pattern names `text` trips, in PATTERNS order."""
    return [k for k, p in PATTERNS.items() if p.search(text or "")]


def fingerprint(text):
    """A normalised prefix for spotting one message pasted into many channels."""
    key = re.sub(r"\W+", "", (text or "").lower())[:150]
    return key if len(key) > 60 else None


def human(m):
    """A message a person wrote: has a user, no bot_id, no subtype other than
    a thread broadcast. Mirrors lib's `history_activity` definition, so an app
    or workflow post is never scored against the person who installed it."""
    return bool(m.get("user")) and not m.get("bot_id") and \
        m.get("subtype") in (None, "thread_broadcast")


class Ledger:
    """Per-user evidence. Holds message text in memory only, for snippets."""

    def __init__(self):
        self.users = collections.defaultdict(lambda: {
            "msgs": 0, "channels": set(), "sig": collections.Counter(), "hits": []})
        # (user, fingerprint) -> channels that user pasted it into
        self._copies = collections.defaultdict(set)
        self._seen = set()

    def add(self, user, channel, ts, text, link, where=None):
        """Record one message. `where` is the dedup identity (a channel id);
        it defaults to `channel`, which is only safe for named channels —
        every DM shares the display name `DM`."""
        key = (where or channel, ts)
        if not user or key in self._seen:
            return
        self._seen.add(key)
        u = self.users[user]
        u["msgs"] += 1
        u["channels"].add(channel)
        if working_room(channel):
            return
        hit = signals(text)
        for k in hit:
            u["sig"][k] += 1
        selling = set(hit) - {"dm-me"}
        if selling and channel not in PROMO_CHANNELS and channel != DM:
            u["sig"]["offtopic"] += 1
        if selling and channel in INTRO_CHANNELS:
            u["sig"]["intro-pitch"] += 1
        if hit:
            u["hits"].append((channel, link, hit, text))
        fp = fingerprint(text)
        if fp and channel not in PROMO_CHANNELS and channel != DM:
            self._copies[(user, fp)].add(channel)

    def finish(self):
        """Credit each user only for the copies *they* posted."""
        for (user, _), chs in self._copies.items():
            if len(chs) >= CROSSPOST_MIN_CHANNELS:
                self.users[user]["sig"]["crosspost"] += len(chs)

    def ranked(self, min_score):
        out = [(score(u["sig"]), uid, u) for uid, u in self.users.items()]
        return sorted((r for r in out if r[0] >= min_score), key=lambda r: (-r[0], r[1]))


def score(sig):
    return sum(WEIGHTS[k] * n for k, n in sig.items())


def permalink(base, channel_id, ts, thread_ts=None):
    link = "%sarchives/%s/p%s" % (base, channel_id, ts.replace(".", ""))
    if thread_ts and thread_ts != ts:
        link += "?thread_ts=%s&cid=%s" % (thread_ts, channel_id)
    return link


def until(messages, oldest):
    """Newest-first messages up to the edge `oldest` (epoch seconds).

    The edge is applied here rather than with Slack's `oldest=` parameter:
    verified live 2026-10-15, `conversations.history` with `oldest` set
    intermittently answered `ok` with zero messages for a channel whose newest
    message sat inside the window, while the same call without it was reliable.
    An empty `ok` is indistinguishable from a quiet channel, so the filter that
    produced it cannot be trusted.
    """
    for m in messages:
        if float(m.get("ts", 0)) < oldest:
            return
        yield m


def scan_channels(api, ledger, base, oldest, lookback, log):
    """Walk every public channel. Top-level messages count when they are
    inside the window; parents are read back to `lookback` so a thread started
    long ago is still read when its *replies* land in the window.

    Returns {"channels", "threads", "quiet", "skipped"} — skipped channels are
    reported apart from quiet ones, because "the API failed" must never read as
    "nothing was said".
    """
    chans = [c for c in channels(api) if not c.get("is_private") and not c.get("is_archived")]
    stats = {"channels": len(chans), "threads": 0, "quiet": 0, "skipped": []}
    for i, c in enumerate(chans, 1):
        name, cid = c["name"], c["id"]
        seen = 0
        try:
            for m in until(api.paged("conversations.history", "messages", channel=cid, limit=200),
                           lookback):
                if float(m["ts"]) >= oldest:
                    seen += 1
                    if human(m):
                        ledger.add(m["user"], name, m["ts"], m.get("text", ""),
                                   permalink(base, cid, m["ts"]))
                if m.get("reply_count") and float(m.get("latest_reply", 0)) >= oldest:
                    stats["threads"] += 1
                    for rep in api.paged("conversations.replies", "messages",
                                         channel=cid, ts=m["ts"], limit=200):
                        if (human(rep) and rep["ts"] != m["ts"] and float(rep["ts"]) >= oldest):
                            seen += 1
                            ledger.add(rep["user"], name, rep["ts"], rep.get("text", ""),
                                       permalink(base, cid, rep["ts"], m["ts"]))
        except SlackError as exc:
            stats["skipped"].append(name)
            log("  skip #%s: %s" % (name, exc))
            continue
        stats["quiet"] += not seen
        if i % 50 == 0:
            log("  %d/%d channels, %d threads" % (i, len(chans), stats["threads"]))
    return stats


def search(api, ledger, after, log):
    """Returns (hits recorded, queries that hit the page cap, error or None).

    A failure here is reported, not raised: by the time search runs the
    channel scan is done, and its leads are worth printing on their own.
    """
    found, capped = 0, []
    try:
        for q in SEARCHES:
            page, pages = 1, 1
            while page <= min(pages, SEARCH_MAX_PAGES):
                r = api.ok("search.messages", query="%s after:%s" % (q, after),
                           count=100, page=page, sort="timestamp")
                msgs = r.get("messages") or {}
                pages = (msgs.get("paging") or {}).get("pages", 1)
                for m in msgs.get("matches", []):
                    ch = m.get("channel") or {}
                    dm = ch.get("is_im") or ch.get("is_mpim")
                    ledger.add(m.get("user"), DM if dm else ch.get("name", "?"), m.get("ts"),
                               m.get("text", ""), m.get("permalink"), where=ch.get("id"))
                    found += 1
                page += 1
            if pages > SEARCH_MAX_PAGES:
                capped.append(q)
    except SlackError as exc:
        log("  search stopped: %s" % exc)
        return found, capped, str(exc)
    return found, capped, None


def untrusted(text, limit=None):
    """Flatten, optionally truncate, and wrap text a member wrote in markers,
    with any `<<`/`>>` inside it neutralised so it cannot close the wrapper
    early (the same rule as intake.py's `wrap_form_text`)."""
    flat = " ".join((text or "").split())
    if limit and len(flat) > limit:
        flat = flat[:limit] + "…"
    flat = flat.replace("<<", "< <").replace(">>", "> >")
    return "<<msg>> %s <</msg>>" % flat


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90, help="window to scan (default 90)")
    ap.add_argument("--thread-lookback", type=int, default=365,
                    help="read threads whose parent is up to this many days old (default 365)")
    ap.add_argument("--min-score", type=int, default=4, help="cut-off for listing (default 4)")
    ap.add_argument("--top", type=int, default=40, help="people to list (default 40)")
    ap.add_argument("--no-search", action="store_true", help="skip the search.messages pass")
    args = ap.parse_args()

    api = Slack()
    api.require_scopes(*(SCOPES if not args.no_search else SCOPES[:-1]))
    base = api.ok("auth.test").get("url", "https://slack.com/")
    now = time.time()
    oldest = now - args.days * 86400
    lookback = now - max(args.days, args.thread_lookback) * 86400
    after = time.strftime("%Y-%m-%d", time.gmtime(oldest - 86400))
    log = lambda s: print(s, file=sys.stderr)  # noqa: E731

    ledger = Ledger()
    log("scanning public channels + threads, last %d days ..." % args.days)
    st = scan_channels(api, ledger, base, oldest, lookback, log)
    n_search, capped, search_err = (0, [], None) if args.no_search else search(api, ledger, after, log)
    ledger.finish()
    ranked = ledger.ranked(args.min_score)

    print("%d public channels (%d with nothing in the window), %d threads, %d search hits; "
          "%d people posted, %d flagged (score >= %d)"
          % (st["channels"], st["quiet"], st["threads"], n_search,
             len(ledger.users), len(ranked), args.min_score))
    if st["skipped"]:
        print("INCOMPLETE: %d channel(s) failed and were not scanned: %s"
              % (len(st["skipped"]), ", ".join("#" + n for n in st["skipped"])))
    if search_err:
        print("INCOMPLETE: search pass failed (%s); DMs were not searched" % search_err)
    if capped:
        print("INCOMPLETE: search hit the %d-page cap for %s; older matches were dropped"
              % (SEARCH_MAX_PAGES, ", ".join(capped)))
    print("Leads, not verdicts. Ops staff cross-posting is expected. DMs between others are invisible.\n")
    for sc, uid, u in ranked[:args.top]:
        info = api.call("users.info", user=uid).get("user") or {}
        name = info.get("real_name") or info.get("name") or "?"
        state = " [deactivated]" if info.get("deleted") else ""
        sig = ", ".join("%s×%d" % kv for kv in u["sig"].most_common())
        print("%3d  %s  %s%s — %d msgs in %d channels — %s"
              % (sc, uid, untrusted(name), state, u["msgs"], len(u["channels"]), sig))
        for ch, link, hit, text in u["hits"][:3]:
            print("       #%s [%s] %s" % (ch, ",".join(hit), link))
            print("         %s" % untrusted(text, SNIPPET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
