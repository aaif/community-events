#!/usr/bin/env python3
"""Sync intake people (organizers / hosts / speakers) into each chapter's
Attendee CRM workbook, carrying their survey answers across.

Companion engine to sync_chapters.py: that one pushes accepted organizer *names*
onto the public chapters feed, this one pushes accepted *people and their stated
interest* into the private per-chapter CRM. Same house rules — the intake sheet
is only ever READ, the report is the default, and --write re-verifies itself.

Who syncs (the 2026-08 organizer-selection policy):

  * Accepted / Existing (from MLOps) people always sync, across all three role
    tabs (see SYNC_STATUSES).
  * Hosts and Speakers in the pipeline (PIPELINE_STATUSES — the recognised
    in-flight statuses; Denied / Inactive / Duplicate and any value the list
    does not name are excluded) sync too, so a chapter sees its candidate
    venues and talks without waiting on central triage.
  * Organizers in the pipeline sync ONLY into a self-serve chapter — one with
    SELF_SERVE_MIN (4) or more accepted organizers, not counting AAIF ops
    people (AAIF_OPS_NAMES). Those chapters run their own interviews and grow
    their own team; below the threshold, organizer approval stays with AAIF
    ops and pipeline organizers are held back and reported.

A pipeline person lands as `Prospect` (organizers) or their role status
(hosts/speakers), never as a trusted team member — Drive access still keys off
acceptance in sync_access.py, so reaching the CRM grants nothing by itself.

Each chapter folder under the Chapters Drive holds one "<City> CRM.xlsx" whose
"Attendees" tab has eleven columns. Only six are ever written (CRM_WRITTEN) —
identity, decision and interest, and nothing else:

    Full name           <- role tab name
    Trusted/Regular     <- "Yes" for an ACCEPTED organizer (they're on the team)
    Status              <- Organizer / Speaker / Host — or Prospect, for a
                           pipeline organizer in a self-serve chapter
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
  python3 sync_crm.py --verbose          # also list every intake row NOT synced
  python3 sync_crm.py --write            # apply, then re-read and verify
"""
import argparse, datetime, io, os, re, shutil, sys, tempfile, zipfile
from collections import Counter, namedtuple
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Shared with the chapters-feed engine on purpose: one gws retry/JSON path, one
# city-folding rule, one near-miss stoplist. Two copies would drift, and a city
# that folds one way here and another way there syncs a person to a chapter whose
# feed row says something else.
from sync_chapters import (INTAKE_ID, bad_public_text, gws_json, get_values,
                           download, upload, fold, fold_city, fresh_if_unchanged,
                           city_tokens, cell, header_index, resolve_city)

CHAPTERS_PARENT = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"   # the "Chapters" Drive folder
TEMPLATE_FOLDER = "TemplateCity"                        # cloned per city; never gets people
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CRM_SHEET = "Attendees"
# The eleven the sheet must have. PRESENCE is checked, not order or position —
# every access is by header name, and extra columns are tolerated. A workbook
# missing any of them is skipped with a report line rather than written by column
# letter: the columns were renumbered once already, and the Guide tab's live-list
# formula still references L, so letter addressing must never come back.
CRM_HEADERS = ("Full name", "Signal", "Trusted/Regular", "Status", "Notes (CRM)",
               "Email", "LinkedIn URL", "Company", "Role / title",
               "Technical expertise", "What brings you here?")

# The "decided yes" statuses. A person with one of these always syncs, and they
# are what an organizer must hold to count toward a chapter's self-serve
# threshold and to earn Trusted/Regular + Drive access downstream.
# Exact dropdown strings — "Existing" alone would miss every MLOps row.
SYNC_STATUSES = ("Accepted", "Existing (from MLOps)")

# The "still in flight" statuses ("" is a blank cell, which triage treats as
# Prospect — and the "" entry must STAY: read_role_tab checks membership on the
# RAW cell value, before the `status or "Prospect"` normalization, so removing
# the "redundant" empty string would reject every untriaged row).
# "New" is the LEGACY spelling of "Prospect" (renamed 2026-08-22: "New" misread
# as new-organizer, and "Prospect" is the term this engine already writes into
# the CRMs). migrate_status_prospect.py rewrites the intake dropdown and cells;
# keep "New" here until that migration has run everywhere and the dropdowns no
# longer offer it — then it can be dropped.
# Pipeline hosts/speakers sync unconditionally; pipeline organizers sync
# only into a self-serve chapter (see gate_pipeline_organizers). Both lists are
# ALLOWLISTS on purpose: a status added to the intake dropdown tomorrow —
# including a new rejected-ish one — syncs nobody until it is placed here, which
# is the fail-closed direction. Denied / Inactive / Duplicate are excluded by
# not appearing. See aaif-triage-intake/SKILL.md for the current dropdown.
PIPELINE_STATUSES = ("", "New", "Prospect", "In progress", "Tentative",
                     "Interviewing")

