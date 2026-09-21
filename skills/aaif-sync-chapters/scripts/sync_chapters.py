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
import argparse, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from collections import namedtuple
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))
# --- stdout redaction -------------------------------------------------------
# The report names real people. `--redact` (default ON when CI is set, because
# a CI log is a publication on a public repo) masks them in every printed line.
# The flag and the helpers it governs come from ONE module on purpose: a helper
# that reads a different module's flag is a helper this `--redact` does not
# actually govern, which is how an address once reached a public CI log.
from aaif_events import findings  # noqa: E402
from aaif_events import gws as _gws_mod  # noqa: E402
# cell/header_index/col_letter are re-exported: five sibling scripts import
# them from here. `header_index` now aborts on a DUPLICATED header, which this
# module's own copy did not — it silently resolved to the first of the two.
from aaif_events.sheets import cell, col_letter, header_index  # noqa: E402,F401
from aaif_events.redact import (add_redact_flag, redact_name, redact_names_cell, redact_text,  # noqa: E402
                                set_redaction)


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

#: The chapter lifecycle column, added 2026-09-18. Set by a HUMAN, exactly like
#: the organizer `Status` it is modelled on — and for the same reason: the
#: estate holds no reliable chapter-founding date, so "provisioned last week"
#: and "died a year ago" are indistinguishable from data. Both candidate
#: proxies measure infrastructure, not chapters: channel creation clusters on
#: the bulk provisioning runs, and all 98 Drive folders carry just 9 distinct
#: creation dates, the oldest 92 days old — younger than events already on the
#: Past Events tab. A script that guessed would mislabel ~30 chapters.
#:
#: **NO script writes this column.** Every chapter is therefore born blank, and
#: blank counts as live (see RETIRED_STATUSES). Stamping `Provisioned` at
#: creation is the obvious producer — it is the one instant the answer is known
#: — but create_chapter.py does not write the feed row today (sync_chapters'
#: new-row path does), so wiring that up is its own change. Until then, moving a
#: chapter out of blank is entirely manual, and chapter_health.py exists to rank
#: which ones to look at.
H_STATUS = "Status"
#: Companion to Status="Merged": the City this chapter folded into. Coalescing a
#: satellite into its metro (Noida -> Delhi NCR) is otherwise unrecorded, and
#: "where did this chapter go" is the question a retired row has to answer.
H_MERGED_INTO = "Merged Into"
#: Human-owned free text beside a chapter row. Read and quoted, never written.
H_OPS_NOTES = "Ops Notes"

CHAPTER_STATUSES = ("Active", "Provisioned", "Dormant", "Merged", "Deprecated")

#: Statuses that RETIRE a chapter — they stop counting toward the cap. Anything
#: else counts, INCLUDING blank: an untriaged chapter is a live one until a
#: human says otherwise, and the alternative (blank = free slot) would let the
#: estate grow past the cap simply by nobody filling the column in.
RETIRED_STATUSES = ("Merged", "Deprecated")

#: Hard ceiling on live chapters (set 2026-09-18). The point is not the number:
#: it is that growth has to be paid for by sunsetting or coalescing something,
#: which is a decision somebody has to make rather than a queue that silently
#: grows.
#:
#: The count is **rows on the chapters tab**, and the feed row is the only place
#: that count changes — so this engine's new-row path is the real chokepoint.
#: create_chapter.py checks the same cap before building a Drive folder, but be
#: precise about what that check is worth: making a folder does not add a row,
#: so it cannot detect its own predecessors. Three `create_chapter --write` runs
#: at 99 live chapters each read 99, each pass, and each build a folder; the
#: next sync run then refuses all three rows at once. The folder check is an
#: early warning that saves the common single-run case, NOT a guarantee — the
#: guarantee lives on the row. Closing that gap properly means create_chapter
#: claiming its row as it creates the folder, which it does not do yet.
CHAPTER_CAP = 100

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
# gws helpers
# ----------------------------------------------------------------------------
# The subprocess plumbing — retries, backoff, the scrubbed environment and the
# U+2028-safe JSON split — lives in `aaif_events.gws`. This module used to carry
# its own copy under a "stays standalone" comment; the copy in aaif-audit-slack
# drifted away from it (it retried on `Internal error` and this one did not), so
# whether a run survived a sick API depended on which script you were in.
#
# The names are re-exported because a dozen sibling scripts import them from
# here. There is no flag involved, so unlike the redaction helpers this is a
# plain alias, not a second source of truth.
gws_json = _gws_mod.json_out
get_values = _gws_mod.values
download = _gws_mod.download
upload = _gws_mod.upload
_gws = _gws_mod.run
_transient = _gws_mod.transient


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

