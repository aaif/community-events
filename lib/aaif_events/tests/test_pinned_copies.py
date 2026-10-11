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
clean = _load("aaif-clean-data", "clean")
intake = _load("aaif-triage-intake", "intake")


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
            for environ in ({}, {"XDG_DATA_HOME": "/x"}, {datadir.ENV: "/d"},
                            {datadir.ENV: "relative/data"}, {datadir.ENV: "~/data"})
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
    assert check_setup.dotenv("KEY", str(env)) == credentials.dotenv_value("KEY", str(env))


def test_check_setup_looks_up_the_keychain_items_the_engines_read():
    assert dict(check_setup.SLACK_TOKENS) == slack.KEYCHAIN_SERVICES
    assert (check_setup.LUMA_VAR, check_setup.LUMA_KEYCHAIN) == \
        ("LUMA_API_KEY", luma.KEYCHAIN_SERVICE)


def _place(monkeypatch, tmp_path, placements):
    """Put each (var, service) value in env / .env / keychain for both sides."""
    env = tmp_path / ".env"
    env.write_text("".join("%s=%s\n" % (var, value)
                           for (var, where), value in placements.items() if where == "dotenv"))
    kc = {service: value for (service, where), value in placements.items()
          if where == "keychain"}
    for var in ("LUMA_API_KEY", slack.READ_TOKEN_VAR, slack.ENV_TOKEN_VAR):
        monkeypatch.setenv(var, "")
    for (var, where), value in placements.items():
        if where == "env":
            monkeypatch.setenv(var, value)
    monkeypatch.setattr(check_setup, "DOTENV", str(env))
    monkeypatch.setattr(check_setup, "keychain", lambda service: (kc.get(service), None))
    monkeypatch.setattr(credentials, "keychain", lambda service: (kc.get(service), None))
    return str(env)


@pytest.mark.parametrize("placements,want", [
    ({("LUMA_API_KEY", "dotenv"): "d", ("luma-api-key", "keychain"): "k"}, "d"),
    ({("luma-api-key", "keychain"): "k"}, "k"),
    ({("LUMA_API_KEY", "env"): "e", ("LUMA_API_KEY", "dotenv"): "d"}, "e"),
])
def test_one_secret_is_found_where_the_engines_find_it(monkeypatch, tmp_path, placements, want):
    env = _place(monkeypatch, tmp_path, placements)
    pairs = ((check_setup.LUMA_VAR, check_setup.LUMA_KEYCHAIN),)
    assert check_setup.find_secrets(pairs)[0] == \
        credentials.stored("LUMA_API_KEY", luma.KEYCHAIN_SERVICE, env)[0] == want


READ, WRITE = slack.READ_TOKEN_VAR, slack.ENV_TOKEN_VAR
R_SVC, W_SVC = slack.KEYCHAIN_SERVICES[READ], slack.KEYCHAIN_SERVICES[WRITE]


@pytest.mark.parametrize("placements", [
    {(R_SVC, "keychain"): "read-k", (WRITE, "dotenv"): "write-d"},   # source beats var
    {(READ, "dotenv"): "read-d", (WRITE, "env"): "write-e"},
    {(R_SVC, "keychain"): "read-k", (W_SVC, "keychain"): "write-k"},  # var order within
    {(W_SVC, "keychain"): "write-k"},
])
def test_slack_token_is_the_one_load_token_picks(monkeypatch, tmp_path, placements):
    """The setup check must test the token the engines will use."""
    env = _place(monkeypatch, tmp_path, placements)
    engines = slack.load_token(path=str(tmp_path / "no-cli.json"), env_path=env,
                               log=lambda source: None)
    assert check_setup.find_secrets(check_setup.SLACK_TOKENS)[0] == engines


@pytest.mark.parametrize("code,stderr", [
    (0, ""),
    (44, "security: The specified item could not be found in the keychain."),
    (51, "security: User interaction is not allowed."),
    (36, "security: The user name or passphrase you entered is not correct."),
])
def test_check_setup_tells_a_refusal_from_a_miss_as_the_engines_do(monkeypatch, code, stderr):
    import subprocess
    result = subprocess.CompletedProcess([], code, "v\n" if code == 0 else "", stderr)
    monkeypatch.setattr(credentials.subprocess, "run", lambda *a, **k: result)
    monkeypatch.setattr(check_setup.subprocess, "run", lambda *a, **k: result)
    credentials.keychain.cache_clear()
    try:
        assert check_setup.keychain("svc") == credentials.keychain("svc")
    finally:
        credentials.keychain.cache_clear()


@pytest.mark.parametrize("stderr", [
    "error[api]: Request had insufficient authentication scopes.",
    "warning: failed to decrypt token cache",
    "invalid_grant: Token has been expired or revoked.",
    "error[auth]: No credentials found. Run 'gws auth login' first.",
    "HTTP 401 Unauthorized",
    "error[api]: File not found: 1AbC401xyz",
    "The caller does not have permission",
    "Range A401:K401 exceeds grid limits",
])
def test_check_setup_and_gws_agree_on_a_sign_in_failure(stderr):
    from aaif_events import gws
    assert check_setup.is_sign_in_failure(stderr) == bool(gws.sign_in_hint(stderr))
    assert check_setup.SIGN_IN_FAILURE == gws.SIGN_IN_FAILURE


@pytest.mark.parametrize("stderr", [
    "Using keyring backend: keyring\nerror[api]: Request had insufficient authentication scopes.",
    '{\n  "error": {\n    "message": "The caller does not have permission",\n  }\n}',
    "token xoxp-1-abc leaked\n}",
    "",
])
def test_the_three_gws_failure_copies_agree(stderr):
    """backup, clean-data and triage-intake each keep a copy (self-contained)."""
    one = backup.gws_failure("Reading X", stderr)
    assert clean.gws_failure("Reading X", stderr) == one == intake.gws_failure("Reading X", stderr)
    assert "\n" not in one and "check my setup" in one
    assert "xoxp-1-abc" not in one
    assert not one.split("failed: ", 1)[1].startswith("}")
