#!/usr/bin/env python3
"""Fail if any script accepts a token, key, or secret as a command-line flag.

argv is public: it shows in `ps`, in shell history, in CI logs, and an agent
running the script echoes the full command line to the console. Secrets come
in through environment variables (or the gitignored `.env` / the keychain)
only — see AGENTS.md. This check catches the easy way of getting that wrong:
a `--token` or `--api-key` style argparse flag.

Usage:  python3 scripts/check_no_secret_args.py [FILE...]
        (no args = every *.py under lib/, skills/, scripts/)
"""
import glob, re, sys

FLAG_RE = re.compile(
    r"""add_argument\(\s*['"]--?[\w-]*(token|api[_-]?key|secret|passw(or)?d|credential)""",
    re.IGNORECASE)


def offenders(path):
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if FLAG_RE.search(line):
                yield n, line.strip()


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
