#!/usr/bin/env python3
"""Tests for the banner-coverage guard, driven against a synthetic tree.

`main()` reading the real repo can only ever be tested against a tree that is
already consistent, so the decision function is exercised on fixtures instead —
the same shape the repo's other guards use.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_tooling_banner_coverage import offenders  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


BANNER = "> **Tooling rule — `gws` + Python only.** ...\n"


def tree(root, skills):
    """skills: {name: (skill_md_text, {relpath: source})}"""
    for name, (doc, files) in skills.items():
        d = os.path.join(root, name)
        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(doc)
        for rel, src in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(src)
    return root


with tempfile.TemporaryDirectory() as td:
    root = tree(td, {
        # drives gws, no banner -> the regression this guard exists for
        "a-bare": ("# A\n", {"scripts/run.py": "from aaif_events import gws\n"}),
        # drives gws and carries the banner -> fine
        "b-ok": ("# B\n" + BANNER, {"scripts/run.py": "gws_json('drive')\n"}),
        # carries the banner, drives nothing -> never objected to
        "c-carrier": ("# C\n" + BANNER, {"scripts/run.py": "print('hi')\n"}),
        # no scripts at all (a content skill) -> out of scope
        "d-content": ("# D\n", {}),
        # only a TEST names gws — the rule governs the engine, not its double
        "e-testonly": ("# E\n", {"scripts/test_run.py": "gws_json = None\n"}),
        # a migration counts too: it edits real Drive files
        "f-migration": ("# F\n", {"migrations/migrate_x.py": "from aaif_events.gws import run\n"}),
    })
    got = dict(offenders(root))

check("a skill driving gws with no banner is reported",
      "a-bare" in got, True)
check("...and the offending script is named, so the fix is obvious",
      got.get("a-bare"), ["scripts/run.py"])
check("a gws skill that carries the banner is clean", "b-ok" in got, False)
check("a carrier that drives nothing is never objected to",
      "c-carrier" in got, False)
check("a content skill with no scripts is out of scope",
      "d-content" in got, False)
check("a test naming gws does not drag its skill in",
      "e-testonly" in got, False)
check("a migration driving gws counts — it edits real Drive files",
      got.get("f-migration"), ["migrations/migrate_x.py"])

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("check_tooling_banner_coverage: all checks passed")
