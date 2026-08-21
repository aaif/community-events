#!/usr/bin/env python3
"""Create a new AAIF online event series by cloning the TemplateSeries folder and
rebranding every Office file from "San Francisco" to the new series.

Online series (e.g. a Reading Group, a Paper Club) live under the top-level
**Online/** folder, NOT under Chapters/. They are the online-event sibling of a
city chapter: same folder shape (Event Tracker, Attendee CRM, Event Template/
design assets, Banners/), but the Event Tracker is the no-venue "online" runbook
(platform / join link / recording / chat-Q&A instead of venue / A-V / door).

Two replacement tokens are swapped (event content like dates/speakers/the example
block is left untouched - organizers fill that per-event using the aaif-* content
skills in this repo, e.g. aaif-speaker-bio / aaif-luma-description):

  1. Series name "San Francisco" / "SAN FRANCISCO" -> new series (case matched)
                 plus the "SF" abbreviation (AAIF SF, ...) -> new series, upper in
                 all-caps contexts and Title-cased in prose.
  2. Luma slug   aaif-sanfrancisco / aaif-sf        -> aaif-<newslug>

The TemplateSeries master is already series-shaped (no "Chapter" wording; the
identity blurb is a [bracketed] placeholder for the organizer to fill).

Usage:
  # Dry run - show what would happen, create nothing:
  python create_series.py --series "Reading Group" --dry-run

  # Create the series in Drive:
  python create_series.py --series "Reading Group"

  # Override the Luma slug:
  python create_series.py --series "Reading Group" --slug readinggroup

  # Test the text engine on a local folder of .pptx/.docx/.xlsx (no Drive):
  python create_series.py --series "Reading Group" --rebrand-local ./somedir
"""
import argparse, html, json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.request, zipfile

ONLINE_PARENT   = "1g2vHrqDHfh9wBkDJryJIl8wqXA4J-d4i"   # the top-level "Online" Drive folder
TEMPLATE_FOLDER = "1M15wzKvQqd_jQz5cG16NO_YcbWU3EH1j"   # the "TemplateSeries" folder
SOURCE_NAME, SOURCE_UPPER = "San Francisco", "SAN FRANCISCO"

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER = "application/vnd.google-apps.folder"
MIME_BY_EXT = {".pptx": PPTX, ".docx": DOCX, ".xlsx": XLSX}

# ----------------------------------------------------------------------------
# Text rebranding engine (pure, unit-testable) - identical to aaif-create-chapter
# ----------------------------------------------------------------------------
def slugify(series):
    s = unicodedata.normalize("NFKD", series).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())

def transform_text(text, name, upper, newslug):
    """Apply all series/slug replacements to one piece of *plain* text."""
    # 1. Luma slug (before the bare-SF pass, since it owns the '-SF' form)
    def luma(m):
        return ("AAIF-" + newslug.upper()) if m.group(0).isupper() else ("aaif-" + newslug)
    text = re.sub(r"(?i)aaif-(?:sanfrancisco|sf)\b", luma, text)
    # 2. Full series name
    text = text.replace(SOURCE_NAME, name).replace(SOURCE_UPPER, upper)
    # 3. Bare "SF" abbreviation - case decided by surrounding words
    def sf(m):
        after = text[m.end(): m.end() + 30]
        before = text[max(0, m.start() - 30): m.start()]
        nxt = re.search(r"[A-Za-z]{2,}", after)
        nxt = nxt.group(0) if nxt else ""
        prevs = re.findall(r"[A-Za-z]{2,}", before)
        prev = prevs[-1] if prevs else ""
        up = nxt.isupper() if nxt else (prev.isupper() if prev else False)
        return upper if up else name
    text = re.sub(r"\bSF\b", sf, text)
    return text

def _process_paragraphs(xml, ptag, ttag, tx):
    """Concatenate the text runs in each paragraph, transform, and write the
    result back into the first run (emptying the rest) so formatting and any
    run-splitting are preserved."""
    pre = re.compile(r"<%s\b[^>]*>.*?</%s>" % (re.escape(ptag), re.escape(ptag)), re.S)
    tre = re.compile(r"(<%s\b[^>]*>)(.*?)(</%s>)" % (re.escape(ttag), re.escape(ttag)), re.S)

    def do_para(pm):
        block = pm.group(0)
        runs = list(tre.finditer(block))
        if not runs:
            return block
        concat = "".join(html.unescape(r.group(2)) for r in runs)
        new = tx(concat)
        if new == concat:
            return block
        new_esc = html.escape(new, quote=False)
        out, last, first = [], 0, True
        for r in runs:
            out.append(block[last:r.start()])
            open_, _txt, close = r.group(1), r.group(2), r.group(3)
            if first:
                if "xml:space" not in open_:
                    open_ = open_[:-1] + ' xml:space="preserve">'
                out.append(open_ + new_esc + close)
                first = False
            else:
                out.append(open_ + close)
            last = r.end()
        out.append(block[last:])
        return "".join(out)

    return pre.sub(do_para, xml)

