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

Apply a reviewed suggestion with `--apply FILE --write`, a JSON list of
`{"row": N, "slack_id": "U…"}` — the same review-then-write shape `clean.py`
uses.

Usage:
    python3 resolve_slack_ids.py                  # report, changes nothing
    python3 resolve_slack_ids.py --suggest        # + name-match candidates
    python3 resolve_slack_ids.py --write          # fill ids resolved by email
    python3 resolve_slack_ids.py --apply ids.json --write  # reviewed suggestions
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from aaif_events import jsoncache  # noqa: E402
from aaif_events.slack import (Slack, SlackError, gmail_variants,  # noqa: E402
                               load_token, lookup_emails, users)
# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks them in every printed line.
# The flag and the helpers it governs come from ONE module on purpose: a helper
# that reads a different module's flag is a helper this `--redact` does not
# actually govern, which is how an address once reached a public CI log.
from aaif_events import gws as gwsmod  # noqa: E402
from aaif_events.sheets import col_letter  # noqa: E402
from aaif_events.redact import (add_redact_flag, redact_email, redact_id, redact_name,  # noqa: E402
                                set_redaction)


SHEET_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
SOURCE = "Form Responses"
H_EMAIL, H_NAME = "Email", "Full name"
#: The columns this script owns, created at the right-hand end if absent.
#: `Slack ID` is the durable key; `Slack Email` is the address the account
#: actually carries, which is frequently NOT the one on the intake row — that
#: difference is the thing worth being able to see, so it is recorded rather
#: than reconciled away. Neither is ever written back over the intake `Email`:
#: that column is what the person told us, and Drive grants are keyed to it.
H_SLACK_ID = "Slack ID"
H_SLACK_EMAIL = "Slack Email"

#: Slack ids are `U`/`W` + uppercase alphanumerics. Validated before every write
#: because `--apply` takes a hand-edited file, and a mistyped id is not inert:
#: it is the key the invite and audit paths will later act on.
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")

CACHE_PATH = os.path.join(".slack-audit-cache", "users.json")


# ---------- gws ----------
def gws(args):
    """Run a prepared `gws` argument list and parse whatever JSON comes back.

    A thin boundary over `aaif_events.gws.run`, keeping two behaviours this
    script relies on: it exits with a sentence rather than a traceback, and an
    empty body is `{}` rather than an error, because a values `update` answers
    with nothing useful.

    This used to be a bare `subprocess.run` with no retry handling, so a single
    intermittent 503 — which the sync engines have always ridden out — failed
    the whole run. It now retries on the shared table.
    """
    try:
        txt = gwsmod.clean_stdout(gwsmod.run(["gws"] + args))
    except gwsmod.GwsError as exc:
        sys.exit(str(exc))
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def colletter(n):
    """1-based column number -> A1 letter.

    The shared helper is 0-based (it is fed header-row indexes); this script's
    call sites count from 1, so the offset is applied here rather than at four
    call site. A third spelling of the same conversion is how they drift.
    """
    return col_letter(n - 1)


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


def ensure_columns(hdr, names, create=True):
    """{name: column index}, creating any header cell that is not there yet.

    `create=False` returns where each column WOULD go without touching the
    sheet. A report run has to know the indexes to read current values, and
    creating headers as a side effect of reporting breaks the promise every
    engine in this estate makes — that nothing changes without a write flag.

    Indexes are assigned against a GROWING header list so two new columns never
    land on the same letter — the bug you get from computing every index off the
    original `len(hdr)`.
    """
    out, width = {}, len(hdr)
    for name in names:
        if name in hdr:
            out[name] = hdr.index(name)
            continue
        out[name] = width
        width += 1
        if not create:
            continue
        gws(["sheets", "spreadsheets", "values", "update", "--params",
             json.dumps({"spreadsheetId": SHEET_ID,
                         "range": "%s!%s1" % (SOURCE, colletter(out[name] + 1)),
                         "valueInputOption": "RAW"}),
             "--json", json.dumps({"values": [[name]]}), "--format", "json"])
        print("Created column %r at %s." % (name, colletter(out[name] + 1)))
    return out


def write_cells(ci, pairs):
    """pairs = [(row_number, value)]. RAW: these are inert text, never formulas."""
    data = [{"range": "%s!%s%d" % (SOURCE, colletter(ci + 1), rn), "values": [[v]]}
            for rn, v in pairs]
    if not data:
        return
    gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": SHEET_ID}), "--json",
         json.dumps({"valueInputOption": "RAW", "data": data}), "--format", "json"])


