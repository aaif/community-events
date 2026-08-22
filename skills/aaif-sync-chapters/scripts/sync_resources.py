#!/usr/bin/env python3
"""Fill the Chapters List resource map: Drive folder and Slack channels per chapter.

The fifth engine, same house rules as the other four — the report is the default,
`--write` recomputes from a fresh read and verifies itself, and a cell a human has
already filled is never overwritten.

| Column | Proposed from | Confidence |
|---|---|---|
| `Chapter Folder` | the Chapters Drive folder, matched on folded name | derived, verifiable |
| `Slack Channel` | live Slack, exact slug or configured prefix | exact only |
| `Organizer Channel` | live Slack, exact `<city><suffix>` | exact only |
| `Country Channel` | live Slack, exact match on the row's `Country` | exact only |

## Why this engine proposes so little

`Chapter Folder` is *derived*: a folder either exists under the Chapters parent
with this city's name or it does not, and the answer can be re-checked against
Drive at any time. The engine fills it freely.

The three channel columns are not derived, they are *claims about where a
community meets*, and the audit skill's rule applies here unchanged:

> NEVER AUTO-MAP AN ALIAS. A wrong alias reports a chapter as covered when it has
> no room, and nothing downstream re-checks it.

So only an **exact** name match is ever written — `#berlin` for Berlin,
`#india` for a row whose Country is India. Anything weaker (`#cape-town-ai` for
Cape Town) is printed as a candidate for a human to confirm and is never written,
no matter how obvious it looks. "No channel found" is a correct answer; a wrong
channel is not.

## Blank vs `none`

A blank cell means nobody has looked yet, and every run proposes for it again. The
literal `none` (`NO_RESOURCE`) means a human checked and there genuinely is no
such channel — it stops the proposals for good. This is the sheet's stand-in for
the JSON `null` that `channel_map.json` used to carry, and it is why the engine
must distinguish "empty" from "answered" rather than treating both as "fill me".

Usage:
    python3 sync_resources.py                      # report everything
    python3 sync_resources.py --only folder        # no Slack needed
    python3 sync_resources.py --city Boston
    python3 sync_resources.py --write
"""

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# The channel matcher is IMPORTED from the audit skill, not copied. Same decision
# as sync_crm importing resolve_city() from sync_chapters: a city that matched one
# way when this engine filled the sheet and another way when the audit read it
# would report a chapter as roomless while its own row names the room. One
# matcher, one answer. This is why the engine only runs from a full checkout or a
# plugin install — see the two-tier note in AGENTS.md.
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-audit-slack", "scripts"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

from sync_chapters import (CHAPTERS_ID, CHAPTERS_TAB, GENERIC_CITY_TOKENS,  # noqa: E402
                           NO_RESOURCE, RESOURCE_COLUMNS, cell, col_letter,
                           fold_city, get_values, gws_json, header_index)
from sync_crm import TEMPLATE_FOLDER, list_chapter_folders  # noqa: E402

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



FOLDER_COLUMN = "Chapter Folder"
CHANNEL_COLUMNS = ("Slack Channel", "Organizer Channel", "Country Channel")
HANDLES_COLUMN = "Organizer Handles"

#: Channels whose current name is kept even though it is not `<city>`. The
#: reasoning matters more than the list — these are NOT oversights to tidy up
#: later:
#:
#: - **One room deliberately serves several chapters**: `#bay-area` (San
#:   Francisco + Silicon Valley, 782 members). The `<city>` convention cannot
#:   express this, and splitting it would empty a working room into two.
#:
#: `#españa` was listed here as a multi-chapter room and that was wrong — it is
#: Spain's COUNTRY room, and belongs in the country column. See MISFILED_COLUMNS.
#: The two cases look identical from the sheet (one channel, several chapters)
#: and are not: a shared *chapter* room means those chapters have a home together,
#: a *country* room means none of them has a local home at all.
#:
#: The 2026-08-10 list was much longer (`#munchen`, `#medellín`, `#colorado`,
#: `#delhi`, and seven legacy meetup-era prefixes). The 2026-08-17 naming sweep
#: reversed that call: those rooms were RENAMED to the convention via
#: provision_channels.CHANNEL_RENAMES — a rename keeps members and history, so
#: "moving people" was never actually the cost. Even the local-language keeps
#: fell to typability then (#munchen -> #munich, #medellín -> #medellin), but
#: the 2026-08-22 qualified-slug decision brought `#munchen` BACK — its
#: `#munich` target turned out to be squatted, and the native name beats
#: waiting (the #españa / #deutschland precedent).
#: 2026-08-22 (user-decided): squatted city names resolve to a QUALIFIED slug
#: instead of waiting on the squatter — `<city>-<state>` for US cities
#: (austin-tx, charlotte-nc, dallas-tx; austin-tx superseded the one-day
#: `#austin-area` keep of 2026-08-21). The squatted originals are recorded in
#: the sheet's `Erstwhile Channels` column; never re-plan them.
#: NOTE this tuple is a DECISION RECORD, not config: nothing at runtime reads
#: it (only a test asserts against it). The live protection is the sheet cell
#: plus the erstwhile guard.
KEPT_NON_CONVENTIONAL = ("bay-area", "austin-tx", "charlotte-nc", "dallas-tx",
                         "munchen")

