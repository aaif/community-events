#!/usr/bin/env python3
"""Conform every template in the Drive estate to the AAIF design system.

The decks and trackers were hand-authored before the design system existed, so
the brand lives in them as literal font names and hex values: Space Grotesk and
Manrope as display faces, Arial in every theme, a warm-grey ramp half a shade
off `--line-2`, a navy `1E2761` in the trackers, and Office's stock colour
scheme underneath all of it. This sweep replaces those with the tokens in
`design/aaif-tokens.css`.

The actual rewriting lives in `aaif_events.ooxml_style`, which is shared with
the repo's own CI check so the rules cannot drift from what the tests assert.
This script is the Drive half: find the templates, archive them, rewrite them,
and report honestly on what it could not reach.

**Scope is templates, not events.** A file is in scope if it sits directly in a
chapter / online-series / shared-Templates folder (`About.docx`, the CRM) or in
one of that folder's template subfolders (`Event Templates (Copy for Each
Event)`, `Event Name`, `Banners (Chapter Specific, Changed Rarely)`). Anything
deeper is an organizer's copy of a real event and is deliberately left alone —
those are counted and reported so "out of scope" never silently becomes
"missed".

That set includes **TemplateCity** and **TemplateSeries**, the folders cloned
for every new chapter and series, and the shared **Templates** folder. Those
three are what mint everything else, so a full run asserts it reached all of
them and exits non-zero if it did not: miss one and every chapter created after
this migration is born off-brand again.

**Nothing is written without an archive.** Every file whose bytes change is
written to `./backups/restyle-<UTC>/<path>` *before* the upload, so there is a
local rollback that does not depend on Drive's revision history. `--write`
refuses to start if that directory cannot be created.

Read-only by default. There is no undo beyond the archive and Drive's own
revisions, so read a plan run before passing `--write`.

Usage:
  # Plan (default) — list what would change, write nothing:
  python restyle_design_system.py

  # Audit only: what is still off the design system, estate-wide? (exit 1 if any)
  python restyle_design_system.py --check

  # Apply to the whole estate:
  python restyle_design_system.py --write

  # One chapter, or one of the three template roots:
  python restyle_design_system.py --chapter TemplateCity --write

  # Run the engine on a local file, no Drive at all:
  python restyle_design_system.py --restyle-local ./Slides.pptx
"""
import argparse
import datetime
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc      # Drive plumbing + the shared zip rewriter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "lib"))
from aaif_events import ooxml_style as ox    # noqa: E402  (after the path shim)
from aaif_events import agent_art as aa      # noqa: E402
from aaif_events import contrast as ctr      # noqa: E402

COMMUNITY_ROOT = "1Z1M-xk0S16sksS1IBNm9OG6Ia22Yql6f"   # "Community Events"
CHAPTERS_FOLDER = "Chapters"
SERIES_FOLDER = "Online"
SHARED_TEMPLATES = "Templates"

#: The three folders that mint everything else. A full run must reach all three.
MINTS = ("TemplateCity", "TemplateSeries", SHARED_TEMPLATES)

#: Folders holding a *template* rather than one event's copy of it. The chapter
#: tree names them "Event Templates (Copy for Each Event)", the online series
#: "Event Template", the shared folder "Event Name"; every tree also has a
#: "Banners (Chapter Specific, Changed Rarely)". Matching on a folder NAME is
#: fragile — one of these has been renamed once already — so main() reports any
#: chapter that contributed no file at all, which is what a rename looks like.
TEMPLATE_FOLDER_RE = re.compile(r"^(event templates?\b.*|event name|banners\b.*)$", re.I)

#: The roots whose *direct* children are in scope: a chapter, an online series,
#: or the shared Templates folder. `About.docx` and the CRM live here.
ROOT_PARENTS = (CHAPTERS_FOLDER, SERIES_FOLDER)

OOXML = {cc.PPTX: ".pptx", cc.DOCX: ".docx", cc.XLSX: ".xlsx"}

