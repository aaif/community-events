#!/usr/bin/env python3
"""Unit tests for the pure/mockable logic in sync_access.py (no network/gws).

This engine grants standing write access to real people and de-publicises a
folder, so the parts that decide WHO gets access are exercised here rather than
only in production. Drive itself is mocked the same way test_sync_chapters.py
mocks the Sheets reads — `mock.patch.object(module, "helper", ...)`.
"""
import os, sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_access
from sync_access import ACCESS_TABS, canon_email

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


def aborts(fn):
    """True if fn() calls sys.exit — the script's only refusal mechanism."""
    try:
        fn()
    except SystemExit:
        return True
    return False


# ---------------------------------------------------------------------------
# ACCESS_TABS — the organizers-only rule, as a standing regression guard
# ---------------------------------------------------------------------------
# The comment above ACCESS_TABS records a privilege escalation that already
# shipped once: looping all three role tabs gave accepted speakers and hosts the
# same writer role as organizers. It is invisible in production while neither
# tab has an accepted row, so only an assertion keeps it from coming back.
check("folder access is organizers-only", ACCESS_TABS, ("Organizers",))
check("speakers and hosts are not access tabs",
      [t for t in ("Speakers", "Hosts") if t in ACCESS_TABS], [])

# sync_access reads the roster with an EMPTY interests dict — it needs who was
# accepted, not what they answered. sync_crm's "the Form Responses join is
# broken" guard must not fire for that caller, where zero matches is expected.
import sync_crm  # noqa: E402  (imported here, beside the behaviour it guards)

ORG_MIN = [["Status", "Full name", "Email", "Chapter", "City (Existing)"],
           ["Accepted", "Ada", "ada@x.io", "Boston", "Boston"]]
with mock.patch.object(sync_crm, "get_values", return_value=ORG_MIN):
    pp, _, fb = sync_crm.read_role_tab("Organizers", {})
check("an empty interests dict does not trip the broken-join guard",
      (len(pp), len(fb)), (1, 1))
with mock.patch.object(sync_crm, "get_values", return_value=ORG_MIN):
    check("a populated interests dict that matches nothing DOES abort",
          aborts(lambda: sync_crm.read_role_tab("Organizers", {"someone@else.io": "x"})),
          True)


# ---------------------------------------------------------------------------
# canon_email — match addresses the way Drive stores them
# ---------------------------------------------------------------------------
# Synthetic addresses only. These fixtures were once taken from real intake rows;
# this repo is public, so a test must never carry a community member's address.
check("gmail dots are folded",
      canon_email("first.m.last@gmail.com"), "firstmlast@gmail.com")
check("case is folded", canon_email("Mixed.Case7@GMAIL.com"), "mixedcase7@gmail.com")
check("googlemail is the same mailbox as gmail",
      canon_email("a.b@googlemail.com"), canon_email("ab@gmail.com"))
check("gmail +tags are ignored", canon_email("jane+aaif@gmail.com"), "jane@gmail.com")
check("dots are significant off gmail", canon_email("a.b@example.co"), "a.b@example.co")
check("+tags are kept off gmail",
      canon_email("a+tag@fastmail.com"), "a+tag@fastmail.com")
check("blank stays blank", canon_email(""), "")



# ---------------------------------------------------------------------------
# assert_all_accepted — the last gate before standing write access
# ---------------------------------------------------------------------------
ORG_HEADERS = ["Status", "Full name", "Timestamp", "Name", "Email", "Phone",
               "LinkedIn", "City (Existing)", "City (New)", "Chapter"]


def org_row(status, email, chapter, city=""):
    row = [""] * len(ORG_HEADERS)
    row[0] = status
    row[ORG_HEADERS.index("Email")] = email
    row[ORG_HEADERS.index("Chapter")] = chapter
    row[ORG_HEADERS.index("City (Existing)")] = city
    return row


def grant(email, chapter, intake=None):
    # The REAL shape plan() builds. The helper used to omit intake_email, so
    # every gate test exercised a fallback branch instead of the shipped one.
    return {"chapter": chapter, "folder_id": "f1", "email": email,
            "intake_email": intake or email, "via_column": bool(intake),
            "name": "X", "role": "writer"}


