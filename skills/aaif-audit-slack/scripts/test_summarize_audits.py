#!/usr/bin/env python3
"""Self-tests for org_findings()'s gaps/no_account split.

Standalone (not pytest) to match the other skills' script tests, which CI picks
up via `for t in skills/*/scripts/test_*.py`.

Covers the one property that has already broken silently once: a person with
no Slack account and a person who has an account but wasn't invited must land
in exactly one of `gaps`/`no_account`, never both, never neither, and never
dropped because their chapter has no organizers channel yet.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import summarize_audits as sa  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def person(slack_account, in_organizers):
    return {"slack_account": slack_account, "in_organizers": in_organizers}


def chapter(city, organizers_channel, accepted):
    return {"city": city, "organizers_channel": organizers_channel,
            "organizers_private": True, "public": "x", "accepted": accepted,
            "unaccounted": []}


# --- the split is exhaustive: every non-invited accepted person lands in
# exactly one bucket -----------------------------------------------------------
audit = {"chapters": [
    chapter("Ahmedabad", "ahmedabad-organizers",
            [person(True, True), person(True, True), person(True, True),
             person(True, True), person(False, False), person(False, False),
             person(False, False)]),
]}
o = sa.org_findings(audit)
check("no-account people never appear in gaps", o["gaps"], [])
check("they appear in no_account instead", o["no_account"],
      [("Ahmedabad", 3, 7)])

# --- an invitable person (has an account, just not in the channel) lands in
# gaps, never no_account --------------------------------------------------------
audit = {"chapters": [
    chapter("Lagos", "lagos-organizers",
            [person(True, False), person(True, True)]),
]}
o = sa.org_findings(audit)
check("an invitable gap lands in gaps", o["gaps"], [("Lagos", 1, 2)])
check("and not in no_account", o["no_account"], [])

# --- a no-account organizer is counted even with no organizers channel yet —
# the bug this fixes: gating no_account on organizers_channel dropped exactly
# the newest chapters, where an unconfirmed intake email is most likely --------
audit = {"chapters": [
    chapter("Kinshasa", "", [person(False, False)]),
]}
o = sa.org_findings(audit)
check("a chapter with no channel yet still counts its no-account organizer",
      o["no_account"], [("Kinshasa", 1, 1)])
check("but is not a gap — there is no channel to be missing from",
      o["gaps"], [])

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nsummarize_audits: all checks passed")
