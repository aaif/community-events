#!/usr/bin/env python3
"""Tests for deck_estate.py — the parts the sweeps share rather than restate.

The shape-level helpers that predate this module are exercised through
`test_backfill_host_footer.py`, where they were written and where their
regression history lives. What is tested here is what has no other home: the
estate walk, the per-file download / rewrite / upload contract, the splice that
keeps a multi-shape edit from corrupting a deck, and the coverage lines that
decide whether a sweep may report itself finished.

Plain script: run it, it collects every failure and exits 1 if there were any.
Every Drive id and folder name below is invented (`Springfield`,
`Shelbyville`), per the no-PII rule in AGENTS.md — and `cc`'s Drive calls are
replaced wholesale, so the suite cannot reach the network even if a helper
starts calling one.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import create_chapter as cc
import deck_estate as de

FAILED = []
CALLS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILED.append("%s%s" % (name, "\n     " + detail if detail else ""))


def eq(name, got, want):
    if got != want:
        FAILED.append("%s\n     got:  %r\n     want: %r" % (name, got, want))


# Every Drive call becomes a recorded no-op. The recording is what lets the
# tests below assert an upload did NOT happen — an assertion that would
# otherwise pass just as happily against a function that never ran — and WHAT
# was uploaded, since uploading the download instead of the rewrite would
# overwrite a live template with its own unmodified bytes.
def _download(file_id, out):
    CALLS.append(("download", file_id, None, None))
    with open(out, "wb") as f:                    # a minimal, valid zip
        f.write(b"PK\x05\x06" + b"\0" * 18)


def _upload(file_id, path, mime):
    CALLS.append(("upload", file_id, os.path.basename(path), mime))


#: Drive's answers to the two `files.get` probes de.upload makes around an
#: upload. A test can make the second equal the first — Drive showing the file
#: unchanged after a "successful" upload.
TIMES = []


def _gws_json(*args, **kwargs):
    CALLS.append(("get", kwargs.get("params", {}).get("fileId"), None, None))
    if TIMES:
        return TIMES.pop(0)
    # Distinct stamps by default: Drive moved the file, which is the happy path.
    # A test that wants the unhappy one queues two identical stamps in TIMES.
    n = len([c for c in CALLS if c[0] == "get"])
    return {"modifiedTime": "2026-01-01T00:00:0%dZ" % n, "size": "1"}


cc.gws_download = _download
cc.gws_upload = _upload
cc.gws_json = _gws_json
for _name in ("list_children", "_gws"):
    setattr(cc, _name, (lambda n: lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("test touched the network via cc.%s" % n)))(_name))

ENTRY = de.Entry("fileid1", "Slides.pptx",
                 "Community Events/Chapters/Springfield/Event Templates/Slides.pptx",
                 "Chapters", "Springfield")


# ------------------------------------------------------------------ process
def run(write, rewrite, times=()):
    """One `process` call in its own scratch dir, with the call log cleared.
    Returns (result, whatever the call left behind in the dir) — the leftovers
    are what proves `process` cleans up after itself, since the dir's own
    context manager would erase the evidence a moment later."""
    del CALLS[:]
    del TIMES[:]
    TIMES.extend(times)
    with tempfile.TemporaryDirectory() as tmp:
        result = de.process(ENTRY, tmp, write, rewrite)
        return result, os.listdir(tmp)


def wrote(src, dst):
    with open(dst, "wb") as f:
        f.write(open(src, "rb").read())
    return {"ppt/slides/slide3.xml": "changed"}


def reported_only(src, dst):
    """Reports work to do without producing `dst` — what a plan run does."""
    return {"ppt/slides/slide3.xml": "would change"}


def kinds():
    return [c[0] for c in CALLS]


(entry, report, err), leftovers = run(True, wrote)
eq("a rewritten file is uploaded", kinds(), ["download", "get", "upload", "get"])
eq("the report comes back untouched", report, {"ppt/slides/slide3.xml": "changed"})
ok("a clean run reports no error", err is None, str(err))
eq("no scratch file is left behind", leftovers, [])
up = [c for c in CALLS if c[0] == "upload"][0]
eq("the REWRITE is uploaded, not the download", up[2], "out-fileid1.pptx")
eq("and as a .pptx", up[3], cc.PPTX)
eq("under the entry's own Drive id", up[1], "fileid1")

# Drive reporting the file unchanged after a "successful" upload is the failure
# gws's exit code cannot show: the sweep would print REWRITTEN over a stale deck.
stamp = {"modifiedTime": "2026-01-01T00:00:00Z", "size": "10"}
(_e, report, err), _left = run(True, wrote, times=[stamp, stamp])
ok("an upload Drive did not take is an error",
   err is not None and err.startswith("UPLOAD"), str(err))
ok("and says this file's state is unknown",
   "UNKNOWN" in (err or "") and "unchanged" in (err or ""), str(err))
eq("the rewrite's own report survives an upload failure",
   report, {"ppt/slides/slide3.xml": "changed"})

(_e, _r, _err), _left = run(False, wrote)
eq("a plan run never uploads, even when the rewrite wrote a file",
   kinds(), ["download"])

(_e, _r, _err), _left = run(True, reported_only)
eq("a truthy report without a file uploads nothing", kinds(), ["download"])


def boom(src, dst):
    raise ValueError("the engine broke")


(_e, report, err), leftovers = run(True, boom)
eq("a failing rewrite reports no work", report, None)
ok("a rewrite failure is named as a script bug, not a Drive problem",
   err.startswith("REWRITE ValueError:") and "re-running will not help" in err, err)
eq("a failed file is not uploaded", kinds(), ["download"])
eq("a failed file still cleans up its download", leftovers, [])


def empty_message(src, dst):
    raise MemoryError()


(_e, _r, err) = run(True, empty_message)[0]
ok("an exception with no message is still truthy", bool(err), repr(err))

long_tail = "keyring noise " * 40 + "insufficientFilePermissions"
(_e, _r, err) = run(True, lambda s, d: (_ for _ in ()).throw(
    RuntimeError(long_tail)))[0]
ok("a long message keeps the Google reason at its end",
   "insufficientFilePermissions" in err, err[-120:])


def no_file(file_id, out):
    CALLS.append(("download", file_id, None, None))


cc.gws_download = no_file
(_e, _r, err) = run(True, wrote)[0]
ok("a download that wrote nothing is caught as a download failure",
   err.startswith("DOWNLOAD") and "wrote no file" in err, repr(err))


def not_a_zip(file_id, out):
    CALLS.append(("download", file_id, None, None))
    with open(out, "wb") as f:
        f.write(b'{"error": {"code": 404}}')      # gws writes this at exit 0


cc.gws_download = not_a_zip
(_e, _r, err) = run(True, wrote)[0]
ok("an error body is caught as one, not as broken OOXML",
   "not a .pptx" in (err or ""), repr(err))
cc.gws_download = _download


# ------------------------------------------------------------- walk_templates
#: A synthetic estate. Only `list_children` sees it, so nothing here reaches
#: Drive, and every id and name is invented.
FOLDER, PPTX = cc.FOLDER, cc.PPTX
TREE = {
    "root": [{"id": "ch", "name": "Chapters", "mimeType": FOLDER},
             {"id": "on", "name": "Online", "mimeType": FOLDER}],
    "ch": [{"id": "spring", "name": "Springfield", "mimeType": FOLDER},
           {"id": "shelby", "name": "Shelbyville", "mimeType": FOLDER},
           {"id": "quiet", "name": "Quietville", "mimeType": FOLDER}],
    # An online series deliberately sharing a chapter's name: the two must not
    # cover for each other in the coverage checks below.
    "on": [{"id": "series", "name": "Springfield", "mimeType": FOLDER}],
    # Springfield: a template folder, plus an archive that holds a deck but is
    # not a template folder.
    "spring": [{"id": "spring-tpl", "name": "Event Templates (Copy for Each Event)",
                "mimeType": FOLDER},
               {"id": "spring-arch", "name": "Archive", "mimeType": FOLDER}],
    "spring-tpl": [{"id": "deck1", "name": "Slides.pptx", "mimeType": PPTX}],
    "spring-arch": [{"id": "deck-old", "name": "2025 Slides.pptx", "mimeType": PPTX}],
    # Shelbyville's template folder was "renamed": it holds a deck, outside any
    # folder the template regex matches.
    "shelby": [{"id": "shelby-misc", "name": "Decks", "mimeType": FOLDER}],
    "shelby-misc": [{"id": "deck2", "name": "Slides.pptx", "mimeType": PPTX}],
    "quiet": [],                                  # a chapter with no decks at all
    "series": [{"id": "series-tpl", "name": "Event Template", "mimeType": FOLDER}],
    # The same Drive file reachable under two parents: two workers would share a
    # scratch filename and race, so the walk must return it once.
    "series-tpl": [{"id": "deck1", "name": "Slides.pptx", "mimeType": PPTX},
                   {"id": "deck3", "name": "Slides.pptx", "mimeType": PPTX}],
}


def no_network(fid):
    raise AssertionError("test touched the network via cc.list_children")


cc.list_children = lambda fid: TREE[fid]
scan = de.walk_templates("root", jobs=2)
cc.list_children = no_network

eq("only decks inside a template folder are swept",
   sorted(e.id for e in scan.templates), ["deck1", "deck3"])
eq("a Drive id reachable twice is returned once",
   len([e for e in scan.templates if e.id == "deck1"]), 1)
eq("each entry carries its section and owner, parsed once",
   sorted((e.section, e.owner) for e in scan.templates),
   [("Chapters", "Springfield"), ("Online", "Springfield")])
eq("chapters are the direct children of Chapters",
   scan.chapters, {"Springfield", "Shelbyville", "Quietville"})
eq("series are the direct children of Online", scan.series, {"Springfield"})
eq("a deck anywhere marks its owner, section-qualified",
   scan.with_decks, {("Chapters", "Springfield"), ("Chapters", "Shelbyville"),
                     ("Online", "Springfield")})
eq("a clean walk lists everything", scan.unlisted, [])

missed = de.coverage_attention(scan, "note")
ok("Shelbyville, which holds a deck outside any template folder, is reported",
   any("Shelbyville" in line and "renamed" in line for line in missed), str(missed))
ok("Quietville, which holds no decks at all, is not",
   not any("Quietville" in line for line in missed), str(missed))
# The section-qualified sets earn themselves here: strip the chapter's template
# and the series of the same name must not satisfy the chapter's check.
series_only = scan._replace(templates=[e for e in scan.templates
                                       if e.section == de.SERIES_FOLDER])
ok("a series' template does not cover for the chapter sharing its name",
   any("chapter 'Springfield'" in line
       for line in de.coverage_attention(series_only, "note")),
   str(de.coverage_attention(series_only, "note")))


def broken(fid):
    if fid == "ch":
        raise RuntimeError("Drive said 500")
    return TREE[fid]


cc.list_children = broken
partial = de.walk_templates("root", jobs=2)
cc.list_children = no_network
eq("a folder that would not list is recorded, not fatal", len(partial.unlisted), 1)
ok("and named in the attention lines",
   any("could not be listed" in line
       for line in de.coverage_attention(partial, "note")),
   str(de.coverage_attention(partial, "note")))


# -------------------------------------------------------- coverage_attention
def tpl(path):
    parts = path.split("/")
    return de.Entry(path, "Slides.pptx", path, parts[1], parts[2])


CH = "Community Events/Chapters/%s/Event Templates/Slides.pptx"
SERIES_PATH = "Community Events/Online/%s/Event Template/Slides.pptx"
FULL = [tpl(CH % "Springfield"), tpl(CH % de.TEMPLATE_CITY),
        tpl(SERIES_PATH % "Reading Group")]
COMPLETE = de.Scan(FULL, {"Springfield", de.TEMPLATE_CITY}, {"Reading Group"},
                   {("Chapters", "Springfield"), ("Online", "Reading Group")}, [])

eq("a complete sweep has nothing to report",
   de.coverage_attention(COMPLETE, "note"), [])

series_missed = de.coverage_attention(
    COMPLETE._replace(series={"Reading Group", "MCP Launch"},
                      with_decks=COMPLETE.with_decks | {("Online", "MCP Launch")}),
    "note")
eq("an online series with decks but no template is named", len(series_missed), 1)
ok("and named as a series",
   "online series" in series_missed[0] and "MCP Launch" in series_missed[0],
   series_missed[0])

no_chapters = de.coverage_attention(de.Scan([], set(), set(), set(), []), "note")
ok("an empty Chapters folder disables the check loudly",
   any("has it been renamed" in line for line in no_chapters), str(no_chapters))

# The TemplateCity check is by owner, not by substring: a folder merely NAMED
# like it must not satisfy the one check this module calls the most important.
lookalike = COMPLETE._replace(
    templates=[tpl(CH % "Springfield"), tpl(CH % (de.TEMPLATE_CITY + " Archive"))],
    chapters={"Springfield", de.TEMPLATE_CITY + " Archive"},
    series=set(), with_decks={("Chapters", "Springfield")})
missing_city = de.coverage_attention(lookalike, "new chapters keep the old thing")
eq("a TemplateCity lookalike does not satisfy the check", len(missing_city), 1)
ok("and the caller's own consequence is spelled out",
   de.TEMPLATE_CITY in missing_city[0]
   and "new chapters keep the old thing" in missing_city[0], missing_city[0])


# --------------------------------------------------------------------- owners
eq("owners reads the chapter names the scan reached",
   de.owners(FULL, de.CHAPTERS_FOLDER), {"Springfield", de.TEMPLATE_CITY})
eq("and keeps the sections apart",
   de.owners(FULL, de.SERIES_FOLDER), {"Reading Group"})


# ---------------------------------------------------- shape_text / font_size
ESC = "\x1b[2K\rREWRITTEN everything"
ok("control characters in deck copy never reach the caller",
   "\x1b" not in de.shape_text("<a:t>%s</a:t>" % ESC),
   repr(de.shape_text("<a:t>%s</a:t>" % ESC)))
eq("and what is left is the printable text",
   de.shape_text("<a:t>%s</a:t>" % ESC), "[2KREWRITTEN everything")
eq("a run that declares no size can be told from one that does",
   de.font_size('<a:rPr b="1"/>', default=None), None)
eq("and still reports the fallback when asked for one",
   de.font_size('<a:rPr b="1"/>'), 1100)
eq("unmeasured counts text shapes with no geometry of their own",
   de.unmeasured(de.shapes(
       '<p:sp><p:spPr><a:xfrm><a:off x="1" y="2"/><a:ext cx="3" cy="4"/></a:xfrm>'
       '</p:spPr><p:txBody><a:t>placed</a:t></p:txBody></p:sp>'
       '<p:sp><p:spPr/><p:txBody><a:t>inherited</a:t></p:txBody></p:sp>')), 1)


# --------------------------------------------------------------------- splice
XML = ('<p:spTree>'
       '<p:sp><p:spPr/><p:txBody><a:t>one</a:t></p:txBody></p:sp>'
       '<p:sp><p:spPr/><p:txBody><a:t>two</a:t></p:txBody></p:sp>'
       '<p:sp><p:spPr/><p:txBody><a:t>three</a:t></p:txBody></p:sp>'
       '</p:spTree>')
shp = de.shapes(XML)
one = de.splice(XML, shp,
                {1: "<p:sp><p:spPr/><p:txBody><a:t>TWO</a:t></p:txBody></p:sp>"})
eq("a single splice replaces just that shape",
   [s.text for s in de.shapes(one)], ["one", "TWO", "three"])
# Why this helper exists: every span indexes the ORIGINAL string, so editing two
# shapes without an ascending cursor walk duplicates or truncates XML that still
# zips, still validates and still uploads.
two = de.splice(XML, shp, {
    0: "<p:sp><p:spPr/><p:txBody><a:t>ONE</a:t></p:txBody></p:sp>",
    2: "<p:sp><p:spPr/><p:txBody><a:t>THREE</a:t></p:txBody></p:sp>"})
eq("two splices leave the untouched shape intact and lose nothing",
   [s.text for s in de.shapes(two)], ["ONE", "two", "THREE"])
eq("an empty body deletes a shape",
   [s.text for s in de.shapes(de.splice(XML, shp, {1: ""}))], ["one", "three"])


if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("deck_estate: all checks passed")
