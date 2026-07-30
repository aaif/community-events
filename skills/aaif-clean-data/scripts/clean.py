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
import argparse, json, re, subprocess, sys

SHEET_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
SOURCE = "Form Responses"
ROLE_TABS = {"Organizers": 537599805, "Hosts": 1923799643, "Speakers": 1491913647}
BRIGHT_RED = {"red": 0.91, "green": 0.26, "blue": 0.21}

# Common person fields live on these source headers.
H_NAME, H_EMAIL, H_LINKEDIN, H_CITY = "Full name", "Email", "LinkedIn URL", "City"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Per-row provenance for applied edits is recorded in this Form Responses column
# (not a separate log tab). Phrases keep cells short; falls back to "<field> updated".
AUTOFIX_COL = "Autofixes"
AUTOFIX_PHRASE = {"LinkedIn URL": "LinkedIn normalized", "Email": "email normalized",
                  "Full name": "name normalized", "Resolved City": "city resolved"}


# ---------- gws helpers ----------
def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gws error: {' '.join(args[:4])}...\n{out.stderr.strip()}")
    txt = out.stdout
    i = min((txt.index(c) for c in "{[" if c in txt), default=-1)
    return json.loads(txt[i:]) if i >= 0 else {}


def read_tab(tab, rng=None):
    """Read a whole tab. The range is the bare tab name by default so the read
    window can never fall behind the sheet: a hardcoded bound (this used to be
    "A1:CJ") silently truncates the moment a column is appended, and the column
    it drops first is the newest one — which is how `Autofixes` (at CK) fell
    outside the window and made apply() overwrite prior notes instead of
    appending them. Sheets trims trailing empty rows/columns for us."""
    rangespec = f"{tab}!{rng}" if rng else tab
    d = gws(["sheets", "spreadsheets", "values", "get", "--params",
             json.dumps({"spreadsheetId": SHEET_ID, "range": rangespec,
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
# started at G/H and are at H/I today. So they are located by header name at run
# time (find_city_cols) and never hardcoded; a fixed letter here is exactly the
# bug that made install_colors abort after the pair shifted by one.
VIOLET = {"red": 0.60, "green": 0.20, "blue": 0.90}   # Status = Existing (from MLOps)
AMBER  = {"red": 0.99, "green": 0.76, "blue": 0.30}   # net-new resolved city
GREEN  = {"red": 0.72, "green": 0.88, "blue": 0.70}   # existing form city
# The two source-column headers marking the pair, before (or after) labeling.
CITY_SRC_HEADERS = ({"City", "City (Existing)"}, {"Resolved City", "City (New)"})

VIOLET_FORMULA = '=$A2="Existing (from MLOps)"'      # column-independent


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

    Returns the first ADJACENT pair, which is what the role-tab array formula
    emits. Aborts loudly rather than guessing: recoloring the wrong columns
    would paint provenance onto unrelated data."""
    for i in range(len(hdr) - 1):
        if hdr[i] in CITY_SRC_HEADERS[0] and hdr[i + 1] in CITY_SRC_HEADERS[1]:
            return i + 1, i + 2
    sys.exit(f"ABORT: {tab} has no adjacent City / Resolved City pair in its "
             f"header row — columns moved or renamed? Headers: {hdr}")


# Our own rules are recognised by SHAPE (any column) + one of our three colors,
# not by exact formula text. Text-matching pinned to a column letter stops
# recognising rules the moment the pair moves, so a refresh stacks a duplicate
# next to the old rule instead of replacing it. The color test is what keeps the
# broad amber pattern from also matching the bright-red Issues rule (=$W2<>"").
STALE_PATTERNS = (
    re.compile(r'^=\$A2="Existing \(from MLOps\)"$'),                     # violet
    re.compile(r'^=\$[A-Z]+2<>""$'),                                      # amber
    re.compile(r'^=AND\(\$[A-Z]+2<>"",LEFT\(\$[A-Z]+2,5\)<>"Other"\)$'),  # green
    re.compile(r'^=AND\(\$[A-Z]+2<>"",\$[A-Z]+2<>"Other"\)$'),            # legacy green
)


def _rgb8(c):
    """Quantize a color dict to 8-bit ints — Sheets round-trips colors at 8-bit,
    so compare with this, never with exact float equality."""
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


def formula_of(cf):
    """The CUSTOM_FORMULA text of a conditional-format rule, or None."""
    c = cf.get("booleanRule", {}).get("condition", {})
    vals = c.get("values", [])
    return vals[0].get("userEnteredValue") if c.get("type") == "CUSTOM_FORMULA" and vals else None


def _is_red(cf):
    """True if this rule is the bright-red error rule (matched by 8-bit color)."""
    bg = cf.get("booleanRule", {}).get("format", {}).get("backgroundColor")
    return bool(bg) and _rgb8(bg) == _rgb8(BRIGHT_RED)


def is_ours(cf, err_formula=None):
    """True if this rule is one of the three provenance rules we install, at ANY
    column position. Requires BOTH a matching formula shape and one of our three
    background colors — the amber shape (=$X2<>"") alone also matches the
    bright-red Issues rule, and deleting that would drop error highlighting.
    `err_formula` names that rule explicitly so it is never claimed even if its
    color has drifted to something near ours."""
    f = formula_of(cf)
    if not f or (err_formula and f == err_formula):
        return False
    if not any(p.match(f) for p in STALE_PATTERNS):
        return False
    bg = cf.get("booleanRule", {}).get("format", {}).get("backgroundColor")
    return bool(bg) and any(_color_eq(bg, c) for c in (VIOLET, AMBER, GREEN))


def color_rule_plan(cfs, err_formula=None):
    """Pure planner for install_colors: given a tab's conditionalFormats, return
    (stale_indices_desc, base_index). stale = our own color rules to delete
    (matched by `is_ours`: formula SHAPE plus one of our colors, so they are
    still recognised after the city pair moves); base = insert index just BELOW
    the bright-red error rule so it keeps top priority — identified by
    `err_formula` when given, else by color — computed from its ACTUAL position
    (not assumed to be index 0), adjusted for the stale rules deleted above it,
    since deletes and adds run in one batch. Testable without touching Sheets."""
    stale = [i for i, cf in enumerate(cfs) if is_ours(cf, err_formula)]
    # Prefer identifying the error rule by formula; fall back to its color for
    # sheets where install_flags has not run and no Issues column exists yet.
    red = next((i for i, cf in enumerate(cfs)
                if err_formula and formula_of(cf) == err_formula), None)
    if red is None:
        red = next((i for i, cf in enumerate(cfs) if _is_red(cf)), None)
    if red is None:
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
            flags.append({"row": rn, "who": who, "issue": "city=Other (resolve into 'City (New)' from their text)"})
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
    data, notes = [], {}
    for ch in wanted:
        rn, header, new = ch["row"], ch["header"], ch["value"]
        ci = idx(hdr, header)
        if ci is None:
            print(f"  skip: no column named {header!r}", file=sys.stderr)
            continue
        data.append({"range": f"{SOURCE}!{colletter(ci + 1)}{rn}", "values": [[new]]})
        notes.setdefault(rn, []).append(AUTOFIX_PHRASE.get(header, f"{header} updated"))
    if not data:
        print("No changes to apply.")
        return
    gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": SHEET_ID}), "--json",
         json.dumps({"valueInputOption": "USER_ENTERED", "data": data}), "--format", "json"])
    # annotate each touched row's Autofixes cell (append, preserving prior notes)
    fix = []
    for rn, phrases in notes.items():
        prior = rows[rn - 2][ai] if (rn - 2 < len(rows) and ai < len(rows[rn - 2])) else ""
        # Dedupe against what the cell ALREADY says, not just within this run —
        # re-resolving a row that was resolved before would otherwise accumulate
        # "city resolved | city resolved | ...". Split on both separators this
        # column has ever used.
        seen = {p.strip() for p in re.split(r"[|;]", prior) if p.strip()}
        uniq = []
        for p in phrases:
            if p not in uniq and p not in seen:
                uniq.append(p)
        if not uniq:
            continue                      # nothing new to say; leave the cell alone
        note = "; ".join(uniq)
        combined = f"{prior} | {note}" if prior.strip() else note
        fix.append({"range": f"{SOURCE}!{colletter(ai + 1)}{rn}", "values": [[combined]]})
    gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
         json.dumps({"spreadsheetId": SHEET_ID}), "--json",
         json.dumps({"valueInputOption": "RAW", "data": fix}), "--format", "json"])
    print(f"Applied {len(data)} change(s); annotated 'Autofixes' on {len(notes)} row(s).")


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
        # add the bright-red rule only if not already installed (Issues was absent)
        if "Issues" not in hdr:
            rng = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 1000,
                   "startColumnIndex": 0, "endColumnIndex": icol}
            req = {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [rng], "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue": f'=${ilet}2<>""'}]},
                "format": {"backgroundColor": BRIGHT_RED,
                           "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}}}}}}
            # bold header for the new column too
            hdrfmt = {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": icol - 1, "endColumnIndex": icol},
                      "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                               "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85}}},
                      "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}}
            gws(["sheets", "spreadsheets", "batchUpdate", "--params",
                 json.dumps({"spreadsheetId": SHEET_ID}), "--json",
                 json.dumps({"requests": [req, hdrfmt]}), "--format", "json"])
        print(f"{tab}: Issues column at {ilet}, bright-red rule "
              f"{'kept' if 'Issues' in hdr else 'added'}.")
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
    """One addConditionalFormatRule request over 0-based half-open columns."""
    fmt = {"backgroundColor": bg}
    if white:
        fmt["textFormat"] = {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
    return {"addConditionalFormatRule": {"index": index, "rule": {
        "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": 1000,
                    "startColumnIndex": c0, "endColumnIndex": c1}],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}]}, "format": fmt}}}}


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
    for tab, sid in ROLE_TABS.items():
        hdr, _ = read_tab(tab)
        lastcol = len(hdr)
        # Locate the pair by header name rather than assuming a letter, so a
        # column inserted upstream shifts the rules instead of aborting the run.
        city_existing_col, city_new_col = find_city_cols(hdr, tab)
        cfs = all_cfs.get(sid)
        if cfs is None:
            sys.exit(f"ABORT: sheetId {sid} ({tab}) not found in the spreadsheet.")
        gws(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
             json.dumps({"spreadsheetId": SHEET_ID}), "--json",
             json.dumps({"valueInputOption": "USER_ENTERED", "data": [
                 {"range": f"{tab}!{colletter(city_existing_col)}1",
                  "values": [["City (Existing)"]]},
                 {"range": f"{tab}!{colletter(city_new_col)}1",
                  "values": [["City (New)"]]}]}), "--format", "json"])
        stale, base = color_rule_plan(cfs, error_rule_formula(hdr))
        dels = [{"deleteConditionalFormatRule": {"sheetId": sid, "index": i}}
                for i in stale]
        adds = [
            _color_rule(sid, base + 0, 0, lastcol, VIOLET_FORMULA, VIOLET, white=True),
            _color_rule(sid, base + 1, city_new_col - 1, city_new_col,
                        amber_formula(city_new_col), AMBER),
            _color_rule(sid, base + 2, city_existing_col - 1, city_existing_col,
                        green_formula(city_existing_col), GREEN),
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
    elif a.cmd == "install-flags":
        install_flags()
    elif a.cmd == "install-colors":
        install_colors()


if __name__ == "__main__":
    main()
