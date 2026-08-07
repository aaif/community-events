#!/usr/bin/env python3
"""Move the Chapters folder off its public link-share and onto per-chapter grants.

Third engine in this skill, and the one with teeth: it changes who can reach
things. Report-only by default, like the other two.

The Chapters folder is shared `anyone -> reader`, inherited by every chapter
folder and every file in them. One thing depends on it: chapter organizers'
access. Nobody has an individual grant — the public link IS how they get in (as
a reader) — so `grant` must run before `lock`.

    grant — give each accepted organizer access to their own chapter folder.
    lock  — remove anyone:reader from the Chapters folder.

The website does NOT depend on it, though the chapters feed makes it look like it
does. Every `Image` cell on that feed is `lh3.googleusercontent.com/d/<id>`
pointing at a `Web Banner.png` inside a chapter folder, which reads as "the site
serves 80 public Drive images". It does not: aaif.io/community-chapters was
loaded and inspected on 2026-08-07 and every one of its 26 images comes from
`cdn.sanity.io`. Chapter content and imagery live in Sanity; the Drive banners
are source assets, not what visitors fetch. Verify with the live page before ever
concluding otherwise — the feed column is not evidence.

A `pin` phase exists for the case where something public genuinely does live in
the tree. It is a NO-OP while the parent is still shared: Drive merges a child's
`anyone:reader` into the inherited one and reports success, so a child can only
hold its own public share AFTER the parent's is gone. Never trust its "N changes"
line — re-read the permission and check `permissionDetails[].inherited`.

Usage:
  python3 sync_access.py                  # full plan, changes nothing
  python3 sync_access.py --write          # apply all three phases, in order
  python3 sync_access.py --write --phase pin
  python3 sync_access.py --role reader    # grant something other than writer
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import (CHAPTERS_ID, CHAPTERS_TAB, INTAKE_ID, gws_json, get_values,
                           cell, fold)
from sync_crm import (CHAPTERS_PARENT, SYNC_STATUSES, TEMPLATE_FOLDER, ROLE_TABS,
                      fold_email, list_chapter_folders, match_chapters, merge_people,
                      read_role_tab)

PUBLIC_ID = "anyoneWithLink"       # the permission id Drive gives an anyone-share
BANNER = "Web Banner.png"

# Kept deliberately: this is the Linux Foundation's own staff access, not public
# reach, and removing it is a separate decision from de-publicising the folder.
KEEP_DOMAIN = "linuxfoundation.org"

# Drive's refusal when the invitee has no Google account. It is a hard 400, not a
# soft warning: the only way to grant these people access is to let it email them.
NO_ACCOUNT = "there is no Google account"


def canon_email(e):
    """Match addresses the way DRIVE does, not the way the intake spells them.

    Google canonicalises a Gmail address by dropping dots from the local part, so
    granting `aman.singh.original@gmail.com` stores `amansinghoriginal@gmail.com`.
    Comparing the intake spelling against the stored one therefore never matches,
    and every run re-proposes a grant that is already in place. Only gmail.com is
    folded — dots are significant on other hosts, which is why sync_crm's
    fold_email (the CRM dedupe key) deliberately keeps them.
    """
    e = fold_email(e)
    local, _, domain = e.partition("@")
    return (local.replace(".", "") + "@" + domain) if domain == "gmail.com" else e


def perms(file_id):
    """Permissions on a file, each tagged with whether it is inherited."""
    res = gws_json("drive", "permissions", "list", params={
        "fileId": file_id, "supportsAllDrives": True, "pageSize": 100,
        "fields": "permissions(id,type,role,emailAddress,domain,permissionDetails)"})
    out = []
    for p in res.get("permissions", []):
        det = p.get("permissionDetails") or [{}]
        out.append(dict(p, inherited=bool(det[0].get("inherited"))))
    return out


def direct_public(file_id):
    """True when this file carries its OWN anyone:reader, not an inherited one.
    An inherited share disappears with the parent's; a direct one survives."""
    return any(p["type"] == "anyone" and not p["inherited"] for p in perms(file_id))


