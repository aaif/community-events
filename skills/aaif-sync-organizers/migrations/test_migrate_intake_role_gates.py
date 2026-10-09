#!/usr/bin/env python3
"""Unit tests for migrate_intake_role_gates.py (no network/gws).

The fixtures are the live formulas' shapes, trimmed to the clauses that matter:
headers only, no row data.
"""
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import migrate_intake_role_gates as mig

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        print("      got : %r\n      want: %r" % (got, want))


ROLE_TAB = ('=LET(hdr,\'Form Responses\'!$A$1:$CT$1,data,\'Form Responses\'!$A$2:$CT,'
            'brand,CHOOSECOLS(data,MATCH("What brings you here?",hdr,0)),'
            'keep,FILTER(data,ISNUMBER(SEARCH("%s",brand))),'
            'IFERROR(CHOOSECOLS(keep,MATCH("Timestamp",hdr,0)),""))')
BY_CITY = ('=LET(hdr,\'Form Responses\'!$A$1:$CT$1,fr,\'Form Responses\'!$A$2:$CT,'
           'brand,CHOOSECOLS(fr,MATCH("What brings you here?",hdr,0)),'
           'mask,ARRAYFORMULA(ISNUMBER(SEARCH("organizer",brand))),keep,FILTER(fr,mask),'
           'ord,SORT(keep,1,FALSE),ord)')

for tab, cell, src, word, question, kind in mig.TARGETS:
    before = BY_CITY if kind == "mask" else ROLE_TAB % word
    after = mig.rewrite(before, src, word, question, kind)
    check("%s: the old test survives, OR'd with the question" % tab,
          ('ISNUMBER(SEARCH("%s",brand))+(gate="Yes"))>0' % word) in after, True)
    check("%s: the question is read from the same data range" % tab,
          'gate,CHOOSECOLS(%s,MATCH("%s",hdr,0)),' % (src, question) in after, True)
    check("%s: nothing outside the clause changes" % tab,
          after.replace(mig.clauses(src, word, question, kind)[1],
                        mig.clauses(src, word, question, kind)[0]), before)
    check("%s: a second run is a no-op" % tab,
          mig.rewrite(after, src, word, question, kind), None)

check("no IFNA: a renamed question must fail loudly",
      "IFNA" in mig.rewrite(ROLE_TAB % "organizer", "data", "organizer", mig.ORGANIZE, "keep"),
      False)

def raises(fn):
    try:
        fn()
    except mig.ShapeError:
        return True
    return False

check("a formula without the clause stops, never guesses",
      raises(lambda: mig.rewrite("=FILTER(A:A,B:B)", "data", "organizer", mig.ORGANIZE, "keep")),
      True)
check("the wrong role's formula stops (Hosts' formula is not the Speakers tab)",
      raises(lambda: mig.rewrite(ROLE_TAB % "venue", "data", "speaker", mig.SPEAK, "keep")),
      True)
check("a formula already using the LET name `gate` stops",
      raises(lambda: mig.rewrite((ROLE_TAB % "organizer").replace("IFERROR", "gate,1,IFERROR"),
                                 "data", "organizer", mig.ORGANIZE, "keep")),
      True)
check("the questions are the form's exact titles (a rename renames the column)",
      (mig.ORGANIZE, mig.SPEAK, mig.VENUE),
      ("Would you like to help organize local AAIF events?",
       "Would you like to speak at an AAIF event?",
       "Would you like to offer a venue for AAIF events?"))

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