# ---------- the column as a source for OTHER engines ----------
def canon(email):
    """The spelling Gmail folds to, for matching a sheet row to a lookup key."""
    v = gmail_variants(email)
    return v[-1] if v else ""


def known_ids():
    """{canonical email: {"id", "slack_email"}} from the `Slack ID` column.

    This is the reviewed answer to "who is this person on Slack", including the
    ones no email lookup can reach because they joined under an address the
    intake has never seen. Engines that resolve people to Slack should consult
    it rather than re-deriving a miss they cannot fix.
    """
    hdr, rows = read_source()
    if H_SLACK_ID not in hdr:
        return {}
    ie, ci = hdr.index(H_EMAIL), hdr.index(H_SLACK_ID)
    cem = hdr.index(H_SLACK_EMAIL) if H_SLACK_EMAIL in hdr else None
    out, malformed, disagreeing = {}, [], []
    for n, r in enumerate(rows, start=2):   # sheet row numbers, header is row 1
        def at(i):
            return (r[i] or "").strip() if i is not None and i < len(r) else ""
        email, sid = at(ie), at(ci)
        if not email:
            continue
        if not sid:
            continue
        if not SLACK_ID_RE.match(sid):
            # A filled cell that is not an id — a lowercase paste, an @handle,
            # a channel id, a truncated copy. `--apply` aborts loudly on exactly
            # this, for the reason its own comment gives: a mistyped id is not
            # inert, it is the key the invite and audit paths later act on. The
            # cell reaching here got past that gate (it can be typed straight
            # into the sheet), and dropping it silently is worse than useless:
            # a human did the review, the cell LOOKS answered, and the person is
            # reported unresolvable forever with nothing pointing at the cause.
            malformed.append((n, sid))
            continue
        key = canon(email)
        prior = out.get(key)
        if prior and prior["id"] != sid:
            # Two rows for one person (the form is submitted repeatedly, and
            # canon() folds Gmail spellings) carrying DIFFERENT reviewed ids.
            # Last-write-wins would pick by sheet order and hand that id to a
            # private-channel invite. There is no answer here worth guessing.
            disagreeing.append((key, prior["id"], sid))
            out[key] = None
            continue
        if key in out and out[key] is None:
            continue
        out[key] = {"id": sid, "slack_email": at(cem)}
    if malformed:
        print("  WARNING: %d %r cell(s) are filled but not shaped like a Slack "
              "id and were IGNORED — those people read as unresolved: %s"
              % (len(malformed), H_SLACK_ID,
                 ", ".join("row %d=%r" % m for m in malformed[:5])),
              file=sys.stderr)
    if disagreeing:
        print("  WARNING: %d person(s) have two reviewed rows naming DIFFERENT "
              "ids; both are ignored until a human picks one (row pairs: %s)"
              % (len(disagreeing),
                 ", ".join("%s/%s" % (a, b) for _, a, b in disagreeing[:5])),
              file=sys.stderr)
    return {k: v for k, v in out.items() if v is not None}


#: `users.info` errors that really mean "there is no such account" — an answer
#: about the id, not about the call. Everything else (missing_scope,
#: invalid_auth, ratelimited, a 5xx) means we FAILED TO ASK, and must never be
#: recorded as a fact about a person.
BENIGN_INFO_MISSES = ("user_not_found",)


def hydrate(api, ids):
    """{id: {"name", "real_name", "email"}} via users.info — the column stores no handle.

    An id is the durable key precisely BECAUSE the handle is not; so the handle
    has to be fetched fresh at the moment it is displayed, never cached into the
    sheet where it would rot into a wrong @mention.

    Ids that resolve to no account, or to a deactivated one, are simply absent
    from the result — so the output can be SMALLER than the input, which is why
    `overlay_known`'s `filled` counter differs from the number of gaps it tried.

    Raises rather than returning short when a lookup fails for any reason other
    than absence. `missing_scope` or `invalid_auth` would otherwise drop every
    id on the floor, and each dropped id is a person the report then states has
    no Slack account — the same failure `lookup_emails` refuses one tier up, and
    the same one that once made a missing scope into the audit's headline.
    """
    out, failures = {}, []
    for uid in sorted(set(ids)):
        payload = api.call("users.info", user=uid)
        if not payload.get("ok"):
            error = payload.get("error", "unknown")
            if error not in BENIGN_INFO_MISSES:
                failures.append(error)
            continue
        u = payload.get("user") or {}
        if u.get("deleted"):
            # A deactivated account is not a usable identity: inviting it fails
            # and printing its handle tells a reader the person is reachable.
            continue
        out[uid] = {"name": u.get("name", ""),
                    "real_name": u.get("real_name") or "",
                    "email": (u.get("profile") or {}).get("email", "")}
    if failures:
        raise SlackError(
            "users.info", "hydrate_failed",
            "%d of %d reviewed id(s) could not be resolved for API reasons "
            "rather than absence (%s). Refusing to report these people as "
            "having no Slack account."
            % (len(failures), len(set(ids)), ", ".join(sorted(set(failures)))))
    return out


