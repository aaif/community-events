#!/usr/bin/env python3
"""Move the Chapters folder off its public link-share and onto per-chapter grants.

Fourth engine in the pipeline (feed -> about -> CRM -> access -> resources),
and the one with teeth: it changes who can reach things. Report-only by
default, like the other four.

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

Nothing in this tree needs a public share (2026-09-03: the `pin` phase that
used to exist for that — making a banner directly public — was removed; the
site serves from Sanity, never from a directly-shared Drive file, so there was
never a real case for it). `--write` runs grant then lock, always.

Usage:
  python3 sync_access.py                    # full plan, changes nothing
  python3 sync_access.py --write            # apply grant + lock, in order
  python3 sync_access.py --write --phase grant
  python3 sync_access.py --role reader      # grant something other than writer
  python3 sync_access.py --write --mail-if-required --i-have-approval
                                            # email only the addresses with no
                                            # Google account, which Drive refuses
                                            # to share with otherwise
  python3 sync_access.py --write --notify --i-have-approval   # email EVERY grantee
  python3 sync_access.py --write --lock-anyway        # lock even though some
                                            # organizers could not be granted

The address granted is the intake `Email`, UNLESS a human has recorded a
different one in the `Drive Email` column on `Form Responses` — see
reviewed_drive_emails(). That column is the supported way to give access to
someone whose intake address has no Google account behind it, which Drive
refuses to share with outright. The intake `Email` is never rewritten: it is
what the person told us and the key the CRM merges on.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_chapters import (INTAKE_ID, gws_json, get_values,
                           cell, fold_city, header_index)
# ROLE_TABS is deliberately NOT imported: folder access reads ACCESS_TABS only,
# and having the wider constant in scope is how the escalation crept in before.
from sync_crm import (CHAPTERS_PARENT, SYNC_STATUSES, TEMPLATE_FOLDER,
                      fold_email, list_chapter_folders, match_chapters, merge_people,
                      read_role_tab)

# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks emails as a***@***.tld and
# names as a first initial in every printed line. Each standalone script
# carries its own copy of this flag and these helpers.
REDACT = False
CI_REDACT_DEFAULT = os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")


def redact_email(e):
    if not REDACT or not e or "@" not in e:
        return e
    local, _, domain = e.partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else "***"
    return "%s***@***.%s" % (local[:1], tld)


def redact_name(n):
    if not REDACT or not n or not n.strip():
        return n
    return n.strip()[0].upper() + "."


def add_redact_flag(ap):
    ap.add_argument("--redact", action=argparse.BooleanOptionalAction,
                    default=CI_REDACT_DEFAULT,
                    help="mask emails (a***@***.tld) and names (first initial) "
                         "on stdout; default on when CI is set")


def set_redaction(on):
    """Apply the parsed flag; one stderr line says so when masking is on."""
    global REDACT
    REDACT = bool(on)
    if REDACT:
        print("redaction ON (CI set; pass --no-redact to disable)"
              if CI_REDACT_DEFAULT else "redaction ON (--redact)", file=sys.stderr)


# Kept deliberately: this is the Linux Foundation's own staff access, not public
# reach, and removing it is a separate decision from de-publicising the folder.
KEEP_DOMAIN = "linuxfoundation.org"

# Drive's refusal when the invitee has no Google account. It is a hard 400, not a
# soft warning: the only way to grant these people access is to let it email them.
NO_ACCOUNT = "there is no Google account"


def canon_email(e):
    """Match addresses the way DRIVE does, not the way the intake spells them.

    Google canonicalises a CONSUMER Gmail address by dropping dots from the local
    part, so granting `first.m.last@gmail.com` stores `firstmlast@gmail.com`.
    Comparing the intake spelling against the stored one therefore never matches,
    and every run re-proposes a grant that is already in place.

    Only gmail.com/googlemail.com fold. Dots stay significant on every other
    host — including a Google Workspace account on a custom domain, where
    `first.last@acme.com` and `firstlast@acme.com` are two different people — so
    folding more widely would collapse two humans onto one grant. That is also
    why sync_crm's fold_email (the CRM dedupe key) deliberately keeps them.
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


#: The `Drive Email` column on `Form Responses`, and its "no grant matches this
#: person" sentinel. Defined HERE rather than in track_drive_email.py, which
#: writes the column: that script already imports from this one, so owning the
#: names there and importing them back would be a cycle. track_drive_email
#: imports them from here instead.
SOURCE = "Form Responses"
H_EMAIL = "Email"
H_DRIVE_EMAIL = "Drive Email"
NO_GRANT = "(no grant)"