#: The only hosts this tool will fetch. `audit_luma` feeds it a `Chapter Luma
#: Link` cell, and a spreadsheet cell is untrusted input: without this an
#: operator running a READ-ONLY report can be made to GET an internal address
#: from their own machine, with live/absent coming back as an existence oracle.
LUMA_HOSTS = ("luma.com", "www.luma.com", "lu.ma")

#: Seconds between requests in an --audit-luma sweep. See audit_luma().
LUMA_PAUSE = 0.4


def url_status(url):
    """'live' (200) / 'absent' (404) / 'unknown' (couldn't verify, or not ours).

    "unknown" never counts as LIVE: a page we could not reach is a page a human
    should look at, not one we assume is fine. (It is reported by --audit-luma
    and counted toward that run's non-zero exit, but it is not "dead".)

    Only https on a Luma host is fetched at all. Anything else — another host,
    http, a `file://` path, a cell with no scheme — is refused without a request.
    """
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return "unknown"
    if u.scheme != "https" or u.hostname not in LUMA_HOSTS:
        return "unknown"
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return "live" if r.status == 200 else "unknown"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "absent"
        # 429 is reported as its own state, not folded into "unknown". A sweep
        # of ~96 rows reliably trips luma.com's limiter (observed live: a 429
        # with no Retry-After, after which every subsequent row also 429s), and
        # a hundred rows of "could not verify" is noise that buries the one row
        # that is genuinely dead.
        return "throttled" if e.code == 429 else "unknown"
    except (urllib.error.URLError, TimeoutError, ValueError):
        # ValueError is what urlopen raises for a malformed URL that got past
        # the split above; one bad cell must not abort a 90-row sweep.
        return "unknown"

def luma_status(slug):
    """'live' / 'absent' / 'unknown' for the Luma page a new row would point at."""
    return url_status("https://luma.com/aaif-" + slug)

#: What one `--audit-luma` sweep measured. `dead`/`unknown` are (row, city,
#: link); `blank` is (row, city); `throttled_at` is the row the sweep stopped
#: on, or None. Kept as data so the printed report and the JSON findings are
#: two readers of one sweep, never two sweeps.
LumaAudit = namedtuple("LumaAudit", "checked dead unknown blank throttled_at")


def audit_luma(report=None):
    """Report every EXISTING feed row whose `Chapter Luma Link` is not live.

    The standing replacement for the old write-time gate. Holding a new row back
    made the engine re-propose that city every run, which is how a missing Luma
    page stayed visible. Now that rows are written regardless, nothing would
    mention the page again — a row with a dead "Stay Updated" button would just
    sit there. This makes that checkable on demand across the WHOLE feed, which
    is strictly more than the gate ever covered: the gate only ever looked at
    the cities being added today, never at the 90 already on the sheet.

    Opt-in (`--audit-luma`) because it is one HTTP request per row.

    Returns the pending-work count. When `report` (a findings.Report) is given,
    the same sweep is also recorded on it via luma_findings().
    """
    sweep = sweep_luma()
    if report is not None:
        luma_findings(report, sweep)
    return print_luma_audit(sweep)


def sweep_luma():
    """One paced GET per feed row with a link; the LumaAudit of what came back."""
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    idx = {h.strip(): i for i, h in enumerate(rows[0]) if h.strip()}
    i_city, i_link = idx.get("City"), idx.get("Chapter Luma Link")
    if i_city is None or i_link is None:
        sys.exit("ABORT: --audit-luma needs both 'City' and 'Chapter Luma Link' "
                 "on %s." % CHAPTERS_TAB)
    dead, unknown, blank, checked, throttled_at = [], [], [], 0, None
    for rownum, row in enumerate(rows[1:], start=2):
        city, link = cell(row, i_city), cell(row, i_link)
        if not city:
            continue
        if not link:
            blank.append((rownum, city))
            continue
        # Paced, because an unthrottled sweep trips the limiter partway through
        # and everything after it is unverified.
        if checked:
            time.sleep(LUMA_PAUSE)
        st = url_status(link)
        if st == "throttled":
            # Stop at the FIRST 429. Once the limiter engages every remaining
            # row returns the same thing, so continuing turns one upstream fact
            # into ninety rows of false findings.
            throttled_at = rownum
            break
        checked += 1
        if st == "absent":
            dead.append((rownum, city, link))
        elif st == "unknown":
            unknown.append((rownum, city, link))
    return LumaAudit(checked, dead, unknown, blank, throttled_at)


