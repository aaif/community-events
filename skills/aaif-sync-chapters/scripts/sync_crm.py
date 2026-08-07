#!/usr/bin/env python3
"""Sync intake people (organizers / hosts / speakers) into each chapter's
Attendee CRM workbook, carrying their survey answers across.

Companion engine to sync_chapters.py: that one pushes accepted organizer *names*
onto the public chapters feed, this one pushes accepted *people and their stated
interest* into the private per-chapter CRM. Same house rules — the intake sheet
is only ever READ, the report is the default, and --write re-verifies itself.

Only Accepted / Existing (from MLOps) people sync, across all three role tabs
(see SYNC_STATUSES). The CRM is the onboarding list that decides who gets access
to a chapter folder, so a person reaches it after a decision, not on submitting
the form.

Each chapter folder under the Chapters Drive holds one "<City> CRM.xlsx" whose
"Attendees" tab has eleven columns. Only six are ever written (CRM_WRITTEN) —
identity, decision and interest, and nothing else:

    Full name           <- role tab name
    Trusted/Regular     <- "Yes" for an organizer (they're on the team)
    Status              <- Organizer / Speaker / Host
    Notes (CRM)         <- provenance: role, intake status, date
    Email               <- role tab email        (also the dedupe key)
    What brings you here? <- the survey answer verbatim, + talk/venue/city detail

Deliberately NOT written: Signal, LinkedIn URL, Company, Role / title, Technical
expertise. They exist on the sheet for an organizer to fill in by hand; the
automation does not push a survey's worth of personal detail into the folder.

The workbooks are stored .xlsx (not native Sheets), so they are edited as OOXML
zip parts: download, rewrite the Attendees sheet XML, upload. Every part we do
not touch is repacked byte-for-byte.

Usage:
  python3 sync_crm.py                    # report + proposed changes, writes nothing
  python3 sync_crm.py --city Boston      # scope the report to one chapter
  python3 sync_crm.py --write            # apply, then re-read and verify
"""
import argparse, datetime, io, json, os, re, sys, tempfile, zipfile
from collections import namedtuple
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Shared with the chapters-feed engine on purpose: one gws retry/JSON path, one
# city-folding rule, one near-miss stoplist. Two copies would drift, and a city
# that folds one way here and another way there syncs a person to a chapter whose
# feed row says something else.
from sync_chapters import (INTAKE_ID, _gws, gws_json, get_values, fold, fold_city,
                           city_tokens, cell, header_index)

CHAPTERS_PARENT = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"   # the "Chapters" Drive folder
TEMPLATE_FOLDER = "TemplateCity"                        # cloned per city; never gets people
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CRM_SHEET = "Attendees"
# The exact eleven, in order. A workbook whose header row disagrees is skipped
# with a report line rather than written by column letter — the columns have been
# renumbered once already (the Guide tab's live-list formula still references L).
CRM_HEADERS = ("Full name", "Signal", "Trusted/Regular", "Status", "Notes (CRM)",
               "Email", "LinkedIn URL", "Company", "Role / title",
               "Technical expertise", "What brings you here?")

# ONLY these two statuses sync. The CRM is the onboarding list that decides who
# gets access to a chapter folder, so a person reaches it after a decision, not
# on submitting the form: "New", "Tentative" and "Denied" are all held back.
# Exact dropdown strings — "Existing" alone would miss every MLOps row.
# Consequence, and it is intended: the Hosts and Speakers tabs have never been
# triaged off "New", so today this syncs organizers only. Both start flowing the
# moment someone accepts them; nothing here needs to change for that.
SYNC_STATUSES = ("Accepted", "Existing (from MLOps)")

ROLE_TABS = ("Organizers", "Speakers", "Hosts")   # also the merge priority order
CRM_STATUS = {"Organizers": "Organizer", "Speakers": "Speaker", "Hosts": "Host"}

# Status values this script is allowed to overwrite — everything the automation
# itself writes, plus the sheet's own default and the "Prospect" an earlier,
# wider-scoped run could have left behind. A human who moved someone to
# "Attended", "Regular", "Volunteer" or "Declined" has said something the intake
# does not know; a later triage decision must not silently undo it.
AUTO_STATUS = frozenset(("", "New", "Prospect", "Organizer", "Speaker", "Host"))

# Keep the CRM minimal. Identity, decision and interest — nothing else. The
# columns left out (LinkedIn URL, Company, Role / title, Technical expertise)
# still exist on the sheet for an organizer to fill in by hand; the automation
# just doesn't push a survey's worth of personal detail into a folder that is
# still link-readable while chapters are being onboarded.
CRM_WRITTEN = ("Full name", "Trusted/Regular", "Status", "Notes (CRM)", "Email",
               "What brings you here?")

# Rows whose email is at one of these domains are shipped fixture data — the
# "Sam Taylor" sample the template puts in every chapter, and the Tatooine test
# chapter's cast. They are cleared, and their rows reused by real people.
DUMMY_DOMAINS = ("example.com", "example.org", "example.net", "example.edu")

