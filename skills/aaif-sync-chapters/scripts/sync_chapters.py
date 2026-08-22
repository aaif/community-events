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
import argparse, json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.request
from collections import namedtuple

# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks emails as a***@domain and
# names as initials in every printed line. Each standalone script carries its
# own copy of this flag and these two helpers.
REDACT = False


def redact_email(e):
    if not REDACT or not e or "@" not in e:
        return e
    local, _, domain = e.partition("@")
    return "%s***@%s" % (local[:1], domain)


def redact_name(n):
    if not REDACT or not n:
        return n
    return " ".join(w[0].upper() + "." for w in n.split() if w)


def redact_names_cell(cell):
    """The Organizers cell is a '; '-joined list of names; mask each."""
    if not REDACT or not cell:
        return cell
    return "; ".join(redact_name(x.strip()) for x in cell.split(";"))


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

# The per-chapter resource map: where this chapter's stuff actually lives.
# Blank on new rows like the editorial columns, but they are NOT site data and no
# human fills them by hand — `sync_resources.py` proposes them from Drive and
# Slack. Reported separately so a new-chapter operator is pointed at that engine
# rather than at a summary to write.
#
# `Chapter Luma Link` is deliberately absent: it is the one resource this engine
# derives itself (see DERIVED_COLUMNS), because a new row's CTA depends on it.
RESOURCE_COLUMNS = ("Chapter Folder", "Slack Channel", "Organizer Channel",
                    "Country Channel", "Organizer Handles")

# The one resource column that is DERIVED rather than recorded, and therefore the
# one that is rewritten rather than only filled when blank. It answers "who should
# be in that organizer channel" — a name does not tell you who to look for in
# Slack, a handle does. A stale handle list is worse than none: it is read as a
# roster, so someone who left keeps looking current. Everything else here is a
# fact about where a thing lives and is never overwritten.
REWRITTEN_COLUMNS = ("Organizer Handles",)

# What a resource cell means when it is not a value. A BLANK cell is "nobody has
# looked yet" and every engine keeps proposing for it; NO_RESOURCE is a human
# saying "there genuinely isn't one" and stops the guessing for good. The sheet
# needs this sentinel because a spreadsheet has no null — in channel_map.json,
# which these columns replace, the same statement was a JSON `null`.
NO_RESOURCE = "none"

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
    # the shared download()/upload() below run it from the file's own directory.
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

# Shared by sync_about.py (.docx) and sync_crm.py (.xlsx) — one copy, here with
# the other gws plumbing, because two byte-identical Drive helpers inside one
# skill had already drifted apart once in comment text alone.
def download(file_id, path):
    """Fetch a Drive file's bytes to `path` and return them.

    gws rejects --output paths outside its cwd, so it runs in the file's dir.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _gws(["gws", "drive", "files", "get", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True, "alt": "media"}),
          "--output", os.path.basename(path)], cwd=os.path.dirname(path) or ".")
    with open(path, "rb") as fh:
        return fh.read()

def upload(file_id, path, raw, content_type):
    """Replace a Drive file's content with `raw` (staged at `path` for gws)."""
    with open(path, "wb") as fh:
        fh.write(raw)
    _gws(["gws", "drive", "files", "update", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True}),
          "--upload", os.path.basename(path), "--upload-content-type", content_type],
         cwd=os.path.dirname(path) or ".")

def fresh_if_unchanged(file_id, tmp_path, planned_bytes):
    """Re-download a Drive file and say whether it drifted from a plan's bytes.

    Returns (fresh_bytes, changed). Shared by sync_crm.write_workbooks,
    sync_about.apply_writes and migrate_status_prospect.write_crm — the one
    compare every write gate hangs on: planning spans minutes plus the approval
    pause, so a human edit in that window is NORMAL, and uploading over it
    would silently revert it. Each caller keeps its own backup and
    skip-reporting behaviour; only the "did the remote move under the plan"
    question lives here, so the callers cannot drift apart in what "unchanged"
    means.
    """
    fresh = download(file_id, tmp_path)
    return fresh, fresh != planned_bytes

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