#: The sheet column that records each chapter's squatted and former channel
#: names ("erstwhile"), added 2026-08-22. Free text per cell, but with a fixed
#: grammar so it stays machine-readable: bare channel slugs, annotations only
#: inside parentheses, `·` or `,` between entries, and the word `formerly` as
#: an allowed connective. parse_erstwhile() below is the one reader; the
#: provisioner refuses to create or rename into any name it yields. The column
#: is optional — a sheet without it just yields no forbidden names — because
#: this engine must keep working against older copies of the tab.
ERSTWHILE_COLUMN = "Erstwhile Channels"
_ERSTWHILE_PARENS = re.compile(r"\([^)]*\)")
# ASCII slugs only, deliberately: an accented erstwhile name (españa, münchen)
# would fall out unprotected. This workspace HAS accented channel history, so
# record such names in their typable ASCII spelling — the test pins the drop.
_ERSTWHILE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def parse_erstwhile(text, discarded=None):
    """The channel names recorded in one Erstwhile Channels cell.

    Strips parenthesized annotations first, then splits on the separators; a
    leading `#` is shorthand people will type and is stripped before matching.
    A token that fails the slug shape (or is the `formerly` connective) is
    dropped — and appended to `discarded` when the caller passes a list, so
    the provisioner can say how many names it is NOT protecting. Two failure
    directions, both deliberate: punctuation-bearing junk under-protects
    (dropped, reported), while bare lowercase prose IS read as names — put
    annotations in parentheses, because a stray word here over-protects by
    refusing an unrelated create (loudly, in the REFUSED report).
    """
    out = set()
    for part in re.split(r"[·,]", _ERSTWHILE_PARENS.sub(" ", text or "")):
        for tok in part.split():
            tok = tok.strip().lower().lstrip("#")
            if not tok or tok == "formerly":
                continue
            if _ERSTWHILE_SLUG.match(tok):
                out.add(tok)
            elif discarded is not None:
                discarded.append(tok)
    return out


