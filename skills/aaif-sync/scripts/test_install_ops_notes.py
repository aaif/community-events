#!/usr/bin/env python3
"""install_ops_notes' placement. Plain script; exit 1 on failure. No network.

The one thing that must hold: the header goes AFTER the last existing column,
never into a gap and never onto an existing header — a tab whose spill
formula occupies the middle columns has literal ops columns to the right,
and that is the only place a typed value is safe.
"""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install_ops_notes as ion  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


# Synthetic headers only — the shape of a role tab, none of its content.
ROLE = ["Status", "Full name", "Timestamp", "Name", "Email", "Chapter",
        "Reviewed by", "Decision notes", "Issues"]

check("a missing column goes after the last header",
      ion.plan(ROLE, grid_cols=30), {"present": False, "col": 10, "widen": False})
check("a grid exactly as wide as its headers is widened first",
      ion.plan(ROLE, grid_cols=9)["widen"], True)
check("a present column is found by name, wherever it sits",
      ion.plan(ROLE[:3] + [ion.HEADER] + ROLE[3:], 30),
      {"present": True, "col": 4, "widen": False})
check("header whitespace does not hide a present column",
      ion.plan(ROLE + [" Ops Notes "], 30)["present"], True)
check("a trailing blank header still counts as a column",
      ion.plan(ROLE + [""], 30)["col"], 11)
check("A1 letters", [ion.col_letter(n) for n in (1, 26, 27, 52, 53)],
      ["A", "Z", "AA", "AZ", "BA"])

_dup = []
try:
    ion.plan(ROLE + [ion.HEADER, ion.HEADER], 30)
except SystemExit as e:
    _dup.append(str(e.code)[:5])
check("a duplicated header aborts rather than picking one", _dup, ["ABORT"])

# Every target is a (spreadsheet, tab) pair the estate really has; the sheet
# ids are the two this repo already names everywhere, and the tabs are the
# three role tabs plus the feed. Pinned so a typo cannot install the column
# on the wrong tab.
check("targets", [t for _s, t in ion.TARGETS],
      ["Organizers", "Hosts", "Speakers", "Chapters & Teams"])


# --- main() end to end, gws mocked -----------------------------------------
#
# A FakeGws is the whole estate: per (sheet, tab) a header row and a grid
# width. It records every argv the script issues and answers the two reads
# (`spreadsheets get` -> tabs_meta shape, `values get` -> headers_of shape)
# from that state; a `values update` lands in the state the way Sheets would,
# unless `lose_writes` is set, which is the "fresh read does not show it"
# case. Synthetic tab names only: the ids under test are ion.TARGETS' own.


class FakeGws:
    def __init__(self, tabs, lose_writes=False):
        # tabs: {(sheet_id, title): (headers list, grid width)}
        self.tabs = {k: (list(h), w) for k, (h, w) in tabs.items()}
        self.lose_writes = lose_writes
        self.calls = []
        #: [(argv, retries)] — the retry budget each call was issued with, so a
        #: test can pin that the non-idempotent widen is never re-sent.
        self.budgets = []

    def __call__(self, args, retries=None):
        self.calls.append(list(args))
        self.budgets.append((list(args), retries))
        verb = tuple(args[:4])
        params = json.loads(args[args.index("--params") + 1])
        sid = params["spreadsheetId"]
        if verb[:3] == ("sheets", "spreadsheets", "get"):
            return {"sheets": [
                {"properties": {"title": t, "sheetId": n,
                                "gridProperties": {"columnCount": w}}}
                for n, ((s, t), (_h, w)) in enumerate(sorted(self.tabs.items()))
                if s == sid]}
        if verb == ("sheets", "spreadsheets", "values", "get"):
            tab = params["range"].split("!")[0].strip("'")
            hdr, _w = self.tabs[(sid, tab)]
            return {"values": [hdr]} if hdr else {}
        if verb == ("sheets", "spreadsheets", "values", "update"):
            if not self.lose_writes:
                tab, cell = params["range"].split("!")
                tab = tab.strip("'")
                body = json.loads(args[args.index("--json") + 1])
                hdr, w = self.tabs[(sid, tab)]
                col = _col_number(cell.rstrip("0123456789"))
                if col > w:
                    raise AssertionError("wrote past the grid of %r (%s > %d)" % (tab, cell, w))
                hdr = hdr + [""] * (col - len(hdr))
                hdr[col - 1] = body["values"][0][0]
                self.tabs[(sid, tab)] = (hdr, w)
            return {}
        if verb[:3] == ("sheets", "spreadsheets", "batchUpdate"):
            body = json.loads(args[args.index("--json") + 1])
            for r in body["requests"]:
                if "appendDimension" in r:
                    a = r["appendDimension"]
                    for (s, t), (h, w) in list(self.tabs.items()):
                        if s == sid and _sheet_id_of(self, s, t) == a["sheetId"]:
                            self.tabs[(s, t)] = (h, w + a["length"])
            return {}
        raise AssertionError("unexpected gws argv: %r" % (args,))

    def of(self, *verb):
        return [c for c in self.calls if tuple(c[:len(verb)]) == verb]

    def batch_requests(self):
        return [r for c in self.of("sheets", "spreadsheets", "batchUpdate")
                for r in json.loads(c[c.index("--json") + 1])["requests"]]

    def updates(self):
        """[(range, valueInputOption, value)] from every `values update`."""
        out = []
        for c in self.of("sheets", "spreadsheets", "values", "update"):
            p = json.loads(c[c.index("--params") + 1])
            b = json.loads(c[c.index("--json") + 1])
            out.append((p["range"], p.get("valueInputOption"), b["values"][0][0]))
        return out