def rebrand_part(part_name, data, name, upper, newslug):
    """Return rebranded bytes for one OOXML part (or the original if unchanged)."""
    tx = lambda s: transform_text(s, name, upper, newslug)
    try:
        xml = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if re.match(r"ppt/slides/slide\d+\.xml$", part_name):
        xml = _process_paragraphs(xml, "a:p", "a:t", tx)
    elif part_name == "word/document.xml":
        xml = _process_paragraphs(xml, "w:p", "w:t", tx)
    elif part_name == "xl/sharedStrings.xml":
        xml = _process_paragraphs(xml, "si", "t", tx)
    elif re.match(r"xl/worksheets/sheet\d+\.xml$", part_name):
        # Cells can hold inline strings (<is><t>...</t></is>) instead of a
        # sharedStrings.xml reference — e.g. the CRM's "Guide" sheet title.
        xml = _process_paragraphs(xml, "is", "t", tx)
    elif part_name in ("docProps/core.xml", "docProps/app.xml"):
        # metadata: labelled "AAIF SF" -> "AAIF <UPPER>"
        xml = xml.replace("AAIF SF", "AAIF " + upper)
        xml = xml.replace(SOURCE_NAME, name).replace(SOURCE_UPPER, upper)
        xml = re.sub(r"(?i)aaif-(?:sanfrancisco|sf)\b",
                     lambda m: ("AAIF-" + newslug.upper()) if m.group(0).isupper() else ("aaif-" + newslug), xml)
    elif part_name.endswith(".rels"):
        # hyperlink targets to the Luma page
        xml = re.sub(r"(?i)aaif-(?:sanfrancisco|sf)\b",
                     lambda m: ("AAIF-" + newslug.upper()) if m.group(0).isupper() else ("aaif-" + newslug), xml)
    else:
        return data
    return xml.encode("utf-8")

def _rewrite_zip(path, transform):
    """Rewrite an OOXML zip in place, mapping each member's bytes through
    transform(name, data) -> bytes while preserving its ZipInfo, then validating
    the repackaged zip with testzip(). Returns the number of members whose bytes
    changed. On a bad repack, removes the temp file and raises (original intact)."""
    tmp = path + ".new"
    changed = 0
    try:
        with zipfile.ZipFile(path) as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = zin.read(it.filename)
                new = transform(it.filename, data)
                if new != data:
                    changed += 1
                zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
                zi.compress_type = it.compress_type
                zi.external_attr = it.external_attr
                zout.writestr(zi, new)
        with zipfile.ZipFile(tmp) as zt:   # close the handle before os.replace
            if zt.testzip() is not None:
                raise RuntimeError("repackaged zip failed validation: " + path)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):   # cleared on success by os.replace; else a leftover
            os.remove(tmp)
    return changed

def rebrand_file(path, name, upper, newslug):
    """Rewrite an .pptx/.docx/.xlsx in place. Returns number of parts changed."""
    return _rewrite_zip(path, lambda n, d: rebrand_part(n, d, name, upper, newslug))

def residual_tokens(path):
    """List any stale San Francisco / SF / aaif-sf tokens still present. The city
    name and slug are matched case-insensitively; the bare "SF" abbreviation is
    matched case-SENSITIVELY (uppercase only) — theme/font XML legitimately holds
    lowercase "sf" tokens that are not stale brand text."""
    hits = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith((".xml", ".rels")):
                continue
            d = z.read(n)
            for pat, flags in ((rb"San Francisco", re.I),
                               (rb"aaif-sf(?![a-z])", re.I),
                               (rb"\bSF\b", 0)):
                if re.search(pat, d, flags):
                    hits.append((n.split("/")[-1], pat.decode("latin1")))
    return hits

# ----------------------------------------------------------------------------
# Drive helpers (via the gws CLI)
# ----------------------------------------------------------------------------
_TRANSIENT = ("timed out", "internalError", "HTTP request failed",
              "Connection", "temporarily", "rateLimit", "userRateLimit",
              "backendError", "503", "500", "502")