def read_erstwhile():
    """Return (names, discarded, column_present) for the whole sheet.

    names   — every erstwhile channel name, folded to one set
    discarded — tokens that failed the slug grammar and are NOT protected
    column_present — False when the sheet has no ERSTWHILE_COLUMN header

    Its own small read (not part of read_grid) so the provisioner can call it
    without adopting read_grid's abort-on-missing-column contract. The absent
    column is TOLERATED (older tab copies) but must be SURFACED by the caller:
    on the live sheet the column exists as of 2026-08-22, so absence means a
    rename or restructure silently disarmed the guard — the exact failure the
    2026-08 A:D-range breakage already demonstrated on this tab.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        return set(), [], False
    headers = [h.strip() for h in rows[0]]
    if ERSTWHILE_COLUMN not in headers:
        return set(), [], False
    i = headers.index(ERSTWHILE_COLUMN)
    names, discarded = set(), []
    for row in rows[1:]:
        names |= parse_erstwhile(cell(row, i), discarded)
    return names, discarded, True

#: Country -> the channel that serves it, where that channel is not named after
#: the country in ASCII English. Without this the engine plans a brand-new
#: `#spain` alongside `#españa`, which IS Spain's room and has 112 people in it.
#:
#: This is the country-column twin of KEPT_NON_CONVENTIONAL: the community's own
#: name for itself wins over the convention. Add an entry whenever the report's
#: "countries with chapters but no channel named after them" section names a
#: country that demonstrably already has a room.
COUNTRY_CHANNELS = {
    "Spain": "españa",
    # #germany is squatted by an invisible private room (Pro plan, no way to
    # reclaim), so Germany's country room takes the native name instead —
    # created 2026-08-22, same precedent as #españa.
    "Germany": "deutschland",
    # One Nordic room, not per-country rooms (user-decided 2026-08-19):
    # #nordics (129 members, the 2021 room, renamed from #nordics-public)
    # serves the Nordic chapters. The four countries with chapters today are
    # folded below; if a Reykjavik/Iceland chapter lands, add "Iceland" here
    # in the same change. The per-country rooms were folded —
    # #sweden/#denmark/#finland were days-old and empty, #norway had been
    # silent since 2024 and got a pointer post before archiving.
    "Denmark": "nordics",
    "Finland": "nordics",
    "Norway": "nordics",
    "Sweden": "nordics",
}

#: Values that channel_map.json's unconfirmed `public` table filed in the wrong
#: COLUMN — not the wrong name, the wrong kind of thing.
#:
#: `#españa` was recorded as Madrid's, Bilbao's and Logroño's own chapter channel.
#: It is Spain's country channel. The distinction is the whole point of the two
#: columns: a country room means those three chapters have no local room of their
#: own, and the audit is supposed to report exactly that ("regional only, a member
#: there has no local room"). Filed as a chapter channel it instead reported all
#: three as covered — the precise failure the never-auto-map rule exists to
#: prevent, arriving through a seed rather than a guess.
#:
#: Keyed by the misfiled value; the pair is (wrong column, right column).
MISFILED_COLUMNS = {"españa": ("Slack Channel", "Country Channel")}

#: The one exception to the above: `Bangalore` is the superseded name for the
#: city, whose actual name is `Bengaluru`. That is not a local-language variant
#: to preserve, it is out of date — so the channel is RENAMED (keeping its 37
#: members and its history) rather than kept or duplicated. A rename, never a
#: create: a new `#bengaluru` next to `#bangalore` splits the chapter in two.
#: What a channel CELL should say afterwards, keyed by what it says now. This is
#: the sheet side only; the Slack operations that make it true (renames, in a
#: dependency order, plus one merge) live in provision_channels.CHANNEL_RENAMES
#: and CHANNEL_MERGES. Two maps because they are not the same statement: a merged
#: room's cell points at a *different* room, not at its own new name.
#:
#: - `bangalore -> bengaluru`: superseded city name (see below).
#: - `london-meetup-organizers -> london-organizers`: London had two organizer
#:   rooms — a 2022 public one with 41 members and a stated organizer purpose, and
#:   a 2023 private one with 18, only 6 people in both. Decided 2026-08-10 to give
#:   the name to the private room; the public one is renamed away first.
#: - `bay-area-sf-organizers -> bay-area-organizers` and
#:   `southbay-chapter-leads -> bay-area-organizers`: San Francisco and Silicon
#:   Valley share `#bay-area`, so the `<public>-organizers` convention collides for
#:   them. Their organizer rooms were 28 and 21 people with **18 in both**, so they
#:   are merged into one rather than kept apart on a technicality.
RENAMES = {
    "bangalore": "bengaluru",
    "london-meetup-organizers": "london-organizers",
    "bay-area-sf-organizers": "bay-area-organizers",
    "southbay-chapter-leads": "bay-area-organizers",
}

#: A folder is written as a URL, not a bare id: the column is read by organizers
#: as often as by scripts, and an opaque 33-character id helps nobody. Scripts
#: recover the id with folder_id().
FOLDER_URL = "https://drive.google.com/drive/folders/%s"


def folder_id(value):
    """The Drive id inside a Chapter Folder cell, or '' if it holds neither."""
    v = (value or "").strip()
    if not v or v == NO_RESOURCE:
        return ""
    return v.rsplit("/", 1)[-1].split("?")[0] if "/" in v else v


# ----------------------------------------------------------------------------
# Reading the grid
# ----------------------------------------------------------------------------
def read_grid(city_filter=None):
    """Return (rows, layout, chapters).

    chapters = [{row, city, country, current: {column -> value}}]. Read `A:AZ`
    and resolved by header name like every other engine — this tab's columns have
    moved twice now, most recently by this very change.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]

    dups = sorted({h for h in headers if h and headers.count(h) > 1})
    if dups:
        sys.exit("ABORT: duplicate column header(s) %s on %s."
                 % (", ".join(map(repr, dups)), CHAPTERS_TAB))

    missing = [c for c in RESOURCE_COLUMNS if c not in headers]
    if missing:
        sys.exit("ABORT: %s missing from %s. Run migrate_resource_columns.py "
                 "first — it opens these columns and seeds them."
                 % (", ".join(map(repr, missing)), CHAPTERS_TAB))

    header_index(headers, CHAPTERS_TAB, "City", "Country", *RESOURCE_COLUMNS)
    layout = {"headers": headers, "index": {h: i for i, h in enumerate(headers) if h}}
    idx = layout["index"]

    chapters = []
    for rownum, row in enumerate(rows[1:], start=2):
        city = cell(row, idx["City"])
        if not city:
            continue
        if city_filter and fold_city(city) != fold_city(city_filter):
            continue
        chapters.append({
            "row": rownum, "city": city, "country": cell(row, idx["Country"]),
            "current": {c: cell(row, idx[c]).strip() for c in RESOURCE_COLUMNS},
        })
    if not chapters:
        sys.exit("ABORT: no chapter rows matched%s."
                 % (" %r" % city_filter if city_filter else ""))
    return rows, layout, chapters


def open_cells(chapters, column):
    """Chapters whose `column` is genuinely unanswered.

    Both a real value and NO_RESOURCE close the question; only a blank is open.
    Collapsing those two is the bug this function exists to prevent — it would
    re-propose a channel for a city a human already recorded as having none, on
    every single run, forever.
    """
    return [c for c in chapters if not c["current"][column]]


# ----------------------------------------------------------------------------
# Chapter Folder — derived from Drive
# ----------------------------------------------------------------------------
def propose_folders(chapters):
    """Return (proposals, near_misses, folderless).

    Same folding and the same generic-token stoplist as every other engine, so a
    near-miss here is a near-miss there: 'San Diego' never resolves to the
    'San Francisco' folder in one engine and stays unmatched in another.
    """
    folders = [f for f in list_chapter_folders() if f["name"] != TEMPLATE_FOLDER]
    by_name = {}
    for f in folders:
        by_name.setdefault(fold_city(f["name"]), f)

    proposals, near, folderless = [], [], []
    for ch in open_cells(chapters, FOLDER_COLUMN):
        key = fold_city(ch["city"])
        hit = by_name.get(key)
        if hit:
            proposals.append({"row": ch["row"], "city": ch["city"],
                              "column": FOLDER_COLUMN,
                              "value": FOLDER_URL % hit["id"],
                              "why": "folder %r" % hit["name"]})
            continue
        tokens = {t for t in key.split() if t and t not in GENERIC_CITY_TOKENS}
        cands = [f["name"] for f in folders
                 if tokens & {t for t in fold_city(f["name"]).split()
                              if t not in GENERIC_CITY_TOKENS}]
        if cands:
            near.append((ch["city"], cands))
        else:
            folderless.append(ch["city"])
    return proposals, near, folderless


