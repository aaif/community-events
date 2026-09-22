#!/usr/bin/env python3
"""Fail when a non-replayable `gws` call is sent without `NO_RETRY`.

A Drive `files.create`/`files.copy`, or a Sheets `appendDimension` /
`insertDimension` / `addSheet`, that succeeded server-side but answered like a
timeout is indistinguishable here from one that never landed. The shared client
retries by default, so the retry sends it again — and makes a second folder,
file, column or tab. **Nothing downstream detects that duplicate.** The next
listing simply reports a subtree holding everything twice, a verify that reads
by header name finds the names it asked for, and Sheets does not refuse a
duplicate tab title, it renames the second one.

`aaif_events.gws` cannot decide this for the caller: only the caller knows
whether the verb it is sending is replayable. So the rule lives at each call
site as `retries=...NO_RETRY`, which made it documentation — opt-in, per site,
with nothing to notice a site that was never opted in. It was not hypothetical:
when the rule was first written down, four call sites in the repo already
violated it, including one inside `lib/` whose private wrapper could not even
express the keyword.

This is the counterexample detector. Two tiers, because the two APIs put the
verb in different places:

**Drive — exact.** The verb IS the first three positional arguments
(`"drive", "files", "create"`), so a call can be judged on its own. Every such
call in this repo is written with string literals, including the ones that
build an argv list inline, so there is no dynamic-construction blind spot.

**Sheets — module level.** The verb is a key inside a `requests` list that is
routinely built in a different function from the call that sends it
(`install_ops_notes.install` builds `reqs`, then sends it four lines later), so
"this call carries an appendDimension" is not decidable per call. The coarse
rule instead: a file that mentions one of those request keys must mention
`NO_RETRY` somewhere. It is weaker, and it is what caught the three unguarded
writes in `migrate_resource_columns.py`.

An exception is argued in `ALLOW`, with a reason, which is this repo's shape
for a rule that has a real exception. Adding a line there to make a check pass
is the bug, not the fix.

Usage:  python3 scripts/check_non_idempotent_retries.py [FILE...]
        (no args = every *.py under lib/ and skills/)
"""
import ast
import glob
import os
import sys

#: Drive verbs that create a new object each time they are sent.
DRIVE_VERBS = (("drive", "files", "create"), ("drive", "files", "copy"))

#: Sheets request keys that add a row, column or tab each time they are sent.
SHEETS_REQUESTS = ("appendDimension", "insertDimension", "addSheet",
                   "duplicateSheet")

#: The keyword, and the spellings of its value that count. The bare literal `1`
#: counts too: it is the same behaviour, and a check that rejected it would be
#: enforcing a name rather than the property.
KEYWORD = "retries"
GUARD_NAMES = ("NO_RETRY",)

#: {path: why}. An entry here says "this verb is replayable in this context",
#: not "this was inconvenient to fix".
ALLOW = {}


def _guarded(node):
    """True when this Call passes `retries=NO_RETRY` (or a bare 1)."""
    for kw in node.keywords:
        if kw.arg != KEYWORD:
            continue
        v = kw.value
        if isinstance(v, ast.Constant) and v.value == 1:
            return True
        if isinstance(v, ast.Name) and v.id in GUARD_NAMES:
            return True
        if isinstance(v, ast.Attribute) and v.attr in GUARD_NAMES:
            return True
    return False


def _const_strings(items):
    """The leading string constants of an argument or element sequence."""
    out = []
    for a in items:
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            break
        out.append(a.value)
    return tuple(out)


def _leading_strings(node):
    """The verb tokens this call sends, in either shape the repo writes.

    Two shapes, and missing the second one would have left a real site
    unguarded: `gws_json("drive", "files", "create", ...)` passes the verb as
    positional arguments, while `_gws(["gws", "drive", "files", "create",
    "--upload", ...], ...)` passes a whole argv list, because the file helpers
    need `--upload`/`--output` and a `cwd`. `sync_badges.upload_new` is the
    second shape, and it is a `files.create`.
    """
    lead = _const_strings(node.args)
    if lead:
        return lead
    if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
        argv = _const_strings(node.args[0].elts)
        return argv[1:] if argv[:1] == ("gws",) else argv
    return ()


def drive_findings(source, path="<source>"):
    """[(line, verb)] for each unguarded Drive create/copy in this module."""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lead = _leading_strings(node)
        for verb in DRIVE_VERBS:
            if lead[:len(verb)] == verb and not _guarded(node):
                out.append((node.lineno, " ".join(verb)))
    return out


def sheets_findings(source, path="<source>"):
    """[(line, key)] for each non-replayable Sheets request key in a module
    that never mentions the guard at all.

    Deliberately coarse: the request dict and the call that sends it are
    routinely in different functions, so a per-call rule is not decidable. A
    file that names one of these keys and never names `NO_RETRY` has not
    thought about it; a file that names both is making a judgement this check
    is not equipped to second-guess.
    """
    if any(g in source for g in GUARD_NAMES):
        return []
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in SHEETS_REQUESTS:
            out.append((node.lineno, node.value))
    return out


def findings(source, path="<source>"):
    """Every finding in one module, as [(line, what, tier)]."""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        sys.exit("ABORT: %s does not parse (%s). A guard that reads an "
                 "unparsable file as 'no findings' answers the wrong question."
                 % (path, exc))
    return ([(ln, v, "drive") for ln, v in drive_findings(source, path)]
            + [(ln, k, "sheets") for ln, k in sheets_findings(source, path)])


def scan(paths):
    """{path: findings} over the given files, skipping tests and the allowlist."""
    out = {}
    for path in paths:
        base = os.path.basename(path)
        if base.startswith("test_") or path in ALLOW:
            continue
        with open(path, encoding="utf-8") as fh:
            hits = findings(fh.read(), path)
        if hits:
            out[path] = hits
    return out


def default_paths():
    return sorted(glob.glob("lib/**/*.py", recursive=True)
                  + glob.glob("skills/**/*.py", recursive=True))


def main(argv=None):
    paths = list(argv or []) or default_paths()
    if not paths:
        sys.exit("ABORT: no files to scan — run this from the repo root.")
    bad = scan(paths)
    if not bad:
        print("check_non_idempotent_retries: %d file(s) scanned, every "
              "non-replayable gws call is sent once." % len(paths))
        return 0
    print("ERROR: these calls create a new object every time they are sent, "
          "and are sent on a retrying budget:\n")
    for path in sorted(bad):
        for line, what, tier in bad[path]:
            if tier == "drive":
                print("  %s:%d  %s  — add `retries=gws.NO_RETRY`" % (path, line, what))
            else:
                print("  %s:%d  %s  — this file never mentions NO_RETRY; the "
                      "batchUpdate carrying it must pass it" % (path, line, what))
    print("\nA retry re-sends a request that may already have landed, and "
          "nothing downstream detects the duplicate it makes. If a call really "
          "is replayable, say why in ALLOW in this script.")
    return 1


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    sys.exit(main(sys.argv[1:]))