# The Status dropdown shipped without a value for a venue host, so hosts had
# nowhere honest to land. Patched in place on every workbook we open.
DV_STATUS_OLD = "New,Prospect,Attended,Regular,Speaker,Organizer,Volunteer,Declined"
DV_STATUS_NEW = "New,Prospect,Attended,Regular,Speaker,Organizer,Volunteer,Host,Declined"

# Per-role source columns on the intake role tabs, resolved by header name and
# taken in order (first non-empty wins). Organizers have no company or title
# question; hosts have no title. Missing headers are tolerated here — unlike the
# feed writer, a blank CRM cell is a gap, not a corrupt public row.
ROLE_FIELDS = {
    "Organizers": {"name": ("Full name",), "company": (), "title": (),
                   "expertise": ("Technical expertise",),
                   "detail": ("Chapter / city wanted",)},
    "Speakers":   {"name": ("Name", "Full name"), "company": ("Affiliation",),
                   "title": ("Headline",), "expertise": ("Areas of expertise",),
                   "detail": ("Talk title",)},
    "Hosts":      {"name": ("Name", "Full name"), "company": ("Company",), "title": (),
                   "expertise": ("Industry",), "detail": ("Venue name",)},
}

# Fallback for "What brings you here?" when a role-tab row can't be joined back to
# its Form Responses row by email — the form's own wording for that branch.
DEFAULT_INTEREST = {
    "Organizers": "I want to be an organizer/volunteer for the local chapter",
    "Speakers":   "I want to be a speaker",
    "Hosts":      "I want to host a meetup (offer a venue)",
}

# Free text goes into a *private* workbook as an inline string, which Excel and
# Sheets both treat as literal text — a leading "=" can never become a formula,
# so no RAW-vs-USER_ENTERED equivalent is needed here. Control characters are
# still stripped (they make the XML unopenable) and absurd lengths capped well
# under Excel's 32767-character ceiling.
MAX_CELL_TEXT = 2000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_XLNS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
X = "{%s}" % _XLNS
R_ID = "{%s}id" % _RELNS
_XMLNS_RE = re.compile(rb'xmlns:([A-Za-z0-9_]+)="([^"]+)"')
_XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


# ----------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------
def clean_text(s):
    """Strip control characters, collapse newlines, cap the length."""
    s = _CONTROL.sub("", (s or "").replace("\r\n", "\n"))
    s = re.sub(r"[\n\t]+", " ", s)
    s = re.sub(r" {2,}", " ", s).strip()
    return s if len(s) <= MAX_CELL_TEXT else s[:MAX_CELL_TEXT - 1] + "…"


def fold_email(s):
    """Dedupe key for a person. Case- and whitespace-insensitive; the local part
    is NOT otherwise normalised (dots and +tags are meaningful on some hosts)."""
    return clean_text(s).casefold()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def valid_email(s):
    return bool(_EMAIL_RE.match(clean_text(s)))


def first_of(row, headers, names):
    """First non-empty value among `names`, resolved by header name."""
    for n in names:
        if n in headers:
            v = cell(row, headers.index(n))
            if v:
                return v
    return ""


def join_distinct(values, sep=" · "):
    """Join non-empty values, dropping folded duplicates, preserving order."""
    out, seen = [], set()
    for v in values:
        v = clean_text(v)
        if v and fold(v) not in seen:
            seen.add(fold(v))
            out.append(v)
    return sep.join(out)


# ----------------------------------------------------------------------------
# OOXML: read/write the Attendees sheet inside a stored .xlsx
# ----------------------------------------------------------------------------
def register_namespaces(xml_bytes):
    """Keep the document's own xmlns prefixes on re-serialization. Without this
    ElementTree renames every namespaced attribute to ns0:/ns1: and Excel
    rejects the file."""
    ET.register_namespace("", _XLNS)
    for m in _XMLNS_RE.finditer(xml_bytes):
        prefix, uri = m.group(1).decode(), m.group(2).decode()
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass   # reserved prefixes like "xml"


def col_of(ref):
    """'AB12' -> 27 (0-based column index)."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def cell_ref(col, row):
    """(27, 12) -> 'AB12'."""
    s, i = "", col + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return "%s%d" % (s, row)


def load_parts(raw):
    """Return (names, {name: bytes}) — the whole zip, order preserved."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        return names, {n: z.read(n) for n in names}


def save_parts(names, parts):
    with io.BytesIO() as buf:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for n in names:
                z.writestr(n, parts[n])
        return buf.getvalue()


