#!/usr/bin/env python3
"""Move the Chapters folder off its public link-share and onto per-chapter grants.

Third engine in this skill, and the one with teeth: it changes who can reach
things. Report-only by default, like the other two.

The Chapters folder was shared `anyone -> reader`, inherited by every chapter
folder and every file in them. One thing depended on it: chapter organizers'
access. Before this engine first ran, essentially nobody held an individual
grant — the public link was how organizers got in, as readers — which is why
`grant` must always run before `lock`.

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
  python3 sync_access.py                    # full plan, changes nothing
  python3 sync_access.py --write            # apply every phase, in order
  python3 sync_access.py --write --phase grant
  python3 sync_access.py --role reader      # grant something other than writer
  python3 sync_access.py --write --mail-if-required   # email only the addresses
                                            # with no Google account, which Drive
                                            # refuses to share with otherwise
  python3 sync_access.py --write --notify   # email EVERY grantee
  python3 sync_access.py --write --lock-anyway        # lock even though some
                                            # organizers could not be granted
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import (CHAPTERS_ID, CHAPTERS_TAB, INTAKE_ID, gws_json, get_values,
                           cell, fold_city, header_index)
# ROLE_TABS is deliberately NOT imported: folder access reads ACCESS_TABS only,
# and having the wider constant in scope is how the escalation crept in before.
from sync_crm import (CHAPTERS_PARENT, SYNC_STATUSES, TEMPLATE_FOLDER,
                      fold_email, list_chapter_folders, match_chapters, merge_people,
                      read_role_tab)

# Kept deliberately: this is the Linux Foundation's own staff access, not public
# reach, and removing it is a separate decision from de-publicising the folder.
KEEP_DOMAIN = "linuxfoundation.org"

# Drive's refusal when the invitee has no Google account. It is a hard 400, not a
# soft warning: the only way to grant these people access is to let it email them.
NO_ACCOUNT = "there is no Google account"


def canon_email(e):
    """Match addresses the way DRIVE does, not the way the intake spells them.

    Google canonicalises a Gmail address by dropping dots from the local part, so
    granting `first.m.last@gmail.com` stores `firstmlast@gmail.com`.
    Comparing the intake spelling against the stored one therefore never matches,
    and every run re-proposes a grant that is already in place. Only gmail.com is
    folded — dots are significant on other hosts, which is why sync_crm's
    fold_email (the CRM dedupe key) deliberately keeps them.
    """
    e = fold_email(e)
    local, _, domain = e.partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        # googlemail.com is the same mailbox as gmail.com, and Gmail ignores a
        # +tag. Missing either reproduces exactly the bug this function fixes:
        # Drive stores the canonical form, the comparison misses, and the grant
        # is re-proposed on every run.
        local = local.split("+", 1)[0].replace(".", "")
        return local + "@gmail.com"
    return e


def perms(file_id):
    """Every permission on a file, each tagged with whether it is inherited.

    Paginated, deliberately. A single page caps at 100, and the Chapters parent
    is the most permission-heavy object in the tree — this PR alone adds ~92
    user grants beneath it. If the `anyone` entry ever falls onto page 2, an
    unpaginated read makes plan() report "already not link-shared", apply_lock
    delete nothing, and verify() confirm success on a still-public folder. That
    is fail-OPEN on the one decision that matters most here.
    """
    out, token = [], None
    while True:
        params = {"fileId": file_id, "supportsAllDrives": True, "pageSize": 100,
                  "fields": "nextPageToken,permissions(id,type,role,emailAddress,"
                            "domain,permissionDetails)"}
        if token:
            params["pageToken"] = token
        res = gws_json("drive", "permissions", "list", params=params)
        if "permissions" not in res:
            # An unexpected response shape must not read as "no permissions".
            raise RuntimeError("permissions.list returned no 'permissions' key for %s: %r"
                               % (file_id, res))
        for p in res["permissions"]:
            det = p.get("permissionDetails") or []
            # Default to inherited=True when the API doesn't say. The permissive
            # answer (False = "this is our own direct grant") would classify a
            # merely-inherited share as pinned/granted, skip the work, and then
            # skip the verification too.
            own = True if not det else any(d.get("inherited") for d in det)
            out.append(dict(p, inherited=own))
        token = res.get("nextPageToken")
        if not token:
            return out


def direct_public(file_id):
    """True when this file carries its OWN anyone:reader, not an inherited one.
    An inherited share disappears with the parent's; a direct one survives."""
    return any(p["type"] == "anyone" and not p["inherited"] for p in perms(file_id))


