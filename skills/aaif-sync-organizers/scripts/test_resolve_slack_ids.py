#!/usr/bin/env python3
"""Self-tests for the Slack-id resolver's pure logic. No network, no gws.

The covered parts are the ones that fail *quietly*. A broken lookup dies on the
next line; a wrong id written into `Slack ID` does not — it is the key the
invite and audit paths act on later, so a mistyped or mis-suggested one ends
with a stranger in a private organizers channel and nothing in the log to say
why. Hence: the id shape guard, the all-or-nothing `--apply` validation, and the
deliberate narrowness of the name matcher.
"""

import json
import os
import sys
import tempfile
import contextlib
import io
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import resolve_slack_ids as r  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def user(uid, real, handle="h", email="", **flags):
    base = {"id": uid, "real_name": real, "name": handle, "email": email,
            "deleted": False, "is_bot": False, "is_app_user": False}
    base.update(flags)
    return base


# ---------- id shape ----------
for good in ("U0AAAAAAAAA", "W0AAAAAAAAA", "U02BBBBBBBB"):
    check("%s is a Slack id" % good, bool(r.SLACK_ID_RE.match(good)), True)
for bad in ("u0aaaaaaaaa", "C0AAAAAAAAA", "U0A", "", "U0AA AAA", "=U0AAAAAAAAA",
            "@handle7", "ada@x.com"):
    check("%r is refused as an id" % bad, bool(r.SLACK_ID_RE.match(bad)), False)

# ---------- column letters ----------
check("column 1 is A", r.colletter(1), "A")
check("column 26 is Z", r.colletter(26), "Z")
check("column 27 is AA", r.colletter(27), "AA")
check("column 90 is CL", r.colletter(90), "CL")

# ---------- name key ----------
check("name key ignores order and case",
      r.name_key("Ada Lovelace"), r.name_key("lovelace, ada"))
check("name key strips accents", r.name_key("Adá Lovelace"), r.name_key("Ada Lovelace"))
check("name key drops a title", r.name_key("Dr Ada Lovelace"), r.name_key("Ada Lovelace"))
check("different people do not share a key",
      r.name_key("Ada Lovelace") == r.name_key("Ada Byron"), False)

# ---------- suggest: full name only, never a token coincidence ----------
DIR = [
    user("U1", "Ada Lovelace", "ada", "ada@x.com"),
    user("U2", "Ada Byron", "byron", "byron@x.com"),
    # Shares one distinctive token AND the handle equals the intake local part —
    # the exact shape that produced a confident wrong pairing in testing.
    user("U3", "Ada Other", "ada.lovelace", "other@x.com"),
    user("U4", "Ada Lovelace", "ada2", "ada2@x.com"),
    user("U5", "Ada Lovelace", "gone", "gone@x.com", deleted=True),
    user("U6", "Ada Lovelace", "bot", "bot@x.com", is_bot=True),
]
got = r.suggest([(5, "Ada Lovelace", "ada.lovelace@x.com")], DIR)
check("suggests every live full-name match", sorted(u["id"] for u in got[5]), ["U1", "U4"])
check("a deleted account is never suggested", "U5" in [u["id"] for u in got[5]], False)
check("a bot is never suggested", "U6" in [u["id"] for u in got[5]], False)
check("a one-token/handle coincidence is NOT suggested",
      "U3" in [u["id"] for u in got[5]], False)
check("no candidates means no entry at all",
      r.suggest([(9, "Grace Hopper", "g@x.com")], DIR), {})
check("a mononym never matches (key needs two tokens)",
      r.suggest([(9, "Ada", "a@x.com")], DIR), {})

# ---------- apply_reviewed: validate everything, write all or nothing ----------
def apply_file(entries, known=(5, 6, 7)):
    """Run apply_reviewed over `entries`; return (exit_code_or_None, written)."""
    written = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(entries, fh)
        path = fh.name
    try:
        with mock.patch.object(r, "write_cells", lambda ci, pairs: written.extend(pairs)):
            try:
                r.apply_reviewed(path, 89, 90, set(known))
            except SystemExit as exc:
                return exc.code, written
        return None, written
    finally:
        os.unlink(path)

code, written = apply_file([{"row": 5, "slack_id": "U0AAAAAAAAA"}])
check("a valid entry is written", written, [(5, "U0AAAAAAAAA")])
check("a valid entry does not abort", code, None)

code, written = apply_file([{"row": 5, "slack_id": "U0AAAAAAAAA"},
                            {"row": 6, "slack_id": "@handle"}])
check("one bad id writes NOTHING at all", written, [])
check("one bad id aborts", isinstance(code, str), True)

