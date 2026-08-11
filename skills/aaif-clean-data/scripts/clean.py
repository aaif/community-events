#!/usr/bin/env python3
"""Data cleanup engine for the AAIF Community Intake Ops sheet.

Operates on the SOURCE tab (`Form Responses`) so cleaned values flow through the
computed role tabs. Reads/writes columns by HEADER NAME, never by letter.

Subcommands:
    scan            Detect & propose mechanical normalizations (dry-run). --json for data.
    apply FILE      Apply an approved list of changes (JSON: [{row,header,value}]),
                    writing to Form Responses and noting what changed per row in the
                    "Autofixes" column (created if missing).
    install-flags   Add/refresh the live "Issues" column + bright-red row rule on the
                    role tabs (Organizers/Hosts/Speakers).
    install-colors  Label City (Existing)/City (New) + (re)install the violet/
                    amber/green provenance rules on the role tabs. Idempotent.

Nothing is written unless you run `apply`, `install-flags`, or `install-colors`.
`scan` only reports.
"""
import argparse, json, os, re, subprocess, sys, tempfile, unicodedata

SHEET_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
SOURCE = "Form Responses"
ROLE_TABS = {"Organizers": 537599805, "Hosts": 1923799643, "Speakers": 1491913647}
BRIGHT_RED = {"red": 0.91, "green": 0.26, "blue": 0.21}

# Common person fields live on these source headers.
H_NAME, H_EMAIL, H_LINKEDIN, H_CITY = "Full name", "Email", "LinkedIn URL", "City"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Per-row provenance for applied edits is recorded in this Form Responses column
# (not a separate log tab). Each note is written as "<phrase> -> <new value>": the
# value is carried so a second edit to the SAME field is not deduped away as a
# repeat of the first (see apply()). Falls back to "<field> updated -> <value>".
AUTOFIX_COL = "Autofixes"
AUTOFIX_PHRASE = {"LinkedIn URL": "LinkedIn normalized", "Email": "email normalized",
                  "Full name": "name normalized", "Resolved City": "city resolved",
                  "Extracted City": "city extracted"}
# The Autofixes cell is a delimited list: ";" joins phrases within one run, "|"
# joins runs. A phrase therefore may NOT contain either character — autofix_note
# strips them out of the embedded value, because a phrase that cannot be split
# back out never matches `seen` and re-appends on every single run.
NOTE_SEPARATORS = re.compile(r"[|;]")

# Columns on Form Responses that are DERIVED and must never receive a literal.
# `Resolved City` became an ARRAYFORMULA on 2026-08-10 (City when it is a real
# city, else Extracted City). Writing a value into any cell of that spill range
# collapses the whole column to #REF!, and the old documented fix for a wrong
# city was to do exactly that — so the guard has to live in apply(), not in a
# doc note. Correct a city via `City` or `Extracted City` instead.
DERIVED_COLUMNS = ("Resolved City",)


# ---------- gws helpers ----------
def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gws error: {' '.join(args[:4])}...\n{out.stderr.strip()}")
    txt = out.stdout
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def read_tab(tab):
    """Read a whole tab. The range is the bare tab name — deliberately NOT a
    bounded one, and there is no parameter to make it bounded again: a hardcoded
    window (this used to be "A1:CJ") silently truncates the moment a column is
    appended, and the column it drops first is the newest one — which is how
    `Autofixes` (at CK) fell outside the window and made apply() overwrite prior
    notes instead of appending them. Sheets trims trailing empty rows/columns.

    The range string is ONLY a sheet title, so there is no "!" to disambiguate
    and `Form Responses` needs no quoting — unlike an "A1"-style range, where a
    name containing spaces must be quoted before the "!"."""
    d = gws(["sheets", "spreadsheets", "values", "get", "--params",
             json.dumps({"spreadsheetId": SHEET_ID, "range": tab,
                         "majorDimension": "ROWS"}), "--format", "json"])
    vals = d.get("values", [])
    if not vals:
        return [], []
    hdr = [h.strip() for h in vals[0]]
    rows = [r + [""] * (len(hdr) - len(r)) for r in vals[1:]]
    return hdr, rows


def colletter(n):  # 1-based -> A1 letter
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------- role-tab provenance colors ----------
# City (Existing) = the submitted dropdown; City (New) = the resolved "Other"
# city. They are the adjacent City,Resolved City pair the role-tab array formula
# emits, but their POSITION moves whenever a column is inserted upstream — they
# started at G/H and were observed at H/I in 2026-07. So they are located by
# header name at run time (find_city_cols) and never hardcoded; a fixed letter
# here is exactly the bug that made install_colors abort after the pair shifted.
VIOLET = {"red": 0.60, "green": 0.20, "blue": 0.90}   # Status = Existing (from MLOps)
AMBER  = {"red": 0.99, "green": 0.76, "blue": 0.30}   # net-new resolved city
GREEN  = {"red": 0.72, "green": 0.88, "blue": 0.70}   # existing form city
# The labels install_colors WRITES, named once so a rename cannot update the
# write sites while leaving the read side (CITY_SRC_HEADERS) unable to recognise
# what was just written.
CITY_LABELS = ("City (Existing)", "City (New)")
# Accepted role-tab header names for each half of the pair: pre-label
# ("City"/"Resolved City") and post-label, so discovery works both before and
# after install_colors renames them.
CITY_SRC_HEADERS = ({"City", CITY_LABELS[0]}, {"Resolved City", CITY_LABELS[1]})

STATUS_HEADER = "Status"


def violet_formula(status_col):
    """Violet: this row came across from the MLOps community rather than the form.

    Built from the DISCOVERED Status column, not a hardcoded "$A2". Widening
    STALE_PATTERNS to any column only fixed *recognition* — after a column is
    inserted left of Status, Sheets rewrites the stored rule to "=$B2=..." and we
    correctly delete it, but a pinned builder would then reinstall a rule testing
    column A, which is no longer Status. Recognition and correctness are separate
    problems and this is the one that mattered."""
    return f'=${colletter(status_col)}2="Existing (from MLOps)"'


