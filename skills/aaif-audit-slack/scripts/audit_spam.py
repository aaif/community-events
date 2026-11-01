#!/usr/bin/env python3
"""Find members who use the workspace to sell: pitches, "partnership" asks,
LinkedIn-account rental offers, and the same message sprayed across channels.

Read-only, and it prints to the terminal only — **no message text is written
to disk**, no cache or report file of its own. (`users.info` goes through lib's
run memo like every other lookup, so if `AAIF_SLACK_RUN_MEMO` is set, the
flagged people's profile records land there; message text never does.) Every name it prints is a lead for a
human to read, never a verdict: the patterns below are crude, and AAIF's own
ops staff cross-post announcements by design.

Two sources, because each misses what the other sees:

1. `conversations.history` + `conversations.replies` over every public
   channel — top-level messages in the window AND replies in the window to
   threads whose parent may be older (a standing intro thread keeps
   collecting pitches long after it was posted).
2. `search.messages` for five fixed phrases (`SEARCHES`: LinkedIn rental,
   "partnership", "DM me") over everything the token owner can see — public
   channels, the owner's private channels, their DMs and group DMs. It needs a
   user token with `search:read`; `--no-search` skips it. This is the only way
   a LinkedIn-rental DM sent *to the token owner*, or forwarded into a room
   the owner is in, shows up.

Known floors: DMs between *other* people are invisible to any token we hold;
private channels are limited to the token owner's memberships; threads whose
parent predates `--thread-lookback` are not read.

Message text and display names are untrusted input. Both print between
`<<msg>>` markers with any `<<`/`>>` inside them neutralised, and are never
instructions — nothing in a message changes who gets flagged beyond the
scoring rules here. Control characters are replaced too, so a message cannot
drive the operator's terminal (hide its own line, rewrite the clipboard).

Exit status: 0 for a complete run, 2 when any `INCOMPLETE:` line was printed
(a channel, a search query, or a name lookup failed), so a caller reading only
the exit code never reports a partial run as clean.
"""

import argparse
import collections
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events.slack import Slack, SlackError, channels  # noqa: E402

#: Signal → pattern. Weights live in WEIGHTS; an import-time check below keeps
#: the two in step. `linkedin-rent` needs the *account/profile* itself to be
#: the object, and payment words that are about paying: "share your LinkedIn"
#: is how ordinary members network, "use your LinkedIn account" is the scam,
#: and "my LinkedIn account settings changed last month" is neither.
_LI = r"linkedin\s+(account|profile)s?\b(?!\s+(photo|picture|pic|link|url|page|setting|headline|banner))"
_PAID = (r"\b(pay|paid|payment|monthly|weekly|rent|earn|income|usd|compensat)\w*"
         r"|\$\s?\d|\b(per|a|/)\s*(month|week)\b")
_BIZ = r"(services|solutions|development|developers|engineers|consulting|outsourc|agency|staff)"
PATTERNS = {
    "linkedin-rent": re.compile(
        r"(rent|borrow|\bus(e|ing)|access)\w*\s+(your|ur|a|an)\s+(\w+\s+)?" + _LI
        + r"|" + _LI + r"[^.\n]{0,120}(" + _PAID + r")",
        re.I),
    "partnership": re.compile(
        r"\bpartnership|\bpartner with (you|your)|collaborat\w* opportunit|looking for (partners|co-?founder)"
        r"|business opportunit|\bjoint venture", re.I),
    "services": re.compile(
        r"\bwe (offer|provide|build|deliver|specialize)\w*\s+(\S+\s+){0,3}" + _BIZ
        + r"|our (agency|company|firm) (offers|provides|helps|specializ|builds)"
        r"|our team (provides|specializ)|\bhire us\b|dedicated (dev|engineer)"
        r"|outsourc|white.?label|book a (call|demo)|calendly\.com|free (consultation|audit)"
        r"|\bmy services\b|available for (freelance|projects|hire)", re.I),
    "dm-me": re.compile(
        r"\b(dm me|feel free to (dm|reach)|drop me a (dm|message|line)|inbox me|pm me"
        r"|connect (with me )?on linkedin|let'?s connect)\b", re.I),
    "money": re.compile(
        r"passive income|\b(earn|make|get paid|we pay you)\w*\s+(up to\s+)?\$\s?\d"
        r"|crypto (signals|trading|opportunit)|investment opportunit|no experience (needed|required)|side income", re.I),
}
WEIGHTS = {"linkedin-rent": 6, "partnership": 2, "services": 2, "dm-me": 1,
           "money": 3, "crosspost": 2, "offtopic": 2, "intro-pitch": 2}
