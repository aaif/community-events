#!/usr/bin/env python3
"""Self-tests for the spam audit's pure logic. No network. Synthetic text only."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_spam as sp  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s:\n     got:  %r\n     want: %r" % (label, got, want))
        print("FAIL %s" % label)
    else:
        print("ok   %s" % label)


check("rental offer", sp.signals("I'd like to rent your LinkedIn account, $300 per month"),
      ["linkedin-rent", "money"])
check("rental, profile wording", "linkedin-rent" in sp.signals(
      "Looking for people with a LinkedIn profile, we pay monthly"), True)
check("plain linkedin mention is clean", sp.signals("Here's my LinkedIn if you want to chat"), [])
check("agency pitch", sp.signals("Our agency provides dedicated developers. Book a call!"),
      ["services"])
check("partnership + dm", sp.signals("Open to a partnership? DM me"), ["partnership", "dm-me"])
check("ordinary question is clean", sp.signals("How do you serve vLLM behind a load balancer?"), [])
check("short text has no fingerprint", sp.fingerprint("hi all"), None)
check("fingerprint ignores case and punctuation",
      sp.fingerprint("Join our AI event this Thursday in Boston, RSVP on the page, free pizza!!"),
      sp.fingerprint("join our ai event this thursday in boston rsvp on the page free pizza"))

L = sp.Ledger()
pitch = "We offer dedicated developers for your AI product, book a call with our team today"
for ch in ("general", "agents", "boston", "be-shameless"):
    L.add("U1", ch, "1.%s" % len(ch), pitch, "link")
L.add("U1", "general", "1.7", pitch, "dupe")  # same (channel, ts) seen twice counts once
L.add("U2", "be-shameless", "2.0", "We offer an open-source eval kit, check it out", "link")
L.add("U3", "introduce-yourself", "3.0", "Hi, I'm Ada from Boston. Our team provides AI services", "l")
L.add("U4", "general", "4.0", "Anyone going to the Boston event?", "link")
L.finish()
u1 = L.users["U1"]["sig"]
check("pitch counted per message", u1["services"], 4)
check("off-topic excludes promo channels", u1["offtopic"], 3)
check("crosspost counts channels", u1["crosspost"], 4)
check("promo-channel pitch is not off-topic", L.users["U2"]["sig"]["offtopic"], 0)
check("intro pitch flagged", L.users["U3"]["sig"]["intro-pitch"], 1)
L.add("U5", "ext-acme-partner", "5.0", "Happy to explore a partnership on the launch", "l")
L.add("U5", "boston-organizers", "5.1", "Our sponsor offers a partnership tier", "l")
check("vendor + organizer rooms are not scored", sum(L.users["U5"]["sig"].values()), 0)
check("crypto chatter is clean", sp.signals("most agent projects are as bad as crypto"), [])
check("ranking", [uid for _, uid, _ in L.ranked(4)], ["U1", "U3"])
check("clean poster not ranked", "U4" in [uid for _, uid, _ in L.ranked(1)], False)
check("window stops at the edge, newest-first",
      [m["ts"] for m in sp.in_window(iter([{"ts": "30.0"}, {"ts": "20.0"}, {"ts": "9.0"}, {"ts": "25.0"}]), 10.0)],
      ["30.0", "20.0"])
check("thread permalink", sp.permalink("https://x.slack.com/", "C1", "2.5", "1.0"),
      "https://x.slack.com/archives/C1/p25?thread_ts=1.0&cid=C1")
check("snippet flattens and truncates", sp.snippet("a\n\nb " + "x" * 400)[:4], "a b ")

if FAILS:
    print("\n%d failure(s):\n  %s" % (len(FAILS), "\n  ".join(FAILS)))
    sys.exit(1)
print("\nall passed")