#: The template files themselves, by name. Being *in* a template folder is not
#: enough to be a template: organizers park their own work there — a dated event
#: deck, a "Copy of …", a personal draft — and rebranding someone's finished
#: event deck is not this script's business. The first estate run swept eleven
#: such files before this allowlist existed; they were restored from the archive.
#:
#: Anything else in a template folder is skipped AND NAMED in the report, so a
#: genuinely new template gets noticed rather than silently missed — which is
#: the failure mode an allowlist trades for.
TEMPLATE_FILES = frozenset((
    "Slides.pptx", "Event-Hero.pptx", "Event-Hero-Square.pptx",
    "LinkedIn Carousel.pptx", "Square Logo.pptx", "Banner 1.91.pptx",
    "Luma Banners.pptx", "Event Tasks.docx", "About.docx",
    "Event Tracker.docx", "Event Tracker (IRL).docx",
    "Event Tracker (Online).docx", "Attendee CRM.xlsx",
))

#: The per-chapter CRM is named for its chapter ("Boston CRM.xlsx"), so it
#: cannot be listed above. Only its styling is ever rewritten — cell values live
#: in xl/worksheets/ and xl/sharedStrings.xml, which restyle_part never opens.
#: `[^/\\]+`, not `.+`: a Drive file name may legally contain a slash, and an
#: unanchored `.+` would let "../../x CRM.xlsx" satisfy the allowlist and then
#: escape the archive directory when that name is used to build a local path.
CRM_RE = re.compile(r"^[^/\\]+ CRM\.xlsx$")


def is_template(name):
    return name in TEMPLATE_FILES or bool(CRM_RE.match(name))

#: Decks that get the background plates, and which aspect each one is drawn at.
#: Only the hero decks: a plate is a title-card background, and the runbook deck
#: and the banners have their own compositions.
#: NOTE: no "Copy of ..." entries. `is_template()` rejects those names, so such
#: a key would be dead — and worse, it would state the opposite of the allowlist
#: that exists precisely to stop the sweep touching organizers' copies.
PLATED = {"Event-Hero.pptx": "wide", "Event-Hero-Square.pptx": "square"}


def walk_estate(root, jobs=8):
    """(entries, chapters, series, owners-with-files, out-of-scope count).

    `entries` are the OOXML files in scope, deduped by Drive id — a file
    reachable under two parents would otherwise be handed to two workers that
    share a scratch filename and race.
    """
    found, chapters, series, with_files = [], set(), set(), set()
    skipped, strays = 0, []
    level, seen = [(root, "Community Events")], set()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while level:
            level = [(f, p) for f, p in level if f not in seen]
            seen.update(f for f, _ in level)
            nxt = []
            for path, kids in pool.map(lambda t: (t[1], cc.list_children(t[0])), level):
                parts = path.split("/")
                leaf = parts[-1]
                # In scope if this folder is a template folder, or is itself a
                # chapter / series root, or is the shared Templates folder.
                is_template_folder = bool(TEMPLATE_FOLDER_RE.match(leaf))
                is_root = ((len(parts) == 3 and parts[1] in ROOT_PARENTS)
                           or (len(parts) == 2 and leaf == SHARED_TEMPLATES))
                owner = parts[2] if len(parts) > 2 else (
                    leaf if leaf == SHARED_TEMPLATES else None)
                for k in kids:
                    if k["mimeType"] == cc.FOLDER:
                        nxt.append((k["id"], path + "/" + k["name"]))
                        if leaf == CHAPTERS_FOLDER:
                            chapters.add(k["name"])
                        elif leaf == SERIES_FOLDER:
                            series.add(k["name"])
                    elif k["mimeType"] in OOXML:
                        if owner:
                            with_files.add(owner)
                        if not (is_template_folder or is_root):
                            skipped += 1          # an organizer's event copy
                        elif is_template(k["name"]):
                            found.append({"id": k["id"], "name": k["name"],
                                          "mime": k["mimeType"],
                                          "path": path + "/" + k["name"]})
                        else:
                            # In a template folder but not a template.
                            strays.append(path + "/" + k["name"])
            level = nxt
    seen_ids, unique = set(), []
    for f in found:
        if f["id"] not in seen_ids:
            seen_ids.add(f["id"])
            unique.append(f)
    return unique, chapters, series, with_files, skipped, sorted(strays)


