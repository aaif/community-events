import os

from aaif_events import datadir


def _toolkit(tmp_path, *parts, git=True):
    root = tmp_path.joinpath(*parts) if parts else tmp_path / "meetups"
    root.mkdir(parents=True)
    if git:
        (root / ".git").mkdir()
    return str(root)


def test_a_checkout_writes_at_its_own_root(tmp_path):
    root = _toolkit(tmp_path)
    assert datadir.data_dir(root, environ={}, platform="darwin") == root


def test_a_copy_without_git_is_a_plugin_install(tmp_path):
    """Claude's plugin cache copies files and drops `.git`."""
    root = _toolkit(tmp_path, "cache", "aaif-events", "0.6.0", git=False)
    got = datadir.data_dir(root, environ={}, platform="darwin")
    assert got.endswith(os.path.join("Library", "Application Support", "AAIF"))


def test_a_goose_clone_is_a_plugin_install_despite_its_git(tmp_path):
    """Goose installs with `git clone`, so `.git` alone cannot decide."""
    root = _toolkit(tmp_path, ".agents", "plugins", "aaif-events")
    assert datadir.data_dir(root, environ={}, platform="darwin") != root


def test_a_claude_plugin_dir_with_git_is_still_a_plugin(tmp_path):
    root = _toolkit(tmp_path, ".claude", "plugins", "marketplaces", "aaif")
    assert datadir.data_dir(root, environ={}, platform="darwin") != root


def test_linux_uses_xdg(tmp_path):
    root = _toolkit(tmp_path, git=False)
    got = datadir.data_dir(root, environ={"XDG_DATA_HOME": "/x/share"}, platform="linux")
    assert got == "/x/share/aaif"


def test_the_override_wins(tmp_path):
    root = _toolkit(tmp_path)
    assert datadir.data_dir(root, environ={datadir.ENV: str(tmp_path / "d")}) == str(tmp_path / "d")


def test_resolve_never_uses_the_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv(datadir.ENV, str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    assert datadir.resolve("report") == str(tmp_path / "data" / "report")
    assert datadir.resolve("/abs/report") == "/abs/report"