# ----------------------------------------------------------------------------
# Slack channels — exact matches only
# ----------------------------------------------------------------------------
def propose_channels(chapters, chans, cfg):
    """Return (proposals, candidates, countries_without_channel).

    Every proposal is an EXACT name hit. `candidates` is the human's queue and is
    never written; `countries_without_channel` is the gap list for the country
    channels that do not exist yet.
    """
    import audit_organizers as ao

    live = {c["name"]: c for c in chans if not c["is_archived"]}
    suffixes = tuple(cfg["organizer_suffixes"])
    proposals, candidates, missing_countries = [], [], {}

    for ch in chapters:
        vs = ao.variants(ch["city"])

        # --- public chapter channel -------------------------------------
        if not ch["current"]["Slack Channel"]:
            hit = None
            for prefix in cfg["public_prefixes"]:
                for v in sorted(vs):
                    c = live.get(prefix + v)
                    # A private room is never a city's public home — the audit
                    # aborts outright if the map claims one, so proposing one
                    # here would just be writing a future abort into the sheet.
                    if c and not c["is_private"]:
                        hit = c
                        break
                if hit:
                    break
            if hit:
                proposals.append({"row": ch["row"], "city": ch["city"],
                                  "column": "Slack Channel", "value": hit["name"],
                                  "why": "exact"})
            else:
                near = _candidates(ch["city"], chans, suffixes, ao)
                if near:
                    candidates.append((ch["city"], "Slack Channel", near))

        # --- private organizer channel ----------------------------------
        if not ch["current"]["Organizer Channel"]:
            hit = None
            for suffix in suffixes:
                for v in sorted(vs):
                    c = live.get(v + suffix)
                    if c:
                        hit = c
                        break
                if hit:
                    break
            if hit:
                proposals.append({"row": ch["row"], "city": ch["city"],
                                  "column": "Organizer Channel",
                                  "value": hit["name"], "why": "exact"})

        # --- country channel --------------------------------------------
        # Matched on the row's OWN Country cell, exactly. This finds #india,
        # #norway, #turkey. It deliberately cannot find #africa, #nordics-public
        # or #spanish-speaking: those serve several countries and no rule derives
        # them from a country name, so they stay whatever a human put there.
        if not ch["current"]["Country Channel"] and ch["country"]:
            key = ao.fold(ch["country"])
            c = live.get(key)
            if c and not c["is_private"]:
                proposals.append({"row": ch["row"], "city": ch["city"],
                                  "column": "Country Channel", "value": c["name"],
                                  "why": "Country=%s" % ch["country"]})
            else:
                missing_countries.setdefault(ch["country"], []).append(ch["city"])

    return proposals, candidates, missing_countries


def plan_channels(chapters, chans, ao):
    """Convention names for cells no exact match filled: `--plan` mode.

    This writes the name a channel *will* have, not one it has — the sheet stops
    meaning "the channel that exists" for these cells until the channels are
    created. That is a deliberate, temporary state, and it has a cost worth
    stating plainly: `assert_aliases_resolve()` **aborts the audit** on a named
    channel that does not resolve. That abort is not a bug to route around; it is
    the check that stops a chapter being silently downgraded to "no channel".
    So the plan is only worth writing if the channels really are about to exist.

    Two limits, both deliberate:

    - **Every chapter gets an organizer channel planned**, including the 26 with
      no accepted organizer yet. The room is then ready for a chapter's first
      organizer instead of being a thing someone has to remember to create, and
      the naming stays uniform across the estate.
    - **A cell that already names something is never touched**, including
      `#españa` and the deliberate multi-chapter room `#bay-area`. The convention
      is for chapters that have nothing, not a reason to rename a working room.
    """
    live = {c["name"] for c in chans if not c["is_archived"]}
    planned = []

    for ch in chapters:
        slug = ao.fold(ch["city"])
        if not slug:
            continue
        public = ch["current"]["Slack Channel"]
        if not public:
            public = slug
            planned.append({"row": ch["row"], "city": ch["city"],
                            "column": "Slack Channel", "value": slug,
                            "why": "exists" if slug in live else "TO CREATE"})
        if not ch["current"]["Organizer Channel"]:
            # Derived from the chapter's OWN channel, not from the city slug.
            # SF's room is #bay-area, so its organizers belong in
            # #bay-area-organizers — `san-francisco-organizers` would be a
            # channel named after a chapter that, in Slack, does not go by that
            # name. The public cell wins because it is the name the community
            # actually answers to.
            base = slug if public == NO_RESOURCE else public
            name = organizer_name(base)
            planned.append({"row": ch["row"], "city": ch["city"],
                            "column": "Organizer Channel", "value": name,
                            "why": "exists" if name in live else "TO CREATE"})
        if not ch["current"]["Country Channel"] and ch["country"]:
            name = COUNTRY_CHANNELS.get(ch["country"]) or ao.fold(ch["country"])
            if name:
                planned.append({"row": ch["row"], "city": ch["city"],
                                "column": "Country Channel", "value": name,
                                "why": "exists" if name in live else "TO CREATE"})
    return planned


