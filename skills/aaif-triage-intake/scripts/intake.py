#!/usr/bin/env python3
"""Pull the AAIF intake queue (Organizers / Hosts / Speakers) from the
"AAIF Community Intake Ops" sheet and print the rows that need review.

Reads everything by *header name* (never column letter), matching the sheet's
name-based extraction design, so it survives column reordering.

Usage:
    intake.py                 # text digest of rows needing attention
    intake.py --json          # same selection as JSON (for the digest routine)
    intake.py --all           # every row, regardless of status
    intake.py --status Prospect "In progress"   # custom status filter
"""
import argparse, json, subprocess, sys

SHEET_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"

# Per-tab: the header names to surface in the digest (resolved by name).
# Name / Email / LinkedIn / City (Existing) / City (New) are shown for every tab;
# these add the distinctive, decision-relevant fields per applicant type.
TABS = {
    "Organizers": ["Full name", "Email", "LinkedIn", "City (Existing)", "City (New)",
                   "Chapter / city wanted", "Technical expertise",
                   "Run events before?", "Why organize / ties"],
    "Hosts":      ["Name", "Email", "LinkedIn", "City (Existing)", "City (New)", "Company",
                   "Venue name", "Capacity", "Holds 30+?", "A/V available?"],
    "Speakers":   ["Name", "Email", "LinkedIn", "City (Existing)", "City (New)", "Headline",
                   "Talk title", "Ships in production?", "Past talks / portfolio"],
}

# Rows in these Status states are "awaiting review". A blank Status IS
# "Prospect" (the form writes none), and so is the legacy value "New" — the
# pre-2026-08-22 name for the same state ("New" misread as new-organizer;
# "Prospect" matches what sync_crm already writes). Both are normalized in
# collect() via normalize_status(), so filters match on "Prospect" alone — a
# custom --status list never needs to know about blanks or legacy cells.
DEFAULT_NEEDS_REVIEW = {"Prospect", "In progress"}


def normalize_status(value):
    """One normalization for Status cells AND --status filter values: blank and
    the legacy "New" are both "Prospect". Cells still saying "New" exist until
    migrate_status_prospect.py has rewritten every sheet; a row holding it must
    behave identically to one holding "Prospect"."""
    v = (value or "").strip()
    return "Prospect" if v in ("", "New") else v


def normalize_filter(values):
    """Normalize a --status list the same way collect() normalizes cells: a
    requested blank (or legacy "New") means the blank/"New"-status rows — which
    the rows themselves report as "Prospect" by then, so an un-normalized
    --status "" or --status New would silently select zero rows."""
    return {normalize_status(v) for v in values}

# The City columns were renamed to City (Existing)/City (New). They only carry
# those headers after `aaif-clean-data install-colors` has run; until then the
# role tabs still show the legacy City/Resolved City. Fall back so the digest
# degrades to the old value instead of silently blanking every applicant's city.
LEGACY_ALIASES = {"City (Existing)": "City", "City (New)": "Resolved City"}


def fetch(tab):
    """Return (headers, rows) for a tab; rows are padded to len(headers)."""
    # The range is the bare tab name — deliberately NOT a bounded window: a
    # hardcoded "A1:BB" silently drops any column added past the bound, and the
    # column it drops first is the newest one (this is the same anti-pattern
    # clean.py's read_tab removed from this very spreadsheet). Sheets trims
    # trailing empty rows/columns. A bare title also needs no quoting, unlike an
    # "A1"-style range where a name with spaces must be quoted before the "!".
    params = json.dumps({"spreadsheetId": SHEET_ID,
                         "range": tab, "majorDimension": "ROWS"})
    out = subprocess.run(["gws", "sheets", "spreadsheets", "values", "get",
                          "--params", params, "--format", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gws error reading {tab}: {out.stderr.strip()}")
    # gws prints a keyring banner line before the JSON; find the JSON start.
    txt = out.stdout
    start = txt.find("{")
    if start < 0:
        sys.exit(f"gws returned no JSON for {tab} (got: {txt.strip()[:200]!r})")
    try:
        data = json.loads(txt[start:])
    except json.JSONDecodeError as e:
        sys.exit(f"gws returned invalid JSON for {tab}: {e}")
    vals = data.get("values", [])
    if not vals:
        return [], []
    headers = [h.strip() for h in vals[0]]
    rows = [r + [""] * (len(headers) - len(r)) for r in vals[1:]]
    return headers, rows


def col(headers, name):
    return headers.index(name) if name in headers else None


def collect(status_filter, show_all):
    result = {}
    for tab, fields in TABS.items():
        headers, rows = fetch(tab)
        if not headers:
            result[tab] = []
            continue
        si = col(headers, "Status")
        ti = col(headers, "Timestamp")  # real-row marker (always present from the form)
        # A missing marker/status column means a header rename, not an empty
        # queue — fail loudly rather than silently reporting "0 awaiting review".
        if ti is None:
            sys.exit(f"ABORT: tab {tab!r} has no 'Timestamp' column; headers present: {headers}")
        if si is None and not show_all:
            sys.exit(f"ABORT: tab {tab!r} has no 'Status' column to filter on; "
                     f"pass --all or fix the header. Headers present: {headers}")
        picked = []
        for rn, row in enumerate(rows, start=2):  # row 2 = first data row
            if not (row[ti] or "").strip():
                continue  # skip empty trailing rows (no Timestamp)
            # Blank IS "Prospect" (and legacy "New" is too) — normalized once
            # here, so the filter below and the reported status can never
            # disagree about what a blank or legacy cell means.
            status = normalize_status(row[si] if si is not None else "")
            if not show_all and status not in status_filter:
                continue
            rec = {"row": rn, "status": status}
            for f in fields:
                ci = col(headers, f)
                if ci is None and f in LEGACY_ALIASES:
                    ci = col(headers, LEGACY_ALIASES[f])
                rec[f] = (row[ci].strip() if ci is not None else "")
            picked.append(rec)
        result[tab] = picked
    return result


def truncate(s, n=70):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def text_digest(data, label="awaiting review"):
    """`label` names the population actually selected — under --all or a custom
    --status filter, "awaiting review" would misdescribe every count printed."""
    total = sum(len(v) for v in data.values())
    counts = " · ".join(f"{len(v)} {t.lower()}" for t, v in data.items())
    print(f"AAIF intake — {total} {label} ({counts})\n")
    for tab, recs in data.items():
        if not recs:
            continue
        print(f"== {tab} ({len(recs)}) ==")
        for r in recs:
            name = r.get("Full name") or r.get("Name") or "(no name)"
            print(f"  • [{r['status']}] {name} — {r.get('Email','')}"
                  f"  {(r.get('City (New)') or r.get('City (Existing)', ''))}  (row {r['row']})")
            for f, v in r.items():
                if f in ("row", "status", "Full name", "Name", "Email",
                         "City (Existing)", "City (New)"):
                    continue
                if v:
                    print(f"      {f}: {truncate(v)}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--status", nargs="*", default=None,
                    help="Status values to include (default: Prospect/In progress; "
                         "blank counts as Prospect)")
    args = ap.parse_args()
    sf = normalize_filter(args.status) if args.status is not None else DEFAULT_NEEDS_REVIEW
    data = collect(sf, args.all)
    if args.all:
        label = "row(s), all statuses"
    elif args.status is not None:
        label = "with status " + " / ".join(sorted(sf))
    else:
        label = "awaiting review"
    if args.json:
        print(json.dumps(data, indent=1))
    else:
        text_digest(data, label)


if __name__ == "__main__":
    main()
