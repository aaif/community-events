"""Unit tests for the rebrand engine + resume logic in create_series.py.

The engine is a deliberate copy of aaif-create-chapter's (see the comment in
create_series.py) — these tests keep the copy honest, covering the pieces that
once drifted: the worksheets inline-string branch, child-name rebranding, the
residual-token check, and the hardened zip rewrite. All offline, synthetic data
only.

Run: python3 skills/aaif-create-online-series/scripts/test_create_series.py
"""
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import create_series as cs  # noqa: E402


def make_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)


class TestTransformText(unittest.TestCase):
    """transform_text() on filename-shaped strings — child NAMES are rebranded
    with the same transform as content (a template file named "SF …" must clone
    renamed)."""

    def test_full_name_in_filename(self):
        self.assertEqual(
            cs.transform_text("San Francisco CRM.xlsx", "Reading Group", "READING GROUP", "readinggroup"),
            "Reading Group CRM.xlsx")

    def test_bare_sf_abbreviation_in_filename(self):
        self.assertEqual(
            cs.transform_text("SF Kickoff Deck.pptx", "Reading Group", "READING GROUP", "readinggroup"),
            "Reading Group Kickoff Deck.pptx")

    def test_luma_slug_in_filename(self):
        self.assertEqual(
            cs.transform_text("aaif-sanfrancisco-banner.png", "Reading Group", "READING GROUP", "readinggroup"),
            "aaif-readinggroup-banner.png")

    def test_filename_with_no_source_tokens_is_unchanged(self):
        self.assertEqual(
            cs.transform_text("About.docx", "Reading Group", "READING GROUP", "readinggroup"),
            "About.docx")


class TestRebrandWorksheetInlineStrings(unittest.TestCase):
    """Regression test for the xl/worksheets/sheetN.xml branch of rebrand_part:
    cells can hold an inline string (<is><t>...</t></is>) instead of a
    sharedStrings.xml reference, e.g. the CRM's "Guide" sheet title — the stale
    fork of this engine left those untouched."""

    SHEET_XML = (
        '<worksheet><sheetData><row r="2">'
        '<c r="B2" t="inlineStr"><is><t>AAIF SF — Attendee CRM</t></is></c>'
        '</row></sheetData></worksheet>'
    )

    def test_inline_string_cell_is_rebranded(self):
        out = cs.rebrand_part("xl/worksheets/sheet2.xml", self.SHEET_XML.encode("utf-8"),
                              "Reading Group", "READING GROUP", "readinggroup")
        text = out.decode("utf-8")
        self.assertIn("AAIF Reading Group — Attendee CRM", text)
        self.assertNotIn(">AAIF SF", text)

    def test_unrelated_part_type_is_left_untouched(self):
        out = cs.rebrand_part("xl/drawings/drawing1.xml",
                              self.SHEET_XML.encode("utf-8"),
                              "Reading Group", "READING GROUP", "readinggroup")
        self.assertEqual(out, self.SHEET_XML.encode("utf-8"))


class TestResidualTokens(unittest.TestCase):
    def check(self, content, expect_hit):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"xl/theme/theme1.xml": content})
            hits = cs.residual_tokens(p)
            if expect_hit:
                self.assertTrue(hits, "expected a residual hit for %r" % content)
            else:
                self.assertEqual(hits, [], "false positive for %r" % content)

    def test_city_name_is_case_insensitive(self):
        self.check(b"visit san francisco soon", True)
        self.check(b"SAN FRANCISCO tonight", True)

    def test_slug_is_case_insensitive(self):
        self.check(b"https://luma.com/aaif-SF", True)

    def test_uppercase_sf_hits(self):
        self.check(b"AAIF SF CHAPTER", True)

    def test_lowercase_sf_token_does_not_false_positive(self):
        # theme/font XML holds lowercase "sf" tokens that are not brand text
        self.check(b'<a:latin typeface="sf pro display"/>', False)

    def test_duplicate_city_pattern_removed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"xl/theme/theme1.xml": b"San Francisco SAN FRANCISCO"})
            # one hit for the (case-insensitive) city pattern, not two duplicates
            self.assertEqual(len(cs.residual_tokens(p)), 1)


