"""Tests for the report chrome — specifically its two security controls.

`assert_git_ignored()` is the only programmatic guard against committing ~100
organizer email addresses and the whole workspace directory into a public repo,
and the escaping in `bars()`/`actions()` is the only thing standing between
free text on a public intake form and script running in a reader's browser.

Both fail *silently* when broken — the guard simply stops refusing, and the
escaping simply stops escaping — so neither is safe to leave un-pinned.
"""

import os
import subprocess

import pytest

from aaif_events import report_style as rs

XSS = '<img src=x onerror="fetch(1)">'


# --------------------------------------------------------------- escaping ---

def test_bars_escapes_labels_and_values():
    out = rs.bars([(XSS, 1)])
    assert "<img" not in out and "&lt;img" in out


def test_actions_escapes_every_field():
    out = rs.actions([(XSS, XSS, XSS, XSS, "now")])
    assert "<img" not in out and out.count("&lt;img") == 4


def test_helpers_do_not_double_escape():
    """The call sites pass raw text on purpose; escaping twice would show the
    entities to the reader."""
    assert "&amp;lt;" not in rs.bars([("a & b", 1)])
    assert "&amp;lt;" not in rs.actions([("a & b", "c & d", "e", "f", "now")])


def test_page_escapes_the_title_but_not_the_body():
    """The body is composed markup; the title is a caller-supplied string. This
    asymmetry is deliberate — "hardening" page() to escape the body would blank
    every report."""
    out = rs.page(XSS, "<div>real markup</div>")
    assert "&lt;img" in out
    assert "<div>real markup</div>" in out


def test_page_declares_a_doctype_and_charset():
    """Without them Chrome renders the PDF in quirks mode and guesses the
    encoding of pages containing 'españa' and 'Montréal'."""
    out = rs.page("t", "b")
    assert out.startswith('<!doctype html>\n<meta charset="utf-8">')


def test_bars_survives_a_zero_maximum():
    assert "bars" in rs.bars([("a", 0), ("b", 0)])


# ------------------------------------------------------- git-ignore guard ---

def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_ignored_paths_are_allowed(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / ".gitignore").write_text(".cache/\nreport.html\n", encoding="utf-8")
    monkeypatch.chdir(root)
    rs.assert_git_ignored(".cache" + os.sep, "report.html")   # must not raise


def test_an_unignored_path_is_refused(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exc:
        rs.assert_git_ignored("leaky.html")
    assert "REFUSING TO RUN" in str(exc.value)


def test_a_directory_is_probed_before_it_exists(tmp_path, monkeypatch):
    """The guard runs before collection, so the cache dir is not there yet —
    `check-ignore` answers differently for a bare directory name."""
    root = _repo(tmp_path)
    (root / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    monkeypatch.chdir(root)
    assert not (root / ".cache").exists()
    rs.assert_git_ignored(".cache" + os.sep)


def test_an_already_tracked_file_is_refused_even_though_it_matches_a_rule(tmp_path, monkeypatch):
    """.gitignore has no effect on a tracked file — it would still ride along
    on `git add -A`."""
    root = _repo(tmp_path)
    (root / "report.html").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-f", "report.html"], check=True)
    (root / ".gitignore").write_text("report.html\n", encoding="utf-8")
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exc:
        rs.assert_git_ignored("report.html")
    assert "TRACKED" in str(exc.value)


def test_a_path_outside_any_repo_is_allowed(tmp_path, monkeypatch):
    """`git check-ignore` exits 128 there, which is not the same as "unignored".
    Refusing it would reject the remedy this guard's own message recommends."""
    root = _repo(tmp_path / "repo")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(root)
    rs.assert_git_ignored(str(outside) + os.sep, str(outside / "r.html"))


def test_a_git_failure_that_is_not_outside_a_repo_aborts(monkeypatch, tmp_path):
    """Exit 128 for e.g. dubious ownership must not read as "outside any repo"
    and silently disengage the guard."""
    fake = subprocess.CompletedProcess(
        ["git"], 128, stdout="",
        stderr="fatal: detected dubious ownership in repository at '/x'")
    monkeypatch.setattr(rs.subprocess, "run", lambda *a, **kw: fake)
    with pytest.raises(SystemExit) as exc:
        rs._repo_root(str(tmp_path / "r.html"))
    assert "REFUSING TO RUN" in str(exc.value)
    assert "dubious ownership" in str(exc.value)


def test_repo_root_pins_git_to_the_c_locale(monkeypatch, tmp_path):
    """The "not a git repository" stderr match is defeated by localized git
    (de_DE says "kein Git-Repository"), so the subprocess must run LC_ALL=C."""
    seen = {}

    def fake_run(*a, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(["git"], 0, stdout="/repo\n", stderr="")

    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    assert rs._repo_root(str(tmp_path / "r.html")) == "/repo"
    assert seen["env"]["LC_ALL"] == "C"
    assert seen["env"]["LANG"] == "C"


def test_to_pdf_makes_the_pdf_0600(monkeypatch, tmp_path):
    """Chrome writes the PDF twin of the 0600 HTML with default (world-readable)
    permissions; to_pdf must tighten it after a successful render."""
    html = tmp_path / "r.html"
    html.write_text("<p>x</p>", encoding="utf-8")
    pdf = tmp_path / "r.pdf"

    def fake_run(cmd, **kw):
        pdf.write_bytes(b"%PDF-" + b"x" * 2000)   # written 0644-ish by umask
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rs, "find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    assert rs.to_pdf(str(html), str(pdf)) == str(pdf)
    assert oct(os.stat(pdf).st_mode & 0o777) == "0o600"


def test_not_a_git_repository_still_means_outside_a_repo(monkeypatch, tmp_path):
    fake = subprocess.CompletedProcess(
        ["git"], 128, stdout="",
        stderr="fatal: not a git repository (or any of the parent directories): .git")
    monkeypatch.setattr(rs.subprocess, "run", lambda *a, **kw: fake)
    assert rs._repo_root(str(tmp_path / "r.html")) is None


def test_a_relative_path_is_judged_from_the_cwd_not_the_repo_root(tmp_path, monkeypatch):
    """From a repo subdirectory, a relative --out must be checked as the file
    it actually names, not as root/<name>."""
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("sub/report.html\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    rs.assert_git_ignored("report.html")       # sub/report.html is ignored
    with pytest.raises(SystemExit):
        rs.assert_git_ignored("leaky.html")    # sub/leaky.html is not


def test_the_check_follows_the_path_not_the_cwd(tmp_path, monkeypatch):
    """Running from a non-repo directory must not disable the control for an
    absolute path pointing into a repo."""
    root = _repo(tmp_path / "repo")
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with pytest.raises(SystemExit):
        rs.assert_git_ignored(str(root / "leaky.html"))


def test_write_private_is_0600_and_tightens_existing(tmp_path):
    import os
    from aaif_events import report_style as rs
    p = tmp_path / "report.html"
    rs.write_private(str(p), "névé — ok")
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert p.read_text(encoding="utf-8") == "névé — ok"
    # A pre-existing looser file is tightened, not left world-readable.
    os.chmod(p, 0o644)
    rs.write_private(str(p), "second")
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert p.read_text(encoding="utf-8") == "second"
