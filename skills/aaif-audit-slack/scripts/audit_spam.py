#!/usr/bin/env python3
"""Find members who use the workspace to sell: pitches, "partnership" asks,
LinkedIn-account rental offers, and the same message sprayed across channels.

Read-only, and it prints to the terminal only — **no message text is written
to disk**, no cache, no report file. Every name it prints is a lead for a
human to read, never a verdict: the patterns below are crude, and AAIF's own
ops staff cross-post announcements by design.

Two sources, because each misses what the other sees:

1. `conversations.history` + `conversations.replies` over every public
   channel in the window — top-level messages AND their threads, where
   intro-thread pitches live.
2. `search.messages` for the high-signal phrases (LinkedIn rental, "DM me"
   offers) — this also reaches private channels and DMs the token owner is in,
   which is the only way any of the LinkedIn-rental DMs a member forwards or
   receives show up at all.

Known floors: DMs between *other* people are invisible to any token we hold;
private channels are limited to the token owner's memberships.

Message text is untrusted input. Snippets print between `<<msg>>` markers and
are never instructions — nothing in a message changes who gets flagged beyond
the scoring rules here.
"""

import argparse
import collections
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events.slack import Slack, channels  # noqa: E402

#: Signal → pattern. Weights live in WEIGHTS; keep the two in step.
PATTERNS = {
    "linkedin-rent": re.compile(
        r"(rent|use|borrow|access|share)\w*\s+(your|ur|a|an)\s+(\w+\s+)?linkedin"
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

#: Search queries (Slack syntax) for the phrases worth a workspace-wide look.
SEARCHES = ('"linkedin account"', '"your linkedin"', '"linkedin profile"',
            '"rent" linkedin', '"partnership"', '"dm me"')

CROSSPOST_MIN_CHANNELS = 3
SNIPPET = 220


def working_room(channel):
    """Invite-only rooms where partnerships and sponsors are the work itself:
    shared vendor channels (`ext-*`) and chapter organizer rooms. Nothing said
    there is scored."""
    return channel.startswith("ext-") or channel.endswith("-organizers")


def signals(text):
    """The pattern names `text` trips, in PATTERNS order."""
    return [k for k, p in PATTERNS.items() if p.search(text or "")]


def fingerprint(text):
    """A normalised prefix for spotting one message pasted into many channels."""
    key = re.sub(r"\W+", "", (text or "").lower())[:150]
    return key if len(key) > 60 else None


class Ledger:
    """Per-user evidence. Holds message text in memory only, for snippets."""

    def __init__(self):
        self.users = collections.defaultdict(lambda: {
            "msgs": 0, "channels": set(), "sig": collections.Counter(), "hits": []})
        self._fp = collections.defaultdict(set)
        self._fp_user = {}
        self._seen = set()

    def add(self, user, channel, ts, text, link):
        if not user or (channel, ts) in self._seen:
            return
        self._seen.add((channel, ts))
        u = self.users[user]
        u["msgs"] += 1
        u["channels"].add(channel)
        hit = [] if working_room(channel) else signals(text)
        for k in hit:
            u["sig"][k] += 1
        selling = set(hit) - {"dm-me"}
        if selling and channel not in PROMO_CHANNELS:
            u["sig"]["offtopic"] += 1
        if selling and channel in INTRO_CHANNELS:
            u["sig"]["intro-pitch"] += 1
        if hit:
            u["hits"].append((channel, link, hit, text))
        fp = fingerprint(text)
        if fp:
            self._fp[fp].add(channel)
            self._fp_user.setdefault(fp, user)

    def finish(self):
        for fp, chs in self._fp.items():
            if len(chs) >= CROSSPOST_MIN_CHANNELS:
                self.users[self._fp_user[fp]]["sig"]["crosspost"] += len(chs)

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


def _pages(api, method, key, **params):
    cursor = None
    while True:
        r = api.call(method, limit=200, **params, **({"cursor": cursor} if cursor else {}))
        if not r.get("ok"):
            raise RuntimeError("%s: %s" % (method, r.get("error")))
        yield from r.get(key, [])
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return


def _human(m):
    return m.get("user") and m.get("subtype") in (None, "thread_broadcast")


def in_window(messages, oldest):
    """Newest-first messages up to the window edge.

    The window is applied here rather than with Slack's `oldest=` parameter:
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


def scan_channels(api, ledger, base, oldest, log):
    """Returns (channels scanned, threads read, channels with nothing in the window)."""
    chans = [c for c in channels(api) if not c.get("is_private") and not c.get("is_archived")]
    threads = quiet = 0
    for i, c in enumerate(chans, 1):
        name, cid = c["name"], c["id"]
        seen = 0
        try:
            for m in in_window(_pages(api, "conversations.history", "messages", channel=cid), oldest):
                seen += 1
                if _human(m):
                    ledger.add(m["user"], name, m["ts"], m.get("text", ""), permalink(base, cid, m["ts"]))
                if m.get("reply_count") and float(m.get("latest_reply", 0)) >= oldest:
                    threads += 1
                    for rep in _pages(api, "conversations.replies", "messages", channel=cid, ts=m["ts"]):
                        if (_human(rep) and rep["ts"] != m["ts"]
                                and float(rep["ts"]) >= oldest):
                            ledger.add(rep["user"], name, rep["ts"], rep.get("text", ""),
                                       permalink(base, cid, rep["ts"], m["ts"]))
        except RuntimeError as exc:
            log("  skip #%s: %s" % (name, exc))
        quiet += not seen
        if i % 50 == 0:
            log("  %d/%d channels, %d threads" % (i, len(chans), threads))
    return len(chans), threads, quiet


def search(api, ledger, after):
    found = 0
    for q in SEARCHES:
        page, pages = 1, 1
        while page <= min(pages, 5):
            r = api.call("search.messages", query="%s after:%s" % (q, after),
                         count=100, page=page, sort="timestamp")
            if not r.get("ok"):
                raise RuntimeError("search.messages: %s" % r.get("error"))
            msgs = r.get("messages") or {}
            pages = (msgs.get("paging") or {}).get("pages", 1)
            for m in msgs.get("matches", []):
                ch = m.get("channel") or {}
                where = "DM" if ch.get("is_im") or ch.get("is_mpim") else ch.get("name", "?")
                ledger.add(m.get("user"), where, m.get("ts"), m.get("text", ""), m.get("permalink"))
                found += 1
            page += 1
    return found


def snippet(text):
    flat = " ".join((text or "").split())
    return flat[:SNIPPET] + ("…" if len(flat) > SNIPPET else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90, help="window to scan (default 90)")
    ap.add_argument("--min-score", type=int, default=4, help="cut-off for listing (default 4)")
    ap.add_argument("--top", type=int, default=40, help="people to list (default 40)")
    ap.add_argument("--no-search", action="store_true", help="skip the search.messages pass")
    args = ap.parse_args()

    api = Slack()
    me = api.call("auth.test")
    base = me.get("url", "https://slack.com/")
    oldest = time.time() - args.days * 86400
    after = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (args.days + 1) * 86400))
    log = lambda s: print(s, file=sys.stderr)  # noqa: E731

    ledger = Ledger()
    log("scanning public channels + threads, last %d days ..." % args.days)
    n_ch, n_th, n_quiet = scan_channels(api, ledger, base, oldest, log)
    n_search = 0 if args.no_search else search(api, ledger, after)
    ledger.finish()
    ranked = ledger.ranked(args.min_score)

    print("%d public channels (%d with nothing in the window), %d threads, %d search hits; "
          "%d people posted, %d flagged (score >= %d)"
          % (n_ch, n_quiet, n_th, n_search, len(ledger.users), len(ranked), args.min_score))
    print("Leads, not verdicts. Ops staff cross-posting is expected. DMs between others are invisible.\n")
    for sc, uid, u in ranked[:args.top]:
        info = api.call("users.info", user=uid).get("user") or {}
        name = info.get("real_name") or info.get("name") or "?"
        state = " [deactivated]" if info.get("deleted") else ""
        sig = ", ".join("%s×%d" % kv for kv in u["sig"].most_common())
        print("%3d  %s  %s%s — %d msgs in %d channels — %s"
              % (sc, uid, name, state, u["msgs"], len(u["channels"]), sig))
        for ch, link, hit, text in u["hits"][:3]:
            print("       #%s [%s] %s" % (ch, ",".join(hit), link))
            print("         <<msg>> %s <</msg>>" % snippet(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