def luma_headline(sweep):
    """The one-line count summary — the text report's first line and the JSON summary."""
    return ("Luma audit: %d row(s) with a link checked; %d dead, %d unverifiable, "
            "%d row(s) with no link.%s"
            % (sweep.checked, len(sweep.dead), len(sweep.unknown), len(sweep.blank),
               "  (stopped early — rate-limited)" if sweep.throttled_at else ""))


def luma_findings(report, sweep):
    """Record a LumaAudit on a findings.Report (step `luma`). Pure; no I/O.

    Subjects are cities, as the text report prints them. A dead link is `bad`
    (the CTA on the site 404s); unverifiable and no-link rows are `warn`
    (pending work, not proven broken); the rate-limit stop is one `warn` row so
    the page shows that the sweep did not finish rather than a short, clean list.
    """
    report.summary = luma_headline(sweep)
    report.measure("rows checked", sweep.checked)
    report.measure("dead", len(sweep.dead), "bad" if sweep.dead else "ok")
    report.measure("unverifiable", len(sweep.unknown), "warn" if sweep.unknown else None)
    report.measure("no link", len(sweep.blank), "warn" if sweep.blank else None)
    report.measure("rate-limited at", "row %d" % sweep.throttled_at if sweep.throttled_at else "no",
                   "warn" if sweep.throttled_at else "ok")
    for rownum, city, link in sweep.dead:
        report.find("dead link", city, "row %d: %s (404)" % (rownum, link),
                    severity="bad", action="create the Luma page or fix the link")
    for rownum, city, link in sweep.unknown:
        report.find("unverifiable link", city, "row %d: %s" % (rownum, link),
                    severity="warn", action="check by hand")
    for rownum, city in sweep.blank:
        report.find("no link", city, "row %d" % rownum,
                    severity="warn", action="create the Luma page and fill the cell")
    if sweep.throttled_at:
        report.find("sweep rate-limited", "row %d" % sweep.throttled_at,
                    "rows after it were not checked", severity="warn",
                    action="re-run later")
    return report


def print_luma_audit(sweep):
    """Print the sweep; return the pending-work count the exit code hangs on."""
    checked, dead, unknown, blank, throttled_at = sweep
    print("\n" + luma_headline(sweep))
    for label, items in (("DEAD (404 — the CTA button goes nowhere)", dead),
                         ("could not verify — check by hand", unknown)):
        if items:
            print("  %s:" % label)
            for rownum, city, link in items:
                print("     row %-4d %-22s %s" % (rownum, city, link))
    if blank:
        print("  no Chapter Luma Link at all: %s"
              % ", ".join("%s (row %d)" % (c, r) for r, c in blank))
    # ALL THREE are pending work, and the caller turns this into the exit code.
    # Returning only len(dead) meant a sweep that luma.com rate-limited into 90
    # "unknown"s exited 0 — a night that verified nothing reading as a clean
    # one. `blank` counts too: a row with no link at all is the state this
    # engine now CREATES by default, so the audit that replaced the write-time
    # gate has to be the thing that reports it.
    if throttled_at:
        # The shared PARTIAL marker (see nightly.py): an involuntary skip must
        # never read as a clean result.
        print("  PARTIAL: luma.com rate-limited this sweep at row %d — the rows "
              "after it were NOT checked. Re-run later to finish."
              % throttled_at)
    if unknown:
        print("  PARTIAL: %d row(s) could not be verified — this run did NOT "
              "prove those links are live." % len(unknown))
    return len(dead) + len(unknown) + len(blank) + (1 if throttled_at else 0)

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
    # Deliberately far wider than the sheet. A tight right edge truncates the NEWEST column
    # first, which is the one a reader is most likely to have just added — and
    # `header_index` then aborts with "layout changed" pointing at a column that
    # is right there on the sheet. Read wide; resolve by header name.
    rows = get_values(INTAKE_ID, "%s!A:AZ" % INTAKE_TAB)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    # `Run events before?` is on the live sheet TWICE (a form-version artefact).
    # It is read-only here — it is printed in the unresolved-row report and
    # never written — so it resolves to the first match with a warning rather
    # than aborting the engine. Every other column here stays fatal on a
    # duplicate, which is the case that would land a write in the wrong column.
    i_status, i_name, i_g, i_h, i_events, i_why = header_index(
        rows[0], INTAKE_TAB, "Status", "Full name", "City (Existing)", "City (New)",
        "Run events before?", "Why organize / ties",
        first_of=("Run events before?",))

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

