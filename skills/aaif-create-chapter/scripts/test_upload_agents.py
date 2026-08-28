#!/usr/bin/env python3
"""Unit tests for the per-chapter icon upload.

This script was the last one in `aaif-create-chapter/scripts/` with no test
beside it, and it is the one that writes the most files: every chapter in the
estate times an agent, ten generics and six logo files. Two of its properties
are the ones worth pinning, because both fail QUIETLY:

  (a) **it must be idempotent.** The whole reason it can be re-run after adding
      one chapter is that a file whose bytes already match is left alone. If
      the digest comparison stops working, a full run silently re-uploads the
      whole estate and every organizer sees their Icons folder change date.

  (b) **a chapter with no generated art must FAIL, not upload a partial set.**
      `art_for` returns the generics and the logos for any chapter name, so a
      chapter whose own agent was never generated still produces sixteen files
      to upload. Without the guard it would get a complete-looking Icons folder
      holding everyone else's art and none of its own — and the run would
      report it as updated.

Nothing here touches Drive: `create_chapter`'s five Drive calls are replaced
with a fake folder tree.

Run: python3 skills/aaif-create-chapter/scripts/test_upload_agents.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upload_agents as ua        # noqa: E402
import create_chapter as cc       # noqa: E402

#: The shared files every chapter's folder holds, exactly as the real art
#: directory names them.
GENERICS = ["Agent %02d.gif" % i for i in range(1, 11)]
LOGOS = ["AAIF Logo.svg", "AAIF Logo.png", "AAIF Logo Reverse.svg",
         "AAIF Logo Reverse.png", "AAIF Mark.svg", "AAIF Mark.png"]


def _art_dir(tmp, chapters=("Boston",)):
    """A directory shaped like `agent_art.build_agents` + `build_logos` output."""
    for name in list(GENERICS) + list(LOGOS):
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(b"shared-" + name.encode())
    for c in chapters:
        with open(os.path.join(tmp, "%s Agent.gif" % c), "wb") as fh:
            fh.write(b"agent-for-" + c.encode())
    return tmp


class TestWhatAChapterShouldHold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.art = _art_dir(self.tmp.name)

    def test_a_chapter_gets_its_own_agent_plus_the_shared_set(self):
        got = [f for f, _p in ua.art_for(self.art, "Boston")]
        self.assertEqual(got[0], "Boston Agent.gif",
                         "the chapter's own agent comes first")
        self.assertEqual(sorted(got[1:]), sorted(GENERICS + LOGOS))

    def test_a_chapter_with_no_agent_still_lists_the_shared_set(self):
        """Which is exactly why `sync_chapter` needs its own guard: this
        function cannot tell the difference, so the caller must."""
        got = [f for f, _p in ua.art_for(self.art, "Nowhere")]
        self.assertEqual(sorted(got), sorted(GENERICS + LOGOS))

    def test_the_filename_is_derived_the_same_way_the_art_builder_derives_it(self):
        """A chapter whose name has a slash or a colon in it must still find
        its file — and must not reach outside the art directory to do it."""
        for name, safe in (("Washington, D.C.", "Washington, D.C."),
                           ("Montréal", "Montréal"),
                           ("Tel Aviv-Yafo", "Tel Aviv-Yafo"),
                           ("A/B", "A_B"),
                           ("../etc", ".._etc")):
            with open(os.path.join(self.art, "%s Agent.gif" % safe), "wb") as fh:
                fh.write(b"x")
            got = [f for f, _p in ua.art_for(self.art, name)]
            self.assertEqual(got[0], "%s Agent.gif" % safe, name)

    def test_a_chapters_own_agent_is_never_mistaken_for_a_shared_one(self):
        """The two regexes are what keep the per-chapter file per-chapter. If
        `GENERIC_RE` widened, every chapter would be handed every other
        chapter's agent."""
        self.assertFalse(ua.GENERIC_RE.match("Boston Agent.gif"))
        self.assertFalse(ua.LOGO_RE.match("Boston Agent.gif"))
        for f in GENERICS:
            self.assertTrue(ua.GENERIC_RE.match(f), f)
        for f in LOGOS:
            self.assertTrue(ua.LOGO_RE.match(f), f)
        # "Agent 1.gif" is not one of ours — the builder writes two digits, and
        # a loose pattern here would pick up a hand-added file.
        self.assertFalse(ua.GENERIC_RE.match("Agent 1.gif"))
        self.assertFalse(ua.LOGO_RE.match("AAIF Logo.pptx"))


