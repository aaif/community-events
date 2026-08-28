#!/usr/bin/env python3
"""Unit tests for the design-system estate sweep.

Two invariants here were learned the expensive way on the first full run, and
both fail silently, so each is pinned by a named test:

  (a) being IN a template folder does not make a file a template. Organizers
      park their own work there — a dated event deck, a "Copy of …", a personal
      draft. Eleven such files were rebranded before the allowlist existed and
      had to be restored one by one.

  (b) an archive entry is never overwritten. Two runs sharing a --backup-dir
      had the second archive the ALREADY-RESTYLED file over the original, which
      left the archive useless as a rollback for exactly the files that most
      needed it — and said nothing.

Nothing here touches Drive.

Run: python3 skills/aaif-create-chapter/scripts/test_restyle_design_system.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restyle_design_system as rd     # noqa: E402


class TestWhatCountsAsATemplate(unittest.TestCase):
    def test_the_canonical_template_files_are_templates(self):
        for name in ("Slides.pptx", "Event-Hero.pptx", "Event-Hero-Square.pptx",
                     "LinkedIn Carousel.pptx", "Square Logo.pptx",
                     "Banner 1.91.pptx", "Luma Banners.pptx", "About.docx",
                     "Event Tasks.docx", "Event Tracker.docx",
                     "Event Tracker (IRL).docx", "Attendee CRM.xlsx"):
            self.assertTrue(rd.is_template(name), name)

    def test_a_chapters_own_crm_is_a_template(self):
        """Named for its chapter, so it cannot be in the allowlist by name."""
        for city in ("Boston", "Mexico City", "Madison, WI", "Montréal"):
            self.assertTrue(rd.is_template("%s CRM.xlsx" % city), city)

    def test_the_files_the_first_run_should_not_have_touched(self):
        """The exact eleven. Each is a real name from the estate."""
        for name in ("Lean Coffee After Dark: Agentic Infrastructure.pptx",
                     "2026-08-27 Denver AAIF Event.pptx",
                     "Copy of About.docx",
                     "Copy of Event Tasks.docx",
                     "#27 linkedin.pptx",
                     "Vijay_Copy_of_Event-Hero-Square.pptx",
                     "AAIF Generic Slide Template.pptx",
                     "CCCCCCCC.pptx",
                     "Copy of Banner 1.91.pptx",
                     "Copy of Luma Banners.pptx",
                     "Copy of Event-Hero-Square.pptx"):
            self.assertFalse(rd.is_template(name), name)

    def test_a_copy_of_a_template_is_not_the_template(self):
        self.assertTrue(rd.is_template("Slides.pptx"))
        self.assertFalse(rd.is_template("Copy of Slides.pptx"))

    def test_crm_matching_is_not_a_loose_suffix_test(self):
        """'…CRM.xlsx' with no chapter in front, or a CRM-ish name, is not one."""
        self.assertFalse(rd.is_template("CRM.xlsx"))
        self.assertFalse(rd.is_template("Boston CRM.pptx"))
        self.assertFalse(rd.is_template("Boston CRM backup.xlsx"))

    def test_every_plated_deck_is_a_template(self):
        """A deck that takes plates but is not swept would never get them."""
        for name in rd.PLATED:
            if name.startswith("Copy of "):
                continue     # deliberately excluded; see the eleven above
            self.assertTrue(rd.is_template(name), name)


class TestTheArchiveIsNeverOverwritten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entry = {"path": "Community Events/Chapters/Boston/About.docx"}

    def _src(self, body):
        p = os.path.join(self.tmp.name, "src.docx")
        with open(p, "wb") as fh:
            fh.write(body)
        return p

    def test_the_first_copy_wins(self):
        backup = os.path.join(self.tmp.name, "bk")
        dst = rd._archive(self.entry, self._src(b"ORIGINAL"), backup)
        again = rd._archive(self.entry, self._src(b"ALREADY RESTYLED"), backup)
        self.assertEqual(dst, again)
        with open(dst, "rb") as fh:
            self.assertEqual(fh.read(), b"ORIGINAL")

    def test_the_archive_mirrors_the_drive_path(self):
        backup = os.path.join(self.tmp.name, "bk")
        dst = rd._archive(self.entry, self._src(b"x"), backup)
        # realpath on both sides: _archive resolves its result so it can prove
        # containment, and on macOS /var is a symlink to /private/var.
        self.assertEqual(os.path.relpath(dst, os.path.realpath(backup)),
                         os.path.join("Chapters", "Boston", "About.docx"))


class TestTheArchiveCannotEscapeItsDirectory(unittest.TestCase):
    """Every segment of an archive path comes from a Drive folder or file name,
    which any organizer with editor access controls. Joining those onto a local
    directory unsanitised lets a folder renamed to `../../..` drop
    attacker-supplied bytes anywhere the operator can write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backup = os.path.join(self.tmp.name, "bk")

    def _path(self, drive_path):
        return rd._archive_path({"path": drive_path}, self.backup)

    def test_a_traversing_folder_name_cannot_leave_the_archive(self):
        for evil in ("Community Events/Chapters/../../../../etc/x/About.docx",
                     "Community Events/Chapters/../../../About.docx",
                     "Community Events/Chapters/..%2f../About.docx"):
            dst = self._path(evil)
            root = os.path.realpath(self.backup)
            self.assertTrue(dst.startswith(root + os.sep), (evil, dst))

    def test_the_containment_check_is_a_real_backstop(self):
        """Sanitising handles `..`; the realpath check is what catches what
        sanitising cannot see. A SYMLINK already inside the archive is the
        case: the name is perfectly ordinary, and only resolution reveals that
        writing through it lands outside."""
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(os.path.join(self.backup, "Chapters"))
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.backup, "Chapters", "Boston"))
        with self.assertRaises(RuntimeError) as cm:
            rd._archive_path(
                {"path": "Community Events/Chapters/Boston/About.docx"},
                self.backup)
        self.assertIn("escape", str(cm.exception))

    def test_separators_in_a_name_are_not_path_structure(self):
        """A Drive NAME may contain a slash. It must become one segment, not
        two, or the allowlist can be satisfied while the path escapes."""
        dst = self._path("Community Events/Chapters/Boston/../../x CRM.xlsx")
        self.assertTrue(os.path.realpath(dst).startswith(
            os.path.realpath(self.backup) + os.sep))
        self.assertNotIn("..", os.path.relpath(dst, os.path.realpath(self.backup)))

    def test_an_ordinary_name_is_still_readable(self):
        """Sanitising must not mangle the real estate: chapter names carry
        commas, accents and parentheses."""
        dst = self._path("Community Events/Chapters/Madison, WI/"
                         "Event Templates (Copy for Each Event)/Slides.pptx")
        rel = os.path.relpath(dst, os.path.realpath(self.backup))
        self.assertEqual(rel, os.path.join(
            "Chapters", "Madison, WI",
            "Event Templates (Copy for Each Event)", "Slides.pptx"))

    def test_the_crm_pattern_does_not_admit_a_separator(self):
        """`^.+ CRM\\.xlsx$` would let "../../x CRM.xlsx" through the
        allowlist; the anchored form must not."""
        self.assertTrue(rd.is_template("Boston CRM.xlsx"))
        self.assertFalse(rd.is_template("../../x CRM.xlsx"))
        self.assertFalse(rd.is_template("a/b CRM.xlsx"))



