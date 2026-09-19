#!/usr/bin/env python3
"""The 100-chapter cap and the Status census. Plain script; exit 1 on failure."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "lib"))

import sync_chapters as sc  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))
    print("%s %s" % ("ok  " if got == want else "FAIL", label))


def census(pairs):
    """census_of over read_chapters()-shaped dicts — the only census path.

    The raw-grid variant this used to exercise was a SECOND spelling of the
    row -> dict extraction read_chapters() already does, kept alive by these
    tests alone. It was deleted rather than tested.
    """
    return sc.census_of([{"city": c, "status": s} for c, s in pairs])


# --- blank counts as LIVE. The alternative lets the estate grow past the cap
# simply by nobody filling the column in. -------------------------------------
live, retired, by = census([("Boston", ""), ("Madrid", "Active")])
check("a blank Status counts toward the cap", live, 2)
check("nothing is retired by a blank", retired, 0)
check("blanks are reported under the empty key", by.get(""), 1)

# --- only Merged/Deprecated retire -------------------------------------------
live, retired, _ = census([("A", "Active"), ("B", "Provisioned"), ("C", "Dormant"),
                           ("D", "Merged"), ("E", "Deprecated")])
check("Merged and Deprecated stop counting", live, 3)
check("...and are counted as retired", retired, 2)
check("Dormant still counts as live — it is a review queue, not a decision",
      "Dormant" in sc.RETIRED_STATUSES, False)

# --- rows with no City are skipped (never append into a gap) -----------------
live, _, _ = census([("Boston", "Active"), ("", ""), ("Madrid", "")])
check("a blank City row is not a chapter", live, 2)

# --- the refusal ------------------------------------------------------------
check("under the cap there is no refusal",
      sc.cap_refusal(sc.CHAPTER_CAP - 1, {}, "create a new chapter"), None)
check("incoming defaults to 1 — the question is always 'may I add?'",
      sc.cap_refusal(sc.CHAPTER_CAP, {}, "x") is not None, True)
msg = sc.cap_refusal(sc.CHAPTER_CAP, {"": 61}, "create the 'Nowhere' chapter folder")
check("at the cap it refuses", msg.startswith("ABORT:"), True)
check("the refusal names what it refused",
      "create the 'Nowhere' chapter folder" in msg, True)
check("the refusal offers coalescing as well as retiring",
      "Merged" in msg and "Deprecated" in msg, True)
check("the refusal points at the untriaged backlog, not just a wall",
      "61 chapter(s) have a BLANK Status" in msg, True)
check("...and names the report that ranks them", "chapter_health.py" in msg, True)
# Over the cap must refuse too: an estate that drifted past it is exactly when
# a `== CHAPTER_CAP` test would silently let the next one through.
check("OVER the cap also refuses",
      sc.cap_refusal(sc.CHAPTER_CAP + 5, {}, "x").startswith("ABORT:"), True)

# --- the cap is defined once -------------------------------------------------
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-create-chapter", "scripts"))
import create_chapter as cc  # noqa: E402
# MODULE identity, not value identity. `cc._chapters.CHAPTER_CAP is
# sc.CHAPTER_CAP` passes even between two independently-defined copies, because
# 100 is a cached small int — the assertion could not fail, so it proved nothing
# about the duplication its label names.
check("create_chapter imports the very module that defines the cap",
      cc._chapters is sc, True)
check("the status vocabulary is the same object, not an equal copy",
      cc._chapters.CHAPTER_STATUSES is sc.CHAPTER_STATUSES, True)

# --- a sheet from before the migration must still read ----------------------
# read_chapters() fills "status" with "" when the column is absent, so the
# census sees the same shape either way.
live, retired, by = sc.census_of([{"city": "Boston"}, {"city": "Madrid"}])
check("chapters with no Status key at all still census", (live, retired), (2, 0))
check("...and report as untriaged", by.get(""), 2)

# --- `incoming` is a separate parameter, because it used to be smuggled ------
# The feed-row guard passed live+new as `live` and restated the breakdown inside
# `what`, so the headline printed "N live chapters" for a number that was not.
check("live + incoming under the cap is allowed",
      sc.cap_refusal(98, {}, "append", incoming=2), None)
check("exactly at the cap with nothing incoming is allowed",
      sc.cap_refusal(sc.CHAPTER_CAP, {}, "x", incoming=0), None)
over = sc.cap_refusal(98, {}, "append 3 new chapter row(s)", incoming=3)
check("live + incoming OVER the cap refuses", over.startswith("ABORT:"), True)
check("the headline separates live from incoming rather than summing silently",
      "98 live chapters + 3 new = 101" in over, True)
check("a single-chapter refusal keeps the plain headline",
      sc.cap_refusal(100, {}, "x").splitlines()[0].startswith("ABORT: 100 live chapters —"),
      True)

# --- an out-of-vocabulary Status counts as live AND is surfaced -------------
# The column is hand-edited and its dropdown is advisory, so `merged` (lower
# case) or `Archived` are typeable. Folding them silently into the live count
# is how the cap's counting rule becomes a spelling contest.
live, retired, by = census([("A", "Active"), ("B", "merged"), ("C", "Archived"),
                            ("D", "merged"), ("E", "merged")])
check("an unrecognised Status still counts as live", live, 5)
check("...and retires nothing", retired, 0)
# Unequal counts on purpose: with 1 and 1, alphabetical and count order coincide
# and this assertion passed under either implementation.
check("unknown_statuses is worst (most rows) first, not alphabetical",
      sc.unknown_statuses(by), [("merged", 3), ("Archived", 1)])
check("the vocabulary itself is not reported as unknown",
      sc.unknown_statuses({"Active": 5, "Merged": 1, "": 3}), [])
odd = sc.cap_refusal(100, {"Archived": 2}, "x")
check("the refusal warns about out-of-vocabulary values",
      "not in the vocabulary" in odd and "'Archived' (2)" in odd, True)

# --- census_of is the single counting rule both guards use -------------------
chaps = [{"city": "A", "status": "Active"}, {"city": "B", "status": "Merged"},
         {"city": "C", "status": ""}, {"city": "", "status": "Active"}]
check("census_of skips rows with no City", sc.census_of(chaps)[0], 2)
check("census_of retires Merged", sc.census_of(chaps)[1], 1)
# Whitespace in a hand-typed dropdown cell must not read as a sixth status.
check("a padded Status cell is still recognised",
      sc.census_of([{"city": "A", "status": " Merged "}])[1], 1)


if FAILS:
    print("\nFAIL (%d)" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("\nchapter_cap: all checks passed")