def sheet_part(parts, sheet_name):
    """Resolve a sheet's zip part through workbook.xml + its rels, never by
    guessing 'xl/worksheets/sheet1.xml' — sheet order and file numbering are
    independent, and the legacy CRMs are packed in a different order."""
    wb = ET.fromstring(parts["xl/workbook.xml"])
    rid = None
    for s in wb.iter(X + "sheet"):
        if s.get("name") == sheet_name:
            rid = s.get(R_ID)
            break
    if rid is None:
        return None
    rels = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    for rel in rels:
        if rel.get("Id") == rid:
            t = rel.get("Target").lstrip("/")
            return t if t.startswith("xl/") else "xl/" + t
    return None


def shared_strings(parts):
    raw = parts.get("xl/sharedStrings.xml")
    if not raw:
        return []
    return ["".join(t.text or "" for t in si.iter(X + "t"))
            for si in ET.fromstring(raw)]


def cell_text(c, sst):
    """Text of a <c>, whichever of the three storage forms it uses. We only ever
    WRITE inline strings, but the older CRMs read back as shared-string indices."""
    if c.get("t") == "inlineStr":
        el = c.find(X + "is")
        return "".join(t.text or "" for t in el.iter(X + "t")) if el is not None else ""
    v = c.find(X + "v")
    if v is None or v.text is None:
        return ""
    if c.get("t") == "s":
        i = int(v.text)
        return sst[i] if 0 <= i < len(sst) else ""
    return v.text


def set_cell(row_el, col, text, style=None):
    """Write `text` into (row_el, col) as an inline string, creating the <c> in
    column order if it isn't there. `style` is applied only to a cell we create,
    so an operator's own formatting on an existing cell survives."""
    ref = cell_ref(col, int(row_el.get("r")))
    kids = list(row_el)
    target, insert_at = None, len(kids)
    for i, c in enumerate(kids):
        ci = col_of(c.get("r") or "")
        if ci == col:
            target = c
            break
        if ci > col:
            insert_at = i
            break
    if target is None:
        target = ET.Element(X + "c", {"r": ref})
        if style is not None:
            target.set("s", style)
        row_el.insert(insert_at, target)
    for child in list(target):
        target.remove(child)
    target.set("r", ref)
    target.set("t", "inlineStr")
    is_el = ET.SubElement(target, X + "is")
    t_el = ET.SubElement(is_el, X + "t")
    t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_el.text = text


class Attendees:
    """The Attendees sheet of one chapter CRM, addressed by header name."""

    def __init__(self, parts, part_name):
        raw = parts[part_name]
        register_namespaces(raw)
        self.parts, self.part_name = parts, part_name
        self.root = ET.fromstring(raw)
        self.sst = shared_strings(parts)
        self.data = self.root.find(X + "sheetData")
        if self.data is None:
            raise ValueError("no <sheetData> in %s" % part_name)
        self.rows = {int(r.get("r")): r for r in self.data.findall(X + "row")}
        head = self.rows.get(1)
        if head is None:
            raise ValueError("no header row")
        self.headers = {}
        for c in head.findall(X + "c"):
            txt = clean_text(cell_text(c, self.sst))
            if txt:
                self.headers[txt] = col_of(c.get("r"))
        missing = [h for h in CRM_HEADERS if h not in self.headers]
        if missing:
            raise ValueError("missing column(s): %s" % ", ".join(missing))
        # Row 2 is the shipped sample row and is the only place the per-column
        # cell styles exist; new rows copy them so a synced person looks like a
        # hand-entered one instead of falling back to the sheet default.
        self.sample = {}
        row2 = self.rows.get(2)
        for c in (row2.findall(X + "c") if row2 is not None else []):
            if c.get("s"):
                self.sample[col_of(c.get("r"))] = c.get("s")

    def value(self, rownum, header):
        row = self.rows.get(rownum)
        if row is None:
            return ""
        col = self.headers[header]
        for c in row.findall(X + "c"):
            if col_of(c.get("r") or "") == col:
                return clean_text(cell_text(c, self.sst))
        return ""

    def index_by_email(self):
        """Folded email -> row number, for every populated row. First wins: a
        workbook that already has the same person twice is a pre-existing mess,
        and picking the later row would strand the earlier one's history."""
        out = {}
        for rownum in sorted(self.rows):
            if rownum == 1:
                continue
            e = fold_email(self.value(rownum, "Email"))
            if e and e not in out:
                out[e] = rownum
        return out

    def occupied(self, rownum):
        return bool(self.value(rownum, "Full name") or self.value(rownum, "Email"))

    def free_rows(self, also_free=()):
        """Row numbers available for new people, lowest first, then rows past the
        end of the grid. The shipped workbook pre-creates 1000 styled rows, so a
        new person almost always lands in one that already exists.

        `also_free` are rows being cleared in the same plan — the sample row the
        template ships is row 2, so reusing it puts the chapter's first real
        organizer at the top of the list instead of stranding a blank row there.
        """
        existing = sorted(r for r in self.rows if r > 1)
        for r in existing:
            if r in also_free or not self.occupied(r):
                yield r
        nxt = (existing[-1] if existing else 1) + 1
        while True:
            yield nxt
            nxt += 1

    def clear(self, rownum):
        """Blank every CRM column on a row, keeping the cells and their styles."""
        for header in CRM_HEADERS:
            self.write(rownum, header, "")

    def row_for(self, rownum):
        row = self.rows.get(rownum)
        if row is None:
            row = ET.Element(X + "row", {"r": str(rownum)})
            # sheetData children must stay in ascending row order or Excel
            # reports the file as corrupt.
            kids = list(self.data)
            at = len(kids)
            for i, r in enumerate(kids):
                if int(r.get("r")) > rownum:
                    at = i
                    break
            self.data.insert(at, row)
            self.rows[rownum] = row
        return row

    def write(self, rownum, header, text):
        col = self.headers[header]
        set_cell(self.row_for(rownum), col, text, self.sample.get(col))

    def serialize(self):
        """Write the sheet back into its zip part, refreshing <dimension> to
        cover the rows we added. ET emits its own XML declaration with a
        different encoding spelling, so it is sliced off and replaced with the
        one Excel writes."""
        dim = self.root.find(X + "dimension")
        if dim is not None and self.rows:
            start = (dim.get("ref") or "A1").split(":")[0] or "A1"
            dim.set("ref", "%s:%s" % (start, cell_ref(max(self.headers.values()),
                                                      max(self.rows))))
        body = ET.tostring(self.root, encoding="UTF-8")
        self.parts[self.part_name] = _XML_DECL + body[body.find(b"<worksheet"):]


