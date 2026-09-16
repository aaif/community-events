#!/usr/bin/env python3
"""Write the three Pulse drafts to `.pulse-cache/`, 0600, one file per audience.

The Pulse is one gathering pass and three posts, because the three audiences
are not the same people:

  organizers  #local-champs   what to DO — asks, admin, tooling, per-chapter detail
  members     #general        what HAPPENED and what's next — an invitation, not a memo
  public      LinkedIn / X    the outside view — no internal process, no roster

They are deliberately NOT the same text. The organizer post is written as
though the reader has already seen the #general one, so it never repeats it;
the public post assumes no context at all.

Drafts arrive as ONE JSON object on **stdin**, never as arguments: a post is
multi-line prose that would be mangled by shell quoting, and argv is visible in
`ps` and echoed into logs — the same reason `scripts/check_no_secret_args.py`
refuses secrets as flags. Keys are the audiences below; every key is optional,
so a run that only re-words one post rewrites only that file.

    python3 write_drafts.py <<'JSON'
    {"local_champs": "...", "general": "...", "social": "..."}
    JSON

This script only writes files. It never posts to Slack, LinkedIn or X — the
whole skill is draft-only, and the user pastes.

Usage: write_drafts.py [--outdir .pulse-cache]
"""
import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fetch_local_champs import CACHE_DIR_NAME, write_0600_text  # noqa: E402

#: audience key -> (filename, what the draft is for). The filename carries the
#: destination because these are pasted by hand, often days apart, and a file
#: called `draft2.txt` is how the organizer post ends up in #general.
DRAFTS = {
    "local_champs": ("pulse-local-champs.txt",
                     "organizers only (#local-champs) — asks, admin, tooling"),
    "general": ("pulse-general.txt",
                "the whole workspace (#general) — wins and what's next"),
    "social": ("pulse-social.txt",
               "public (LinkedIn / X) — no internal process, no roster"),
}

#: Contact details must never reach a post — the house rule every aaif-*-post
#: skill carries. Checked here rather than trusted to the writer, because these
#: three files exist to be pasted somewhere public and a slip is unrecallable.
#: Deliberately narrow: an address needs a dotted domain (so a bare Slack
#: @handle is not one), and a phone needs 9+ digits of run (so a date, a member
#: count and a Luma URL are not). Dots are allowed as separators so
#: `415.555.0134` is caught like `415-555-0134`, and the match is then required
#: to carry PHONE_MIN_DIGITS actual digits — without that, admitting the dot
#: made `2026.09.15` a "phone number", and a false positive here refuses the
#: whole run and teaches the writer to distrust the check.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().–-]{8,}\d(?![\w/])")


#: A number worth masking has at least this many digits (NANP and E.164 both
#: bottom out here). Dates, member counts and years fall short of it.
PHONE_MIN_DIGITS = 10


def contact_details(text):
    """Every email address or phone-shaped run in `text`, as (kind, match)."""
    phones = [m.group(0).strip() for m in PHONE_RE.finditer(text)
              if sum(c.isdigit() for c in m.group(0)) >= PHONE_MIN_DIGITS]
    return ([("email address", m.group(0)) for m in EMAIL_RE.finditer(text)]
            + [("phone number", p) for p in phones])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default=CACHE_DIR_NAME,
                    help="where the drafts go (must be inside %s/)" % CACHE_DIR_NAME)
    args = ap.parse_args()

    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit("ERROR: no JSON on stdin. Pipe {\"general\": \"...\"} in; "
                 "see this file's docstring.")
    try:
        drafts = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit("ERROR: stdin is not valid JSON (%s)" % exc)
    if not isinstance(drafts, dict):
        sys.exit("ERROR: expected a JSON object keyed by audience, got %s"
                 % type(drafts).__name__)

    unknown = sorted(set(drafts) - set(DRAFTS))
    if unknown:
        # Never silently drop a draft someone wrote: a typo'd key would look
        # like a successful run that quietly published two posts instead of three.
        sys.exit("ERROR: unknown audience key(s) %s. Known: %s"
                 % (", ".join(unknown), ", ".join(sorted(DRAFTS))))

    # Check every draft BEFORE writing any of them. Validating and writing in
    # one pass left the drafts that sorted earlier on disk when a later one was
    # refused, and a half-written set is exactly how the unchecked file gets
    # pasted — the run must leave nothing rather than leave some of it.
    for key in sorted(drafts):
        text = drafts[key]
        if not isinstance(text, str) or not text.strip():
            sys.exit("ERROR: draft %r is empty — omit the key instead, or the "
                     "previous draft is replaced with nothing." % key)
        found = contact_details(text)
        if found:
            sys.exit("ERROR: draft %r contains contact details that must never "
                     "go in a post:\n  %s\nNothing was written. Remove them "
                     "and re-run."
                     % (key, "\n  ".join("%s: %s" % kv for kv in found)))

    for key in sorted(drafts):
        text = drafts[key]
        name, purpose = DRAFTS[key]
        path = os.path.join(args.outdir, name)
        write_0600_text(path, text.rstrip("\n") + "\n")
        print("wrote %s (%d words) — %s" % (path, len(text.split()), purpose),
              file=sys.stderr)
    print("0600, gitignored. Paste them, then delete: rm -rf %s" % CACHE_DIR_NAME,
          file=sys.stderr)


if __name__ == "__main__":
    main()
