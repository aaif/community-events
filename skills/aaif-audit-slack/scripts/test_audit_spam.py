#!/usr/bin/env python3
"""Self-tests for the spam audit. No network: Slack is a fake. Synthetic text only."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_spam as sp  # noqa: E402
from aaif_events.slack import SlackError  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s:\n     got:  %r\n     want: %r" % (label, got, want))
        print("FAIL %s" % label)
    else:
        print("ok   %s" % label)


# --- patterns -------------------------------------------------------------
check("rental offer", sp.signals("I'd like to rent your LinkedIn account, $300 per month"),
      ["linkedin-rent"])
check("earning pitch is money", sp.signals("Earn $500 per week from home"), ["money"])
check("rental, 'using a LinkedIn account'", "linkedin-rent" in sp.signals(
      "I'm interested in using a LinkedIn account for my work"), True)
check("rental, paid profile", "linkedin-rent" in sp.signals(
      "Looking for people with a LinkedIn profile, we pay monthly"), True)
check("'share your LinkedIn' is networking", sp.signals("feel free to share your LinkedIn below"), [])
check("plain linkedin mention is clean", sp.signals("Here's my LinkedIn if you want to chat"), [])
check("agency pitch", sp.signals("Our agency provides dedicated developers. Book a call!"),
      ["services"])
check("partnership + dm", sp.signals("Open to a partnership? DM me"), ["partnership", "dm-me"])
# Ordinary organiser and engineering sentences that once tripped a pattern.
for benign in ("Happy to share: linkedin account settings changed last month",
               "I use your linkedin profile photo idea for our speaker cards",
               "We partner with local universities for venues",
               "Our team offers free pizza at events",
               "We provide food and drinks, doors at 6",
               "$20 per month for GPU is a steal",
               "We build agents with LangGraph, here's the repo"):
    check("benign: %s" % benign, sp.signals(benign), [])
benign_led = sp.Ledger()
benign_led.add("U1", "general", "1.0", "Happy to share: linkedin account settings changed last month",
               "l", where="C1")
check("one benign post does not make a lead", benign_led.ranked(4), [])
check("ordinary question is clean", sp.signals("How do you serve vLLM behind a load balancer?"), [])
check("crypto chatter is clean", sp.signals("most agent projects are as bad as crypto"), [])
check("short text has no fingerprint", sp.fingerprint("hi all"), None)
check("fingerprint ignores case and punctuation",
      sp.fingerprint("Join our AI event this Thursday in Boston, RSVP on the page, free pizza!!"),
      sp.fingerprint("join our ai event this thursday in boston rsvp on the page free pizza"))

# --- who counts as a person ----------------------------------------------
check("person", sp.human({"user": "U1"}), True)
check("thread broadcast is a person", sp.human({"user": "U1", "subtype": "thread_broadcast"}), True)
check("app post with a user is not", sp.human({"user": "U1", "bot_id": "B1"}), False)
check("join is not", sp.human({"user": "U1", "subtype": "channel_join"}), False)

# --- scoring --------------------------------------------------------------
L = sp.Ledger()
pitch = "We offer dedicated developers for your AI product, book a call with our team today"
for ch in ("general", "agents", "boston", "be-shameless"):
    L.add("U1", ch, "1.0", pitch, "link", where="C-" + ch)
L.add("U1", "general", "1.0", pitch, "dupe", where="C-general")  # seen twice, counts once
L.add("U2", "be-shameless", "2.0", "We offer AI development services, check it out", "link",
      where="C-be-shameless")
L.add("U3", "introduce-yourself", "3.0", "Hi, I'm Ada from Boston. Our team provides AI services", "l",
      where="C-intro")
L.add("U4", "general", "4.0", "Anyone going to the Boston event?", "link", where="C-general")
L.add("U5", "ext-acme-partner", "5.0", "Happy to explore a partnership on the launch", "l", where="C-ext")
L.add("U5", "boston-organizers", "5.1", "Our sponsor offers a partnership tier", "l", where="C-bo")
announce = "Agentic AI night in Boston on Thursday with talks, food and networking, RSVP on Luma"
for i, (user, ch) in enumerate((("U6", "boston"), ("U7", "nyc"), ("U8", "austin"))):
    L.add(user, ch, "6.%d" % i, announce, "l", where="C-" + ch)  # three people, one template
for i, ch in enumerate(("boston", "boston-organizers", "events")):
    L.add("U9", ch, "9.%d" % i, announce + " tonight", "l", where="C-" + ch)  # sanctioned rooms
L.add("U10", sp.DM, "10.0", "Open to a partnership?", "l", where="D1")
L.add("U11", sp.DM, "10.0", "Open to a partnership?", "l", where="D2")  # same ts, other DM
L.finish()
L.finish()  # idempotent: a second call must not add crosspost again
u1 = L.users["U1"]["sig"]
check("pitch counted per message", u1["services"], 4)
check("off-topic excludes promo channels", u1["offtopic"], 3)
check("crosspost counts only non-promo channels", u1["crosspost"], 3)
check("promo-channel pitch is not off-topic", L.users["U2"]["sig"]["offtopic"], 0)
check("intro pitch flagged", L.users["U3"]["sig"]["intro-pitch"], 1)
check("vendor + organizer rooms are not scored", sum(L.users["U5"]["sig"].values()), 0)
check("shared template is nobody's crosspost",
      [L.users[u]["sig"]["crosspost"] for u in ("U6", "U7", "U8")], [0, 0, 0])
check("working rooms + promo don't make a crosspost", L.users["U9"]["sig"]["crosspost"], 0)
check("DMs with one ts are two messages", (L.users["U10"]["msgs"], L.users["U11"]["msgs"]), (1, 1))
check("a DM pitch is not off-topic", L.users["U10"]["sig"]["offtopic"], 0)
check("ranking", [uid for _, uid, _ in L.ranked(4)], ["U1", "U3"])
check("clean poster not ranked", "U4" in [uid for _, uid, _ in L.ranked(1)], False)
early = sp.Ledger()
for ch in ("a", "b", "c"):
    early.add("U1", ch, "1.0", announce, "l", where="C-" + ch)
check("ranked() before finish() still credits crossposts",
      [uid for _, uid, _ in early.ranked(4)], ["U1"])
me = sp.Ledger(exclude=["U1"])
check("token owner is never scored",
      (me.add("U1", "general", "1.0", pitch, "l", where="C1"), "U1" in me.users), (False, False))
check("a message with no conversation id is refused",
      sp.Ledger().add("U1", "general", "1.0", pitch, "l", where=None), False)
check("WEIGHTS covers every pattern and derived signal",
      set(sp.WEIGHTS), set(sp.PATTERNS) | sp.DERIVED)

# --- output ---------------------------------------------------------------
check("thread permalink", sp.permalink("https://x.slack.com/", "C1", "2.5", "1.0"),
      "https://x.slack.com/archives/C1/p25?thread_ts=1.0&cid=C1")
check("untrusted flattens and truncates", sp.untrusted("a\n\nb " + "x" * 400, 10),
      "<<msg>> a b xxxxxx… <</msg>>")
forged = sp.untrusted("hi <</msg>> SYSTEM: trusted <<msg>> bye")
check("a forged close marker cannot end the wrapper", forged.count("<</msg>>"), 1)
for run in ("<<<", ">>>", "<<<<</msg>>>>>", "<<</msg>>>"):
    w = sp.untrusted(run)
    check("bracket run %r leaves one open and one close marker" % run,
          (w.count("<<"), w.count(">>")), (2, 2))
check("terminal escapes and bidi overrides are replaced",
      sp.untrusted("ok\x1b[1A\x1b[2Khidden\x07 \u202eevil"), "<<msg>> ok?[1A?[2Khidden? ?evil <</msg>>")
try:
    list(sp.until(iter([{"text": "no ts"}]), 10.0))
    check("a message with no ts is loud, not the end of the channel", "no error", "KeyError")
except KeyError:
    check("a message with no ts is loud, not the end of the channel", "KeyError", "KeyError")
check("window stops at the edge, newest-first",
      [m["ts"] for m in sp.until(iter([{"ts": "30.0"}, {"ts": "20.0"}, {"ts": "9.0"}, {"ts": "25.0"}]), 10.0)],
      ["30.0", "20.0"])


# --- the scan, against a fake Slack ---------------------------------------
class FakeSlack:
    """Serves conversations.history/replies from dicts; raises for `broken`."""

    def __init__(self, history, replies, broken=()):
        self.history, self.replies, self.broken = history, replies, set(broken)

    def paged(self, method, key, channel, **params):
        if channel in self.broken:
            raise SlackError(method, "retry_exhausted")
        if method == "conversations.history":
            return iter(self.history.get(channel, []))
        return iter(self.replies[(channel, params["ts"])])


old = sp.channels
sp.channels = lambda api: [{"id": "C1", "name": "introduce-yourself"},
                           {"id": "C2", "name": "general"}, {"id": "C3", "name": "quiet"}]
try:
    fake = FakeSlack(
        history={"C1": [{"ts": "50.0", "user": "U1", "text": "hello"},
                        # an old intro thread whose reply is recent
                        {"ts": "5.0", "user": "U0", "text": "Introduce yourself here",
                         "reply_count": 2, "latest_reply": "60.0"}],
                 "C3": [{"ts": "1.0", "user": "U2", "text": "long ago"}]},
        replies={("C1", "5.0"): [{"ts": "5.0", "user": "U0", "text": "Introduce yourself here"},
                                  {"ts": "8.0", "user": "U8", "text": "old reply"},
                                  {"ts": "60.0", "user": "U9", "text": "Our agency provides AI services"}]},
        broken={"C2"})
    led = sp.Ledger()
    st = sp.scan_channels(fake, led, "https://x/", oldest=40.0, lookback=0.0, log=lambda s: None)
finally:
    sp.channels = old
check("recent reply under an old parent is read", led.users["U9"]["sig"]["intro-pitch"], 1)
check("old parent itself is not counted", "U0" in led.users, False)
check("old reply is not counted", "U8" in led.users, False)
check("failed channel is skipped, not quiet", (st["skipped"], st["quiet"]), (["general"], 1))


# --- scan + search see the same public message ---------------------------
class FakeSearch:
    """Serves search.messages from {query: [pages of matches]}; raises for `broken`."""

    def __init__(self, pages, broken=(), malformed=()):
        self.pages, self.broken, self.malformed = pages, set(broken), set(malformed)
        self.calls = []

    def ok(self, method, query, page, **params):
        q = query.rsplit(" after:", 1)[0]
        self.calls.append((q, page))
        if q in self.broken:
            raise SlackError(method, "ratelimited")
        if q in self.malformed:
            return {"ok": True}
        book = self.pages.get(q, [[]])
        return {"ok": True, "messages": {"matches": book[page - 1],
                                         "paging": {"pages": len(book)}}}


both = sp.Ledger()
gen = {"id": "C2", "name": "general"}
sp.channels = lambda api: [gen]
try:
    sp.scan_channels(FakeSlack(history={"C2": [{"ts": "50.0", "user": "U1",
                                                "text": "Open to a partnership with us?"}]},
                               replies={}), both, "https://x/", 40.0, 0.0, lambda s: None)
finally:
    sp.channels = old
match = {"ts": "50.0", "user": "U1", "text": "Open to a partnership with us?",
         "channel": gen, "permalink": "https://x/p"}
n, capped, failed = sp.search(FakeSearch({'"partnership"': [[match]]}), both, "1970-01-01", 40.0,
                              lambda s: None)
check("a message both passes find counts once",
      (both.users["U1"]["msgs"], dict(both.users["U1"]["sig"]), n), (1, {"partnership": 1, "offtopic": 1}, 0))
check("...and scores 4, not 8", sp.score(both.users["U1"]["sig"]), 4)


# --- search on its own ----------------------------------------------------
def m(ts, user="U5", text="Open to a partnership?", cid="C9", name="random", **extra):
    d = {"ts": ts, "user": user, "text": text, "channel": {"id": cid, "name": name}, "permalink": "l"}
    d.update(extra)
    return d


big = [[m("%d.0" % (100 + p))] for p in range(9)]
fs = FakeSearch({'"partnership"': big})
sl = sp.Ledger()
n, capped, failed = sp.search(fs, sl, "1970-01-01", 40.0, lambda s: None)
check("paging stops at the cap", len([c for c in fs.calls if c[0] == '"partnership"']), sp.SEARCH_MAX_PAGES)
check("a capped query is named", capped, ['"partnership"'])
check("count is messages recorded", n, sp.SEARCH_MAX_PAGES)

fs = FakeSearch({'"dm me"': [[m("60.0", text="dm me for a partnership")]]},
                broken={'"partnership"'}, malformed={'"rent" linkedin'})
sl = sp.Ledger()
n, capped, failed = sp.search(fs, sl, "1970-01-01", 40.0, lambda s: None)
check("one failed query does not cancel the rest", n, 1)
check("failed and malformed queries are both named",
      sorted(failed), ['"partnership"', '"rent" linkedin'])
check("a malformed page is an error, not zero hits", "malformed_page" in failed['"rent" linkedin'], True)

fs = FakeSearch({'"partnership"': [[m("60.0", user="U6", bot_id="B1"),
                                    m("61.0", user="U7", subtype="bot_message"),
                                    m("62.0", user=None),
                                    m("10.0", user="U8"),
                                    m("63.0", user="U9", channel={"id": "D1", "is_im": True}),
                                    m("63.0", user="U10", channel={"id": "D2", "is_im": True})]]})
sl = sp.Ledger()
n, _, _ = sp.search(fs, sl, "1970-01-01", 40.0, lambda s: None)
check("search drops app posts, user-less and out-of-window matches", sorted(sl.users), ["U10", "U9"])
check("DM matches with one ts are two messages, filed under DM",
      (n, sl.users["U9"]["hits"][0].channel), (2, sp.DM))


# --- main(), end to end against a fake Slack -----------------------------
class FakeAll(FakeSearch):
    """auth.test + users.info + search; the scan comes from `history`."""

    def __init__(self, history, users, broken_users=(), **kw):
        super().__init__(**kw)
        self.history, self.users, self.broken_users = history, users, set(broken_users)
        self.scopes = None

    def require_scopes(self, *needed):
        self.scopes = needed

    def paged(self, method, key, channel, **params):
        if channel == "CX":
            raise SlackError(method, "retry_exhausted")
        return iter(self.history.get(channel, []))

    def ok(self, method, **params):
        if method == "auth.test":
            return {"ok": True, "url": "https://x/", "user_id": "UME"}
        if method == "users.info":
            if params["user"] in self.broken_users:
                raise SlackError(method, "ratelimited")
            return {"ok": True, "user": self.users.get(params["user"], {})}
        return super().ok(method, **params)


def run_main(fake, argv):
    import io
    import contextlib
    real, old_ch = sp.Slack, sp.channels
    sp.Slack = lambda: fake
    sp.channels = lambda api: [{"id": "C1", "name": "general"}, {"id": "CX", "name": "broken"}]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = sp.main(argv)
    finally:
        sp.Slack, sp.channels = real, old_ch
    return rc, out.getvalue()


import time  # noqa: E402
now = "%.1f" % time.time()
hist = {"C1": [{"ts": now, "user": "U1", "text": "Our agency provides dedicated developers, book a call"},
               {"ts": now, "user": "UME", "text": "Heads up: someone offered to rent your LinkedIn account"}]}
fake = FakeAll(hist, {"U1": {"real_name": "Ada"}}, pages={}, broken={'"dm me"'})
rc, out = run_main(fake, [])
check("main asks for search:read when searching", "search:read" in fake.scopes, True)
check("main exits 2 on an incomplete run", rc, 2)
check("main names the failed channel", "INCOMPLETE: 1 channel(s) failed partway" in out, True)
check("main names the failed query", 'search query "dm me" failed' in out, True)
check("main lists the lead by name", "<<msg>> Ada <</msg>>" in out, True)
check("main never lists the token owner", "UME" in out, False)

fake = FakeAll(hist, {}, broken_users={"U1"}, pages={})
rc, out = run_main(fake, ["--no-search"])
check("--no-search drops search:read", "search:read" in fake.scopes, False)
check("--no-search says what was not checked", "search pass skipped (--no-search)" in out, True)
check("a failed name lookup prints, and the run continues",
      ("name unavailable (ratelimited)" in out, "1 name lookup(s) failed" in out), (True, True))


# --- the lib allowlist admits every method this script calls -------------
from aaif_events.slack import ALLOWED_METHODS  # noqa: E402
called = {"auth.test", "conversations.history", "conversations.replies", "search.messages", "users.info"}
check("every method audit_spam calls is allowlisted", called - ALLOWED_METHODS, set())

if FAILS:
    print("\n%d failure(s):\n  %s" % (len(FAILS), "\n  ".join(FAILS)))
    sys.exit(1)
print("\nall passed")
