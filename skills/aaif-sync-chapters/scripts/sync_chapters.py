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

# Feed columns a new row can't derive from the intake — left blank for a human.
EDITORIAL_COLUMNS = ("Country", "Generated Geolocation", "Summary", "Image")

# ----------------------------------------------------------------------------
# gws helpers (same retry/JSON pattern as aaif-create-chapter)
# ----------------------------------------------------------------------------
_TRANSIENT = ("timed out", "internalError", "HTTP request failed",
              "Connection", "temporarily", "rateLimit", "userRateLimit",
              "backendError", "503", "500", "502")

def _gws(cmd, retries=5):
    for i in range(max(1, retries)):   # retries<=0 must raise below, not return None
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < max(1, retries) - 1 and any(k in msg for k in _TRANSIENT):
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
    unequal AND fail the substring near-miss test, so the sync would append a
    duplicate row for a city already on the list.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", fold(s))).strip()

def city_tokens(s):
    return set(fold_city(s).split())

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

    entries    = [{row, name, city, status}]           city resolved, deduped
    unresolved = [{row, name, status, g, h, events, why}]  needs a human
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
            unresolved.append({"row": rownum, "name": name, "status": status,
                               "g": g, "h": h,
                               "events": cell(row, i_events), "why": cell(row, i_why)})
            continue
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
    whose columns have moved before (it was City|Organizers|MLOps|Luma until
    2026-07-21), so read the whole width and resolve every column by header name.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:Z" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]
    i_city, i_org = header_index(headers, CHAPTERS_TAB, "City", "Organizers")
    layout = {"headers": headers, "index": {h: i for i, h in enumerate(headers) if h}}

    chapters, last_row = [], 1
    for rownum, row in enumerate(rows[1:], start=2):
        city = cell(row, i_city)
        if not city:
            continue   # never append into a gap; find the true last City row
        chapters.append({"row": rownum, "city": city, "organizers_raw": cell(row, i_org)})
        last_row = rownum
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
        # Near-miss on substring OR any shared token: 'New Delhi' vs 'Delhi NCR'
        # overlaps on neither substring test but is the same chapter. Over-
        # reporting only costs a human confirmation; under-reporting silently
        # forks a city into two rows.
        toks = city_tokens(grp["city"])
        cands = [c for c, cf in folded
                 if fc in cf or cf in fc or (toks & set(cf.split()))]
        if cands:
            near_misses.append({"city": grp["city"], "names": grp["names"],
                                "candidates": [(c["city"], c["row"]) for c in cands]})
            continue
        new_rows.append({"row": next_row, "city": grp["city"], "names": grp["names"],
                         "slug": slugify(grp["city"])})
        next_row += 1
    return adds, new_rows, near_misses

def annotate_unresolved(unresolved, chapters):
    """Mark unresolved rows already hand-placed on the chapters list, and infer
    a city ONLY when the row's free text explicitly names a chapter city."""
    for u in unresolved:
        u["placed"] = [(c["city"], c["row"]) for c in chapters
                       if fold(u["name"]) in {fold(n) for n in parse_organizers(c["organizers_raw"])}
                       ] if u["name"] else []
        text = fold(u["events"] + " " + u["why"])
        u["inferred"] = [c["city"] for c in chapters
                         if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(fold(c["city"])), text)]

# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def print_report(entries, unresolved, counts, dupes, chapters, last_row,
                 adds, new_rows, near_misses, layout):
    org_col = col_letter(layout["index"]["Organizers"])
    qual = " + ".join("%d %s" % (counts[s], s) for s in SYNC_STATUSES)
    print("Intake  : %d qualifying organizers (%s) across %d cities; %d unresolved; %d duplicate row(s)."
          % (len(entries), qual, len({fold(e["city"]) for e in entries}), len(unresolved), len(dupes)))
    print("Chapters: %d city rows (2-%d)." % (len(chapters), last_row))

    if adds:
        print("\nProposed adds to existing rows:")
        for a in adds:
            print("  %s (row %d): + %s" % (a["city"], a["row"], "; ".join(a["names"])))
            print("      %s%d -> %r" % (org_col, a["row"], a["new_value"]))
    if new_rows:
        print("\nProposed NEW city rows (appended after row %d):" % last_row)
        blanks = [c for c in EDITORIAL_COLUMNS if c in layout["index"]]
        if blanks:
            print("  (%s left blank on new rows — fill them before the row goes live on the site)"
                  % ", ".join(blanks))
        for n in new_rows:
            status = luma_status(n["slug"])
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

    if not adds and not new_rows:
        print("\nNo changes needed — the chapters list is in sync with the intake.")

# ----------------------------------------------------------------------------
# Named, not a bare tuple: print_report() takes the whole thing positionally and
# main() picks fields out of it, so a new field used to mean editing every
# unpack site by hand (that's how `layout` landed).
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
    — the report says so. Writing the whole width, rather than A:D, is what
    keeps names out of Title/Country after the 2026-07 restructure.
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
        i = layout["index"].get(name)
        if i is not None:
            vals[i] = v
    return vals

def apply_changes(adds, new_rows, layout):
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
    a = ap.parse_args()

    # --write recomputes from a fresh read here — a stale proposal is never applied.
    state = compute()
    print_report(*state)
    if not a.write or (not state.adds and not state.new_rows):
        return

    print("\nApplying %d cell update(s) + %d new row(s) in one batchUpdate..."
          % (len(state.adds), len(state.new_rows)))
    n = apply_changes(state.adds, state.new_rows, state.layout)
    print("Wrote %d range(s)." % n)

    print("\nRe-verifying...")
    after = compute()
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
