#!/usr/bin/env python3
"""Refuse real people's identifiers in tracked files. Run by pre-commit and CI.

This exists because it already happened. A real organizer's Gmail address, in
both spellings, went into a `lib/` docstring under the words "Verified live",
and two real Slack account ids plus a handle went into a test fixture — all of
it pushed to a public repo, where a rewrite cleans the branch but leaves the old
objects served at their SHA until GitHub garbage-collects them. AGENTS.md said
not to. Saying so was not enough.

WHAT THIS CAN AND CANNOT DO. A checker cannot tell a real person's name from an
invented one: `first.last@gmail.com` is fine and `realname.here@gmail.com` is
not, and nothing in the text distinguishes them. So it enforces the two rules
that ARE decidable, and the rest stays a human judgement:

  1. An account-id SHAPE (a Slack `U…`/`W…` id) must be on the allowlist below.
     Real ids are high-entropy; every synthetic one this repo uses is a visible
     fake (a repeated run, or a word). A new id in a diff is nearly always one
     someone pasted out of a live run, so it has to be added here deliberately.

  2. An email address must be at a KNOWN-SYNTHETIC domain, or — at a domain
     this estate really uses — carry a local part from the allowlist. Real
     people are at gmail.com and at the community's own domains; fixtures are
     at x.com and example.com. That is the line, and it is checkable.

Adding to an allowlist is the escape hatch, and it is meant to be a speed bump:
if you are adding a value you observed in a live run, that is the bug.
"""
import os
import re
import subprocess
import sys

#: Domains that cannot belong to a real person — fixtures, RFC-reserved names,
#: and the obvious stand-ins this repo already uses. An address here is free.
SYNTHETIC_DOMAINS = {
    "x.com", "x.io", "x.test", "b.co", "b.io", "c.io", "c.co", "c.co.uk",
    "y.example", "z.example", "one.test", "two.test", "old.example",
    "new.example", "else.com", "else.io", "example.com", "example.co",
    "example.company", "examples.com", "notexample.com", "nowhere.com",
    "acme.com", "vendor.co", "vendor.studio", "slack.com", "aaif.test",
}

#: Domains real people in this estate actually use. An address at one of these
#: is allowed only with a local part from SYNTHETIC_LOCALS — the tests need to
#: exercise "is this address at the staff domain?" without naming a colleague.
REAL_DOMAINS = {
    "gmail.com", "googlemail.com", "fastmail.com", "mlops.community",
    "aihero.studio", "linuxfoundation.org", "aaif.io",
}

#: Local parts that are plainly invented. Kept explicit rather than pattern-
#: matched: "is this a real person's name" is the judgement being made, and a
#: list makes each answer visible in review.
SYNTHETIC_LOCALS = {
    "a", "b", "c", "d", "e", "k", "o", "s", "z", "ab", "a.b", "a.b+tag",
    "ada", "bo", "cy", "dee", "eve", "zed", "gone", "ghost", "grace", "jane",
    "jane+aaif", "sam", "ravi", "ops", "two", "newhire", "outsider",
    "stranger", "handle7", "mixedcase7", "mixed.case7", "a+tag", "a.b.c", "first.last",
    "firstlast", "first.m.last", "firstmlast", "not-an-email",
}

#: Slack ids that are visibly fake. Every real id is high-entropy mixed
#: alphanumerics; these are repeated runs or words, and that is the point.
ALLOWED_SLACK_IDS = {
    "U0AAAAAAA", "U0AAAAAAAAA", "U02BBBBBBBB", "W0AAAAAAAAA",
    "U0GONEGONE", "U0LIVELIVE", "USLACKBOT", "U0BBBBBBB", "U0DDDDDDD",
}

#: `[UW]` + 7-11 uppercase alphanumerics, requiring at least one digit. The
#: digit is what separates an id from an ordinary capitalised word — without it
#: this flags WARNING, UNKNOWN and UNMEASURED on every run, and a check that
#: cries wolf gets disabled.
SLACK_ID_RE = re.compile(r"\b(?=[A-Z0-9]*[0-9])[UW][A-Z0-9]{7,11}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

#: Only text a human writes. Caches and downloaded artefacts are gitignored and
#: never reach here; binaries would produce noise.
SUFFIXES = (".py", ".md", ".json", ".txt", ".yml", ".yaml", ".html", ".css")

#: This file names the very identifiers it forbids in its own docstring and
#: allowlists, and the audit caches are full of real ones by design.
SKIP = {"scripts/check_no_real_pii.py", "scripts/test_check_no_real_pii.py"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                         check=True).stdout.splitlines()
    return [f for f in out
            if f.endswith(SUFFIXES) and f not in SKIP and os.path.isfile(f)]


def check_text(path, text):
    """[(line number, what, value, why)] for every violation in one file."""
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in SLACK_ID_RE.finditer(line):
            if m.group(0) not in ALLOWED_SLACK_IDS:
                bad.append((n, "Slack id", m.group(0),
                            "not a known synthetic id — if you copied it from a "
                            "live run it is a real account"))
        for m in EMAIL_RE.finditer(line):
            addr = m.group(0)
            local, _, domain = addr.rpartition("@")
            domain, local = domain.lower(), local.lower()
            if domain in SYNTHETIC_DOMAINS:
                continue
            if domain in REAL_DOMAINS and local in SYNTHETIC_LOCALS:
                continue
            if domain in REAL_DOMAINS:
                bad.append((n, "email", addr,
                            "a real domain with a local part that is not a known "
                            "stand-in"))
            else:
                bad.append((n, "email", addr,
                            "unknown domain — use one of: "
                            + ", ".join(sorted(list(SYNTHETIC_DOMAINS)[:4]))
                            + ", …"))
    return bad


def main():
    findings = []
    files = tracked_files()
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue    # a symlink to a missing file, or genuinely not text
        for n, kind, value, why in check_text(path, text):
            findings.append("%s:%d  %s %r — %s" % (path, n, kind, value, why))

    if findings:
        print("REFUSING: %d real-looking identifier(s) in tracked files.\n"
              % len(findings), file=sys.stderr)
        for f in findings:
            print("  " + f, file=sys.stderr)
        print("\nThis repo is public. An identifier committed here is public "
              "forever: rewriting history cleans the branch, but the old commit "
              "is still served at its SHA until GitHub garbage-collects it, "
              "which needs a support request.\n"
              "\nUse a synthetic value (a@x.com, Ada, Boston, U0AAAAAAAAA). If a "
              "value genuinely must appear, add it to the allowlist in %s and "
              "say why in the commit message — and if you took it from a live "
              "run, don't." % __file__, file=sys.stderr)
        return 1

    print("OK: no real-looking identifiers in %d tracked file(s)" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