code, written = apply_file([{"row": 999, "slack_id": "U0AAAAAAAAA"}])
check("a row outside the data rows writes nothing", written, [])
check("a row outside the data rows aborts", isinstance(code, str), True)

code, written = apply_file([{"row": "5", "slack_id": "U0AAAAAAAAA"}])
check("a string row number is refused (never indexed)", written, [])

code, written = apply_file([{"row": 5}])
check("a missing id is refused, not written as blank", written, [])

# ---------- collect ----------
HDR = ["Full name", "Email", "Slack ID", "Slack Email"]
ROWS = [["Ada", "ada@x.com", "U1", "ada@slack"], ["Bo", "", "", ""],
        ["Cy", "cy@x.com", "", ""]]
check("collect skips rows with no email and reports both owned columns",
      r.collect(HDR, ROWS, 2, 3),
      [(2, "Ada", "ada@x.com", "U1", "ada@slack"), (4, "Cy", "cy@x.com", "", "")])
check("a short row does not IndexError", r.collect(HDR, [["Dee", "d@x.com"]], 2, 3),
      [(2, "Dee", "d@x.com", "", "")])

# ---------- ensure_columns ----------
# Two NEW columns must not be handed the same index. Computing each off the
# original len(hdr) gives both the same letter and the second write silently
# overwrites the first.
calls = []
with mock.patch.object(r, "gws", lambda args: calls.append(args)):
    got = r.ensure_columns(["A", "B"], ("Slack ID", "Slack Email"), create=True)
check("two new columns get distinct indexes", got, {"Slack ID": 2, "Slack Email": 3})
check("each new column header is written once", len(calls), 2)

with mock.patch.object(r, "gws", lambda args: calls.append("SHOULD NOT HAPPEN")):
    got = r.ensure_columns(["A", "Slack ID", "B"], ("Slack ID",), create=True)
check("an existing column is found, not recreated", got, {"Slack ID": 1})

before = len(calls)
with mock.patch.object(r, "gws", lambda args: calls.append("SHOULD NOT HAPPEN")):
    got = r.ensure_columns(["A"], ("Slack ID", "Slack Email"), create=False)
check("create=False touches the sheet not at all", len(calls), before)
check("create=False still reports where they would go",
      got, {"Slack ID": 1, "Slack Email": 2})

# ---------- known_ids / overlay_known ----------
SRC_HDR = ["Full name", "Email", "Slack ID", "Slack Email"]
SRC_ROWS = [
    ["Ada", "a.b@gmail.com", "U0AAAAAAA", "ab@slack.com"],   # gmail-dotted key
    ["Bo", "bo@x.com", "", ""],                              # no id yet
    ["Cy", "cy@x.com", "@handle", ""],                       # not an id -> skipped
    ["Dee", "", "U0DDDDDDD", ""],                            # no email -> skipped
]
with mock.patch.object(r, "read_source", lambda: (SRC_HDR, SRC_ROWS)):
    km = r.known_ids()
check("known_ids keys on the Gmail-canonical spelling", sorted(km), ["ab@gmail.com"])
check("known_ids carries the id and slack email",
      km["ab@gmail.com"], {"id": "U0AAAAAAA", "slack_email": "ab@slack.com"})

with mock.patch.object(r, "read_source", lambda: (["Full name", "Email"], [])):
    check("no column means no answers, not a crash", r.known_ids(), {})


class FakeApi:
    """Minimal users.info stand-in."""

    def __init__(self, users):
        self.users = users
        self.calls = []

    def call(self, method, **kw):
        self.calls.append((method, kw))
        u = self.users.get(kw.get("user"))
        return {"ok": True, "user": u} if u else {"ok": False, "error": "user_not_found"}


DIRECTORY = {"U0AAAAAAA": {"name": "ada", "real_name": "Ada Lovelace",
                           "profile": {"email": "ab@slack.com"}},
             "U0GONEGONE": {"name": "gone", "real_name": "Gone", "deleted": True,
                            "profile": {}}}

with mock.patch.object(r, "read_source", lambda: (SRC_HDR, SRC_ROWS)):
    api = FakeApi(DIRECTORY)
    resolved = {"a.b@gmail.com": {"id": None, "error": "users_not_found"},
                "bo@x.com": {"id": None, "error": "users_not_found"}}
    filled, conflicts = r.overlay_known(api, resolved)
check("a miss covered by the column is filled", filled, 1)
check("the filled entry carries the handle", resolved["a.b@gmail.com"]["name"], "ada")
check("the filled entry is marked as coming from the column",
      resolved["a.b@gmail.com"]["from_column"], True)