def census_of(chapters):
    """(live, retired, by_status) over read_chapters()-shaped dicts.

    The ONE place the cap's counting rule lives — both guards call it, because
    two spellings of "which statuses count" is how the folder guard and the row
    guard would come to disagree about whether the estate is full.

    `live` is what the cap counts: every chapter whose Status is not in
    RETIRED_STATUSES, blank included. `by_status` carries blanks under the key
    "" so a caller can say how much of the estate is untriaged, which decides
    whether a cap refusal is actionable or just a wall.

    A status outside CHAPTER_STATUSES counts as LIVE but is reported separately
    (see unknown_statuses below). The column is hand-edited and its dropdown is
    advisory rather than strict, so `merged`, `Retired` or `Archived` are all
    typeable — and each would otherwise fold silently into the live count with
    nothing anywhere saying so.
    """
    by_status, live, retired = {}, 0, 0
    for c in chapters:
        if not (c.get("city") or "").strip():
            continue
        st = (c.get("status") or "").strip()
        by_status[st] = by_status.get(st, 0) + 1
        if st in RETIRED_STATUSES:
            retired += 1
        else:
            live += 1
    return live, retired, by_status


def unknown_statuses(by_status):
    """Statuses on the sheet that are not in the vocabulary, worst (most rows) first.

    Split out so both the refusal and chapter_health can surface the same set
    rather than each deciding for itself what counts as a typo.
    """
    # By COUNT descending, then name — "worst first" as the docstring says and
    # as cap_refusal renders it. Sorting by the status string put a 40-chapter
    # typo below a 1-chapter one, which is backwards for a list whose job is
    # "fix this spelling and the count may change".
    return sorted(((st, n) for st, n in by_status.items()
                   if st and st not in CHAPTER_STATUSES),
                  key=lambda kv: (-kv[1], kv[0]))


def cap_refusal(live, by_status, what, incoming=1):
    """The abort message when the cap is reached, or None when there is room.

    `live` is the current count and `incoming` the rows about to be added
    (default 1 — every caller is asking "may I add?"). They are separate
    parameters because the caller used to smuggle the breakdown through `what`
    while passing the SUM as `live`, so the headline printed "N live chapters"
    for a number that was not live chapters.

    Deliberately names the untriaged count and the retire-or-coalesce options: a
    refusal that only says "full" leaves the reader with no next step, and the
    next step is a decision about an EXISTING chapter, not a retry.
    """
    if live + incoming <= CHAPTER_CAP:
        return None
    # The arithmetic is only worth showing when it is not obvious: adding one
    # more to N is, adding seven is not.
    head = ("ABORT: %d live chapters" % live if incoming <= 1 else
            "ABORT: %d live chapters + %d new = %d" % (live, incoming, live + incoming))
    lines = ["%s — the cap is %d. Refusing to %s." % (head, CHAPTER_CAP, what),
             "",
             "Growth is paid for by retiring or coalescing an existing chapter:",
             "  * set Status=Merged on the chapters tab (plus %r) to fold a "
             "satellite into its metro, or" % H_MERGED_INTO,
             "  * set Status=Deprecated to retire one outright."]
    untriaged = by_status.get("", 0)
    if untriaged:
        lines += ["",
                  "%d chapter(s) have a BLANK Status and have never been "
                  "triaged — run chapter_health.py for the ranked evidence "
                  "before deciding." % untriaged]
    odd = unknown_statuses(by_status)
    if odd:
        lines += ["",
                  "These Status values are not in the vocabulary and are being "
                  "counted as LIVE — fix the spelling and the count may change:",
                  "  " + ", ".join("%r (%d)" % (st, n) for st, n in odd)]
    return "\n".join(lines)