# A chapter with this many accepted organizers (SYNC_STATUSES, minus AAIF ops
# people) runs its own organizer interviews: its pipeline organizers sync into
# its CRM as Prospects. Below it, organizer approval stays with AAIF ops.
SELF_SERVE_MIN = 4

# AAIF / MLOps-community ops people. They appear on the intake like anyone else
# but are not a chapter's own team, so they never count toward SELF_SERVE_MIN.
# Matched on the folded FULL name exactly — never a substring, which would also
# catch an unrelated organizer sharing a first name. The bare "Demetrios" entry
# is a deliberate alias for a real intake row that carries only the first name;
# it can exact-match an unrelated mononymous "Demetrios", and that failure
# direction is accepted (the chapter is merely held longer). Names, not emails,
# so this public repo carries no addresses.
AAIF_OPS_NAMES = ("Rahul Parundekar", "Demetrios Brinkmann", "Demetrios",
                  "Ijeoma Onwuka")
AAIF_OPS_FOLDED = frozenset(fold(n) for n in AAIF_OPS_NAMES)

ROLE_TABS = ("Organizers", "Speakers", "Hosts")   # also the merge priority order
CRM_STATUS = {"Organizers": "Organizer", "Speakers": "Speaker", "Hosts": "Host"}

# Status values this script is allowed to overwrite — everything the automation
# itself writes, plus the sheet's own defaults: "Prospect", and its legacy
# spelling "New" (pre-2026-08-22 rows and un-migrated dropdowns still hold it,
# and it must keep upgrading identically). A human who moved someone to
# "Attended", "Regular", "Volunteer" or "Declined" has said something the intake
# does not know; a later triage decision must not silently undo it.
AUTO_STATUS = frozenset(("", "New", "Prospect", "Organizer", "Speaker", "Host"))