check("a miss the column cannot cover stays a miss", resolved["bo@x.com"]["id"], None)
check("no conflicts when nothing resolved live", conflicts, [])

# A live hit must OUTRANK the column, and disagreement is reported not hidden.
with mock.patch.object(r, "read_source", lambda: (SRC_HDR, SRC_ROWS)):
    api = FakeApi(DIRECTORY)
    resolved = {"a.b@gmail.com": {"id": "U0LIVELIVE", "name": "live"}}
    filled, conflicts = r.overlay_known(api, resolved)
check("a live hit is never overwritten by the column",
      resolved["a.b@gmail.com"]["id"], "U0LIVELIVE")
check("nothing is filled when the live lookup already hit", filled, 0)
check("the disagreement is reported", conflicts,
      [("a.b@gmail.com", "U0LIVELIVE", "U0AAAAAAA")])
check("users.info is not called when there is no gap", api.calls, [])

# A deactivated account is not a usable identity.
with mock.patch.object(r, "read_source",
                       lambda: (SRC_HDR, [["Z", "z@x.com", "U0GONEGONE", ""]])):
    api = FakeApi(DIRECTORY)
    resolved = {"z@x.com": {"id": None, "error": "users_not_found"}}
    filled, _ = r.overlay_known(api, resolved)
check("a deleted account never fills a miss", filled, 0)
check("the deleted account leaves the miss intact", resolved["z@x.com"]["id"], None)

def _raises(fn):
    """The exception type name, or "" — checks read better than try/except."""
    try:
        fn()
    except BaseException as exc:
        return type(exc).__name__
    return ""


# --- a filled-but-wrong cell, and two rows that disagree, are REPORTED -------
# Both used to be dropped in silence: the human had done the review, the cell
# looked answered, and the person stayed "no Slack account" forever.
_BAD_HDR = ["Full name", "Email", "Slack ID", "Slack Email"]
_BAD_ROWS = [
    ["Ada", "ada@x.com", "u0aaaaaaa", ""],      # lowercase paste — not an id
    ["Bo", "bo@x.com", "@handle", ""],          # a handle, not an id
]
_err = io.StringIO()
with mock.patch.object(r, "read_source", lambda: (_BAD_HDR, _BAD_ROWS)), \
     contextlib.redirect_stderr(_err):
    _km = r.known_ids()
check("a malformed reviewed cell yields no answer", _km, {})
check("...and says so, naming the row", "row 2" in _err.getvalue(), True)
check("...and calls out the column", "Slack ID" in _err.getvalue(), True)

_DUP_ROWS = [
    ["Ada", "a.b@gmail.com", "U0AAAAAAA", ""],
    ["Ada again", "ab@gmail.com", "U0BBBBBBB", ""],   # same person, other id
]
_err = io.StringIO()
with mock.patch.object(r, "read_source", lambda: (_BAD_HDR, _DUP_ROWS)), \
     contextlib.redirect_stderr(_err):
    _km = r.known_ids()
check("two reviewed rows naming different ids resolve to neither", _km, {})
check("...and the disagreement is reported",
      "DIFFERENT" in _err.getvalue(), True)

_SAME_ROWS = [
    ["Ada", "a.b@gmail.com", "U0AAAAAAA", ""],
    ["Ada again", "ab@gmail.com", "U0AAAAAAA", "ab@slack.com"],   # agreeing
]
with mock.patch.object(r, "read_source", lambda: (_BAD_HDR, _SAME_ROWS)):
    check("two rows AGREEING still resolve", r.known_ids()["ab@gmail.com"]["id"],
          "U0AAAAAAA")


# --- hydrate: an API failure is never a fact about a person -----------------
class _FailingApi:
    def __init__(self, error):
        self.error = error

    def call(self, method, **kw):
        return {"ok": False, "error": self.error}


check("a missing scope raises rather than reporting people as accountless",
      _raises(lambda: r.hydrate(_FailingApi("missing_scope"), ["U0AAAAAAA"])),
      "SlackError")
check("a genuine absence is not an error",
      r.hydrate(_FailingApi("user_not_found"), ["U0AAAAAAA"]), {})



# --- --apply is a write, and writes are gated by --write ----------------------
# It used to write on its own, so "does this invocation touch the sheet?" could
# not be answered by reading the command line — which is exactly how every other
# script in this skill, and the SKILL.md documenting them, expects it to work.
def _main_with(argv):
    """Run main() with argv, returning (SystemExit code-or-message, ran?)."""
    ran = {}
    with mock.patch.object(sys, "argv", ["resolve_slack_ids.py"] + argv), \
         mock.patch.object(r, "run", lambda **kw: ran.setdefault("kw", kw)):
        try:
            r.main()
        except SystemExit as exc:
            return exc.code, "kw" in ran
    return None, "kw" in ran


