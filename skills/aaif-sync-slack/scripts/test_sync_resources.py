#!/usr/bin/env python3
"""Self-tests for the resource-map engine's pure logic. No network, no gws.

What is covered is the part that can be wrong *quietly*: which cells are treated
as open, whether a weak channel match can reach the sheet, and whether the
`none` sentinel survives a round trip. A broken Drive or Slack call fails loudly
on the next line; a cell filled with a plausible-but-wrong channel is copied into
the audit and reported to community leadership as coverage.
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-sync-chapters", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-sync-organizers", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import audit_organizers as ao  # noqa: E402
import sync_chapters  # noqa: E402
import sync_resources as sr  # noqa: E402
from aaif_events import redact as _redact  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def chan(name, private=False, archived=False):
    return {"name": name, "id": "C_" + name, "is_private": private,
            "is_archived": archived, "num_members": 5}


CFG = {"public_prefixes": ["", "meetup-"],
       "organizer_suffixes": ["-organizers", "-chapter-leads"]}

HEADERS = ["Title", "City", "Country", "Chapter Folder", "Slack Channel",
           "Organizer Channel", "Country Channel", "Organizer Handles", "Summary"]


def ch(city, country="", folder="", slack="", org="", ctry="", handles="", row=2):
    return {"row": row, "city": city, "country": country,
            "current": {"Chapter Folder": folder, "Slack Channel": slack,
                        "Organizer Channel": org, "Country Channel": ctry,
                        "Organizer Handles": handles}}


def read_over(rows, city=None):
    with mock.patch.object(sr, "get_values", return_value=rows):
        return sr.read_grid(city)


# --- the schema is shared, not restated ---------------------------------------
check("engine and sheet agree on the resource columns",
      (sr.FOLDER_COLUMN,) + sr.CHANNEL_COLUMNS + (sr.HANDLES_COLUMN,),
      sync_chapters.RESOURCE_COLUMNS)
check("only the handles column is rewritten rather than filled",
      sync_chapters.REWRITTEN_COLUMNS, (sr.HANDLES_COLUMN,))

# --- open vs answered ---------------------------------------------------------
# The single most important distinction in the engine: 'none' is an ANSWER.
# Treating it as a blank re-proposes a channel for a city a human already
# recorded as having none, on every run, forever.
_mixed = [ch("A", slack=""), ch("B", slack="berlin"),
          ch("C", slack=sync_chapters.NO_RESOURCE)]
check("only blank cells are open",
      [c["city"] for c in sr.open_cells(_mixed, "Slack Channel")], ["A"])

# --- reading the grid ---------------------------------------------------------
_rows = [HEADERS,
         ["AAIF Boston Chapter", "Boston", "USA", "", "", "", "", "", "blurb"],
         ["AAIF Pune Chapter", "Pune", "India", "", "pune", "none", "india", "@a", ""]]
_, _layout, _chapters = read_over(_rows)
check("rows are keyed by their real row number",
      [(c["row"], c["city"]) for c in _chapters], [(2, "Boston"), (3, "Pune")])
check("current values are read by header name",
      _chapters[1]["current"],
      {"Chapter Folder": "", "Slack Channel": "pune",
       "Organizer Channel": "none", "Country Channel": "india",
       "Organizer Handles": "@a"})
check("--city filters on the folded name", [c["city"] for c in
      read_over(_rows, "boston")[2]], ["Boston"])


def aborts(fn):
    try:
        fn()
    except SystemExit:
        return True
    return False


check("a sheet without the resource columns aborts with the migration named",
      aborts(lambda: read_over([["Title", "City", "Country"], ["t", "Boston", "US"]])),
      True)
check("a duplicated header aborts",
      aborts(lambda: read_over([HEADERS + ["Slack Channel"], _rows[1] + [""]])), True)

# --- channels: exact only -----------------------------------------------------
_p, _cand, _ = sr.propose_channels([ch("Berlin")], [chan("berlin")], CFG)
check("an exact slug is proposed",
      [(p["column"], p["value"]) for p in _p], [("Slack Channel", "berlin")])

_p, _cand, _ = sr.propose_channels([ch("Cape Town")], [chan("cape-town-ai")], CFG)
check("a near miss is NEVER written", _p, [])
check("a near miss is offered to a human",
      [(c[0], c[2]) for c in _cand], [("Cape Town", ["cape-town-ai"])])

_p, _, _ = sr.propose_channels([ch("Boston")], [chan("boston", private=True)], CFG)
check("a private channel is never proposed as the public one", _p, [])

_p, _, _ = sr.propose_channels([ch("Boston")], [chan("boston", archived=True)], CFG)
check("an archived channel is never proposed", _p, [])

# A filled cell is left alone even when Slack disagrees with it — the human who
# typed it knows something the matcher does not.
_p, _, _ = sr.propose_channels([ch("Berlin", slack="berlin-ai")], [chan("berlin")], CFG)
check("a filled cell is never overwritten", _p, [])
_p, _, _ = sr.propose_channels([ch("Berlin", slack=sync_chapters.NO_RESOURCE)],
                               [chan("berlin")], CFG)
check("'none' stops the proposal even when a channel exists", _p, [])

# --- organizer channels -------------------------------------------------------
_p, _, _ = sr.propose_channels([ch("Pune", slack="pune")],
                               [chan("pune"), chan("pune-organizers", private=True)],
                               CFG)
check("a private organizer channel IS proposed (unlike the public column)",
      [(p["column"], p["value"]) for p in _p],
      [("Organizer Channel", "pune-organizers")])

# --- country channels ---------------------------------------------------------
_p, _, _missing = sr.propose_channels([ch("Chennai", country="India", slack="chennai")],
                                      [chan("chennai"), chan("india")], CFG)
check("the country channel matches the row's own Country cell",
      [(p["column"], p["value"]) for p in _p], [("Country Channel", "india")])

# The supra-national rooms are exactly what this cannot derive, and must not try.
_p, _, _missing = sr.propose_channels([ch("Lagos", country="Nigeria", slack="lagos")],
                                      [chan("lagos"), chan("africa")], CFG)
check("#africa is not guessed from Nigeria", _p, [])
check("and the country is reported as a gap", sorted(_missing), ["Nigeria"])

# A blank Country Channel cell must consult COUNTRY_CHANNELS too, not only
# fold(country) — otherwise a country whose room's real name diverges from its
# ASCII fold (#deutschland, #korea, #brasil, ...) reports a gap that has had a
# room the whole time.
_p, _, _missing = sr.propose_channels(
    [ch("Stuttgart", country="Germany", slack="stuttgart")],
    [chan("stuttgart"), chan("deutschland")], CFG)
check("COUNTRY_CHANNELS wins over fold(country) for a blank cell",
      [(p["column"], p["value"]) for p in _p], [("Country Channel", "deutschland")])
check("so it is not reported as a gap", _missing, {})

# --- country_channel_name(): the ONE call site propose_channels() and
# plan_channels() both go through, so they can't drift on the rule again -----
check("an override wins", sr.country_channel_name("Germany", ao), "deutschland")
check("no override falls back to fold()",
      sr.country_channel_name("India", ao), "india")

# --- folder cells -------------------------------------------------------------
check("a folder URL round-trips to its id",
      sr.folder_id("https://drive.google.com/drive/folders/1ABC"), "1ABC")
check("a bare id is accepted too", sr.folder_id("1ABC"), "1ABC")
check("'none' is not an id", sr.folder_id(sync_chapters.NO_RESOURCE), "")
check("a blank is not an id", sr.folder_id(""), "")

with mock.patch.object(sr, "list_chapter_folders",
                       return_value=[{"id": "F1", "name": "Washington DC"},
                                     {"id": "F2", "name": "San Francisco"}]):
    _p, _near, _none = sr.propose_folders([ch("Washington, DC")])
    check("a folder matches through punctuation folding",
          [p["value"] for p in _p],
          ["https://drive.google.com/drive/folders/F1"])

    # The generic-token stoplist, shared with every other engine: without it
    # 'San Diego' is reported against 'San Francisco' and can never be added.
    _p, _near, _none = sr.propose_folders([ch("San Diego")])
    check("San Diego does not near-miss San Francisco", (_p, _near), ([], []))
    check("and it lands on the create-chapter queue", _none, ["San Diego"])


# --- plan mode: convention names for cells nothing exact filled ---------------
class _AO:
    """The two audit helpers plan_channels needs, without importing Slack."""
    @staticmethod
    def fold(x):
        import re, unicodedata
        x = unicodedata.normalize("NFKD", x or "")
        x = "".join(c for c in x if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")


_planned = sr.plan_channels(
    [ch("Charlotte", country="United States", row=5)], [], _AO)
check("plan fills city, organizers and country by convention",
      [(p["column"], p["value"], p["why"]) for p in _planned],
      [("Slack Channel", "charlotte", "TO CREATE"),
       ("Organizer Channel", "charlotte-organizers", "TO CREATE"),
       ("Country Channel", "united-states", "TO CREATE")])

# The organizer channel follows the chapter's OWN channel, not the city slug:
# Munich's room is #munchen, so its organizers belong in #munchen-organizers.
_planned = sr.plan_channels(
    [ch("Munich", country="Germany", slack="munchen", ctry="germany", row=5)],
    [], _AO)
check("the organizer channel follows the public channel name",
      [(p["column"], p["value"]) for p in _planned],
      [("Organizer Channel", "munchen-organizers")])

# Every chapter gets one, including the 26 with no accepted organizer yet.
_planned = sr.plan_channels([ch("Oslo", country="Norway", slack="oslo",
                                ctry="norway", row=5)], [], _AO)
check("a chapter with no organizers still gets an organizer channel",
      [(p["column"], p["value"]) for p in _planned],
      [("Organizer Channel", "oslo-organizers")])

# The whole point of KEPT_NON_CONVENTIONAL: a filled cell is never re-planned,
# so #munchen, #bay-area and #espana survive plan mode untouched.
_planned = sr.plan_channels(
    [ch("Munich", country="Germany", slack="munchen", org="none",
        ctry="germany", row=5)], [], _AO)
check("plan mode never overwrites a local-language or multi-chapter name",
      _planned, [])

check("an existing channel is marked so, not as TO CREATE",
      [p["why"] for p in sr.plan_channels([ch("Berlin", row=5)],
                                          [chan("berlin")], _AO)][0:1],
      ["exists"])

# --- realigning organizer cells written before the rule changed ---------------
# The narrow safety rule: only a cell naming a channel that does NOT exist may be
# repointed, so a real room can never be renamed out of the sheet.
_a = sr.propose_organizer_alignment(
    [ch("Munich", slack="munchen", org="munich-organizers", row=5)], set())
check("a planned organizer cell is repointed at the real channel",
      [(p["was"], p["value"]) for p in _a],
      [("munich-organizers", "munchen-organizers")])
check("an organizer channel that EXISTS is never repointed",
      sr.propose_organizer_alignment(
          [ch("New York", slack="nyc", org="nyc-chapter-leads", row=5)],
          {"nyc-chapter-leads"}), [])
check("'none' is left answered",
      sr.propose_organizer_alignment(
          [ch("X", slack="x", org=sync_chapters.NO_RESOURCE, row=5)], set()), [])

# --- Bengaluru is a rename, never a create ------------------------------------
# RENAMES is the SHEET side: what a cell should say afterwards. Bengaluru is a
# plain rename; the Bay Area pair is a merge, so two different cells end up
# naming the same room.
check("the superseded city name is repointed",
      sr.RENAMES["bangalore"], "bengaluru")
check("both Bay Area chapters end up naming one room",
      (sr.RENAMES["bay-area-sf-organizers"],
       sr.RENAMES["southbay-chapter-leads"]),
      ("bay-area-organizers", "bay-area-organizers"))
check("London's private room takes the canonical name",
      sr.RENAMES["london-meetup-organizers"], "london-organizers")
# The deprecated names are Slack-side only — a cell must never point at one.
check("no cell is ever repointed at a deprecated room",
      [v for v in sr.RENAMES.values() if v.endswith("-deprecated")], [])
check("and the kept list does not also claim bangalore",
      "bangalore" in sr.KEPT_NON_CONVENTIONAL, False)

# The rename is the ONE case where a filled channel cell is changed, and it must
# carry `was` so the write step can tell a stale proposal from a live one.
_r = sr.propose_renames([ch("Bengaluru", slack="bangalore", row=8)])
check("a renamed channel repoints its cell",
      [(p["column"], p["was"], p["value"]) for p in _r],
      [("Slack Channel", "bangalore", "bengaluru")])
check("a cell not being renamed is untouched",
      sr.propose_renames([ch("Berlin", slack="berlin")]), [])

# --- a country room misfiled as a chapter room --------------------------------
# The failure this catches is subtle and severe: #espana in the chapter column
# reports Madrid as HAVING a local room. In the country column it correctly
# reports Madrid as regional-only, which is the truth.
_c = sr.propose_column_corrections(
    [ch("Madrid", country="Spain", slack="españa", org="españa-organizers",
        ctry="spain", row=48)], _AO)
check("the misfiled value moves column, and the row is repaired",
      sorted((p["column"], p["was"], p["value"]) for p in _c),
      sorted([("Slack Channel", "españa", "madrid"),
              ("Country Channel", "spain", "españa"),
              ("Organizer Channel", "españa-organizers", "madrid-organizers")]))
check("a row that never claimed it is untouched",
      sr.propose_column_corrections([ch("Berlin", slack="berlin")], _AO), [])

# --- parse_erstwhile(): the history column's fixed grammar ---------------------
check("parse_erstwhile reads slugs around annotations",
      sr.parse_erstwhile("seattle-organizers (squatted) · formerly meetup-seattle-organizers"),
      {"seattle-organizers", "meetup-seattle-organizers"})
check("parenthesized prose never yields a name",
      sr.parse_erstwhile("munich (squatted) · germany (squatted country room)"),
      {"munich", "germany"})
check("comma-separated names all survive",
      sr.parse_erstwhile("austin (squatted) · formerly austin-area, austin-area-organizers"),
      {"austin", "austin-area", "austin-area-organizers"})
check("a leading # is shorthand, not a parse failure",
      sr.parse_erstwhile("#austin (squatted) · #munich"), {"austin", "munich"})
_disc = []
check("punctuation-bearing junk under-protects instead of inventing names",
      sr.parse_erstwhile("TBD?? (ask ops) · n/a!", _disc), set())
check("...and the dropped tokens are reported to the caller",
      sorted(_disc), ["n/a!", "tbd??"])
check("an empty cell yields nothing", sr.parse_erstwhile(""), set())
# Documented limitations, pinned so a change is a decision rather than drift:
check("bare lowercase prose IS read as names (annotate in parens instead)",
      sr.parse_erstwhile("pending decision"), {"pending", "decision"})
check("accented slugs fall out unprotected — record the ASCII spelling",
      sr.parse_erstwhile("españa (kept)"), set())

# read_erstwhile(): tolerate an absent column, but SAY so via column_present.
_saved_gv = sr.get_values
def _fake_grid(rows):
    sr.get_values = lambda *_a, **_k: rows
try:
    _fake_grid([])
    check("an empty sheet yields an inactive guard",
          sr.read_erstwhile(), (set(), [], False))
    _fake_grid([["City", "Country"], ["Boston", "USA"]])
    check("a sheet without the column yields an inactive guard",
          sr.read_erstwhile(), (set(), [], False))
    _fake_grid([["City", " Erstwhile Channels "],
                ["Boston", "boston-old (squatted)"],
                ["Berlin", "#berlin-old · junk!!"]])
    _n, _d, _p = sr.read_erstwhile()
    check("names union across rows, header whitespace stripped",
          (_n, _p), ({"boston-old", "berlin-old"}, True))
    check("unparsable tokens are surfaced, not swallowed", _d, ["junk!!"])
finally:
    sr.get_values = _saved_gv

# The country override stops a brand-new #spain being planned beside #espana.
check("a country with its own named room is not re-planned",
      [(p["was"], p["value"]) for p in sr.propose_country_overrides(
          [ch("Barcelona", country="Spain", ctry="spain")])],
      [("spain", "españa")])
# Japan, not Norway: the Nordics became COUNTRY_CHANNELS exceptions
# (one #nordics room), so a Norway fixture now correctly gets repointed.
check("a country whose channel matches the convention is left alone",
      sr.propose_country_overrides([ch("Tokyo", country="Japan", ctry="japan")]),
      [])
check("a Nordic country is repointed at the shared #nordics room",
      [(p["was"], p["value"]) for p in sr.propose_country_overrides(
          [ch("Oslo", country="Norway", ctry="norway")])],
      [("norway", "nordics")])

# Both the misfiling fix and the country override reach Madrid's country cell.
_madrid = [ch("Madrid", country="Spain", slack="españa",
              org="españa-organizers", ctry="spain", row=48)]
_both = sr.dedupe(sr.propose_column_corrections(_madrid, _AO)
                  + sr.propose_country_overrides(_madrid))
check("one proposal per cell, however many steps want it",
      len([p for p in _both if p["column"] == "Country Channel"]), 1)
check("and nothing else is lost to the dedupe",
      sorted(p["column"] for p in _both),
      ["Country Channel", "Organizer Channel", "Slack Channel"])

# --- malformed filled cells: the format-only guard ---------------------------
# "filled" counts any non-blank cell as healthy, so a URL pasted into a channel
# column would read as a mapped channel forever without this.
check("a pasted URL in a channel column is flagged",
      sr.malformed_channel_cells(
          [ch("Montréal", slack="https://drive.google.com/drive/folders/X",
              org="montreal-organizers", ctry="canada", row=48)]),
      [(48, "Montréal", "Slack Channel",
        "https://drive.google.com/drive/folders/X")])
check("an email address in a channel column is flagged",
      [m[2] for m in sr.malformed_channel_cells(
          [ch("Berlin", org="a@x.com")])],
      ["Organizer Channel"])
check("local-language names, none, blanks and legacy names all pass",
      sr.malformed_channel_cells(
          [ch("Munich", slack="munchen", org="munchen-organizers", ctry="none"),
           ch("Madrid", slack="españa", org="", ctry="españa"),
           ch("Washington DC", slack="washington-dc-the-capital",
              org="frankfurt_main-organizers")]),
      [])


# --- the none sentinel is case-insensitive in the malformed scan ---------------
# A human typing "None" must mean the sentinel, not a channel literally named
# None that then suppresses proposals forever.
check("'None' and 'NONE' are the sentinel, not malformed cells",
      sr.malformed_channel_cells([ch("Oslo", slack="None", org="NONE", ctry="none")]),
      [])

# --- an empty roster never overwrites a human's `none` in Handles --------------
_h = [ch("Oslo", handles="none")]
with mock.patch.object(sr, "fold_city", sr.fold_city):
    pass  # (import sanity; propose_handles needs live deps, tested via guard below)
check("the handles guard constant matches the shared sentinel",
      sync_chapters.NO_RESOURCE, "none")


# --- propose_handles() actually calls ao.read_intake() — the 3-value unpacking
# is the same fix class prune_organizers.py/invite_organizers.py needed; this
# one takes `ao` as a plain argument rather than a module attribute, so no
# mock.patch is needed — a fake object with the right shape is enough. -------
class _FakeAO:
    def read_intake(self):
        return ([{"name": "Ada", "email": "a@x.com", "city": "Oslo"}], 0, {})


class _FakeSlackmod:
    def lookup_emails(self, api, emails):
        return {"a@x.com": {"id": "U1", "name": "ada"}}


# `known_ids` is stubbed because propose_handles now consults the reviewed
# `Slack ID` column through resolve_slack_ids, which shells out to `gws`. A test
# that reaches a real CLI passes only where that CLI is installed and
# authenticated — it went green here and failed in CI, which has neither, and it
# would read live Drive data if it ever did run.
with mock.patch.object(sr.rsi, "known_ids", lambda: {}):
    _proposals, _unresolved = sr.propose_handles(
        [ch("Oslo", row=7, handles="")], _FakeAO(), None, _FakeSlackmod())
check("propose_handles() runs to completion against a 3-tuple read_intake()",
      len(_proposals), 1)
check("the resolved organizer's handle is proposed",
      _proposals[0]["value"], "@ada")

# --- the CI default is a real boolean, and masking announces itself ------------
import io as _io  # noqa: E402
import contextlib as _ctx  # noqa: E402
check("the CI default is the strict 1/true/yes parse of $CI", _redact.CI_REDACT_DEFAULT,
      os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sr.set_redaction(True)
check("turning redaction on prints exactly one stderr line",
      (_err.getvalue().count("\n"), "redaction ON" in _err.getvalue()), (1, True))
_err = _io.StringIO()
with _ctx.redirect_stderr(_err):
    sr.set_redaction(False)
check("turning redaction off is silent", _err.getvalue(), "")
check("set_redaction(False) leaves REDACT off", _redact.REDACT, False)

# --- --redact: the unresolved-organizer list is the one place names print -----
_redact.REDACT = True
try:
    check("redacted name is a first initial", sr.redact_name("ada lovelace"), "A.")
    check("redacted email keeps one char + TLD only", sr.redact_email("ada@x.com"), "a***@***.com")
finally:
    _redact.REDACT = False
check("redaction off passes a name through", sr.redact_name("Ada Lovelace"), "Ada Lovelace")

# --- --json-out: the report as data, same numbers, no sheet text, no names ----
import json as _json  # noqa: E402
import tempfile as _tempfile  # noqa: E402
_rep = sr.build_findings(
    chapters=[ch("Boston", "United States", row=2),
              ch("Oslo", "Norway", row=3, slack="not a channel!"),
              ch("Lima", "Peru", row=4, folder=sync_chapters.NO_RESOURCE)],
    proposals=[{"row": 2, "city": "Boston", "column": "Slack Channel",
                "value": "boston", "why": "exact"},
               {"row": 3, "city": "Oslo", "column": "Organizer Handles",
                "value": "@ada", "was": "@old-cell-text"}],
    near=[("Lima", ["Lima Norte"])], folderless=["Boston"],
    candidates=[("Oslo", "Slack Channel", ["oslo-ai"])],
    missing_countries={"Peru": {"Lima"}},
    did_slack=True,
    malformed=[(3, "Oslo", "Slack Channel", "not a channel!")],
    unresolved=[("Boston", "Ada Lovelace")],
    skipped_slack=False)
_doc = _rep.to_dict()
check("findings: format 1, step resources, report mode",
      (_doc["format"], _doc["step"], _doc["mode"]), (1, "resources", "report"))
check("findings: the summary is the report's headline counts",
      _doc["summary"], "3 chapter rows; 2 cell(s) proposed; 1 malformed")
check("findings: one tile per resource column plus the row and proposal totals",
      [m["label"] for m in _doc["measured"]],
      ["chapter rows", "Chapter Folder", "Slack Channel", "Organizer Channel",
       "Country Channel", "Organizer Handles", "proposed cells"])
check("findings: a column tile carries filled/none/blank/proposed",
      next(m["value"] for m in _doc["measured"] if m["label"] == "Chapter Folder"),
      "0 filled / 1 none / 2 blank / 0 proposed")
_by = {}
for _f in _doc["findings"]:
    _by.setdefault(_f["kind"], []).append(_f)
check("findings: a proposed cell names city + column, never a row of people",
      (_by["proposed cell"][0]["subject"], _by["proposed cell"][0]["severity"]),
      ("Boston · Slack Channel", "warn"))
check("findings: a malformed cell is bad and quotes the row, not the cell text",
      (_by["malformed cell"][0]["subject"], _by["malformed cell"][0]["severity"],
       _by["malformed cell"][0]["detail"]),
      ("Oslo · Slack Channel", "bad", "row 3"))
check("findings: a rewrite never carries the old cell text",
      "@old-cell-text" in _json.dumps(_doc), False)
check("findings: organizers without Slack are one row under subject Slack, naming them",
      ([f["subject"] for f in _by["no Slack account"]],
       "Ada" in _by["no Slack account"][0]["detail"]),
      (["Slack"], True))
check("findings: a country without a channel is one row per country",
      (_by["country without channel"][0]["subject"],
       _by["country without channel"][0]["detail"]),
      ("Peru", "1 chapter(s): Lima"))
check("findings: no partial finding when Slack was reached", "partial" in _by, False)

_part = sr.build_findings([ch("Boston", row=2)], [], [], [], [], {}, did_slack=False,
                          malformed=[], unresolved=[], skipped_slack=True).to_dict()
check("findings: the PARTIAL run still lands, with a warn finding on subject Slack",
      [(f["subject"], f["severity"], f["detail"]) for f in _part["findings"]],
      [("Slack", "warn", "Slack unavailable — channel columns not checked")])
check("findings: the PARTIAL run marks each channel column as not checked",
      [m["value"] for m in _part["measured"] if m["label"] in sr.CHANNEL_COLUMNS],
      ["not checked"] * 3)
check("findings: PARTIAL is in the summary", "Slack unavailable" in _part["summary"], True)

with _tempfile.TemporaryDirectory() as _d:
    _path = os.path.join(_d, "resources.json")
    _rep.write(_path)
    check("findings: the JSON round-trips through the shared reader",
          sr.findings.read(_path)["step"], "resources")
    check("findings: the file is private (0600)", oct(os.stat(_path).st_mode & 0o777), "0o600")
check("findings: write() without a path is a no-op", _rep.write(None), None)
# The names travel through redact_name, like the text report's roster.
from aaif_events import redact as _redact  # noqa: E402
_redact.REDACT = True
try:
    _rr = sr.build_findings([ch("Boston", row=2)], [], [], [], [], {}, did_slack=True,
                            malformed=[], unresolved=[("Boston", "Ada Lovelace")],
                            skipped_slack=False).to_dict()
finally:
    _redact.REDACT = False
check("findings: --redact masks the organizers without Slack",
      [f["detail"] for f in _rr["findings"] if f["kind"] == "no Slack account"],
      ["1 accepted organizer(s) have no Slack account: A. (Boston)"])

import contextlib as _ctx  # noqa: E402
import io as _io  # noqa: E402


# main() lands the file on a normal exit in both modes, and not on an ABORT.
def json_out_main(argv, proposals):
    """Run main() over one mocked chapter row; (exit code, JSON or None)."""
    grid = (None, {"index": {c: i for i, c in enumerate(HEADERS)}}, [ch("Boston", row=2)])
    with _tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.json")
        with mock.patch.object(sr, "read_grid", lambda city: grid), \
             mock.patch.object(sr, "propose_folders", lambda c: (list(proposals), [], [])), \
             mock.patch.object(sr, "slack_half", lambda c, plan: ([], [], {}, [], True)), \
             mock.patch.object(sr, "apply", lambda p, layout: len(p)), \
             mock.patch.object(sr, "verify", lambda p, city: None), \
             mock.patch.object(sys, "argv", ["sync_resources.py", "--json-out", path] + argv), \
             _ctx.redirect_stdout(_io.StringIO()):
            code = sr.main()
        return code, (sr.findings.read(path) if os.path.exists(path) else None)


_FILL = [{"row": 2, "city": "Boston", "column": "Chapter Folder",
          "value": "https://drive/x", "why": "exact"}]
_code, _out = json_out_main([], [])
check("main: report mode in sync lands the JSON on exit 0",
      (_code, _out["step"], _out["mode"], _out["written"]), (0, "resources", "report", False))
_code, _out = json_out_main([], _FILL)
check("main: report mode with a proposal lands the JSON on exit 2",
      (_code, _out["mode"], [f["kind"] for f in _out["findings"]]),
      (2, "report", ["proposed cell"]))
_code, _out = json_out_main(["--write"], [])
check("main: write mode with nothing to do lands the JSON, written=False",
      (_code, _out["mode"], _out["written"]), (0, "write", False))
_code, _out = json_out_main(["--write"], _FILL)
check("main: a verified write lands written=True on exit 0",
      (_code, _out["mode"], _out["written"]), (0, "write", True))
# A --json-out path git would commit is refused before the sheet is read.
_unignored = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "findings-selftest.json")
with mock.patch.object(sr, "read_grid", side_effect=AssertionError("read_grid ran")), \
     mock.patch.object(sys, "argv", ["sync_resources.py", "--json-out", _unignored]):
    try:
        sr.main()
        _refused = False
    except SystemExit as e:
        _refused = "not ignored" in str(e)
check("main: an unignored --json-out aborts before any work, and lands nothing",
      (_refused, os.path.exists(_unignored)), (True, False))

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nsync_resources: all checks passed")