def bad_public_text(kind, s):
    """Why this intake free-text must not reach the public website feed — or None.

    The intake sheet is fed by a public Google Form, and this script is the last
    controlled point before a name or city is republished. RAW input mode already
    stops formula injection; this stops markup and control characters.

    The caller EXCLUDES the offending row and reports it — one hostile or fat-
    fingered form submission used to sys.exit the whole engine, report mode
    included, holding every other chapter's sync hostage to a row only a human
    can fix. The safety property is unchanged: a row this flags is never
    written anywhere.
    """
    if _UNSAFE_PUBLIC_TEXT.search(s):
        return ("%s %r contains control characters or angle brackets, which "
                "must never reach the public feed" % (kind, s))
    if len(s) > MAX_PUBLIC_TEXT:
        return "%s is %d characters (max %d)" % (kind, len(s), MAX_PUBLIC_TEXT)
    return None

def resolve_city(existing, new):
    """Resolve an intake row's chapter city: `City (New)` wins if non-empty, else
    `City (Existing)` unless it's an "Other…" placeholder, else "" (needs a human).

    Imported by sync_about.py rather than copied: a row that resolves to one city
    here and another there would put an organizer on the chapters list under one
    city and into a different chapter's About doc.
    """
    return new or (existing if existing and not fold(existing).startswith("other") else "")

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
    """Return (entries, unresolved, status_counts, dupes, malformed).

    entries    = [{row, name, city, status}]                        city resolved, deduped
    unresolved = [{row, name, status, g, h, events, why,
                   placed, inferred}]                               needs a human
    malformed  = [{row, name, city, why}]      public-unsafe text — never written
    """
    rows = get_values(INTAKE_ID, "%s!A:U" % INTAKE_TAB)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    i_status, i_name, i_g, i_h, i_events, i_why = header_index(
        rows[0], INTAKE_TAB, "Status", "Full name", "City (Existing)", "City (New)",
        "Run events before?", "Why organize / ties")

    entries, unresolved, dupes, malformed = [], [], [], []
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
        city = resolve_city(g, h)
        if not name or not city:
            # placed/inferred are filled by annotate_unresolved but initialised
            # here, so the record is never half-built and print_report is safe
            # regardless of call order.
            unresolved.append({"row": rownum, "name": name, "status": status,
                               "g": g, "h": h,
                               "events": cell(row, i_events), "why": cell(row, i_why),
                               "placed": [], "inferred": []})
            continue
        # Skip-and-report, never write: excluding the row here keeps the
        # injection-safety property — a flagged value reaches no cell and no
        # About doc, because it never becomes an entry at all. The CRM path
        # does NOT pass through here (sync_crm.read_role_tab reads the role
        # tabs directly), so it runs the same bad_public_text check itself;
        # together the two checks are what make "no cell, no About doc and
        # no CRM" a true statement rather than a hopeful one.
        bad = bad_public_text("name", name) or bad_public_text("city", city)
        if bad:
            malformed.append({"row": rownum, "name": name, "city": city, "why": bad})
            continue
        key = (fold(name), fold(city))
        if key in seen:
            dupes.append({"row": rownum, "name": name, "city": city})
            continue
        seen.add(key)
        entries.append({"row": rownum, "name": name, "city": city, "status": status})
    return entries, unresolved, counts, dupes, malformed

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
    # counts[] tallies rows BEFORE dedup, len(entries) is people AFTER — print the
    # arithmetic, or the headline reads "102 (102 + 2)" and looks like a bug.
    print("Intake  : %d qualifying organizers (%s, minus %d duplicate row(s)) across %d cities; %d unresolved."
          % (len(entries), qual, len(dupes),
             len({fold_city(e["city"]) for e in entries}), len(unresolved)))
    print("Chapters: %d city rows (2-%d)." % (len(chapters), last_row))

    if adds:
        print("\nProposed adds to existing rows:")
        for a in adds:
            print("  %s (row %d): + %s" % (a["city"], a["row"], "; ".join(map(redact_name, a["names"]))))
            print("      %s%d -> %r" % (org_col, a["row"], redact_names_cell(a["new_value"])))
    if new_rows:
        print("\nProposed NEW city rows (appended after row %d):" % last_row)
        # Derived from the header row, not from EDITORIAL_COLUMNS: a twelfth feed
        # column added tomorrow is written blank, so it must be reported blank too.
        # Split by who fills it — an operator told to "fill them before the row
        # goes live" will go hunting for a Slack channel to type in by hand, which
        # is exactly the guessing sync_resources.py exists to prevent.
        blanks = [h for h in layout["headers"]
                  if h and h not in DERIVED_COLUMNS and h not in NEVER_FILLED]
        editorial = [h for h in blanks if h not in RESOURCE_COLUMNS]
        resources = [h for h in blanks if h in RESOURCE_COLUMNS]
        if editorial:
            print("  (%s left blank on new rows — fill them before the row goes live on the site)"
                  % ", ".join(editorial))
        if resources:
            print("  (%s left blank — run sync_resources.py once the chapter's folder "
                  "and channels exist)" % ", ".join(resources))
        for n in new_rows:
            status = n["luma"] = luma_status(n["slug"])
            note = {"live": "Luma page live",
                    "absent": "Luma page NOT LIVE yet — create it manually; run aaif-create-chapter for the assets",
                    "unknown": "could not verify the Luma page — check it manually"}[status]
            print("  row %d: %s — %s — https://luma.com/aaif-%s (%s)"
                  % (n["row"], n["city"], "; ".join(map(redact_name, n["names"])), n["slug"], note))
    if near_misses:
        print("\nNear-miss cities (NOT written — confirm the right row, or fix the intake city):")
        for m in near_misses:
            cand = ", ".join("%r (row %d)" % c for c in m["candidates"])
            print("  intake %r (%s) ~ chapter %s" % (m["city"], "; ".join(map(redact_name, m["names"])), cand))
    if unresolved:
        print("\nUnresolved city — needs a human, never written:")
        for u in unresolved:
            print("  intake row %d: %s (%s) — City (Existing)=%r, City (New)=%r"
                  % (u["row"], redact_name(u["name"]) or "(no name)", u["status"], u["g"], u["h"]))
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
            print("  intake row %d: %s / %s" % (d["row"], redact_name(d["name"]), d["city"]))
    if st.malformed:
        # Excluded, loudly: everything else still syncs, but a row listed here
        # reaches nothing until the intake text is fixed — the values are
        # printed repr'd so control characters are visible.
        print("\nMalformed public-form text — EXCLUDED from every write until the "
              "intake row is fixed:")
        for m in st.malformed:
            print("  intake row %d (city %r): %s" % (m["row"], m["city"], m["why"]))

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
                            "adds new_rows near_misses layout malformed")

