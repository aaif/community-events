#!/usr/bin/env python3
"""Assert each shared SKILL.md banner is byte-identical everywhere it appears.

A few rules have to be duplicated into each SKILL.md because the skills ship
downstream on their own — CLAUDE.md does not travel with them. Duplication is
therefore deliberate, but undetected *drift* between the copies is not:

* **Tooling rule** — gws + Python only, native Google formats, never
  LibreOffice. A copy that quietly loses the `unoconv` ban lets an agent reach
  for it without violating anything it was told.
* **Public-copy rule** — never publish an address, a phone number, a door code
  or a non-public name. This was nine hand-written variants of one sentence,
  which is one edit away from a skill whose copy of the rule is narrower than
  the rest.
* **Standard footer** — the Code of Conduct and Privacy Policy links on
  attendee-facing copy. Four variants, and a chapter forking this repo has to
  find every one of them to swap the URLs.

This does not police which skills carry which banner — that is an editorial
call. It only enforces that every copy which exists says exactly the same thing.

Usage:  python3 scripts/check_tooling_banner.py [FILE...]
        (no args = every skills/*/SKILL.md)
"""
import glob, os, sys

#: Each shared banner, by the blockquote line that starts it. A banner is one
#: unbroken blockquote, so the marker plus "until the quoting stops" delimits it.
MARKERS = ("> **Tooling rule", "> **Public-copy rule", "> **Standard footer")


def extract(path, marker):
    """Return this banner's blockquote, or None when the file doesn't carry it."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    for i, line in enumerate(lines):
        if line.startswith(marker):
            block = []
            for line in lines[i:]:
                # The banner is one unbroken blockquote; the first non-">" line ends it.
                if not line.startswith(">"):
                    break
                block.append(line)
            return "\n".join(block)
    return None


def check_one(paths, marker):
    """0 when every copy of this banner agrees; 1 and a diff when they do not."""
    banners = {}
    for p in paths:
        b = extract(p, marker)
        if b is not None:
            banners.setdefault(b, []).append(p)

    if len(banners) <= 1:
        n = len(next(iter(banners.values()))) if banners else 0
        print("  %-24s %d file(s), all identical." % (marker.strip("> *"), n))
        return 0

    # More than one distinct text: report the minority variants against the most
    # common one, which is almost always the intended wording.
    canonical, canonical_files = max(banners.items(), key=lambda kv: len(kv[1]))
    print("ERROR: the %s banner has drifted between SKILL.md files.\n"
          % marker.strip("> *"))
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


def main(argv):
    paths = argv[1:] or sorted(glob.glob("skills/*/SKILL.md"))
    print("check_tooling_banner:")
    return max(check_one(paths, m) for m in MARKERS)


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    sys.exit(main(sys.argv))