def patch_status_dropdown(parts, part_name):
    """Add "Host" to the Status column's data-validation list.

    Returns "patched" (the part changed), "already" (Host is offered), or
    "absent" (no list matching either spelling — reported, never guessed at).

    A bytes-level swap of the one formula string: the validation lives outside
    <sheetData>, nothing else in the file mentions it, and re-serializing the
    whole sheet through ElementTree just to change a literal would rewrite
    unrelated markup. Both quote encodings are handled — the template writes
    `"…"` and the older workbooks write `&quot;…&quot;`, and matching only the
    first silently left every legacy CRM un-patched while reporting success.
    """
    raw = parts[part_name]
    for q in (b'"', b"&quot;"):
        new = b"<formula1>" + q + DV_STATUS_NEW.encode() + q + b"</formula1>"
        if new in raw:
            return "already"
        old = b"<formula1>" + q + DV_STATUS_OLD.encode() + q + b"</formula1>"
        if old in raw:
            parts[part_name] = raw.replace(old, new)
            return "patched"
    return "absent"


# ----------------------------------------------------------------------------
# Read the intake
# ----------------------------------------------------------------------------
def read_survey_interests():
    """Folded email -> the person's verbatim "What brings you here?" answer.

    The role tabs are filtered views that drop the routing question, so the one
    column that is literally the person's stated interest has to come from
    `Form Responses`. Joined on email; a repeat applicant's latest answer wins.
    """
    rows = get_values(INTAKE_ID, "'Form Responses'!A:CO")
    if not rows:
        sys.exit("ABORT: 'Form Responses' came back empty.")
    i_email, i_what = header_index(rows[0], "Form Responses",
                                   "Email", "What brings you here?")
    out = {}
    for row in rows[1:]:
        e, what = fold_email(cell(row, i_email)), clean_text(cell(row, i_what))
        if e and what:
            out[e] = what
    return out


def read_role_tab(tab, interests):
    """Return (people, rejected) for one role tab.

    people   = [{row, tab, name, email, city, status, ...}]
    rejected = [{row, tab, name, why}]   no email / no city / denied — never written
    """
    rows = get_values(INTAKE_ID, "%s!A:BB" % tab)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % tab)
    headers = [h.strip() for h in rows[0]]
    # Status and Email are load-bearing: without Status we cannot drop denied
    # applicants, and without Email there is no dedupe key at all. Both are
    # resolved up front so a header rename aborts instead of syncing everyone as
    # a brand-new row on every run.
    i_status, i_email = header_index(headers, tab, "Status", "Email")
    i_chapter = headers.index("Chapter") if "Chapter" in headers else None
    i_g = headers.index("City (Existing)") if "City (Existing)" in headers else None
    i_h = headers.index("City (New)") if "City (New)" in headers else None
    f = ROLE_FIELDS[tab]

    people, rejected = [], []
    for rownum, row in enumerate(rows[1:], start=2):
        email = cell(row, i_email)
        name = first_of(row, headers, f["name"])
        if not (email or name):
            continue                          # trailing empty grid row
        status = cell(row, i_status)
        if status not in SYNC_STATUSES:
            rejected.append({"row": rownum, "tab": tab, "name": name,
                             "why": "status %r — not accepted yet" % (status or "New")})
            continue
        if not valid_email(email):
            rejected.append({"row": rownum, "tab": tab, "name": name,
                             "why": "no usable email (%r) — the CRM dedupes on it" % email})
            continue
        # Chapter assignment wins (a human made it); then the resolved city, then
        # the submitted dropdown unless it is an "Other…" placeholder.
        chapter = cell(row, i_chapter) if i_chapter is not None else ""
        g = cell(row, i_g) if i_g is not None else ""
        h = cell(row, i_h) if i_h is not None else ""
        city = chapter or h or (g if g and not fold(g).startswith("other") else "")
        if not city:
            rejected.append({"row": rownum, "tab": tab, "name": name,
                             "why": "no chapter/city on the intake row"})
            continue
        detail = first_of(row, headers, f["detail"])
        interest = interests.get(fold_email(email)) or DEFAULT_INTEREST[tab]
        people.append({
            "row": rownum, "tab": tab, "status": status,
            "name": clean_text(name) or clean_text(email),
            "email": clean_text(email), "city": clean_text(city),
            "linkedin": clean_text(first_of(row, headers, ("LinkedIn",))),
            "company": clean_text(first_of(row, headers, f["company"])),
            "title": clean_text(first_of(row, headers, f["title"])),
            "expertise": clean_text(first_of(row, headers, f["expertise"])),
            "interest": join_distinct([interest, detail]),
        })
    return people, rejected


