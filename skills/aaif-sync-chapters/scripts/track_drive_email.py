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

**A blank `Drive Email` on an accepted organizer is the finding.** It means no
direct grant on their chapter folder matches any spelling of their intake
address — they cannot open the folder, and an access request is coming.

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
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

from aaif_events.slack import scrubbed_env  # noqa: E402
from sync_access import canon_email, perms  # noqa: E402
from sync_chapters import INTAKE_ID  # noqa: E402
from sync_crm import (TEMPLATE_FOLDER, list_chapter_folders,  # noqa: E402
                      match_chapters, merge_people, read_role_tab)

SOURCE = "Form Responses"
H_EMAIL = "Email"
#: The column this script owns. Created at the right-hand end if absent.
H_DRIVE_EMAIL = "Drive Email"
#: Written when an accepted organizer's chapter folder carries no grant matching
#: any spelling of their address. A visible sentinel, not a blank — a blank cell
#: is indistinguishable from "this script has not run yet", and the whole point
#: of the column is that the empty case is the one worth acting on.
NO_GRANT = "(no grant)"


def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True,
                         env=scrubbed_env())
    if out.returncode != 0:
        sys.exit("gws error: %s...\n%s" % (" ".join(args[:4]), out.stderr.strip()[:400]))
    txt = out.stdout
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def colletter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def grant_by_person():
    """{canonical intake email: (chapter, address on the ACL)} for organizers.

    Reads permissions for every chapter folder an accepted organizer maps to.
    A person is matched to a grant by CANONICAL address, which is what makes the
    Gmail-dot case resolve rather than read as "no grant".
    """
    folders = [f for f in list_chapter_folders() if f["name"] != TEMPLATE_FOLDER]
    people, _, _ = read_role_tab("Organizers", {})
    by_folder, orphans, _near = match_chapters(merge_people(people), folders)

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
            out[ce] = (f["name"], held.get(ce))
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


def plan(hdr, rows, granted, no_folder):
    """[(row, value, kind)] for every Form Responses row that maps to a grant."""
    ie = hdr.index(H_EMAIL)
    out = []
    for n, r in enumerate(rows, 2):
        email = (r[ie] or "").strip()
        if not email:
            continue
        ce = canon_email(email)
        if ce in granted:
            chapter, addr = granted[ce]
            if addr:
                # The ACL spelling always canonicalizes back to `ce` — that is
                # how it matched — so the only question left is whether it is
                # spelled the same way the person typed it.
                kind = "same" if addr.lower() == email.lower() else "differs"
                out.append((n, addr, "%s/%s" % (chapter, kind)))
            else:
                out.append((n, NO_GRANT, "%s/missing" % chapter))
        elif ce in no_folder:
            out.append((n, NO_GRANT, "%s/no folder yet" % no_folder[ce]))
    return out


def run(write):
    hdr, rows = read_source()
    ci = ensure_column(hdr, create=write)
    rows = [r + [""] * (ci + 1 - len(r)) for r in rows]
    granted, no_folder = grant_by_person()
    wanted = plan(hdr, rows, granted, no_folder)

    differs = [(n, v, k) for n, v, k in wanted
               if v != NO_GRANT and v.lower() != (rows[n - 2][hdr.index(H_EMAIL)] or "").strip().lower()]
    missing = [(n, v, k) for n, v, k in wanted if v == NO_GRANT]

    print("%d organizer row(s) map to a chapter folder grant." % len(wanted))
    if differs:
        print("\nGranted under a DIFFERENT spelling than the intake row "
              "(the intake column is left alone):")
        ie = hdr.index(H_EMAIL)
        for n, v, k in differs:
            print("   row %-5s %-34s -> %-34s %s" % (n, rows[n - 2][ie][:34], v[:34], k))
    if missing:
        print("\nNo grant matches their address — these people CANNOT open their "
              "chapter folder:")
        for n, _v, k in missing:
            ie = hdr.index(H_EMAIL)
            print("   row %-5s %-34s %s" % (n, rows[n - 2][ie][:34], k))

    if not write:
        print("\nReport only — nothing was written. Re-run with --write to fill "
              "%d %s value(s)." % (len(wanted), H_DRIVE_EMAIL))
        return
    data = [{"range": "%s!%s%d" % (SOURCE, colletter(ci + 1), n), "values": [[v]]}
            for n, v, _k in wanted]
    if data:
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": INTAKE_ID}), "--json",
             json.dumps({"valueInputOption": "RAW", "data": data}), "--format", "json"])
    print("\nWrote %d %s value(s)." % (len(data), H_DRIVE_EMAIL))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="fill the column (default: report only)")
    run(ap.parse_args().write)


if __name__ == "__main__":
    main()