def overlay_known(api, resolved, known=None):
    """Fill `resolved`'s misses from the `Slack ID` column. Returns (n, conflicts).

    Only ever fills a MISS. A live lookup that hit is a hard fact — this address
    really is on that account — and must outrank a sheet cell, which may predate
    someone changing their Slack address. Where the two disagree the row is
    reported as a conflict rather than silently resolved either way.
    """
    known = known_ids() if known is None else known
    gaps = {e: known[canon(e)] for e, got in resolved.items()
            if not got.get("id") and canon(e) in known}
    conflicts = [(e, got["id"], known[canon(e)]["id"])
                 for e, got in resolved.items()
                 if got.get("id") and canon(e) in known
                 and known[canon(e)]["id"] != got["id"]]
    if not gaps:
        return 0, conflicts
    info = hydrate(api, [g["id"] for g in gaps.values()])
    filled = 0
    for email, rec in gaps.items():
        u = info.get(rec["id"])
        if not u:
            continue
        resolved[email] = {"id": rec["id"], "name": u["name"],
                           "real_name": u["real_name"], "deleted": False,
                           "matched_email": rec["slack_email"] or u["email"],
                           "from_column": True}
        filled += 1
    return filled, conflicts


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
def collect(hdr, rows, ci, cem=None):
    """(row, name, email, current_id, current_slack_email) per row with an email.

    Both owned columns are reported because a row is only DONE when both are
    filled: the 175 rows written before `Slack Email` existed carry an id and a
    blank email, and keying "to do" off the id alone would skip them forever.
    """
    ie, iname = hdr.index(H_EMAIL), hdr.index(H_NAME)
    out = []
    for n, r in enumerate(rows, 2):
        email = (r[ie] or "").strip()
        if not email:
            continue
        def at(i):
            return (r[i] or "").strip() if i is not None and i < len(r) else ""
        out.append((n, (r[iname] or "").strip(), email, at(ci), at(cem)))
    return out


def run(write=False, want_suggest=False, apply_path=None):
    hdr, rows = read_source()
    cols = ensure_columns(hdr, (H_SLACK_ID, H_SLACK_EMAIL),
                          create=bool(write or apply_path))
    ci, cem = cols[H_SLACK_ID], cols[H_SLACK_EMAIL]
    width = max(ci, cem) + 1
    rows = [r + [""] * (width - len(r)) for r in rows]
    records = collect(hdr, rows, ci, cem)

    if apply_path:
        return apply_reviewed(apply_path, ci, cem, {r[0] for r in records})

    todo = [r for r in records if not (r[3] and r[4])]
    print("%d row(s) with an email; %d already carry both %s and %s; "
          "%d to resolve.\n"
          % (len(records), len(records) - len(todo), H_SLACK_ID, H_SLACK_EMAIL,
             len(todo)))
    if not todo:
        return

    api = Slack(load_token())
    who = api.ok("auth.test")
    print("workspace: %s (%s)\n" % (who.get("team"), who.get("team_id")))
    # One lookup per DISTINCT address: a person who submitted the form three
    # times is one account, and the row->email map fans the answer back out.
    resolved = lookup_emails(api, [rec[2] for rec in todo])

    hits, slack_emails, misses, folded = [], [], [], []
    for rn, name, email, _, _ in todo:
        got = resolved.get(email) or {}
        if got.get("id"):
            hits.append((rn, got["id"]))
            matched = got.get("matched_email") or email
            slack_emails.append((rn, matched))
            if matched.lower() != email.lower():
                folded.append((name, email, matched))
        else:
            misses.append((rn, name, email))

    print("Resolved by email: %d    Unresolved: %d" % (len(hits), len(misses)))
    if folded:
        print("\nMatched only after Gmail-canonicalizing the address — the intake "
              "spelling is the one to fix at the source:")
        for name, was, now in folded:
            print("   %-26s %-34s -> %s"
                  % (redact_name(name)[:26], redact_email(was),
                     redact_email(now)))

    if misses:
        print("\nNo account at the address on file (NOT proof they have no Slack — "
              "they may have joined under another address):")
        for rn, name, email in misses[:40]:
            print("   row %-5s %-26s %s"
                  % (rn, redact_name(name)[:26], redact_email(email)))
        if len(misses) > 40:
            print("   … and %d more" % (len(misses) - 40))

    if want_suggest and misses:
        cands = suggest(misses, directory(api, who.get("team_id")))
        print("\nName-match candidates — SUGGESTIONS, never written automatically. "
              "Confirm each is the same human, then feed the ids to --apply:")
        byrow = {rn: (name, email) for rn, name, email in misses}
        for rn in sorted(cands):
            name, email = byrow[rn]
            print("   row %-5s %-26s %s"
                  % (rn, redact_name(name)[:26], redact_email(email)))
            for u in cands[rn][:4]:
                print("        %-12s @%-24s %-26s %s"
                      % (redact_id(u["id"]), u["name"],
                         redact_name(u["real_name"])[:26],
                         redact_email(u["email"]) or "(no email visible)"))

    if not write:
        print("\nReport only — nothing was written. Re-run with --write to fill "
              "the %d id(s) resolved by email." % len(hits))
        return

    bad = [(rn, sid) for rn, sid in hits if not SLACK_ID_RE.match(sid)]
    if bad:
        sys.exit("ABORT: %d id(s) from the API are not shaped like Slack ids (%r). "
                 "Nothing written." % (len(bad), bad[:3]))
    write_cells(ci, hits)
    write_cells(cem, slack_emails)
    print("\nWrote %d %s and %d %s value(s)."
          % (len(hits), H_SLACK_ID, len(slack_emails), H_SLACK_EMAIL))