def banner_ids():
    """{chapter folder id -> banner file id} for the images the website serves.

    Resolved from the feed's `Image` column, not by globbing for a file named
    `Web Banner.png`: the feed is what the public site actually requests, so a
    chapter whose cell points somewhere unexpected must be pinned there, and a
    banner nothing references is not load-bearing.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    if "Image" not in headers:
        sys.exit("ABORT: no 'Image' column on the chapters feed — cannot tell which "
                 "files the website serves, so the lock step is unsafe to plan.")
    i_img, i_city = headers.index("Image"), headers.index("City")
    out = {}
    for row in rows[1:]:
        url, city = cell(row, i_img), cell(row, i_city)
        if not (url and city):
            continue
        # .../d/<id> is the only form the feed uses; anything else is reported.
        fid = url.rsplit("/d/", 1)[-1].split("/")[0].split("?")[0] if "/d/" in url else ""
        out[fold(city)] = {"city": city, "file_id": fid, "url": url}
    return out


# Folder access is for ORGANIZERS ONLY — deliberately narrower than the CRM,
# which carries all three roles. An accepted speaker belongs in a chapter's CRM
# (they are a person the chapter deals with) but has no business with write
# access to its Drive folder: trackers, decks, budgets and the CRM itself live
# there. Looping ROLE_TABS here granted speakers and hosts the same writer role
# as organizers — invisible today because neither tab has an accepted row, and a
# silent privilege escalation the first time one is triaged.
ACCESS_TABS = ("Organizers",)


def plan(role):
    folders = [f for f in list_chapter_folders() if f["name"] != TEMPLATE_FOLDER]
    people = []
    for tab in ACCESS_TABS:
        pp, _ = read_role_tab(tab, {})
        people += pp
    by_folder, orphans, near = match_chapters(merge_people(people), folders)
    imgs = banner_ids()

    pins, grants, already_pinned, already_granted, no_banner = [], [], [], [], []
    for f in folders:
        img = imgs.get(fold(f["name"]))
        if not img or not img["file_id"]:
            no_banner.append(f["name"])
        elif direct_public(img["file_id"]):
            already_pinned.append(f["name"])
        else:
            pins.append({"chapter": f["name"], "file_id": img["file_id"], "url": img["url"]})

        want = by_folder.get(f["id"], [])
        if not want:
            continue
        # Owner/organizer access already covers everything, inherited or not —
        # re-granting the folder owner is a no-op Drive rejects.
        have = {canon_email(p.get("emailAddress", "")) for p in perms(f["id"])
                if p["type"] == "user" and (not p["inherited"] or p["role"] == "owner")}
        for p in want:
            if canon_email(p["email"]) in have:
                already_granted.append((f["name"], p["email"]))
            else:
                grants.append({"chapter": f["name"], "folder_id": f["id"],
                               "email": p["email"], "name": p["name"], "role": role})

    parent = perms(CHAPTERS_PARENT)
    public = [p for p in parent if p["type"] == "anyone"]
    return {"pins": pins, "grants": grants, "already_pinned": already_pinned,
            "already_granted": already_granted, "no_banner": no_banner,
            "public": public, "parent": parent, "orphans": orphans, "near": near}


def report(p, role):
    print("PHASE 1 — pin the website's banner images (make each one directly public)")
    print("  %d banner(s) need their own anyone:reader; %d already have one."
          % (len(p["pins"]), len(p["already_pinned"])))
    for x in p["pins"][:6]:
        print("     %-20s %s" % (x["chapter"], x["file_id"]))
    if len(p["pins"]) > 6:
        print("     … and %d more" % (len(p["pins"]) - 6))
    if p["no_banner"]:
        print("  !! %d chapter(s) have no resolvable Image on the feed — the site shows "
              "nothing for them either way: %s" % (len(p["no_banner"]), ", ".join(p["no_banner"])))

    print("\nPHASE 2 — grant each accepted organizer %r on their own chapter folder" % role)
    print("  %d new grant(s) across %d chapter(s); %d already in place."
          % (len(p["grants"]), len({g["chapter"] for g in p["grants"]}), len(p["already_granted"])))
    by_ch = {}
    for g in p["grants"]:
        by_ch.setdefault(g["chapter"], []).append(g["email"])
    for ch in sorted(by_ch)[:6]:
        print("     %-20s %s" % (ch, ", ".join(by_ch[ch])))
    if len(by_ch) > 6:
        print("     … and %d more chapter(s)" % (len(by_ch) - 6))

    print("\nPHASE 3 — remove the public share from the Chapters folder")
    if not p["public"]:
        print("  Nothing to remove — the folder is already not link-shared.")
    for x in p["public"]:
        print("     delete permission %s (%s:%s) on Chapters/" % (x["id"], x["type"], x["role"]))
    print("  Kept on the parent:")
    for x in p["parent"]:
        if x["type"] == "anyone":
            continue
        who = x.get("emailAddress") or x.get("domain") or ""
        print("     %-10s %-10s %s%s" % (x["type"], x["role"], who,
                                         "   <- LF staff, kept by design"
                                         if who == KEEP_DOMAIN else ""))
    if p["orphans"] or p["near"]:
        print("\nAccepted organizers with no matching chapter folder (they get NO grant):")
        for o in p["orphans"]:
            print("     %-24s %s" % (o["city"], ", ".join(x["name"] for x in o["people"])))
        for m in p["near"]:
            print("     %-24s ~ %s" % (m["city"], ", ".join(m["candidates"])))

    print("\nNet effect: the website keeps its %d images, %d organizers keep access to "
          "their own chapter only, and the CRMs stop being readable by anyone with the link."
          % (len(p["pins"]) + len(p["already_pinned"]), len(p["grants"]) + len(p["already_granted"])))


def apply_pins(p):
    for x in p["pins"]:
        gws_json("drive", "permissions", "create",
                 params={"fileId": x["file_id"], "supportsAllDrives": True},
                 body={"type": "anyone", "role": "reader"})
        print("  pinned %s" % x["chapter"])
    return len(p["pins"])


def assert_all_accepted(grants):
    """Re-read the intake and confirm every grant target really is an accepted
    organizer, aborting on the first that isn't.

    Deliberately redundant with read_role_tab's status filter, and deliberately a
    different code path: this is the last gate before handing someone standing
    write access to a chapter, and "the filter that built the list says the list
    is fine" is not a check. Matches on email across ALL role tabs, because a
    person can hold several rows and only one of them needs to be a decision.
    """
    ok = {}
    for tab in ROLE_TABS:
        rows = get_values(INTAKE_ID, "%s!A:BB" % tab)
        headers = [h.strip() for h in rows[0]]
        i_st, i_em = headers.index("Status"), headers.index("Email")
        for row in rows[1:]:
            row = row + [""] * (len(headers) - len(row))
            e = fold_email(cell(row, i_em))
            if e:
                ok.setdefault(e, set()).add(cell(row, i_st))
    bad = [g for g in grants
           if not (ok.get(fold_email(g["email"]), set()) & set(SYNC_STATUSES))]
    if bad:
        sys.exit("ABORT: %d grant target(s) are NOT accepted/existing organizers — "
                 "nothing was granted:\n%s"
                 % (len(bad), "\n".join("  %s (%s) status=%s" % (
                     g["email"], g["chapter"],
                     sorted(ok.get(fold_email(g["email"]), {"<no intake row>"})))
                     for g in bad)))
    print("  double-checked: all %d target(s) hold an %s row."
          % (len(grants), " / ".join(SYNC_STATUSES)))


def apply_grants(p, notify, allow_mail=False):
    """Grant each organizer their chapter, surviving individual failures.

    One unusable address must not abandon the rest: the intake is fed by a public
    form, so a typo'd address is a NORMAL input, and Drive rejects it with a hard
    400. Aborting the phase on the first one left 82 of 91 grants unapplied and
    made the failure look systemic rather than like one bad row to fix.
    """
    assert_all_accepted(p["grants"])
    failed, mailed = [], []

    def create(g, send):
        gws_json("drive", "permissions", "create",
                 params={"fileId": g["folder_id"], "supportsAllDrives": True,
                         "sendNotificationEmail": send},
                 body={"type": "user", "role": g["role"], "emailAddress": g["email"]})

    for g in p["grants"]:
        try:
            # Notifications off by default: 98 share-mails arriving unannounced
            # reads as a phishing wave, and everyone already has read access.
            create(g, bool(notify))
            print("  granted %s -> %s (%s)" % (g["email"], g["chapter"], g["role"]))
            continue
        except Exception as e:
            msg = str(e)
        # Drive REFUSES to share with an address that has no Google account
        # unless it may email them — there is no silent path for these, so the
        # notification is the price of granting access at all, not a choice.
        if NO_ACCOUNT in msg and not notify:
            if not allow_mail:
                failed.append((g["chapter"], g["name"], g["email"],
                               "no Google account — Drive requires emailing them; "
                               "re-run with --notify (or --mail-if-required)"))
                print("  SKIPPED %s -> %s: needs a notification email"
                      % (g["email"], g["chapter"]), file=sys.stderr)
                continue
            try:
                create(g, True)
                mailed.append((g["chapter"], g["email"]))
                print("  granted %s -> %s (%s, notification sent — no Google account)"
                      % (g["email"], g["chapter"], g["role"]))
                continue
            except Exception as e:
                msg = str(e)
        hint = ("Drive rejected the address — check it for a typo on the intake row"
                if "problem with this email" in msg else msg[:160])
        failed.append((g["chapter"], g["name"], g["email"], hint))
        print("  FAILED %s -> %s: %s" % (g["email"], g["chapter"], hint), file=sys.stderr)

    if mailed:
        print("\n  %d grant(s) sent a Drive notification email (unavoidable — no "
              "Google account on the address):" % len(mailed))
        for ch, em in mailed:
            print("     %-18s %s" % (ch, em))
    if failed:
        print("\n  %d grant(s) could not be applied — fix the intake row and re-run:"
              % len(failed))
        for ch, name, em, why in failed:
            print("     %-18s %-26s %s\n        %s" % (ch, name, em, why))
    return len(p["grants"]) - len(failed)


def apply_lock(p):
    for x in p["public"]:
        gws_json("drive", "permissions", "delete",
                 params={"fileId": CHAPTERS_PARENT, "permissionId": x["id"],
                         "supportsAllDrives": True})
        print("  removed %s:%s from Chapters/" % (x["type"], x["role"]))
    return len(p["public"])


def verify(p):
    """Re-read the three things the phases changed. The banners are checked FIRST
    and individually — a site-wide image outage is the worst outcome here and the
    only one nobody would notice from inside Drive."""
    bad = []
    for x in p["pins"]:
        if not direct_public(x["file_id"]):
            bad.append("banner for %s is still not directly public" % x["chapter"])
    if any(q["type"] == "anyone" for q in perms(CHAPTERS_PARENT)):
        bad.append("Chapters/ is still link-shared")
    # Spot-check the grants rather than re-reading all 52 folders: a missing
    # grant is recoverable and visible to the organizer, unlike a dark website.
    for g in p["grants"][:5]:
        if canon_email(g["email"]) not in {canon_email(q.get("emailAddress", ""))
                                           for q in perms(g["folder_id"])}:
            bad.append("%s has no grant on %s" % (g["email"], g["chapter"]))
    return bad


def main():
    ap = argparse.ArgumentParser(description="Plan/apply per-chapter access for the Chapters folder.")
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    ap.add_argument("--role", default="writer", choices=("writer", "reader", "commenter"),
                    help="role granted to each organizer on their chapter (default: writer)")
    ap.add_argument("--phase", choices=("pin", "grant", "lock"),
                    help="apply only one phase (default: all three, in order)")
    ap.add_argument("--notify", action="store_true",
                    help="let Drive email EVERY organizer about their new access")
    ap.add_argument("--mail-if-required", action="store_true",
                    help="email only the organizers whose address has no Google "
                         "account, where Drive refuses to share without it")
    a = ap.parse_args()

    p = plan(a.role)
    report(p, a.role)
    if not a.write:
        print("\nReport only — nothing was changed. Re-run with --write to apply.")
        return 0

    order = [("pin", apply_pins, (p,)), ("grant", apply_grants, (p, a.notify, a.mail_if_required)),
             ("lock", apply_lock, (p,))]
    for name, fn, args in order:
        if a.phase and a.phase != name:
            continue
        print("\nApplying phase %r..." % name)
        # Phases run in order and the loop stops on the first failure: locking
        # after a failed pin is the one combination that takes the site down.
        try:
            n = fn(*args)
        except Exception as e:
            sys.exit("ABORT during phase %r: %s\nEarlier phases were applied; later "
                     "ones were NOT. Re-run to continue." % (name, e))
        print("  phase %r: %d change(s)" % (name, n))

    if not a.phase:
        print("\nVerifying...")
        bad = verify(p)
        if bad:
            print("VERIFY FAILED:")
            for b in bad:
                print("  " + b)
            return 1
        print("Verified: banners are directly public and Chapters/ is no longer link-shared.")
    return 0


if __name__ == "__main__":
    main()
