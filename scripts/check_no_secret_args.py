#!/usr/bin/env python3
"""Fail if any script accepts a token, key, or secret as a command-line flag.

argv is public: it shows in `ps`, in shell history, in CI logs, and an agent
running the script echoes the full command line to the console. Secrets come
in through environment variables (or the gitignored `.env` / the keychain)
only — see AGENTS.md. This check catches the easy way of getting that wrong:
a `--token` or `--api-key` style argparse flag.

Walks the AST rather than grepping, so multi-line `add_argument(` calls, a
short flag listed first (`-t, --token`), and `dest="token"` are all seen. A
flag is flagged when, split on `-`/`_`, it contains one of SECRET_WORDS as a
whole word: `--token`, `--api-key` and `dest="token"` fail; `--tokenize` and
`--keyboard` pass. A compound like `--secret-sauce-mode` is flagged too — a
whole-word hit is a hit, so rename the flag rather than special-case it.

Usage:  python3 scripts/check_no_secret_args.py [FILE...]
        (no args = every *.py under lib/, skills/, scripts/)
"""
import ast, glob, re, sys

SECRET_WORDS = frozenset({
    "token", "key", "apikey", "api-key", "secret", "password", "passwd",
    "credential", "credentials", "auth", "bearer", "xoxb", "xoxp",
})


def is_secret_flag(name):
    """True when a flag/dest name carries a secret word as a whole word."""
    words = [w for w in re.split(r"[-_]", name.strip("-").lower()) if w]
    return any(w in SECRET_WORDS for w in words)


def _string_args(call):
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            yield a.value
    for kw in call.keywords:
        if kw.arg == "dest" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            yield kw.value.value


def offenders(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        yield e.lineno or 0, f"(unparsable: {e.msg})"
        return
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        hit = [s for s in _string_args(node) if is_secret_flag(s)]
        if hit:
            yield node.lineno, lines[node.lineno - 1].strip()


def main(argv):
    paths = argv[1:] or sorted(
        p for pat in ("lib/**/*.py", "skills/**/*.py", "scripts/*.py")
        for p in glob.glob(pat, recursive=True))
    bad = [(p, n, l) for p in paths for n, l in offenders(p)]
    for p, n, l in bad:
        print("%s:%d: secret accepted as a CLI flag — read it from an env var instead: %s"
              % (p, n, l))
    if bad:
        return 1
    print("OK: no script takes a token/key/secret on the command line (%d files)" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
