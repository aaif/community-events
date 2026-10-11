"""Where a secret comes from: the environment, the repo-root `.env`, the keychain.

One order for every engine, because an installed plugin (Goose, Claude
Desktop) reaches only the last of them: those apps pass no shell environment,
and a plugin update replaces the `.env` inside the install folder. `slack` and
`luma` both resolve through here, and `aaif-check-setup` carries a lib-free
copy of the order that its tests pin to this one. A diagnosis that looked in a
place the engine never reads would report a key as working that the engine
then cannot find.

Nothing here prints or returns a value except to the caller that asked for it;
sources are named, values never are.
"""

import functools
import os
import re
import subprocess

#: `.env` lives at the repo root, not wherever the process happens to run.
DOTENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".env")


def dotenv_value(key, env_path=DOTENV_PATH):
    """`key` out of a KEY=VALUE `.env` file, or None.

    Key first, path second — the order of every secret helper here and of
    check_setup's copy. Both are strings, so a swap would not raise; it would
    read as "not in .env" and move on silently.

    Deliberately not a dotenv loader: only this one key is read, nothing is
    exported into the environment, and the value never appears in any output.
    It does understand the shapes real .env files take, though: an optional
    leading `export `, quoted values (kept verbatim, `#` and all), and — for
    unquoted values only — a trailing ` # comment`, which would otherwise ride
    into the token and make it invalid.
    """
    if not os.path.exists(env_path):
        return None
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            name, sep, value = line.strip().partition("=")
            name = name.strip()
            if name.startswith("export "):
                name = name[len("export "):].strip()
            if not sep or name != key:
                continue
            value = value.strip()
            if value[:1] in ("'", '"'):
                close = value.find(value[0], 1)
                # A quoted value keeps its content verbatim — a '#' inside the
                # quotes is part of the token, not a comment.
                value = value[1:close] if close > 0 else value[1:]
            else:
                # Strip an inline comment: a '#' only starts one when preceded
                # by whitespace, so a bare `xoxb-a#b` stays intact.
                if value.startswith("#"):
                    value = ""
                else:
                    value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if value:
                return value
    return None


@functools.lru_cache(maxsize=None)
def keychain(service):
    """(value or None, failure or None) for a macOS keychain item.

    Cached for the process as a prompt saving, not a contract: each
    `security` call can raise its own macOS "allow access" prompt, and one run
    asks for the same item several times. `stored()` returns the failure, so
    no caller needs a second call to learn it.
    No keychain (not macOS) and no stored item are both a plain miss. Any other
    failure — a locked keychain, a denied ACL — comes back as `failure`, so it
    is never reported as "no key" and sent off to re-create one that exists.
    """
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True)
    except OSError:
        return None, None
    if r.returncode == 0:
        return (r.stdout.strip() or None), None
    err = (r.stderr or "").strip()
    if err and "could not be found" not in err:
        return None, err.splitlines()[-1]
    return None, None


def stored(var, service, env_path=DOTENV_PATH):
    """(value, source, failure) for `var` from the environment, `.env`, then
    the keychain.

    `source` names where it came from (`env:VAR`, `.env:<path>`,
    `keychain:<service>`), never the value. When it is nowhere, value and
    source are None and `failure` says whether the keychain refused (locked,
    denied) rather than simply holding no item — a caller must not report a
    refusal as "no key".
    """
    value = os.environ.get(var, "").strip()
    if value:
        return value, "env:%s" % var, None
    value = (dotenv_value(var, env_path) or "").strip()
    if value:
        return value, ".env:%s" % env_path, None
    value, failure = keychain(service)
    if value:
        return value, "keychain:%s" % service, None
    return None, None, failure


def hint(service):
    """How to store a secret as `service`: the app first, because an agent's
    shell has no keyboard for the terminal command's hidden prompt."""
    return ("Keychain Access → File → New Password Item, Keychain Item Name "
            "%s, Account Name aaif; or in Terminal: "
            "security add-generic-password -s %s -a aaif -w" % (service, service))