# Keep the CRM minimal. Identity, decision and interest — nothing else. The five
# columns left out (Signal, LinkedIn URL, Company, Role / title, Technical
# expertise) still exist on the sheet for an organizer to fill in by hand; the
# automation does not push a survey's worth of personal detail into a shared
# folder. Enforced by Attendees.write(), not just by crm_fields()'s dict body.
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
# The same two lists after migrate_status_prospect.py has removed the legacy
# "New" (Prospect is the surviving spelling). The Host patch must recognise a
# migrated workbook, or every migrated CRM would be reported as having no
# Status list at all.
DV_STATUS_OLD_MIGRATED = DV_STATUS_OLD.replace("New,", "", 1)
DV_STATUS_NEW_MIGRATED = DV_STATUS_NEW.replace("New,", "", 1)

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
    rejects the file.

    Two traps, both of which produced a corrupt-but-uploaded worksheet:

    1. A document may bind a PREFIX to the spreadsheetml namespace as well as
       the default. Registering that prefix displaces the default binding, and
       the root then serializes as `<x:worksheet>` — which `serialize()`'s
       `find(b"<worksheet")` misses entirely. The spreadsheetml URI therefore
       keeps the empty prefix here, always.
    2. `ET.register_namespace` is GLOBAL process state, so what is registered
       last wins for the whole run. `run()` opens every workbook before it
       serializes the first one, which means the map would reflect the LAST
       workbook opened. Callers must re-register immediately before writing —
       `Attendees.serialize()` does, from its own stored bytes.
    """
    ET.register_namespace("", _XLNS)
    for m in _XMLNS_RE.finditer(xml_bytes):
        prefix, uri = m.group(1).decode(), m.group(2).decode()
        if uri == _XLNS:
            continue   # never let an alias displace the default binding
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
    if not text:
        # A truly blank cell, not an empty inline string. `<is><t/></is>` is a
        # zero-length TEXT VALUE: ISBLANK reads FALSE and COUNTA counts it, so
        # a cleared fixture row would still register as populated in the Guide
        # tab's live-list formula. Keep the cell (and its style) but drop the
        # type and the value.
        target.attrib.pop("t", None)
        return
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
        # Kept so serialize() can re-register this workbook's prefixes right
        # before writing — the registry is global and every other workbook in
        # the run has been opened in between.
        self.raw = raw
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
        """True if ANY CRM column on the row holds something.

        Not just name+email: the five columns this script never writes are also
        never cleared, so a row whose name and email an operator deleted still
        carries the departed person's LinkedIn, Company and expertise. Treating
        it as free hands those fields to the next person written there — a real
        current organizer showing a stranger's employer, in the workbook that
        decides folder access. Nothing downstream can detect it, because the
        verify only ever compares CRM_WRITTEN.
        """
        return any(self.value(rownum, h) for h in CRM_HEADERS)

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
        """Blank every CRM column on a row, keeping the cells and their styles.

        Goes through _write, not write: clearing legitimately touches all
        eleven columns, including the five the automation may never author.
        """
        for header in CRM_HEADERS:
            self._write(rownum, header, "")

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
        """Write one CRM cell. Only CRM_WRITTEN columns may be written.

        The "we never push survey detail into this folder" promise was upheld
        only by the literal body of crm_fields() and guarded only by a test.
        Enforcing it here means the privacy boundary cannot be crossed by a
        future caller at all — use clear() for the blanking path, which is
        allowed to touch every column.
        """
        if header not in CRM_WRITTEN:
            raise ValueError(
                "%r is not in CRM_WRITTEN — the automation must not write it. "
                "Use clear() to blank a fixture row." % header)
        self._write(rownum, header, text)

    def _write(self, rownum, header, text):
        col = self.headers[header]
        set_cell(self.row_for(rownum), col, text, self.sample.get(col))

    def serialize(self):
        """Write the sheet back into its zip part, refreshing <dimension> to
        cover the rows we added. ET emits its own XML declaration with a
        different encoding spelling, so it is sliced off and replaced with the
        one Excel writes."""
        # The namespace registry is global and every other workbook in the run
        # has been opened since __init__ ran, so re-register from this
        # workbook's own bytes before serializing.
        register_namespaces(self.raw)
        dim = self.root.find(X + "dimension")
        if dim is not None and self.rows:
            ref = dim.get("ref") or "A1"
            start = ref.split(":")[0] or "A1"
            # Never NARROW the sheet: a workbook with columns past the last
            # CRM header would have them fall outside the declared range.
            end = ref.split(":")[-1] if ":" in ref else ""
            width = max([max(self.headers.values())] + ([col_of(end)] if end else []))
            dim.set("ref", "%s:%s" % (start, cell_ref(width, max(self.rows))))
        body = ET.tostring(self.root, encoding="UTF-8")
        at = body.find(b"<worksheet")
        if at < 0:
            # `find` returns -1 on a miss and body[-1:] is the LAST BYTE, which
            # packs and uploads as a 57-byte "worksheet" without a murmur. Only
            # reachable if the root serialized under a prefix, which
            # register_namespaces now prevents — so this is the assertion that
            # keeps it prevented.
            raise ValueError(
                "%s: serialized root is not <worksheet> (got %r) — refusing to write"
                % (self.part_name, body[:80]))
        self.parts[self.part_name] = _XML_DECL + body[at:]


def patch_status_dropdown(parts, part_name):
    """Add "Host" to the Status column's data-validation list.

    Returns "patched" (the part changed), "already" (Host is offered), or
    "absent" (no list matching any known spelling — reported, never guessed at).

    A bytes-level swap of the one formula string: the validation lives outside
    <sheetData>, nothing else in the file mentions it, and re-serializing the
    whole sheet through ElementTree just to change a literal would rewrite
    unrelated markup. Both quote encodings are handled — the template writes
    `"…"` and the older workbooks write `&quot;…&quot;`, and matching only the
    first silently left every legacy CRM un-patched while reporting success.
    Both the legacy list (leading "New") and the migrated list (Prospect only,
    after migrate_status_prospect.py) are recognised — every "already" check
    runs before any patch, so the pass a workbook actually matches decides.
    """
    raw = parts[part_name]
    variants = ((DV_STATUS_OLD, DV_STATUS_NEW),
                (DV_STATUS_OLD_MIGRATED, DV_STATUS_NEW_MIGRATED))
    for q in (b'"', b"&quot;"):
        for _old, new in variants:
            if b"<formula1>" + q + new.encode() + q + b"</formula1>" in raw:
                return "already"
    for q in (b'"', b"&quot;"):
        for old, new in variants:
            old_b = b"<formula1>" + q + old.encode() + q + b"</formula1>"
            if old_b in raw:
                parts[part_name] = raw.replace(
                    old_b, b"<formula1>" + q + new.encode() + q + b"</formula1>")
                return "patched"
    return "absent"


# ----------------------------------------------------------------------------
# Read the intake
# ----------------------------------------------------------------------------
def read_survey_interests():
    """Folded email -> the person's verbatim "What brings you here?" answer.

    The role tabs are filtered views that drop the routing question, so the one
    column that is literally the person's stated interest has to come from
    `Form Responses`. Joined on email; the LAST row for an address wins, which
    is the latest answer only because the form appends chronologically — a
    sorted or hand-reordered tab would silently change which answer is used.
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