def looks_like_address(v):
    """A conservative "is this an address" test for a hand-editable cell.

    Deliberately strict rather than clever: this value becomes the target of a
    Drive grant, so anything ambiguous must fall through to the intake address
    rather than be guessed at. Rejects the `(no grant)` sentinel by construction
    (no `@`), and anything carrying whitespace, a comma or a semicolon — the
    shapes a human produces when they put TWO addresses in one cell.
    """
    v = (v or "").strip()
    if not v or v == NO_GRANT:
        return False
    if any(c in v for c in " \t,;<>"):
        return False
    local, at, domain = v.partition("@")
    return bool(at) and bool(local) and "." in domain


def reviewed_drive_emails(rows=None):
    """{canonical intake email: the address to grant} from `Drive Email`.

    The intake `Email` is what the person told us and is never overwritten — but
    it is not always an address Drive can grant. An Apple or Outlook address with
    no Google account behind it is refused outright (Drive's hard 400), and a
    person may simply sign in as someone else entirely. `Drive Email` is where
    that second address is recorded, and this is what makes the column an INPUT
    to access rather than a note about it: the grant goes to the recorded
    address, while `Email`, the CRM merge key, stays untouched.

    The authority still comes from the intake row — a recorded address only
    redirects a grant that an accepted organizer row already justifies, and
    assert_all_accepted re-derives this mapping independently before anything is
    granted. A cell agreeing with the intake address is no override at all and is
    dropped here, so `via_column` means only what it says.

    Two refusals, both toward doing nothing rather than the wrong thing:

      * a value that is not address-shaped is ignored with a stderr line — the
        column is hand-editable and a typo must not become a grant target;
      * two rows for one person disagreeing about the address drops BOTH, also
        loudly. The column is per-row and a person can hold several rows, so a
        disagreement is a real question about which address is theirs, and
        picking one is exactly the guess this estate's identity bugs came from.
    """
    if rows is None:
        rows = get_values(INTAKE_ID, "'%s'" % SOURCE)
    if not rows:
        sys.exit("ABORT: %r came back empty — cannot read %r." % (SOURCE, H_DRIVE_EMAIL))
    hdr = [h.strip() for h in rows[0]]
    if H_DRIVE_EMAIL not in hdr or H_EMAIL not in hdr:
        return {}
    ie, ic = hdr.index(H_EMAIL), hdr.index(H_DRIVE_EMAIL)
    out, bad, seen = {}, [], {}
    for n, r in enumerate(rows[1:], start=2):
        def at(i):
            return (r[i] or "").strip() if i < len(r) else ""
        intake, recorded = at(ie), at(ic)
        if not intake or not recorded or recorded == NO_GRANT:
            continue
        if not looks_like_address(recorded):
            bad.append((n, recorded))
            continue
        ce = canon_email(intake)
        # An auto-recorded cell usually just mirrors the intake address. That is
        # not an override, and treating it as one would label ordinary rows
        # "via Drive Email" in the report for no reason.
        if canon_email(recorded) == ce:
            continue
        seen.setdefault(ce, {}).setdefault(canon_email(recorded), []).append((n, recorded))
    for ce, byaddr in seen.items():
        if len(byaddr) > 1:
            where = ", ".join("row %d %s" % (n, redact_email(v))
                              for spellings in byaddr.values() for n, v in spellings)
            print("  CONFLICT: %s has more than one %r (%s) — no override applied; "
                  "fix the rows." % (redact_email(ce), H_DRIVE_EMAIL, where),
                  file=sys.stderr)
            continue
        out[ce] = next(iter(byaddr.values()))[0][1]
    for n, v in bad:
        print("  IGNORED: row %d has a %r that is not an address (%r) — the intake "
              "address is used instead." % (n, H_DRIVE_EMAIL, v[:40]), file=sys.stderr)
    return out


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
            # merely-inherited share as granted, skip the work, and then skip
            # the verification too.
            own = True if not det else any(d.get("inherited") for d in det)
            out.append(dict(p, inherited=own))
        token = res.get("nextPageToken")
        if not token:
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

    # The reviewed `Drive Email` column, read ONCE for the whole plan: it
    # redirects a grant to the address Drive can actually honour.
    reviewed = reviewed_drive_emails()

    def target_for(person):
        """(address to grant, whether the column redirected it)."""
        alt = reviewed.get(canon_email(person["email"]))
        return (alt, True) if alt else (person["email"], False)

    grants, already_granted, already_granted_ids, stale = [], [], [], []
    for f in folders:
        want = by_folder.get(f["id"], [])
        # Read permissions for EVERY chapter, including the ones with no accepted
        # organizer. Skipping those hid their stale grants entirely — and a
        # chapter nobody is accepted for is exactly where an unexplained writer
        # is most worth seeing.
        folder_perms = perms(f["id"])
        # BOTH spellings are expected. A grant made to someone's reviewed
        # address would otherwise be reported as "held by people the intake does
        # not know about" forever — the engine's own write showing up in its
        # audit list, which is how a real finding gets lost in noise.
        expected = set()
        for x in want:
            expected.add(canon_email(x["email"]))
            expected.add(canon_email(target_for(x)[0]))
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
            addr, via_column = target_for(p)
            # Either spelling satisfies the grant: someone already granted under
            # their intake address does not need a second permission just because
            # a second address was recorded later. Whichever one actually matched
            # is what gets recorded, so verify() re-reads the right address.
            matched = next((a for a in (p["email"], addr) if canon_email(a) in have), None)
            if matched:
                already_granted.append((f["name"], matched))
                already_granted_ids.append((f["name"], f["id"], matched))
            else:
                grants.append({"chapter": f["name"], "folder_id": f["id"],
                               "email": addr, "intake_email": p["email"],
                               "via_column": via_column,
                               "name": p["name"], "role": role})

    parent = perms(CHAPTERS_PARENT)
    public = [p for p in parent if p["type"] == "anyone"]
    return {"grants": grants, "already_granted": already_granted,
            "public": public, "parent": parent, "orphans": orphans, "near": near,
            "already_granted_ids": already_granted_ids, "stale": stale, "role": role}


