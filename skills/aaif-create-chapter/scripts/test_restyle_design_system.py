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
    return _docx(["Instrument Sans"])


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
            setattr(rd.cc, name, fn)
            self.addCleanup(setattr, rd.cc, name, getattr(rd.cc, name))

    def _run(self, write):
        with tempfile.TemporaryDirectory() as work:
            return rd.process(self.entry, work, write, self.backup, False)

    def test_a_write_run_archives_the_bytes_that_were_in_drive(self):
        _e, report, err = self._run(write=True)
        self.assertIsNone(err)
        self.assertTrue(report, "a file that changed must report what changed")
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
        _e, report, err = self._run(write=True)
        self.assertIsNone(err)
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


if __name__ == "__main__":
    unittest.main()
