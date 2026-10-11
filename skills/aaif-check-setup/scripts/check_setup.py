#!/usr/bin/env python3
"""Is this computer ready to run the AAIF skills? Plain-language answer.

Written for an operator who is not technical and is running the toolkit from
Goose or Claude Desktop rather than a terminal: every line says what was
checked, whether it is fine, and — when it is not — the one thing to do next.

Read-only. It makes three network calls, all reads: one `gws` metadata read of
the Community Intake Ops sheet, Slack `auth.test`, and Luma `calendars/get`.
No token or key is ever printed — only where it was found and whether it works.

Stdlib only and no `lib/aaif_events` import, so it still runs (and can say
what is missing) from a standalone skill zip.

    python3 check_setup.py          # exit 0 when the required parts are ready
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
#: skills/aaif-check-setup/scripts → the plugin root.
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DOTENV = os.path.join(ROOT, ".env")

#: AAIF Community Intake Ops — the sheet every ops skill starts from.
INTAKE_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"
#: The sign-in the toolkit needs. Named scopes, not `-s` (an interactive scope
#: picker the assistant's shell cannot answer) and not the default set (which
#: can leave Drive out — "insufficient authentication scopes"). Same list as
#: MAINTAINING.md's "OAuth scopes".
SCOPES = ",".join("https://www.googleapis.com/auth/" + s for s in (
    "drive", "spreadsheets", "documents", "presentations", "forms.body",
    "forms.responses.readonly"))
LOGIN = "gws auth login --scopes " + SCOPES
#: Where Homebrew and friends put `gws` when the app's PATH does not include it.
GWS_FALLBACKS = ("/opt/homebrew/bin/gws", "/usr/local/bin/gws",
                 os.path.expanduser("~/.cargo/bin/gws"))
SLACK_TOKENS = (("AAIF_SLACK_READ_TOKEN", "aaif-slack-read-token"),
                ("AAIF_SLACK_WRITE_TOKEN", "aaif-slack-write-token"))
LUMA_VAR, LUMA_KEYCHAIN = "LUMA_API_KEY", "luma-api-key"
CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium")
CHROME_NAMES = ("google-chrome", "chromium", "chromium-browser")

OK, NEEDED, OPTIONAL = "ok", "needed", "optional"
MARK = {OK: "✅", NEEDED: "❌", OPTIONAL: "⚪"}
#: Anything shaped like a credential, in case a tool echoes one in an error.
_SECRET = re.compile(r"(xox[a-z]-[\w-]+|ya29\.[\w.-]+|[A-Za-z0-9_-]{32,})")
#: gws output that means the sign-in, not the request, is the problem. A COPY
#: of aaif_events.gws.SIGN_IN_FAILURE / SIGN_IN_401, pinned to it by
#: lib/aaif_events/tests/test_pinned_copies.py: the engines and this diagnosis
#: must agree on what a sign-in failure looks like. 401 only as a word — a file
#: id or a range can contain those digits.
SIGN_IN_FAILURE = ("insufficient authentication scopes", "failed to decrypt",
                   "invalid_grant", "unauthenticated", "no credentials",
                   "run 'gws auth login'")
SIGN_IN_401 = re.compile(r"\b401\b")


def keychain_steps(service, what):
    """How to store a secret without a terminal: the assistant's shell has no
    keyboard, so `security add-generic-password -w` cannot prompt there, and a
    secret typed into the chat is kept in its transcript."""
    return ("Open the Keychain Access app (search for it with Spotlight) → "
            "File → New Password Item. Keychain Item Name: %s · Account Name: "
            "aaif · Password: paste the %s. Click Add. The first time a job "
            "uses it, macOS asks — click Always Allow. Never paste it into "
            "the chat." % (service, what))


def scrub(text, limit=160):
    lines = [ln for ln in (text or "").splitlines()
             if ln.strip() and "keyring backend" not in ln]
    return _SECRET.sub("<hidden>", lines[-1] if lines else "")[:limit]


def is_sign_in_failure(text):
    text = (text or "").lower()
    return any(s in text for s in SIGN_IN_FAILURE) or bool(SIGN_IN_401.search(text))


def keychain(service):
    """(value or None, failure or None) for a macOS keychain item. A COPY of
    aaif_events.credentials.keychain: a locked keychain or a Deny click is a
    `failure`, never a plain miss, so it is not reported as "no key" and the
    operator is not sent to create a second item under the same name."""
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


def dotenv(var, path=None):
    """`var` out of the toolkit's `.env`. A COPY of
    aaif_events.credentials.dotenv_value (this script stays lib-free so it runs
    from a zip), pinned to it by a test: a diagnosis that parsed `.env`
    differently from the engines would pass a key the engines then reject."""
    path = path or DOTENV
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            name, sep, value = line.strip().partition("=")
            name = name.strip()
            if name.startswith("export "):
                name = name[len("export "):].strip()
            if not sep or name != var:
                continue
            value = value.strip()
            if value[:1] in ("'", '"'):
                close = value.find(value[0], 1)
                value = value[1:close] if close > 0 else value[1:]
            elif value.startswith("#"):
                value = ""
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if value:
                return value
    return None


def find_secrets(pairs):
    """(value, where, failure) for the first of `pairs` ((var, service), ...)
    found, searched one SOURCE at a time — the environment for every var, then
    the toolkit's .env, then the keychain — exactly as the engines search
    (aaif_events.credentials.stored for one var, slack.load_token for two).
    Each source is read once: a second `security` call can mean a second
    macOS prompt. `failure` is a keychain refusal when nothing was found."""
    for var, _ in pairs:
        value = os.environ.get(var, "").strip()
        if value:
            return value, "the environment", None
    for var, _ in pairs:
        value = dotenv(var)
        if value:
            return value, "the .env file", None
    failure = None
    for _, service in pairs:
        value, refused = keychain(service)
        if value:
            return value, "the Mac keychain", None
        failure = failure or refused
    return None, None, failure


def keychain_refused(what, failure):
    return NEEDED, "Your Mac keychain would not hand over the saved %s (%s)." % (
        what, failure), ("Unlock the keychain (or, when macOS asks, click Always "
                         "Allow), then check your setup again. Do not create a "
                         "second item — the one you have is still there.")


def check_python():
    v = sys.version_info
    if v >= (3, 9):
        return OK, "Python %d.%d is installed." % v[:2], None
    return NEEDED, "Python %d.%d is too old." % v[:2], (
        "Install Python 3.9 or newer from python.org, then restart this app.")


def check_checkout():
    if os.path.isdir(os.path.join(ROOT, "lib", "aaif_events")):
        return OK, "The full AAIF toolkit is installed.", None
    return NEEDED, "Only this one skill is installed, not the whole toolkit.", (
        "Install the toolkit as a plugin (see the README's 'Set up once' "
        "section). Uploaded single skills can write copy but cannot run the "
        "Drive, Slack or Luma jobs.")


def find_gws():
    on_path = shutil.which("gws")
    if on_path:
        return on_path, True
    for path in GWS_FALLBACKS:
        if os.access(path, os.X_OK):
            return path, False
    return None, False


def check_gws():
    path, on_path = find_gws()
    if not path:
        return NEEDED, "The `gws` Google Workspace tool is not installed.", (
            "Ask your helper to install it (on a Mac: `brew install "
            "googleworkspace-cli`), then restart this app.")
    if not on_path:
        return NEEDED, "`gws` is installed but this app cannot see it.", (
            "Quit this app completely and open it again. If that does not fix "
            "it, ask your helper to add %s to the app's PATH."
            % os.path.dirname(path))
    return OK, "The `gws` Google Workspace tool is installed.", None


def stray_gws_key():
    """True when gws's keychain holds a key minted without $USER.

    Attributes only (no `-w`): this asks whether the item exists, never for
    its value."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "gws-cli",
                            "-a", "unknown-user"], capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0