def report(p, role):
    print("PHASE 1 — grant each accepted organizer %r on their own chapter folder" % role)
    print("  %d new grant(s) across %d chapter(s); %d already in place."
          % (len(p["grants"]), len({g["chapter"] for g in p["grants"]}), len(p["already_granted"])))
    by_ch = {}
    for g in p["grants"]:
        # Say so when the address is not the one on the intake row: an operator
        # reading this has to be able to see that a hand-edited cell chose it.
        shown = redact_email(g["email"])
        if g.get("via_column"):
            shown += " (via %s, intake says %s)" % (H_DRIVE_EMAIL,
                                                    redact_email(g["intake_email"]))
        by_ch.setdefault(g["chapter"], []).append(shown)
    for ch in sorted(by_ch)[:6]:
        print("     %-20s %s" % (ch, ", ".join(by_ch[ch])))
    if len(by_ch) > 6:
        print("     … and %d more chapter(s)" % (len(by_ch) - 6))

    print("\nPHASE 2 — remove the public share from the Chapters folder")
    if not p["public"]:
        print("  Nothing to remove — the folder is already not link-shared.")
    for x in p["public"]:
        print("     delete permission %s (%s:%s) on Chapters/" % (x["id"], x["type"], x["role"]))
    print("  Kept on the parent:")
    for x in p["parent"]:
        if x["type"] == "anyone":
            continue
        who = redact_email(x.get("emailAddress") or "") or x.get("domain") or ""
        print("     %-10s %-10s %s%s" % (x["type"], x["role"], who,
                                         "   <- LF staff, kept by design"
                                         if who == KEEP_DOMAIN else ""))
    if p["orphans"] or p["near"]:
        print("\nAccepted organizers with no matching chapter folder (they get NO grant):")
        for o in p["orphans"]:
            print("     %-24s %s" % (o["city"], ", ".join(redact_name(x["name"]) for x in o["people"])))
        for m in p["near"]:
            print("     %-24s ~ %s" % (m["city"], ", ".join(m["candidates"])))

    if p["stale"]:
        # Direct grants held by people with no intake row. They survive the lock,
        # so a denied ex-organizer keeps write access until someone removes it
        # by hand — which requires knowing they exist.
        print("\nDirect grants held by people the intake does not know about "
              "(NOT touched; they survive the lock — audit these):")
        for ch, em, r in sorted(p["stale"]):
            print("     %-18s %-42s %s" % (ch, redact_email(em), r))
    print("\nNet effect: %d chapter-folder grant(s) at %r for accepted organizers, and "
          "the CRMs stop being readable by anyone with the link."
          % (len(p["grants"]) + len(p["already_granted"]), role))