class _FakeDrive:
    """The five `create_chapter` calls `sync_chapter` makes, over a dict tree.

    Files are `{id: (name, bytes)}`; `children` maps a folder id to the ids
    under it. Every upload and create is recorded so a test can assert that a
    run wrote NOTHING, which is the assertion idempotence needs.
    """

    def __init__(self, children=None, files=None):
        self.children = children or {}
        self.files = files or {}
        self.uploaded, self.created, self.made_folders = [], [], []
        #: {filename: the mimeType the file was CREATED with in Drive}
        self.declared = {}
        self._next = 0

    def _id(self, prefix):
        self._next += 1
        return "%s%d" % (prefix, self._next)

    def list_children(self, folder_id):
        out = []
        for cid in self.children.get(folder_id, []):
            name, data = self.files[cid]
            out.append({"id": cid, "name": name,
                        "mimeType": cc.FOLDER if data is None else "image/gif"})
        return out

    def gws_download(self, file_id, out):
        with open(out, "wb") as fh:
            fh.write(self.files[file_id][1])

    def gws_upload(self, file_id, path, mime):
        with open(path, "rb") as fh:
            data = fh.read()
        self.uploaded.append((file_id, data))
        self.files[file_id] = (self.files[file_id][0], data)

    def create_folder(self, name, parent):
        fid = self._id("folder")
        self.made_folders.append((name, parent))
        self.files[fid] = (name, None)
        self.children.setdefault(parent, []).append(fid)
        return fid

    def gws_json(self, *args, params=None, body=None):
        fid = self._id("file")
        self.created.append(body["name"])
        self.declared[body["name"]] = body["mimeType"]
        self.files[fid] = (body["name"], b"")
        self.children.setdefault(body["parents"][0], []).append(fid)
        return {"id": fid}

    def install(self, case):
        for name in ("list_children", "gws_download", "gws_upload",
                     "create_folder", "gws_json"):
            real = getattr(cc, name)
            setattr(cc, name, getattr(self, name))
            case.addCleanup(setattr, cc, name, real)
        return self


class TestSyncingOneChapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.art = os.path.join(self.tmp.name, "art")
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.art)
        os.makedirs(self.work)
        _art_dir(self.art)

    def _full_icons_folder(self, chapter="Boston"):
        """A Drive tree whose Icons folder already holds byte-identical art."""
        children, files = {"chap": ["icons"]}, {"icons": ("Icons", None)}
        kids = []
        for fname, path in ua.art_for(self.art, chapter):
            fid = "d-" + fname
            with open(path, "rb") as fh:
                files[fid] = (fname, fh.read())
            kids.append(fid)
        children["icons"] = kids
        return _FakeDrive(children, files).install(self)

    def test_a_folder_that_already_matches_is_left_completely_alone(self):
        """Idempotence, asserted on the WRITE path — a plan run uploads nothing
        whatever the digests say, so testing it with write=False would pass
        even if the comparison were deleted."""
        drive = self._full_icons_folder()
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertIsNone(r.error)
        self.assertEqual(r.uploaded, ())
        self.assertEqual(len(r.skipped), 17)      # own agent + 10 + 6
        self.assertEqual(drive.uploaded, [])
        self.assertEqual(drive.created, [])
        self.assertEqual(drive.made_folders, [])

    def test_a_file_whose_bytes_drifted_is_re_uploaded_over_the_same_id(self):
        """Over the EXISTING id, not as a second file: creating a new one would
        leave the organizer's folder holding two 'Agent 03.gif'."""
        drive = self._full_icons_folder()
        drive.files["d-Agent 03.gif"] = ("Agent 03.gif", b"stale bytes")
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual(r.uploaded, ("Agent 03.gif",))
        self.assertEqual([fid for fid, _d in drive.uploaded], ["d-Agent 03.gif"])
        self.assertEqual(drive.created, [], "no duplicate file was created")

    def test_a_missing_file_is_created_then_filled(self):
        drive = self._full_icons_folder()
        drive.children["icons"] = [c for c in drive.children["icons"]
                                   if c != "d-AAIF Mark.svg"]
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual(r.uploaded, ("AAIF Mark.svg",))
        self.assertEqual(drive.created, ["AAIF Mark.svg"])
        self.assertEqual(len(drive.uploaded), 1, "created, then its bytes sent")

    def test_each_file_is_created_with_its_own_mime_type(self):
        """An .svg stored as image/gif is not previewable in Drive and
        downloads with the wrong type — and the module holds a GIF constant
        left over from when the folder was agents only."""
        drive = _FakeDrive({"chap": []}, {}).install(self)
        ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual(drive.declared["AAIF Mark.svg"], "image/svg+xml")
        self.assertEqual(drive.declared["AAIF Mark.png"], "image/png")
        self.assertEqual(drive.declared["Agent 01.gif"], "image/gif")
        self.assertEqual(drive.declared["Boston Agent.gif"], "image/gif")

    def test_mime_by_ext_covers_everything_the_matchers_can_admit(self):
        """`MIME_BY_EXT[...]` is a bare subscript, so it must be TOTAL over
        what `art_for` can return — and it is, because the two matchers admit
        only .gif, .svg and .png. Widening either regex without adding the
        extension here turns into a KeyError mid-run, after some chapters have
        already been written. An unrecognised extension in the art directory is
        simply not collected:"""
        with open(os.path.join(self.art, "AAIF Logo.webp"), "wb") as fh:
            fh.write(b"x")
        names = [f for f, _p in ua.art_for(self.art, "Boston")]
        self.assertNotIn("AAIF Logo.webp", names)
        self.assertEqual(
            {os.path.splitext(f)[1].lower() for f in names} - set(ua.MIME_BY_EXT),
            set(), "art_for returned an extension MIME_BY_EXT cannot map")

    def test_a_chapter_with_no_agent_art_is_an_error_not_a_partial_upload(self):
        drive = _FakeDrive({"chap": []}, {}).install(self)
        r = ua.sync_chapter("chap", "Nowhere", self.art, True, self.work)
        self.assertIsNotNone(r.error)
        self.assertIn("no agent art", r.error)
        self.assertFalse(r.uploaded)
        self.assertEqual(drive.uploaded, [], "not one shared file was sent")
        self.assertEqual(drive.made_folders, [], "no empty Icons folder left behind")

    def test_a_plan_run_writes_nothing_and_names_everything_missing(self):
        drive = _FakeDrive({"chap": []}, {}).install(self)
        r = ua.sync_chapter("chap", "Boston", self.art, False, self.work)
        self.assertIsNone(r.error)
        self.assertEqual(len(r.uploaded), 17)
        self.assertEqual(drive.made_folders, [])
        self.assertEqual(drive.uploaded, [])
        self.assertEqual(drive.created, [])

    def test_a_drive_failure_is_returned_not_raised(self):
        """`sync_chapter` runs under a pool map over eighty chapters; one
        raising would abandon the rest mid-run."""
        drive = _FakeDrive({"chap": []}, {}).install(self)

        def boom(folder_id):
            raise RuntimeError("403 insufficient permissions")
        cc.list_children = boom
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertIn("RuntimeError", r.error)
        self.assertIn("403", r.error)
        self.assertFalse(r.uploaded)
        self.assertEqual(drive.uploaded, [])

    def test_an_exception_with_an_empty_message_still_reports_as_a_failure(self):
        """A bare str(e) would be "" — falsy — and main() counts a chapter with
        no error and no uploads as ALREADY CURRENT. The class name is what
        keeps the message truthy."""
        _drive = _FakeDrive({"chap": []}, {}).install(self)

        def boom(folder_id):
            raise RuntimeError()
        cc.list_children = boom
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertTrue(r.error, "an empty message must not read as success")
        self.assertIn("RuntimeError", r.error)

    def test_the_comparison_download_is_cleaned_up_even_when_it_fails(self):
        """The scratch copies land in one shared tmpdir for the whole run; a
        leaked one per file is 1400 files of art on the runner's disk."""
        drive = self._full_icons_folder()
        real = drive.gws_download

        def flaky(file_id, out):
            real(file_id, out)
            if file_id == "d-Agent 05.gif":
                raise OSError("connection reset")
        cc.gws_download = flaky
        r = ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertIn("OSError", r.error)
        self.assertEqual(os.listdir(self.work), [], "a scratch file was left behind")