def read_role_tab(tab, interests, include_pipeline=False):
    """Return (people, rejected, fallbacks) for one role tab.

    people   = [{row, tab, name, email, city, status, ...}]
    rejected = [{row, tab, name, why}]   not syncable / no email / no city

    `include_pipeline=False` (the default) keeps the original contract — only
    SYNC_STATUSES people — and every other caller depends on it staying that
    way: sync_access turns this roster into Drive grants, and a pipeline person
    must never reach one. Only sync_crm's own run() opts in, and it still gates
    pipeline ORGANIZERS per chapter afterwards (gate_pipeline_organizers).
    """
    rows = get_values(INTAKE_ID, "%s!A:BB" % tab)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % tab)
    headers = [h.strip() for h in rows[0]]
    # Every column that decides WHO syncs and WHERE they land is load-bearing —
    # resolved through header_index so a rename aborts loudly:
    #   Status  — without it nothing can be filtered
    #   Email   — the only dedupe key; without it everyone re-adds every run
    #   Chapter — the tab's own resolved city, which outranks the raw dropdown
    # A soft `.index(...) if in headers else None` on Chapter silently demoted
    # every resolved assignment to the submitted dropdown, writing people into
    # the wrong chapter's CRM with no way to tell from the report.
    #
    # `Chapter` is NOT a human assignment — it is an ARRAYFORMULA on every role
    # tab (Organizers!P2, Speakers!V, Hosts!AA), and it resolves
    # `City (New)` -> `City (Existing)` unless "Other..." -> the form's free-text
    # city. That last fallback is one step MORE than resolve_city() does, so an
    # accepted person whose only city signal is the free text lands in a CRM here
    # while the chapters feed still counts them unresolved. That divergence is
    # currently 0 of 103 accepted rows — verified, not assumed — but it is why
    # the two must be reconciled rather than left to drift.
    i_status, i_email, i_chapter = header_index(headers, tab, "Status", "Email", "Chapter")
    # These two are genuinely optional — the tabs carried the legacy
    # City/Resolved City names before the rename, and either alone still
    # resolves a city.
    i_g = headers.index("City (Existing)") if "City (Existing)" in headers else None
    i_h = headers.index("City (New)") if "City (New)" in headers else None
    if i_g is None and i_h is None:
        sys.exit("ABORT: tab %r has neither 'City (Existing)' nor 'City (New)' — "
                 "every row would be rejected as having no city. Headers: %s"
                 % (tab, headers))
    f = ROLE_FIELDS[tab]
    # The name column is load-bearing too, but per-role: its absence would send
    # every person through the `or email` fallback below, writing email
    # addresses into `Full name` across dozens of workbooks — and since
    # populated cells are never overwritten, the script could not repair it.
    if not any(n in headers for n in f["name"]):
        sys.exit("ABORT: tab %r has none of the name column(s) %s — every row "
                 "would be named after its email address. Headers: %s"
                 % (tab, list(f["name"]), headers))

    people, rejected, fallbacks = [], [], []
    for rownum, row in enumerate(rows[1:], start=2):
        email = cell(row, i_email)
        name = first_of(row, headers, f["name"])
        if not (email or name):
            continue                          # trailing empty grid row
        status = cell(row, i_status)
        accepted = status in SYNC_STATUSES
        if not accepted:
            if not include_pipeline:
                rejected.append({"row": rownum, "tab": tab, "name": name,
                                 "why": "status %r — not accepted yet"
                                        % (status or "Prospect")})
                continue
            if status not in PIPELINE_STATUSES:
                # Denied / Inactive / Duplicate, or a dropdown value this script
                # has never seen — either way, fail closed and say so.
                rejected.append({"row": rownum, "tab": tab, "name": name,
                                 "why": "status %r — declined, parked, or not a "
                                        "recognised pipeline status" % status})
                continue
        if not valid_email(email):
            rejected.append({"row": rownum, "tab": tab, "name": name,
                             "why": "no usable email (%r) — the CRM dedupes on it" % email})
            continue
        # Chapter wins — the role tab's OWN resolved city (a formula, not a human
        # assignment); the fallback is the shared resolve_city(), imported from
        # sync_chapters so the two engines cannot disagree on what a row's city
        # means. The Chapter formula's extra free-text step lives in the sheet,
        # not here (see the header_index comment above).
        chapter = cell(row, i_chapter)
        g = cell(row, i_g) if i_g is not None else ""
        h = cell(row, i_h) if i_h is not None else ""
        city = chapter or resolve_city(g, h)
        if not city:
            rejected.append({"row": rownum, "tab": tab, "name": name,
                             "why": "no chapter/city on the intake row"})
            continue
        # The same public-text gate sync_chapters.read_intake runs, enforced
        # here too because this reader does NOT go through it: the role tabs
        # are read directly, and without this check a name or city full of
        # markup or control characters walked straight into a chapter CRM —
        # and, via sync_access's roster read, toward a Drive grant target.
        # This is what makes the documented property ("a flagged value can
        # reach no cell, no About doc and no CRM") true on the CRM path.
        bad = bad_public_text("name", name) or bad_public_text("city", city)
        if bad:
            rejected.append({"row": rownum, "tab": tab, "name": name, "why": bad})
            continue
        detail = first_of(row, headers, f["detail"])
        # A failed join is invisible once written: the generic branch text is
        # indistinguishable from a real answer, and the cell is then non-empty
        # so no later run corrects it. Count the fallbacks so run() can say so.
        joined = interests.get(fold_email(email))
        if not joined:
            fallbacks.append(rownum)
        interest = joined or DEFAULT_INTEREST[tab]
        people.append({
            "row": rownum, "tab": tab, "status": status or "Prospect",
            "name": clean_text(name) or clean_text(email),
            "email": clean_text(email), "city": clean_text(city),
            "linkedin": clean_text(first_of(row, headers, ("LinkedIn",))),
            "company": clean_text(first_of(row, headers, f["company"])),
            "title": clean_text(first_of(row, headers, f["title"])),
            "expertise": clean_text(first_of(row, headers, f["expertise"])),
            "interest": join_distinct([interest, detail]),
        })
    # Only when a join was actually attempted: sync_access calls this with an
    # empty interests dict on purpose (it needs the roster, not the answers), and
    # every row missing a match is the expected result there, not a broken join.
    if interests and fallbacks and len(fallbacks) == len(people) and people:
        # Every single person missing a Form Responses match is a broken join
        # (a renamed email column, a diverged address set), not a data
        # condition — and it would write boilerplate into every interest cell.
        sys.exit("ABORT: none of the %d person/people on tab %r matched a "
                 "'Form Responses' row by email, so every 'What brings you here?' "
                 "would be generic branch text. Check the Email columns on both "
                 "tabs." % (len(people), tab))
    return people, rejected, fallbacks