def assert_all_accepted(grants):
    """Re-read the intake and confirm every grant target really is an accepted
    organizer, aborting on the first that isn't.

    Deliberately redundant with read_role_tab's status filter, and deliberately a
    different code path: this is the last gate before handing someone standing
    write access to a chapter, and "the filter that built the list says the list
    is fine" is not a check. Matches on email across ALL role tabs, because a
    person can hold several rows and only one of them needs to be a decision.

    A grant redirected by the `Drive Email` column is checked TWICE over, because
    that column is hand-editable and the address in it need not appear on the
    intake at all: the ACCEPTANCE is still read off the intake row (identity and
    chapter come from `Email`, never from the recorded address), and the recorded
    address is re-derived here from a fresh read rather than trusted from plan().
    Typing an address into that column can therefore only redirect a grant an
    accepted organizer row already justifies — it can never manufacture one.
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
    def identity(g):
        return canon_email(g.get("intake_email") or g["email"])

    bad = [g for g in grants
           if fold_city(g["chapter"]) not in ok.get(identity(g), set())]
    if bad:
        sys.exit("ABORT: %d grant target(s) are not accepted ORGANIZERS for the "
                 "chapter being granted — nothing was granted:\n%s"
                 % (len(bad), "\n".join(
                     "  %s -> %s (accepted organizer for: %s)"
                     % (g.get("intake_email") or g["email"], g["chapter"],
                        sorted(ok.get(identity(g), set())) or "<no accepted organizer row>")
                     for g in bad)))

    # A redirected target must still BE the recorded address, re-derived here
    # from its own read. Without this the gate checks the intake row's acceptance
    # and then grants whatever address plan() happened to attach to it.
    redirected = [g for g in grants
                  if canon_email(g["email"]) != identity(g)]
    if redirected:
        fresh = reviewed_drive_emails()
        wrong = [g for g in redirected
                 if canon_email(fresh.get(identity(g), "")) != canon_email(g["email"])]
        if wrong:
            sys.exit("ABORT: %d grant target(s) do not match the %r recorded for "
                     "that organizer on a fresh read — nothing was granted:\n%s"
                     % (len(wrong), H_DRIVE_EMAIL, "\n".join(
                         "  %s -> %s (sheet now says %r)"
                         % (g.get("intake_email"), g["chapter"],
                            fresh.get(identity(g), "") or "<nothing>")
                         for g in wrong)))
        print("  double-checked: %d target(s) redirected by the %r column still "
              "match the sheet." % (len(redirected), H_DRIVE_EMAIL))
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
            print("  granted %s -> %s (%s)" % (redact_email(g["email"]), g["chapter"], g["role"]))
            continue
        except Exception as e:
            msg = str(e)
        # Drive REFUSES to share with an address that has no Google account
        # unless it may email them — there is no silent path for these, so the
        # notification is the price of granting access at all, not a choice.
        if NO_ACCOUNT in msg and not notify:
            if not allow_mail:
                # The column is the better fix and is named first: an address
                # with a Google account behind it grants silently, where mailing
                # sends an unsolicited Drive invitation that cannot be unsent.
                failed.append((g["chapter"], g["name"], g["email"],
                               "no Google account — record an address that HAS one "
                               "in the %r column on %s and re-run; or, to invite "
                               "this address anyway, re-run with --notify "
                               "(or --mail-if-required) --i-have-approval"
                               % (H_DRIVE_EMAIL, SOURCE)))
                print("  SKIPPED %s -> %s: needs a notification email"
                      % (redact_email(g["email"]), g["chapter"]), file=sys.stderr)
                continue
            try:
                create(g, True)
                mailed.append((g["chapter"], g["email"]))
                print("  granted %s -> %s (%s, notification sent — no Google account)"
                      % (redact_email(g["email"]), g["chapter"], g["role"]))
                continue
            except Exception as e:
                msg = str(e)
        hint = ("Drive rejected the address — check it for a typo on the intake row"
                if "problem with this email" in msg else msg[:160])
        failed.append((g["chapter"], g["name"], g["email"], hint))
        print("  FAILED %s -> %s: %s" % (redact_email(g["email"]), g["chapter"], hint), file=sys.stderr)

    if mailed:
        print("\n  %d grant(s) sent a Drive notification email (unavoidable — no "
              "Google account on the address):" % len(mailed))
        for ch, em in mailed:
            print("     %-18s %s" % (ch, redact_email(em)))
    if failed:
        print("\n  %d grant(s) could not be applied — fix the intake row and re-run:"
              % len(failed))
        for ch, name, em, why in failed:
            print("     %-18s %-26s %s\n        %s" % (ch, redact_name(name), redact_email(em), why))
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

      * it verified only the PLANNED grants, so anything the plan classified
        as `already_granted` — i.e. exactly the case where the classification
        was wrong — was never checked;
      * it sampled `grants[:5]` of ninety-odd, so a systematic failure past the
        fifth passed clean;
      * it accepted ANY permission for the address, so an inherited or
        reader-level one satisfied a check for a direct `writer` grant.

    `ran` also matters: an empty plan must not print a success line claiming
    checks that iterated nothing.
    """
    bad, checked = [], 0
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