class TestRewriteZip(unittest.TestCase):
    def test_success_replaces_in_place_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"a.xml": b"old", "b.xml": b"keep"})
            n = cs._rewrite_zip(p, lambda name, data: b"new" if name == "a.xml" else data)
            self.assertEqual(n, 1)
            self.assertFalse(os.path.exists(p + ".new"))
            with zipfile.ZipFile(p) as z:
                self.assertEqual(z.read("a.xml"), b"new")
                self.assertEqual(z.read("b.xml"), b"keep")

    def test_mid_loop_failure_cleans_temp_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"a.xml": b"one", "b.xml": b"two"})

            def boom(name, data):
                if name == "b.xml":
                    raise RuntimeError("transform failed")
                return data
            with self.assertRaises(RuntimeError):
                cs._rewrite_zip(p, boom)
            self.assertFalse(os.path.exists(p + ".new"))   # no leftover temp
            with zipfile.ZipFile(p) as z:                  # original intact
                self.assertEqual(z.read("a.xml"), b"one")


class FakeDrive:
    """In-memory Drive: {folder_id: [child dicts]}. Records creates/copies/
    renames/uploads."""

    def __init__(self, folders):
        self.folders = {k: list(v) for k, v in folders.items()}
        self.created, self.copied, self.renamed, self.uploaded = [], [], [], []
        self.next = 0

    def list_children(self, fid):
        return list(self.folders.get(fid, []))

    def create_folder(self, name, parent):
        self.next += 1
        fid = "fld%d" % self.next
        self.folders.setdefault(parent, []).append(
            {"id": fid, "name": name, "mimeType": cs.FOLDER})
        self.folders[fid] = []
        self.created.append(name)
        return fid

    def copy_file(self, src, name, parent):
        self.next += 1
        fid = "cp%d" % self.next
        self.folders.setdefault(parent, []).append(
            {"id": fid, "name": name, "mimeType": "application/x-copied"})
        self.copied.append(name)
        return fid

    def rename_file(self, fid, name):
        for kids in self.folders.values():
            for c in kids:
                if c["id"] == fid:
                    c["name"] = name
        self.renamed.append((fid, name))


def fake_download(_file_id, out):
    """Every downloaded Office file is a minimal xlsx whose sharedStrings carries
    the source name — the real rebrand engine then runs on it, offline. Under
    --resume this doubles as the copied-but-never-rebranded crash state."""
    make_zip(out, {"xl/sharedStrings.xml":
                   "<sst><si><t>San Francisco</t></si></sst>"})


def clean_download(_file_id, out):
    """An already-rebranded file — what a healthy resume skip downloads."""
    make_zip(out, {"xl/sharedStrings.xml":
                   "<sst><si><t>Reading Group</t></si></sst>"})


def sticky_download(_file_id, out):
    """A residual the rebrand engine can NOT rewrite (lowercase city name is a
    case-insensitive residual hit but not a transform_text token)."""
    make_zip(out, {"xl/sharedStrings.xml":
                   "<sst><si><t>visit san francisco</t></si></sst>"})


TEMPLATE = {
    "tpl": [
        {"id": "f1", "name": "San Francisco CRM.xlsx", "mimeType": "application/x"},
        {"id": "f2", "name": "About.txt", "mimeType": "application/x"},
        {"id": "sub", "name": "Event Template", "mimeType": cs.FOLDER},
    ],
    "sub": [
        {"id": "f3", "name": "notes.txt", "mimeType": "application/x"},
    ],
}