def _docx(font_table_faces, doc_font="Instrument Sans"):
    """A minimal .docx: token-conformant in its document, but declaring
    `font_table_faces` in its font table."""
    import io
    import zipfile as zf
    buf = io.BytesIO()
    entries = "".join('<w:font w:name="%s"/>' % f for f in font_table_faces)
    with zf.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="ct"/>')
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="w"><w:p><w:r><w:rPr><w:rFonts '
                   'w:ascii="%s"/></w:rPr><w:t>x</w:t></w:r></w:p>'
                   "</w:document>" % doc_font)
        z.writestr("word/fontTable.xml",
                   '<?xml version="1.0"?><w:fonts xmlns:w="w" xmlns:r="r">%s'
                   "</w:fonts>" % entries)
    return buf.getvalue()


def _conformant_docx_needing_a_prune():
    """Token-audit clean, but its font table still names a retired face — i.e.
    `before` is empty while the file is still about to change. That is exactly
    the --fix-contrast-over-a-conformant-estate shape that broke the archive."""
    return _docx(["Instrument Sans", "Space Grotesk"])


def _already_conformant_docx():
    """A file with nothing left to do — produced by RUNNING the passes rather
    than hand-built to look settled. Hand-building it got the fallback wrong
    (an entry with no <w:altName> pointing at it, which prune then removes),
    and the test failed for a reason that had nothing to do with the archive."""
    import tempfile as tf
    with tf.TemporaryDirectory() as d:
        path = os.path.join(d, "settled.docx")
        with open(path, "wb") as fh:
            fh.write(_docx(["Instrument Sans"]))
        rd.ox.prune_embedded_fonts(path)
        rd.ox.ensure_fallback_font(path)
        with open(path, "rb") as fh:
            return fh.read()