class TestTheWriteGate(unittest.TestCase):
    """`--write` is the only thing between a plan and 80 chapters of uploads.

    CLAUDE.md: "a script that writes on its default invocation is a bug." The
    first version of this file did not actually pin that — mutating
    `if write:` to `if True:` left all fifteen tests green, because the only
    plan-run test used an EMPTY Drive and returned at the `folder is None`
    early exit without ever reaching the upload loop. These drive the loop.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.art = os.path.join(self.tmp.name, "art")
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.art)
        os.makedirs(self.work)
        _art_dir(self.art)

    def _drifted_folder(self):
        """An Icons folder that is current except for one stale file."""
        children, files = {"chap": ["icons"]}, {"icons": ("Icons", None)}
        kids = []
        for fname, path in ua.art_for(self.art, "Boston"):
            fid = "d-" + fname
            with open(path, "rb") as fh:
                files[fid] = (fname, fh.read())
            kids.append(fid)
        children["icons"] = kids
        files["d-Agent 03.gif"] = ("Agent 03.gif", b"stale bytes")
        return _FakeDrive(children, files).install(self)

    def test_a_plan_run_over_an_existing_folder_names_the_drift_but_writes_none(self):
        """The mutation-catching case: the upload loop IS entered, one file is
        found to differ, and still nothing reaches Drive."""
        drive = self._drifted_folder()
        r = ua.sync_chapter("chap", "Boston", self.art, False, self.work)
        self.assertIsNone(r.error)
        self.assertEqual(r.uploaded, ("Agent 03.gif",), "the drift must be named")
        self.assertEqual(drive.uploaded, [], "a plan run uploaded a file")
        self.assertEqual(drive.created, [], "a plan run created a file")
        self.assertEqual(drive.made_folders, [], "a plan run created a folder")

    def test_the_same_run_with_write_does_reach_drive(self):
        """The other half — without this, the test above passes if uploading
        broke entirely."""
        drive = self._drifted_folder()
        ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual([fid for fid, _d in drive.uploaded], ["d-Agent 03.gif"])

    def test_a_plan_run_never_creates_the_icons_folder(self):
        drive = _FakeDrive({"chap": []}, {}).install(self)
        ua.sync_chapter("chap", "Boston", self.art, False, self.work)
        self.assertEqual(drive.made_folders, [])

    def test_the_icons_folder_is_created_by_name_under_the_chapter(self):
        """`made_folders` was only ever asserted EMPTY, so the create path ran
        untested: mutating the name to "Ikons", or the parent to the tree root,
        left every test green and would scatter 80 stray folders."""
        drive = _FakeDrive({"chap": []}, {}).install(self)
        ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual(drive.made_folders, [(ua.ICONS_FOLDER, "chap")])

    def test_a_stray_file_named_icons_is_not_mistaken_for_the_folder(self):
        """An organizer parking a file called "Icons" in their chapter folder
        would otherwise have `list_children` called on a file id."""
        drive = _FakeDrive({"chap": ["stray"]},
                           {"stray": ("Icons", b"not a folder")}).install(self)
        ua.sync_chapter("chap", "Boston", self.art, True, self.work)
        self.assertEqual(drive.made_folders, [(ua.ICONS_FOLDER, "chap")],
                         "the real folder must still be created alongside it")


class TestMainSummary(unittest.TestCase):
    """`main()` is where the misreport the `Synced` record exists to prevent
    would actually surface, and it had no test at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.art = os.path.join(self.tmp.name, "art")
        os.makedirs(self.art)
        _art_dir(self.art)

    def _run(self, results, argv_extra=()):
        """Drive main() over canned Synced records; return (exit code, stdout)."""
        import io
        import contextlib
        real_chapters, real_sync, real_argv = ua.chapters, ua.sync_chapter, sys.argv
        ua.chapters = lambda: [("id-%s" % r.name, r.name) for r in results]
        ua.sync_chapter = lambda cid, name, *a, **k: next(
            r for r in results if r.name == name)
        sys.argv = ["upload_agents.py", "--art", self.art] + list(argv_extra)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = ua.main()
            return code, buf.getvalue()
        finally:
            ua.chapters, ua.sync_chapter, sys.argv = real_chapters, real_sync, real_argv

    def test_the_three_outcomes_are_counted_and_the_exit_code_follows(self):
        code, out = self._run([
            ua.Synced("Alpha", uploaded=("Agent 01.gif",)),
            ua.Synced("Bravo"),                       # nothing to do
            ua.Synced("Delta", error="RuntimeError: 403"),
        ])
        self.assertEqual(code, 1, "a failure must not exit 0")
        self.assertIn("1 chapter(s) would be updated, 1 already current, 1 failed", out)

    def test_a_failed_chapter_is_named_in_the_summary_not_only_inline(self):
        """The docstring promises a run "reports which chapters are missing
        their agent". `missing` was collected and never printed."""
        _code, out = self._run([
            ua.Synced("Delta", error="no agent art generated for this chapter"),
        ])
        self.assertIn("CHAPTERS NEEDING ATTENTION", out)
        self.assertIn("Delta", out.split("CHAPTERS NEEDING ATTENTION")[1])
        self.assertIn("Rebuild the art", out)

    def test_an_all_clean_run_exits_zero_and_says_nothing_alarming(self):
        code, out = self._run([ua.Synced("Alpha"), ua.Synced("Bravo")])
        self.assertEqual(code, 0)
        self.assertNotIn("CHAPTERS NEEDING ATTENTION", out)
        self.assertIn("2 already current", out)

    def test_a_chapter_filter_that_matches_nothing_is_an_error(self):
        code, _out = self._run([ua.Synced("Alpha")], ("--chapter", "Nowhere"))
        self.assertEqual(code, 1)

    def test_the_chapter_filter_is_case_insensitive(self):
        code, out = self._run([ua.Synced("Alpha", uploaded=("x.gif",))],
                              ("--chapter", "alpha"))
        self.assertEqual(code, 0)
        self.assertIn("1 chapter(s)", out)

    def test_an_incomplete_art_directory_is_refused_before_any_chapter(self):
        os.remove(os.path.join(self.art, "Agent 01.gif"))
        code, out = self._run([ua.Synced("Alpha")])
        self.assertEqual(code, 2, "a short art set must not half-populate Drive")
        self.assertIn("expected 10 generic agents", out)

    def test_an_art_directory_with_no_logos_is_refused(self):
        for f in LOGOS:
            os.remove(os.path.join(self.art, f))
        code, out = self._run([ua.Synced("Alpha")])
        self.assertEqual(code, 2)
        self.assertIn("no AAIF logos", out)