def _sheet_id_of(fake, sheet_id, title):
    for n, ((s, t), _v) in enumerate(sorted(fake.tabs.items())):
        if s == sheet_id and t == title:
            return n
    raise KeyError(title)


def _col_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def run_main(fake, argv):
    """(exit code or SystemExit message, stdout) with ion.gws swapped for `fake`."""
    real = ion.gws
    ion.gws = fake
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                rc = ion.main(argv)
            except SystemExit as e:
                rc = e.code
    finally:
        ion.gws = real
    return rc, buf.getvalue()


def estate(**overrides):
    """Every target tab lacks the header; ROLE-shaped, grids wider than needed.

    overrides: {title: (headers, width)} for the tabs a case changes.
    """
    tabs = {}
    for sheet_id, tab in ion.TARGETS:
        tabs[(sheet_id, tab)] = overrides.get(tab, (ROLE, 30))
    return tabs


_writes = ("sheets", "spreadsheets", "batchUpdate")
_upd = ("sheets", "spreadsheets", "values", "update")

# (1) report mode: a missing header is reported, nothing is issued, exit 2.
fake = FakeGws(estate())
rc, out = run_main(fake, [])
check("report mode exits 2 when a tab lacks the header", rc, 2)
check("report mode issues no batchUpdate", fake.of(*_writes), [])
check("report mode issues no values update", fake.of(*_upd), [])
check("report mode names each missing tab",
      all(t in out for _s, t in ion.TARGETS), True)
check("report mode still reads every target tab",
      len(fake.of("sheets", "spreadsheets", "values", "get")), len(ion.TARGETS))

# (2) write mode: the value lands at '<tab>'!<letter>1 RAW; only the exactly
# full grid is widened. Hosts is 9 headers on a 9-wide grid; the rest are 30.
fake = FakeGws(estate(Hosts=(ROLE, 9)))
rc, out = run_main(fake, ["--write"])
check("write mode exits 0 once every tab has the header", rc, 0)
check("the header lands at column J row 1 of each tab, RAW",
      sorted(fake.updates()),
      sorted([("'%s'!J1" % t, "RAW", ion.HEADER) for _s, t in ion.TARGETS]))
appends = [r["appendDimension"] for r in fake.batch_requests() if "appendDimension" in r]
hosts_sid = _sheet_id_of(fake, ion.INTAKE_ID, "Hosts")
check("appendDimension is issued only for the exactly-full grid",
      appends, [{"sheetId": hosts_sid, "dimension": "COLUMNS", "length": 1}])
check("every appended header is styled",
      len([r for r in fake.batch_requests() if "repeatCell" in r]), len(ion.TARGETS))
# The widen is the one call here that must never be re-sent: a create-shaped
# request that succeeded but answered like a timeout adds a SECOND column on
# the retry. Reads and the fixed-cell `values update` are safe at the default.
check("the batchUpdate carrying the widen is issued with no retry budget",
      sorted({r for a, r in fake.budgets if a[2] == "batchUpdate"}), [1])
check("reads and the values update do not override the shared budget",
      sorted({r for a, r in fake.budgets if a[2] != "batchUpdate"}), [None])
check("the write lands in the fake estate",
      all(ion.HEADER in h for h, _w in fake.tabs.values()), True)
check("the batchUpdate precedes the values update on each tab",
      [c[2] for c in fake.calls if c[2] in ("batchUpdate",) or c[2:4] == ["values", "update"]],
      ["batchUpdate", "values"] * len(ion.TARGETS))

# (3) a tab that already has the header gets no request at all.
fake = FakeGws(estate(Speakers=(ROLE + [ion.HEADER], 30)))
rc, out = run_main(fake, ["--write"])
check("write mode exits 0 with one tab already done", rc, 0)
check("a present tab gets no values update",
      [u for u in fake.updates() if "Speakers" in u[0]], [])
check("a present tab gets no batchUpdate",
      [r for r in fake.batch_requests()
       if r.get("repeatCell", r.get("appendDimension", {})).get("sheetId")
       == _sheet_id_of(fake, ion.INTAKE_ID, "Speakers")], [])
check("the other tabs are still written", len(fake.updates()), len(ion.TARGETS) - 1)
check("a present tab is reported present", "Speakers           present at J (J1)" in out, True)

fake = FakeGws({k: (ROLE + [ion.HEADER], 30) for k in estate()})
rc, out = run_main(fake, ["--write"])
check("nothing to do exits 0", rc, 0)
check("nothing to do issues no write", fake.of(*_writes) + fake.of(*_upd), [])

# (4) the post-write re-read must show the header, or main aborts naming the tab.
fake = FakeGws(estate(), lose_writes=True)
rc, out = run_main(fake, ["--write"])
check("a write the re-read does not show aborts", str(rc)[:5], "ABORT")
check("the abort names the tab", "Organizers" in str(rc), True)
check("the abort stops at the first tab", len(fake.updates()), 1)

# The abort for a renamed tab happens before any read of headers or write.
fake = FakeGws({k: v for k, v in estate().items() if k[1] != "Hosts"})
rc, out = run_main(fake, ["--write"])
check("a missing tab title aborts", str(rc)[:5], "ABORT")
check("the missing-tab abort names the tab", "Hosts" in str(rc), True)
check("the missing-tab abort issues no write", fake.of(*_writes) + fake.of(*_upd), [])

print()
if FAILS:
    print("FAILURES:\n" + "\n".join(FAILS))
    sys.exit(1)
print("install_ops_notes: all checks passed")