def compute():
    entries, unresolved, counts, dupes, malformed = read_intake()
    chapters, last_row, layout = read_chapters()
    adds, new_rows, near_misses = build_proposal(entries, chapters, last_row)
    annotate_unresolved(unresolved, chapters)
    return State(entries, unresolved, counts, dupes, chapters, last_row,
                 adds, new_rows, near_misses, layout, malformed)

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

def partition_new_rows(new_rows, last_row, allow_missing):
    """Split proposed new rows into (write, held) and renumber the written ones.

    A city whose Luma page is not live is HELD BACK, never written — its CTA
    would point at a 404 — but it must not block the rows and adds that ARE
    ready: page creation is manual and can lag a decision by weeks, so one
    pending city used to freeze every other chapter's sync. "unknown" is held
    too: a page we could not verify gets a human look, not a published button.

    The written rows are renumbered onto consecutive rows after last_row, so a
    held city never leaves a blank row in the middle of the feed. Rows are
    copied, not mutated — the caller's proposal still describes what the report
    showed. --allow-missing-luma writes everything, dead CTAs included.
    """
    if allow_missing:
        write, held = [dict(n) for n in new_rows], []
    else:
        write = [dict(n) for n in new_rows if n.get("luma") == "live"]
        held = [n for n in new_rows if n.get("luma") != "live"]
    for i, n in enumerate(write):
        n["row"] = last_row + 1 + i
    return write, held

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
    ap.add_argument("--redact", action=argparse.BooleanOptionalAction,
                    default=bool(os.environ.get("CI")),
                    help="mask emails (a***@domain) and names (initials) on "
                         "stdout; default on when CI is set")
    a = ap.parse_args()
    global REDACT
    REDACT = a.redact

    # --write recomputes from a fresh read here — a stale proposal is never applied.
    state = compute()
    print_report(state)
    drift = bool(state.adds or state.new_rows)
    if not a.write:
        # Exit convention (shared by all five engines, consumed by nightly.py):
        # report mode exits 0 when in sync, 2 when it proposes changes.
        return 2 if drift else 0
    if not drift:
        return 0

    # Luma page creation is manual, so "absent" is the NORMAL state for a net-new
    # city — without this gate the common path publishes a "Stay Updated" button
    # pointing at a 404, and the only warning is one line of per-city output.
    # print_report already stamped n["luma"]; the loop below keeps the partition
    # correct even if a future path reaches here without printing the report,
    # where a missing key would silently hold every row.
    for n in state.new_rows:
        if "luma" not in n:
            n["luma"] = luma_status(n["slug"])
    to_write, held = partition_new_rows(state.new_rows, state.last_row,
                                        a.allow_missing_luma)
    if held:
        print("\nHELD BACK %d new row(s) with no live Luma page (their CTA would "
              "point at a 404): %s.\nCreate the page(s) and re-run, or re-run with "
              "--allow-missing-luma if a dead CTA is intended."
              % (len(held), ", ".join(n["city"] for n in held)))
    if not state.adds and not to_write:
        print("Nothing else to write — every proposed change is held back.")
        return 2

    print("\nApplying %d cell update(s) + %d new row(s) in one batchUpdate..."
          % (len(state.adds), len(to_write)))
    # The report above printed pre-partition row numbers; holding a row shifts
    # everything after it, so name the FINAL rows — an operator filling the
    # blank editorial cells must be pointed at the row the city actually got.
    for nr in to_write:
        print("  writing %s at row %d" % (nr["city"], nr["row"]))
    n = apply_changes(state.adds, to_write, state.layout)
    print("Wrote %d range(s)." % n)

    print("\nRe-verifying...")
    # The write has already landed. A bare traceback here would leave the operator
    # unable to tell whether the sheet was modified, so say so explicitly.
    try:
        after = compute()
    except (Exception, SystemExit) as e:
        sys.exit("WRITE WAS APPLIED (%d range(s)) but verification could not run: %s\n"
                 "Re-run without --write to confirm the sheet state." % (n, e))
    # Held-back rows are EXPECTED to re-propose — the verify's question is "did
    # everything we actually wrote land", not "is the sheet fully in sync".
    # Matching held rows by city name alone is churn-fragile: an intake edit
    # that respells a held city during the multi-minute run window would miss
    # held_cities and indict a write that fully succeeded. So a re-proposed row
    # that STILL has no live Luma page is also expected — this run could never
    # have written it, whatever it is called now. (Checked only when something
    # was held; with --allow-missing-luma nothing is, and the verify stays
    # strict.)
    held_cities = {fold_city(h["city"]) for h in held}
    still_held, leftover_rows = [], []
    for x in after.new_rows:
        if fold_city(x["city"]) in held_cities or \
                (held and luma_status(x["slug"]) != "live"):
            still_held.append(x)
        else:
            leftover_rows.append(x)
    if after.adds or leftover_rows:
        print("VERIFY FAILED — still out of sync after write:")
        for x in after.adds:
            print("  row %d %s: + %s" % (x["row"], x["city"], "; ".join(x["names"])))
        for x in leftover_rows:
            print("  new row %s: %s" % (x["city"], "; ".join(x["names"])))
        sys.exit(1)
    if held:
        # Exit 2, the shared drift code: the held rows are still pending work,
        # and a wrapper (nightly.py) must keep seeing them until the pages exist.
        # Count what the re-read actually proposes, not len(held) — a held row
        # deleted from the intake during the run would overstate pending work.
        print("Verified: a fresh run proposes only held-back row(s) "
              "(%d still pending)." % len(still_held))
        return 2
    print("Verified: a fresh run proposes zero changes.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