def phases_to_run(phase):
    """The grant/lock subset this invocation applies, in order.

    `--phase` names exactly one; without it, `--write` always runs grant then
    lock.
    """
    if phase:
        return [phase]
    return ["grant", "lock"]


def main():
    ap = argparse.ArgumentParser(description="Plan/apply per-chapter access for the Chapters folder.")
    ap.add_argument("--write", action="store_true", help="apply (default: report only)")
    ap.add_argument("--role", default="writer", choices=("writer", "reader", "commenter"),
                    help="role granted to each organizer on their chapter (default: writer)")
    ap.add_argument("--phase", choices=("grant", "lock"),
                    help="apply only one phase (default with --write: grant then lock)")
    ap.add_argument("--notify", action="store_true",
                    help="let Drive email EVERY organizer about their new access")
    ap.add_argument("--lock-anyway", action="store_true",
                    help="remove the public share even when some organizers could "
                         "not be granted access (they will lose all access)")
    ap.add_argument("--mail-if-required", action="store_true",
                    help="email only the organizers whose address has no Google "
                         "account, where Drive refuses to share without it")
    ap.add_argument("--i-have-approval", action="store_true",
                    help="required alongside --notify or --mail-if-required: "
                         "both make Drive email real people, which cannot be unsent")
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)
    # Emailing a real person is the line the Slack write steps already draw
    # with --i-have-approval; the same consent is required here, at parse
    # time, before plan() touches the network.
    if (a.notify or a.mail_if_required) and not a.i_have_approval:
        ap.error("--notify / --mail-if-required make Drive email real people; "
                 "add --i-have-approval to confirm a human signed off on that.")
    if a.i_have_approval and not (a.notify or a.mail_if_required):
        print("NOTE: --i-have-approval is inert without --notify or "
              "--mail-if-required; nothing will be emailed.", file=sys.stderr)

    p = plan(a.role)
    report(p, a.role)
    if not a.write:
        print("\nReport only — nothing was changed. Re-run with --write to apply.")
        # Shared engine exit convention: report mode exits 0 when in sync, 2
        # when it proposes changes (consumed by nightly.py).
        return 2 if (p["grants"] or p["public"]) else 0

    # Same rule in write mode: with nothing to grant and nothing to lock, do
    # NOT run the phases and do NOT print the "Verified:" line — nightly.py
    # reads that marker as "wrote", so printing it unconditionally would
    # label every in-sync write night "wrote+verified" and exit 2 forever.
    # The other engines' no-drift early return, in this engine's terms.
    if not a.phase and not (p["grants"] or p["public"]):
        print("\nNo changes needed — every accepted organizer holds their "
              "grant and the folder is not link-shared.")
        return 0

    selected = phases_to_run(a.phase)
    order = [("grant", apply_grants, (p, a.notify, a.mail_if_required)),
             ("lock", apply_lock, (p,))]
    grant_failures = []
    for name, fn, args in order:
        if name not in selected:
            continue
        # Removing the public share is the last thing that happens, and only if
        # every organizer actually has their own grant. Skipping a no-Google-
        # account address is the DOCUMENTED DEFAULT, so without this gate the
        # normal run locks out exactly the people it failed to grant — the
        # public link being the only access they had.
        if name == "lock" and grant_failures and not a.lock_anyway:
            sys.exit("ABORT before lock: %d organizer(s) have no grant (listed above). "
                     "Removing the public share now would leave them with NO access at "
                     "all.\nFix the intake rows or record a grantable address in the %r "
                     "column (or pass --mail-if-required "
                     "--i-have-approval), then "
                     "re-run — or pass --lock-anyway to accept locking them out."
                     % (len(grant_failures), H_DRIVE_EMAIL))
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
    # `selected` is never empty (phases_to_run returns at least ["grant", "lock"]).
    print("\nVerifying...")
    bad = verify(p, selected)
    if bad:
        print("VERIFY FAILED:")
        for b in bad:
            print("  " + b)
        return 1
    # Claim only what this run actually re-read.
    bits = []
    if "grant" in selected and (p["grants"] or p["already_granted_ids"]):
        bits.append("every accepted organizer holds their grant")
    if "lock" in selected:
        bits.append("Chapters/ is not link-shared")
    print("Verified: %s." % "; ".join(bits))
    return 0


if __name__ == "__main__":
    # sys.exit(main()), not main(): the return code is the ONLY signal a caller,
    # CI step or `&&` chain gets. Discarding it made every run — including
    # "VERIFY FAILED" after the public share was removed — exit 0.
    sys.exit(main())