def find_status_col(hdr, tab):
    """1-based Status column, by header name. Aborts rather than assuming A."""
    if STATUS_HEADER not in hdr:
        sys.exit(f"ABORT: {tab} has no {STATUS_HEADER!r} column; the violet "
                 f"provenance rule has nothing to test. Headers: {hdr}")
    return hdr.index(STATUS_HEADER) + 1


def amber_formula(new_col):
    """Amber: this row has a net-new resolved city."""
    return f'=${colletter(new_col)}2<>""'


def green_formula(existing_col):
    """Green: the submitted city is a real one — non-empty and not ANY "Other..."
    placeholder (the form has both "Other" and "Other (PLEASE TELL US WHERE IN
    NEXT QUESTION)"), so match the prefix rather than the exact string."""
    c = colletter(existing_col)
    return f'=AND(${c}2<>"",LEFT(${c}2,5)<>"Other")'


def find_city_cols(hdr, tab):
    """1-based (City (Existing), City (New)) located by header name.

    Requires EXACTLY ONE adjacent pair, which is what the role-tab array formula
    emits. Aborts loudly rather than guessing in every ambiguous case, because
    the write target is a formula-driven role tab where a stray literal #REF!s
    the whole sheet, and recoloring the wrong columns would paint provenance
    onto unrelated data:

    - zero pairs   -> the columns moved or were renamed;
    - two+ pairs   -> a migration left a stale block next to the live one, and
                      taking the leftmost would style the stale one;
    - half-labeled -> usually a header renamed by hand, or an upstream change;
                      an interrupted run is unlikely to produce it, since both
                      labels go out in a single values.batchUpdate. Either way
                      it means the two halves disagree about which generation of
                      naming is in force, so say what is expected rather than
                      guessing which one to keep."""
    hits = [(i + 1, i + 2) for i in range(len(hdr) - 1)
            if hdr[i] in CITY_SRC_HEADERS[0] and hdr[i + 1] in CITY_SRC_HEADERS[1]]
    if len(hits) != 1:
        sys.exit(f"ABORT: {tab} has {len(hits)} adjacent City / Resolved City pair(s) "
                 f"at {hits}; expected exactly 1. Headers: {hdr}")
    existing, new = hits[0]
    if (hdr[existing - 1] == CITY_LABELS[0]) != (hdr[new - 1] == CITY_LABELS[1]):
        sys.exit(f"ABORT: {tab} city pair is half-labeled "
                 f"({hdr[existing - 1]!r} / {hdr[new - 1]!r}). Set the two headers to "
                 f"either {CITY_LABELS[0]!r}/{CITY_LABELS[1]!r} or "
                 f"'City'/'Resolved City', then re-run.")
    return existing, new


# Our own rules are recognised by SHAPE (any column) + one of our three colors,
# not by exact formula text: text pinned to a column letter stops matching the
# moment Sheets rewrites a rule for a column insert, so a refresh stacks a
# duplicate instead of replacing it. The color test plus the err_formula
# exclusion in is_ours are what keep the broad amber shape from also matching the
# bright-red Issues rule, whose formula has the identical shape. The green
# patterns backreference their column, so a rule painting H while testing Z is
# NOT ours. These regexes are hand-written and nothing checks them against the
# builders above — an earlier release derived both from one set of constants;
# that construction-time guarantee is gone, and
# test_recognises_installed_rules_at_any_column is now the only thing that
# catches drift. Do not edit a builder without running it.
STALE_PATTERNS = (
    re.compile(r'^=\$[A-Z]+2="Existing \(from MLOps\)"$'),                     # violet
    re.compile(r'^=\$[A-Z]+2<>""$'),                                           # amber
    re.compile(r'^=AND\((?P<c>\$[A-Z]+)2<>"",LEFT\((?P=c)2,5\)<>"Other"\)$'),  # green
    re.compile(r'^=AND\((?P<c>\$[A-Z]+)2<>"",(?P=c)2<>"Other"\)$'),            # legacy green
)


def _rgb8(c):
    """Quantize a color dict to 8-bit ints — Sheets round-trips colors at 8-bit.
    NOT sufficient for equality on its own: Sheets floors where round() rounds,
    so use `_color_eq`, never `_rgb8(a) == _rgb8(b)` and never float equality.
    The `.get(k, 0)` default is load-bearing, not defensive padding — the Sheets
    Color proto OMITS zero-valued channels, so pure red arrives as {"red": 1.0}."""
    return tuple(round(c.get(k, 0) * 255) for k in ("red", "green", "blue"))


def _color_eq(a, b, tol=1):
    """8-bit color compare with a ±1 per-channel tolerance.

    Sheets FLOORS the float->8-bit conversion where round() rounds, so a color
    written as 0.90 comes back as 229 while round(0.90*255) is 230. Exact
    equality therefore fails to recognise our own rules on the round trip —
    which silently breaks idempotency: install_colors stops seeing the rules it
    installed and stacks a duplicate set next to them on every run."""
    return all(abs(x - y) <= tol for x, y in zip(_rgb8(a), _rgb8(b)))


def error_rule_formula(hdr):
    """Formula of the bright-red data-error rule install_flags writes.

    Identified by the column it references, not by color: the red has been
    re-picked in the UI (it is (214,28,30) today, not the BRIGHT_RED this module
    writes), and a color match then fails to find it — which would drop our
    rules ABOVE the error rule instead of below, losing error priority."""
    return f'=${colletter(hdr.index("Issues") + 1)}2<>""' if "Issues" in hdr else None


def autofix_note(prior, phrases):
    """New text for a row's Autofixes cell, or None when it already says it all.

    Appends rather than overwrites, and dedupes against what the cell ALREADY
    holds — not just within one run — so re-applying a change list cannot
    accumulate "city resolved | city resolved | ...".

    Separators are stripped OUT of each incoming phrase first. `prior` is parsed
    by splitting on them, so a phrase containing one cannot be split back out,
    never matches `seen`, and re-appends on EVERY run — unbounded growth, the
    exact bug this function exists to prevent. That became reachable when apply()
    started embedding a free-text value in the phrase: `Frankfurt; Germany` and
    `Washington, DC | DC` are ordinary values for a hand-filled Resolved City.
    Both ends are stripped too, since `seen` is stripped and a trailing space
    would otherwise read as a different phrase.

    Pure — no I/O — so it is testable without touching Sheets."""
    phrases = [NOTE_SEPARATORS.sub(",", p).strip() for p in phrases]
    seen = {p.strip() for p in NOTE_SEPARATORS.split(prior) if p.strip()}
    uniq = []
    for p in phrases:
        if p and p not in uniq and p not in seen:
            uniq.append(p)
    if not uniq:
        return None
    note = "; ".join(uniq)
    return f"{prior} | {note}" if prior.strip() else note


