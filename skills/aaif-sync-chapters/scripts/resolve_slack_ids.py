#!/usr/bin/env python3
"""Resolve each intake person to their Slack account id, on `Form Responses`.

## Why an id and not a handle

A handle (`@someone`) is a display name its owner can change at any moment. Key
an invite or an audit off one and it breaks silently the day they rename
themselves — and "silently" here means an organizer quietly stops being invited
to their own chapter's room. The account **id** (`U0B…`) is immutable for the
life of the account, so that is what this writes. `invite_organizers.py` makes
the same argument from the other direction and it stays true here.

## Why on Form Responses and not the role tabs

`Organizers`/`Hosts`/`Speakers` are computed: their C–O block is spill output
from one `LET` over this tab, and writing a literal anywhere inside a spill
range collapses the whole column to `#REF!`. `Form Responses` is the source, it
already carries two script-maintained literal columns (`Autofixes`,
`Extracted City`), and a value here flows outward instead of having to be
written three times and kept in sync. Columns are appended to the RIGHT of the
form's own, which Google Forms never disturbs.

## What it will and will not decide on its own

`--write` fills `Slack ID` **only** from an email lookup — the address the
person gave us resolving to an account. That is the one link strong enough to
act on unattended.

Everyone else is reported, never written:

  * a **name match** against the workspace directory is a suggestion for a
    human, because two people genuinely share a name and writing the wrong id
    here would put a stranger in a private organizers channel; and
  * a **miss** may just mean they joined Slack under an address the intake has
    never seen, which is not the same fact as "has no Slack".

Apply a reviewed suggestion with `--apply FILE`, a JSON list of
`{"row": N, "slack_id": "U…"}` — the same review-then-write shape `clean.py`
uses.

Usage:
    python3 resolve_slack_ids.py                  # report, changes nothing
    python3 resolve_slack_ids.py --suggest        # + name-match candidates
    python3 resolve_slack_ids.py --write          # fill ids resolved by email
    python3 resolve_slack_ids.py --apply ids.json # write reviewed suggestions
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events.slack import (Slack, load_token,  # noqa: E402
                               lookup_emails, scrubbed_env, users)

SHEET_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
SOURCE = "Form Responses"
H_EMAIL, H_NAME = "Email", "Full name"
#: The column this script owns. Created at the right-hand end if absent.
H_SLACK_ID = "Slack ID"

#: Slack ids are `U`/`W` + uppercase alphanumerics. Validated before every write
#: because `--apply` takes a hand-edited file, and a mistyped id is not inert:
#: it is the key the invite and audit paths will later act on.
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")

CACHE_PATH = os.path.join(".slack-audit-cache", "users.json")


# ---------- gws ----------
def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True,
                         env=scrubbed_env())
    if out.returncode != 0:
        sys.exit("gws error: %s...\n%s" % (" ".join(args[:4]), out.stderr.strip()[:400]))
    txt = out.stdout
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def colletter(n):  # 1-based -> A1 letter
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_source():
    """Whole tab, unbounded. A hardcoded window truncates the newest column
    first — which is exactly the one this script owns."""
    d = gws(["sheets", "spreadsheets", "values", "get", "--params",
             json.dumps({"spreadsheetId": SHEET_ID, "range": SOURCE,
                         "majorDimension": "ROWS"}), "--format", "json"])
    vals = d.get("values", [])
    if not vals:
        sys.exit("ABORT: %r came back empty." % SOURCE)
    hdr = [h.strip() for h in vals[0]]
    rows = [r + [""] * (len(hdr) - len(r)) for r in vals[1:]]
    return hdr, rows


def ensure_column(hdr, create=True):
    """Index of H_SLACK_ID, creating the header cell if it is not there yet.

    `create=False` returns where the column WOULD go without touching the sheet.
    A report run has to know the index to read current values, and creating the
    header as a side effect of reporting breaks the promise every engine in this
    estate makes — that nothing changes without an explicit write flag.
    """
    if H_SLACK_ID in hdr:
        return hdr.index(H_SLACK_ID)
    ci = len(hdr)
    if not create:
        return ci
    gws(["sheets", "spreadsheets", "values", "update", "--params",
         json.dumps({"spreadsheetId": SHEET_ID,
                     "range": "%s!%s1" % (SOURCE, colletter(ci + 1)),
                     "valueInputOption": "RAW"}),
         "--json", json.dumps({"values": [[H_SLACK_ID]]}), "--format", "json"])
    print("Created column %r at %s." % (H_SLACK_ID, colletter(ci + 1)))
    return ci


def write_ids(ci, pairs):
    """pairs = [(row_number, slack_id)]. RAW: an id is inert text, never a formula."""
    data = [{"range": "%s!%s%d" % (SOURCE, colletter(ci + 1), rn), "values": [[sid]]}
            for rn, sid in pairs]
    if not data:
        return
    gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": SHEET_ID}), "--json",
         json.dumps({"valueInputOption": "RAW", "data": data}), "--format", "json"])


# ---------- name matching (suggestions only) ----------
TITLES = {"dr", "mr", "mrs", "ms", "prof", "phd"}


def norm_tokens(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c)).lower()
    return [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in TITLES]


def name_key(s):
    return " ".join(sorted(norm_tokens(s)))


def directory(api, team_id):
    """The workspace directory, reusing the shared audit cache when it is there."""
    cached = jsoncache.read(CACHE_PATH, team_id=team_id, note=print)
    if cached is not None:
        print("  directory: cache %s" % jsoncache.age(CACHE_PATH))
        return cached
    print("  directory: pulling from Slack (slow; cached for next time)")
    got = users(api)
    jsoncache.write(CACHE_PATH, got, team_id)
    return got


def suggest(unresolved, people):
    """Name-key candidates for rows an email lookup could not resolve.

    Returns {row: [user, ...]}. Deliberately only the FULL-name key: a surname
    or a handle coincidence produced a confident, wrong pairing in testing
    (an unrelated account sharing one distinctive token), and a suggestion a
    reviewer has to disprove is worse than no suggestion at all.
    """
    live = [u for u in people
            if not u["deleted"] and not u["is_bot"] and not u["is_app_user"]]
    by_key = defaultdict(list)
    for u in live:
        # real_name ONLY. Indexing the @handle too looks like it widens the net
        # for free, but a handle is punctuation away from any name — "ada.lovelace"
        # normalizes to the same key as "Ada Lovelace" and pairs the row with
        # whoever happens to hold that handle. That is a name COINCIDENCE wearing
        # a full-name match's confidence, and test_resolve_slack_ids pins it.
        k = name_key(u.get("real_name", ""))
        if k and len(k.split()) >= 2:
            by_key[k].append(u)
    out = {}
    for rn, name, _email in unresolved:
        seen, cands = set(), []
        for u in by_key.get(name_key(name), []):
            if u["id"] not in seen:
                seen.add(u["id"])
                cands.append(u)
        if cands:
            out[rn] = cands
    return out


# ---------- run ----------
def collect(hdr, rows, ci):
    """(row, name, email, current_id) for every row carrying an email."""
    ie, iname = hdr.index(H_EMAIL), hdr.index(H_NAME)
    out = []
    for n, r in enumerate(rows, 2):
        email = (r[ie] or "").strip()
        if not email:
            continue
        out.append((n, (r[iname] or "").strip(), email,
                    (r[ci] or "").strip() if ci < len(r) else ""))
    return out


def run(write=False, want_suggest=False, apply_path=None):
    hdr, rows = read_source()
    ci = ensure_column(hdr, create=bool(write or apply_path))
    if ci >= len(hdr):          # freshly created: widen the in-memory rows
        rows = [r + [""] * (ci + 1 - len(r)) for r in rows]
    records = collect(hdr, rows, ci)

    if apply_path:
        return apply_reviewed(apply_path, ci, {r[0] for r in records})

    todo = [r for r in records if not r[3]]
    print("%d row(s) with an email; %d already carry a %s; %d to resolve.\n"
          % (len(records), len(records) - len(todo), H_SLACK_ID, len(todo)))
    if not todo:
        return

    api = Slack(load_token())
    who = api.ok("auth.test")
    print("workspace: %s (%s)\n" % (who.get("team"), who.get("team_id")))
    # One lookup per DISTINCT address: a person who submitted the form three
    # times is one account, and the row->email map fans the answer back out.
    resolved = lookup_emails(api, [e for _, _, e, _ in todo])

    hits, misses, folded = [], [], []
    for rn, name, email, _ in todo:
        got = resolved.get(email) or {}
        if got.get("id"):
            hits.append((rn, got["id"]))
            if got.get("matched_email", email).lower() != email.lower():
                folded.append((name, email, got["matched_email"]))
        else:
            misses.append((rn, name, email))

    print("Resolved by email: %d    Unresolved: %d" % (len(hits), len(misses)))
    if folded:
        print("\nMatched only after Gmail-canonicalizing the address — the intake "
              "spelling is the one to fix at the source:")
        for name, was, now in folded:
            print("   %-26s %-34s -> %s" % (name[:26], was, now))

    if misses:
        print("\nNo account at the address on file (NOT proof they have no Slack — "
              "they may have joined under another address):")
        for rn, name, email in misses[:40]:
            print("   row %-5s %-26s %s" % (rn, name[:26], email))
        if len(misses) > 40:
            print("   … and %d more" % (len(misses) - 40))

    if want_suggest and misses:
        cands = suggest(misses, directory(api, who.get("team_id")))
        print("\nName-match candidates — SUGGESTIONS, never written automatically. "
              "Confirm each is the same human, then feed the ids to --apply:")
        byrow = {rn: (name, email) for rn, name, email in misses}
        for rn in sorted(cands):
            name, email = byrow[rn]
            print("   row %-5s %-26s %s" % (rn, name[:26], email))
            for u in cands[rn][:4]:
                print("        %-12s @%-24s %-26s %s"
                      % (u["id"], u["name"], u["real_name"][:26],
                         u["email"] or "(no email visible)"))

    if not write:
        print("\nReport only — nothing was written. Re-run with --write to fill "
              "the %d id(s) resolved by email." % len(hits))
        return

    bad = [(rn, sid) for rn, sid in hits if not SLACK_ID_RE.match(sid)]
    if bad:
        sys.exit("ABORT: %d id(s) from the API are not shaped like Slack ids (%r). "
                 "Nothing written." % (len(bad), bad[:3]))
    write_ids(ci, hits)
    print("\nWrote %d %s value(s)." % (len(hits), H_SLACK_ID))


def apply_reviewed(path, ci, known_rows):
    """Write a reviewed [{row, slack_id}] list, validating every entry first."""
    with open(path) as fh:
        wanted = json.load(fh)
    pairs, bad = [], []
    for ch in wanted:
        rn, sid = ch.get("row"), (ch.get("slack_id") or "").strip()
        if not isinstance(rn, int) or rn not in known_rows:
            bad.append("row %r is not a data row carrying an email" % (rn,))
        elif not SLACK_ID_RE.match(sid):
            bad.append("row %s: %r is not a Slack id" % (rn, sid))
        else:
            pairs.append((rn, sid))
    if bad:
        # All-or-nothing: a half-applied review leaves nobody able to say which
        # half, and these ids gate access to private channels.
        sys.exit("ABORT: %d invalid entr(ies); nothing written.\n   %s"
                 % (len(bad), "\n   ".join(bad[:10])))
    write_ids(ci, pairs)
    print("Applied %d reviewed %s value(s)." % (len(pairs), H_SLACK_ID))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="fill ids resolved by email (default: report only)")
    ap.add_argument("--suggest", action="store_true",
                    help="also list name-match candidates for the unresolved")
    ap.add_argument("--apply", metavar="FILE",
                    help="write a reviewed [{row, slack_id}] JSON list")
    a = ap.parse_args()
    run(write=a.write, want_suggest=a.suggest, apply_path=a.apply)


if __name__ == "__main__":
    main()