#: Characters kept when a Drive name becomes part of a local path. Everything
#: else — separators, "..", control characters — is replaced.
_SAFE_SEGMENT = re.compile(r"[^\w .,()&+-]")


def _archive_path(entry, backup_dir):
    """Where this file's pre-change copy belongs, under `backup_dir`.

    Every path segment comes from a **Drive folder or file name**, which any
    organizer with editor access controls. Joining those onto a local directory
    unsanitised lets a folder renamed to `../../..` walk the archive out of
    `./backups/` and drop attacker-supplied bytes anywhere the operator can
    write. So each segment is sanitised, and the result is then checked to be
    inside `backup_dir` after resolution — belt and braces, because sanitising
    alone is the kind of thing a later edit quietly weakens.
    """
    rel = entry["path"].replace("Community Events/", "", 1)
    parts = []
    for seg in rel.split("/"):
        seg = _SAFE_SEGMENT.sub("_", seg)
        # "." is kept by the character class above — file names need it — so a
        # segment of nothing but dots survives sanitising and still walks up.
        # Neutralise it here and let the containment check below stay a
        # backstop for the case nobody thought of, rather than the only guard.
        if set(seg) == {"."}:
            seg = "_" * len(seg)
        if seg:
            parts.append(seg)
    if not parts:
        raise RuntimeError("cannot derive an archive path from %r" % entry["path"])
    dst = os.path.realpath(os.path.join(backup_dir, *parts))
    root = os.path.realpath(backup_dir)
    if dst != root and not dst.startswith(root + os.sep):
        raise RuntimeError(
            "refusing to archive %r outside %s — a Drive name is trying to "
            "escape the archive directory" % (entry["path"], root))
    return dst


def _archive(entry, src, backup_dir):
    """Copy the pre-change file into the archive, mirroring its Drive path.

    Raises on failure. An upload that proceeds after a failed archive is an
    unrecoverable edit to a file nobody has a copy of.
    """
    dst = _archive_path(entry, backup_dir)
    if os.path.exists(dst):
        # Never overwrite. Two runs sharing a --backup-dir would otherwise have
        # the second archive the ALREADY-RESTYLED file over the original, and
        # the archive would then silently be useless as a rollback — which is
        # exactly what happened to two files on the first estate run. The
        # earliest copy is the pristine one, so it is the one that is kept.
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return dst


#: The plate that replaces a retired legacy background. `soft-plate` and not the
#: hero gradient on purpose: the replacement sits behind text that is ALREADY
#: written and positioned, so the darkest plate in the set is the one that keeps
#: it readable. Measured over the four affected deck types, swapping in the
#: hero gradient leaves 50 runs below AA and the soft plate leaves 36 — against
#: 61 with the legacy plate still in place.
RETIREMENT_PLATE = "soft-plate"


def _aspect_of(path):
    """"wide" or "square" from the deck's own slide size."""
    with zipfile.ZipFile(path) as z:
        presentation_xml = z.read("ppt/presentation.xml").decode("utf-8", "replace")
    m = re.search(r"<p:sldSz[^>]*/?>", presentation_xml)
    dims = dict(re.findall(r'\b(cx|cy)="(\d+)"', m.group(0))) if m else {}
    cx, cy = int(dims.get("cx", 9144000)), int(dims.get("cy", 5143500))
    return "square" if abs(cx - cy) < cx * 0.02 else "wide"


def _plate_digests(plate_dir):
    """SHA-256 of every plate this toolkit generated, so retirement can tell
    ours from a legacy one without having to recognise the legacy one."""
    out = set()
    for name in os.listdir(plate_dir):
        if name.startswith("plate-"):
            with open(os.path.join(plate_dir, name), "rb") as fh:
                out.add(hashlib.sha256(fh.read()).hexdigest())
    return out