def is_aaif_ops(name):
    """Exact folded-name match against AAIF_OPS_FOLDED — never a substring."""
    return fold(name) in AAIF_OPS_FOLDED


def is_accepted(p):
    """Whether a PRE-MERGE person/row dict carries a decided-yes intake status.

    Only valid before merge_people: a merged person has `status` popped, so
    calling this on one raises KeyError — deliberately, per the stale-read rule.
    """
    return p["status"] in SYNC_STATUSES


def gate_pipeline_organizers(people):
    """Split (kept, held): pipeline organizers whose chapter is below
    SELF_SERVE_MIN accepted organizers are held back for central approval.
    Held entries carry a `why`, authored here beside the rule itself; they are
    a SUPERSET of the rejection shape (the full person dict plus `why`, not the
    4-key {row, tab, name, why}) — run()'s Held summary reads `city` off them,
    which a plain rejection does not have.

    The threshold counts DISTINCT accepted-organizer emails per folded city,
    excluding AAIF ops people — a chapter is self-serve on the strength of its
    own team, not because central staff appear on its roster. Accepted people
    and pipeline hosts/speakers pass through untouched; the count comes from
    the same intake read, so a chapter crossing the threshold starts pulling
    its Prospects on the very next run.
    """
    accepted = {}
    for p in people:
        if p["tab"] == "Organizers" and is_accepted(p) and not is_aaif_ops(p["name"]):
            accepted.setdefault(fold_city(p["city"]), set()).add(fold_email(p["email"]))
    kept, held = [], []
    for p in people:
        if (p["tab"] == "Organizers" and not is_accepted(p)
                and len(accepted.get(fold_city(p["city"]), ())) < SELF_SERVE_MIN):
            # The folded key is named in the why: the count groups by it, so a
            # chapter whose accepted organizers split across spellings that
            # fold differently shows the split instead of stating a false
            # count as fact.
            held.append(dict(p, why="status %r — %r (city key %r) has fewer "
                                    "than %d accepted organizers, so organizer "
                                    "approval stays with AAIF ops"
                                    % (p["status"], p["city"],
                                       fold_city(p["city"]), SELF_SERVE_MIN)))
        else:
            kept.append(p)
    return kept, held