def formula_of(cf):
    """The CUSTOM_FORMULA text of a conditional-format rule, or None."""
    c = cf.get("booleanRule", {}).get("condition", {})
    vals = c.get("values", [])
    return vals[0].get("userEnteredValue") if c.get("type") == "CUSTOM_FORMULA" and vals else None


def _is_red(cf):
    """True if this rule is the bright-red error rule, matched by color.

    Uses `_color_eq`, NOT exact `_rgb8` equality: BRIGHT_RED comes back from
    Sheets floored to (232,66,53) while _rgb8 computes (232,66,54), so an exact
    compare never recognises the rule this module itself wrote. That made
    color_rule_plan fall through to base=0 and install the provenance rules
    ABOVE the error rule — the whole-row violet then hid error highlighting on
    exactly the rows most likely to have errors."""
    bg = cf.get("booleanRule", {}).get("format", {}).get("backgroundColor")
    return bg is not None and _color_eq(bg, BRIGHT_RED)


def is_ours(cf, err_formula):
    """True if this rule is one of the three provenance rules we install, at ANY
    column position. Requires BOTH a matching formula shape and one of our three
    background colors — the amber shape (=$X2<>"") alone also matches the
    bright-red Issues rule, and deleting that would drop error highlighting.
    `err_formula` names that rule explicitly so it is never claimed even if its
    color has drifted to something near ours; it is REQUIRED rather than
    defaulted because omitting it silently disables that guard, and `None` (no
    Issues column yet) stays a legitimate value to pass."""
    f = formula_of(cf)
    if not f or (err_formula and f == err_formula):
        return False
    if not any(p.match(f) for p in STALE_PATTERNS):
        return False
    bg = cf.get("booleanRule", {}).get("format", {}).get("backgroundColor")
    return bg is not None and any(_color_eq(bg, c) for c in (VIOLET, AMBER, GREEN))


def color_rule_plan(cfs, err_formula):
    """Planner for install_colors: given a tab's conditionalFormats, return
    (stale_indices_desc, base_index). stale = our own color rules to delete
    (matched by `is_ours`: formula SHAPE plus one of our colors, so they are
    still recognised after the city pair moves); base = insert index just BELOW
    the bright-red error rule so it keeps top priority — located by `err_formula`
    when that is not None and the matched rule is actually red, else by color —
    computed from its ACTUAL position (not assumed to be index 0), adjusted for
    the stale rules deleted above it, since deletes and adds run in one batch.

    Returns base=0 when no error rule can be found at all, which installs ABOVE
    everything; that case warns on stderr. Does no I/O beyond those warnings, so
    it stays testable without touching Sheets."""
    def warn(msg):
        print("WARNING: " + msg, file=sys.stderr)

    stale = [i for i, cf in enumerate(cfs) if is_ours(cf, err_formula)]
    # Identify the error rule by formula, but VERIFY the colour: matching on
    # formula text alone and taking the first hit put our rules above the real
    # red rule whenever an operator had their own rule on the Issues column
    # sitting higher — silently, because the only warnings were on the fallback
    # branch, which never ran in that case.
    hits = [i for i, cf in enumerate(cfs) if err_formula and formula_of(cf) == err_formula]
    reds = [i for i in hits if _is_red(cfs[i])]
    if len(hits) > 1:
        warn(f"{len(hits)} rules reference the Issues column ({err_formula}) at {hits}; "
             f"using {reds[0] if reds else hits[0]}. Delete the duplicates.")
    if reds:
        red = reds[0]
    elif hits:
        red = hits[0]
        warn(f"the rule referencing {err_formula} at index {red} is not bright red; "
             f"treating it as the error rule anyway.")
    else:
        red = next((i for i, cf in enumerate(cfs) if _is_red(cf)), None)
        if red is not None:
            # Unconditional: err_formula being None means the Issues header was
            # deleted or renamed, which is the state MOST worth reporting, and it
            # also silently disables the err_formula exclusion inside is_ours.
            warn(f"no rule references the Issues column "
                 f"({err_formula or 'no Issues header found'}); located the error "
                 f"rule by color instead.")
    if red is None:
        # Not the same fact as "the error rule is at index 0" — say so, because
        # base=0 installs our rules at the TOP and nothing in the success message
        # reveals whether error priority was preserved. Warn even when cfs is
        # empty but an Issues column exists: that means the red rule was deleted
        # wholesale, not that this is a fresh tab.
        if cfs or err_formula:
            warn(f"no bright-red error rule found among {len(cfs)} existing rule(s); "
                 f"installing at index 0, ABOVE any error highlighting. Run "
                 f"install-flags first if that is not what you want.")
        base = 0
    else:
        base = red - sum(1 for s in stale if s < red) + 1
    return sorted(stale, reverse=True), base


# ---------- normalizers ----------
def smart_title(s):
    def fix(w):
        if not w:
            return w
        low = w.lower()
        if low in ("von", "van", "de", "da", "del", "der", "la", "di"):
            return low
        if low.startswith("mc") and len(low) > 2:
            return "Mc" + low[2:].capitalize()
        if "-" in w:
            return "-".join(fix(p) for p in w.split("-"))
        if "'" in w:
            return "'".join(p[:1].upper() + p[1:] for p in low.split("'"))
        return w[:1].upper() + w[1:].lower()
    return " ".join(fix(w) for w in s.split())


def norm_name(s):
    t = " ".join(s.split())
    # only re-case when the whole string is clearly all-lower or all-upper
    if t and (t == t.lower() or t == t.upper()) and any(c.isalpha() for c in t):
        cand = smart_title(t)
        if cand != t:
            return cand
    return t


def norm_email(s):
    return " ".join(s.split()).lower()


