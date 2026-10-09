#!/usr/bin/env python3
"""Unit tests for migrate_intake_role_gates.py (no network/gws).

The fixtures are the live formulas' shapes, trimmed to the clauses that matter:
headers only, no row data. main() runs against a COUNTED fake of the sheet, so
"report mode writes nothing" is asserted by the writes that happened, not by
how a call was spelled.
"""
import contextlib, io, os, sys

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
COLLAB = ('=LET(hdr,\'Form Responses\'!$A$1:$CT$1,data,\'Form Responses\'!$A$2:$CT,'
          'gate,CHOOSECOLS(data,MATCH("%s",hdr,0)),keep,FILTER(data,gate="Yes"),'
          'IFERROR(CHOOSECOLS(keep,MATCH("Timestamp",hdr,0)),""))' % mig.COLLABORATE_Q)
BY_TAB = {t.tab: t for t in mig.TARGETS}

def live_shape(t):
    return {"keep": ROLE_TAB % (t.word or ""), "mask": BY_CITY, "gated": COLLAB}[t.kind]


# ---------------------------------------------------------------------------
# rewrite(): the two edits
# ---------------------------------------------------------------------------
for t in mig.TARGETS:
    before = live_shape(t)
    after = mig.rewrite(before, t)
    check("%s: wrapped in the missing-column guard" % t.tab,
          after.startswith(mig.guard(t.question)) and after.endswith("))"), True)
    if t.kind != "gated":
        check("%s: the old test survives, OR'd with the question" % t.tab,
              ('ISNUMBER(SEARCH("%s",brand))+(gate="Yes"))>0' % t.word) in after, True)
        unwrapped = "=" + after[len(mig.guard(t.question)):-1]
        old, new = mig.clauses(t)
        check("%s: nothing outside the clause and the guard changes" % t.tab,
              unwrapped.replace(new, old), before)
    check("%s: a second run is a no-op" % t.tab, mig.rewrite(after, t), None)

def raises(fn, exc=mig.ShapeError):
    try:
        fn()
    except exc:
        return True
    return False

org, spk = BY_TAB["Organizers"], BY_TAB["Speakers"]
check("a formula without the clause stops, never guesses",
      raises(lambda: mig.rewrite("=FILTER(A:A,B:B)", org)), True)
check("the wrong role's formula stops (Hosts' formula is not the Speakers tab)",
      raises(lambda: mig.rewrite(ROLE_TAB % "venue", spk)), True)
check("a formula already using the LET name `gate` stops",
      raises(lambda: mig.rewrite((ROLE_TAB % "organizer").replace("IFERROR", "gate,1,IFERROR"), org)),
      True)
half = (ROLE_TAB % "organizer").replace(
    "keep,", 'gate,CHOOSECOLS(data,MATCH("%s",hdr,0)),keep,' % mig.ORGANIZE_Q, 1)
check("a half-done edit (question referenced, clause missing) is refused, not 'done'",
      raises(lambda: mig.rewrite(half, org)), True)
check("the guard alone, without the clause, is not 'done' either",
      mig.rewrite(mig.guard(mig.ORGANIZE_Q) + (ROLE_TAB % "organizer")[1:] + ")", org) is None,
      False)
check("an unknown kind is a programming error, not a mask",
      raises(lambda: mig.rewrite(BY_CITY, org._replace(kind="mkas")), ValueError), True)
check("the questions are imported from sync_crm, the one definition",
      (mig.ORGANIZE_Q, mig.SPEAK_Q, mig.VENUE_Q, mig.COLLABORATE_Q),
      ("Would you like to help organize local AAIF events?",
       "Would you like to speak at an AAIF event?",
       "Would you like to offer a venue for AAIF events?",
       "Would you like to collaborate/partner with us?"))
# clean.py must stay zippable on its own, so it carries its own copy; this is
# the test that keeps the two from drifting.
sys.path.insert(0, os.path.join(_HERE, "..", "..", "aaif-clean-data", "scripts"))
import clean  # noqa: E402
check("clean.py's copy of the organize question matches", clean.H_ORGANIZER_GATE, mig.ORGANIZE_Q)

# ---------------------------------------------------------------------------
# expected_rows() and the marketing-column order
# ---------------------------------------------------------------------------
HDR = (["Timestamp", "Email", "What brings you here?"] + list(mig.COLLAB_BLOCK[:2])
       + [mig.MARKETING_Q] + list(mig.COLLAB_BLOCK[2:])
       + [mig.ORGANIZE_Q, mig.SPEAK_Q, mig.VENUE_Q, mig.COLLABORATE_Q, mig.MARKETING_Q])
Q = {q: HDR.index(q) for q in (mig.ORGANIZE_Q, mig.SPEAK_Q, mig.VENUE_Q, mig.COLLABORATE_Q)}
def row(brand="", **yes):
    r = [""] * len(HDR)
    r[0], r[1], r[2] = "t", "a@x.com", brand
    for q in yes.get("y", ()):
        r[Q[q]] = "Yes"
    return r