def apply_reviewed(path, ci, cem, known_rows):
    """Write a reviewed [{row, slack_id, slack_email}] list, validating first.

    `slack_email` is optional but strongly wanted: a row carrying an id and a
    blank email is never "done" by run()'s reckoning, so it would be looked up
    again — and miss again — on every future run, permanently.
    """
    with open(path) as fh:
        wanted = json.load(fh)
    pairs, emails, bad = [], [], []
    for ch in wanted:
        rn, sid = ch.get("row"), (ch.get("slack_id") or "").strip()
        sem = (ch.get("slack_email") or "").strip()
        if not isinstance(rn, int) or rn not in known_rows:
            bad.append("row %r is not a data row carrying an email" % (rn,))
            continue
        if not SLACK_ID_RE.match(sid):
            bad.append("row %s: %r is not a Slack id" % (rn, sid))
            continue
        if sem and "@" not in sem:
            bad.append("row %s: %r is not an email address" % (rn, sem))
            continue
        pairs.append((rn, sid))
        if sem:
            emails.append((rn, sem))
    if bad:
        # All-or-nothing: a half-applied review leaves nobody able to say which
        # half, and these ids gate access to private channels.
        sys.exit("ABORT: %d invalid entr(ies); nothing written.\n   %s"
                 % (len(bad), "\n   ".join(bad[:10])))
    write_cells(ci, pairs)
    write_cells(cem, emails)
    print("Applied %d reviewed %s and %d %s value(s)."
          % (len(pairs), H_SLACK_ID, len(emails), H_SLACK_EMAIL))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="fill ids resolved by email (default: report only)")
    ap.add_argument("--suggest", action="store_true",
                    help="also list name-match candidates for the unresolved")
    ap.add_argument("--apply", metavar="FILE",
                    help="write a reviewed [{row, slack_id}] JSON list "
                         "(needs --write, like every other write here)")
    add_redact_flag(ap, masks="emails (a***@***.tld), names (first initial) and Slack ids")
    a = ap.parse_args()
    set_redaction(a.redact)
    # `--apply` used to write on its own. Every other script in this skill, and
    # the SKILL.md that documents them, treats `--write` as the single answer to
    # "does this invocation touch the sheet?" — a second, quieter write path
    # means that question cannot be answered by reading the command line.
    if a.apply and not a.write:
        sys.exit("REFUSING: --apply writes to the sheet, so it needs --write too.\n"
                 "  python3 resolve_slack_ids.py --apply ids.json --write")
    run(write=a.write, want_suggest=a.suggest, apply_path=a.apply)


if __name__ == "__main__":
    main()
