#!/usr/bin/env python3
"""Fail when a skill's coupling to `lib/aaif_events` disagrees with the README.

A skill whose scripts import the shared library only runs from a full checkout
or a plugin install: zipped on its own for claude.ai, the zip has no `lib/`, and
the skill fails at import. That is a real cost, and AGENTS.md says taking it on
is a decision — "don't hoist a helper into `lib` without deciding the skill can
stop being portable".

Nothing enforced that decision, so it could be made by accident, one convenient
import at a time, and the README's list of affected skills had drifted to five
where eight were coupled. Someone reading it would zip one of the other three
and find out at runtime.

This compares the two: the skills whose non-test scripts IMPORT `aaif_events`,
against the skills the README names. Either list moving without the other is
the failure. Adding an import is still allowed — it just has to be written down
in the same commit.

This was a substring scan until a script grew a comment saying why it
deliberately does NOT import the library — exactly the false positive the old
docstring said to tighten on when it arrived. It is an AST pass over the import
statements now, so prose about `aaif_events` is prose:
`aaif-create-chapter/scripts/deck_estate.py` explains that it is *not*
`lib/aaif_events`, and `aaif-sync/scripts/sync.py` names the package while
importing nothing, driving every engine by subprocess instead. Neither is
coupled; under the substring scan both read as coupled.

A file that does not parse is a hard failure rather than "not coupled": a scan
that answers "no import" for a broken file answers the wrong question.

Only non-test scripts count. A test already runs from the checkout, so importing
the library there costs nothing.

Usage:  python3 scripts/check_portable_skills.py
"""
import ast
import glob
import os
import re
import sys

README = "README.md"

#: The paragraph naming the coupled skills, matched from its own sentence so
#: that moving the section around the README does not break the check. Matched
#: across whitespace, because the phrase is prose and prose gets re-wrapped —
#: a check that fails on a reflowed paragraph teaches people to delete it.
MARKER = "do **not** work zipped standalone"
MARKER_RE = re.compile(r"\s+".join(re.escape(w) for w in MARKER.split()))


def imports_lib(source, path="<source>"):
    """True when this module actually imports `aaif_events` (not just names it).

    Both spellings the repo uses count: `from aaif_events import gws` and
    `import aaif_events.redact`, at any nesting — a couple of scripts import
    inside a function.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        sys.exit("ABORT: %s does not parse (%s). A guard that reads an "
                 "unparsable file as 'not coupled' answers the wrong "
                 "question." % (path, exc))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "aaif_events":
            return True
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "aaif_events" for a in node.names):
                return True
    # An AST pass sees `import` statements, and a dynamic import is not one.
    # `importlib.import_module("aaif_events.gws")` would read as portable and
    # leave the README wrong — the same quiet drift this guard exists to catch,
    # one spelling further out. Nothing in the repo does it; rather than model
    # it, refuse it, so the question comes to a person.
    if "aaif_events" in source and ("importlib" in source or "__import__" in source):
        sys.exit("ABORT: %s reaches for `aaif_events` and also mentions a "
                 "dynamic import. This guard reads `import` statements, so it "
                 "cannot tell whether that skill is coupled. Import it "
                 "normally, or teach this check the case on purpose." % path)
    return False


def coupled_skills():
    """Skills whose non-test scripts import `aaif_events` (see the docstring)."""
    out = set()
    for path in glob.glob("skills/*/scripts/*.py") + glob.glob("skills/*/migrations/*.py"):
        if os.path.basename(path).startswith("test_"):
            continue
        with open(path, encoding="utf-8") as fh:
            if imports_lib(fh.read(), path):
                out.add(path.split(os.sep)[1])
    return out


def documented_skills(text=None):
    """Skills named in the README's standalone-zip caveat."""
    if text is None:
        with open(README, encoding="utf-8") as fh:
            text = fh.read()
    m = MARKER_RE.search(text)
    i = m.start() if m else -1
    if i < 0:
        sys.exit("ABORT: %s no longer contains the standalone-zip caveat (%r). "
                 "If it moved, update MARKER here; if it went away, this check "
                 "has nothing to compare against." % (README, MARKER))
    # The caveat runs to the end of its paragraph.
    end = text.find("\n\n", i)
    return set(re.findall(r"`(aaif-[a-z-]+)`", text[i:end if end > 0 else len(text)]))


def compare(actual, documented):
    """(undocumented, stale) — the whole decision, drivable without the repo.

    Split out of `main()` because `main()` reading the real tree could only
    ever be tested against a repo that is already consistent: both error
    branches were unreachable from a test, and `main()` could be replaced with
    `return 0` with the suite still green.
    """
    return sorted(actual - documented), sorted(documented - actual)


def main():
    actual, documented = coupled_skills(), documented_skills()
    undocumented, stale = compare(actual, documented)
    if not undocumented and not stale:
        print("check_portable_skills: %d skill(s) import aaif_events, all documented."
              % len(actual))
        return 0

    if undocumented:
        print("ERROR: these skills import `lib/aaif_events` but the README does "
              "not list them as needing a full checkout:\n")
        for s in undocumented:
            print("  %s" % s)
        print("\nEither add them to the caveat in %s — giving up a skill's "
              "portability is a decision, so write it down — or drop the import."
              % README)
    if stale:
        print("\nERROR: the README lists these as coupled, but nothing in them "
              "imports `lib/aaif_events` any more:\n")
        for s in stale:
            print("  %s" % s)
        print("\nThey are portable again; take them out of the caveat.")
    return 1


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    sys.exit(main())