def _plates_for(entry, plate_dir):
    """`[(label, path), ...]` for this file, or [] if it takes no plates."""
    aspect = PLATED.get(entry["name"])
    if not (aspect and plate_dir):
        return []
    out = []
    for kind in aa.PLATES:
        ext = "gif" if kind in aa.ANIMATED else "png"
        path = os.path.join(plate_dir, "plate-%s-%s.%s" % (kind, aspect, ext))
        if not os.path.exists(path):
            raise RuntimeError("plate %s missing from %s — run agent_art.build "
                               "first" % (os.path.basename(path), plate_dir))
        out.append((kind, path))
    return out


def contrast_report(entry, src):
    """Text in this file that fails WCAG AA against what is behind it.

    A token check cannot find this: black-on-black is two correct AAIF tokens in
    the wrong pairing. Only .pptx is measured — a Word tracker has no slide
    background and its text sits on the page.
    """
    if entry["mime"] != cc.PPTX:
        return [], []
    found = ctr.check_pptx(src)
    # Unreadable and unchecked are DIFFERENT, and conflating them destroys the
    # distinction the checker is built around: inherited-colour runs are common,
    # so counting them as failures means --contrast can never exit 0 and its
    # exit status stops meaning anything.
    return ([f for f in found if f.ratio is not None and f.ratio < f.threshold],
            [f for f in found if f.ratio is None])


def process(entry, tmpdir, write, backup_dir, check_only, plate_dir=None,
            contrast_only=False, fix_contrast=False, retire=False):
    """Restyle one file. Returns (entry, report, error or None).

    `report` is `{"parts": n, "before": [...], "after": [...]}` when the file
    changed or is off-system, and `{}` when it is already conformant.
    """
    ext = OOXML[entry["mime"]]
    # Named by Drive id, not by path: two truncated paths that collide would
    # have concurrent workers overwriting each other's download.
    src = os.path.join(tmpdir, "in-%s%s" % (entry["id"], ext))
    try:
        cc.gws_download(entry["id"], src)
        # gws writes the response body to --output even when the API returned an
        # error at exit 0, so a JSON error page would reach zipfile and surface
        # as a BadZipFile blaming the OOXML engine.
        if not os.path.exists(src) or os.path.getsize(src) == 0:
            raise RuntimeError("download wrote no file")
        if not zipfile.is_zipfile(src):
            raise RuntimeError("download is not OOXML (%d bytes) — an error body, "
                               "not the template" % os.path.getsize(src))
        if contrast_only:
            bad, unchecked = contrast_report(entry, src)
            return entry, ({"contrast": bad, "unchecked": unchecked}
                           if bad or unchecked else {}), None
        before = ox.audit(src)
        if check_only:
            return entry, ({"before": before, "parts": 0, "after": before,
                            "plates": []} if before else {}), None

        # Archive BEFORE any rewrite: the archive must hold what Drive holds.
        # Gated on `write` ALONE, not on `before` — a file can be perfectly
        # conformant on tokens and still be about to change, because a contrast
        # repair or a plate is also a change. Gating on `before` meant a
        # --fix-contrast run over an already-conformant estate would rewrite
        # every deck with no rollback at all.
        #
        # `fresh` is False when an earlier run in the same --backup-dir already
        # archived this file; that copy is the pristine one and is kept, so this
        # run must not remove it on a no-op below.
        archived, fresh = None, False
        if write:
            existed = os.path.exists(_archive_path(entry, backup_dir))
            archived = _archive(entry, src, backup_dir)
            fresh = not existed

        parts = cc._rewrite_zip(src, ox.restyle_part)
        # Renaming the faces leaves a Word file referencing a font it does not
        # embed, still declaring the faces nobody uses, and carrying ~760KB of
        # their embedded bytes. Reconcile the font table with what the document
        # now actually asks for.
        pruned = (ox.prune_embedded_fonts(src)
                  if entry["mime"] == cc.DOCX else ([], []))
        # Plates are appended after the restyle so the slide they are cloned
        # from is already conformant — otherwise every new slide would carry a
        # copy of the drift this run just removed.
        wanted = _plates_for(entry, plate_dir)
        plated = ox.add_plate_slides(src, wanted) if wanted else []
        # Adding is idempotent by label, so a deck that already has its plates
        # needs its ARTWORK refreshed separately when the art itself changes.
        plated += ox.update_plates(src, wanted) if wanted else []
        # Legibility last: it measures the file as it will actually ship, so it
        # has to run after the restyle and after any plate has gone in behind
        # the text.
        # Retire before repairing: the repair measures text against whatever is
        # behind it, so it has to see the NEW plate, not the one being removed.
        # Only the decks a plate belongs on. `retire_plates` replaces ANY
        # background image that is not ours, so letting it loose on every
        # template .pptx would treat designed art on a banner or the carousel
        # as legacy — PLATED exists precisely to say which decks take a plate.
        retired = []
        if retire and plate_dir and entry["name"] in PLATED:
            aspect = PLATED[entry["name"]]
            with open(os.path.join(plate_dir, "plate-%s-%s.png"
                                   % (RETIREMENT_PLATE, aspect)), "rb") as fh:
                replacement = fh.read()
            retired = ox.retire_plates(src, replacement, _plate_digests(plate_dir))
        rescued = (ox.improve_contrast(src)
                   if fix_contrast and entry["mime"] == cc.PPTX else (0, 0, 0))
        after = ox.audit(src)
        if not parts and not plated and not rescued[0] and not retired \
                and not pruned[0] and not pruned[1]:
            # Nothing changed. If we archived a file we are not going to upload,
            # take the copy back out so the archive means "these were replaced".
            if fresh and archived and os.path.exists(archived):
                os.remove(archived)
            return entry, ({"before": before, "parts": 0, "after": after,
                            "plates": [], "rescued": (0, 0, 0)}
                           if before else {}), None
        if write:
            cc.gws_upload(entry["id"], src, entry["mime"])
        return entry, {"before": before, "parts": parts, "after": after,
                       "plates": plated, "rescued": rescued,
                       "retired": retired, "pruned": pruned}, None
    except Exception as e:                # one bad file must not stop the run
        # Prefix the class: this catch spans the XML engine, the archive, and
        # both transfers, and a bare message makes a ValueError in the rewrite
        # read as a network blip. Never return a bare str() — an exception whose
        # str() is empty would be falsy and the caller would count the file as
        # clean, printing nothing at all.
        return entry, {}, "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        if os.path.exists(src):
            os.remove(src)