def merge_people(people):
    """One CRM row per person per chapter, even when they applied twice.

    Keyed on (folded city, folded email). Role precedence follows ROLE_TABS, so
    someone who is both an organizer and a speaker lands as Organizer with both
    interests recorded — the alternative, two rows, breaks the workbook's own
    "keep one row per person, merge by email" rule.
    """
    merged = {}
    for tab in ROLE_TABS:                       # priority order
        for p in (x for x in people if x["tab"] == tab):
            key = (fold_city(p["city"]), fold_email(p["email"]))
            cur = merged.get(key)
            if cur is None:
                merged[key] = dict(p, tabs=[tab], rows=[p["row"]])
                continue
            cur["tabs"].append(tab)
            cur["rows"].append(p["row"])
            for field in ("linkedin", "company", "title"):
                cur[field] = cur[field] or p[field]
            cur["expertise"] = join_distinct([cur["expertise"], p["expertise"]])
            cur["interest"] = join_distinct([cur["interest"], p["interest"]])
    return list(merged.values())


def crm_fields(p, today):
    """The CRM values for one merged person.

    Only CRM_WRITTEN columns are produced — `Signal` and the detail columns are
    deliberately absent, so the automation never touches them. A blank value
    means "leave that cell alone", never "blank it out".

    Every person reaching here is Accepted or Existing (from MLOps): read_role_tab
    drops everything else, so the CRM Status is always the role itself.
    """
    role_tab = p["tabs"][0]
    return {
        "Full name": p["name"],
        # An accepted organizer is on the chapter's team, not a guest to triage.
        "Trusted/Regular": "Yes" if role_tab == "Organizers" else "",
        "Status": CRM_STATUS[role_tab],
        "Notes (CRM)": "Intake: %s · %s · %s" % (
            "/".join(CRM_STATUS[t] for t in p["tabs"]), p["status"], today),
        "Email": p["email"],
        "What brings you here?": p["interest"],
    }


def is_dummy(email):
    """True only for the reserved example domains. This is the ONLY gate on
    clearing a row, and it is deliberately narrow: anything with a real-looking
    address is left exactly where it is and reported instead, because a row a
    human typed is indistinguishable from one we do not recognise."""
    e = fold_email(email)
    return any(e.endswith("@" + d) for d in DUMMY_DOMAINS)


def preexisting(att, ops, people=()):
    """Occupied rows that are neither fixture data, nor touched by this plan, nor
    someone the intake expects to be there — i.e. people a human added by hand.

    `people` matters on a settled CRM: once everyone is synced there are no ops,
    so every row the sync itself wrote would otherwise be reported back as an
    unrecognised "real-looking address — clear by hand if it's fixture data".
    That reads as a warning about correct data, which trains an operator to
    ignore the one section that exists to flag the genuinely unexpected.
    """
    touched = {o["rownum"] for o in ops}
    expected = {fold_email(p["email"]) for p in people}
    return [{"row": r, "name": att.value(r, "Full name"), "email": att.value(r, "Email")}
            for r in sorted(att.rows)
            if r > 1 and r not in touched and att.occupied(r)
            and fold_email(att.value(r, "Email")) not in expected
            and not is_dummy(att.value(r, "Email"))]


