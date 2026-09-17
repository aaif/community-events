#!/usr/bin/env python3
"""Record which address actually holds each organizer's chapter-folder grant.

The intake `Email` is what the person typed on the form. It is NOT necessarily
the address Drive ended up granting, and the gap is invisible until someone is
locked out and files an access request:

  * Drive stores a CONSUMER Gmail address canonically — grant
    `first.m.last@gmail.com` and the permission comes back `firstmlast@gmail.com`
    — so the spelling on the row and the spelling on the ACL differ routinely;
  * a person can hold a grant under a second address entirely (a work account,
    a personal Gmail they actually sign in with), which the intake never sees.

So this writes a `Drive Email` column on `Form Responses` carrying the address
on the ACL, and leaves the intake `Email` alone. Nothing is reconciled away:
`Email` stays what they told us, `Drive Email` is what Drive really honours, and
the difference between them is the fact worth being able to see. Together with
`Slack Email` (resolve_slack_ids.py) a row shows all three identities a person
has in this estate without any of them overwriting another.

**A `(no grant)` in `Drive Email` on an accepted organizer is the finding.** It
means no direct grant on their chapter folder matches any spelling of their
intake address — they cannot open the folder, and an access request is coming.
A *blank* cell is not the finding and never means one: it is indistinguishable
from "this script has not run for that row yet", which is exactly why the
sentinel is written instead.

**The column is also an INPUT, which is why this script does not own every
cell.** `sync_access.py` grants the address recorded here in preference to the
intake `Email` — that is the supported fix for someone whose intake address has
no Google account behind it, which Drive refuses to share with at all. So a
human writes into this column too, and a hand-entered address that Drive has not
granted yet is reported and **left alone** rather than overwritten with
`(no grant)`: this script derives its values from the ACL, and the window
between recording an address and sync_access acting on it is precisely when the
derived value is wrong. Anything already on the ACL is still written from the
ACL, including a grant made under the recorded address.

Organizers only, deliberately — the same narrowing `sync_access.ACCESS_TABS`
makes. An accepted speaker belongs in a chapter's CRM but has no business with
write access to its Drive folder, so there is no grant to record for them.

Usage:
    python3 track_drive_email.py           # report, changes nothing
    python3 track_drive_email.py --write   # fill the Drive Email column
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

# SOURCE / H_EMAIL / H_DRIVE_EMAIL / NO_GRANT are owned by sync_access, which
# READS this column to decide where a grant goes — the sentinel's exact spelling
# is load-bearing in both scripts, so there is one definition of it.
from sync_access import (H_DRIVE_EMAIL, H_EMAIL, NO_GRANT, SOURCE,  # noqa: E402
                         canon_email, looks_like_address, perms,
                         reviewed_drive_emails)
from sync_chapters import INTAKE_ID  # noqa: E402
from sync_crm import (TEMPLATE_FOLDER, list_chapter_folders,  # noqa: E402
                      match_chapters, merge_people, read_role_tab)
# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks them in every printed line.
# The flag and the helpers it governs come from ONE module on purpose: a helper
# that reads a different module's flag is a helper this `--redact` does not
# actually govern, which is how an address once reached a public CI log.
from aaif_events import gws as gwsmod  # noqa: E402
from aaif_events.sheets import col_letter  # noqa: E402
from aaif_events.redact import add_redact_flag, redact_email, set_redaction  # noqa: E402


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


def grant_by_person():
    """{canonical intake email: (chapter, address on the ACL)} for organizers.

    Reads permissions for every chapter folder an accepted organizer maps to.
    A person is matched to a grant by CANONICAL address, which is what makes the
    Gmail-dot case resolve rather than read as "no grant".

    One entry per PERSON, not per person-and-chapter: someone who organizes two
    chapters keeps whichever folder actually grants them access, because the
    column this fills answers "can this person open a chapter folder at all",
    and reporting them ungranted on the strength of their second chapter would
    be a finding about a person who is not locked out.
    """
    folders = [f for f in list_chapter_folders() if f["name"] != TEMPLATE_FOLDER]
    people, _, _ = read_role_tab("Organizers", {})
    by_folder, orphans, _near = match_chapters(merge_people(people), folders)

    # A grant made to someone's recorded address is still THEIR grant. Matching
    # on the intake spelling alone reported them "(no grant)" the moment
    # sync_access honoured the column — the script writing the finding that its
    # own column had already answered.
    reviewed, _problems = reviewed_drive_emails()
    out, no_folder = {}, {}
    for o in orphans:
        for p in o["people"]:
            no_folder[canon_email(p["email"])] = o["city"]

    for f in folders:
        want = by_folder.get(f["id"], [])
        if not want:
            continue
        held = {}
        for q in perms(f["id"]):
            if q["type"] == "user" and (not q["inherited"] or q["role"] == "owner"):
                held[canon_email(q.get("emailAddress", ""))] = q.get("emailAddress")
        for p in want:
            ce = canon_email(p["email"])
            alt = reviewed.get(ce)
            grant = held.get(ce) or (held.get(canon_email(alt)) if alt else None)
            prior = out.get(ce)
            # One person can organize two chapters. Keyed by person alone, the
            # LAST folder iterated used to win — so someone with a grant on
            # their first chapter's folder and none on their second was written
            # as "(no grant)", which this script's own docstring calls the
            # finding: "they cannot open the folder, an access request is
            # coming". A false one of those sends ops chasing access the person
            # already has. A grant anywhere wins; only someone with a grant on
            # NONE of their folders is the finding.
            if prior is None or (grant and not prior[1]):
                out[ce] = (f["name"], grant)
    return out, no_folder


def read_source():
    d = gws(["sheets", "spreadsheets", "values", "get", "--params",
             json.dumps({"spreadsheetId": INTAKE_ID, "range": SOURCE,
                         "majorDimension": "ROWS"}), "--format", "json"])
    vals = d.get("values", [])
    if not vals:
        sys.exit("ABORT: %r came back empty." % SOURCE)
    hdr = [h.strip() for h in vals[0]]
    return hdr, [r + [""] * (len(hdr) - len(r)) for r in vals[1:]]


def ensure_column(hdr, create):
    if H_DRIVE_EMAIL in hdr:
        return hdr.index(H_DRIVE_EMAIL)
    ci = len(hdr)
    if not create:
        return ci
    gws(["sheets", "spreadsheets", "values", "update", "--params",
         json.dumps({"spreadsheetId": INTAKE_ID,
                     "range": "%s!%s1" % (SOURCE, colletter(ci + 1)),
                     "valueInputOption": "RAW"}),
         "--json", json.dumps({"values": [[H_DRIVE_EMAIL]]}), "--format", "json"])
    print("Created column %r at %s." % (H_DRIVE_EMAIL, colletter(ci + 1)))
    return ci


def plan(hdr, rows, granted, no_folder, ci):
    """([(row, value, kind)], [(row, address, kind)]) — writes, then requests.

    The second list is the addresses a HUMAN put in the column that Drive has
    not granted yet. They are deliberately NOT writes: this script derives its
    values from the ACL, so it would replace every one of them with `(no grant)`
    — erasing the operator's answer to "which address should this person be
    granted under" between the moment they record it and the moment sync_access
    acts on it. That window is the whole point of the column being an input.
    """
    ie = hdr.index(H_EMAIL)
    out, pending = [], []
    for n, r in enumerate(rows, 2):
        email = (r[ie] or "").strip()
        if not email:
            continue
        current = (r[ci] or "").strip() if ci < len(r) else ""
        ce = canon_email(email)
        # The recorded-address check comes FIRST, before the "is there a grant"
        # branch. It used to sit only in the no-grant branch, so the common case
        # — an organizer who still holds an old grant under their intake address
        # and has a new address recorded — took the `if addr:` path and wrote
        # the OLD ACL spelling straight over the operator's entry, reported as
        # `same`, visible nowhere. The instruction was cancelled silently by the
        # very script whose docstring promises to leave it alone.
        recorded = (looks_like_address(current)
                    and canon_email(current) != ce
                    and (ce not in granted or not granted[ce][1]
                         or canon_email(granted[ce][1]) != canon_email(current)))
        if ce in granted:
            chapter, addr = granted[ce]
            if recorded:
                why = ("recorded, not granted yet" if not addr
                       else "recorded; still granted as %s — sync_access.py --write "
                            "will move it" % addr)
                pending.append((n, current, "%s/%s" % (chapter, why)))
            elif addr:
                # The ACL spelling canonicalizes back to `ce`, or to the address
                # recorded for them — that is how it matched — so the only
                # question left is whether it is spelled the way they typed it.
                kind = "same" if addr.lower() == email.lower() else "differs"
                out.append((n, addr, "%s/%s" % (chapter, kind)))
            else:
                out.append((n, NO_GRANT, "%s/missing" % chapter))
        elif ce in no_folder:
            if recorded:
                pending.append((n, current, "%s/no folder yet" % no_folder[ce]))
            else:
                out.append((n, NO_GRANT, "%s/no folder yet" % no_folder[ce]))
    return out, pending


def run(write):
    hdr, rows = read_source()
    ci = ensure_column(hdr, create=write)
    rows = [r + [""] * (ci + 1 - len(r)) for r in rows]
    granted, no_folder = grant_by_person()
    wanted, pending = plan(hdr, rows, granted, no_folder, ci)

    differs = [(n, v, k) for n, v, k in wanted
               if v != NO_GRANT and v.lower() != (rows[n - 2][hdr.index(H_EMAIL)] or "").strip().lower()]
    missing = [(n, v, k) for n, v, k in wanted if v == NO_GRANT]

    print("%d organizer row(s) map to a chapter folder grant." % len(wanted))
    if differs:
        print("\nGranted under a DIFFERENT spelling than the intake row "
              "(the intake column is left alone):")
        ie = hdr.index(H_EMAIL)
        for n, v, k in differs:
            print("   row %-5s %-34s -> %-34s %s"
                  % (n, redact_email(rows[n - 2][ie])[:34],
                     redact_email(v)[:34], k))
    if missing:
        print("\nNo grant matches their address — these people CANNOT open their "
              "chapter folder:")
        for n, _v, k in missing:
            ie = hdr.index(H_EMAIL)
            print("   row %-5s %-34s %s"
                  % (n, redact_email(rows[n - 2][ie])[:34], k))
    if pending:
        print("\nA %s is recorded but not granted yet — LEFT ALONE (run "
              "sync_access.py --write to grant these):" % H_DRIVE_EMAIL)
        ie = hdr.index(H_EMAIL)
        for n, v, k in pending:
            print("   row %-5s %-34s -> %-34s %s"
                  % (n, redact_email(rows[n - 2][ie])[:34],
                     redact_email(v)[:34], k))

    if not write:
        print("\nReport only — nothing was written. Re-run with --write to fill "
              "%d %s value(s)." % (len(wanted), H_DRIVE_EMAIL))
        # The shared engine convention: 0 in sync, 2 when there is pending work.
        # This script used to exit 0 unconditionally — including when `missing`
        # listed people who cannot open their chapter folder, which is the
        # finding it exists to produce.
        return 2 if (missing or pending) else 0
    data = [{"range": "%s!%s%d" % (SOURCE, colletter(ci + 1), n), "values": [[v]]}
            for n, v, _k in wanted]
    if data:
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": INTAKE_ID}), "--json",
             json.dumps({"valueInputOption": "RAW", "data": data}), "--format", "json"])
    print("\nWrote %d %s value(s)." % (len(data), H_DRIVE_EMAIL))
    return 2 if (missing or pending) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="fill the column (default: report only)")
    add_redact_flag(ap, masks="emails (a***@***.tld)")
    a = ap.parse_args()
    set_redaction(a.redact)
    return run(a.write)


if __name__ == "__main__":
    sys.exit(main())