class TestTheRecord(unittest.TestCase):
    def test_a_synced_defaults_to_no_work_and_no_error(self):
        r = ua.Synced("Boston")
        self.assertEqual((r.name, r.uploaded, r.skipped, r.error),
                         ("Boston", (), (), None))

    def test_every_error_path_returns_empty_uploaded_and_skipped(self):
        """`main()` relies on this: it reads `uploaded` only after finding no
        error, so a record carrying both would be miscounted. The docstring
        claims the invariant; nothing checked it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        art, work = os.path.join(tmp.name, "a"), os.path.join(tmp.name, "w")
        os.makedirs(art)
        os.makedirs(work)
        _art_dir(art)
        _FakeDrive({"chap": []}, {}).install(self)

        def boom(_folder_id):
            raise RuntimeError("403")

        cases = [("no art", lambda: ua.sync_chapter("chap", "Nowhere", art, True, work))]
        cc.list_children = boom
        cases.append(("drive down",
                      lambda: ua.sync_chapter("chap", "Boston", art, True, work)))
        for label, call in cases:
            r = call()
            self.assertIsNotNone(r.error, label)
            self.assertEqual(r.uploaded, (), label)
            self.assertEqual(r.skipped, (), label)

    def test_an_unreadable_art_directory_is_returned_not_raised(self):
        """`art_for` does an os.listdir and used to sit OUTSIDE the try, so a
        vanished art dir raised through pool.map and abandoned every chapter
        not yet reached."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = os.path.join(tmp.name, "w")
        os.makedirs(work)
        _FakeDrive({"chap": []}, {}).install(self)
        r = ua.sync_chapter("chap", "Boston",
                            os.path.join(tmp.name, "gone"), True, work)
        self.assertIsNotNone(r.error, "it raised instead of reporting")
        self.assertIn("FileNotFoundError", r.error)


if __name__ == "__main__":
    # Every TestCase in this module must actually be collected. Appending a
    # class BELOW this guard defines it after unittest.main() has already run,
    # so it is silently never executed.
    import inspect
    defined = [n for n, o in list(globals().items())
               if inspect.isclass(o) and issubclass(o, unittest.TestCase)]
    loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    collected = {type(t).__name__ for s_ in loaded for t in s_}
    missing = sorted(set(defined) - collected)
    if missing:
        raise SystemExit("test classes defined but never collected: %s"
                         % ", ".join(missing))
    unittest.main()