def plan_workbook(att, people, today):
    """Diff one chapter's Attendees sheet against its people. Returns
    [{kind, email, name, rownum, sets}] — empty when in sync. `kind` is
    "clear" (fixture row), "add" (new person) or "fill" (existing person).

    Two write rules, and they are the whole safety story for a sheet humans curate:
      * a cell that already has content is left alone, so notes, corrected
        spellings and hand-added detail survive every re-run;
      * except `Status`, which is upgraded when it still holds a value this
        script wrote (AUTO_STATUS) — that is how a person's role is corrected
        after re-triage, without ever undoing a human's "Declined".
    """
    # Fixture rows go first so their row numbers are reusable below, and so a
    # dummy address can never be mistaken for an existing person to merge into.
    clears = [r for r in sorted(att.rows)
              if r > 1 and att.occupied(r) and is_dummy(att.value(r, "Email"))]
    ops = [{"kind": "clear", "rownum": r, "email": att.value(r, "Email"),
            "name": att.value(r, "Full name"), "sets": {}} for r in clears]

    dropped = set(clears)
    by_email = {e: r for e, r in att.index_by_email().items() if r not in dropped}
    free = att.free_rows(also_free=dropped)
    for p in sorted(people, key=lambda x: (fold(x["name"]), x["email"])):
        want = crm_fields(p, today)
        rownum = by_email.get(fold_email(p["email"]))
        new = rownum is None
        if new:
            rownum = next(free)
        sets = {}
        for header, value in want.items():
            if not value:
                continue
            # A reused fixture row still reads back its dummy content here, but
            # it is `new`, so nothing on it is treated as a human's edit.
            current = "" if new else att.value(rownum, header)
            if current == value:
                continue
            if current and not (header == "Status" and current in AUTO_STATUS):
                continue
            sets[header] = value
        if sets:
            ops.append({"kind": "add" if new else "fill", "email": p["email"],
                        "name": p["name"], "rownum": rownum, "sets": sets})
        if new:
            # Claim the row even when nothing was written, so two people can
            # never be planned into the same empty row.
            by_email[fold_email(p["email"])] = rownum
    return ops


def apply_ops(att, ops):
    # Clears first, and as a separate pass: a cleared row is reused by a person
    # later in the same plan, so blanking after writing would wipe them out.
    for op in (o for o in ops if o["kind"] == "clear"):
        att.clear(op["rownum"])
    for op in (o for o in ops if o["kind"] != "clear"):
        for header, value in op["sets"].items():
            att.write(op["rownum"], header, value)


def finalize(book, ops):
    """Produce the workbook's new bytes: rows first, then serialize, then the
    dropdown patch — in that order, and only in that order.

    `serialize()` rewrites the sheet part wholesale from the element tree, which
    was parsed before any bytes-level edit. Patching the dropdown first and
    serializing second therefore throws the patch away, and the run still reports
    it as applied — exactly what shipped to a probe workbook until this was
    caught. Keeping both steps in one function is what stops the order drifting
    apart again.
    """
    apply_ops(book.att, ops)
    book.att.serialize()
    patch_status_dropdown(book.parts, book.part)
    return save_parts(book.names, book.parts)


# ----------------------------------------------------------------------------
# Drive
# ----------------------------------------------------------------------------
def list_chapter_folders():
    res = gws_json("drive", "files", "list", params={
        "q": "'%s' in parents and mimeType='application/vnd.google-apps.folder' "
             "and trashed=false" % CHAPTERS_PARENT,
        "fields": "files(id,name)", "pageSize": 1000,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    return sorted(res.get("files", []), key=lambda f: f["name"])


def find_crm(folder_id):
    """The one "* CRM.xlsx" in a chapter folder, or (None, why)."""
    res = gws_json("drive", "files", "list", params={
        "q": "'%s' in parents and trashed=false" % folder_id,
        "fields": "files(id,name,mimeType)", "pageSize": 1000,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    crms = [f for f in res.get("files", [])
            if f["name"].lower().endswith("crm.xlsx") and f["mimeType"] == XLSX]
    if not crms:
        return None, "no '<City> CRM.xlsx' in the folder"
    if len(crms) > 1:
        return None, "%d CRM files (%s) — expected one" % (
            len(crms), ", ".join(sorted(f["name"] for f in crms)))
    return crms[0], None


def download(file_id, path):
    # gws rejects --output paths outside its cwd, so run it in the file's dir.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _gws(["gws", "drive", "files", "get", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True, "alt": "media"}),
          "--output", os.path.basename(path)], cwd=os.path.dirname(path) or ".")
    with open(path, "rb") as fh:
        return fh.read()


def upload(file_id, path, raw):
    with open(path, "wb") as fh:
        fh.write(raw)
    _gws(["gws", "drive", "files", "update", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True}),
          "--upload", os.path.basename(path), "--upload-content-type", XLSX],
         cwd=os.path.dirname(path) or ".")