_DRIVE_ID_RE = re.compile(r"(?:/d/|[?&]id=)([A-Za-z0-9_-]{20,})")


def banner_ids():
    """{folded city -> {city, file_id, url}} for each chapter's banner asset.

    Resolved from the feed's `Image` column rather than by globbing for a file
    named `Web Banner.png`, so a cell pointing somewhere unexpected is visible
    rather than silently replaced by whatever the glob found.

    These are the Drive SOURCE assets the feed points at — NOT what visitors
    fetch. See the module docstring: the site serves its imagery from Sanity.

    Keyed with fold_city, matching every other city-to-folder comparison in this
    skill. Plain fold() leaves punctuation intact, so a feed spelling of
    `Washington, DC` missed the `Washington DC` folder and the chapter was
    reported as having no resolvable image at all.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters feed tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    i_img, i_city = header_index(headers, CHAPTERS_TAB, "Image", "City")
    out = {}
    for row in rows[1:]:
        url, city = cell(row, i_img), cell(row, i_city)
        if not (url and city):
            continue
        # Both forms Drive hands out: `/d/<id>` and `?id=<id>`. An unrecognised
        # shape yields "" and is reported, never silently treated as absent.
        m = _DRIVE_ID_RE.search(url)
        key = fold_city(city)
        if key in out:
            print("  !! duplicate feed row for %r — the later one wins" % city,
                  file=sys.stderr)
        out[key] = {"city": city, "file_id": m.group(1) if m else "", "url": url}
    return out


# Folder access is for ORGANIZERS ONLY — deliberately narrower than the CRM,
# which carries all three roles. An accepted speaker belongs in a chapter's CRM
# (they are a person the chapter deals with) but has no business with write
# access to its Drive folder: trackers, decks, budgets and the CRM itself live
# there. Looping ROLE_TABS here granted speakers and hosts the same writer role
# as organizers — invisible while neither tab has an accepted row (true as of
# 2026-08), and a silent privilege escalation the first time one is triaged.
# test_sync_access.py asserts this constant so the regression cannot return.
ACCESS_TABS = ("Organizers",)


def plan(role):
    folders = [f for f in list_chapter_folders() if f["name"] != TEMPLATE_FOLDER]
    people = []
    for tab in ACCESS_TABS:
        pp, _, _ = read_role_tab(tab, {})
        people += pp
    by_folder, orphans, near = match_chapters(merge_people(people), folders)
    imgs = banner_ids()

    pins, grants, already_pinned, already_granted, no_banner = [], [], [], [], []
    already_pinned_ids, already_granted_ids, stale = [], [], []
    for f in folders:
        img = imgs.get(fold_city(f["name"]))
        if not img or not img["file_id"]:
            no_banner.append(f["name"])
        elif direct_public(img["file_id"]):
            already_pinned.append(f["name"])
            already_pinned_ids.append((f["name"], img["file_id"]))
        else:
            pins.append({"chapter": f["name"], "folder_id": f["id"],
                         "file_id": img["file_id"], "url": img["url"]})

        want = by_folder.get(f["id"], [])
        # Read permissions for EVERY chapter, including the ones with no accepted
        # organizer. Skipping those hid their stale grants entirely — and a
        # chapter nobody is accepted for is exactly where an unexplained writer
        # is most worth seeing.
        folder_perms = perms(f["id"])
        expected = {canon_email(x["email"]) for x in want}
        for q in folder_perms:
            if q["type"] != "user" or q["inherited"]:
                continue
            if canon_email(q.get("emailAddress", "")) not in expected:
                stale.append((f["name"], q.get("emailAddress"), q["role"]))
        if not want:
            continue
        # The folder owner already has everything, inherited or not — re-granting
        # an owner is a no-op Drive rejects. (This tree is My Drive; on a Shared
        # Drive the equivalent top-level role is "organizer".)
        have = {canon_email(p.get("emailAddress", "")) for p in folder_perms
                if p["type"] == "user" and (not p["inherited"] or p["role"] == "owner")}
        for p in want:
            if canon_email(p["email"]) in have:
                already_granted.append((f["name"], p["email"]))
                already_granted_ids.append((f["name"], f["id"], p["email"]))
            else:
                grants.append({"chapter": f["name"], "folder_id": f["id"],
                               "email": p["email"], "name": p["name"], "role": role})

    parent = perms(CHAPTERS_PARENT)
    public = [p for p in parent if p["type"] == "anyone"]
    return {"pins": pins, "grants": grants, "already_pinned": already_pinned,
            "already_granted": already_granted, "no_banner": no_banner,
            "public": public, "parent": parent, "orphans": orphans, "near": near,
            "already_pinned_ids": already_pinned_ids,
            "already_granted_ids": already_granted_ids, "stale": stale, "role": role}


def report(p, role):
    print("PHASE 1 — pin the website's banner images (make each one directly public)")
    print("  %d banner(s) need their own anyone:reader; %d already have one."
          % (len(p["pins"]), len(p["already_pinned"])))
    for x in p["pins"][:6]:
        print("     %-20s %s" % (x["chapter"], x["file_id"]))
    if len(p["pins"]) > 6:
        print("     … and %d more" % (len(p["pins"]) - 6))
    if p["no_banner"]:
        print("  !! %d chapter(s) have no resolvable Image id on the feed (the cell is "
              "empty or not a Drive URL): %s"
              % (len(p["no_banner"]), ", ".join(p["no_banner"])))

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

    if p["stale"]:
        # Direct grants held by people with no intake row. They survive the lock,
        # so a denied ex-organizer keeps write access until someone removes it
        # by hand — which requires knowing they exist.
        print("\nDirect grants held by people the intake does not know about "
              "(NOT touched; they survive the lock — audit these):")
        for ch, em, r in sorted(p["stale"]):
            print("     %-18s %-42s %s" % (ch, em, r))
    print("\nNet effect: %d chapter-folder grant(s) at %r for accepted organizers, and "
          "the CRMs stop being readable by anyone with the link."
          % (len(p["grants"]) + len(p["already_granted"]), role))
    print("           (%d Drive banner(s) would hold their own public share. The "
          "website itself serves from Sanity and is unaffected either way.)"
          % (len(p["pins"]) + len(p["already_pinned"])))


def apply_pins(p):
    """Give each banner its own anyone:reader — validated, then re-read.

    The file id is string-sliced out of a spreadsheet cell that anyone with edit
    access to the chapters feed can change. Without checking what it points at,
    aiming an `Image` cell at a CRM file id would make that CRM permanently
    world-readable — and `lock` only touches the parent, so the grant survives
    the very step meant to make things private.
    """
    landed = 0
    for x in p["pins"]:
        meta = gws_json("drive", "files", "get", params={
            "fileId": x["file_id"], "supportsAllDrives": True,
            "fields": "id,name,mimeType,parents"})
        if not meta.get("mimeType", "").startswith("image/"):
            sys.exit("ABORT: %s's Image cell points at %r (%s), which is not an image. "
                     "Nothing further was pinned." % (x["chapter"], meta.get("name"),
                                                      meta.get("mimeType")))
        if x["folder_id"] not in (meta.get("parents") or []):
            sys.exit("ABORT: %s's Image cell points at %r, which does not live in that "
                     "chapter's folder. Nothing further was pinned."
                     % (x["chapter"], meta.get("name")))
        gws_json("drive", "permissions", "create",
                 params={"fileId": x["file_id"], "supportsAllDrives": True},
                 body={"type": "anyone", "role": "reader"})
        # Drive merges a duplicate anyone:reader into the inherited one and
        # returns 200 having stored nothing, so the create call proves nothing.
        if not direct_public(x["file_id"]):
            sys.exit("ABORT: pinning %s returned success but the file still has no "
                     "direct anyone:reader — Drive merged it into the parent's "
                     "inherited share. The parent must be unshared FIRST. Do not "
                     "run lock." % x["chapter"])
        landed += 1
        print("  pinned %s" % x["chapter"])
    return landed


def assert_all_accepted(grants):
    """Re-read the intake and confirm every grant target really is an accepted
    organizer, aborting on the first that isn't.

    Deliberately redundant with read_role_tab's status filter, and deliberately a
    different code path: this is the last gate before handing someone standing
    write access to a chapter, and "the filter that built the list says the list
    is fine" is not a check. Matches on email across ALL role tabs, because a
    person can hold several rows and only one of them needs to be a decision.
    """
    # Scan ACCESS_TABS, not ROLE_TABS. Matching a decision on ANY tab meant an
    # accepted SPEAKER satisfied the gate — precisely the privilege escalation
    # the ACCESS_TABS comment above records as having already shipped once. A
    # gate has to be at least as narrow as the thing it guards.
    ok = {}
    for tab in ACCESS_TABS:
        rows = get_values(INTAKE_ID, "%s!A:BB" % tab)
        if not rows:
            sys.exit("ABORT: intake tab %r came back empty — cannot verify grants." % tab)
        headers = [h.strip() for h in rows[0]]
        i_st, i_em, i_ch = header_index(headers, tab, "Status", "Email", "Chapter")
        i_h = headers.index("City (New)") if "City (New)" in headers else None
        i_g = headers.index("City (Existing)") if "City (Existing)" in headers else None
        for row in rows[1:]:
            row = row + [""] * (len(headers) - len(row))
            e = canon_email(cell(row, i_em))
            if not e or cell(row, i_st) not in SYNC_STATUSES:
                continue
            g_, h_ = (cell(row, i_g) if i_g is not None else ""), \
                     (cell(row, i_h) if i_h is not None else "")
            city = cell(row, i_ch) or h_ or (g_ if g_ and not g_.lower().startswith("other") else "")
            ok.setdefault(e, set()).add(fold_city(city))

    # ...and the accepted row must name the chapter being granted. Without this
    # an accepted organizer for one city satisfies a grant on any other, so a
    # chapter mis-binding upstream would sail through the last gate.
    bad = [g for g in grants
           if fold_city(g["chapter"]) not in ok.get(canon_email(g["email"]), set())]
    if bad:
        sys.exit("ABORT: %d grant target(s) are not accepted ORGANIZERS for the "
                 "chapter being granted — nothing was granted:\n%s"
                 % (len(bad), "\n".join(
                     "  %s -> %s (accepted organizer for: %s)"
                     % (g["email"], g["chapter"],
                        sorted(ok.get(canon_email(g["email"]), set())) or "<no accepted organizer row>")
                     for g in bad)))
    print("  double-checked: all %d target(s) hold an %s row on %s, for the chapter "
          "being granted." % (len(grants), " / ".join(SYNC_STATUSES), "/".join(ACCESS_TABS)))


def apply_grants(p, notify, allow_mail=False):
    """Grant each organizer their chapter, surviving individual failures.

    One unusable address must not abandon the rest: the intake is fed by a public
    form, so a typo'd address is a NORMAL input, and Drive rejects it with a hard
    400. Aborting the phase on the first one left most of the batch unapplied and
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
            # Notifications off by default: one share-mail per organizer, all
            # arriving unannounced at once, reads as a phishing wave.
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
    return len(p["grants"]) - len(failed), failed