#: The Google lines a sign-in fixes; main() prints the sign-in command after them.
SIGN_IN_UNREADABLE = "Your saved Google sign-in can no longer be read."
SIGN_IN_NO_DRIVE = "Signed in to Google, but without permission to use Drive."
SIGN_IN_NONE = "You are not signed in to Google through `gws`."
SIGN_IN_WRONG_ACCOUNT = "Signed in, but this account cannot open the Community Intake sheet."
SIGN_IN_LINES = (SIGN_IN_UNREADABLE, SIGN_IN_NO_DRIVE, SIGN_IN_NONE, SIGN_IN_WRONG_ACCOUNT)


def check_google():
    path, on_path = find_gws()
    if not (path and on_path):
        return NEEDED, "Google sign-in not checked — `gws` is not available.", None
    params = json.dumps({"fileId": INTAKE_ID, "supportsAllDrives": True,
                         "fields": "name,capabilities/canEdit"})
    try:
        r = subprocess.run([path, "drive", "files", "get", "--params", params],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return NEEDED, "Google did not answer within a minute.", (
            "Check the internet connection and try again.")
    if r.returncode != 0:
        why = scrub(r.stderr or r.stdout)          # for display only
        text = ((r.stderr or "") + (r.stdout or "")).lower()   # every test reads this
        if "failed to decrypt" in text:
            # Seen live 2026-10-09: gws names its keychain key after $USER, so a
            # run with USER unset minted a second `gws-cli` key under
            # `unknown-user` and re-encrypted the cache with it; every normal
            # run then read the original key and failed to decrypt. Sign-out
            # does not remove keys, and a login over a mismatched key did not
            # stick. Every `gws-cli` key has to go first.
            stray = " A second, stray sign-in key is in the keychain." if stray_gws_key() else ""
            return NEEDED, SIGN_IN_UNREADABLE + stray, (
                "Open Keychain Access, search for gws-cli and delete every entry "
                "it finds. Then ask the assistant to sign you out of gws "
                "(`gws auth logout`) and in again with the toolkit's "
                "permissions; a browser opens — use your AAIF Google account.")
        if "insufficient authentication scopes" in text:
            return NEEDED, SIGN_IN_NO_DRIVE, (
                "Ask the assistant to sign you in again with the toolkit's "
                "permissions; a browser opens — allow everything it asks for.")
        if is_sign_in_failure(text):
            return NEEDED, SIGN_IN_NONE, (
                "Ask the assistant to sign you in with the toolkit's "
                "permissions; a browser opens — use your AAIF Google account.")
        if any(w in text for w in ("404", "not found", "403")):
            return NEEDED, SIGN_IN_WRONG_ACCOUNT, (
                "Sign in again with the Google account AAIF shared the "
                "Chapters folder with, or ask an admin for access.")
        return NEEDED, "Google check failed: %s" % why, (
            "Try again; if it repeats, send this line to your helper.")
    try:
        can_edit = json.loads(r.stdout[r.stdout.index("{"):]).get(
            "capabilities", {}).get("canEdit")
    except ValueError:
        # Not a permissions finding: sending an admin to grant access that
        # already exists would be the wrong fix.
        return NEEDED, "Google answered, but its reply could not be read.", (
            "Try again; if it repeats, send this line to your helper.")
    if can_edit:
        return OK, "Signed in to Google, and you can edit the Community Intake sheet.", None
    return NEEDED, "Signed in to Google, but you can only VIEW the Community Intake sheet.", (
        "Ask an AAIF admin to make you an editor of the Community Intake Ops sheet.")


def check_slack():
    token, where, failure = find_secrets(SLACK_TOKENS)
    if not token and failure:
        return keychain_refused("Slack token", failure)
    if not token:
        return OPTIONAL, "No Slack token — needed for \"run the sync\", the Slack audit and the community update; writing jobs don't need it.", (
            "Get the AAIF Slack app token from your helper. "
            + keychain_steps("aaif-slack-write-token", "token"))
    req = urllib.request.Request("https://slack.com/api/auth.test", data=b"",
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as e:
        return NEEDED, "Slack token found in %s, but Slack could not be reached (%s)." % (
            where, type(e).__name__), "Check the internet connection and try again."
    if body.get("ok"):
        return OK, "Slack token in %s works (workspace: %s)." % (
            where, body.get("team", "?")), None
    return NEEDED, "Slack token in %s was refused (%s)." % (where, body.get("error")), (
        "The token has expired or been revoked; ask your helper for a new one.")


def check_luma():
    key, where, failure = find_secrets(((LUMA_VAR, LUMA_KEYCHAIN),))
    if not key and failure:
        return keychain_refused("Luma key", failure)
    if not key:
        return OPTIONAL, "No Luma key — event pages will come back as copy to paste into Luma by hand.", (
            "To let the assistant publish to Luma, get the API key from the "
            "AAIF Luma calendar's settings. "
            + keychain_steps("luma-api-key", "API key"))
    req = urllib.request.Request("https://public-api.luma.com/v1/calendars/get",
                                 headers={"x-luma-api-key": key,
                                          "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return OK, "Luma key in %s works." % where, None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return NEEDED, "Luma key in %s was refused (HTTP %d)." % (where, e.code), (
                "The key may have been rotated; get a fresh one from the Luma "
                "calendar settings.")
        # A Luma outage or rate limit is not a bad key: do not send the
        # operator to rotate one that works.
        return NEEDED, "Luma key found in %s, but Luma is not answering right now (HTTP %d)." % (
            where, e.code), "Try again in a few minutes."
    except (urllib.error.URLError, OSError) as e:
        return NEEDED, "Luma key found in %s, but Luma could not be reached (%s)." % (
            where, type(e).__name__), "Check the internet connection and try again."


def check_chrome():
    if any(os.path.exists(p) for p in CHROME_PATHS) or any(
            shutil.which(n) for n in CHROME_NAMES):
        return OK, "Google Chrome is installed (used to turn reports into PDFs).", None
    return OPTIONAL, "Google Chrome is not installed — reports stay as web pages, no PDFs.", (
        "Install Google Chrome if you want PDF copies of reports.")


CHECKS = (check_python, check_checkout, check_gws, check_google,
          check_slack, check_luma, check_chrome)


def main():
    results = [check() for check in CHECKS]
    print("AAIF toolkit — setup check\n")
    for status, line, fix in results:
        print("%s %s" % (MARK[status], line))
        if fix and status != OK:
            print("   → %s" % fix)
    blocking = sum(status == NEEDED for status, _, _ in results)
    if any(status == NEEDED and line.startswith(SIGN_IN_LINES) for status, line, _ in results):
        print("\n(For the assistant: the sign-in command is\n  %s\n)" % LOGIN)
    print()
    if blocking:
        print("%d thing(s) to fix before the Drive/Slack/Luma jobs will run. "
              "Writing jobs (posts, bios, invites) work already." % blocking)
    else:
        print("Ready. ⚪ items are optional — they only matter for the jobs named.")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