def merge_people(people, blocked=None):
    """One CRM row per person per chapter, even when they applied twice.

    Keyed on (folded city, folded email). Role precedence follows ROLE_TABS, so
    someone who is both an organizer and a speaker lands as Organizer with both
    interests recorded — the alternative, two rows, breaks the workbook's own
    "keep one row per person, merge by email" rule.

    One refusal (2026-08-22, security review): a NOT-yet-accepted row never
    merges into a person whose rows so far are ALL accepted. The form is
    public and email is the merge key, so a stranger submitting under an
    accepted organizer's address would otherwise fill that person's blank CRM
    cells and restamp their Notes — attacker content attributed to a trusted
    identity. Refused rows are appended to `blocked` (rejection-shaped, for
    the report) when the caller passes a list; the legitimate mixed case — one
    person pipeline in one role and accepted in another — still merges when
    the pipeline row seeds first (ROLE_TABS order), which is how a real
    person's rows arrive, and is covered by the tests either way.
    """
    merged = {}
    for tab in ROLE_TABS:                       # priority order
        for p in (x for x in people if x["tab"] == tab):
            key = (fold_city(p["city"]), fold_email(p["email"]))
            cur = merged.get(key)
            if (cur is not None and not is_accepted(p)
                    and all(s in SYNC_STATUSES for s in cur["statuses"])):
                if blocked is not None:
                    blocked.append({
                        "row": p["row"], "tab": tab, "name": p["name"],
                        "why": "status %r — same email as an accepted person's "
                               "CRM row; refusing to merge an unvetted "
                               "submission into it. Review the intake row."
                               % p["status"]})
                continue
            if cur is None:
                m = dict(p, tabs=[tab], rows=[p["row"]], statuses=[p["status"]])
                # Drop the singular forms: on a merged person `tab`/`row`/
                # `status` are an arbitrary member of the plural lists, equal
                # to the winning one only by the accident that the
                # highest-priority tab seeds the entry. Removing them makes a
                # stale read a KeyError instead of a plausible wrong answer.
                for k in ("tab", "row", "status"):
                    m.pop(k, None)
                merged[key] = m
                continue
            cur["tabs"].append(tab)
            cur["rows"].append(p["row"])
            cur["statuses"].append(p["status"])
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

    Status and Trusted/Regular follow the ACCEPTED roles only:
      * any accepted role -> that role's status (highest-priority accepted tab
        wins), and Trusted/Regular = "Yes" for an accepted organizer;
      * no accepted role, but an organizer application in flight -> "Prospect"
        — a candidate for the chapter's own interviews, not on the team;
      * a pipeline host/speaker -> their role status, never trusted.
    "Prospect" is in AUTO_STATUS, so acceptance upgrades the row on a later run
    without a human having to touch it.
    """
    # Derived, not stored: tabs/statuses are index-aligned by merge_people, so
    # the accepted roles need no third parallel list to keep in sync. strict=
    # True is the alignment tripwire: a future edit that appends to one list
    # but not the other must raise here, not silently truncate the zip and
    # demote an accepted person to Prospect.
    acc = [t for t, s in zip(p["tabs"], p["statuses"], strict=True)
           if s in SYNC_STATUSES]
    if acc:
        status = CRM_STATUS[acc[0]]
        # An accepted organizer is on the chapter's team, not a guest to triage.
        trusted = "Yes" if acc[0] == "Organizers" else ""
    else:
        status = "Prospect" if "Organizers" in p["tabs"] else CRM_STATUS[p["tabs"][0]]
        trusted = ""
    return {
        "Full name": p["name"],
        "Trusted/Regular": trusted,
        "Status": status,
        "Notes (CRM)": "Intake: %s · %s · %s" % (
            join_distinct([CRM_STATUS[t] for t in p["tabs"]], "/"),
            join_distinct(p["statuses"], "/"), today),
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
    #
    # A row is only fixture data if NOBODY IN THE PLAN owns that address.
    # `valid_email("sam@example.com")` is true, so an intake person really can
    # have an example-domain address — and clearing their row while also
    # re-adding them makes the plan non-empty forever. Under --write the
    # re-verify then never converges and the chapter reports "ops still
    # pending" permanently, indistinguishable from a real write failure.
    owned = {fold_email(p["email"]) for p in people}
    clears = [r for r in sorted(att.rows)
              if r > 1 and att.occupied(r) and is_dummy(att.value(r, "Email"))
              and fold_email(att.value(r, "Email")) not in owned]
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
    # Match the write kinds explicitly rather than `!= "clear"`. The negative
    # form treats any future kind as a write — the fail-open direction, on the
    # one path that touches the workbook, while the report (which indexes a
    # dict of the three known marks) would crash or undercount.
    unknown = [o["kind"] for o in ops if o["kind"] not in ("clear", "add", "fill")]
    if unknown:
        raise ValueError("unknown op kind(s) %s — refusing to write" % sorted(set(unknown)))
    for op in (o for o in ops if o["kind"] in ("add", "fill")):
        for header, value in op["sets"].items():
            att.write(op["rownum"], header, value)


def finalize(book, ops, expected_dv=None):
    """Produce the workbook's new bytes: rows first, then serialize, then the
    dropdown patch — in that order, and only in that order.

    `serialize()` rewrites the sheet part wholesale from the element tree, which
    was parsed before any bytes-level edit. Patching the dropdown first and
    serializing second therefore throws the patch away, and the run still reports
    it as applied — exactly what shipped to a probe workbook until this was
    caught. Keeping both steps in one function is what stops the order drifting
    apart again.
    """
    # finalize serializes through `att` but saves `book.parts` — they must be
    # the same dict, or the returned zip silently loses either the row writes or
    # the dropdown patch. open_crm builds them that way; assert it rather than
    # trusting every future caller to.
    if book.parts is not book.att.parts or book.part != book.att.part_name:
        raise ValueError("Book.parts/part must be the same objects Attendees holds")
    apply_ops(book.att, ops)
    book.att.serialize()
    got = patch_status_dropdown(book.parts, book.part)
    if expected_dv is not None and got != expected_dv:
        # The prediction was made against the downloaded bytes; the real patch
        # runs against ElementTree's re-encoding of them. They agree today
        # because both quote spellings are handled — this is what catches the
        # day they stop agreeing, rather than reporting a patch never applied.
        raise ValueError("dropdown patch predicted %r but returned %r for %s"
                         % (expected_dv, got, book.crm["name"]))
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
    # Everything from the download onward is guarded, not just Attendees(): a
    # truncated download raises zipfile.BadZipFile, a missing rels part raises
    # KeyError, and ET.ParseError is a SyntaxError — NOT a ValueError. Catching
    # only ValueError let any of those abort the whole run, and in the verify
    # loop that happens AFTER uploads have landed, replacing the summary with a
    # bare traceback that names neither the chapter nor what was written.
    try:
        names, parts = load_parts(download(crm["id"], path))
        part = sheet_part(parts, CRM_SHEET)
        if part is None:
            return None, "%s has no %r sheet" % (crm["name"], CRM_SHEET)
        att = Attendees(parts, part)
    except Exception as e:
        return None, "%s: %s: %s" % (crm["name"], type(e).__name__, e)
    return Book(folder, crm, names, parts, part, att, path), None


def write_workbooks(touched, workdir, backup_dir):
    """Upload every planned workbook. Returns (written, changed, failed).

    Right before each upload the workbook is re-downloaded — that fresh copy is
    also the pre-edit backup. Planning takes minutes across ~80 workbooks and
    the approval pause adds more, so a human edit in that window is a NORMAL
    event: if the fresh bytes differ from the bytes the plan was built on
    (still sitting at book.path), the workbook is skipped loudly instead of
    silently reverting the edit, and re-proposes on the next run. `changed`
    counts as a failure for exit-code purposes; nothing was written to those.
    """
    written, changed, failed = [], [], []
    for t in touched:
        book = t["book"]
        name = book.folder["name"]
        try:
            # Keep the pre-edit bytes before touching anything: an upload that
            # lands a workbook Excel won't open is otherwise only recoverable by
            # hand, through Drive's revision history. The compare itself is the
            # shared fresh_if_unchanged — one definition of "unchanged" for
            # this gate and sync_about's.
            with open(book.path, "rb") as fh:
                planned = fh.read()
            current, drifted = fresh_if_unchanged(
                book.crm["id"], os.path.join(workdir, "reread.xlsx"), planned)
            with open(os.path.join(backup_dir, os.path.basename(book.path)), "wb") as fh:
                fh.write(current)
            if drifted:
                changed.append(name)
                print("  SKIPPED %s — workbook changed since the plan was built; "
                      "NOT written, re-run to sync it" % name, file=sys.stderr)
                continue
            upload(book.crm["id"], book.path, finalize(book, t["ops"], t["dv"]), XLSX)
            written.append(name)
            print("  wrote %s (%s)" % (name, book.crm["name"]))
        except Exception as e:                     # one bad workbook must not
            failed.append((name, str(e)))          # abandon the other eighty
            print("  FAILED %s — %s" % (name, e), file=sys.stderr)
    return written, changed, failed


def run(args):
    # Every opened workbook — real names, emails and survey answers — lands in
    # this temp dir. A report-only run must not strand ~80 of them there
    # forever, so the directory is removed on the way out; --write keeps it,
    # because its before/ backups are the recovery path (the output says where).
    workdir = tempfile.mkdtemp(prefix="aaif-crm-")
    try:
        return _run(args, workdir)
    finally:
        if not args.write:
            shutil.rmtree(workdir, ignore_errors=True)


def _run(args, workdir):
    today = datetime.date.today().isoformat()
    interests = read_survey_interests()

    people, rejected, fallbacks = [], [], []
    for tab in ROLE_TABS:
        pp, rr, fb = read_role_tab(tab, interests, include_pipeline=True)
        people += pp
        rejected += rr
        fallbacks += fb
    people, held = gate_pipeline_organizers(people)
    # Held-back pipeline organizers surface through the same not-synced channel
    # as every other excluded row, so --verbose names them individually.
    rejected += held
    counts = Counter(p["tab"] for p in people)
    merge_blocked = []
    merged = merge_people(people, blocked=merge_blocked)
    rejected += merge_blocked

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

    # counts[] tallies qualifying ROWS per tab, len(merged) is PEOPLE after the
    # cross-role email merge — say so, or "102 people (104 organizers)" reads
    # like a counting bug.
    print("Intake  : %d people across %d chapters (from %s qualifying row(s), "
          "merged on email); %d intake row(s) not synced."
          % (len(merged), len(by_folder),
             " + ".join("%d %s" % (counts[t], t.lower()) for t in ROLE_TABS),
             len(rejected)))
    if held:
        # Distinct people, not rows — the same candidate with two intake rows
        # must not inflate a number that gets quoted in status reports.
        print("Held    : %d pipeline organizer(s) across %d chapter(s) still under "
              "central approval (fewer than %d accepted organizers) — counted in "
              "the not-synced total above."
              % (len({fold_email(p["email"]) for p in held}),
                 len({fold_city(p["city"]) for p in held}), SELF_SERVE_MIN))
    if merge_blocked:
        print("SECURITY: %d not-yet-accepted intake row(s) share an email with "
              "an accepted person and were NOT merged into their CRM row — "
              "--verbose lists them; review those intake rows."
              % len(merge_blocked))
    print("Chapters: %d folder(s) in scope.\n" % len(folders))

    touched, skipped, no_dropdown, keepers = [], [], [], []
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
        # people. `if grp else []` left the sample row sitting in all the rest.
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
        # Recapped at the end, not just inline: across every chapter the inline
        # line scrolls away, and a skipped chapter means people silently did not
        # reach a CRM that the operator believes is now in sync.
        print("\nChapters SKIPPED — nobody was synced to these, fix the workbook and re-run:")
        for name, why in skipped:
            print("  %-28s %s (%d person/people waiting)"
                  % (name, why, len(by_folder.get(
                      next((f["id"] for f in folders if f["name"] == name), ""), []))))
    if fallbacks:
        # Silent once written: the branch text is indistinguishable from a real
        # answer and the cell is never corrected on a later run.
        print("\n%d person/people had no 'Form Responses' match, so their "
              "'What brings you here?' is the generic branch text for their role."
              % len(fallbacks))
    if no_dropdown:
        print("\nNo Status dropdown to extend (people still sync; the column just "
              "won't constrain typing):\n  %s" % ", ".join(no_dropdown))
    if rejected and args.verbose:
        print("\nIntake rows not synced:")
        for r in rejected:
            print("  %s row %d: %s — %s" % (r["tab"], r["row"], r["name"] or "(no name)", r["why"]))
    elif rejected:
        print("\n%d intake row(s) not synced (not yet accepted, or no email/city) "
              "— --verbose lists them." % len(rejected))

    if not touched:
        # Never claim a clean sweep over chapters that were never opened: a
        # skipped workbook means people silently did not reach a CRM the
        # operator now believes is in sync.
        if skipped:
            print("\nNo changes needed for the chapters that could be opened — but "
                  "%d was/were SKIPPED above and are NOT in sync." % len(skipped))
            return 1
        print("\nNo changes needed — every chapter CRM is in sync with the intake.")
        return 0
    if not args.write:
        print("\n%d workbook(s) would change. Re-run with --write to apply." % len(touched))
        # Shared engine exit convention: report mode exits 0 when in sync,
        # 2 when it proposes changes (consumed by nightly.py).
        return 2

    print("\nWriting %d workbook(s)..." % len(touched))
    backup_dir = os.path.join(workdir, "before")
    os.makedirs(backup_dir, exist_ok=True)
    written, changed, failed = write_workbooks(touched, workdir, backup_dir)
    print("Wrote %d workbook(s); pre-edit copies kept in %s" % (len(written), backup_dir))
    if changed:
        print("\n%d workbook(s) changed since the plan was built and were NOT "
              "written — re-run to sync them:\n  %s"
              % (len(changed), ", ".join(changed)))

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
    if failed or stale or changed:
        if failed or stale:
            print("VERIFY FAILED:")
            for name, why in failed + stale:
                print("  %s — %s" % (name, why))
        # `changed` was already reported above; it shares the failure exit so a
        # wrapper never reads a run with unwritten workbooks as complete.
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