def apply_lock(p):
    for x in p["public"]:
        gws_json("drive", "permissions", "delete",
                 params={"fileId": CHAPTERS_PARENT, "permissionId": x["id"],
                         "supportsAllDrives": True})
        print("  removed %s:%s from Chapters/" % (x["type"], x["role"]))
    return len(p["public"])


def verify(p, ran):
    """Re-read what the phases changed, for the phases that actually ran.

    Every check here re-reads remote truth. Three earlier weaknesses are closed:

      * it verified only the PLANNED pins/grants, so anything the plan
        classified as `already_*` — i.e. exactly the case where the
        classification was wrong — was never checked;
      * it sampled `grants[:5]` of ninety-odd, so a systematic failure past the
        fifth passed clean;
      * it accepted ANY permission for the address, so an inherited or
        reader-level one satisfied a check for a direct `writer` grant.

    `ran` also matters: an empty plan must not print a success line claiming
    checks that iterated nothing.
    """
    bad, checked = [], 0
    if "pin" in ran:
        # `already_pinned` is an assertion the plan made from an earlier read —
        # re-verify it rather than trusting it.
        for x in p["pins"] + [{"chapter": c, "file_id": i}
                              for c, i in p.get("already_pinned_ids", [])]:
            checked += 1
            if not direct_public(x["file_id"]):
                bad.append("banner for %s is still not directly public" % x["chapter"])
    if "grant" in ran:
        want = [(g["chapter"], g["folder_id"], g["email"], g["role"]) for g in p["grants"]]
        want += [(c, fid, e, p["role"]) for c, fid, e in p.get("already_granted_ids", [])]
        for chapter, folder_id, email, role in want:
            checked += 1
            # Mirror plan()'s owner exception: the tree's owner holds everything
            # (inherited "owner" on every folder), Drive rejects re-granting
            # them, and plan() therefore never proposes it — so verify must not
            # demand the direct grant plan correctly refused to make.
            direct = {canon_email(q.get("emailAddress", "")): q["role"]
                      for q in perms(folder_id)
                      if q["type"] == "user"
                      and (not q["inherited"] or q["role"] == "owner")}
            got = direct.get(canon_email(email))
            if got is None:
                bad.append("%s has no direct grant on %s" % (email, chapter))
            elif got != role and got != "owner":
                bad.append("%s has %r on %s, expected %r" % (email, got, chapter, role))
    if "lock" in ran:
        checked += 1
        if any(q["type"] == "anyone" for q in perms(CHAPTERS_PARENT)):
            bad.append("Chapters/ is still link-shared")
    if not checked:
        bad.append("nothing was verified — the plan was empty for the phase(s) that ran")
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
    ap.add_argument("--lock-anyway", action="store_true",
                    help="remove the public share even when some organizers could "
                         "not be granted access (they will lose all access)")
    ap.add_argument("--mail-if-required", action="store_true",
                    help="email only the organizers whose address has no Google "
                         "account, where Drive refuses to share without it")
    a = ap.parse_args()

    p = plan(a.role)
    report(p, a.role)
    if not a.write:
        print("\nReport only — nothing was changed. Re-run with --write to apply.")
        # Shared engine exit convention: report mode exits 0 when in sync, 2 when
        # it proposes changes (consumed by nightly.py). Pending banner pins are
        # NOT drift: the site serves from Sanity, so pinning is a standing human
        # decision, and counting it would report drift every night forever.
        return 2 if (p["grants"] or p["public"]) else 0

    order = [("pin", apply_pins, (p,)), ("grant", apply_grants, (p, a.notify, a.mail_if_required)),
             ("lock", apply_lock, (p,))]
    grant_failures = []
    for name, fn, args in order:
        if a.phase and a.phase != name:
            continue
        # Removing the public share is the last thing that happens, and only if
        # every organizer actually has their own grant. Skipping a no-Google-
        # account address is the DOCUMENTED DEFAULT, so without this gate the
        # normal run locks out exactly the people it failed to grant — the
        # public link being the only access they had.
        if name == "lock" and grant_failures and not a.lock_anyway:
            sys.exit("ABORT before lock: %d organizer(s) have no grant (listed above). "
                     "Removing the public share now would leave them with NO access at "
                     "all.\nFix the intake rows (or pass --mail-if-required), then "
                     "re-run — or pass --lock-anyway to accept locking them out."
                     % len(grant_failures))
        print("\nApplying phase %r..." % name)
        try:
            n = fn(*args)
        except Exception as e:
            sys.exit("ABORT during phase %r: %s\nEarlier phases were applied; later "
                     "ones were NOT. Re-run to continue." % (name, e))
        if name == "grant":
            n, grant_failures = n
        print("  phase %r: %d change(s)" % (name, n))

    # Verify whatever ran, including a single --phase. `--write --phase lock` is
    # the most destructive invocation available and used to be the one path with
    # no re-read at all, reporting a PLANNED count as its result.
    ran = [n for n, _, _ in order if not a.phase or a.phase == n]
    if ran:
        print("\nVerifying...")
        bad = verify(p, ran)
        if bad:
            print("VERIFY FAILED:")
            for b in bad:
                print("  " + b)
            return 1
        print("Verified: banners are directly public and Chapters/ is no longer link-shared.")
    return 0


if __name__ == "__main__":
    # sys.exit(main()), not main(): the return code is the ONLY signal a caller,
    # CI step or `&&` chain gets. Discarding it made every run — including
    # "VERIFY FAILED" after the public share was removed — exit 0.
    sys.exit(main())