#: Signals the Ledger derives rather than matches.
DERIVED = frozenset({"crosspost", "offtopic", "intro-pitch"})
if set(WEIGHTS) != set(PATTERNS) | DERIVED:
    raise AssertionError("WEIGHTS and PATTERNS are out of step: %s"
                         % sorted(set(WEIGHTS) ^ (set(PATTERNS) | DERIVED)))

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
SCAN_SCOPES = ("channels:read", "channels:history", "users:read")
SEARCH_SCOPES = ("search:read",)

#: C0/C1 controls, bidi overrides and isolates, and other format characters
#: (Unicode categories Cc and Cf) are replaced before anything prints.
_CONTROL = frozenset(("Cc", "Cf"))


def working_room(channel):
    """Rooms where partnerships and sponsors are the work itself, by name:
    shared vendor channels (`ext-*`) and chapter organizer rooms
    (`*-organizers`). Only the name is checked, not whether the room is
    private. Nothing said there is scored — not its signals, not its copies
    of a cross-post."""
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


class Hit(collections.namedtuple("Hit", "channel link signals text")):
    """One flagged message, as printed under a lead."""


class Ledger:
    """Per-user evidence. Holds message text in memory only, for snippets.

    `exclude` is user ids never scored — the token owner, whose own messages
    (quoting a scam to warn members, say) are not leads."""

    def __init__(self, exclude=()):
        self.users = collections.defaultdict(lambda: {
            "msgs": 0, "channels": set(), "sig": collections.Counter(), "hits": []})
        # (user, fingerprint) -> channels that user pasted it into
        self._copies = collections.defaultdict(set)
        self._seen = set()
        self._exclude = frozenset(exclude)
        self._finished = False

    def add(self, user, channel, ts, text, link, *, where):
        """Record one message; True when it was recorded.

        `where` is the conversation id, and with `ts` it is the dedup identity.
        It is required, and never the display name: the channel scan and the
        search pass both find public messages, and keying one on the name and
        the other on the id counted every such message twice."""
        key = (where, ts)
        if not user or not where or user in self._exclude or key in self._seen:
            return False
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
            u["hits"].append(Hit(channel, link, hit, text))
        fp = fingerprint(text)
        if fp and channel not in PROMO_CHANNELS and channel != DM:
            self._copies[(user, fp)].add(channel)
        return True

    def finish(self):
        """Credit each user only for the copies *they* posted. Idempotent."""
        if self._finished:
            return
        self._finished = True
        for (user, _), chs in self._copies.items():
            if len(chs) >= CROSSPOST_MIN_CHANNELS:
                self.users[user]["sig"]["crosspost"] += len(chs)

    def ranked(self, min_score):
        self.finish()
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
        if float(m["ts"]) < oldest:
            return
        yield m