_code, _ran = _main_with(["--apply", "ids.json"])
check("--apply without --write refuses", isinstance(_code, str) and "REFUSING" in _code, True)
check("--apply without --write does not reach run()", _ran, False)
check("the refusal shows the corrected command",
      "--apply ids.json --write" in (_code or ""), True)

_code, _ran = _main_with(["--apply", "ids.json", "--write"])
check("--apply with --write proceeds", _ran, True)

_code, _ran = _main_with([])
check("a bare run still reports without writing", _ran, True)


# --- --json-out: the same report as data --------------------------------------
# The engine redacts by default when CI is set; these checks assert the
# unmasked names, so pin redaction off for them.
from aaif_events import redact as _redact_mod  # noqa: E402
_redact_mod.REDACT = False
# One row per human call, the row as the subject, never an address. The
# candidate name reaches `detail` only because the text report already prints
# it (and masked the same way when --redact is on).
_hits = [(2, "U0AAAAAAAAA"), (3, "U02BBBBBBBB")]
_folded = [(3, "Ada Lovelace", "a.da@x.com", "ada@x.com")]
_misses = [(4, "Grace Hopper", "g@x.com"), (5, "Bo Lin", "b@x.com")]
_cands = {4: [user("U0AAAAAAAAA", "Grace Hopper")]}
_doc = r.build_findings(9, _hits, _folded, _misses, _cands, "report").to_dict()
check("findings carry the shared format", _doc["format"], 1)
check("findings name the runner's step", _doc["step"], "identity")
check("findings carry the mode", _doc["mode"], "report")
check("the summary is the text report's headline",
      _doc["summary"], "4 row(s) to resolve: 2 resolved by email, 2 unresolved")
check("nothing is written in report mode", _doc["written"], False)
_tiles = {m["label"]: m["value"] for m in _doc["measured"]}
check("tiles: resolved / suggestions / no account",
      (_tiles["resolved by email"], _tiles["name-match suggestions"],
       _tiles["no account at the address"]), (2, 1, 2))
check("already-resolved is what the header line prints", _tiles["already resolved"], 5)
_rows = {(f["kind"], f["subject"]): f for f in _doc["findings"]}
check("a name-match suggestion is a warn on its row",
      _rows[("name-match suggestion", "row 4")]["severity"], "warn")
check("the suggestion names the person, as the text report does",
      _rows[("name-match suggestion", "row 4")]["detail"].startswith("Grace Hopper"), True)
check("a Gmail-spelling hit is a finding on its row",
      _rows[("gmail spelling", "row 3")]["action"], "fix the intake spelling")
check("the no-account count is ONE finding, not one per person",
      sum(1 for f in _doc["findings"] if f["kind"] == "no account"), 1)
check("no finding subject is an address",
      [f["subject"] for f in _doc["findings"] if "@" in f["subject"]], [])
check("no address reaches a finding at all",
      [f for f in _doc["findings"] if "@" in f["detail"]], [])
_none = r.build_findings(3, [], [], [], {}, "write").to_dict()
check("an empty run still lands a summary and tiles",
      (bool(_none["summary"]), len(_none["measured"]) >= 3, _none["findings"]),
      (True, True, []))
with tempfile.TemporaryDirectory() as _td:
    _path = os.path.join(_td, "identity.json")
    r.build_findings(9, _hits, _folded, _misses, _cands, "report").write(_path)
    with open(_path) as fh:
        check("write() lands the same document", json.load(fh), _doc)
    check("the file is private to the operator", oct(os.stat(_path).st_mode & 0o777), "0o600")
check("run() with no --json-out writes nothing",
      r.findings.Report("identity").write(None), None)
with tempfile.TemporaryDirectory() as _td:
    _code, _ran = _main_with(["--json-out", os.path.join(_td, "x.json")])
check("--json-out is accepted and reaches run()", _ran, True)


# --- main() lands the findings file on a normal exit, never on an abort -------
# The runner reads `written` from this file as its "wrote" signal, so the
# file has to land on every normal exit, in report and write mode alike, and
# must NOT land when run() aborts or main() refuses. The sheet and Slack are
# mocked at the module's own boundaries; run() itself is real.
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", ".."))
HDR4 = ["Full name", "Email", "Slack ID", "Slack Email"]
ROWS4 = [["Ada", "ada@x.com", "", ""], ["Bo", "bo@x.com", "", ""]]
RESOLVED4 = {"ada@x.com": {"id": "U0AAAAAAAAA"}, "bo@x.com": {"id": None}}