def assert_under_cap(what="create a new chapter", incoming=1):
    """Abort unless there is room under CHAPTER_CAP. Reads the tab live."""
    chapters, _last_row, _layout = read_chapters()
    live, _retired, by_status = census_of(chapters)
    msg = cap_refusal(live, by_status, what, incoming=incoming)
    if msg:
        sys.exit(msg)


def read_chapters():
    """Return (chapters, last_row, layout). chapters = [{row, city, organizers_raw,
    status, merged_into, public, ops_notes}]. All keys are always present (""
    when the column is absent), so consumers subscript rather than .get().

    layout = {headers, index: {name -> 0-based col}} — the tab is a website feed
    whose columns have moved before, so read well past the current width and
    resolve every column by header name.
    """
    rows = get_values(CHAPTERS_ID, "'%s'!A:AZ" % CHAPTERS_TAB)
    if not rows:
        sys.exit("ABORT: chapters tab %r came back empty." % CHAPTERS_TAB)
    headers = [h.strip() for h in rows[0]]

    # A duplicated header would resolve differently for reads and writes: the
    # dict comprehension below keeps the LAST match. header_index() now aborts
    # on a duplicate too, but only for the columns it is asked about — this
    # checks EVERY header, which is what the write path needs. Refuse rather
    # than pick a winner.
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
    # .get, not [...]: a checkout pointed at a sheet from before 2026-09-18 has
    # no Status column, and every read path here must keep working against it
    # rather than abort on a column the cap happens to care about.
    i_status = layout["index"].get(H_STATUS)
    i_merged = layout["index"].get(H_MERGED_INTO)
    i_pub = layout["index"].get("Slack Channel")
    i_notes = layout["index"].get(H_OPS_NOTES)

    chapters, last_row = [], 1
    for rownum, row in enumerate(rows[1:], start=2):
        city = cell(row, i_city)
        if not city:
            continue   # never append into a gap; find the true last City row
        chapters.append({"row": rownum, "city": city, "organizers_raw": cell(row, i_org),
                         "status": cell(row, i_status) if i_status is not None else "",
                         "merged_into": cell(row, i_merged) if i_merged is not None else "",
                         "public": cell(row, i_pub) if i_pub is not None else "",
                         "ops_notes": cell(row, i_notes) if i_notes is not None else ""})
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
                  % (u["row"], redact_name(u["name"]) or "(no name)", u["status"],
                     redact_text(u["g"]), redact_text(u["h"])))
            print("      Run events before?: %r" % redact_text(u["events"]))
            print("      Why organize / ties: %r" % redact_text(u["why"]))
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
            print("  intake row %d (city %r): %s"
                  % (m["row"], redact_text(m["city"]), redact_text(m["why"])))

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


