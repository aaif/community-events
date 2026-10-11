"""The deliberate copies agree with the library they copy.

AGENTS.md keeps three scripts free of `lib`: `aaif-sync/scripts/sync.py`
(drives engines by subprocess), `aaif-backup/scripts/backup.py` and
`aaif-check-setup/scripts/check_setup.py` (self-contained skills; the setup
check must run from a zip to say what is missing). Each carries a copy of a
library decision, and a drifted copy is wrong silently: reports in one folder
and caches in another, or a setup check that passes a key the engines then
cannot find. One case list, every copy, checked here.
"""

import importlib.util
import os

import pytest

from aaif_events import credentials, datadir, luma, slack

SKILLS = os.path.join(datadir.TOOLKIT, "skills")


def _load(skill, name):
    spec = importlib.util.spec_from_file_location(
        "pinned_" + name, os.path.join(SKILLS, skill, "scripts", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load("aaif-sync", "sync")
backup = _load("aaif-backup", "backup")
check_setup = _load("aaif-check-setup", "check_setup")


def _data_dir_cases(tmp):
    """(toolkit, environ, platform) covering every branch of data_dir()."""
    def make(*parts, git=True):
        root = os.path.join(tmp, *parts)
        os.makedirs(root)
        if git:
            os.makedirs(os.path.join(root, ".git"))
        return root
    roots = (make("meetups"),                                     # checkout
             make(".agents", "plugins", "aaif-events"),           # Goose clone
             make(".claude", "plugins", "cache", "aaif", "aaif-events", "0.6.0",
                  git=False),                                     # Claude cache
             make("zip", git=False))                              # bare copy
    return [(root, environ, platform)
            for root in roots
            for environ in ({}, {"XDG_DATA_HOME": "/x"}, {datadir.ENV: "/d"})
            for platform in ("darwin", "linux")]


@pytest.mark.parametrize("copy", [sync, backup], ids=["sync", "backup"])
def test_data_dir_copies_agree_on_every_branch(copy, tmp_path):
    for root, environ, platform in _data_dir_cases(str(tmp_path)):
        assert copy.data_dir(root, environ, platform) == \
            datadir.data_dir(root, environ, platform), (root, environ, platform)


def test_this_checkout_still_writes_at_its_own_root():
    assert sync.data_dir(environ={}) == sync.REPO == datadir.TOOLKIT


@pytest.mark.parametrize("line", [
    "KEY=plain",
    "export KEY=exported",
    "KEY=with-comment  # a note",
    "KEY='quoted # kept'",
    'KEY="double"',
    "KEY=#only-a-comment",
    "KEY=a#b",
    "OTHER=x",
])
def test_check_setup_reads_dotenv_as_the_engines_do(line, tmp_path):
    env = tmp_path / ".env"
    env.write_text(line + "\n")
    assert check_setup.dotenv("KEY", str(env)) == credentials.dotenv_value(str(env), "KEY")


def test_check_setup_looks_up_the_keychain_items_the_engines_read():
    assert dict(check_setup.SLACK_TOKENS) == slack.KEYCHAIN_SERVICES
    assert (check_setup.LUMA_VAR, check_setup.LUMA_KEYCHAIN) == \
        ("LUMA_API_KEY", luma.KEYCHAIN_SERVICE)


def test_check_setup_searches_in_the_engines_order(monkeypatch, tmp_path):
    """env, then .env, then keychain — the first hit wins in both."""
    env = tmp_path / ".env"
    env.write_text("LUMA_API_KEY=from-dotenv\n")
    monkeypatch.setattr(check_setup, "DOTENV", str(env))
    monkeypatch.setattr(check_setup, "keychain", lambda service: "from-keychain")
    monkeypatch.setattr(credentials, "keychain", lambda service: ("from-keychain", None))
    monkeypatch.setenv("LUMA_API_KEY", "")
    assert check_setup.find_secret("LUMA_API_KEY", "luma-api-key")[0] == \
        credentials.stored("LUMA_API_KEY", "luma-api-key", str(env))[0] == "from-dotenv"
    env.write_text("")
    assert check_setup.find_secret("LUMA_API_KEY", "luma-api-key")[0] == \
        credentials.stored("LUMA_API_KEY", "luma-api-key", str(env))[0] == "from-keychain"
