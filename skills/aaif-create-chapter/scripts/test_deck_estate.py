#!/usr/bin/env python3
"""Tests for deck_estate.py — the parts the sweeps share rather than restate.

The slide-XML helpers are exercised through `test_backfill_host_footer.py`,
which is where they were written and where their regression history lives. What
is tested here is what has no other home: the per-file download / rewrite /
upload contract, and the estate-coverage lines that decide whether a sweep may
report itself finished.

Plain script: run it, it exits 1 on failure. Every Drive id and folder name
below is invented (`Springfield`, `Shelbyville`), per the no-PII rule in
AGENTS.md — and `cc`'s Drive calls are replaced wholesale, so the suite cannot
reach the network even if a helper starts calling one.
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


# Every Drive call becomes a recorded no-op. `process` is the one function here
# that talks to Drive at all, and the recording is what lets the tests below
# assert an upload did NOT happen — an assertion that would otherwise pass just
# as happily against a function that never ran.
def _download(file_id, out):
    CALLS.append(("download", file_id))
    with open(out, "wb") as f:                    # a minimal, valid zip
        f.write(b"PK\x05\x06" + b"\0" * 18)


def _upload(file_id, path, mime):
    CALLS.append(("upload", file_id))


cc.gws_download = _download
cc.gws_upload = _upload
for _name in ("gws_json", "list_children", "_gws"):
    setattr(cc, _name, (lambda n: lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("test touched the network via cc.%s" % n)))(_name))

ENTRY = {"id": "fileid1", "name": "Slides.pptx",
         "path": "Community Events/Chapters/Springfield/Event Templates/Slides.pptx"}


# ------------------------------------------------------------------ process
def run(write, rewrite):
    """One `process` call in its own scratch dir, with the call log cleared.
    Returns (result, whatever the call left behind in the dir) — the leftovers
    are what proves `process` cleans up after itself, since the dir's own
    context manager would erase the evidence a moment later."""
    del CALLS[:]
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


(entry, report, err), leftovers = run(True, wrote)
eq("a rewritten file is uploaded", [c[0] for c in CALLS], ["download", "upload"])
eq("the report comes back untouched", report, {"ppt/slides/slide3.xml": "changed"})
ok("a clean run reports no error", err is None, str(err))
eq("no scratch file is left behind", leftovers, [])

(_e, _r, _err), _left = run(False, wrote)
eq("a plan run never uploads, even when the rewrite wrote a file",
   [c[0] for c in CALLS], ["download"])

(_e, _r, _err), _left = run(True, reported_only)
eq("a truthy report without a file uploads nothing",
   [c[0] for c in CALLS], ["download"])


def boom(src, dst):
    raise ValueError("the engine broke")


(_e, report, err), leftovers = run(True, boom)
eq("a failing rewrite reports no work", report, {})
ok("the error names its class", err.startswith("ValueError:"), err)
eq("a failed file is not uploaded", [c[0] for c in CALLS], ["download"])
eq("a failed file still cleans up its download", leftovers, [])


def empty_message(src, dst):
    raise MemoryError()


(_e, _r, err) = run(True, empty_message)[0]
ok("an exception with no message is still truthy", bool(err), repr(err))


def not_a_zip(file_id, out):
    CALLS.append(("download", file_id))
    with open(out, "wb") as f:
        f.write(b'{"error": {"code": 404}}')      # gws writes this at exit 0


cc.gws_download = not_a_zip
(_e, _r, err) = run(True, wrote)[0]
ok("an error body is caught as one, not as broken OOXML",
   "not a .pptx" in (err or ""), repr(err))
cc.gws_download = _download


# -------------------------------------------------------- coverage_attention
def tpl(path):
    return {"id": path, "name": "Slides.pptx", "path": path}


CH = "Community Events/Chapters/%s/Event Templates/Slides.pptx"
SERIES_PATH = "Community Events/Online/%s/Event Template/Slides.pptx"
FULL = [tpl(CH % "Springfield"), tpl(CH % de.TEMPLATE_CITY),
        tpl(SERIES_PATH % "Reading Group")]

eq("a complete sweep has nothing to report",
   de.coverage_attention(FULL, {"Springfield", de.TEMPLATE_CITY},
                         {"Reading Group"}, {"Springfield", "Reading Group"}, "note"),
   [])

missed = de.coverage_attention(
    FULL, {"Springfield", "Shelbyville", de.TEMPLATE_CITY}, {"Reading Group"},
    {"Springfield", "Shelbyville", "Reading Group"}, "note")
eq("a chapter with decks but no template is named", len(missed), 1)
ok("and named as a possible rename", "Shelbyville" in missed[0]
   and "renamed" in missed[0], missed[0])

# A chapter that simply holds no decks is not a rename and must not cry wolf.
eq("a deckless chapter is not reported",
   de.coverage_attention(FULL, {"Springfield", "Shelbyville", de.TEMPLATE_CITY},
                         {"Reading Group"}, {"Springfield", "Reading Group"}, "note"),
   [])

series_missed = de.coverage_attention(
    FULL, {"Springfield", de.TEMPLATE_CITY}, {"Reading Group", "MCP Launch"},
    {"Springfield", "Reading Group", "MCP Launch"}, "note")
eq("an online series with decks but no template is named", len(series_missed), 1)
ok("and named as a series", "online series" in series_missed[0]
   and "MCP Launch" in series_missed[0], series_missed[0])

no_city = de.coverage_attention([tpl(CH % "Springfield")], {"Springfield"},
                                set(), {"Springfield"},
                                "new chapters keep the old thing")
eq("an unswept TemplateCity is reported", len(no_city), 1)
ok("with the caller's own consequence spelled out",
   de.TEMPLATE_CITY in no_city[0] and "new chapters keep the old thing" in no_city[0],
   no_city[0])

no_chapters = de.coverage_attention([], set(), set(), set(), "note")
ok("an empty Chapters folder disables the check loudly",
   any("has it been renamed" in line for line in no_chapters), str(no_chapters))


# --------------------------------------------------------------------- owners
eq("owners reads the chapter name out of a path",
   de.owners(FULL, de.CHAPTERS_FOLDER), {"Springfield", de.TEMPLATE_CITY})
eq("owners ignores a path too short to have one",
   de.owners([tpl("Community Events/Chapters")], de.CHAPTERS_FOLDER), set())


if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("deck_estate: all checks passed")