def gate(rows, grants):
    with mock.patch.object(sync_access, "get_values", return_value=rows), \
         mock.patch.object(sync_access, "reviewed_drive_emails", lambda: ({}, [])):
        return aborts(lambda: sync_access.assert_all_accepted(grants))


TAB = [ORG_HEADERS,
       org_row("Accepted", "ada@x.io", "Boston"),
       org_row("Existing (from MLOps)", "bo@x.io", "Berlin"),
       org_row("New", "cy@x.io", "Boston"),
       org_row("Denied", "dee@x.io", "Boston")]

check("an accepted organizer for that chapter passes",
      gate(TAB, [grant("ada@x.io", "Boston")]), False)
check("Existing (from MLOps) counts as accepted",
      gate(TAB, [grant("bo@x.io", "Berlin")]), False)
check("a New organizer is refused", gate(TAB, [grant("cy@x.io", "Boston")]), True)
check("a Denied organizer is refused", gate(TAB, [grant("dee@x.io", "Boston")]), True)
check("an address with no intake row is refused",
      gate(TAB, [grant("nobody@x.io", "Boston")]), True)
# The gate must bind person AND chapter: without this, an accepted organizer for
# one city satisfies a grant on any other, so a chapter mis-binding upstream
# (e.g. a folder renamed to collide) sails straight through the last check.
check("an accepted organizer for a DIFFERENT chapter is refused",
      gate(TAB, [grant("ada@x.io", "Berlin")]), True)
check("the chapter match is punctuation/accent folded",
      gate([ORG_HEADERS, org_row("Accepted", "ada@x.io", "Washington, DC")],
           [grant("ada@x.io", "Washington DC")]), False)
check("gmail dot spellings still match",
      gate([ORG_HEADERS, org_row("Accepted", "a.b@gmail.com", "Boston")],
           [grant("ab@gmail.com", "Boston")]), False)
check("one bad grant in a batch refuses the whole batch",
      gate(TAB, [grant("ada@x.io", "Boston"), grant("cy@x.io", "Boston")]), True)
check("a missing Status/Email/Chapter header aborts",
      gate([[h for h in ORG_HEADERS if h != "Chapter"]], [grant("ada@x.io", "Boston")]), True)