ROWS = [row("I want to be an organizer/volunteer for the local chapter"),
        row("I want to be a speaker"),
        row(y=[mig.ORGANIZE_Q, mig.SPEAK_Q]),
        row(y=[mig.COLLABORATE_Q]),
        row()]
check("organizers: old answer + new Yes", mig.expected_rows(org, HDR, ROWS), 2)
check("speakers: old answer + new Yes", mig.expected_rows(spk, HDR, ROWS), 2)
check("collaborators: the question alone", mig.expected_rows(BY_TAB["Collaborators"], HDR, ROWS), 1)
check("Organizers by City is grouped, so it has no expected count",
      mig.expected_rows(BY_TAB["Organizers by City"], HDR, ROWS), None)
check("the first marketing column inside the Collaborate block passes",
      mig.marketing_in_collab_block(HDR), True)
swapped = [h for h in HDR]
first = swapped.index(mig.MARKETING_Q)
swapped[first] = "something else"
check("...and outside it is caught", mig.marketing_in_collab_block(swapped), False)

# ---------------------------------------------------------------------------
# main() against a counted fake sheet
# ---------------------------------------------------------------------------
class FakeSheet:
    def __init__(self, formulas, shown=None, errors=0, after_write=None):
        self.formulas = dict(formulas)
        self.shown = shown or {}          # tab -> list shown before any write
        self.after_write = after_write    # tab -> list shown after the write
        self.errors = errors
        self.writes = []

    def install(self):
        mig.read_form_responses = lambda: (HDR, ROWS)
        mig.read_formula = lambda tab, cell: self.formulas[tab]
        mig.tab_column = lambda t: ((self.after_write or {}).get(t.tab, self.shown.get(t.tab, []))
                                    if self.writes else self.shown.get(t.tab, []))
        mig.errors_on = lambda tab: self.errors
        def write(t, formula):
            self.writes.append(t.tab)
            self.formulas[t.tab] = formula
        mig.write_formula = write

def run(sheet, argv):
    saved = {n: getattr(mig, n) for n in
             ("read_form_responses", "read_formula", "tab_column", "errors_on", "write_formula")}
    sheet.install()
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = mig.main(argv)
    finally:
        for n, f in saved.items():
            setattr(mig, n, f)
    return code, out.getvalue()

LIVE = {t.tab: live_shape(t) for t in mig.TARGETS}
GOOD_AFTER = {"Organizers": ["t"] * 2, "Hosts": [], "Speakers": ["t"] * 2,
              "Organizers by City": ["Boston — 1", "t"], "Collaborators": ["t"]}
BEFORE = {"Organizers": ["t"], "Hosts": [], "Speakers": ["t"],
          "Organizers by City": ["Boston — 1", "t"], "Collaborators": []}

s = FakeSheet(LIVE, BEFORE)
code, out = run(s, [])
check("report mode: exit 0", code, 0)
check("report mode: NOTHING is written", s.writes, [])
check("report mode: says how many would change", "5 formula(s) would change" in out, True)
check("report mode: shows current vs expected rows", "shows 1, should show 2" in out, True)

broken = dict(LIVE, Speakers="=FILTER(A:A,B:B)")
s = FakeSheet(broken, BEFORE)
code, out = run(s, ["--write"])
check("a shape error on a later target: exit 2", code, 2)
check("...and nothing was written, not even the earlier targets", s.writes, [])

s = FakeSheet(LIVE, BEFORE, after_write=GOOD_AFTER)
code, out = run(s, ["--write"])
check("--write: every due formula written once", sorted(s.writes), sorted(LIVE))
check("--write: verified ok, exit 0", (code, "FAIL" in out), (0, False))

s2 = FakeSheet(s.formulas, GOOD_AFTER)
code, out = run(s2, ["--write"])
check("a second --write: nothing due, nothing written", (code, s2.writes, "Nothing due" in out),
      (0, [], True))

s = FakeSheet(LIVE, BEFORE, after_write=BEFORE)
code, out = run(s, ["--write"])
check("--write that recovers nobody FAILS (rows unchanged != expected)", code, 1)
check("...naming the tab", "FAIL Organizers!C2" in out, True)

guarded = dict(GOOD_AFTER, Organizers=[mig.GUARD_MESSAGE + mig.ORGANIZE_Q])
s = FakeSheet(LIVE, BEFORE, after_write=guarded)
check("--write where the guard message shows FAILS", run(s, ["--write"])[0], 1)

s = FakeSheet(LIVE, BEFORE, errors=1, after_write=GOOD_AFTER)
check("--write with a formula error on the tab FAILS", run(s, ["--write"])[0], 1)

mig_saved = mig.read_form_responses
code, out = run(FakeSheet(LIVE, BEFORE), [])  # sanity: restored after run()
check("run() restores the live I/O functions", mig.read_form_responses is mig_saved, True)

no_q = [h for h in HDR if h != mig.VENUE_Q]
s = FakeSheet(LIVE, BEFORE)
s.install = (lambda orig: (lambda: (orig(), setattr(mig, "read_form_responses",
                                                    lambda: (no_q, ROWS)))))(s.install)
code, out = run(s, ["--write"])
check("a missing question column stops before any write", (code, s.writes), (2, []))

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