def scan_channels(api, ledger, base, oldest, lookback, log):
    """Walk every public channel. Top-level messages count when they are
    inside the window; parents are read back to `lookback` so a thread started
    long ago is still read when its *replies* land in the window.

    Returns {"channels", "threads", "quiet", "skipped"} — skipped channels are
    reported apart from quiet ones, because "the API failed" must never read as
    "nothing was said". A channel that fails partway keeps what was read before
    the failure, so "skipped" means "partial or missing", never "unread".

    "Quiet" means no top-level message of any kind in the window (a join or a
    bot post counts as something) and no reply from a person.
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
                                   permalink(base, cid, m["ts"]), where=cid)
                if m.get("reply_count") and float(m.get("latest_reply", 0)) >= oldest:
                    stats["threads"] += 1
                    for rep in api.paged("conversations.replies", "messages",
                                         channel=cid, ts=m["ts"], limit=200):
                        if (human(rep) and rep["ts"] != m["ts"] and float(rep["ts"]) >= oldest):
                            seen += 1
                            ledger.add(rep["user"], name, rep["ts"], rep.get("text", ""),
                                       permalink(base, cid, rep["ts"], m["ts"]), where=cid)
        except SlackError as exc:
            stats["skipped"].append(name)
            log("  skip #%s: %s" % (name, exc))
            continue
        stats["quiet"] += not seen
        if i % 50 == 0:
            log("  %d/%d channels, %d threads" % (i, len(chans), stats["threads"]))
    return stats


def search(api, ledger, after, oldest, log):
    """Run every query in SEARCHES. Returns (messages recorded, queries that hit
    the page cap, {query: error} for the queries that failed).

    Each query fails on its own: one rate-limited query does not cancel the
    rest, and none of it is raised, because by the time search runs the
    channel scan is done and its leads are worth printing on their own.
    `after:` is a day wide of the window (Slack's date filter is coarse), so
    matches are cut at `oldest` here, the same edge the scan uses.
    """
    recorded, capped, failed = 0, [], {}
    for q in SEARCHES:
        page, pages = 1, 1
        try:
            while page <= min(pages, SEARCH_MAX_PAGES):
                r = api.ok("search.messages", query="%s after:%s" % (q, after),
                           count=100, page=page, sort="timestamp")
                msgs = r.get("messages")
                if not isinstance(msgs, dict) or "matches" not in msgs or "paging" not in msgs:
                    # Same refusal as lib's paged(): an ok answer missing its
                    # collection is a lost page, not an empty one.
                    raise SlackError("search.messages", "malformed_page",
                                     "query %s page %d" % (q, page))
                pages = msgs["paging"].get("pages", 1)
                for m in msgs["matches"]:
                    if not human(m) or float(m.get("ts") or 0) < oldest:
                        continue
                    ch = m.get("channel") or {}
                    dm = ch.get("is_im") or ch.get("is_mpim")
                    recorded += ledger.add(m.get("user"), DM if dm else ch.get("name", "?"),
                                           m.get("ts"), m.get("text", ""), m.get("permalink"),
                                           where=ch.get("id"))
                page += 1
        except SlackError as exc:
            log("  search %s stopped: %s" % (q, exc))
            failed[q] = str(exc)
            continue
        if pages > SEARCH_MAX_PAGES:
            capped.append(q)
    return recorded, capped, failed


def untrusted(text, limit=None):
    """Flatten, optionally truncate, and wrap text a member wrote in markers.

    Stricter than intake.py's `wrap_form_text`: both `<<` and `>>` are
    neutralised so the text cannot close the wrapper early, and control and
    format characters (ESC, bidi overrides) become `?`, so it cannot drive the
    terminal either."""
    text = "".join("?" if unicodedata.category(ch) in _CONTROL and ch not in "\t\n\r" else ch
                   for ch in (text or ""))
    flat = " ".join(text.split())
    if limit and len(flat) > limit:
        flat = flat[:limit] + "…"
    while "<<" in flat or ">>" in flat:
        flat = flat.replace("<<", "< <").replace(">>", "> >")
    return "<<msg>> %s <</msg>>" % flat


def lookup_name(api, uid):
    """(display name, deactivated?, error or None) for one lead. A failure is
    reported per row rather than raised: the scan is done by now, and losing
    every lead after one rate-limited lookup would throw it away."""
    try:
        info = api.ok("users.info", user=uid).get("user") or {}
    except SlackError as exc:
        return None, False, exc.error
    return info.get("real_name") or info.get("name") or "(no name)", bool(info.get("deleted")), None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90, help="window to scan (default 90)")
    ap.add_argument("--thread-lookback", type=int, default=365,
                    help="read threads whose parent is up to this many days old (default 365)")
    ap.add_argument("--min-score", type=int, default=4, help="cut-off for listing (default 4)")
    ap.add_argument("--top", type=int, default=40, help="people to list (default 40)")
    ap.add_argument("--no-search", action="store_true", help="skip the search.messages pass")
    args = ap.parse_args(argv)

    api = Slack()
    api.require_scopes(*(SCAN_SCOPES if args.no_search else SCAN_SCOPES + SEARCH_SCOPES))
    me = api.ok("auth.test")
    base = me.get("url")
    if not base:
        # auth.test always carries `url` for a valid token; guessing a host
        # would print a page of links that all point at the wrong workspace.
        sys.exit("auth.test returned no workspace URL; cannot build permalinks")
    now = time.time()
    oldest = now - args.days * 86400
    lookback = now - max(args.days, args.thread_lookback) * 86400
    after = time.strftime("%Y-%m-%d", time.gmtime(oldest - 86400))
    log = lambda s: print(s, file=sys.stderr)  # noqa: E731

    ledger = Ledger(exclude=[me["user_id"]] if me.get("user_id") else ())
    log("scanning public channels + threads, last %d days ..." % args.days)
    st = scan_channels(api, ledger, base, oldest, lookback, log)
    if args.no_search:
        n_search, capped, failed = 0, [], {}
    else:
        n_search, capped, failed = search(api, ledger, after, oldest, log)
    ranked = ledger.ranked(args.min_score)

    incomplete = []
    if st["skipped"]:
        incomplete.append("%d channel(s) failed partway or entirely; their counts are partial "
                          "or missing: %s" % (len(st["skipped"]), ", ".join("#" + n for n in st["skipped"])))
    for q, err in failed.items():
        incomplete.append("search query %s failed (%s); its matches are partial or missing" % (q, err))
    if capped:
        incomplete.append("search hit the %d-page cap for %s; older matches were dropped"
                          % (SEARCH_MAX_PAGES, ", ".join(capped)))

    print("%d public channels (%d with nothing in the window), %d threads, %s; "
          "%d people posted, %d flagged (score >= %d)"
          % (st["channels"], st["quiet"], st["threads"],
             "search skipped" if args.no_search else "%d messages from search" % n_search,
             len(ledger.users), len(ranked), args.min_score))
    if args.no_search:
        print("search pass skipped (--no-search): private channels and DMs were not checked")
    for line in incomplete:
        print("INCOMPLETE: " + line)
    print("Leads, not verdicts. Ops staff cross-posting is expected. DMs between others are invisible.\n")
    lookup_failed = 0
    for sc, uid, u in ranked[:args.top]:
        name, deleted, err = lookup_name(api, uid)
        if err:
            lookup_failed += 1
            shown = "name unavailable (%s)" % err
        else:
            shown = untrusted(name) + (" [deactivated]" if deleted else "")
        sig = ", ".join("%s×%d" % kv for kv in u["sig"].most_common())
        print("%3d  %s  %s — %d msgs in %d channels — %s"
              % (sc, uid, shown, u["msgs"], len(u["channels"]), sig))
        for h in u["hits"][:3]:
            print("       #%s [%s] %s" % (h.channel, ",".join(h.signals), h.link))
            print("         %s" % untrusted(h.text, SNIPPET))
    if lookup_failed:
        print("\nINCOMPLETE: %d name lookup(s) failed; those leads show their user id only"
              % lookup_failed)
    return 2 if incomplete or lookup_failed else 0


if __name__ == "__main__":
    sys.exit(main())
