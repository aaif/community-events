#!/usr/bin/env python3
"""Fail when a skill that drives `gws` does not carry the tooling-rule banner.

`check_tooling_banner.py` proves every *copy* of a banner agrees. It says so
itself: it does not police which skills carry which banner, because that is an
editorial call. That leaves one hole, and it is not theoretical — splitting
`aaif-sync-chapters` into four skills dropped the tooling rule from the one that
had it and never added it to the three new ones, and every check stayed green.
The banner count went 12 -> 11 and nothing said a word.

This check closes exactly that hole and nothing wider. The editorial call is
which skills carry the rule; what is *not* editorial is a skill whose own
scripts shell out to `gws` shipping without the rule that governs how `gws` may
be used. Those skills read and write real Drive files, and they ship downstream
without AGENTS.md — the banner is the only copy of the rule that travels with
them. A skill with no such script may still carry it (several do, because the
agent does the Drive work by hand there); this check never objects to a carrier.

Usage:  python3 scripts/check_tooling_banner_coverage.py
"""
import glob
import os
import re
import sys

MARKER = "> **Tooling rule"

#: A script "drives gws" when it imports the shared client or names the binary.
#: Substring matching, like its sibling checks — a skill that only mentions gws
#: in a comment is a false positive, and a false positive here costs one banner.
DRIVES_GWS = re.compile(
    r"aaif_events import [^\n]*\bgws\b|from aaif_events\.gws|\bgws_json\b|[\"']gws[\"']")


def offenders(root="skills"):
    """[(skill, [scripts that drive gws])] for skills carrying no tooling rule."""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*", ""))):
        doc = os.path.join(d, "SKILL.md")
        if not os.path.exists(doc):
            continue
        with open(doc, encoding="utf-8") as fh:
            if MARKER in fh.read():
                continue
        driving = []
        for sub in ("scripts", "migrations"):
            for p in sorted(glob.glob(os.path.join(d, sub, "*.py"))):
                # A test may name gws only to mock it; the rule governs the
                # engine, not its test double.
                if os.path.basename(p).startswith("test_"):
                    continue
                with open(p, encoding="utf-8") as fh:
                    if DRIVES_GWS.search(fh.read()):
                        driving.append(os.path.relpath(p, d))
        if driving:
            out.append((os.path.basename(d.rstrip(os.sep)), driving))
    return out


def main():
    bad = offenders()
    if not bad:
        print("check_tooling_banner_coverage: every gws-driving skill carries "
              "the tooling rule.")
        return 0
    print("ERROR: these skills drive `gws` but their SKILL.md carries no "
          "tooling-rule banner.\nThe skills ship downstream without AGENTS.md, "
          "so the banner is the only copy\nof the rule that travels with them. "
          "Paste it in, byte-identical — copy it from\na skill that has it; "
          "check_tooling_banner.py will catch a retyped one.\n")
    for skill, scripts in bad:
        print("  %s" % skill)
        for s in scripts:
            print("      %s" % s)
    return 1


if __name__ == "__main__":
    sys.exit(main())
