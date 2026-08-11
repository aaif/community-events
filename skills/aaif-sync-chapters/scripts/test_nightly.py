#!/usr/bin/env python3
"""Self-tests for the nightly runner's pure logic. No network, no subprocesses.

The runner's one non-obvious mapping is classify(): the shared exit-code
convention (0 in sync / 2 drift / else failure) crossed with the 'Verified:'
log marker that separates "--write wrote" from "--write had nothing to do".
Getting it wrong mislabels a nightly run — a FAILED read as DRIFT would send
someone to approve a proposal that never existed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nightly  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


check("report mode, exit 0 -> in sync",
      nightly.classify(0, wrote_marker=False, write_mode=False), nightly.IN_SYNC)
check("report mode, exit 2 -> drift",
      nightly.classify(2, wrote_marker=False, write_mode=False), nightly.DRIFT)
check("report mode, exit 1 -> failed",
      nightly.classify(1, wrote_marker=False, write_mode=False), nightly.FAILED)
check("write mode, exit 0 with Verified marker -> wrote+verified",
      nightly.classify(0, wrote_marker=True, write_mode=True), nightly.WROTE)
check("write mode, exit 0 without marker -> nothing needed doing",
      nightly.classify(0, wrote_marker=False, write_mode=True), nightly.IN_SYNC)
check("write mode, nonzero is failed even if the marker printed "
      "(a verify that failed after a partial write)",
      nightly.classify(1, wrote_marker=True, write_mode=True), nightly.FAILED)
check("a stray Verified line in report mode never claims a write",
      nightly.classify(0, wrote_marker=True, write_mode=False), nightly.IN_SYNC)
check("a PARTIAL marker beats in-sync — a half-checked night is never healthy",
      nightly.classify(0, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.PARTIAL)
check("a PARTIAL marker beats drift — the log has the details either way",
      nightly.classify(2, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.PARTIAL)
check("but never beats FAILED, which is strictly worse news",
      nightly.classify(1, wrote_marker=False, write_mode=False,
                       partial_marker=True), nightly.FAILED)

# The pipeline order the runner encodes must be the SKILL.md order — feed first,
# resources last — because each stage's writes are inputs to the next.
check("engine order is the documented pipeline order",
      [n for n, _ in nightly.ENGINES],
      ["chapters", "about", "crm", "access", "resources"])

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nnightly: all checks passed")