# ----------------------------------------------------------------------------
# Chapter matching
# ----------------------------------------------------------------------------
def match_chapters(people, folders):
    """Return (by_folder, orphans, near_misses).

    by_folder   = {folder_id: [person]}
    orphans     = [{city, people}]                 no folder at all
    near_misses = [{city, people, candidates}]     similar folder(s) — never written
    """
    live = [f for f in folders if f["name"] != TEMPLATE_FOLDER]
    folded = [(f, fold_city(f["name"])) for f in live]
    by_fold = {cf: f for f, cf in folded}

    groups = {}
    for p in people:
        groups.setdefault(fold_city(p["city"]), []).append(p)

    by_folder, orphans, near_misses = {}, [], []
    for fc, grp in sorted(groups.items()):
        folder = by_fold.get(fc)
        if folder:
            by_folder.setdefault(folder["id"], []).extend(grp)
            continue
        toks = city_tokens(grp[0]["city"])
        cands = [f["name"] for f, cf in folded
                 if (fc and cf and (fc in cf or cf in fc)) or (toks & set(cf.split()))]
        rec = {"city": grp[0]["city"], "people": grp}
        if cands:
            near_misses.append(dict(rec, candidates=sorted(set(cands))))
        else:
            orphans.append(rec)
    return by_folder, orphans, near_misses


# ----------------------------------------------------------------------------
# Report + write
# ----------------------------------------------------------------------------
Book = namedtuple("Book", "folder crm names parts part att path")


def open_crm(folder, workdir):
    """Download a chapter's CRM and parse its Attendees sheet.

    Returns (Book, None) or (None, reason). A chapter whose workbook we cannot
    understand is reported and left untouched — never written by column letter.
    """
    crm, why = find_crm(folder["id"])
    if crm is None:
        return None, why
    path = os.path.join(workdir, "%s.xlsx" % re.sub(r"[^\w.-]", "_", folder["name"]))
    names, parts = load_parts(download(crm["id"], path))
    part = sheet_part(parts, CRM_SHEET)
    if part is None:
        return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
    try:
        att = Attendees(parts, part)
    except ValueError as e:
        return None, "%s: %s" % (crm["name"], e)
    return Book(folder, crm, names, parts, part, att, path), None


