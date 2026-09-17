#!/usr/bin/env python3
"""Fail if a skill script defines its own redaction flag or helpers.

`--redact` masks the names and addresses an ops report prints, and it defaults
ON under CI because a workflow log on a public repo is world-readable forever.
It only works when the flag and the helpers that read it are the SAME module's:
an earlier refactor moved `redact_email` into a sibling script and left `REDACT`
and `set_redaction` behind, so the flag was set in one module and read in
another, `--redact` silently governed nothing, and a real address reached a
public log.

The repo's answer was ten near-identical private copies, one per script. That
traded a silent failure for a drift surface — and it drifted: one copy lost its
address masker entirely. `lib/aaif_events/redact.py` is now the single module, and this
check keeps it single, because both failure modes are invisible in output.
Masked text and text that was never sensitive look exactly alike.

Walks the AST rather than grepping, so a name inside a docstring or a comment
about the old design does not trip it.

Usage:  python3 scripts/check_no_local_redaction.py [FILE...]
        (no args = every *.py under lib/ and skills/)
"""
import ast
import glob
import os
import sys

#: The module that is allowed to define these. Everything else imports from it.
CANONICAL = os.path.join("lib", "aaif_events", "redact.py")

#: Module-level names that must come from `aaif_events.redact`. `REDACT` and
#: `CI_REDACT_DEFAULT` are the flag itself; `set_redaction` writes it;
#: `add_redact_flag` builds the CLI option that feeds it; `redact_*` reads it.
RESERVED = frozenset({"REDACT", "CI_REDACT_DEFAULT", "set_redaction",
                      "add_redact_flag", "redacting"})

#: Names that may NOT be imported by value, only reached as an attribute
#: (`redact.REDACT`). `from aaif_events.redact import REDACT` binds a *copy* of
#: the flag at import time: `set_redaction()` rebinds it in the library and the
#: consumer's name stays `False` forever, while the helpers it imported
#: alongside keep masking. Half-masked output is the original bug wearing an
#: import statement, and it looks exactly like output that had nothing to mask.
NO_VALUE_IMPORT = frozenset({"REDACT", "CI_REDACT_DEFAULT"})

#: The module those names live in.
MODULE = "aaif_events.redact"


def is_reserved(name):
    return name in RESERVED or name.startswith("redact_")


def offenders(path):
    """Module-level definitions of a reserved name, as (lineno, name, kind)."""
    if os.path.normpath(path).endswith(os.path.normpath(CANONICAL)):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            tree = ast.parse(fh.read(), filename=path)
        except SyntaxError as exc:
            print("%s: cannot parse (%s)" % (path, exc), file=sys.stderr)
            return [(getattr(exc, "lineno", 0) or 0, "<syntax error>", "parse")]
    found = []
    for node in tree.body:               # module level only
        if isinstance(node, ast.ImportFrom) and node.module == MODULE:
            for alias in node.names:
                if alias.name in NO_VALUE_IMPORT:
                    found.append((node.lineno, alias.name, "value-import"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_reserved(node.name):
                found.append((node.lineno, node.name, "def"))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and is_reserved(t.id):
                    found.append((node.lineno, t.id, "assignment"))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and is_reserved(node.target.id):
                found.append((node.lineno, node.target.id, "assignment"))
    return found


def main(argv):
    paths = argv[1:] or sorted(
        glob.glob("lib/**/*.py", recursive=True)
        + glob.glob("skills/**/*.py", recursive=True))
    if not argv[1:] and not paths:
        # The globs are relative. Run from anywhere but the repo root and they
        # match nothing, and a guard that examined zero files would otherwise
        # exit 0 — silently switching itself off.
        print("ABORT: no files under lib/ or skills/ — check_no_local_redaction "
              "must run from the repo root.", file=sys.stderr)
        return 1
    bad = 0
    for p in paths:
        for lineno, name, kind in offenders(p):
            bad += 1
            if kind == "value-import":
                print("%s:%d: `from %s import %s` binds a COPY of the flag — "
                      "reach it as an attribute (`redact.%s`) or via "
                      "`redacting()`" % (p, lineno, MODULE, name, name))
            else:
                print("%s:%d: local %s of `%s` — import it from "
                      "`%s` instead" % (p, lineno, kind, name, MODULE))
    if not bad:
        # Say the count: "0 file(s) scanned" is the only visible symptom of a
        # guard that has quietly stopped looking at anything.
        print("check_no_local_redaction: %d file(s) scanned, no local "
              "definitions." % len(paths))
    if bad:
        print("\n%d local redaction definition(s). The flag and the helpers it\n"
              "governs must be one module, or `--redact` masks nothing while\n"
              "still printing 'redaction ON'. See lib/aaif_events/redact.py."
              % bad, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    sys.exit(main(sys.argv))
