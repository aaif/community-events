#!/usr/bin/env python3
"""Assert the "Tooling rule" banner is byte-identical in every SKILL.md that carries it.

The rule (gws + Python only, native Google formats, never LibreOffice) has to be
duplicated into each SKILL.md because the skills ship downstream on their own —
CLAUDE.md does not travel with them. Duplication is therefore deliberate, but
undetected *drift* between the copies is not: a skill whose banner quietly loses
the `unoconv` ban would let an agent reach for it without violating anything it
was told.

This does not police which skills carry the banner — that is an editorial call.
It only enforces that every copy which exists says exactly the same thing.

Usage:  python3 scripts/check_tooling_banner.py [FILE...]
        (no args = every skills/*/SKILL.md)
"""
import glob, os, sys

MARKER = "> **Tooling rule"


def extract(path):
    """Return the banner blockquote, or None when the file doesn't carry one."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    for i, line in enumerate(lines):
        if line.startswith(MARKER):
            block = []
            for line in lines[i:]:
                # The banner is one unbroken blockquote; the first non-"> " line ends it.
                if not line.startswith(">"):
                    break
                block.append(line)
            return "\n".join(block)
    return None


def main(argv):
    paths = argv[1:] or sorted(glob.glob("skills/*/SKILL.md"))
    banners = {}
    for p in paths:
        b = extract(p)
        if b is not None:
            banners.setdefault(b, []).append(p)

    if len(banners) <= 1:
        n = len(next(iter(banners.values()))) if banners else 0
        print("check_tooling_banner: %d file(s) carry the banner, all identical." % n)
        return 0

    # More than one distinct text: report the minority variants against the most
    # common one, which is almost always the intended wording.
    canonical, canonical_files = max(banners.items(), key=lambda kv: len(kv[1]))
    print("ERROR: the tooling-rule banner has drifted between SKILL.md files.\n")
    print("Canonical (%d file(s)): %s" % (len(canonical_files), ", ".join(canonical_files)))
    for text, files in banners.items():
        if text == canonical:
            continue
        print("\nDiffers in: %s" % ", ".join(files))
        want = canonical.split("\n")
        got = text.split("\n")
        for i in range(max(len(want), len(got))):
            w = want[i] if i < len(want) else "(missing)"
            g = got[i] if i < len(got) else "(missing)"
            if w != g:
                print("  line %d\n    canonical: %s\n    here     : %s" % (i + 1, w, g))
    print("\nMake every copy identical, or remove the banner from the outliers.")
    return 1


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    sys.exit(main(sys.argv))