def norm_linkedin(s):
    t = s.strip()
    if not t:
        return t
    t = re.sub(r"^https?://", "", t, flags=re.I).strip()
    t = re.sub(r"^www\.", "", t, flags=re.I)
    t = t.split("?")[0].split("#")[0].rstrip("/")
    if t.lower().startswith("linkedin.com"):
        t = "linkedin.com" + t[len("linkedin.com"):]
        return "https://www." + t
    return "https://" + t  # leave non-linkedin hosts visible (will be flagged)


def norm_city(s):
    t = " ".join(s.split())
    if t and (t == t.lower() or t == t.upper()):
        return smart_title(t)
    return t


# ---------- city extraction: Other -> Extracted ----------
#
# The intake asks for a city two ways: a dropdown (`City`) and, when that answers
# "Other...", a free-text box (`Other`). Nothing ever read the free text, so people
# who typed their city sat unresolved forever while a human hand-filled a third
# column. This turns the free text into a real answer.
#
# Precedence, decided 2026-08-10: `City` when it is a real city, else `Extracted`.
# Raw `Other` NEVER becomes the answer — it is the input, not the output.
CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"
CHAPTERS_TAB = "Chapters & Teams"
H_OTHER = "Don't see your city above? Enter it here."
H_EXTRACTED = "Extracted City"
H_RESOLVED = "Resolved City"

# Country -> capital. Used ONLY when a country is all someone gave: a named city
# always wins, so "UAE, Dubai" is Dubai and never Abu Dhabi. Covers what the form
# has actually received plus the obvious neighbours.
CAPITALS = {
    "india": "New Delhi", "united states": "Washington DC", "usa": "Washington DC",
    "us": "Washington DC", "united states of america": "Washington DC",
    "uk": "London", "united kingdom": "London", "england": "London",
    "scotland": "Edinburgh", "ireland": "Dublin", "france": "Paris",
    "germany": "Berlin", "spain": "Madrid", "portugal": "Lisbon",
    "italy": "Rome", "netherlands": "Amsterdam", "belgium": "Brussels",
    "switzerland": "Bern", "austria": "Vienna", "denmark": "Copenhagen",
    "sweden": "Stockholm", "norway": "Oslo", "finland": "Helsinki",
    "poland": "Warsaw", "romania": "Bucharest", "bulgaria": "Sofia",
    "greece": "Athens", "turkey": "Ankara", "türkiye": "Ankara",
    "russia": "Moscow", "ukraine": "Kyiv", "luxembourg": "Luxembourg",
    "canada": "Ottawa", "mexico": "Mexico City", "brazil": "Brasilia",
    "argentina": "Buenos Aires", "colombia": "Bogota", "chile": "Santiago",
    "peru": "Lima", "nigeria": "Abuja", "ghana": "Accra", "kenya": "Nairobi",
    "uganda": "Kampala", "tanzania": "Dodoma", "ethiopia": "Addis Ababa",
    "egypt": "Cairo", "morocco": "Rabat", "south africa": "Pretoria",
    "uae": "Abu Dhabi", "united arab emirates": "Abu Dhabi",
    "saudi arabia": "Riyadh", "qatar": "Doha", "israel": "Jerusalem",
    "japan": "Tokyo", "china": "Beijing", "south korea": "Seoul",
    "korea": "Seoul", "singapore": "Singapore", "malaysia": "Kuala Lumpur",
    "indonesia": "Jakarta", "philippines": "Manila", "vietnam": "Hanoi",
    "thailand": "Bangkok", "bangladesh": "Dhaka", "pakistan": "Islamabad",
    "sri lanka": "Colombo", "nepal": "Kathmandu", "australia": "Canberra",
    "new zealand": "Wellington",
}

# Short forms and spellings people use that are not the chapter's own name. Kept
# small and explicit — a fuzzy matcher here would silently move someone's city.
ALIASES = {
    "dc": "Washington DC", "washington d.c.": "Washington DC",
    "nyc": "New York", "ny": "New York", "new york city": "New York",
    "sf": "San Francisco", "bay area": "San Francisco",
    "bangalore": "Bengaluru", "bombay": "Mumbai", "calcutta": "Kolkata",
    "madras": "Chennai", "gurgaon": "Gurugram", "delhi": "Delhi NCR",
    "new delhi": "Delhi NCR", "ncr": "Delhi NCR",
}

# Words that carry no city information, stripped before a segment is judged.
_NOISE = re.compile(r"^(?:i\s+am\s+(?:in|from|based\s+in)|based\s+in|living\s+in|"
                    r"currently\s+in|from|in|near|around|cities\s+in|city\s+of)\s+", re.I)
_SEGMENT = re.compile(r"[,/;+&()\n]| and | or ", re.I)


