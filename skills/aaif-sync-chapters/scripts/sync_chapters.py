#!/usr/bin/env python3
"""Sync organizer decisions from the AAIF Community Intake Ops sheet into the
AAIF Community Chapters List.

Every intake organizer whose Status is "Accepted" or "Existing (from MLOps)"
must appear in the Organizers column of their city's row on the chapters list;
cities with no row yet get one appended. The intake sheet is only ever READ.

Usage:
  python3 sync_chapters.py            # report + proposed changes, writes nothing
  python3 sync_chapters.py --write    # apply the proposal via one batchUpdate

The report shows: per-city name adds (existing rows), new rows with their
appended row numbers and Luma slugs, unresolved-city rows needing a human,
near-miss city names (never auto-matched), and a "no changes" line when the
sheets are already in sync. --write recomputes the proposal from a fresh read,
applies it atomically, then re-reads and verifies the diff is empty.
"""
import argparse, json, re, subprocess, sys, time, unicodedata, urllib.error, urllib.request
from collections import namedtuple

INTAKE_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
INTAKE_TAB = "Organizers"
CHAPTERS_ID = "18_7aHD45-5NhlN6IZKW2QzswZlDHVb8nBSP7rl5-yWg"
CHAPTERS_TAB = "Chapters & Teams"

# Exact dropdown strings — "Existing" alone would miss every MLOps row.
SYNC_STATUSES = ("Accepted", "Existing (from MLOps)")

# Folded city -> Luma slug, for cities whose page doesn't follow the default
# slug rule (same exceptions as aaif-create-chapter).
SLUG_OVERRIDES = {"denver": "colorado"}

# Blank-on-new-row columns a human must fill before the row goes live on the site.
EDITORIAL_COLUMNS = ("Country", "Generated Geolocation", "Summary", "Image")

# Also blank on new rows, but read-only legacy history — never backfilled, so the
# report must not tell an operator to fill it.
NEVER_FILLED = ("MLOps Community Organizers",)

# Every column a new row writes. All must resolve, or we refuse to write at all:
# these are the columns the old hardcoded A:D write got wrong, and a silently
# skipped one publishes a chapter row with no title or no CTA link.
DERIVED_COLUMNS = ("Title", "City", "Organizers", "CTA", "URL for CTA", "Chapter Luma Link")

# Tokens too generic to imply the same chapter. A near-miss is never written and
# has no override, so a false positive blocks the city permanently — without this
# stoplist "San Diego" is reported against "San Francisco" and can never be added.
GENERIC_CITY_TOKENS = frozenset(
    "new san santa saint st city los las el la de del di da du "
    "north south east west upper lower grand port cape lake fort mount".split())

# Free text from the PUBLIC intake form is republished to the website feed, so it
# is length- and charset-checked before it can reach a cell.
MAX_PUBLIC_TEXT = 120
_UNSAFE_PUBLIC_TEXT = re.compile(r"[\x00-\x1f\x7f<>]")

# ----------------------------------------------------------------------------
# gws helpers (same retry/JSON pattern as aaif-create-chapter)
# ----------------------------------------------------------------------------
_TRANSIENT = ("timed out", "internalError", "HTTP request failed",
              "Connection reset", "Connection refused", "Connection aborted",
              "temporarily", "rateLimit", "userRateLimit", "backendError")
# Bare "500"/"502"/"503" as substrings match any range or quota id that happens to
# contain those digits ("A500:K500 exceeds grid limits"), so a permanent error
# would burn the full backoff. Match them only as standalone HTTP statuses.
_TRANSIENT_STATUS = re.compile(r"(?<![0-9])(?:429|500|502|503|504)(?![0-9])")

def _transient(msg):
    return any(k in msg for k in _TRANSIENT) or bool(_TRANSIENT_STATUS.search(msg))

def _gws(cmd, retries=5, cwd=None):
    # cwd: gws rejects --output/--upload paths outside its working directory, so
    # the Drive callers in sync_crm.py run it from the file's own directory.
    for i in range(max(1, retries)):   # retries<=0 must raise below, not return None
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < max(1, retries) - 1 and _transient(msg):
            # Announce it: a silent 30s backoff looks like a hang, and a run that
            # succeeds on attempt 4 should still leave a trace that the API was sick.
            print("  gws call failed (attempt %d/%d), retrying in %ds: %s"
                  % (i + 1, max(1, retries), 2 * (i + 1), msg.strip()[:120]), file=sys.stderr)
            time.sleep(2 * (i + 1))
            continue
        raise RuntimeError("gws failed (%s): %s" % (r.returncode, msg.strip()[:400]))

