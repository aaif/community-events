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
from sync_access import ACCESS_TABS, banner_ids, canon_email

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
# banner_ids — city key and URL parsing
# ---------------------------------------------------------------------------
FEED_HEADERS = ["Title", "City", "Country", "Generated Geolocation", "Summary",
                "Image", "CTA", "URL for CTA", "Organizers", "Chapter Luma Link",
                "MLOps Community Organizers"]
ID_A, ID_B = "1usAa88CR88_XUlMp35tFoF2OIv1mf3IO", "1Q-OzWVYW1lzjI_Hlu6QvY8bNBZYXlodH"


def feed_row(city, image):
    row = [""] * len(FEED_HEADERS)
    row[FEED_HEADERS.index("City")] = city
    row[FEED_HEADERS.index("Image")] = image
    return row


def banners_over(rows):
    with mock.patch.object(sync_access, "get_values", return_value=rows):
        return banner_ids()


got = banners_over([FEED_HEADERS,
                    feed_row("Washington, DC", "https://lh3.googleusercontent.com/d/" + ID_A),
                    feed_row("Montréal", "https://drive.google.com/uc?id=" + ID_B),
                    feed_row("Nowhere", "not a drive url"),
                    feed_row("", "https://lh3.googleusercontent.com/d/" + ID_A)])
# fold_city, not fold: plain fold leaves punctuation intact, so the feed's
# 'Washington, DC' missed the 'Washington DC' folder and the chapter was
# reported as having no image at all — then never pinned, then locked.
check("city key is punctuation-folded", "washington dc" in got, True)
check("city key is accent-folded", "montreal" in got, True)
check("the /d/<id> form parses", got["washington dc"]["file_id"], ID_A)
check("the ?id=<id> form parses", got["montreal"]["file_id"], ID_B)
check("an unrecognised URL yields no id, not a bogus one",
      got["nowhere"]["file_id"], "")
check("a row with no city is skipped", len(got), 3)
check("the original city spelling is preserved for the report",
      got["washington dc"]["city"], "Washington, DC")

check("a missing Image column aborts rather than planning a lock",
      aborts(lambda: banners_over([[h for h in FEED_HEADERS if h != "Image"]])), True)
check("an empty feed aborts", aborts(lambda: banners_over([])), True)

# A trailing-fragment URL must not produce a truncated-but-plausible id.
frag = banners_over([FEED_HEADERS,
                     feed_row("Boston", "https://lh3.googleusercontent.com/d/%s#gid=1" % ID_A)])
check("a URL fragment does not corrupt the id", frag["boston"]["file_id"], ID_A)


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


def grant(email, chapter):
    return {"chapter": chapter, "folder_id": "f1", "email": email,
            "name": "X", "role": "writer"}


def gate(rows, grants):
    with mock.patch.object(sync_access, "get_values", return_value=rows):
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
    p = {"grants": [], "pins": [], "already_pinned_ids": [],
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

# --- phases_to_run: pinning is a standing human decision, never a default -------
# nightly.py passes only --write, so the unattended path must never publish a
# banner to the internet as a side effect of syncing grants.
check("a plain --write runs grant + lock only",
      sync_access.phases_to_run(None, False), ["grant", "lock"])
check("--pins adds the pin phase, first",
      sync_access.phases_to_run(None, True), ["pin", "grant", "lock"])
check("--phase pin is explicit consent on its own",
      sync_access.phases_to_run("pin", False), ["pin"])
check("--phase runs exactly the named phase",
      [sync_access.phases_to_run(p, False) for p in ("grant", "lock")],
      [["grant"], ["lock"]])


# --- the flag combinations that used to be silently inert ----------------------
# `--phase X --pins` discarded --pins (phases_to_run returns [phase]), and
# `--pins` without `--write` did nothing at all — the worst behaviours for a
# flag whose whole job is recording explicit consent. Both must now refuse at
# parse time, before plan() touches the network (asserted by the plan mock).
def _main_with(argv):
    with mock.patch.object(sync_access, "plan",
                           side_effect=AssertionError("plan() must not run")), \
         mock.patch.object(sys, "argv", ["sync_access.py"] + argv):
        return aborts(sync_access.main)


check("--phase with --pins is refused loudly, never discarded",
      _main_with(["--write", "--phase", "grant", "--pins"]), True)
check("--phase pin with --pins is refused too — one spelling per consent",
      _main_with(["--write", "--phase", "pin", "--pins"]), True)
check("--pins without --write is refused, not silently inert",
      _main_with(["--pins"]), True)

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
check("...and one stderr line says it was inert",
      ("inert" in _err.getvalue(), _err.getvalue().count("\n")), (True, 1))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    _main_reaches_plan(["--write", "--notify", "--i-have-approval"])
check("with --notify the flag is not called inert", "inert" in _err.getvalue(), False)

# --- the remediation hints name the consent flag, not just the mail flag ------
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_access.py"),
            encoding="utf-8").read()
check("both remediation hints name the consent flag",
      (_src.count("(or --mail-if-required) \"\n                               \"--i-have-approval\""),
       _src.count("(or pass --mail-if-required \"\n                     \"--i-have-approval)")), (1, 1))

# --- redaction through the whole report: no fixture email or full name survives --
_plan = {"pins": [], "already_pinned": [], "no_banner": [], "already_granted": [],
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