def fold_city(s):
    """City comparison key — MUST fold identically to sync_chapters.fold_city().

    Cannot be imported (different skill), so it is duplicated deliberately and
    asserted by the tests: a city that folds one way here and another way there
    would resolve someone into a chapter the sync engines cannot find.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", s)).strip() or s


def known_cities():
    """{folded -> canonical} for every city on the Chapters List.

    Matching against the real chapter names is what keeps "Bangalore" and
    "Vancouver BC, Canada" reaching the rows that already exist instead of
    minting near-duplicate cities the sync engines then report as near-misses.
    """
    res = gws(["sheets", "spreadsheets", "values", "batchGet", "--params",
               json.dumps({"spreadsheetId": CHAPTERS_ID,
                           "ranges": ["'%s'!A:AZ" % CHAPTERS_TAB]})])
    vals = res["valueRanges"][0].get("values", [])
    if not vals:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    hdr = [h.strip() for h in vals[0]]
    if "City" not in hdr:
        sys.exit("ABORT: no 'City' column on %r." % CHAPTERS_TAB)
    ci = hdr.index("City")
    out = {}
    for row in vals[1:]:
        c = (row[ci] if ci < len(row) else "").strip()
        if c:
            out.setdefault(fold_city(c), c)
    return out


def _clean_segment(s):
    return re.sub(r"\s+", " ", _NOISE.sub("", s.strip())).strip(" .-·")


def _canonical(text, known):
    """Chapter name for `text`, via the chapters list then the alias table."""
    f = fold_city(text)
    if not f:
        return None
    if f in known:
        return known[f]
    if f in ALIASES:
        a = ALIASES[f]
        return known.get(fold_city(a), a)
    return None


def _strip_country(seg):
    """Drop a trailing/leading country from a bare segment: 'Noida India' -> 'Noida'.

    Only ever removes a country when something else survives, so "India" alone
    still reads as a country and reaches the capital rule.
    """
    words = seg.split()
    for n in (2, 1):                      # "sri lanka" before "lanka"
        if len(words) > n and " ".join(words[-n:]).casefold() in CAPITALS:
            return " ".join(words[:-n])
        if len(words) > n and " ".join(words[:n]).casefold() in CAPITALS:
            return " ".join(words[n:])
    return seg


def extract_city(other, known):
    """Free text -> (city, why, ambiguous). ('', reason, False) when nothing is found.

    Order matters and each step exists for a row the intake actually received:
      1. the whole answer IS a chapter          "Madison, WI" / "Delhi NCR"
      2. a segment IS a chapter                 "UAE, Dubai" -> Dubai
      3. a chapter name appears anywhere        "I am in Paris, France" -> Paris
      4. a country was all they gave            "Bulgaria" -> Sofia
      5. otherwise the first real segment       "India, Gurugram" -> Gurugram
    """
    text = re.sub(r"\s+", " ", (other or "").strip())
    if not text:
        return "", "no free text", False

    hit = _canonical(_clean_segment(text), known)
    if hit:
        return hit, "whole answer matches the %s chapter" % hit, False

    segs = [_clean_segment(s) for s in _SEGMENT.split(text)]
    segs = [s for s in segs if s]
    # Collect EVERY segment that names a chapter before returning one. Answering
    # "Gujarat, India + Bengaluru/Mumbai" names two real chapters, and returning
    # the first without saying so hides a choice a human should make.
    seg_hits = [(s, _canonical(s, known)) for s in segs]
    seg_hits = [(s, h) for s, h in seg_hits if h]
    if seg_hits:
        s, hit = seg_hits[0]
        return hit, "segment %r matches the %s chapter" % (s, hit), \
            len({h for _s, h in seg_hits}) > 1

    # A chapter named anywhere inside the answer. Longest first so "Delhi NCR"
    # wins over "Delhi", earliest position breaking ties.
    found = []
    for f, canon in known.items():
        m = re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(f), fold_city(text))
        if m:
            found.append((m.start(), -len(f), canon))
    if found:
        found.sort()
        best = found[0][2]
        return best, "%s named in the answer" % best, len(found) > 1

    countries = [s for s in segs if s.casefold() in CAPITALS]
    non_country = [s for s in segs if s.casefold() not in CAPITALS]
    if countries and not non_country:
        cap = CAPITALS[countries[0].casefold()]
        return cap, "only a country (%s) — using its capital" % countries[0], len(countries) > 1

    for s in non_country:
        bare = _strip_country(s)
        if bare:
            return norm_city(bare), "first city-like segment", len(non_country) > 1
    return "", "no city found in %r" % text, False


# ---------- scan ----------
def idx(hdr, name):
    return hdr.index(name) if name in hdr else None


def scan():
    hdr, rows = read_tab(SOURCE)
    ni, ei, li, ci = (idx(hdr, h) for h in (H_NAME, H_EMAIL, H_LINKEDIN, H_CITY))
    # Reading by header name survives a reorder, not a *rename*: if a required
    # column is gone, fail loudly instead of reporting "nothing to fix".
    missing = [h for h, i in ((H_NAME, ni), (H_EMAIL, ei)) if i is None]
    if missing:
        sys.exit("ABORT: required column(s) %s not found in %r tab. Headers present: %s"
                 % (", ".join(missing), SOURCE, hdr))
    ri = idx(hdr, "Resolved City")  # if filled, City="Other" is already resolved
    changes, flags = [], []
    seen_email = {}
    for rn, row in enumerate(rows, start=2):
        # ni/ei guaranteed non-None above, so row[ni]/row[ei] are safe.
        if not (row[ni] or row[ei] or "").strip():
            continue
        def prop(i, fn, header):
            if i is None:
                return
            old = row[i]
            new = fn(old)
            if new != old and old.strip():
                changes.append({"row": rn, "header": header, "old": old, "new": new})
        prop(ni, norm_name, H_NAME)
        prop(ei, norm_email, H_EMAIL)
        prop(li, norm_linkedin, H_LINKEDIN)
        prop(ci, norm_city, H_CITY)
        # flags (not auto-fixable mechanically)
        email = (row[ei] if ei is not None else "").strip().lower()
        name = (row[ni] if ni is not None else "").strip()
        link = (row[li] if li is not None else "").strip().lower()
        city = (row[ci] if ci is not None else "").strip()
        who = name or email or f"row {rn}"
        if not email:
            flags.append({"row": rn, "who": who, "issue": "missing email"})
        elif not EMAIL_RE.match(email):
            flags.append({"row": rn, "who": who, "issue": f"invalid email: {email}"})
        if not name:
            flags.append({"row": rn, "who": who, "issue": "missing name"})
        if link and "linkedin.com/" not in link:
            flags.append({"row": rn, "who": who, "issue": f"LinkedIn not a profile URL: {row[li].strip()}"})
        resolved = (row[ri].strip() if ri is not None and ri < len(row) else "")
        # The form has two placeholders: "Other" and "Other (PLEASE TELL US
        # WHERE IN NEXT QUESTION)" — match the prefix, not the exact string.
        if city.lower().startswith("other") and not resolved:
            # Points at the `cities` mode, not at a column to type into by hand:
            # the role tabs are array-formula views, so a literal written into one
            # #REF!s the whole tab.
            flags.append({"row": rn, "who": who,
                          "issue": "city=Other (run `clean.py cities` to derive it)"})
        if email:
            seen_email.setdefault(email, []).append(rn)
    for email, rns in seen_email.items():
        if len(rns) > 1:
            flags.append({"row": rns[0], "who": email, "issue": f"duplicate email in rows {rns}"})
    return changes, flags


def print_scan(changes, flags):
    print(f"Cleanup scan of '{SOURCE}' — {len(changes)} proposed fixes, {len(flags)} flags\n")
    if changes:
        print("PROPOSED NORMALIZATIONS (apply to clean):")
        for c in changes:
            print(f"  row {c['row']:>3}  {c['header']:<13}  {c['old']!r}  ->  {c['new']!r}")
        print()
    if flags:
        print("FLAGS (need a human / judgment call):")
        for f in flags:
            print(f"  row {f['row']:>3}  [{f['issue']}]  {f['who']}")
    if not changes and not flags:
        print("Clean — nothing to fix.")


# ---------- cities: fill the Extracted column ----------
def is_placeholder(city):
    """True when the dropdown did not answer the question. The form has shipped
    two wordings ("Other" and "Other (PLEASE TELL US WHERE...)"), so match the
    prefix, never the exact string."""
    return not city.strip() or city.strip().lower().startswith("other")


def plan_cities():
    """Propose an `Extracted City` for every row where the dropdown didn't answer.

    Migration policy — **an existing hand-filled `Resolved City` always wins.**
    123 of those were typed by a human, 23 of them for rows whose free text is
    empty (the question didn't exist yet), so deriving them fresh would blank
    real organizers. Seeding Extracted from Resolved makes the switch to a
    derived Resolved lossless by construction; extraction only fills blanks.
    """
    hdr, rows = read_tab(SOURCE)
    ci, oi, ri = idx(hdr, H_CITY), idx(hdr, H_OTHER), idx(hdr, H_RESOLVED)
    ni, ei, xi = idx(hdr, H_NAME), idx(hdr, H_EMAIL), idx(hdr, H_EXTRACTED)
    for h, i in ((H_CITY, ci), (H_OTHER, oi)):
        if i is None:
            sys.exit("ABORT: no %r column in %r. Headers: %s" % (h, SOURCE, hdr))
    known = known_cities()

    seeded, derived, unresolved, overrides = [], [], [], []
    for rn, row in enumerate(rows, start=2):
        get = lambda i: (row[i].strip() if i is not None and i < len(row) else "")
        name, email = get(ni), get(ei)
        if not (name or email):
            continue
        city, other, res, cur = get(ci), get(oi), get(ri), get(xi)
        # A real dropdown answer settles it, so Extracted is left empty on purpose
        # — a value there would never be read and would rot.
        if not is_placeholder(city):
            # Must be empty: a Resolved that contradicts a real City is exactly
            # the trap being removed, and it would change someone's chapter.
            if res and fold_city(res) != fold_city(city):
                overrides.append({"row": rn, "who": name or email,
                                  "city": city, "resolved": res})
            continue
        if res:
            if fold_city(cur) != fold_city(res):
                seeded.append({"row": rn, "who": name or email, "other": other,
                               "value": res, "why": "hand-filled Resolved City",
                               "amb": False})
            continue
        value, why, amb = extract_city(other, known)
        rec = {"row": rn, "who": name or email, "other": other,
               "value": value, "why": why, "amb": amb}
        if not value:
            unresolved.append(rec)
        elif fold_city(cur) != fold_city(value):
            derived.append(rec)
    return hdr, seeded, derived, unresolved, overrides


def print_cities(seeded, derived, unresolved, overrides):
    print("Extracted City plan — %d seeded from a human's value, %d newly derived, "
          "%d still unresolved.\n" % (len(seeded), len(derived), len(unresolved)))
    if overrides:
        print("STOP — %d row(s) have a real City AND a contradicting Resolved City.\n"
              "Making Resolved derived would change these people's chapter:" % len(overrides))
        for o in overrides:
            print("  row %-4d %-24s City=%r but Resolved=%r"
                  % (o["row"], o["who"][:24], o["city"], o["resolved"]))
        print()
    if seeded:
        print("SEEDED from the existing hand-filled Resolved City (no value changes):")
        for r in seeded:
            print("  row %-4d %-24s -> %-18r (free text: %r)"
                  % (r["row"], r["who"][:24], r["value"], r["other"][:40]))
        print()
    if derived:
        print("NEWLY DERIVED from the free text:")
        for r in derived:
            print("  row %-4d %-24s -> %-18r %s%s"
                  % (r["row"], r["who"][:24], r["value"], r["why"],
                     "  [AMBIGUOUS - check]" if r["amb"] else ""))
        print()
    if unresolved:
        print("STILL UNRESOLVED (no dropdown answer, nothing usable in the free text):")
        for r in unresolved:
            print("  row %-4d %-24s %s" % (r["row"], r["who"][:24], r["why"]))


def cities(write=False):
    hdr, seeded, derived, unresolved, overrides = plan_cities()
    print_cities(seeded, derived, unresolved, overrides)
    changes = [{"row": r["row"], "header": H_EXTRACTED, "value": r["value"]}
               for r in seeded + derived]
    if not write:
        if changes:
            print("\nRe-run with --write to apply %d value(s)." % len(changes))
        return
    if overrides:
        sys.exit("\nABORT: %d row(s) have a City/Resolved conflict (above). Resolve "
                 "those by hand first — writing now would bake in a chapter change."
                 % len(overrides))
    if not changes:
        print("\nNothing to write.")
        return
    if idx(hdr, H_EXTRACTED) is None:      # create the column at the end
        col = colletter(len(hdr) + 1)
        gws(["sheets", "spreadsheets", "values", "update", "--params",
             json.dumps({"spreadsheetId": SHEET_ID, "range": "%s!%s1" % (SOURCE, col),
                         "valueInputOption": "RAW"}),
             "--json", json.dumps({"values": [[H_EXTRACTED]]}), "--format", "json"])
        print("\nCreated the %r column at %s." % (H_EXTRACTED, col))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(changes, fh)
        path = fh.name
    # Reuse apply(): row bounds, RAW writes and the Autofixes provenance note are
    # all already correct there, and a second implementation would drift.
    apply(path)
    os.unlink(path)


# ---------- apply ----------
def apply(path):
    with open(path) as fh:
        wanted = json.load(fh)
    hdr, rows = read_tab(SOURCE)
    ai = idx(hdr, AUTOFIX_COL)
    if ai is None:  # create the Autofixes column at the end of the source headers
        ai = len(hdr)
        gws(["sheets", "spreadsheets", "values", "update", "--params",
             json.dumps({"spreadsheetId": SHEET_ID,
                         "range": f"{SOURCE}!{colletter(ai + 1)}1", "valueInputOption": "RAW"}),
             "--json", json.dumps({"values": [[AUTOFIX_COL]]}), "--format", "json"])
    data, notes, dropped = [], {}, 0
    for ch in wanted:
        rn, header, new = ch["row"], ch["header"], ch["value"]
        # Row 1 is the header. Without a LOWER bound, rn=1 gives rows[-1] below —
        # seeding the note from the last data row — and writes over the header
        # cell that this module's entire header-driven design is anchored to.
        if not isinstance(rn, int) or not 2 <= rn <= len(rows) + 1:
            sys.exit(f"ABORT: row {rn!r} is outside the data rows (2..{len(rows) + 1}). "
                     f"Row 1 is the header; nothing written.")
        if header in DERIVED_COLUMNS:
            sys.exit("ABORT: %r is derived by an ARRAYFORMULA — writing a literal "
                     "into it collapses the whole column to #REF!. Nothing written.\n"
                     "To change someone's city, set %r (the dropdown answer) or "
                     "%r instead." % (header, H_CITY, H_EXTRACTED))
        ci = idx(hdr, header)
        if ci is None:
            print(f"  skip: no column named {header!r}", file=sys.stderr)
            dropped += 1
            continue
        data.append({"range": f"{SOURCE}!{colletter(ci + 1)}{rn}", "values": [[new]]})
        # Carry the new value in the phrase. The phrase alone is per-HEADER, so
        # a second genuine edit to the same field ("Zurich" -> "Zürich") would
        # dedupe against the first and silently record nothing, even though the
        # value write above already happened.
        notes.setdefault(rn, []).append(
            f"{AUTOFIX_PHRASE.get(header, f'{header} updated')} -> {new}")
    if not data:
        # "already clean" and "every header in the change list was renamed away"
        # are different facts; the second is a failure and must not exit 0.
        if dropped:
            sys.exit(f"ABORT: none of the {dropped} requested change(s) matched a column "
                     f"in {SOURCE!r} — the change list is stale. Nothing written.")
        print("No changes to apply.")
        return
    gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": SHEET_ID}), "--json",
         # RAW, never USER_ENTERED: these values come from a PUBLIC form, and the
         # normalizers happily re-case "=IMPORTXML(...)" into a still-valid
         # formula, so a submission can look like a routine capitalisation fix in
         # scan and become a live formula here — able to exfiltrate the row. RAW
         # keeps it inert text. Leading "=", "+", "-" and "@" are all covered.
         json.dumps({"valueInputOption": "RAW", "data": data}), "--format", "json"])
    # one entry per touched row, or None when the cell already says it all
    merged = {rn: autofix_note(
                  rows[rn - 2][ai] if ai < len(rows[rn - 2]) else "", phrases)
              for rn, phrases in notes.items()}
    fix = [{"range": f"{SOURCE}!{colletter(ai + 1)}{rn}", "values": [[c]]}
           for rn, c in merged.items() if c]
    # `fix` can legitimately be empty (every phrase already recorded), and an
    # empty `data` list is exactly the shape gws drops — guard it rather than
    # firing a write whose no-op is indistinguishable from a success.
    if fix:
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--json",
             json.dumps({"valueInputOption": "RAW", "data": fix}), "--format", "json"])
    skipped = len(notes) - len(fix)
    print(f"Applied {len(data)} change(s); annotated 'Autofixes' on {len(fix)} row(s)"
          + (f" ({skipped} row(s) already noted)." if skipped else "."))


# ---------- install live Issues flag + bright-red rule ----------
def install_flags():
    for tab, sid in ROLE_TABS.items():
        hdr, _ = read_tab(tab)
        def L(name):
            return colletter(hdr.index(name) + 1) if name in hdr else None
        ts = L("Timestamp")
        email = L("Email")
        link = L("LinkedIn")
        if "Issues" in hdr:
            icol = hdr.index("Issues") + 1
        else:
            icol = len(hdr) + 1
        ilet = colletter(icol)
        # ARRAYFORMULA building a "; "-joined list of *errors*, blank when clean.
        # NOTE: City="Other" is a normalization opportunity, NOT an error -> not here
        # (it must not turn the row bright red). It's surfaced by `scan` instead.
        parts = []
        if email:
            parts.append(f'IF(REGEXMATCH(${email}2:${email},"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"),"","missing/bad email; ")')
        if link:
            parts.append(f'IF(REGEXMATCH(LOWER(${link}2:${link}),"linkedin\\.com/"),"","bad LinkedIn; ")')
        concat = "&".join(parts) if parts else '""'
        formula = (f'=ARRAYFORMULA(IF(${ts}2:${ts}="","",'
                   f'REGEXREPLACE({concat},"; $","")))')
        # write header + formula
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--json",
             json.dumps({"valueInputOption": "USER_ENTERED", "data": [
                 {"range": f"{tab}!{ilet}1", "values": [["Issues"]]},
                 {"range": f"{tab}!{ilet}2", "values": [[formula]]}]}), "--format", "json"])
        # endRowIndex omitted: a fixed bound stops highlighting on the newest
        # rows once the tab grows past it (see _color_rule).
        rng = {"sheetId": sid, "startRowIndex": 1,
               "startColumnIndex": 0, "endColumnIndex": icol}
        if "Issues" in hdr:
            # The rule already exists, so ADDING one would duplicate it — but
            # leaving it alone was how the endRowIndex fix failed to reach the
            # live sheet: every role tab's red rule was installed with the old
            # hardcoded 1000 and nothing ever rewrote it, so the one rule that
            # flags broken emails stayed bounded while the provenance colors
            # became unbounded. Re-point its RANGE in place, preserving whatever
            # color the rule currently has (the red has been re-picked in the UI
            # and that choice is the operator's, not ours).
            existing = _all_conditional_formats().get(sid, [])
            err = f'=${ilet}2<>""'
            at = next((i for i, cf in enumerate(existing) if formula_of(cf) == err), None)
            if at is None:
                print(f"  {tab}: no rule references {err}; leaving conditional "
                      f"formats alone.", file=sys.stderr)
                req = None
            else:
                rule = dict(existing[at])
                rule["ranges"] = [rng]
                req = {"updateConditionalFormatRule": {"sheetId": sid, "index": at,
                                                       "rule": rule}}
        else:
            req = {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [rng], "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue": f'=${ilet}2<>""'}]},
                "format": {"backgroundColor": BRIGHT_RED,
                           "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}}}}}}
        reqs = [req] if req else []
        if "Issues" not in hdr:
            # bold header for the new column too
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                        "startColumnIndex": icol - 1, "endColumnIndex": icol},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                 "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85}}},
                        "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}})
        if reqs:
            gws(["sheets", "spreadsheets", "batchUpdate", "--params",
                 json.dumps({"spreadsheetId": SHEET_ID}), "--json",
                 json.dumps({"requests": reqs}), "--format", "json"])
        if "Issues" in hdr:
            action = "range re-pointed" if req else "left alone (not found)"
        else:
            action = "added"
        print(f"{tab}: Issues column at {ilet}, bright-red rule {action}.")
    install_colors()


# ---------- install City (Existing)/City (New) labels + provenance colors ----------
def _all_conditional_formats():
    """One spreadsheet fetch -> {sheetId: conditionalFormats}. Fetched once and
    indexed, rather than re-fetching the whole spreadsheet per role tab."""
    d = gws(["sheets", "spreadsheets", "get", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--format", "json"])
    return {sh["properties"]["sheetId"]: sh.get("conditionalFormats", [])
            for sh in d.get("sheets", [])}


def _color_rule(sid, index, c0, c1, formula, bg, white=False):
    """One addConditionalFormatRule request over 0-based half-open columns.

    endRowIndex is deliberately OMITTED (= to the end of the sheet). It used to
    be a hardcoded 1000, which is the same anti-pattern read_tab just lost: past
    that row the coloring silently stops, and the rows it stops on are the newest
    submissions. The role tabs are already ~998 rows."""
    fmt = {"backgroundColor": bg}
    if white:
        fmt["textFormat"] = {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
    return {"addConditionalFormatRule": {"index": index, "rule": {
        "ranges": [{"sheetId": sid, "startRowIndex": 1,
                    "startColumnIndex": c0, "endColumnIndex": c1}],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}]}, "format": fmt}}}}


# (builder, color) travel together. A loose `bg` argument was silently
# unconstrained: installing a rule in a color is_ours does not know makes it
# invisible to every later run, so each run stacks a fresh duplicate — the exact
# bug this module exists to prevent. Pass one of these, never a bare color.
AMBER_RULE = (amber_formula, AMBER)
GREEN_RULE = (green_formula, GREEN)


def _city_rule(sid, index, col, rule):
    """A one-column provenance rule. The painted range and the tested formula
    both derive from `col`, so a rule can never paint one column while testing
    another — which Sheets accepts silently and `is_ours` still recognises."""
    formula_fn, bg = rule
    return _color_rule(sid, index, col - 1, col, formula_fn(col), bg)


def install_colors():
    """Label the two city columns and (re)install violet/amber/green rules.

    Idempotent: deletes the rules it owns (matched by formula shape + color, via
    `color_rule_plan`) and re-adds them just below the bright-red error rule so
    the error keeps top priority. The violet whole-row rule sits above amber/
    green, so on an "Existing (from MLOps)" row the whole row (city cells
    included) reads violet — the Status color wins over the city colors where
    they overlap.
    """
    all_cfs = _all_conditional_formats()
    # Pre-flight: validate EVERY tab before writing to any of them. The loop
    # below mutates tab-by-tab, so a find_city_cols abort on the second tab used
    # to leave the first relabeled and re-ruled, the third untouched, and the
    # error message describing only the tab that failed.
    plans = {}
    for tab, sid in ROLE_TABS.items():
        hdr, _ = read_tab(tab)
        if all_cfs.get(sid) is None:
            sys.exit(f"ABORT: sheetId {sid} ({tab}) not found in the spreadsheet. "
                     f"No tab modified.")
        # sid lives in the plan so the write loop iterates ONE source, not two
        # that must stay in lockstep.
        plans[tab] = (sid, hdr, find_status_col(hdr, tab), find_city_cols(hdr, tab))

    for tab, (sid, hdr, status_col, (city_existing_col, city_new_col)) in plans.items():
        lastcol = len(hdr)
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--json",
             json.dumps({"valueInputOption": "USER_ENTERED", "data": [
                 {"range": f"{tab}!{colletter(city_existing_col)}1",
                  "values": [[CITY_LABELS[0]]]},
                 {"range": f"{tab}!{colletter(city_new_col)}1",
                  "values": [[CITY_LABELS[1]]]}]}), "--format", "json"])
        stale, base = color_rule_plan(all_cfs[sid], error_rule_formula(hdr))
        dels = [{"deleteConditionalFormatRule": {"sheetId": sid, "index": i}}
                for i in stale]
        adds = [
            _color_rule(sid, base + 0, 0, lastcol,
                        violet_formula(status_col), VIOLET, white=True),
            _city_rule(sid, base + 1, city_new_col, AMBER_RULE),
            _city_rule(sid, base + 2, city_existing_col, GREEN_RULE),
        ]
        gws(["sheets", "spreadsheets", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--json",
             json.dumps({"requests": dels + adds}), "--format", "json"])
        print(f"{tab}: labeled City (Existing)/City (New); "
              f"{len(stale)} old rule(s) refreshed, 3 installed.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("scan"); sp.add_argument("--json", action="store_true")
    ap_apply = sub.add_parser("apply"); ap_apply.add_argument("file")
    ap_cities = sub.add_parser("cities")
    ap_cities.add_argument("--write", action="store_true")
    sub.add_parser("install-flags")
    sub.add_parser("install-colors")
    a = ap.parse_args()
    if a.cmd == "scan":
        changes, flags = scan()
        if a.json:
            print(json.dumps({"changes": changes, "flags": flags}, indent=1))
        else:
            print_scan(changes, flags)
    elif a.cmd == "apply":
        apply(a.file)
    elif a.cmd == "cities":
        cities(a.write)
    elif a.cmd == "install-flags":
        install_flags()
    elif a.cmd == "install-colors":
        install_colors()


if __name__ == "__main__":
    main()