#: The suffix a planned organizer channel takes. One spelling, used both when
#: planning the name and when checking whether an existing cell already follows
#: the rule — two copies would disagree the first time either changed.
ORGANIZER_SUFFIX = "-organizers"


def organizer_name(public):
    """The organizer channel belonging to the room named `public`."""
    return public + ORGANIZER_SUFFIX


def dedupe(proposals):
    """First proposal per (row, column) wins.

    Two steps can legitimately reach the same cell — correcting Madrid's misfiled
    `#españa` sets its Country Channel, and the Spain override would set the same
    cell to the same value. Writing it twice is harmless but it inflates every
    count in the report, and a reader checking "13 proposed" against the listed
    rows finds seven where four belong. First wins because corrections run before
    overrides and are the more specific statement.
    """
    out, seen = [], set()
    for p in proposals:
        key = (p["row"], p["column"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def propose_column_corrections(chapters, ao):
    """Move a misfiled value into the column it belongs in, and repair the row.

    Three edits per affected row, because a misfiling is never just one cell:
    the value leaves the wrong column, the right column takes it (displacing
    whatever the convention had planned there), and anything *derived* from the
    wrong value — the organizer channel — is recomputed from the corrected one.
    Doing only the first would leave the chapter with no channel at all; doing
    only the first two would leave `españa-organizers` behind, naming a room for
    a chapter that no longer claims `#españa`.
    """
    out = []
    for ch in chapters:
        for value, (wrong, right) in MISFILED_COLUMNS.items():
            if ch["current"][wrong] != value:
                continue
            slug = ao.fold(ch["city"])
            out.append({"row": ch["row"], "city": ch["city"], "column": wrong,
                        "value": slug, "was": value,
                        "why": "#%s is a %s, not this chapter's own room"
                               % (value, right.replace(" Channel", "").lower())})
            if ch["current"][right] != value:
                out.append({"row": ch["row"], "city": ch["city"], "column": right,
                            "value": value, "was": ch["current"][right],
                            "why": "#%s belongs here" % value})
            want_org = organizer_name(slug)
            if ch["current"]["Organizer Channel"] != want_org:
                out.append({"row": ch["row"], "city": ch["city"],
                            "column": "Organizer Channel", "value": want_org,
                            "was": ch["current"]["Organizer Channel"],
                            "why": "follows #%s" % slug})
    return out


def propose_country_overrides(chapters):
    """Point a country cell at the room that really serves it (COUNTRY_CHANNELS).

    Only replaces a planned value — a cell naming a channel a human confirmed is
    left alone, same rule as everywhere else.
    """
    out = []
    for ch in chapters:
        want = COUNTRY_CHANNELS.get(ch["country"])
        current = ch["current"]["Country Channel"]
        if want and current and current != want and current != NO_RESOURCE:
            out.append({"row": ch["row"], "city": ch["city"],
                        "column": "Country Channel", "value": want,
                        "was": current,
                        "why": "#%s is %s's room" % (want, ch["country"])})
    return out


def propose_organizer_alignment(chapters, live):
    """Repoint a PLANNED organizer channel at the chapter's real channel name.

    Fixes cells written before the organizer name was derived from the public
    channel rather than the city slug — `san-francisco-organizers` becomes
    `bay-area-organizers`, because #bay-area is what that chapter is actually
    called.

    The safety rule is narrow and load-bearing: **only a cell naming a channel
    that does not exist is touched.** That is what makes this incapable of
    renaming a real room out of the sheet — `#nyc-chapter-leads`,
    `#bay-area-sf-organizers`, `#southbay-chapter-leads`, `#berlin-organizers`,
    `#london-meetup-organizers` and `#pune-organizers` all exist, so all six are
    left exactly as they are, whatever the convention would have said.
    """
    out = []
    for ch in chapters:
        current = ch["current"]["Organizer Channel"]
        public = ch["current"]["Slack Channel"]
        if not current or current == NO_RESOURCE or current in live:
            continue          # blank, answered, or a channel that really exists
        if not public or public == NO_RESOURCE:
            continue
        want = organizer_name(public)
        if want != current:
            out.append({"row": ch["row"], "city": ch["city"],
                        "column": "Organizer Channel", "value": want,
                        "was": current,
                        "why": "follows #%s" % public})
    return out


def propose_renames(chapters):
    """Point a cell at a channel's new name when RENAMES says it is being renamed.

    The one case where a *filled* channel cell is changed. It is safe precisely
    because it is not a guess: the pair is written down, `provision_channels.py`
    performs the matching `conversations.rename`, and a rename carries every
    member and the whole history across — so the cell and the workspace end up
    agreeing again. Between the two runs the audit aborts on this row, exactly as
    it does for every other planned-but-not-yet-created channel.
    """
    out = []
    for ch in chapters:
        for column in CHANNEL_COLUMNS:
            current = ch["current"][column]
            if current in RENAMES:
                out.append({"row": ch["row"], "city": ch["city"],
                            "column": column, "value": RENAMES[current],
                            "was": current,
                            "why": "renaming #%s -> #%s" % (current,
                                                            RENAMES[current])})
    return out


def _candidates(city, chans, suffixes, ao):
    """Plausible public channels for a human to confirm. Never written.

    Errs towards showing too many, on the audit's reasoning: matching whole
    variants against single tokens missed the likeliest real name (#cape-town-ai
    for Cape Town), so it compares the city's *tokens* as a subset instead.
    """
    vs = ao.variants(city)
    tokens = {t for t in ao.fold(city).split("-") if t}
    out = []
    for c in chans:
        if c["is_archived"] or c["is_private"] or c["name"].endswith(suffixes):
            continue
        chan_tokens = set(c["name"].split("-"))
        if (tokens and tokens <= chan_tokens) or (vs & chan_tokens):
            out.append(c["name"])
    return sorted(out)


# ----------------------------------------------------------------------------
# Report / write
# ----------------------------------------------------------------------------

#: Characters a Slack channel name can never contain. Deliberately minimal — the
#: local-language name #españa must never be flagged —
#: but enough to catch a URL, an email address or a sentence pasted into the
#: wrong column. The "filled" count treats any non-blank cell as healthy, so
#: without this check a pasted Drive URL reads as a mapped channel forever
#: (Montréal's Slack Channel cell held its folder URL for a while).
_CHANNEL_CELL_BAD = re.compile(r"[\s/:#@,]")


def malformed_channel_cells(chapters):
    """(row, city, column, value) for filled channel cells that cannot possibly
    name a Slack channel.

    Format-only, so it needs no Slack auth and still runs when the token is dead
    and the live does-it-resolve check (the audit's) cannot.
    """
    out = []
    for ch in chapters:
        for col in CHANNEL_COLUMNS:
            v = ch["current"][col]
            # Case-insensitive sentinel: a human typing "None" or "NONE" means
            # the sentinel, not a channel literally named None.
            if v and v.lower() != NO_RESOURCE and _CHANNEL_CELL_BAD.search(v):
                out.append((ch["row"], ch["city"], col, v))
    return out


def report(chapters, proposals, near, folderless, candidates, missing_countries,
           did_slack):
    print("Chapter resource map — %d chapter rows\n" % len(chapters))

    for column in (FOLDER_COLUMN,) + CHANNEL_COLUMNS + (HANDLES_COLUMN,):
        mine = [p for p in proposals if p["column"] == column]
        filled = sum(1 for c in chapters if c["current"][column]
                     and c["current"][column] != NO_RESOURCE)
        none_ = sum(1 for c in chapters if c["current"][column] == NO_RESOURCE)
        blank = sum(1 for c in chapters if not c["current"][column])
        skip = "" if (column == FOLDER_COLUMN or did_slack) else "  [skipped]"
        print("%-18s %3d filled  %3d none  %3d blank  -> %d proposed%s"
              % (column, filled, none_, blank, len(mine), skip))
        for p in sorted(mine, key=lambda x: x["row"]):
            # A rewritten column shows what it replaces: the value is a roster,
            # and "+ @a; @b" alone hides whether anyone was dropped.
            if p.get("was"):
                print("    ~ row %-3d %-18s %s\n        was: %s"
                      % (p["row"], p["city"], p["value"], p["was"]))
            else:
                print("    + row %-3d %-18s %s   (%s)"
                      % (p["row"], p["city"], p["value"], p["why"]))

    if near:
        print("\nNear-miss folders (%d) — NOT written, confirm by hand:" % len(near))
        for city, cands in near:
            print("  %-18s ~ %s" % (city, ", ".join(cands)))
    if folderless:
        print("\nNo Drive folder at all (%d) — the aaif-create-chapter queue:"
              % len(folderless))
        print("  " + ", ".join(folderless))

    if candidates:
        print("\nPossible channels (%d) — NOT written, confirm and type them in:"
              % len(candidates))
        for city, column, cands in candidates:
            print("  %-18s %-18s ~ %s" % (city, column, ", ".join(cands)))

    if missing_countries:
        print("\nCountries with chapters but no channel named after them (%d):"
              % len(missing_countries))
        for country, cities in sorted(missing_countries.items()):
            print("  %-20s %d chapter(s): %s"
                  % (country, len(cities), ", ".join(sorted(cities))))
        print("  Either create the channel, or put the regional room that already"
              "\n  serves them in Country Channel (#africa, #nordics-public, ...),"
              "\n  or write %r to record that there is none." % NO_RESOURCE)


def apply(proposals, layout):
    """Write every proposal in one values batchUpdate.

    RAW, matching sync_chapters: a channel name is text and must never be
    interpreted. Each proposal is a single cell, so there is no full-width write
    that could clear a neighbouring column.
    """
    data = [{"range": "'%s'!%s%d" % (CHAPTERS_TAB,
                                     col_letter(layout["index"][p["column"]]),
                                     p["row"]),
             "values": [[p["value"]]]}
            for p in proposals]
    gws_json("sheets", "spreadsheets", "values", "batchUpdate",
             params={"spreadsheetId": CHAPTERS_ID},
             body={"valueInputOption": "RAW", "data": data})
    return len(data)


def verify(proposals, city_filter):
    """Re-read and confirm every written cell holds what was proposed."""
    _, _, chapters = read_grid(city_filter)
    by_row = {c["row"]: c for c in chapters}
    bad = []
    for p in proposals:
        ch = by_row.get(p["row"])
        if not ch:
            bad.append("row %d vanished" % p["row"])
        elif ch["current"][p["column"]] != p["value"]:
            bad.append("row %d %s: %r, expected %r"
                       % (p["row"], p["column"], ch["current"][p["column"]],
                          p["value"]))
    if bad:
        sys.exit("VERIFY FAILED:\n  " + "\n  ".join(bad))
    print("\nVerified: a fresh read of every written cell matches the proposal.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="apply the proposals (default: report only)")
    ap.add_argument("--only", choices=("folder", "slack"),
                    help="folder: Drive only, no Slack auth needed. "
                         "slack: channels only.")
    ap.add_argument("--city", help="limit to one chapter")
    ap.add_argument("--plan", action="store_true",
                    help="also fill blank channel cells with the CONVENTION name "
                         "(<city>, <city>-organizers, <country>) even where the "
                         "channel does not exist yet. The sheet then names "
                         "channels to be created, and the audit will abort until "
                         "they are — see the module docstring.")
    add_redact_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)

    _, layout, chapters = read_grid(a.city)

    # Computed BEFORE slack_half, which corrects misfiled cells in place —
    # this scan must describe what the sheet actually stores, not the
    # hypothetical post-correction sheet.
    malformed = malformed_channel_cells(chapters)

    proposals, near, folderless = [], [], []
    if a.only != "slack":
        proposals, near, folderless = propose_folders(chapters)

    candidates, missing_countries, unresolved, did_slack = [], {}, [], False
    if a.only != "folder":
        more, candidates, missing_countries, unresolved, did_slack = slack_half(
            chapters, plan=a.plan)
        proposals += more

    report(chapters, proposals, near, folderless, candidates, missing_countries,
           did_slack)
    if malformed:
        print("\nMalformed channel cell(s) (%d) — not a possible channel name; "
              "fix by hand (or write %r):" % (len(malformed), NO_RESOURCE))
        for row, city, col, v in malformed:
            print("  row %-3d %-18s %-18s %r" % (row, city, col, v[:70]))
    if unresolved:
        print("\nAccepted organizers with no Slack account (%d) — they cannot be "
              "invited until they join:" % len(unresolved))
        for city, name in unresolved:
            print("  %-18s %s" % (city, redact_name(name)))
    if a.plan:
        todo = [p for p in proposals if p.get("why") == "TO CREATE"]
        print("\n%d planned channel(s) DO NOT EXIST yet. Until they are created "
              "the\norganizer audit will abort — that check is what stops a "
              "chapter being\nsilently downgraded to 'no channel'." % len(todo))

    # `--only folder` is a choice; a dead token is not. Only the involuntary
    # skip is "partial" — and it goes to STDOUT with a fixed prefix because
    # nightly.py reads the log for it: the exit code alone cannot tell "in
    # sync" from "only half-checked", and a token that dies permanently in CI
    # must never read green forever.
    skipped_slack = a.only != "folder" and not did_slack
    if skipped_slack:
        print("\nPARTIAL: Slack unavailable — the channel columns were NOT "
              "checked this run. Fix Slack auth and re-run for full coverage.")

    if not a.write:
        print("\nReport only (%d proposed). Re-run with --write to apply."
              % len(proposals))
        # Shared engine exit convention: report mode exits 0 when in sync, 2 when
        # anything needs a human or a write — a malformed cell is drift, and a
        # half-checked run is too.
        return 2 if (proposals or malformed or skipped_slack) else 0
    if not proposals:
        print("\nNo changes needed.")
        return 2 if skipped_slack else 0

    # Recompute from a fresh read before writing — the report above is the
    # human-reading window, and an edit made during it must win. The test is the
    # same for both kinds of proposal: the cell must still hold what it held when
    # the proposal was built. For a fill that means still blank; for a rewritten
    # column it means still the old value. Comparing against "is it blank" would
    # have silently clobbered every hand-corrected handle list.
    _, layout, fresh = read_grid(a.city)
    now = {(c["row"], col): c["current"][col]
           for c in fresh for col in RESOURCE_COLUMNS}
    def unchanged(p):
        return now.get((p["row"], p["column"]), None) == p.get("was", "")
    stale = [p for p in proposals if not unchanged(p)]
    proposals = [p for p in proposals if unchanged(p)]
    if stale:
        print("\n%d proposal(s) dropped — the cell changed while the report was "
              "being read:" % len(stale))
        for p in stale:
            print("  row %d %s (now %r)"
                  % (p["row"], p["column"], now.get((p["row"], p["column"]))))
    if not proposals:
        print("\nNothing left to write.")
        return 2 if skipped_slack else 0

    n = apply(proposals, layout)
    print("\nWrote %d cell(s)." % n)
    verify(proposals, a.city)
    return 2 if skipped_slack else 0


#: How a person with no Slack account is written into Organizer Handles. The
#: column answers "who should be in this channel", and someone who cannot be
#: invited is precisely who an organizer needs to see — writing only the
#: resolvable handles would make the roster look complete while quietly dropping
#: the people who need chasing. Their NAME goes in, never their email: this sheet
#: is world-readable.
NO_ACCOUNT = "%s (no Slack account)"


def propose_handles(chapters, ao, api, slackmod):
    """Rewrite Organizer Handles from the intake's accepted organizers.

    A REPLACEMENT, not a fill — see REWRITTEN_COLUMNS. Each proposal carries the
    value observed at read time in `was`, so the write step can drop it if a
    human edited the cell in between rather than clobbering them.

    Order follows the intake, matching the `Organizers` name column beside it, so
    the two read as the same list in the same order.
    """
    people, _ = ao.read_intake()
    by_city = {}
    for p in people:
        if p["city"]:
            by_city.setdefault(fold_city(p["city"]), []).append(p)

    resolved = slackmod.lookup_emails(
        api, {p["email"] for p in people if p["email"]})

    proposals, unresolved = [], []
    for ch in chapters:
        roster = by_city.get(fold_city(ch["city"]), [])
        parts = []
        for p in roster:
            hit = resolved.get(p["email"]) or {}
            if hit.get("id") and hit.get("name"):
                parts.append("@" + hit["name"])
            else:
                parts.append(NO_ACCOUNT % p["name"])
                unresolved.append((ch["city"], p["name"]))
        value = "; ".join(parts)
        current = ch["current"][HANDLES_COLUMN]
        # An empty roster must not overwrite a human's `none`: proposing ""
        # there converts "a human answered" back into "nobody has looked",
        # the exact confusion the blank-vs-none distinction exists to prevent.
        if not value and current == NO_RESOURCE:
            continue
        if value != current:
            proposals.append({"row": ch["row"], "city": ch["city"],
                              "column": HANDLES_COLUMN, "value": value,
                              "was": current,
                              "why": "%d organizer(s)" % len(roster)})
    return proposals, unresolved


def slack_half(chapters, plan=False):
    """Proposals from live Slack, or an empty result if Slack is unreachable.

    A dead Slack token must not sink the folder half of the run: the columns come
    from unrelated systems, and one being unreachable is no reason to withhold
    the other's proposal. The report says which half was skipped so an empty
    channel section is never mistaken for "nothing to do".
    """
    import audit_organizers as ao
    from aaif_events import slack as slackmod

    try:
        api = slackmod.Slack()
        api.require_scopes("channels:read", "groups:read",
                           "users:read", "users:read.email")
        chans = slackmod.channels(api)
    except (slackmod.SlackError, SystemExit) as exc:
        print("Slack unavailable (%s) — channel columns skipped.\n"
              "Set AAIF_SLACK_READ_TOKEN or AAIF_SLACK_WRITE_TOKEN (environment, or "
              "the repo-root .env — the Slack CLI credential expired for good in "
              "2026-08), then re-run for those.\n" % exc, file=sys.stderr)
        return [], [], {}, [], False

    cfg = ao.load_config()
    proposals, candidates, missing = propose_channels(chapters, chans, cfg)
    # Before the convention fills anything: a cell being renamed is not blank, so
    # plan mode would skip it, and the sheet would keep the superseded name.
    proposals += propose_renames(chapters)
    # Corrections first: they change what the other steps see. A row still
    # claiming #españa as its own channel would otherwise be realigned to
    # españa-organizers a second time, undoing the fix in the same run.
    corrections = dedupe(propose_column_corrections(chapters, ao)
                         + propose_country_overrides(chapters))
    for c in corrections:
        for ch in chapters:
            if ch["row"] == c["row"]:
                ch["current"][c["column"]] = c["value"]
    proposals += corrections
    proposals += propose_organizer_alignment(
        chapters, {c["name"] for c in chans if not c["is_archived"]})

    if plan:
        # Exact matches first, then the convention fills whatever is still blank.
        # Order matters: an exact hit must win, or a chapter whose room really is
        # #austin-area would be planned onto a #austin that nobody is in.
        for p in proposals:
            for ch in chapters:
                if ch["row"] == p["row"]:
                    ch["current"][p["column"]] = p["value"]
        proposals += plan_channels(chapters, chans, ao)

    print("  resolving organizer handles (~1.5s per person) ...", file=sys.stderr)
    handles, unresolved = propose_handles(chapters, ao, api, slackmod)
    proposals += handles
    # One proposal per cell: after a rename lands in Slack, propose_renames and
    # propose_organizer_alignment both fire on the same cell with the same
    # value — harmless, but the count inflates and the batch carries a
    # duplicate range. First wins, and corrections were appended first.
    return dedupe(proposals), candidates, missing, unresolved, True


if __name__ == "__main__":
    sys.exit(main())
