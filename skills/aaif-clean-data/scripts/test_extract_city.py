#!/usr/bin/env python3
"""Unit tests for the Other -> Extracted city logic in clean.py (no network).

Every case below is a real answer the public form received, or the shape of one.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clean import (ALIASES, CAPITALS, extract_city, fold_city, is_placeholder,
                   _strip_country)

# The chapters that exist, as extract_city() receives them.
KNOWN = {fold_city(c): c for c in [
    "Bengaluru", "Chennai", "Delhi NCR", "Dubai", "Hyderabad", "Jaipur", "London",
    "Luxembourg", "Madison, WI", "Melbourne", "Mumbai", "New York", "Paris", "Pune",
    "San Francisco", "Singapore", "Tokyo", "Toronto", "Vancouver", "Washington DC"]}

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))

def city(other):
    return extract_city(other, KNOWN)[0]

# --- 1. the whole answer is a chapter ----------------------------------------
# Both of these broke the naive "take the text before the comma" version: the
# chapter's own name CONTAINS the comma.
check("'Madison, WI' stays whole", city("Madison, WI"), "Madison, WI")
check("'Washington, DC' folds onto the chapter", city("Washington, DC"), "Washington DC")
check("exact chapter name", city("Bengaluru"), "Bengaluru")
check("case and spacing are folded", city("  bengaluru "), "Bengaluru")

# --- 2. a segment is a chapter; a named city always beats a country ----------
check("'UAE, Dubai' -> the city, not the capital", city("UAE, Dubai"), "Dubai")
check("'Hyderabad, India'", city("Hyderabad, India"), "Hyderabad")
check("'Colombo , Sri lanka' spacing", city("Colombo , Sri lanka"), "Colombo")
# The human picked Jaipur out of "Udaipur , Jaipur" because Jaipur is the chapter.
check("'Udaipur , Jaipur' -> the one that is a chapter", city("Udaipur , Jaipur"), "Jaipur")

# --- 3. a chapter named anywhere in a sentence -------------------------------
check("sentence answer", city("I am in Paris, France and Toronto"), "Paris")
check("'Vancouver BC, Canada'", city("Vancouver BC, Canada"), "Vancouver")
check("slash-separated", city("Delhi NCR, Bangalore"), "Delhi NCR")
# Longest match wins, so "Delhi" never beats the "Delhi NCR" chapter.
check("longest chapter name wins", city("delhi ncr"), "Delhi NCR")
check("multi-city answers are flagged ambiguous",
      extract_city("Gujarat, India + Bengaluru/Mumbai", KNOWN)[2], True)
check("...and still resolve to one", city("Gujarat, India + Bengaluru/Mumbai"), "Bengaluru")

# --- 4. a country alone -> its capital ---------------------------------------
check("country only -> capital", city("Bulgaria"), "Sofia")
# The capital must go through the same canonicalization as a typed city:
# CAPITALS says India -> "New Delhi", but the chapter is "Delhi NCR" — without
# the alias pass, "India" and "New Delhi" land the same person in different
# chapters.
check("country only -> capital -> alias -> the chapter",
      city("India"), "Delhi NCR")
check("country only, cased", city("  NIGERIA "), "Abuja")
check("country whose capital IS a chapter", city("Japan"), "Tokyo")
check("the capital rule says so", extract_city("Bulgaria", KNOWN)[1],
      "only a country (Bulgaria) — using its capital")
# A city alongside the country must never reach the capital rule.
check("country + city never yields the capital", city("India, Gurugram"), "Gurugram")

# --- 5. fallback: the first city-like segment --------------------------------
check("trailing country stripped", city("Noida India"), "Noida")
check("leading country stripped", city("India, Gurugram"), "Gurugram")
check("two-word country stripped", city("Kandy Sri Lanka"), "Kandy")
check("unknown city kept as typed", city("Stuttgart"), "Stuttgart")
check("noise phrase removed", city("I am based in Stuttgart"), "Stuttgart")
check("a typo is preserved, not guessed at", city("Monterreyy Mexico"), "Monterreyy")
check("empty answer", extract_city("", KNOWN), ("", "no free text", False))
check("punctuation-only answer", city("--"), "")

# _strip_country must never empty a segment that is only a country, or the
# capital rule below it never fires.
check("_strip_country leaves a bare country alone", _strip_country("India"), "India")
check("_strip_country needs a survivor", _strip_country("Sri Lanka"), "Sri Lanka")

# --- aliases ------------------------------------------------------------------
check("'Bangalore' reaches the Bengaluru chapter", city("Bangalore"), "Bengaluru")
check("'NYC' reaches New York", city("Nyc"), "New York")
check("'DC' reaches Washington DC", city("DC"), "Washington DC")
# An alias whose target is not a chapter still normalises the spelling.
check("alias with no chapter yet", extract_city("Gurgaon", KNOWN)[0], "Gurugram")

# --- is_placeholder -----------------------------------------------------------
check("plain Other", is_placeholder("Other"), True)
check("the long Other wording",
      is_placeholder("Other (PLEASE TELL US WHERE IN NEXT QUESTION)"), True)
check("empty dropdown", is_placeholder(""), True)
check("a real city is not a placeholder", is_placeholder("Bengaluru"), False)

# --- fold_city parity with sync_chapters -------------------------------------
# The two skills cannot import each other, so the duplication is asserted here:
# a city that folds differently in the two would resolve someone into a chapter
# the sync engines cannot find.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "aaif-sync-chapters", "scripts"))
try:
    from sync_chapters import fold_city as sync_fold
except ImportError:
    print("ok   (sync_chapters not importable here — parity check skipped)")
else:
    for s in ["Washington, DC", "Madison, WI", "Montréal", "Delhi NCR", "  São Paulo ",
              "東京", "Logroño", "St. Louis", "Other (PLEASE TELL US WHERE)"]:
        check("fold parity %r" % s, fold_city(s), sync_fold(s))

# --- capitals table sanity ----------------------------------------------------
check("no country maps to an empty capital",
      [k for k, v in CAPITALS.items() if not v.strip()], [])
check("aliases never point at a country",
      [k for k in ALIASES if k in CAPITALS], [])

print("\n%s (%d failure(s))" % ("ALL PASS" if not fails else "FAILURES", fails))
sys.exit(1 if fails else 0)