def run(args):
    today = datetime.date.today().isoformat()
    interests = read_survey_interests()

    people, rejected = [], []
    counts = {}
    for tab in ROLE_TABS:
        pp, rr = read_role_tab(tab, interests)
        counts[tab] = len(pp)
        people += pp
        rejected += rr
    merged = merge_people(people)

    # People are matched against EVERY chapter folder, then --city narrows only
    # which workbooks get opened. Filtering first made --city report every other
    # city in the world as an orphan with no chapter folder.
    all_folders = list_chapter_folders()
    by_folder, orphans, near_misses = match_chapters(merged, all_folders)
    folders = all_folders
    if args.city:
        want = fold_city(args.city)
        folders = [f for f in all_folders if fold_city(f["name"]) == want]
        if not folders:
            sys.exit("ABORT: no chapter folder matches %r." % args.city)
        # The orphan/near-miss lists are global facts about the intake, not about
        # the scoped chapter; showing them under --city reads as this chapter's
        # problem. The full run reports them.
        orphans, near_misses = [], []

    print("Intake  : %d people across %d chapters (%s); %d intake row(s) not synced."
          % (len(merged), len(by_folder),
             ", ".join("%d %s" % (counts[t], t.lower()) for t in ROLE_TABS),
             len(rejected)))
    print("Chapters: %d folder(s) in scope.\n" % len(folders))

    touched, skipped, no_dropdown, keepers = [], [], [], []
    workdir = tempfile.mkdtemp(prefix="aaif-crm-")
    # Every folder is opened, not just the ones with people: the Status dropdown
    # patch has to reach chapters that gained nobody this run, and TemplateCity
    # most of all — otherwise every chapter created from it re-inherits a list
    # with no value for a venue host.
    for folder in folders:
        book, why = open_crm(folder, workdir)
        if book is None:
            skipped.append((folder["name"], why))
            print("  %-18s SKIPPED — %s" % (folder["name"], why))
            continue
        grp = by_folder.get(folder["id"], [])
        # Always planned, even for a chapter that gains nobody: every workbook
        # carries the template's fixture row, and only 57 of the 82 chapters have
        # people. `if grp else []` left Sam Taylor sitting in the other 25.
        ops = plan_workbook(book.att, grp, today)
        kept = preexisting(book.att, ops, grp)
        if kept:
            keepers.append((folder["name"], kept))
        # Detection only — over a shallow copy, so the real patch happens exactly
        # once, inside finalize(), after the sheet has been serialized.
        dv = patch_status_dropdown(dict(book.parts), book.part)
        if dv == "absent":
            # Not fatal — people still sync — but say so: a chapter whose Status
            # column has no dropdown at all will not constrain what gets typed.
            no_dropdown.append(folder["name"])
        if not ops and dv != "patched":
            continue
        n = {k: sum(1 for o in ops if o["kind"] == k) for k in ("clear", "add", "fill")}
        bits = ([("%d dummy cleared" % n["clear"])] if n["clear"] else []) \
            + ([("%d new" % n["add"])] if n["add"] else []) \
            + ([("%d filled in" % n["fill"])] if n["fill"] else []) \
            + (['Status dropdown += "Host"'] if dv == "patched" else [])
        print("  %-18s %s" % (folder["name"], ", ".join(bits)))
        for o in ops:
            mark = {"clear": "-", "add": "+", "fill": "~"}[o["kind"]]
            detail = ("dummy row wiped" if o["kind"] == "clear" else
                      ", ".join("%s=%r" % (k, v if len(v) < 60 else v[:57] + "…")
                                for k, v in o["sets"].items()))
            print("      %s row %-4d %s <%s> — %s"
                  % (mark, o["rownum"], o["name"], o["email"], detail))
        touched.append({"book": book, "ops": ops, "dv": dv})

    if near_misses:
        print("\nNear-miss chapter names (NOT written — fix the intake city or rename the folder):")
        for m in near_misses:
            print("  intake %r (%d people) ~ folder(s) %s"
                  % (m["city"], len(m["people"]), ", ".join(map(repr, m["candidates"]))))
    if orphans:
        print("\nNo chapter folder (NOT written — run aaif-create-chapter for these cities):")
        for o in sorted(orphans, key=lambda x: -len(x["people"])):
            print("  %-28s %d person/people: %s"
                  % (o["city"], len(o["people"]),
                     ", ".join(p["name"] for p in o["people"][:4])
                     + (", …" if len(o["people"]) > 4 else "")))
    if keepers:
        print("\nAlready in a CRM and NOT touched (real-looking address — clear by hand "
              "if it's fixture data):")
        for name, rows in keepers:
            for r in rows:
                print("  %-18s row %-4d %s <%s>" % (name, r["row"], r["name"], r["email"]))
    if skipped:
        # Recapped at the end, not just inline: in an 82-chapter run the inline
        # line scrolls away, and a skipped chapter means people silently did not
        # reach a CRM that the operator believes is now in sync.
        print("\nChapters SKIPPED — nobody was synced to these, fix the workbook and re-run:")
        for name, why in skipped:
            print("  %-28s %s (%d person/people waiting)"
                  % (name, why, len(by_folder.get(
                      next((f["id"] for f in folders if f["name"] == name), ""), []))))
    if no_dropdown:
        print("\nNo Status dropdown to extend (people still sync; the column just "
              "won't constrain typing):\n  %s" % ", ".join(no_dropdown))
    if rejected and args.verbose:
        print("\nIntake rows not synced:")
        for r in rejected:
            print("  %s row %d: %s — %s" % (r["tab"], r["row"], r["name"] or "(no name)", r["why"]))
    elif rejected:
        print("\n%d intake row(s) not synced (denied, or no email/city) — --verbose lists them."
              % len(rejected))

    if not touched:
        print("\nNo changes needed — every chapter CRM is in sync with the intake.")
        return 0
    if not args.write:
        print("\n%d workbook(s) would change. Re-run with --write to apply." % len(touched))
        return 0

    print("\nWriting %d workbook(s)..." % len(touched))
    backup_dir = os.path.join(workdir, "before")
    os.makedirs(backup_dir, exist_ok=True)
    written, failed = [], []
    for t in touched:
        book = t["book"]
        name = book.folder["name"]
        try:
            # Keep the pre-edit bytes before touching anything: an upload that
            # lands a workbook Excel won't open is otherwise only recoverable by
            # hand, through Drive's revision history.
            with open(os.path.join(backup_dir, os.path.basename(book.path)), "wb") as fh:
                fh.write(download(book.crm["id"], os.path.join(workdir, "reread.xlsx")))
            upload(book.crm["id"], book.path, finalize(book, t["ops"]))
            written.append(name)
            print("  wrote %s (%s)" % (name, book.crm["name"]))
        except Exception as e:                     # one bad workbook must not
            failed.append((name, str(e)))          # abandon the other eighty
            print("  FAILED %s — %s" % (name, e), file=sys.stderr)
    print("Wrote %d workbook(s); pre-edit copies in %s" % (len(written), backup_dir))

    print("\nRe-verifying...")
    stale = []
    for t in touched:
        folder = t["book"].folder
        if folder["name"] not in written:
            continue
        book, why = open_crm(folder, os.path.join(workdir, "verify"))
        if book is None:
            stale.append((folder["name"], "could not re-open: %s" % why))
            continue
        left = plan_workbook(book.att, by_folder.get(folder["id"], []), today)
        if left:
            stale.append((folder["name"], "%d op(s) still pending" % len(left)))
        elif t["dv"] == "patched" and patch_status_dropdown(dict(book.parts), book.part) != "already":
            stale.append((folder["name"], 'Status dropdown still lacks "Host"'))
    if failed or stale:
        print("VERIFY FAILED:")
        for name, why in failed + stale:
            print("  %s — %s" % (name, why))
        return 1
    print("Verified: a fresh read of every written workbook proposes zero changes.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Sync intake people + their survey interests into the chapter CRMs.")
    ap.add_argument("--write", action="store_true",
                    help="apply the proposed changes (default: report only)")
    ap.add_argument("--city", help="limit to one chapter folder")
    ap.add_argument("--verbose", action="store_true",
                    help="list every intake row that was not synced")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