class _MainApi:
    def ok(self, method, **kw):
        assert method == "auth.test"
        return {"team": "T", "team_id": "T1"}


def _main_json(argv, rows=ROWS4, resolved=RESOLVED4, json_out=None):
    """Run main() for real over a mocked sheet and Slack.

    Returns (SystemExit code-or-message or None, the findings doc or None,
    the cells write_cells() was handed)."""
    written = []

    def lookup(api, emails):
        if isinstance(resolved, Exception):
            raise resolved
        return resolved

    with tempfile.TemporaryDirectory() as td:
        path = json_out or os.path.join(td, "identity.json")
        with mock.patch.object(r, "read_source", lambda: (list(HDR4), [list(x) for x in rows])), \
             mock.patch.object(r, "load_token", lambda *a, **k: "xoxp-test"), \
             mock.patch.object(r, "Slack", lambda token=None: _MainApi()), \
             mock.patch.object(r, "lookup_emails", lookup), \
             mock.patch.object(r, "write_cells",
                               lambda ci, pairs: written.append((ci, list(pairs)))), \
             mock.patch.object(sys, "argv", ["resolve_slack_ids.py", "--json-out", path] + argv), \
             contextlib.redirect_stdout(io.StringIO()):
            try:
                r.main()
                code = None
            except SystemExit as exc:
                code = exc.code
        return code, r.findings.read(path), written


_code, _doc, _written = _main_json([])
check("main: report mode lands the JSON on a normal exit, nothing written",
      (_code, _doc["step"], _doc["mode"], _doc["written"], _written,
       sorted({f["kind"] for f in _doc["findings"]})),
      (None, "identity", "report", False, [], ["no account"]))
_code, _doc, _written = _main_json(["--write"])
check("main: --write fills the resolved id and lands written=True",
      (_code, _doc["mode"], _doc["written"], _written),
      (None, "write", True, [(2, [(2, "U0AAAAAAAAA")]), (3, [(2, "ada@x.com")])]))
_done = [["Ada", "ada@x.com", "U0AAAAAAAAA", "ada@x.com"]]
_code, _doc, _written = _main_json(["--write"], rows=_done,
                                   resolved=AssertionError("looked up with nothing to do"))
check("main: --write with nothing to resolve lands the JSON, written=False, no lookup",
      (_code, _doc["mode"], _doc["written"], _written), (None, "write", False, []))
_code, _doc, _written = _main_json(["--write"],
                                   resolved={"ada@x.com": {"id": "not-an-id"}, "bo@x.com": {}})
check("main: an API id not shaped like one ABORTS the write and lands no JSON",
      (isinstance(_code, str) and "ABORT" in _code, _doc, _written), (True, None, []))
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as _fh:
    json.dump([{"row": 2, "slack_id": "U0AAAAAAAAA", "slack_email": "ada@slack"}], _fh)
    _reviewed = _fh.name
try:
    _code, _doc, _written = _main_json(["--apply", _reviewed, "--write"])
    check("main: an applied review lands written=True in write mode",
          (_code, _doc["mode"], _doc["written"], _doc["summary"], len(_written)),
          (None, "write", True, "applied 1 reviewed Slack id(s)", 2))
    _code, _doc, _written = _main_json(["--apply", _reviewed])
    check("main: --apply without --write REFUSES and lands no JSON",
          (isinstance(_code, str) and "REFUSING" in _code, _doc, _written), (True, None, []))
finally:
    os.unlink(_reviewed)
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as _fh:
    json.dump([{"row": 2, "slack_id": "@handle"}], _fh)
    _reviewed = _fh.name
try:
    _code, _doc, _written = _main_json(["--apply", _reviewed, "--write"])
    check("main: an invalid review entry ABORTS and lands no JSON",
          (isinstance(_code, str) and "ABORT" in _code, _doc, _written), (True, None, []))
finally:
    os.unlink(_reviewed)
# The findings file carries names: like every --out, it must be gitignored
# before anything runs. A path in this (public) repo is refused before the
# sheet is read.
_probe = os.path.join(_REPO, "identity-findings-probe.json")
with mock.patch.object(r, "read_source",
                       lambda: (_ for _ in ()).throw(AssertionError("read the sheet"))):
    _code, _doc, _written = _main_json([], json_out=_probe)
check("main: a committable --json-out is refused before any work",
      (isinstance(_code, str) and "REFUSING TO RUN" in _code, os.path.exists(_probe)),
      (True, False))


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nresolve_slack_ids: all checks passed")
