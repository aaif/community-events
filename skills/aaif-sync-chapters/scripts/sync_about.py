#!/usr/bin/env python3
"""Sync accepted intake organizers into each chapter's About.docx.

Every chapter folder under the Chapters Drive folder holds one About.docx whose
"Organizers" section is a bulleted list of names. Every one of those lists was
cloned from TemplateCity — which is itself a copy of the San Francisco doc — so
79 of 80 chapters shipped naming the same four people. This engine rewrites that
list from the intake decisions, so the section names the chapter's OWN organizers.

Source of truth: intake rows whose Status is "Accepted" or "Existing (from
MLOps)", resolved to a city exactly as sync_chapters.py resolves it. The intake
sheet is only ever READ.

Usage:
  python3 sync_about.py                   # report + proposed changes, writes nothing
  python3 sync_about.py --city Melbourne  # one chapter
  python3 sync_about.py --write           # apply, then re-download and verify

The section is rewritten to the accepted list WHOLESALE, and that is deliberate:
"leave a hand-edited list alone" reads like the safe default, but the one
hand-edited list in the estate (Melbourne) grouped applicants under "Approved" /
"Submitted Application" / "Planning Application" — publishing, in a doc shared
with the chapter, that two named people had applied and had not been approved.
Preserving that preserves the disclosure. So anything in the section that is not
an accepted organizer is removed, and every removal is itemised in the report for
the operator to approve BEFORE anything is written.
"""
import argparse, html, io, os, re, sys, tempfile, zipfile
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Imported, not copied: city folding, the near-miss stoplist and city resolution
# must mean the same thing here as on the chapters list, or an organizer lands on
# one city's feed row and in another city's About doc.
from sync_chapters import (INTAKE_ID, INTAKE_TAB, SYNC_STATUSES, cell,
                           city_tokens, download, fold, fold_city, get_values,
                           gws_json, header_index, read_intake, resolve_city,
                           upload)

CHAPTERS_PARENT = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"
TEMPLATE_FOLDER = "TemplateCity"
ABOUT_NAME = "about.docx"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The folded heading that opens the block. A trailing space or colon is tolerated
# — the template's own heading is "Organizers " with a trailing space.
HEADING = "organizers"

# The four names every chapter cloned from TemplateCity. Recognised only as a
# COMPLETE block: individually they are ordinary names (three of the four really
# do organize the chapter the template was copied from), so treating one of them
# as a fixture on its own would silently drop a real organizer from a real
# chapter.
#
# A conscious exception to the no-real-names rule: these are the publicly listed
# organizers on the chapters feed, and the constant MUST stay literal —
# TemplateCity itself now carries the placeholder, so the block can no longer be
# derived at runtime, and a legacy doc that was never synced still carries these
# exact four names.
TEMPLATE_NAMES = ("Rahul Parundekar", "Arthur Coleman", "Leo Walker",
                  "Shreeganesh Ramanan (SG)")

# What the section reads when nobody is accepted for the chapter yet. It is also
# what TemplateCity carries, so a chapter created from it starts with a visible
# blank to fill rather than four wrong names. Recognised as a fixture, so it is
# replaced the moment the chapter's first organizer is accepted.
PLACEHOLDER = "[Organizer name]"

# A fresh bullet, used only when the block has no bullet left to clone (a chapter
# sitting on the placeholder that gains its first organizer). Copied from
# TemplateCity's own markup: numId 1 is the Organizers list — the "Luma & Socials"
# list below it is numId 4, so cloning the nearest bullet would renumber the list.
MODEL_BULLET = (
    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0" /><w:numId w:val="1" /></w:numPr>'
    '<w:spacing w:after="0" w:line="288" w:lineRule="auto" />'
    '<w:ind w:left="720" w:hanging="360" /><w:rPr /></w:pPr></w:p>')

# Bottom margin on the last bullet vs the rest, as the template sets them. A
# reused bullet keeps everything else it had; only this is normalised, so the
# section never collapses into the "Luma & Socials" heading below it.
SPACING_LAST, SPACING_INNER = "140", "0"