class TestCloneResume(unittest.TestCase):
    """clone_and_rebrand with the Drive layer faked — covers the --resume
    skip-by-name decision (network paths excluded by design)."""

    def run_clone(self, drive, existing_id=None, download=fake_download, repair=False):
        ctx = {"name": "Reading Group", "upper": "READING GROUP",
               "slug": "readinggroup", "residuals": [], "existing_residuals": [], "latlon": None,
               "repair_existing": repair}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(cs, "list_children", drive.list_children), \
                mock.patch.object(cs, "create_folder", drive.create_folder), \
                mock.patch.object(cs, "copy_file", drive.copy_file), \
                mock.patch.object(cs, "rename_file", drive.rename_file), \
                mock.patch.object(cs, "gws_download", download), \
                mock.patch.object(cs, "gws_upload",
                                  lambda fid, path, mime: drive.uploaded.append(fid)):
            ctx["tmp"] = d
            return cs.clone_and_rebrand("tpl", "parent", "Reading Group", ctx,
                                        existing_id=existing_id), ctx

    def test_fresh_clone_renames_children(self):
        drive = FakeDrive(TEMPLATE)
        self.run_clone(drive)
        self.assertEqual(sorted(drive.copied),
                         ["About.txt", "Reading Group CRM.xlsx", "notes.txt"])
        self.assertEqual(sorted(drive.created), ["Event Template", "Reading Group"])

    def test_resume_skips_existing_and_clones_missing(self):
        drive = FakeDrive(dict(TEMPLATE, **{
            "ex": [{"id": "e1", "name": "Reading Group CRM.xlsx",
                    "mimeType": "application/x-copied"}],
        }))
        _, ctx = self.run_clone(drive, existing_id="ex", download=clean_download)
        # the already-present (rebranded-name) file is skipped; the rest cloned
        self.assertEqual(sorted(drive.copied), ["About.txt", "notes.txt"])
        self.assertEqual(drive.created, ["Event Template"])
        # the clean skip was residual-checked but not touched
        self.assertEqual(drive.uploaded, [])
        self.assertEqual(ctx["residuals"], [])

    def test_resume_into_fully_cloned_folder_is_a_noop(self):
        drive = FakeDrive(dict(TEMPLATE, **{
            "ex": [
                {"id": "e1", "name": "Reading Group CRM.xlsx", "mimeType": "application/x-copied"},
                {"id": "e2", "name": "About.txt", "mimeType": "application/x-copied"},
                {"id": "esub", "name": "Event Template", "mimeType": cs.FOLDER},
            ],
            "esub": [{"id": "e3", "name": "notes.txt", "mimeType": "application/x-copied"}],
        }))
        new_id, _ = self.run_clone(drive, existing_id="ex", download=clean_download)
        self.assertEqual(new_id, "ex")
        self.assertEqual(drive.copied, [])
        self.assertEqual(drive.created, [])
        self.assertEqual(drive.uploaded, [])

    def test_resume_recurses_into_partial_subfolder(self):
        # subfolder exists but is empty -> re-entered, its missing child cloned
        drive = FakeDrive(dict(TEMPLATE, **{
            "ex": [
                {"id": "e1", "name": "Reading Group CRM.xlsx", "mimeType": "application/x-copied"},
                {"id": "e2", "name": "About.txt", "mimeType": "application/x-copied"},
                {"id": "esub", "name": "Event Template", "mimeType": cs.FOLDER},
            ],
            "esub": [],
        }))
        self.run_clone(drive, existing_id="ex")
        self.assertEqual(drive.copied, ["notes.txt"])
        self.assertEqual(drive.created, [])

    def test_resume_reports_a_skipped_but_unrebranded_file_and_does_not_touch_it(self):
        # The likeliest partial-run state: copied under the rebranded name, crash
        # before the rebrand's upload. The skip must residual-check the existing
        # file and REPORT it — never rewrite a file that is already in Drive
        # unless the operator opts in with --repair-existing.
        drive = FakeDrive({
            "tpl": [{"id": "f1", "name": "SF Notes.xlsx", "mimeType": "application/x"}],
            "ex": [{"id": "e1", "name": "Reading Group Notes.xlsx",
                    "mimeType": "application/x-copied"}],
        })
        _, ctx = self.run_clone(drive, existing_id="ex")  # fake_download = SF content
        self.assertEqual(drive.copied, [])                    # still a skip, no dupe
        self.assertEqual(drive.uploaded, [])                  # NOT repaired
        self.assertEqual(ctx["residuals"], [])                # not a clone failure
        self.assertEqual([fn for fn, _ in ctx["existing_residuals"]], ["Reading Group Notes.xlsx"])

    def test_repair_existing_rebrands_a_non_member_data_file_in_place(self):
        drive = FakeDrive({
            "tpl": [{"id": "f1", "name": "SF Notes.xlsx", "mimeType": "application/x"}],
            "ex": [{"id": "e1", "name": "Reading Group Notes.xlsx",
                    "mimeType": "application/x-copied"}],
        })
        _, ctx = self.run_clone(drive, existing_id="ex", repair=True)
        self.assertEqual(drive.copied, [])
        self.assertIn("e1", drive.uploaded)                   # repaired in place
        self.assertEqual(ctx["existing_residuals"], [])       # flag cleared by repair

    def test_member_data_files_are_never_repaired_even_with_repair_existing(self):
        drive = FakeDrive({
            "tpl": [{"id": "f1", "name": "San Francisco CRM.xlsx", "mimeType": "application/x"},
                    {"id": "f2", "name": "Event Tracker.docx", "mimeType": "application/x"}],
            "ex": [{"id": "e1", "name": "Reading Group CRM.xlsx", "mimeType": "application/x-copied"},
                   {"id": "e2", "name": "Event Tracker.docx", "mimeType": "application/x-copied"}],
        })
        _, ctx = self.run_clone(drive, existing_id="ex", repair=True)
        self.assertEqual(drive.uploaded, [])
        self.assertEqual(sorted(fn for fn, _ in ctx["existing_residuals"]),
                         ["Event Tracker.docx", "Reading Group CRM.xlsx"])

    def test_is_member_data_globs(self):
        self.assertTrue(cs.is_member_data("Reading Group CRM.xlsx"))
        self.assertTrue(cs.is_member_data("Event Tracker.docx"))
        self.assertFalse(cs.is_member_data("Slides.pptx"))
        self.assertFalse(cs.is_member_data("notes.txt"))
        # hand-renamed in Drive: case and stray whitespace must not hide a roster
        self.assertTrue(cs.is_member_data("  Reading Group CRM.xlsx  ".upper()))
        self.assertTrue(cs.is_member_data("event tracker.DOCX"))

    def test_resume_flags_a_skip_the_repair_cannot_clean(self):
        # A residual the rebrand engine can't rewrite must survive as a flag and
        # fail the run — exactly as it would on a fresh clone.
        drive = FakeDrive(dict(TEMPLATE, **{
            "ex": [{"id": "e1", "name": "Reading Group CRM.xlsx",
                    "mimeType": "application/x-copied"}],
        }))
        _, ctx = self.run_clone(drive, existing_id="ex", download=sticky_download)
        self.assertEqual([fn for fn, _ in ctx["existing_residuals"]], ["Reading Group CRM.xlsx"])

    def test_resume_matches_original_name_file_and_renames(self):
        # A survivor of the pre-rename engine holds the ORIGINAL template name —
        # it must be renamed and treated as a hit, never re-cloned as a duplicate
        # (two CRMs would send sync_crm's find_crm to the wrong one).
        drive = FakeDrive(dict(TEMPLATE, **{
            "ex": [{"id": "e1", "name": "San Francisco CRM.xlsx",
                    "mimeType": "application/x-copied"}],
        }))
        _, ctx = self.run_clone(drive, existing_id="ex")
        self.assertEqual(sorted(drive.copied), ["About.txt", "notes.txt"])
        self.assertEqual(drive.renamed, [("e1", "Reading Group CRM.xlsx")])
        # residual-checked, but a CRM is member data: reported, never rewritten
        self.assertEqual(drive.uploaded, [])
        self.assertEqual([fn for fn, _ in ctx["existing_residuals"]], ["Reading Group CRM.xlsx"])

    def test_resume_matches_original_name_subfolder_and_renames(self):
        drive = FakeDrive({
            "tpl": [{"id": "sub", "name": "SF Assets", "mimeType": cs.FOLDER}],
            "sub": [{"id": "f3", "name": "notes.txt", "mimeType": "application/x"}],
            "ex": [{"id": "esub", "name": "SF Assets", "mimeType": cs.FOLDER}],
            "esub": [{"id": "e3", "name": "notes.txt",
                      "mimeType": "application/x-copied"}],
        })
        self.run_clone(drive, existing_id="ex")
        self.assertEqual(drive.copied, [])        # recursed into the renamed hit
        self.assertEqual(drive.created, [])
        self.assertEqual(drive.renamed, [("esub", "Reading Group Assets")])