class TestNothingIsWrittenWithoutAnArchive(unittest.TestCase):
    """The archive is the rollback, so the gate on it has to be "am I about to
    change this file", not "is this file off-token".

    A --fix-contrast run happens over an estate that is ALREADY conformant, so
    `before` is empty for every file while every file is still about to be
    rewritten. Gating the archive on `before` silently produced exactly the
    dangerous combination: hundreds of decks replaced, nothing kept.

    These are BEHAVIOURAL. The first version of this test read process()'s
    source and asserted the string "if write:" appeared in it — which passes
    just as happily when the `_archive(...)` call underneath has been deleted.
    A test for a bug that already shipped, that the bug walks straight through,
    is worse than no test: it reads like the case is covered.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backup = os.path.join(self.tmp.name, "bk")
        self.uploaded = []
        self.entry = {"id": "fid1", "name": "About.docx", "mime": rd.cc.DOCX,
                      "path": "Community Events/Chapters/Boston/About.docx"}
        # A .docx whose token audit is CLEAN but whose font table still needs
        # pruning — i.e. `before` is empty while the file is still about to
        # change. That combination is the whole point.
        self.payload = _conformant_docx_needing_a_prune()

        def fake_download(fid, out):
            with open(out, "wb") as fh:
                fh.write(self.payload)

        def fake_upload(fid, path, mime):
            with open(path, "rb") as fh:
                self.uploaded.append((fid, fh.read()))

        for name, fn in (("gws_download", fake_download), ("gws_upload", fake_upload)):
            # Capture the ORIGINAL before patching. Reading it back out after
            # the setattr — as this did — hands addCleanup the fake, so cleanup
            # "restores" the fake and rd.cc stays patched for the rest of the
            # process. Harmless while no later class called these; a live trap
            # for the --check/--contrast tests below, which do.
            real = getattr(rd.cc, name)
            setattr(rd.cc, name, fn)
            self.addCleanup(setattr, rd.cc, name, real)

    def _run(self, write, **kw):
        with tempfile.TemporaryDirectory() as work:
            return rd.process(self.entry, work, write, self.backup,
                              kw.pop("check_only", False), **kw)

    def test_a_write_run_archives_the_bytes_that_were_in_drive(self):
        _e, changes, err = self._run(write=True)
        self.assertIsNone(err)
        # `.conformant`, not `assertFalse(changes)`: a Changes is a namedtuple
        # and is therefore ALWAYS truthy. When this was a dict, `{}` carried
        # the clean/changed decision implicitly and a bare truth test caught
        # it; against a record the same line passes unconditionally.
        self.assertFalse(changes.conformant,
                         "a file that changed must report what changed")
        self.assertTrue(changes.wrote, "and must be recorded as written")
        self.assertEqual(changes.pruned.faces, ("Space Grotesk",))
        archived = rd._archive_path(self.entry, self.backup)
        self.assertTrue(os.path.exists(archived), "nothing was archived")
        with open(archived, "rb") as fh:
            self.assertEqual(fh.read(), self.payload,
                             "the archive must hold the PRE-change bytes")
        self.assertEqual(len(self.uploaded), 1)
        self.assertNotEqual(self.uploaded[0][1], self.payload,
                            "the uploaded file should differ from the original")

    def test_a_plan_run_neither_uploads_nor_archives(self):
        self._run(write=False)
        self.assertEqual(self.uploaded, [])
        self.assertFalse(os.path.exists(rd._archive_path(self.entry, self.backup)))

    def test_a_file_that_does_not_change_leaves_no_archive_entry(self):
        """The archive means "these were replaced". A file that turned out to
        need nothing must not leave a copy behind implying it was."""
        self.payload = _already_conformant_docx()
        _e, changes, err = self._run(write=True)
        self.assertIsNone(err)
        self.assertTrue(changes.conformant,
                        "a file that needed nothing must report nothing")
        self.assertFalse(changes.wrote, "and must not be recorded as written")
        self.assertEqual(self.uploaded, [])
        self.assertFalse(os.path.exists(rd._archive_path(self.entry, self.backup)))

    def test_a_download_that_is_not_ooxml_is_refused_before_any_rewrite(self):
        """gws writes the response body to --output even when the API returned
        an error at exit 0, so a JSON error page reaches the engine as bytes."""
        self.payload = b'{"error": {"code": 401}}'
        _e, _report, err = self._run(write=True)
        self.assertIsNotNone(err)
        self.assertIn("not OOXML", err)
        self.assertEqual(self.uploaded, [])



class TestWalkEstate(unittest.TestCase):
    """`walk_estate` decides what the whole sweep touches. Returning NOTHING is
    caught by a print in main(); returning a SUBSET is caught by nothing at all
    — the run says "Found N template file(s)" and exits 0 having swept a
    fraction. Mutation testing confirmed a no-op left every suite green.

    The tree below is the estate's real shape, faked one level at a time.
    """

    TREE = {
        "root": [("chapters", "Chapters", True), ("online", "Online", True),
                 ("shared", "Templates", True)],
        "chapters": [("boston", "Boston", True), ("tc", "TemplateCity", True)],
        "boston": [("f1", "About.docx", False), ("f2", "Boston CRM.xlsx", False),
                   ("f3", "Web Banner.png", False),
                   ("bt", "Event Templates (Copy for Each Event)", True),
                   ("dated", "2026-09-01 Boston Night", True)],
        "bt": [("f4", "Slides.pptx", False), ("f5", "#27 linkedin.pptx", False)],
        "dated": [("f6", "Slides.pptx", False)],
        "tc": [("f7", "About.docx", False)],
        "online": [("ts", "TemplateSeries", True)],
        "ts": [("f8", "Event Tracker.docx", False)],
        "shared": [("f9", "Event Tracker (IRL).docx", False)],
    }

    MIME = {"docx": rd.cc.DOCX, "xlsx": rd.cc.XLSX, "pptx": rd.cc.PPTX}

    def setUp(self):
        def kids(fid):
            out = []
            for cid, name, is_folder in self.TREE.get(fid, []):
                mime = rd.cc.FOLDER
                if not is_folder:
                    mime = self.MIME.get(name.rsplit(".", 1)[-1], "image/png")
                out.append({"id": cid, "name": name, "mimeType": mime})
            return out
        real = rd.cc.list_children
        rd.cc.list_children = kids
        self.addCleanup(setattr, rd.cc, "list_children", real)

    def walk(self):
        return rd.walk_estate("root", jobs=2)

    def test_it_finds_exactly_the_templates_and_no_more(self):
        entries = self.walk().entries
        got = {e["path"].replace("Community Events/", "", 1) for e in entries}
        self.assertEqual(got, {
            "Chapters/Boston/About.docx",
            "Chapters/Boston/Boston CRM.xlsx",
            "Chapters/Boston/Event Templates (Copy for Each Event)/Slides.pptx",
            "Chapters/TemplateCity/About.docx",
            "Online/TemplateSeries/Event Tracker.docx",
            "Templates/Event Tracker (IRL).docx",
        })

    def test_an_organizers_dated_copy_is_counted_not_swept(self):
        estate = self.walk()
        paths = {e["path"] for e in estate.entries}
        self.assertFalse(any("2026-09-01" in p for p in paths))
        self.assertEqual(estate.skipped, 1)

    def test_a_non_template_in_a_template_folder_is_named_as_a_stray(self):
        strays = self.walk().strays
        self.assertEqual(
            [p.replace("Community Events/", "", 1) for p in strays],
            ["Chapters/Boston/Event Templates (Copy for Each Event)/"
             "#27 linkedin.pptx"])

    def test_the_mint_folders_are_reached(self):
        entries = self.walk().entries
        paths = " ".join(e["path"] for e in entries)
        for mint in rd.MINTS:
            self.assertIn(mint, paths)

    def test_a_file_reachable_twice_is_handed_out_once(self):
        """The dedupe exists so two workers never share a scratch filename."""
        self.TREE["shared"] = self.TREE["shared"] + [
            ("f9", "Event Tracker (IRL).docx", False)]
        ids = [e["id"] for e in self.walk().entries]
        self.assertEqual(len(ids), len(set(ids)))

    def test_chapter_and_series_names_are_reported(self):
        estate = self.walk()
        self.assertEqual(estate.chapters, {"Boston", "TemplateCity"})
        self.assertEqual(estate.series, {"TemplateSeries"})

    def test_owners_holding_office_files_are_reported(self):
        """`with_files` is the DENOMINATOR of the "contributed none" coverage
        check — the thing that catches a renamed template folder. It was the
        ignored `_w` of the old 6-tuple and no test ever named it; a scan that
        stopped populating it would silently disable that check.

        The shared Templates folder counts as an owner too, which is why the
        field is documented as owners rather than chapters."""
        estate = self.walk()
        self.assertIn("Boston", estate.with_files)
        self.assertIn("TemplateSeries", estate.with_files)
        self.assertTrue(estate.with_files <= (estate.chapters | estate.series
                                              | {rd.SHARED_TEMPLATES}),
                        "an owner appeared that is neither chapter, series, "
                        "nor the shared Templates folder: %s" % estate.with_files)


class TestTheTwoPredicates(unittest.TestCase):
    """`conformant` and `wrote` carry the whole clean/changed and
    upload/skip decision, and every term of them was individually unpinned:
    replacing any single disjunct with False left the suite green, because the
    two tests that touched the predicate had four terms true at once.

    Dropping `before` alone, for instance, makes a `--check` run — which
    returns `Changes(before=..., after=...)` with `parts=0` — report nothing
    and exit 0 forever. That is the exact failure the record exists to stop.
    """

    #: (label, a Changes that must be NON-conformant on the strength of one field)
    ONE_TERM = [
        ("before", rd.Changes(before=("drift",))),
        ("after", rd.Changes(after=("residue",))),
        ("parts", rd.Changes(parts=1)),
        ("plates", rd.Changes(plates=("plate-a",))),
        ("retired", rd.Changes(retired=("bg1.png",))),
        ("fallback", rd.Changes(fallback=True)),
        ("contrast", rd.Changes(contrast=("finding",))),
        ("unchecked", rd.Changes(unchecked=("finding",))),
        ("pruned.faces", rd.Changes(pruned=rd.ox.Pruned(faces=("Arial",)))),
        ("pruned.parts", rd.Changes(pruned=rd.ox.Pruned(parts=("f.ttf",)))),
        ("rescued.runs", rd.Changes(rescued=rd.ox.Rescued(runs=1, before=1))),
        ("rescued.before", rd.Changes(rescued=rd.ox.Rescued(runs=0, before=3, after=3))),
    ]

    def test_an_empty_record_is_conformant_and_unwritten(self):
        self.assertTrue(rd.Changes().conformant)
        self.assertFalse(rd.Changes().wrote)

    def test_each_field_on_its_own_makes_the_record_non_conformant(self):
        for label, ch in self.ONE_TERM:
            with self.subTest(field=label):
                self.assertFalse(ch.conformant,
                                 "%s alone must earn a line" % label)

    #: The fields that mean "bytes changed on disk" — a strict subset.
    WROTE_FIELDS = {"parts", "plates", "retired", "fallback",
                    "pruned.faces", "pruned.parts", "rescued.runs"}

    def test_wrote_is_exactly_the_fields_that_change_bytes(self):
        """`wrote` gates the upload and the archive, so a field that does NOT
        change the file must not set it — otherwise a --check run would upload."""
        for label, ch in self.ONE_TERM:
            with self.subTest(field=label):
                self.assertEqual(ch.wrote, label in self.WROTE_FIELDS, label)

    def test_wrote_implies_non_conformant(self):
        """Anything written must also be reported. The reverse does not hold."""
        for label, ch in self.ONE_TERM:
            if ch.wrote:
                self.assertFalse(ch.conformant, label)

    def test_a_deck_measured_unreadable_but_not_repaired_is_still_reported(self):
        """The named regression. `improve_contrast` declines a remap that would
        break other runs and returns Rescued(0, 12, 12) — nothing written, but
        twelve runs measured below AA. Gating on `.runs` alone made
        --fix-contrast answer "already conformant" over unreadable text."""
        ch = rd.Changes(rescued=rd.ox.Rescued(runs=0, before=12, after=12))
        self.assertFalse(ch.wrote, "nothing was written, so nothing to upload")
        self.assertFalse(ch.conformant, "but it must NOT be counted clean")


class TestCheckAndContrastModes(unittest.TestCase):
    """Both branches of `process()` were rewritten and neither had a test.
    `_run` hard-coded both flags False."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entry = {"id": "fid1", "name": "About.docx", "mime": rd.cc.DOCX,
                      "path": "Community Events/Chapters/Boston/About.docx"}
        # doc_font, not just the font TABLE: `ox.audit` reports off-system
        # values in the document, so a drifted table alone leaves `before`
        # empty and the --check assertions below would pass vacuously.
        self.payload = _docx(["Space Grotesk"], doc_font="Space Grotesk")
        self.uploaded = []

        def fake_download(fid, out):
            with open(out, "wb") as fh:
                fh.write(self.payload)

        def fake_upload(fid, path, mime):
            self.uploaded.append(fid)

        for name, fn in (("gws_download", fake_download), ("gws_upload", fake_upload)):
            real = getattr(rd.cc, name)
            setattr(rd.cc, name, fn)
            self.addCleanup(setattr, rd.cc, name, real)

    def _process(self, check_only=False, **kw):
        with tempfile.TemporaryDirectory() as work:
            return rd.process(self.entry, work, False, self.tmp.name,
                              check_only, **kw)

    def test_check_reports_the_drift_and_writes_nothing(self):
        r = self._process(check_only=True)
        self.assertIsNone(r.error)
        self.assertFalse(r.changes.conformant, "--check found nothing to say")
        self.assertFalse(r.changes.wrote, "--check must never write")
        self.assertEqual(self.uploaded, [])

    def test_check_leaves_after_equal_to_before(self):
        """Nothing is rewritten, so what the file holds afterwards is what it
        held — the field doc says so and `main()` prints REMAINS from it."""
        r = self._process(check_only=True)
        self.assertEqual(r.changes.after, r.changes.before)
        self.assertTrue(r.changes.before, "the fixture is off-system on purpose")

    def test_a_conformant_file_under_check_says_nothing(self):
        self.payload = _docx(["Instrument Sans"], doc_font="Instrument Sans")
        r = self._process(check_only=True)
        self.assertTrue(r.changes.conformant)

    def test_contrast_on_a_docx_measures_nothing_and_is_conformant(self):
        """Only .pptx has a slide background; a Word tracker's text sits on the
        page, so `contrast_report` returns an empty Legibility."""
        r = self._process(contrast_only=True)
        self.assertIsNone(r.error)
        self.assertEqual(r.changes.contrast, ())
        self.assertEqual(r.changes.unchecked, ())
        self.assertTrue(r.changes.conformant)

    def test_contrast_report_on_a_non_pptx_equals_the_empty_record(self):
        """Tuples, not lists: built as lists this is `[] != ()` and reads like
        a logic bug in the caller."""
        self.assertEqual(rd.contrast_report(self.entry, "ignored"),
                         rd.Legibility())

    def test_the_two_legibility_lists_are_not_interchangeable(self):
        """A positional swap is silent and would make --contrast report
        inherited-colour runs as AA failures and never exit 0."""
        leg = rd.Legibility(bad=("f1",), unchecked=("f2", "f3"))
        self.assertEqual(leg.bad, ("f1",))
        self.assertEqual(leg.unchecked, ("f2", "f3"))
        self.assertEqual(leg[0], leg.bad)