RUN = ('<w:r><w:rPr><w:rtl w:val="0" /></w:rPr>'
       '<w:t xml:space="preserve">%s</w:t></w:r>')

_TAG = re.compile(r"<w:p(?=[\s/>])[^>]*?(/?)>|</w:p>", re.S)
_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_PPR = re.compile(r"(<w:p(?=[\s/>])[^>]*>)(<w:pPr>.*?</w:pPr>)?", re.S)
_PARA_ID = re.compile(r'\s+w14:(?:paraId|textId)="[^"]*"')
_AFTER = re.compile(r'(<w:spacing\b[^>]*?)\s*w:after="\d+"')


# ----------------------------------------------------------------------------
# document.xml surgery (pure — every function here is unit-tested offline)
#
# Byte-level, not ElementTree: re-serializing the part reorders every namespace
# declaration on <w:document> and rewrites markup this engine never meant to
# touch. Splicing one paragraph range leaves the rest of the part — and every
# other zip member, the embedded brand fonts among them — byte-identical.
# ----------------------------------------------------------------------------
def paragraphs(xml):
    """[(start, end)] for every TOP-LEVEL <w:p> in the part, in document order.

    Depth-counted rather than a flat `<w:p>.*?</w:p>` match: a paragraph nested
    in a textbox would end the outer match at the inner `</w:p>` and splice the
    document in half.
    """
    out, depth, start = [], 0, None
    for m in _TAG.finditer(xml):
        if m.group(0).startswith("</"):
            depth = max(0, depth - 1)
            if depth == 0 and start is not None:
                out.append((start, m.end()))
                start = None
        elif m.group(1):                    # <w:p/> — self-closing, no content
            if depth == 0:
                out.append((m.start(), m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def para_text(block):
    return html.unescape("".join(_TEXT.findall(block)))


def is_list(block):
    return "<w:numPr" in block


def find_section(xml):
    """Locate the Organizers list.

    Returns {"bullets": [(s, e)], "span": (s, e)} or None. The block runs from
    the heading to the first paragraph that is not a list item — the
    "Luma & Socials" heading in every chapter doc.

    The heading is matched on its TEXT, not its style: two of these docs have
    been round-tripped through desktop Word, and a restyled heading must not
    make a chapter silently unsyncable.
    """
    paras = paragraphs(xml)
    for i, (s, e) in enumerate(paras):
        block = xml[s:e]
        if is_list(block) or fold(para_text(block)).rstrip(":").strip() != HEADING:
            continue
        bullets = []
        for js, je in paras[i + 1:]:
            if not is_list(xml[js:je]):
                break
            bullets.append((js, je))
        return {"bullets": bullets, "span": (e, bullets[-1][1] if bullets else e)}
    return None


def set_spacing(block, after):
    return _AFTER.sub(lambda m: '%s w:after="%s"' % (m.group(1), after), block, count=1)


def clone_bullet(model, name):
    """A bullet carrying `name`, built from `model`'s paragraph properties.

    Only the <w:pPr> is kept, so a hyperlink or stray run formatting on the model
    does not follow the new name in. w14:paraId is stripped — it is meant to be
    unique per paragraph, and a clone would duplicate the one it came from.
    """
    m = _PPR.match(model)
    open_tag, ppr = m.group(1), m.group(2) or ""
    if open_tag.endswith("/>"):                       # a self-closing model
        open_tag = open_tag[:-2] + ">"
    return _PARA_ID.sub("", open_tag + ppr) + RUN % html.escape(name, quote=False) + "</w:p>"


def render(names, bullets):
    """The replacement XML for the whole bullet range.

    A bullet whose text already matches a wanted name is reused **verbatim** —
    that is what keeps a hyperlinked name (the template links Rahul's) and any
    hand formatting alive across a re-run. Only the trailing spacing is
    normalised, so the last bullet keeps its bottom margin wherever it lands.
    """
    by_name = {}
    for b in bullets:
        by_name.setdefault(fold(para_text(b)), b)
    model = next((b for b in bullets if para_text(b).strip()), MODEL_BULLET)
    return "".join(
        set_spacing(by_name.get(fold(n)) or clone_bullet(model, n),
                    SPACING_LAST if i == len(names) - 1 else SPACING_INNER)
        for i, n in enumerate(names))


def removals(current, names, roster):
    """Bullets that will disappear, as (applicants, unknown).

    `applicants` are people this chapter's intake knows who are NOT accepted —
    the disclosure class. Their presence in the section is the reason this engine
    rewrites rather than skips, so they are called out on their own.

    `unknown` is everything else the intake cannot account for: the sub-headings
    a human interleaved with the names, and any organizer kept off the intake
    entirely. Reported loudly because removing a real person's name on the
    strength of "the intake has never heard of them" is a judgement the operator
    makes at the approval gate, not one this script makes silently.

    The template names and the placeholder are excluded from both: they are the
    fixture this engine exists to clear, and the per-chapter diff already shows them.
    """
    wanted = {fold(n) for n in names}
    fixture = {fold(n) for n in TEMPLATE_NAMES} | {fold(PLACEHOLDER)}
    gone = [c for c in current if c and fold(c) not in wanted and fold(c) not in fixture]
    return ([c for c in gone if fold(c) in roster],
            [c for c in gone if fold(c) not in roster])


def plan_doc(xml, names, roster):
    """Return (new_xml, current_names) — or (None, reason) when there is no section.

    There is no "this list looks hand-edited, skip it" branch by design; see the
    module docstring. The gate is the report plus the operator's approval.
    """
    sec = find_section(xml)
    if sec is None:
        return None, "no 'Organizers' heading in the doc"
    bullets = [xml[s:e] for s, e in sec["bullets"]]
    current = [para_text(b).strip() for b in bullets]
    s, e = sec["span"]
    return xml[:s] + render(names, bullets) + xml[e:], current


# ----------------------------------------------------------------------------
# Intake
# ----------------------------------------------------------------------------
def read_roster():
    """{folded city: {folded name}} across EVERY intake status.

    The recognition gate needs to know a name came from the intake at all, not
    just that it is accepted today: a chapter whose organizer was later declined
    must still be rewritable, or its doc names them forever.
    """
    rows = get_values(INTAKE_ID, "%s!A:U" % INTAKE_TAB)
    if not rows:
        sys.exit("ABORT: intake tab %r came back empty." % INTAKE_TAB)
    i_name, i_g, i_h = header_index(rows[0], INTAKE_TAB,
                                    "Full name", "City (Existing)", "City (New)")
    roster = {}
    for row in rows[1:]:
        name = cell(row, i_name)
        city = resolve_city(cell(row, i_g), cell(row, i_h))
        if name and city:
            roster.setdefault(fold_city(city), set()).add(fold(name))
    return roster


def match_cities(by_city, folders, display=None):
    """Split accepted-organizer cities into (orphans, near_misses).

    Same folding and the same generic-word stoplist as the chapters and CRM
    engines, so "San Diego" is never written into "San Francisco".

    `display` maps the folded key back to the intake's own spelling, so the
    report names "Stuttgart" rather than the comparison key "stuttgart".
    """
    live = [f for f in folders if f["name"] != TEMPLATE_FOLDER]
    folded = [(f, fold_city(f["name"])) for f in live]
    have = {cf for _f, cf in folded}
    orphans, near = [], []
    for fc, names in sorted(by_city.items()):
        if fc in have:
            continue
        toks = city_tokens(fc)
        cands = sorted({f["name"] for f, cf in folded
                        if (fc and cf and (fc in cf or cf in fc)) or (toks & set(cf.split()))})
        rec = {"city": (display or {}).get(fc, fc), "names": names, "candidates": cands}
        (near if cands else orphans).append(rec)
    return orphans, near


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


def find_about(folder_id):
    """The one About.docx in a chapter folder, or (None, why)."""
    res = gws_json("drive", "files", "list", params={
        "q": "'%s' in parents and trashed=false" % folder_id,
        "fields": "files(id,name,mimeType)", "pageSize": 1000,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    hits = [f for f in res.get("files", [])
            if f["name"].lower() == ABOUT_NAME and f["mimeType"] == DOCX]
    if not hits:
        return None, "no About.docx in the folder"
    if len(hits) > 1:
        return None, "%d About.docx files — expected one" % len(hits)
    return hits[0], None


def read_document(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return z.read("word/document.xml").decode("utf-8")


def write_document(raw, xml):
    """Repack the .docx with a new document.xml, every other member preserved
    byte-for-byte along with its ZipInfo — the embedded brand fonts are stored
    members and must not be re-compressed."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, io.BytesIO() as buf:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = xml.encode("utf-8") if it.filename == "word/document.xml" \
                    else zin.read(it.filename)
                zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
                zi.compress_type = it.compress_type
                zi.external_attr = it.external_attr
                zout.writestr(zi, data)
        out = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(out)) as z:   # never upload a broken zip
        if z.testzip() is not None:
            raise RuntimeError("repackaged .docx failed validation")
    return out


# ----------------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------------
Doc = namedtuple("Doc", "folder about raw xml current names new_xml reason "
                        "applicants unknown")


def wanted_names(city, by_city):
    """The accepted organizers for a chapter, in intake row order.

    Falls back to the placeholder so the section never renders as a heading with
    nothing under it — and so the block keeps a bullet to clone from when the
    chapter's first organizer is accepted.
    """
    return by_city.get(fold_city(city), []) or [PLACEHOLDER]


def plan_one(folder, by_city, roster, workdir):
    about, why = find_about(folder["id"])
    if about is None:
        return Doc(folder, None, None, None, None, None, None, why, [], [])
    path = os.path.join(workdir, "%s.docx" % re.sub(r"[^\w.-]", "_", folder["name"]))
    names = wanted_names(folder["name"], by_city)
    known = roster.get(fold_city(folder["name"]), set())
    # Everything from the download on is guarded: a truncated download raises
    # BadZipFile, a missing part raises KeyError, bad bytes raise
    # UnicodeDecodeError. One unreadable chapter must not abandon the other 80.
    try:
        raw = download(about["id"], path)
        xml = read_document(raw)
        new_xml, detail = plan_doc(xml, names, known)
    except Exception as e:
        return Doc(folder, about, None, None, None, names, None,
                   "%s: %s" % (type(e).__name__, e), [], [])
    if new_xml is None:
        return Doc(folder, about, raw, xml, None, names, None, detail, [], [])
    applicants, unknown = removals(detail, names, known)
    return Doc(folder, about, raw, xml, detail, names, new_xml, None,
               applicants, unknown)


def changed(d):
    return d.new_xml is not None and d.new_xml != d.xml


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def print_report(docs, counts, orphans, near):
    qual = " + ".join("%d %s" % (counts[s], s) for s in SYNC_STATUSES)
    print("Intake  : %s." % qual)
    print("Chapters: %d About.docx read." % sum(1 for d in docs if d.xml is not None))

    edits = [d for d in docs if changed(d)]
    noop = [d for d in docs if d.new_xml is not None and not changed(d)]
    skipped = [d for d in docs if d.reason]

    if edits:
        print("\nProposed Organizers rewrites:")
        for d in sorted(edits, key=lambda d: d.folder["name"]):
            print("  %s" % d.folder["name"])
            print("      - %s" % ("; ".join(d.current) or "(empty)"))
            print("      + %s" % "; ".join(d.names))
    if noop:
        print("\nAlready correct (%d): %s"
              % (len(noop), ", ".join(sorted(d.folder["name"] for d in noop))))
    if skipped:
        print("\nSkipped — NOT written:")
        for d in sorted(skipped, key=lambda d: d.folder["name"]):
            print("  %s: %s" % (d.folder["name"], d.reason))

    # The disclosure the rewrite exists to clear. Printed as its own section
    # because it is the one class of removal an operator must not skim past.
    leaks = [d for d in edits if d.applicants]
    if leaks:
        print("\nNon-accepted applicants named in a shared doc — REMOVED by this run:")
        for d in sorted(leaks, key=lambda d: d.folder["name"]):
            print("  %s: %s" % (d.folder["name"], "; ".join(d.applicants)))
    odd = [d for d in edits if d.unknown]
    if odd:
        print("\nLines the intake cannot account for — also removed. Check these are "
              "sub-headings and not a real organizer who never went through intake:")
        for d in sorted(odd, key=lambda d: d.folder["name"]):
            print("  %s: %s" % (d.folder["name"], "; ".join(d.unknown)))
    if near:
        print("\nNear-miss cities (NOT written — fix the intake city, or create the folder):")
        for m in near:
            print("  intake %r (%s) ~ folder %s"
                  % (m["city"], "; ".join(m["names"]), ", ".join(map(repr, m["candidates"]))))
    if orphans:
        print("\nAccepted organizers whose city has no chapter folder "
              "(run aaif-create-chapter):")
        for m in orphans:
            print("  %s: %s" % (m["city"], "; ".join(m["names"])))

    blank = [d for d in edits if d.names == [PLACEHOLDER]]
    if blank:
        print("\n%d chapter(s) have no accepted organizer yet, so their template "
              "names are replaced by %r:\n  %s"
              % (len(blank), PLACEHOLDER,
                 ", ".join(sorted(d.folder["name"] for d in blank))))
    if not edits:
        print("\nNo changes needed — every About doc matches the intake.")


# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------
def compute(args, workdir):
    entries, _unresolved, counts, _dupes, malformed = read_intake()
    roster = read_roster()

    by_city, display = {}, {}
    for e in entries:
        by_city.setdefault(fold_city(e["city"]), []).append(e["name"])
        display.setdefault(fold_city(e["city"]), e["city"])

    folders = list_chapter_folders()
    # The create-chapter queue is computed against EVERY folder, never the
    # --city subset, or a single-chapter run would report all 80 as missing.
    orphans, near = match_cities(by_city, folders, display)
    if args.city:
        want = fold_city(args.city)
        folders = [f for f in folders if fold_city(f["name"]) == want]
        if not folders:
            sys.exit("ABORT: no chapter folder named %r." % args.city)

    with ThreadPoolExecutor(max_workers=6) as ex:
        docs = list(ex.map(lambda f: plan_one(f, by_city, roster, workdir), folders))
    return docs, counts, orphans, near, malformed


def apply_writes(docs, workdir):
    """Upload every changed doc. A failure is collected, not raised — one bad
    upload must not abandon the rest, and the operator needs the whole list.

    Each doc is re-downloaded and compared against its compute()-time bytes
    first: the plan window spans ~80 downloads plus the approval pause, and a
    spliced document.xml uploaded over a human's edit from that window would
    silently revert it. A changed doc is skipped (counted with the failures,
    so the run exits non-zero) and re-proposes on the next run.
    """
    ok, failed = [], []
    for d in sorted((d for d in docs if changed(d)), key=lambda d: d.folder["name"]):
        safe = re.sub(r"[^\w.-]", "_", d.folder["name"])
        try:
            fresh = download(d.about["id"], os.path.join(workdir, "re_%s.docx" % safe))
            if fresh != d.raw:
                failed.append((d.folder["name"],
                               "changed since the plan was built — NOT written; re-run"))
                print("  %-22s SKIPPED — changed since plan; re-run" % d.folder["name"])
                continue
            raw = write_document(d.raw, d.new_xml)
            upload(d.about["id"], os.path.join(workdir, "up_%s.docx" % safe), raw, DOCX)
        except Exception as e:
            failed.append((d.folder["name"], "%s: %s" % (type(e).__name__, e)))
            print("  %-22s FAILED" % d.folder["name"])
            continue
        ok.append(d.folder["name"])
        print("  %-22s written" % d.folder["name"])
    return ok, failed


def main():
    ap = argparse.ArgumentParser(
        description="Sync accepted intake organizers into each chapter's About.docx.")
    ap.add_argument("--write", action="store_true",
                    help="apply the proposed rewrites (default: report only)")
    ap.add_argument("--city", help="limit to one chapter folder")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="aaif-about-") as workdir:
        # --write recomputes from a fresh read here — a stale proposal is never applied.
        docs, counts, orphans, near, malformed = compute(args, workdir)
        print_report(docs, counts, orphans, near)
        if malformed:
            # read_intake excluded these rows, so an accepted organizer listed
            # here silently drops OUT of their chapter's rewritten section —
            # say so, or the removal reads as a decision instead of a data bug.
            print("\nMalformed public-form text — these intake rows are EXCLUDED "
                  "(their names sync nowhere, About docs included) until fixed:")
            for m in malformed:
                print("  intake row %d (city %r): %s" % (m["row"], m["city"], m["why"]))
        drift = any(changed(d) for d in docs)
        # A doc that could not be READ (an About.docx was found but download or
        # parse raised) is UNKNOWN, not clean. Without this, a dead gws
        # credential turns every doc into a "Skipped" line, drift stays False,
        # and a permanently broken engine reads green in nightly forever.
        unread = sorted(d.folder["name"] for d in docs
                        if d.reason and d.about is not None and d.raw is None)
        findable = sum(1 for d in docs if d.about is not None)
        if unread and len(unread) == findable:
            sys.exit("ABORT: every About.docx failed to read (%d of %d) — that "
                     "is the API or auth broken, not the docs. Nothing is known "
                     "about the estate; fix gws and re-run." % (len(unread), findable))
        if unread:
            # Same stdout marker contract as sync_resources: nightly.py reads
            # this prefix, because the exit code alone cannot tell "in sync"
            # from "only half-checked".
            print("\nPARTIAL: %d doc(s) could not be read — their state is "
                  "unknown, not clean: %s" % (len(unread), ", ".join(unread)))
        if not args.write:
            # Shared engine exit convention: report mode exits 0 when in sync,
            # 2 when it proposes changes (consumed by nightly.py).
            return 2 if (drift or unread) else 0
        if not drift:
            return 2 if unread else 0

        print("\nWriting %d About doc(s)..." % sum(1 for d in docs if changed(d)))
        ok, failed = apply_writes(docs, workdir)
        print("Wrote %d doc(s); %d failed." % (len(ok), len(failed)))
        for name, why in failed:
            print("  FAILED %s: %s" % (name, why))

        print("\nRe-verifying (re-downloading every written doc)...")
        # The writes have already landed. A bare traceback here would leave the
        # operator unable to tell what was modified, so say so explicitly.
        try:
            after, _c, _o, _n, _m = compute(args, workdir)
        except (Exception, SystemExit) as e:
            sys.exit("WRITES WERE APPLIED (%d doc(s)) but verification could not run: "
                     "%s\nRe-run without --write to confirm." % (len(ok), e))
        stale = [d.folder["name"] for d in after
                 if changed(d) and d.folder["name"] in ok]
        # A written doc whose re-verify READ failed was never actually checked
        # — and it is the one doc most likely to be damaged, since its upload
        # just happened. Excluding it from `stale` (changed() is False on an
        # unread doc) would make this a vacuous pass; fail it explicitly.
        after_by_name = {d.folder["name"]: d for d in after}
        unchecked = [n for n in ok
                     if after_by_name.get(n) is None or after_by_name[n].reason]
        if stale or unchecked:
            print("VERIFY FAILED:")
            for n in sorted(stale):
                print("  %s — still out of sync after write" % n)
            for n in sorted(unchecked):
                print("  %s — written but could not be re-read; check it by hand"
                      % n)
            sys.exit(1)
        print("Verified: a fresh read of every written doc proposes zero changes.")
        if failed:
            sys.exit(1)
        return 2 if unread else 0


if __name__ == "__main__":
    sys.exit(main())