def build_findings(report, st, held=()):
    """Record the proposal on a findings.Report (step `chapters`). Pure; no I/O.

    The same numbers print_report prints, as data for the run page. `held` is
    the rows partition_new_rows kept back under `--require-luma` (empty in
    report mode and by default). Subjects are cities or intake row numbers,
    never addresses; names go through the same redaction as stdout, so
    `--redact` governs this file too. Free text from the form never lands here:
    a malformed row is reported by row number and the reason's KIND, not the
    offending value (bad_public_text's message repr's it).
    """
    adds, new_rows, near_misses = st.adds, st.new_rows, st.near_misses
    cities = len({fold_city(e["city"]) for e in st.entries})
    report.summary = ("%d qualifying organizers across %d cities; %d add(s), %d new "
                      "row(s), %d near-miss(es); %d unresolved"
                      % (len(st.entries), cities, len(adds), len(new_rows),
                         len(near_misses), len(st.unresolved)))
    report.measure("qualifying organizers", len(st.entries))
    report.measure("cities", cities)
    report.measure("chapter rows", len(st.chapters))
    report.measure("adds", len(adds), "warn" if adds else "ok")
    report.measure("new rows", len(new_rows), "warn" if new_rows else "ok")
    report.measure("near-misses", len(near_misses), "warn" if near_misses else None)
    report.measure("unresolved", len(st.unresolved), "warn" if st.unresolved else None)
    held_cities = {fold_city(h["city"]) for h in held}
    for a in adds:
        report.find("add", a["city"], "row %d: + %s" % (a["row"], "; ".join(map(redact_name, a["names"]))),
                    severity="warn", action="apply with --write")
    for n in new_rows:
        is_held = fold_city(n["city"]) in held_cities
        luma = n.get("luma")
        detail = "row %d: %s — https://luma.com/aaif-%s%s" % (
            n["row"], "; ".join(map(redact_name, n["names"])), n["slug"],
            " (Luma page %s)" % luma if luma else "")
        if is_held:
            report.find("held row", n["city"], detail, severity="warn",
                        action="create the Luma page and re-run")
        else:
            report.find("new row", n["city"], detail, severity="warn",
                        action="apply with --write, then fill the editorial columns")
    for m in near_misses:
        report.find("near-miss", m["city"],
                    "%s ~ %s" % ("; ".join(map(redact_name, m["names"])),
                                 ", ".join("%s (row %d)" % c for c in m["candidates"])),
                    severity="warn", action="confirm the row or fix the intake city")
    for m in st.malformed:
        kind = "too long" if "characters (max" in m["why"] else "control characters or markup"
        report.find("malformed text", "intake row %d" % m["row"],
                    "excluded from every write: %s" % kind,
                    severity="bad", action="fix the intake row")
    return report

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

