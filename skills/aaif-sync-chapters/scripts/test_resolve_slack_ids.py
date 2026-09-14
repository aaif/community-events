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
for good in ("U0BJDG1SWFK", "W01LT585W8Z", "U02HG3DF68G"):
    check("%s is a Slack id" % good, bool(r.SLACK_ID_RE.match(good)), True)
for bad in ("u0bjdg1swfk", "C0BJDG1SWFK", "U0B", "", "U0BJ DG1", "=U0BJDG1SWFK",
            "@zohebshaik7", "zoheb@x.com"):
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

code, written = apply_file([{"row": 5, "slack_id": "U0BJDG1SWFK"}])
check("a valid entry is written", written, [(5, "U0BJDG1SWFK")])
check("a valid entry does not abort", code, None)

code, written = apply_file([{"row": 5, "slack_id": "U0BJDG1SWFK"},
                            {"row": 6, "slack_id": "@handle"}])
check("one bad id writes NOTHING at all", written, [])
check("one bad id aborts", isinstance(code, str), True)

code, written = apply_file([{"row": 999, "slack_id": "U0BJDG1SWFK"}])
check("a row outside the data rows writes nothing", written, [])
check("a row outside the data rows aborts", isinstance(code, str), True)

code, written = apply_file([{"row": "5", "slack_id": "U0BJDG1SWFK"}])
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

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nresolve_slack_ids: all checks passed")