def restyle_local(path):
    """--restyle-local: run the engine on one file, no Drive access at all."""
    out = re.sub(r"\.(pptx|docx|xlsx)$", "", path) + "-restyled" + os.path.splitext(path)[1]
    shutil.copy2(path, out)
    before = ox.audit(out)
    parts = cc._rewrite_zip(out, ox.restyle_part)
    after = ox.audit(out)
    print("%s -> %s" % (path, out))
    print("  %d part(s) rewritten; %d off-system value(s) before, %d after"
          % (parts, len(before), len(after)))
    for _p, kind, val in after:
        print("     REMAINS  %s %s" % (kind, val))
    return 0 if not after else 1


def _summarise(hits):
    """"font Arial x3, colour 1E2761 x12" — values, not part paths, because the
    operator is deciding whether a RULE is missing, not reading a file."""
    counts = {}
    for _part, kind, val in hits:
        counts[(kind, val)] = counts.get((kind, val), 0) + 1
    return ", ".join("%s %s x%d" % (k, v, n)
                     for (k, v), n in sorted(counts.items(), key=lambda kv: -kv[1]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="Actually upload the restyled templates (default: plan only)")
    ap.add_argument("--retire-plates", action="store_true", dest="retire",
                    help="Replace every background image that this toolkit did "
                         "not generate with the AAIF %s. Retires the hand-made "
                         "plate the decks were built with. Needs --plates and "
                         "--write." % RETIREMENT_PLATE)
    ap.add_argument("--fix-contrast", action="store_true", dest="fix_contrast",
                    help="Repair unreadable text as well as restyling: move runs "
                         "on a dark ground onto the inverse ramp, but only where "
                         "measurement shows it fixes a failure and breaks none. "
                         "Needs --write to upload.")
    ap.add_argument("--contrast", action="store_true",
                    help="Audit TEXT LEGIBILITY instead: report every run whose "
                         "contrast against its background is below WCAG AA. "
                         "Never writes; exit 1 if anything fails.")
    ap.add_argument("--check", action="store_true",
                    help="Audit only: report every off-system value and exit 1 if any. "
                         "Downloads nothing else and never writes.")
    ap.add_argument("--chapter", help="Only files under this exact folder name, "
                                      "matched as a whole path segment and "
                                      "case-insensitively, e.g. 'TemplateCity'")
    ap.add_argument("--restyle-local", metavar="FILE",
                    help="Restyle a local .pptx/.docx/.xlsx; no Drive access")
    ap.add_argument("--backup-dir", help="Where to archive pre-change files "
                                         "(default: ./backups/restyle-<UTC>)")
    ap.add_argument("--plates", metavar="DIR",
                    help="Also append the background plates in DIR to the hero "
                         "decks (build them with aaif_events.agent_art). "
                         "Idempotent: a deck that already has them is skipped.")
    ap.add_argument("--jobs", type=int, default=6,
                    help="Concurrent Drive transfers and folder listings (default: 6)")
    args = ap.parse_args()

    if args.restyle_local:
        return restyle_local(args.restyle_local)
    if args.retire and not args.plates:
        print("--retire-plates needs --plates DIR: the replacement comes from "
              "there, and so does the list of plates that must NOT be replaced.")
        return 2
    if (args.check or args.contrast) and args.write:
        print("--check and --contrast audit and never write; drop --write.")
        return 2
    if args.check and args.contrast:
        print("--check and --contrast are different audits; run one at a time.")
        return 2

    backup_dir = args.backup_dir or os.path.join(
        os.getcwd(), "backups", "restyle-%s"
        % datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if args.write:
        # Fail before touching Drive, not after the first upload.
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as e:
            print("ABORT: cannot create the archive at %s (%s). Nothing was "
                  "written." % (backup_dir, e))
            return 2
        print("Archiving pre-change files to %s" % backup_dir)

    print("Scanning the Community Events tree for templates...")
    entries, chapters, series, with_files, skipped, strays = walk_estate(
        COMMUNITY_ROOT, max(args.jobs, 8))
    scanned = entries
    if args.chapter:
        # Matched against whole path SEGMENTS, not as a substring. A substring
        # match on "Templates" would also select every chapter's "Event
        # Templates (Copy for Each Event)" folder — i.e. quietly sweep the whole
        # estate when the operator asked for one shared folder.
        needle = args.chapter.lower()
        entries = [e for e in entries
                   if needle in [seg.lower() for seg in e["path"].split("/")]]
    if not entries:
        print("No templates matched." if args.chapter else
              "No templates found — has the Community Events tree moved?")
        return 1
    mode = ("CONTRAST AUDIT ONLY." if args.contrast else
            "AUDIT ONLY." if args.check else
            "" if args.write else "PLAN ONLY — nothing will be written.")
    print("Found %d template file(s); %d event copies left alone.  %s"
          % (len(entries), skipped, mode))
    if strays:
        print("\n%d file(s) sit in a template folder but are not templates, and "
              "are left alone.\nIf one of these IS a template, add it to "
              "TEMPLATE_FILES:" % len(strays))
        for p_ in strays:
            print("  - %s" % p_.replace("Community Events/", "", 1))
    print()

    changed = clean = failed = 0
    residue, unchecked_only = [], []
    with tempfile.TemporaryDirectory() as tmpdir, \
            ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for entry, report, err in pool.map(
                lambda e: process(e, tmpdir, args.write, backup_dir, args.check,
                                  args.plates, args.contrast, args.fix_contrast,
                                  args.retire),
                entries):
            if err is not None:
                failed += 1
                print("  FAILED  %s\n            %s" % (entry["path"], err))
                continue
            if not report:
                clean += 1
                continue
            changed += 1
            if args.contrast:
                bad = report["contrast"]
                unchecked = report.get("unchecked", [])
                if not bad:
                    unchecked_only.append((entry["path"], len(unchecked)))
                    changed -= 1
                    clean += 1
                    continue
                invisible = [f for f in bad if f.invisible]
                print("  %-3d issue(s)%s%s  %s"
                      % (len(bad), "  <-- %d INVISIBLE" % len(invisible)
                         if invisible else "",
                         "  (+%d unchecked)" % len(unchecked) if unchecked else "",
                         entry["path"]))
                for f in sorted(bad, key=lambda f: (f.ratio is not None, f.ratio or 0))[:6]:
                    print("        %s" % f)
                if len(bad) > 6:
                    print("        ... and %d more" % (len(bad) - 6))
                continue
            if report["after"]:
                residue.append((entry["path"], report["after"]))
            verb = ("off-system" if args.check else
                    "RESTYLED" if args.write else "would restyle")
            print("  %-12s %s\n                 %s"
                  % (verb, entry["path"],
                     _summarise(report["before"]) or "(already conformant)"))
            if report.get("plates"):
                print("                 + plates: %s" % ", ".join(report["plates"]))
            if report.get("pruned") and report["pruned"][0]:
                faces, dropped = report["pruned"]
                print("                 + fonts: dropped %s from the font table"
                      "%s" % (", ".join(faces),
                              "; removed %d embedded face part(s)" % len(dropped)
                              if dropped else ""))
            if report.get("retired"):
                print("                 + retired legacy plate: %s"
                      % ", ".join(os.path.basename(x) for x in report["retired"]))
            if report.get("rescued", (0,))[0]:
                n, b, a = report["rescued"]
                print("                 + legibility: %d run(s) rescued "
                      "(%d below AA -> %d)" % (n, b, a))
            if report["after"]:
                print("                 REMAINS: %s" % _summarise(report["after"]))

    if args.contrast:
        print("\n%d file(s) hold unreadable text, %d clean, %d failed."
              % (changed, clean, failed))
        if unchecked_only:
            runs = sum(n for _p, n in unchecked_only)
            print("%d file(s) are readable everywhere this can measure, but hold "
                  "%d run(s) it will not score — inherited colours, translucent "
                  "runs, or a background it could not read. Those are NOT "
                  "counted as failures." % (len(unchecked_only), runs))
    elif args.check:
        print("\n%d file(s) off the design system, %d clean, %d failed."
              % (changed, clean, failed))
    else:
        print("\n%d %s, %d already conformant, %d failed."
              % (changed, "restyled" if args.write else "would be restyled",
                 clean, failed))

    # A folder-name match that stops matching looks exactly like a clean estate,
    # so name what the scan could not see instead of letting it read as done.
    attention = []
    for path, hits in residue:
        attention.append("%s still holds %s — ooxml_style has no rule for it"
                         % (path, _summarise(hits)))
    if not args.chapter:
        covered = {e["path"].split("/")[2] for e in scanned
                   if len(e["path"].split("/")) > 2
                   and e["path"].split("/")[1] == CHAPTERS_FOLDER}
        for missing in sorted((chapters - covered) & with_files):
            attention.append("chapter %r holds Office files but contributed none — "
                             "template folder renamed?" % missing)
        covered_series = {e["path"].split("/")[2] for e in scanned
                          if len(e["path"].split("/")) > 2
                          and e["path"].split("/")[1] == SERIES_FOLDER}
        for missing in sorted((series - covered_series) & with_files):
            attention.append("online series %r holds Office files but contributed "
                             "none — template folder renamed?" % missing)
        if not chapters:
            attention.append("no chapter folders found under %r — has it been renamed? "
                             "the per-chapter coverage check is disabled without it"
                             % CHAPTERS_FOLDER)
        for mint in MINTS:
            if not any(("/%s/" % mint) in e["path"] or e["path"].endswith("/" + mint)
                       for e in scanned):
                attention.append("%s was never reached — anything cloned from it "
                                 "would still be off-brand" % mint)
    if attention:
        print("\nATTENTION — the sweep did not cover the whole estate:")
        for line in attention:
            print("  - %s" % line)
    if changed and not args.write and not args.check and not args.contrast:
        print("Re-run with --write to apply.")
    return 1 if (failed or attention
                 or ((args.check or args.contrast) and changed)) else 0


if __name__ == "__main__":
    sys.exit(main())
