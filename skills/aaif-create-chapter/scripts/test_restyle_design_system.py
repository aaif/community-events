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
        self.assertEqual(os.path.relpath(dst, backup),
                         os.path.join("Chapters", "Boston", "About.docx"))


if __name__ == "__main__":
    unittest.main()