def gws_json(*args, params=None, body=None):
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = _gws(cmd)
    # Split on "\n" only — NOT splitlines(), which also splits on U+2028 and
    # friends INSIDE cell values, corrupting the JSON when rejoined.
    s = "\n".join(l for l in out.split("\n") if "keyring backend" not in l).strip()
    if not s:
        raise RuntimeError("gws produced no JSON output for: %s" % " ".join(args))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise RuntimeError("gws returned non-JSON output for %s: %s" % (" ".join(args), s[:200]))

def get_values(sheet_id, rng):
    res = gws_json("sheets", "spreadsheets", "values", "batchGet",
                   params={"spreadsheetId": sheet_id, "ranges": [rng]})
    return res["valueRanges"][0].get("values", [])

# ----------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------
def fold(s):
    """Comparison key: accent-folded, casefolded, whitespace-collapsed.
    Only ever used to COMPARE — written values keep their original UTF-8."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()

def fold_city(s):
    """City comparison key: fold(), with punctuation flattened to spaces.

    'Washington, DC' and 'Washington DC' are one city; without this they compare
    unequal and get reported as a near-miss every run instead of merging cleanly.

    `\\W` is Unicode-aware, and that is load-bearing: an ASCII allowlist
    (`[^a-z0-9]`) folds every non-Latin city — 'Москва', '東京' — to "", which
    collides them all onto one key and merges organizers into the WRONG row.
    The `or fold(s)` fallback covers a name that is punctuation all the way down.
    """
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", fold(s))).strip() or fold(s)

def city_tokens(s):
    """Discriminating tokens only — see GENERIC_CITY_TOKENS."""
    return set(fold_city(s).split()) - GENERIC_CITY_TOKENS

def check_public_text(kind, s, row):
    """Reject intake free-text that must not reach the public website feed.

    The intake sheet is fed by a public Google Form, and this script is the last
    controlled point before a name or city is republished. RAW input mode already
    stops formula injection; this stops markup and control characters.
    """
    if _UNSAFE_PUBLIC_TEXT.search(s):
        sys.exit("ABORT: intake row %d %s %r contains control characters or angle "
                 "brackets, which must never reach the public feed." % (row, kind, s))
    if len(s) > MAX_PUBLIC_TEXT:
        sys.exit("ABORT: intake row %d %s is %d characters (max %d)."
                 % (row, kind, len(s), MAX_PUBLIC_TEXT))

def slugify(city):
    s = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()
    return SLUG_OVERRIDES.get(fold(city), re.sub(r"[^a-z0-9]", "", s.lower()))

def cell(row, i):
    return row[i].strip() if i < len(row) and isinstance(row[i], str) else ""

def header_index(headers, sheet, *names):
    idx = []
    for n in names:
        if n not in headers:
            sys.exit("ABORT: column %r not found on %s — sheet layout changed?" % (n, sheet))
        idx.append(headers.index(n))
    return idx

def col_letter(i):
    """0-based column index -> A1 letter. Every write target is derived from the
    header row through this, so a column reorder moves the writes with it."""
    if i < 0:
        raise ValueError("col_letter: negative column index %d" % i)
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s

def luma_status(slug):
    """'live' (200) / 'absent' (404) / 'unknown' (couldn't verify)."""
    req = urllib.request.Request("https://luma.com/aaif-" + slug, method="GET",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return "live" if r.status == 200 else "unknown"
    except urllib.error.HTTPError as e:
        return "absent" if e.code == 404 else "unknown"
    except (urllib.error.URLError, TimeoutError):
        return "unknown"

# ----------------------------------------------------------------------------
# Read the two sheets
# ----------------------------------------------------------------------------
def read_intake():
    """Return (entries, unresolved, status_counts, dupes).

    entries    = [{row, name, city, status}]                        city resolved, deduped
    unresolved = [{row, name, status, g, h, events, why,
                   placed, inferred}]                               needs a human
    """
    rows = get_values(INTAKE_ID, "%s!A:U" % INTAKE_TAB)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    i_status, i_name, i_g, i_h, i_events, i_why = header_index(
        rows[0], INTAKE_TAB, "Status", "Full name", "City (Existing)", "City (New)",
        "Run events before?", "Why organize / ties")

    entries, unresolved, dupes = [], [], []
    counts = {s: 0 for s in SYNC_STATUSES}
    seen = set()
    for rownum, row in enumerate(rows[1:], start=2):
        status = cell(row, i_status)
        if status not in SYNC_STATUSES:
            continue
        counts[status] += 1
        name = cell(row, i_name)
        g, h = cell(row, i_g), cell(row, i_h)
        # City resolution: City (New) wins; else City (Existing) unless it's an
        # "Other..." placeholder; else the row needs a human.
        city = h or (g if g and not fold(g).startswith("other") else "")
        if not name or not city:
            # placed/inferred are filled by annotate_unresolved but initialised
            # here, so the record is never half-built and print_report is safe
            # regardless of call order.
            unresolved.append({"row": rownum, "name": name, "status": status,
                               "g": g, "h": h,
                               "events": cell(row, i_events), "why": cell(row, i_why),
                               "placed": [], "inferred": []})
            continue
        check_public_text("name", name, rownum)
        check_public_text("city", city, rownum)
        key = (fold(name), fold(city))
        if key in seen:
            dupes.append({"row": rownum, "name": name, "city": city})
            continue
        seen.add(key)
        entries.append({"row": rownum, "name": name, "city": city, "status": status})
    return entries, unresolved, counts, dupes

def read_chapters():
    """Return (chapters, last_row, layout). chapters = [{row, city, organizers_raw}].

    layout = {headers, index: {name -> 0-based col}} — the tab is a website feed
    whose columns have moved before, so read well past the current width and
    resolve every column by header name.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]

    # A duplicated header would resolve differently for reads and writes:
    # header_index() takes the FIRST match, a dict comprehension keeps the LAST.
    # The script would then read organizers from one column and write the merged
    # value over another, clobbering it. Refuse rather than pick a winner.
    dups = sorted({h for h in headers if h and headers.count(h) > 1})
    if dups:
        sys.exit("ABORT: duplicate column header(s) %s on %s — reads and writes "
                 "would resolve to different columns."
                 % (", ".join(map(repr, dups)), CHAPTERS_TAB))

    # Every column we write must exist, not just the two we read. Resolving them
    # all here is what lets new_row_values index directly instead of silently
    # skipping a renamed column and publishing a row with no title or CTA link.
    header_index(headers, CHAPTERS_TAB, *DERIVED_COLUMNS)
    layout = {"headers": headers, "index": {h: i for i, h in enumerate(headers) if h}}
    i_city, i_org = layout["index"]["City"], layout["index"]["Organizers"]

    chapters, last_row = [], 1
    for rownum, row in enumerate(rows[1:], start=2):
        city = cell(row, i_city)
        if not city:
            continue   # never append into a gap; find the true last City row
        chapters.append({"row": rownum, "city": city, "organizers_raw": cell(row, i_org)})
        last_row = rownum

    # New rows are appended at last_row+1 and written FULL WIDTH, which clears
    # every column we don't derive. A half-drafted row below the table (Summary
    # written, City not filled in yet) is invisible to the loop above and would
    # be silently wiped, so refuse to run while one exists.
    for rownum, row in enumerate(rows[1:], start=2):
        if rownum > last_row and any(str(v).strip() for v in row):
            occupied = [headers[i] for i, v in enumerate(row)
                        if i < len(headers) and headers[i] and str(v).strip()]
            sys.exit("ABORT: row %d sits below the last City row (%d) but is not empty "
                     "(%s).\nNew chapters are appended there and would overwrite it. "
                     "Give it a City or clear it." % (rownum, last_row, ", ".join(occupied)))
    return chapters, last_row, layout

# ----------------------------------------------------------------------------
# Diff
# ----------------------------------------------------------------------------
def parse_organizers(raw):
    return [p.strip() for p in raw.split(";") if p.strip()]

def build_proposal(entries, chapters, last_row):
    """Return (adds, new_rows, near_misses).

    adds       = [{row, city, names, new_value}]   merge into an Organizers cell
    new_rows   = [{row, city, names, slug}]        append after last_row
    near_misses= [{city, names, candidates}]       no exact row; never written
    """
    by_city = {}          # folded intake city -> {city, names[]}   (intake order)
    for e in entries:
        by_city.setdefault(fold_city(e["city"]), {"city": e["city"], "names": []})["names"].append(e["name"])

    # Fold each chapter city once: the near-miss scan below is O(intake x chapters)
    # and fold_city() is regex + unicode normalization.
    folded = [(c, fold_city(c["city"])) for c in chapters]
    chap_by_fold = {f: c for c, f in folded}
    adds, new_rows, near_misses = [], [], []
    next_row = last_row + 1
    for fc, grp in by_city.items():
        chap = chap_by_fold.get(fc)
        if chap:
            existing = parse_organizers(chap["organizers_raw"])
            present = {fold(n) for n in existing}
            # Merge, don't overwrite: keep every name already in Organizers
            # (manual entries included), append only the intake names missing.
            missing = [n for n in grp["names"] if fold(n) not in present]
            if missing:
                adds.append({"row": chap["row"], "city": chap["city"], "names": missing,
                             "new_value": "; ".join(existing + missing)})
            continue
        # Near-miss on substring OR a shared DISCRIMINATING token: 'New Delhi' vs
        # 'Delhi NCR' overlaps on neither substring test but is the same chapter.
        # Generic tokens are excluded (see GENERIC_CITY_TOKENS) because a
        # near-miss is never written and has no override — a false positive
        # doesn't cost a confirmation, it blocks the city permanently.
        toks = city_tokens(grp["city"])
        cands = [c for c, cf in folded
                 if (fc and cf and (fc in cf or cf in fc)) or (toks & set(cf.split()))]
        if cands:
            near_misses.append({"city": grp["city"], "names": grp["names"],
                                "candidates": [(c["city"], c["row"]) for c in cands]})
            continue
        slug = slugify(grp["city"])
        if not slug:
            sys.exit("ABORT: city %r has no ASCII letters or digits, so its Luma slug "
                     "would be empty and the row would publish a link to "
                     "https://luma.com/aaif-.\nAdd a SLUG_OVERRIDES entry for it."
                     % grp["city"])
        new_rows.append({"row": next_row, "city": grp["city"], "names": grp["names"],
                         "slug": slug})
        next_row += 1
    return adds, new_rows, near_misses

def annotate_unresolved(unresolved, chapters):
    """Mark unresolved rows already hand-placed on the chapters list, and infer
    a city ONLY when the row's free text explicitly names a chapter city."""
    for u in unresolved:
        u["placed"] = [(c["city"], c["row"]) for c in chapters
                       if fold(u["name"]) in {fold(n) for n in parse_organizers(c["organizers_raw"])}
                       ] if u["name"] else []   # keys pre-initialised in read_intake
        text = fold(u["events"] + " " + u["why"])
        u["inferred"] = [c["city"] for c in chapters
                         if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(fold(c["city"])), text)]

# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def print_report(st):
    """Print the proposal. Takes the whole State by name — the fields are read
    by attribute, so adding one can't silently rebind a positional parameter.

    Side effect: caches each new row's Luma status on the record as `luma`, so
    main() can gate --write on it without a second round of HTTP calls.
    """
    entries, unresolved, counts, dupes = st.entries, st.unresolved, st.counts, st.dupes
    chapters, last_row, layout = st.chapters, st.last_row, st.layout
    adds, new_rows, near_misses = st.adds, st.new_rows, st.near_misses
    org_col = col_letter(layout["index"]["Organizers"])
    qual = " + ".join("%d %s" % (counts[s], s) for s in SYNC_STATUSES)
    print("Intake  : %d qualifying organizers (%s) across %d cities; %d unresolved; %d duplicate row(s)."
          % (len(entries), qual, len({fold_city(e["city"]) for e in entries}),
             len(unresolved), len(dupes)))
    print("Chapters: %d city rows (2-%d)." % (len(chapters), last_row))

    if adds:
        print("\nProposed adds to existing rows:")
        for a in adds:
            print("  %s (row %d): + %s" % (a["city"], a["row"], "; ".join(a["names"])))
            print("      %s%d -> %r" % (org_col, a["row"], a["new_value"]))
    if new_rows:
        print("\nProposed NEW city rows (appended after row %d):" % last_row)
        # Derived from the header row, not from EDITORIAL_COLUMNS: a twelfth feed
        # column added tomorrow is written blank, so it must be reported blank too.
        blanks = [h for h in layout["headers"]
                  if h and h not in DERIVED_COLUMNS and h not in NEVER_FILLED]
        if blanks:
            print("  (%s left blank on new rows — fill them before the row goes live on the site)"
                  % ", ".join(blanks))
        for n in new_rows:
            status = n["luma"] = luma_status(n["slug"])
            note = {"live": "Luma page live",
                    "absent": "Luma page NOT LIVE yet — create it manually; run aaif-create-chapter for the assets",
                    "unknown": "could not verify the Luma page — check it manually"}[status]
            print("  row %d: %s — %s — https://luma.com/aaif-%s (%s)"
                  % (n["row"], n["city"], "; ".join(n["names"]), n["slug"], note))
    if near_misses:
        print("\nNear-miss cities (NOT written — confirm the right row, or fix the intake city):")
        for m in near_misses:
            cand = ", ".join("%r (row %d)" % c for c in m["candidates"])
            print("  intake %r (%s) ~ chapter %s" % (m["city"], "; ".join(m["names"]), cand))
    if unresolved:
        print("\nUnresolved city — needs a human, never written:")
        for u in unresolved:
            print("  intake row %d: %s (%s) — City (Existing)=%r, City (New)=%r"
                  % (u["row"], u["name"] or "(no name)", u["status"], u["g"], u["h"]))
            print("      Run events before?: %r" % u["events"])
            print("      Why organize / ties: %r" % u["why"])
            if u["inferred"]:
                print("      -> free text names %s; fill City (New) on the intake row to sync."
                      % ", ".join(map(repr, u["inferred"])))
            if u["placed"]:
                print("      -> already on the chapters list: %s — no action needed."
                      % ", ".join("%s (row %d)" % p for p in u["placed"]))
    if dupes:
        print("\nDuplicate intake rows (deduped, first occurrence wins):")
        for d in dupes:
            print("  intake row %d: %s / %s" % (d["row"], d["name"], d["city"]))

    # The chapters side has its own duplicate problem, and only the intake side
    # was ever reported. chap_by_fold is last-wins, so an earlier duplicate row
    # is never updated and its organizers stay invisible to every future run.
    by_fold = {}
    for c in chapters:
        by_fold.setdefault(fold_city(c["city"]), []).append((c["city"], c["row"]))
    collisions = [v for v in by_fold.values() if len(v) > 1]
    if collisions:
        print("\nDuplicate chapter rows (only the LAST is ever updated — merge them):")
        for v in collisions:
            print("  %s" % ", ".join("%r (row %d)" % p for p in v))

    if not adds and not new_rows:
        print("\nNo changes needed — the chapters list is in sync with the intake.")

# ----------------------------------------------------------------------------
# Named, not a bare tuple: every consumer reads fields by attribute (print_report
# takes the State itself, not *state), so adding a field can't silently rebind a
# positional parameter the way it did when `layout` landed.
State = namedtuple("State", "entries unresolved counts dupes chapters last_row "
                            "adds new_rows near_misses layout")

def compute():
    entries, unresolved, counts, dupes = read_intake()
    chapters, last_row, layout = read_chapters()
    adds, new_rows, near_misses = build_proposal(entries, chapters, last_row)
    annotate_unresolved(unresolved, chapters)
    return State(entries, unresolved, counts, dupes, chapters, last_row,
                 adds, new_rows, near_misses, layout)

def new_row_values(n, layout):
    """Full-width feed row for a brand-new city.

    Only the columns derivable from the intake are filled; the editorial ones
    (Country, Generated Geolocation, Summary, Image) are left blank for a human
    — the report names them. Writing the whole width, rather than A:D, is what
    keeps the city out of `Title`, the organizer names out of `City`, and the
    Luma URL out of `Generated Geolocation` after the 2026-07 restructure.

    Every DERIVED_COLUMNS name is indexed directly, not via .get(): read_chapters
    has already aborted if one is missing, so a renamed column can no longer be
    skipped into a blank cell that the report claims was written.
    """
    luma = "https://luma.com/aaif-" + n["slug"]
    derived = {"Title": "AAIF %s Chapter" % n["city"],
               "City": n["city"],
               "Organizers": "; ".join(n["names"]),
               "CTA": "Stay Updated",
               "URL for CTA": luma,
               "Chapter Luma Link": luma}
    vals = [""] * len(layout["headers"])
    for name, v in derived.items():
        vals[layout["index"][name]] = v
    return vals

def assert_rows_unchanged(adds, new_rows, layout):
    """Re-read the City column and confirm the proposal's row numbers still mean
    what they meant when it was computed.

    Row numbers are indices into a snapshot. print_report() sits between that read
    and this write, spending up to 15s per new city checking Luma, so a human
    inserting a row in that window would shift every target — silently putting an
    organizer on the wrong city's row.
    """
    c = col_letter(layout["index"]["City"])
    rows = get_values(CHAPTERS_ID, "'%s'!%s:%s" % (CHAPTERS_TAB, c, c))
    def at(r):
        return cell(rows[r - 1], 0) if 0 < r <= len(rows) else ""
    for a in adds:
        if fold_city(at(a["row"])) != fold_city(a["city"]):
            sys.exit("ABORT: row %d now reads %r, expected %r — the sheet changed while "
                     "the proposal was being built. Nothing was written; re-run."
                     % (a["row"], at(a["row"]), a["city"]))
    for n in new_rows:
        if at(n["row"]):
            sys.exit("ABORT: row %d is no longer empty (now %r) — the sheet changed while "
                     "the proposal was being built. Nothing was written; re-run."
                     % (n["row"], at(n["row"])))

def apply_changes(adds, new_rows, layout):
    assert_rows_unchanged(adds, new_rows, layout)
    org_col = col_letter(layout["index"]["Organizers"])
    last_col = col_letter(len(layout["headers"]) - 1)
    data = [{"range": "'%s'!%s%d" % (CHAPTERS_TAB, org_col, a["row"]),
             "values": [[a["new_value"]]]}
            for a in adds]
    data += [{"range": "'%s'!A%d:%s%d" % (CHAPTERS_TAB, n["row"], last_col, n["row"]),
              "values": [new_row_values(n, layout)]}
             for n in new_rows]
    # One batchUpdate for everything, so a partial failure can't half-sync the
    # sheet. RAW, not USER_ENTERED: a name starting with = + - @ must stay text,
    # never become a formula.
    gws_json("sheets", "spreadsheets", "values", "batchUpdate",
             params={"spreadsheetId": CHAPTERS_ID},
             body={"valueInputOption": "RAW", "data": data})
    return len(data)

def main():
    ap = argparse.ArgumentParser(description="Sync intake organizer decisions into the chapters list.")
    ap.add_argument("--write", action="store_true",
                    help="apply the proposed changes (default: report only)")
    ap.add_argument("--allow-missing-luma", action="store_true",
                    help="write new rows even if their Luma page isn't live yet "
                         "(their CTA will point at a 404 until it is created)")
    a = ap.parse_args()

    # --write recomputes from a fresh read here — a stale proposal is never applied.
    state = compute()
    print_report(state)
    if not a.write or (not state.adds and not state.new_rows):
        return

    # Luma page creation is manual, so "absent" is the NORMAL state for a net-new
    # city — without this gate the common path publishes a "Stay Updated" button
    # pointing at a 404, and the only warning is one line of per-city output.
    stale = [n for n in state.new_rows if n.get("luma") != "live"]
    if stale and not a.allow_missing_luma:
        sys.exit("ABORT: %d new row(s) have no live Luma page: %s.\nTheir CTA would "
                 "point at a 404. Create the pages first, or re-run with "
                 "--allow-missing-luma if that's intended."
                 % (len(stale), ", ".join(n["city"] for n in stale)))

    print("\nApplying %d cell update(s) + %d new row(s) in one batchUpdate..."
          % (len(state.adds), len(state.new_rows)))
    n = apply_changes(state.adds, state.new_rows, state.layout)
    print("Wrote %d range(s)." % n)

    print("\nRe-verifying...")
    # The write has already landed. A bare traceback here would leave the operator
    # unable to tell whether the sheet was modified, so say so explicitly.
    try:
        after = compute()
    except (Exception, SystemExit) as e:
        sys.exit("WRITE WAS APPLIED (%d range(s)) but verification could not run: %s\n"
                 "Re-run without --write to confirm the sheet state." % (n, e))
    if after.adds or after.new_rows:
        print("VERIFY FAILED — still out of sync after write:")
        for x in after.adds:
            print("  row %d %s: + %s" % (x["row"], x["city"], "; ".join(x["names"])))
        for x in after.new_rows:
            print("  new row %s: %s" % (x["city"], "; ".join(x["names"])))
        sys.exit(1)
    print("Verified: a fresh run proposes zero changes.")

if __name__ == "__main__":
    main()