class TestRetirementScope(unittest.TestCase):
    """retire_plates replaces ANY background that is not ours, so the decks it
    is pointed at matter. Running it over every template .pptx would treat
    designed art on a banner or the carousel as a legacy plate."""

    def test_only_the_decks_that_take_a_plate_are_retired(self):
        for name in ("Square Logo.pptx", "Banner 1.91.pptx", "Luma Banners.pptx",
                     "LinkedIn Carousel.pptx", "Slides.pptx"):
            self.assertNotIn(name, rd.PLATED, name)
        self.assertEqual(set(rd.PLATED), {"Event-Hero.pptx",
                                          "Event-Hero-Square.pptx"})

    def test_every_plated_deck_names_an_aspect_that_exists(self):
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))), "lib"))
        from aaif_events import agent_art as aa
        for name, aspect in rd.PLATED.items():
            self.assertIn(aspect, aa.ASPECTS, name)


class TestPlateDigests(unittest.TestCase):
    """`_plate_digests` is what tells retirement OURS from a legacy plate. If it
    ever returns empty, retire_plates classifies every background we generated
    as legacy and overwrites all of them — while reporting success."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_it_recognises_the_plates_the_generator_actually_writes(self):
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))), "lib"))
        from aaif_events import agent_art as aa
        # Write files named exactly as build() names them, without invoking
        # Chrome: the naming contract is what is under test.
        import hashlib
        want = set()
        for kind in aa.PLATES:
            for aspect in aa.ASPECTS:
                ext = "gif" if kind in aa.ANIMATED else "png"
                p = os.path.join(self.tmp.name,
                                 "plate-%s-%s.%s" % (kind, aspect, ext))
                with open(p, "wb") as fh:
                    fh.write(b"PLATE " + kind.encode())
                want.add(hashlib.sha256(b"PLATE " + kind.encode()).hexdigest())
        got = rd._plate_digests(self.tmp.name)
        self.assertTrue(got, "no plate was recognised — retirement would "
                             "overwrite every plate we generated")
        self.assertTrue(want <= got, want - got)

    def test_an_empty_directory_yields_no_digests(self):
        self.assertEqual(rd._plate_digests(self.tmp.name), set())

    def test_the_retirement_plate_is_one_of_ours_and_is_static(self):
        """process() opens plate-<RETIREMENT_PLATE>-<aspect>.PNG by hand."""
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))), "lib"))
        from aaif_events import agent_art as aa
        self.assertIn(rd.RETIREMENT_PLATE, aa.PLATES)
        self.assertNotIn(rd.RETIREMENT_PLATE, aa.ANIMATED)


if __name__ == "__main__":
    # Every TestCase in this module must actually be collected. Appending a
    # class BELOW this guard defines it after unittest.main() has already run,
    # so it is silently never executed — which has happened twice while writing
    # these tests, both times leaving a real gap looking covered.
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