# ---------------------------------------------------------------------------
# apply_grants — the four failure branches
# ---------------------------------------------------------------------------
def run_grants(side_effects, notify=False, allow_mail=False):
    """Drive apply_grants over a scripted sequence of gws outcomes."""
    calls = []

    def fake(*args, **kw):
        calls.append(kw.get("params", {}).get("sendNotificationEmail"))
        out = side_effects[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return out

    plan = {"grants": [grant("ada@x.io", "Boston")]}
    with mock.patch.object(sync_access, "gws_json", side_effect=fake), \
         mock.patch.object(sync_access, "assert_all_accepted", lambda g: None):
        applied, failed = sync_access.apply_grants(plan, notify, allow_mail)
    return applied, failed, calls


NO_ACCT = RuntimeError('Bad Request. User message: "You are trying to invite x. '
                       'Since there is no Google account associated with this address"')
TYPO = RuntimeError('Bad Request. User message: "There\'s a problem with this email or domain."')

applied, failed, calls = run_grants([{}])
check("a normal grant applies", (applied, len(failed)), (1, 0))
check("notifications are off by default", calls, [False])

applied, failed, calls = run_grants([{}], notify=True)
check("--notify emails every grantee", calls, [True])

# Drive refuses a no-Google-account address unless it may email them. Mailing
# real people must never be a silent side effect of a sync.
applied, failed, calls = run_grants([NO_ACCT])
check("no-account without permission is skipped, not mailed", (applied, len(failed)), (0, 1))
check("no mail was sent", calls, [False])
check("the skip explains itself", "no Google account" in failed[0][3], True)

applied, failed, calls = run_grants([NO_ACCT, {}], allow_mail=True)
check("--mail-if-required retries with a notification",
      (applied, len(failed), calls), (1, 0, [False, True]))

applied, failed, _ = run_grants([TYPO])
check("a typo'd address fails without retrying", (applied, len(failed)), (0, 1))
check("the typo hint points at the intake row",
      "typo" in failed[0][3], True)

# One bad row must not abandon the rest — the shipped incident this guards.
def run_many(effects):
    plan = {"grants": [grant("a@x.io", "Boston"), grant("b@x.io", "Berlin"),
                       grant("c@x.io", "Pune")]}
    seq = list(effects)

    def fake(*args, **kw):
        out = seq.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    with mock.patch.object(sync_access, "gws_json", side_effect=fake), \
         mock.patch.object(sync_access, "assert_all_accepted", lambda g: None):
        return sync_access.apply_grants(plan, False, False)


applied, failed = run_many([TYPO, {}, {}])
check("a failure mid-batch does not abandon the remaining grants",
      (applied, len(failed)), (2, 1))


# --- verify(): the owner exception must mirror plan()'s ------------------------
# plan() counts a folder owner as already granted (Drive rejects re-granting an
# owner), so verify() demanding a direct writer grant from them would FAIL every
# night on any chapter the tree owner organizes — and loosening it to accept any
# inherited role would pass with no grant made at all.
def run_verify(perm_rows):
    p = {"grants": [],
         "already_granted_ids": [("Boston", "F1", "a@x.com")], "role": "writer"}
    with mock.patch.object(sync_access, "perms", lambda fid: perm_rows):
        return sync_access.verify(p, ["grant"])


check("an inherited owner satisfies a writer grant",
      run_verify([{"type": "user", "emailAddress": "a@x.com",
                   "role": "owner", "inherited": True}]), [])
check("a direct owner satisfies a writer grant",
      run_verify([{"type": "user", "emailAddress": "a@x.com",
                   "role": "owner", "inherited": False}]), [])
check("a merely-inherited writer does NOT count as a grant",
      len(run_verify([{"type": "user", "emailAddress": "a@x.com",
                       "role": "writer", "inherited": True}])), 1)
check("a direct commenter where writer was expected is a mismatch",
      len(run_verify([{"type": "user", "emailAddress": "a@x.com",
                       "role": "commenter", "inherited": False}])), 1)
check("a direct writer passes",
      run_verify([{"type": "user", "emailAddress": "a@x.com",
                   "role": "writer", "inherited": False}]), [])

# --- phases_to_run: --write always runs grant + lock, in order -----------------
check("a plain --write runs grant + lock",
      sync_access.phases_to_run(None), ["grant", "lock"])
check("--phase runs exactly the named phase",
      [sync_access.phases_to_run(p) for p in ("grant", "lock")],
      [["grant"], ["lock"]])


def _main_with(argv):
    with mock.patch.object(sync_access, "plan",
                           side_effect=AssertionError("plan() must not run")), \
         mock.patch.object(sys, "argv", ["sync_access.py"] + argv):
        return aborts(sync_access.main)


# --notify / --mail-if-required make Drive email real people: the same
# --i-have-approval consent the Slack write steps require, refused at parse
# time so plan() never runs without it.
check("--notify without --i-have-approval is refused",
      _main_with(["--write", "--notify"]), True)
check("--mail-if-required without --i-have-approval is refused",
      _main_with(["--write", "--mail-if-required"]), True)
check("--notify in report mode is refused too (the flag records consent, "
      "and a report never needs it)", _main_with(["--notify"]), True)


def _main_reaches_plan(argv):
    """True when parse succeeded and plan() was reached (the mock raises)."""
    with mock.patch.object(sync_access, "plan",
                           side_effect=RuntimeError("reached plan")), \
         mock.patch.object(sys, "argv", ["sync_access.py"] + argv):
        try:
            sync_access.main()
        except RuntimeError as e:
            return "reached plan" in str(e)
        except SystemExit:
            return False
    return False


check("--notify with --i-have-approval parses and proceeds",
      _main_reaches_plan(["--write", "--notify", "--i-have-approval"]), True)
check("--mail-if-required with --i-have-approval parses and proceeds",
      _main_reaches_plan(["--write", "--mail-if-required", "--i-have-approval"]), True)


# --- looks_like_address: every shape a spreadsheet cell really produces -------
# This decides what address a Drive grant is made to, so each rejection below is
# a way a hand-edited cell could hand write access to the wrong party.
lla = sync_access.looks_like_address
check("a plain address passes", lla("ada@x.io"), True)
# Alt+Enter in a Sheets cell. A `" "` check missed this, so two addresses in one
# cell passed as one and the whole blob became the grant target.
check("a NEWLINE between two addresses is refused", lla("a@x.io\nb@x.io"), False)
check("a non-breaking space is refused", lla("ada@x.io\xa0x"), False)
check("a zero-width space is refused", lla("ada@x.io\u200b"), False)
check("a comma-separated pair is refused", lla("a@x.io,b@x.io"), False)
check("the display form is refused", lla("Ada <ada@x.io>"), False)
# A homoglyph domain renders identically in the report, and under --redact the
# operator sees only a***@***.io — there is no reviewing your way past it.
check("a non-ASCII (homoglyph) domain is refused", lla("ada@\u0445.io"), False)
# A group address grants every current AND future member of a list somebody
# else administers, from one cell. Exercised through the constant rather than by
# writing the real domain into a fixture, which the PII guard rightly refuses.
_real_groups = sync_access.GROUP_DOMAINS
sync_access.GROUP_DOMAINS = ("b.io",)
try:
    check("an address at a group-hosting domain is refused", lla("team@b.io"), False)
    check("...while the same local part elsewhere is fine", lla("team@x.io"), True)
finally:
    sync_access.GROUP_DOMAINS = _real_groups
check("the shipped list names Google Groups",
      "googlegroups" in " ".join(sync_access.GROUP_DOMAINS), True)
check("the sentinel is refused", lla(sync_access.NO_GRANT), False)
check("a bare local part is refused", lla("ada"), False)
# A trailing ideographic space is normalised to a plain one and stripped, which
# is right; an INTERIOR one is a second value hiding in the cell.
check("a trailing ideographic space is normalised away, not refused",
      lla("ada@x.io\u3000"), True)
check("an interior ideographic space is refused",
      lla("ada@x.io\u3000b@x.io"), False)


# ---------------------------------------------------------------------------
# --redact: stdout masking (default on under CI)
# ---------------------------------------------------------------------------
sync_access.REDACT = False
check("redaction off: email passes through", sync_access.redact_email("ada@x.com"), "ada@x.com")
check("redaction off: name passes through", sync_access.redact_name("Ada Lovelace"), "Ada Lovelace")
sync_access.REDACT = True
try:
    check("redacted email keeps one char + TLD only", sync_access.redact_email("ada@x.com"), "a***@***.com")
    check("redacted name is a first initial", sync_access.redact_name("ada lovelace"), "A.")
    check("a non-email is left alone", sync_access.redact_email("Boston"), "Boston")
    check("the domain is hidden, not just the local part",
          "x.com" in sync_access.redact_email("ada@x.com"), False)
    check("a dotless domain shows nothing", sync_access.redact_email("ada@localhost"), "a***@***.***")
    check("empty values survive", (sync_access.redact_email(""), sync_access.redact_name("")), ("", ""))
finally:
    sync_access.REDACT = False


# --- the CI default is a real boolean, and masking announces itself ------------
import io as _io  # noqa: E402
import contextlib as _ctx  # noqa: E402
check("the CI default is the strict 1/true/yes parse of $CI", sync_access.CI_REDACT_DEFAULT,
      os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sync_access.set_redaction(True)
check("turning redaction on prints exactly one stderr line",
      (_err.getvalue().count("\n"), "redaction ON" in _err.getvalue()), (1, True))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sync_access.set_redaction(False)
check("turning redaction off is silent", _err.getvalue(), "")
check("set_redaction(False) leaves REDACT off", sync_access.REDACT, False)

# --- --i-have-approval without --notify/--mail-if-required is inert, and says so --
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    _ok = _main_reaches_plan(["--i-have-approval"])
check("a lone --i-have-approval still reaches plan (no error)", _ok, True)
# Count only the inert line: under CI=true, main() also prints "redaction ON".
check("...and exactly one stderr line says it was inert",
      sum("inert" in ln for ln in _err.getvalue().splitlines()), 1)
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    _main_reaches_plan(["--write", "--notify", "--i-have-approval"])
check("with --notify the flag is not called inert", "inert" in _err.getvalue(), False)

# --- the remediation hints name the consent flag, not just the mail flag ------
# Checked through the strings a user actually sees rather than by matching the
# source's line wrapping, which made an unrelated reflow look like a regression.
_no_acct_hint = run_grants([NO_ACCT])[1][0][3]
check("the no-account hint names the consent flag",
      "--i-have-approval" in _no_acct_hint, True)
check("...and offers the column as the fix that emails nobody",
      sync_access.H_DRIVE_EMAIL in _no_acct_hint, True)


def _lock_abort_message():
    """The abort text shown when the lock phase is refused after a failed grant."""
    p_ = {"grants": [grant("ada@x.io", "Boston")], "public": [{"id": "p1"}],
          "already_granted": [], "already_granted_ids": [], "role": "writer"}
    with mock.patch.object(sync_access, "plan", lambda role: p_), \
         mock.patch.object(sync_access, "report", lambda *a: None), \
         mock.patch.object(sync_access, "apply_grants",
                           lambda *a: (0, [("Boston", "X", "ada@x.io", "nope")])), \
         mock.patch.object(sync_access, "apply_lock",
                           side_effect=AssertionError("lock must not run")), \
         mock.patch.object(sys, "argv", ["sync_access.py", "--write"]):
        try:
            sync_access.main()
        except SystemExit as e:
            return str(e.code)
    return ""


_abort = _lock_abort_message()
check("the lock refusal names the consent flag", "--i-have-approval" in _abort, True)
check("...and points at the column first",
      sync_access.H_DRIVE_EMAIL in _abort, True)
check("...and still offers --lock-anyway", "--lock-anyway" in _abort, True)


# ---------------------------------------------------------------------------
# reviewed_drive_emails — the column that redirects a grant
# ---------------------------------------------------------------------------
# This column decides WHICH ADDRESS receives standing write access to a chapter
# folder, and a human types into it. Every check here is a way of turning a
# hand-edited cell into the wrong grant.
RDE_HDR = ["Full name", "Email", "Drive Email"]


def rde(*pairs):
    rows = [RDE_HDR] + [["Someone", e, d] for e, d in pairs]
    with mock.patch.object(sync_access, "get_values", return_value=rows):
        return sync_access.reviewed_drive_emails()[0]


check("a recorded address overrides the intake one",
      rde(("ada@x.io", "b@x.io")), {"ada@x.io": "b@x.io"})
check("the key is canonical, so a gmail-dot intake row still matches",
      rde(("a.b@gmail.com", "b@x.io")), {"ab@gmail.com": "b@x.io"})
# `(no grant)` is track_drive_email's own sentinel for "nobody granted them".
# Granting it would be nonsense; treating it as an override would be worse.
check("the (no grant) sentinel is not an address", rde(("ada@x.io", "(no grant)")), {})
check("a blank cell is no override", rde(("ada@x.io", "")), {})
check("a cell restating the intake address is no override",
      rde(("ada@x.io", "ada@x.io")), {})
check("a differently-SPELLED but identical gmail is no override",
      rde(("a.b@gmail.com", "ab@gmail.com")), {})
check("free text is ignored, not granted", rde(("ada@x.io", "ask her")), {})
check("two addresses in one cell are ignored",
      rde(("ada@x.io", "a@x.io, b@x.io")), {})
check("a bare local part with no domain is ignored", rde(("ada@x.io", "ada")), {})
# One person, several rows, two different answers: picking one is the guess
# that the estate's identity bugs are made of.
check("rows disagreeing about a person drop the override entirely",
      rde(("ada@x.io", "b@x.io"), ("ada@x.io", "c@x.io")), {})
check("rows agreeing (in any spelling) keep it",
      rde(("ada@x.io", "a.b@gmail.com"), ("ada@x.io", "ab@gmail.com")),
      {"ada@x.io": "a.b@gmail.com"})
with mock.patch.object(sync_access, "get_values",
                       return_value=[["Full name", "Email"], ["Someone", "ada@x.io"]]):
    got, probs = sync_access.reviewed_drive_emails()
    check("a missing Drive Email column is not an error", got, {})
    check("...but it says so, rather than looking like an empty column",
          len(probs), 1)


# ---------------------------------------------------------------------------
# plan() — the column redirects the grant, and both spellings satisfy it
# ---------------------------------------------------------------------------
def run_plan(folder_perms, people, reviewed):
    with mock.patch.object(sync_access, "list_chapter_folders",
                           lambda: [{"id": "F1", "name": "Boston"}]), \
         mock.patch.object(sync_access, "read_role_tab",
                           lambda tab, x: (people, [], [])), \
         mock.patch.object(sync_access, "merge_people", lambda p: p), \
         mock.patch.object(sync_access, "match_chapters",
                           lambda ppl, folders: ({"F1": ppl}, [], [])), \
         mock.patch.object(sync_access, "reviewed_drive_emails",
                           lambda: (reviewed, [])), \
         mock.patch.object(sync_access, "perms",
                           lambda fid: folder_perms if fid == "F1" else []):
        return sync_access.plan("writer")


ADA = [{"email": "ada@x.io", "name": "Ada"}]


def held(email, inherited=False, role="writer"):
    return {"type": "user", "emailAddress": email, "role": role,
            "inherited": inherited}


p_ = run_plan([], ADA, {"ada@x.io": "b@x.io"})
check("the grant goes to the recorded address, not the intake one",
      [(g["email"], g["intake_email"]) for g in p_["grants"]],
      [("b@x.io", "ada@x.io")])
check("and it is flagged as coming from the column",
      p_["grants"][0]["via_column"], True)

p_ = run_plan([], ADA, {})
check("with no recorded address the intake one is granted",
      [g["email"] for g in p_["grants"]], ["ada@x.io"])
check("and it is not flagged", p_["grants"][0]["via_column"], False)

# A RECORDED address is an instruction, not a tiebreak. Someone already granted
# under an OLD address — the "changed Google accounts" case, which is the main
# thing the column is for — must be granted the recorded one, or the operator's
# entry wins nowhere: sync_access skipped it as already-granted and
# track_drive_email then wrote the old ACL spelling back over the cell.
p_ = run_plan([held("ada@x.io")], ADA, {"ada@x.io": "b@x.io"})
check("a recorded address is granted even when an older grant exists",
      [(g["email"], g["intake_email"]) for g in p_["grants"]],
      [("b@x.io", "ada@x.io")])
check("...and the older grant is named as superseded, not silently left",
      [(c, o, n) for c, o, n in p_["superseded"]],
      [("Boston", "ada@x.io", "b@x.io")])
check("...and it is NOT double-counted as already granted",
      p_["already_granted"], [])

# Once the recorded address IS on the ACL, there is nothing left to do.
p_ = run_plan([held("b@x.io")], ADA, {"ada@x.io": "b@x.io"})
check("a grant already held under the recorded address satisfies it",
      (p_["grants"], [e for _c, e in p_["already_granted"]]), ([], ["b@x.io"]))
check("...and verify is pointed at the address that actually matched",
      [e for _c, _f, e in p_["already_granted_ids"]], ["b@x.io"])

p_ = run_plan([held("b@x.io")], ADA, {"ada@x.io": "b@x.io"})
check("a grant under the recorded address satisfies it too",
      (p_["grants"], [e for _c, e in p_["already_granted"]]),
      ([], ["b@x.io"]))
# The engine's own write must not come back as a finding in its own audit list.
check("...and does NOT read as a stranger's grant", p_["stale"], [])

p_ = run_plan([held("someone.else@x.io")], ADA, {"ada@x.io": "b@x.io"})
check("an unrelated direct grant is still reported as stale",
      [e for _c, e, _r in p_["stale"]], ["someone.else@x.io"])


# ---------------------------------------------------------------------------
# assert_all_accepted — a redirected target is checked twice over
# ---------------------------------------------------------------------------
def redirected(intake, target):
    return {"chapter": "Boston", "folder_id": "f1", "email": target,
            "intake_email": intake, "via_column": True, "name": "X",
            "role": "writer"}


def gate_redirect(rows, grants, reviewed):
    with mock.patch.object(sync_access, "get_values", return_value=rows), \
         mock.patch.object(sync_access, "reviewed_drive_emails",
                           lambda: (reviewed, [])):
        return aborts(lambda: sync_access.assert_all_accepted(grants))


# The authority comes from the INTAKE row; the column only redirects where the
# grant lands. An address that appears nowhere on the intake is fine as a
# target, and would be refused outright as an identity.
check("a redirected grant passes when the intake row is accepted",
      gate_redirect(TAB, [redirected("ada@x.io", "b@x.io")],
                    {"ada@x.io": "b@x.io"}), False)
check("a redirected grant for a NON-accepted intake row is still refused",
      gate_redirect(TAB, [redirected("cy@x.io", "d@x.io")],
                    {"cy@x.io": "d@x.io"}), True)
check("a redirected grant for the wrong chapter is still refused",
      gate_redirect(TAB, [dict(redirected("ada@x.io", "b@x.io"),
                               chapter="Berlin")],
                    {"ada@x.io": "b@x.io"}), True)
# The gate re-reads the column itself: trusting plan()'s attachment would mean
# the acceptance is checked and then some other address is granted.
check("a target the sheet no longer records is refused",
      gate_redirect(TAB, [redirected("ada@x.io", "b@x.io")],
                    {"ada@x.io": "c@x.io"}), True)
check("a target with no recorded address at all is refused",
      gate_redirect(TAB, [redirected("ada@x.io", "b@x.io")], {}), True)
check("a gmail-dot respelling of the recorded address still passes",
      gate_redirect(TAB, [redirected("ada@x.io", "a.b@gmail.com")],
                    {"ada@x.io": "ab@gmail.com"}), False)

# --- the gate refuses a redirect aimed at someone the intake REFUSED ---------
# The acceptance check keys on the intake row, so it bounds how many grants
# exist and for which chapter — it says nothing about who the recorded address
# belongs to. Without this, one cell edit points an accepted organizer's grant
# at a Denied applicant.
DENIED_TAB = [ORG_HEADERS,
              org_row("Accepted", "ada@x.io", "Boston"),
              org_row("Denied", "dee@x.io", "Boston")]
check("a redirect to a Denied person's address is refused",
      gate_redirect(DENIED_TAB, [grant("dee@x.io", "Boston", intake="ada@x.io")],
                    {"ada@x.io": "dee@x.io"}), True)
check("a redirect to a New/pending person's address is refused",
      gate_redirect([ORG_HEADERS, org_row("Accepted", "ada@x.io", "Boston"),
                     org_row("New", "cy@x.io", "Boston")],
                    [grant("cy@x.io", "Boston", intake="ada@x.io")],
                    {"ada@x.io": "cy@x.io"}), True)
# An address on NO intake row is still allowed — that is the feature: a personal
# Google account the public form never saw.
check("a redirect to an address the intake has never seen is allowed",
      gate_redirect(DENIED_TAB, [grant("b@x.io", "Boston", intake="ada@x.io")],
                    {"ada@x.io": "b@x.io"}), False)



# --- redaction through the whole report: no fixture email or full name survives --
_plan = {"already_granted": [],
         "grants": [{"chapter": "Boston", "email": "ada@x.com",
                     "name": "Ada Lovelace", "role": "writer"}],
         "public": [], "near": [],
         "parent": [{"type": "user", "role": "writer", "emailAddress": "grace@x.com"}],
         "orphans": [{"city": "Pune", "people": [{"name": "Grace Hopper"}]}],
         "stale": [("Berlin", "ada@x.com", "writer")]}
_out = _io.StringIO()
sync_access.REDACT = True
try:
    with _ctx.redirect_stdout(_out):
        sync_access.report(_plan, "writer")
finally:
    sync_access.REDACT = False
_text = _out.getvalue()
check("redacted report carries no fixture email",
      [w for w in ("ada@x.com", "grace@x.com", "x.com") if w in _text], [])
check("redacted report carries no full name",
      [w for w in ("Ada Lovelace", "Lovelace", "Grace Hopper", "Hopper") if w in _text], [])
check("the redacted report still names the chapter", "Boston" in _text, True)
print()
print("FAILED %d check(s)" % fails if fails else "All checks passed.")
sys.exit(1 if fails else 0)