def _gws(cmd, cwd=None, retries=5):
    """Run a gws command, retrying transient network/server errors."""
    for i in range(retries):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < retries - 1 and any(k in msg for k in _TRANSIENT):
            time.sleep(2 * (i + 1))
            continue
        raise RuntimeError("gws failed (%s): %s" % (r.returncode, msg.strip()[:400]))

def gws_json(*args, params=None, body=None):
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = _gws(cmd)
    # Split on "\n" only — NOT splitlines(), which also splits on U+2028 and
    # friends INSIDE JSON string values, corrupting them when rejoined.
    s = "\n".join(l for l in out.split("\n") if "keyring backend" not in l).strip()
    if not s:
        # Empty-but-successful stdout would silently become {} -> an empty file
        # list -> a subtree that fails to clone while the run still says "Done".
        raise RuntimeError("gws produced no JSON output for: %s" % " ".join(args))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise RuntimeError("gws returned non-JSON output for %s: %s" % (" ".join(args), s[:200]))

def gws_download(file_id, out):
    # gws rejects --output paths outside its cwd, so run it in the file's dir.
    d = os.path.dirname(out) or "."
    _gws(["gws", "drive", "files", "get", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True, "alt": "media"}),
          "--output", os.path.basename(out)], cwd=d)

def gws_upload(file_id, path, mime):
    d = os.path.dirname(path) or "."
    _gws(["gws", "drive", "files", "update", "--params",
          json.dumps({"fileId": file_id, "supportsAllDrives": True}),
          "--upload", os.path.basename(path), "--upload-content-type", mime], cwd=d)

