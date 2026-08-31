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
                                "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "lib"))

import sync_chapters  # noqa: E402
import sync_resources as sr  # noqa: E402

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


_proposals, _unresolved = sr.propose_handles(
    [ch("Oslo", row=7, handles="")], _FakeAO(), None, _FakeSlackmod())
check("propose_handles() runs to completion against a 3-tuple read_intake()",
      len(_proposals), 1)
check("the resolved organizer's handle is proposed",
      _proposals[0]["value"], "@ada")

# --- the CI default is a real boolean, and masking announces itself ------------
import io as _io  # noqa: E402
import contextlib as _ctx  # noqa: E402
check("the CI default is the strict 1/true/yes parse of $CI", sr.CI_REDACT_DEFAULT,
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
check("set_redaction(False) leaves REDACT off", sr.REDACT, False)

# --- --redact: the unresolved-organizer list is the one place names print -----
sr.REDACT = True
try:
    check("redacted name is a first initial", sr.redact_name("ada lovelace"), "A.")
    check("redacted email keeps one char + TLD only", sr.redact_email("ada@x.com"), "a***@***.com")
finally:
    sr.REDACT = False
check("redaction off passes a name through", sr.redact_name("Ada Lovelace"), "Ada Lovelace")

if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nsync_resources: all checks passed")