class TestRebrandFileCleansTemp(unittest.TestCase):
    def test_rebrand_file_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.xlsx")
            make_zip(p, {"xl/sharedStrings.xml":
                         "<sst><si><t>San Francisco</t></si></sst>"})
            n = cs.rebrand_file(p, "Reading Group", "READING GROUP", "readinggroup")
            self.assertEqual(n, 1)
            self.assertEqual(cs.residual_tokens(p), [])
            with zipfile.ZipFile(p) as z:
                self.assertIn("Reading Group",
                              z.read("xl/sharedStrings.xml").decode("utf-8"))


class TestMainGuards(unittest.TestCase):
    """Argument checks in main() that run before any network or Drive call,
    and the plan-by-default / tempdir contract of a --write run."""

    def run_main(self, argv, clone=None):
        clone = clone or (lambda *a, **k: self.fail("clone_and_rebrand must not run"))
        with mock.patch.object(sys, "argv", ["x"] + argv), \
                mock.patch.object(cs, "luma_status", lambda slug: "live"), \
                mock.patch.object(cs, "list_children", lambda fid: []), \
                mock.patch.object(cs, "clone_and_rebrand", clone):
            cs.main()

    def test_slug_must_match_safe_charset(self):
        for bad in ("a b", "x/../y", "ÄBC", "a?b=c", ""):
            with self.assertRaises(SystemExit) as cm:
                self.run_main(["--series", "Zed", "--slug", bad])
            self.assertIn("invalid slug", str(cm.exception))

    def test_resume_requires_write(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--resume"])
        self.assertIn("--write", str(cm.exception))

    def test_repair_existing_requires_resume(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--write", "--repair-existing"])
        self.assertIn("--resume", str(cm.exception))

    def test_default_invocation_plans_only(self):
        self.run_main(["--series", "Zed"])
        self.run_main(["--series", "Zed", "--dry-run"])   # plan-only spelling, still plans

    def test_dry_run_with_write_is_a_usage_error(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--write", "--dry-run"])
        self.assertEqual(cm.exception.code, 2)     # argparse usage error, nothing ran

    def _clone_with(self, fresh=(), existing=()):
        def fake_clone(_tpl, _parent, _name, ctx, existing_id=None):
            ctx["residuals"].extend(fresh)
            ctx["existing_residuals"].extend(existing)
            return "new"
        return fake_clone

    def test_fresh_clone_residual_fails_with_exit_1(self):
        with mock.patch("sys.stdout"), self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--write"],
                          clone=self._clone_with(fresh=[("Slides.pptx", ["SF"])]))
        self.assertIn("NOT clean", str(cm.exception))

    def test_existing_file_residual_exits_2_with_the_next_step(self):
        import io
        err = io.StringIO()
        with mock.patch("sys.stdout"), mock.patch("sys.stderr", err), \
                self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--write", "--resume"],
                          clone=self._clone_with(existing=[("Reading Group CRM.xlsx", ["SF"])]))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("1 existing file(s) still carry source tokens; re-run with "
                      "--write --resume --repair-existing for design assets, fix "
                      "CRM/Tracker by hand", err.getvalue())

    def test_fresh_residual_wins_when_both_classes_are_present(self):
        with mock.patch("sys.stdout"), self.assertRaises(SystemExit) as cm:
            self.run_main(["--series", "Zed", "--write", "--resume"],
                          clone=self._clone_with(fresh=[("a.pptx", ["SF"])],
                                                 existing=[("Reading Group CRM.xlsx", ["SF"])]))
        self.assertIn("NOT clean", str(cm.exception))

    def test_surviving_tempdir_is_reported_on_stderr(self):
        import io
        err, seen = io.StringIO(), {}
        def fake_clone(_tpl, _parent, _name, ctx, existing_id=None):
            seen["tmp"] = ctx["tmp"]
            return "new"
        try:
            with mock.patch("sys.stdout"), mock.patch("sys.stderr", err), \
                    mock.patch.object(cs.shutil, "rmtree", lambda *a, **k: None):
                self.run_main(["--series", "Zed", "--write"], clone=fake_clone)
            self.assertIn("could not remove temp dir", err.getvalue())
            self.assertIn(seen["tmp"], err.getvalue())
        finally:
            import shutil
            shutil.rmtree(seen["tmp"], ignore_errors=True)

    def test_write_clones_into_a_fresh_tempdir_that_is_removed(self):
        seen = {}
        def fake_clone(_tpl, _parent, _name, ctx, existing_id=None):
            seen["tmp"] = ctx["tmp"]
            self.assertTrue(os.path.isdir(ctx["tmp"]))
            self.assertTrue(os.path.basename(ctx["tmp"]).startswith("aaif-series-"))
            self.assertFalse(ctx["repair_existing"])
            return "new"
        self.run_main(["--series", "Zed", "--write"], clone=fake_clone)
        self.assertFalse(os.path.exists(seen["tmp"]))

    def test_tempdir_is_removed_when_clone_raises(self):
        seen = {}
        def boom(_tpl, _parent, _name, ctx, existing_id=None):
            seen["tmp"] = ctx["tmp"]
            raise RuntimeError("gws failed")
        with self.assertRaises(RuntimeError):
            self.run_main(["--series", "Zed", "--write"], clone=boom)
        self.assertFalse(os.path.exists(seen["tmp"]))


if __name__ == "__main__":
    unittest.main()