def list_children(folder_id):
    res = gws_json("drive", "files", "list", params={
        "q": "'%s' in parents and trashed=false" % folder_id,
        "fields": "files(id,name,mimeType)", "pageSize": 1000,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    return res.get("files", [])

def create_folder(name, parent):
    return gws_json("drive", "files", "create",
                    params={"supportsAllDrives": True},
                    body={"name": name, "mimeType": FOLDER, "parents": [parent]})["id"]

def copy_file(file_id, name, parent):
    return gws_json("drive", "files", "copy",
                    params={"fileId": file_id, "supportsAllDrives": True},
                    body={"name": name, "parents": [parent]})["id"]

def luma_status(slug):
    """Return 'live' (HTTP 200), 'absent' (HTTP 404), or 'unknown' (could not
    verify: timeout, DNS/SSL, 403/429/5xx). Never report a hard 404 for a
    failure we could not actually confirm."""
    # Luma rejects HEAD and bare urllib UAs with 403; use GET + a browser UA.
    req = urllib.request.Request("https://luma.com/aaif-" + slug, method="GET",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return "live" if r.status == 200 else "unknown"
    except urllib.error.HTTPError as e:
        return "absent" if e.code == 404 else "unknown"
    except Exception:
        return "unknown"

# ----------------------------------------------------------------------------
def clone_and_rebrand(folder_id, parent, name, ctx, indent="", existing_id=None):
    """Recursively copy `folder_id` into `parent` as `name`, rebranding files.

    Under --resume, `existing_id` is an already-created target folder: it is
    entered instead of recreated, its children are listed by (rebranded) name,
    name-matching children are skipped, and only missing items are cloned — so
    resuming into a fully-cloned folder is a no-op."""
    if existing_id:
        new_id = existing_id
        print("%s= %s/ (exists, resumed)" % (indent, name))
    else:
        new_id = create_folder(name, parent)
        print("%s+ %s/" % (indent, name))
    have = {c["name"]: c for c in list_children(new_id)} if existing_id else {}
    for child in list_children(folder_id):
        cname, cid, mime = child["name"], child["id"], child["mimeType"]
        # Names (not just file content) can carry the source city, e.g. "San
        # Francisco CRM.xlsx" -> "Reading Group CRM.xlsx" — same transform as content.
        new_cname = transform_text(cname, ctx["name"], ctx["upper"], ctx["slug"])
        hit = have.get(new_cname)
        if mime == FOLDER:
            clone_and_rebrand(cid, new_id, new_cname, ctx, indent + "  ",
                              existing_id=hit["id"] if hit and hit["mimeType"] == FOLDER else None)
        elif hit:
            print("%s  - %s (exists, skipped)" % (indent, new_cname))
        else:
            copy_id = copy_file(cid, new_cname, new_id)
            ext = os.path.splitext(cname)[1].lower()
            if ext in MIME_BY_EXT:
                tmp = os.path.join(ctx["tmp"], "f" + copy_id + ext)
                try:
                    gws_download(copy_id, tmp)
                    n = rebrand_file(tmp, ctx["name"], ctx["upper"], ctx["slug"])
                    if n:
                        gws_upload(copy_id, tmp, MIME_BY_EXT[ext])
                    left = residual_tokens(tmp)
                    if left:
                        ctx["residuals"].append((new_cname, left))
                    flag = "  !! residual %s" % left if left else ""
                    print("%s  - %s (%d parts)%s" % (indent, new_cname, n, flag))
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
            else:
                print("%s  - %s (copied)" % (indent, new_cname))
    return new_id

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, help='Series display name, e.g. "Reading Group" or "Online Reading Group"')
    ap.add_argument("--slug", help="Luma slug override (default: series, lowercased, no spaces)")
    ap.add_argument("--dry-run", action="store_true", help="Plan only; create nothing")
    ap.add_argument("--resume", action="store_true",
                    help="Enter an existing target folder instead of aborting: skip "
                         "name-matching children and clone only what's missing "
                         "(recovery for a failed/partial run)")
    ap.add_argument("--rebrand-local", metavar="DIR",
                    help="Rebrand .pptx/.docx/.xlsx in a local dir (no Drive); for testing")
    a = ap.parse_args()

    name = a.series.strip()
    upper = name.upper()
    slug = a.slug.strip().lower() if a.slug else slugify(name)
    print("Series: %s" % name)
    print("Upper : %s" % upper)
    print("Slug  : aaif-%s  ->  https://luma.com/aaif-%s" % (slug, slug))

    if a.rebrand_local:
        residual_any = False
        for root, _d, files in os.walk(a.rebrand_local):
            for f in files:
                if os.path.splitext(f)[1].lower() in MIME_BY_EXT:
                    p = os.path.join(root, f)
                    n = rebrand_file(p, name, upper, slug)
                    left = residual_tokens(p)
                    if left:
                        residual_any = True
                    print("  %s: %d parts%s" % (f, n, ("  !! " + str(left)) if left else ""))
        if residual_any:
            sys.exit("FAIL: residual source tokens remain after rebrand (see !! above).")
        return

    status = luma_status(slug)
    print("Luma  : %s" % {
        "live": "LIVE (200)",
        "absent": "NOT LIVE (404) - create the page at luma.com, or pass --slug",
        "unknown": "COULD NOT VERIFY - network error reaching luma.com; check aaif-%s manually" % slug,
    }[status])

    existing = [c for c in list_children(ONLINE_PARENT)
                if c["name"].lower() == name.lower() and c["mimeType"] == FOLDER]
    resume_id = None
    if existing:
        if a.resume:
            resume_id = existing[0]["id"]
            print("Resume: entering existing folder %s — children already present "
                  "are skipped; only missing items are cloned." % resume_id)
        else:
            sys.exit("ABORT: an Online series folder named %r already exists (%s). "
                     "To fill in missing items from a failed/partial run, re-run "
                     "with --resume." % (name, existing[0]["id"]))

    if a.dry_run:
        if resume_id:
            print("\n[dry-run] Would resume into the existing %r folder under Online "
                  "and clone only the missing items." % name)
        else:
            print("\n[dry-run] Would clone TemplateSeries -> %r under Online and rebrand all files." % name)
        if status == "absent":
            print("[dry-run] WARNING: Luma page aaif-%s is not live yet." % slug)
        elif status == "unknown":
            print("[dry-run] NOTE: could not verify the Luma page aaif-%s; check it manually." % slug)
        return

    ctx = {"name": name, "upper": upper, "slug": slug, "residuals": [],
           "tmp": os.path.join(os.environ.get("TMPDIR", "/tmp"), "aaif_series")}
    os.makedirs(ctx["tmp"], exist_ok=True)
    print()
    new_id = clone_and_rebrand(TEMPLATE_FOLDER, ONLINE_PARENT, name, ctx,
                               existing_id=resume_id)
    print("\nDone. New series folder id: %s" % new_id)
    print("https://drive.google.com/drive/folders/%s" % new_id)
    print("REMINDER: fill the [bracketed] series blurb in Event Tracker.docx (the template ships a placeholder).")
    if status == "absent":
        print("REMINDER: create the Luma page at https://luma.com/aaif-%s (it is not live yet)." % slug)
    elif status == "unknown":
        print("REMINDER: could not verify the Luma page aaif-%s; check it manually at luma.com." % slug)
    if ctx["residuals"]:
        print("\nWARNING: %d file(s) still contain source tokens after rebrand:" % len(ctx["residuals"]))
        for fn, toks in ctx["residuals"]:
            print("  - %s: %s" % (fn, toks))
        sys.exit("The new folder is NOT clean - fix the template or rebrand engine and re-run.")

if __name__ == "__main__":
    main()