def partition_new_rows(new_rows, last_row, require_luma=False):
    """Split proposed new rows into (write, held) and renumber the written ones.

    **Under `--require-luma`**, a city whose Luma page is not live is held back
    — its CTA would point at a 404 — but it must not block the rows and adds
    that ARE ready: page creation is manual and can lag a decision by weeks, so
    one pending city used to freeze every other chapter's sync. Under that flag
    "unknown" is held too: a page we could not verify gets a human look, not a
    published button. **By default the gate is off and every row is written** —
    see the paragraph below.

    The written rows are renumbered onto consecutive rows after last_row, so a
    held city never leaves a blank row in the middle of the feed. Rows are
    copied, not mutated — the caller's proposal still describes what the report
    showed.

    The Luma gate is OPT-IN (`--require-luma`); by default every row is written.
    It was the other way round until 2026-09-17, when the policy changed
    (user-decided): a chapter's Luma page is made by hand and can follow the row,
    and holding the row helped nobody — a new row is not site-ready regardless
    until every EDITORIAL_COLUMNS cell is filled in by a human, so the dead
    CTA was never what kept the page off the site. What the gate did do was
    re-propose the city on every run, which is the visibility that
    `--audit-luma` now provides for the whole feed rather than only for rows
    this engine happens to be adding today.
    """
    if not require_luma:
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
    ap.add_argument("--require-luma", action="store_true",
                    help="hold back a new row whose Luma page is not live yet "
                         "(default: write it; the page is made by hand and can "
                         "follow the row)")
    ap.add_argument("--allow-missing-luma", action="store_true",
                    help="deprecated and inert — writing without a live Luma page "
                         "is now the default; see --require-luma")
    ap.add_argument("--audit-luma", action="store_true",
                    help="also check EVERY existing feed row's Chapter Luma Link "
                         "and report the dead ones (one request per row; slow)")
    add_redact_flag(ap, masks="names (first initial) and free-text answers")
    findings.add_flag(ap)
    a = ap.parse_args()
    set_redaction(a.redact)

    # One flag, two steps: the runner calls this script once for the sync
    # (`chapters`) and once more with --audit-luma (`luma`), and each call's
    # JSON is that step's report, not both.
    step = "luma" if a.audit_luma else "chapters"
    report = findings.Report(step, "write" if a.write else "report")
    held = []

    def done(code):
        """Every NORMAL exit (0 or 2) lands the JSON beside the log first. Abort
        paths sys.exit around this on purpose: a run that failed has no
        state-of-the-estate to report, and the log says why."""
        if step == "chapters":
            build_findings(report, state, held)
        report.write(a.json_out)
        return code

    # --write recomputes from a fresh read here — a stale proposal is never applied.
    state = compute()
    print_report(state)
    if a.allow_missing_luma:
        print("NOTE: --allow-missing-luma is inert — writing without a live Luma "
              "page is the default now; see --require-luma.", file=sys.stderr)
    dead_luma = audit_luma(report) if a.audit_luma else 0
    drift = bool(state.adds or state.new_rows)
    if not a.write:
        # Exit convention (shared by all five engines, consumed by nightly.py):
        # report mode exits 0 when in sync, 2 when it proposes changes. A dead
        # Luma link is drift too when it was asked about: the row is on the site
        # with a button that 404s, which is pending work whoever writes it.
        return done(2 if (drift or dead_luma) else 0)
    if not drift:
        return done(0)

    # The cap's second enforcement point. create_chapter.py guards the Drive
    # folder; this guards the feed row. Checked here rather than in report mode
    # on purpose — seeing WHICH cities are queued is how an operator decides
    # what to retire, and refusing the report would remove the tool they need to
    # comply. Counted against the estate as it stands plus the rows this run
    # would append, so a run that would cross the line is refused before it
    # writes any of them rather than half way through.
    if state.new_rows:
        live, _retired, by_status = census_of(state.chapters)
        msg = cap_refusal(live, by_status,
                          "append %d new chapter row(s)" % len(state.new_rows),
                          incoming=len(state.new_rows))
        if msg:
            sys.exit(msg)

    # Luma page creation is manual, so "absent" is the NORMAL state for a net-new
    # city — without this gate the common path publishes a "Stay Updated" button
    # pointing at a 404, and the only warning is one line of per-city output.
    # print_report already stamped n["luma"]; the loop below keeps the partition
    # correct even if a future path reaches here without printing the report,
    # where a missing key would silently hold every row.
    # Only the gate consumes this, so only fetch it when the gate is armed.
    # Without the guard a default write run spent one 15s-timeout request per
    # new city computing a value nothing reads.
    for n in state.new_rows:
        if a.require_luma and "luma" not in n:
            n["luma"] = luma_status(n["slug"])
    to_write, held = partition_new_rows(state.new_rows, state.last_row,
                                        a.require_luma)
    if held:
        print("\nHELD BACK %d new row(s) with no live Luma page (their CTA would "
              "point at a 404): %s.\nCreate the page(s) and re-run, or drop "
              "--require-luma to write them anyway."
              % (len(held), ", ".join(n["city"] for n in held)))
    if not state.adds and not to_write:
        print("Nothing else to write — every proposed change is held back.")
        return done(2)

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
    # was held; by default nothing is, and the verify stays strict.)
    held_cities = {fold_city(h["city"]) for h in held}
    still_held, leftover_rows = [], []
    for x in after.new_rows:
        # `== "absent"` , not `!= "live"`. Swallowing "unknown" meant a row
        # re-proposed because the WRITE FAILED was filed as an expected hold —
        # the run printed "Verified" over a city that never reached the sheet.
        # Only a page we positively know is missing proves this run could not
        # have written that row.
        if fold_city(x["city"]) in held_cities or \
                (held and luma_status(x["slug"]) == "absent"):
            still_held.append(x)
        else:
            leftover_rows.append(x)
    if after.adds or leftover_rows:
        print("VERIFY FAILED — still out of sync after write:")
        for x in after.adds:
            print("  row %d %s: + %s" % (x["row"], x["city"], "; ".join(map(redact_name, x["names"]))))
        for x in leftover_rows:
            print("  new row %s: %s" % (x["city"], "; ".join(map(redact_name, x["names"]))))
        sys.exit(1)
    if held:
        # Exit 2, the shared drift code: the held rows are still pending work,
        # and a wrapper (nightly.py) must keep seeing them until the pages exist.
        # Count what the re-read actually proposes, not len(held) — a held row
        # deleted from the intake during the run would overstate pending work.
        print("Verified: a fresh run proposes only held-back row(s) "
              "(%d still pending)." % len(still_held))
        report.written = True
        return done(2)
    print("Verified: a fresh run proposes zero changes.")
    report.written = True
    # A --write run that was ALSO asked to audit must report what the audit
    # found. dead_luma used to be computed here and consumed only on the
    # report-mode branch, so `--write --audit-luma` printed dead links and then
    # exited 0.
    return done(2 if dead_luma else 0)

if __name__ == "__main__":
    sys.exit(main())
