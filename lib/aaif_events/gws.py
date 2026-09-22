"""One `gws` subprocess client for every script that reaches Google.

Everything in this repo talks to Google Workspace by shelling out to the `gws`
CLI (a third-party client, not an official Google tool). Four scripts had grown
their own wrapper around that subprocess: two near-identical full versions with
retries and backoff, and two weaker ones with no retry handling at all, so
whether a run survived a 503 depended on which script you happened to be in.

The two full copies had also drifted. One retried on `Internal error` and the
other did not, which is the quiet kind of divergence: the run just fails, and
nothing says it would have succeeded under the sibling's table.

Three layers, each the one below plus a little:

    run(...)        -> stdout, with retries and a scrubbed environment
    json_out(...)   -> that stdout parsed as JSON
    values(...)     -> one A1 range read as a list of rows

Errors raise `GwsError`. Callers that want to abort with a friendlier message
catch it at their own boundary — an audit engine exits, a sync engine lets it
propagate — because that choice is about the caller, not about `gws`.
"""

import json
import os
import re
import subprocess
import sys
import time

from .report_style import redact as _scrub
from .slack import scrubbed_env

#: Substrings that mean "the API was sick, ask again". Originally the union of
#: the two full tables that existed then (`Internal error` came from only one
#: of them, and its absence in the other is the drift this module removed) —
#: and since the last of the private copies was folded in, simply the table.
#: There is no other one left to be a union OF.
TRANSIENT = ("timed out", "internalError", "Internal error", "HTTP request failed",
             "Connection reset", "Connection refused", "Connection aborted",
             "temporarily", "rateLimit", "userRateLimit", "backendError")

#: Bare `500`/`502` as substrings match any range or quota id that happens to
#: contain those digits, so a permanent error would burn the full backoff before
#: failing. Match them only as standalone HTTP statuses.
#:
#: Both inherited copies excluded an adjacent *digit* only, and carried a
#: comment claiming `A500:K500 exceeds grid limits` was handled — it was not.
#: `500` there is preceded by `A` and followed by `:`, so it matched, and that
#: permanent error retried five times over 20 seconds before failing anyway.
#: Letters are excluded on both sides now, which is what the comment always
#: said.
TRANSIENT_STATUS = re.compile(r"(?<![0-9A-Za-z])(?:429|500|502|503|504)(?![0-9A-Za-z])")

#: Default attempts, including the first. Backoff is 2s, 4s, 6s, 8s.
RETRIES = 5

#: Pass `retries=NO_RETRY` for any call that is NOT idempotent — a Drive
#: `files.create` or `files.copy`, a Sheets `appendDimension`. A request that
#: succeeded server-side but answered like a timeout is indistinguishable here
#: from one that never landed, and re-sending it makes a SECOND folder, file or
#: column under the same name. Nothing downstream detects that duplicate: the
#: next listing simply reports a subtree holding everything twice.
#:
#: This module cannot make the call itself — only the caller knows whether the
#: verb it is sending is replayable — so the rule lives at each call site, and
#: each of those sites has a test pinning the keyword.
NO_RETRY = 1


class GwsError(RuntimeError):
    """A `gws` call failed, or returned something that was not the JSON asked for."""


def transient(msg):
    """True when a failure message looks retryable rather than final."""
    return any(k in msg for k in TRANSIENT) or bool(TRANSIENT_STATUS.search(msg))


def verb(cmd):
    """The leading non-flag tokens of a gws command line: `drive files create`.

    Every failure message names this, because "gws failed (1)" out of a script
    that issues a dozen calls across two spreadsheets tells an operator nothing
    about which one died — and `install_ops_notes` exits mid-loop, so which one
    is the difference between "nothing happened" and "a grid was widened and
    its header never written".

    ONLY the verb. The rest of the argv is `--params`/`--json` blobs carrying
    sheet rows and form answers, and an error message is exactly where those
    must not appear.
    """
    out = []
    for tok in cmd[1:]:
        if tok.startswith("-"):
            break
        out.append(tok)
    return " ".join(out) or " ".join(cmd[:1])


def run(cmd, retries=RETRIES, cwd=None):
    """Run a `gws` command line, returning stdout.

    `cwd` exists because `gws` rejects `--output`/`--upload` paths outside its
    working directory, so the file helpers below run it from the file's own
    directory and pass a bare basename.

    Retries are announced on stderr: a silent 30-second backoff looks like a
    hang, and a run that succeeds on attempt 4 should still leave a trace that
    the API was sick.

    Child output is scrubbed before it reaches stderr or an exception. `gws`
    prints its environment or a request dump on some failures, and
    `scrubbed_env` deliberately KEEPS `GOOGLE_WORKSPACE_CLI_*` because `gws`
    needs it — so the OAuth client secret and refresh token are exactly what
    such a dump contains. Several callers turn the exception straight into
    `sys.exit(str(exc))`, and an operator pastes that into an issue on a public
    repo. `slides_export` scrubbed for this reason before it was folded in here;
    this module is now the path every script in the repo reaches Google through,
    `slides_export` included.
    """
    attempts = max(1, retries)
    for i in range(attempts):
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                              env=scrubbed_env())
        if proc.returncode == 0:
            return proc.stdout
        msg = (proc.stderr or "") + (proc.stdout or "")
        if i < attempts - 1 and transient(msg):
            print("  gws %s failed (attempt %d/%d), retrying in %ds: %s"
                  % (verb(cmd), i + 1, attempts, 2 * (i + 1),
                     _scrub((proc.stderr or "").strip(), 120)),
                  file=sys.stderr)
            time.sleep(2 * (i + 1))
            continue
        # stdout is in `msg` for the retry decision only — some gws builds print
        # the error there. It is NOT shown: when a `values.get` streams part of
        # its response and then exits nonzero, that partial body is sheet data,
        # and `_scrub` masks credential shapes, not a person's name. `json_out`
        # takes care not to print a body one layer up; showing it here would
        # undo that.
        shown = _scrub((proc.stderr or "").strip())
        if not shown:
            shown = "(nothing on stderr; %d chars on stdout)" % len(proc.stdout or "")
        raise GwsError("gws %s failed (%s): %s"
                       % (verb(cmd), proc.returncode, shown))
    # Unreachable: the final iteration fails the `i < attempts - 1` guard and
    # raises above. Kept as a guard against a future edit to the loop, which is
    # the only way a caller could otherwise receive None.
    raise GwsError("gws exhausted %d attempt(s): %s" % (attempts, verb(cmd)))


def clean_stdout(out):
    """`gws` stdout minus the keyring notice, ready to parse.

    Split on `"\\n"` only — NOT `splitlines()`, which also splits on U+2028 and
    friends *inside* cell values, corrupting the JSON when the lines are
    rejoined. A sheet holding a form answer with a line separator in it is not
    hypothetical here.
    """
    return "\n".join(ln for ln in out.split("\n") if "keyring backend" not in ln).strip()


def json_out(*args, params=None, body=None, retries=RETRIES, what=None):
    """Run `gws <args>` and parse its stdout as JSON.

    `what` names the thing being fetched for the error message; it defaults to
    the command line, which is usually enough to find the call.
    """
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    label = what or " ".join(args)
    text = clean_stdout(run(cmd, retries=retries))
    if not text:
        raise GwsError("gws produced no JSON output for: %s" % label)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Every other failure names what was being read; a stray non-JSON line
        # on stdout should not be the one that dies as a bare traceback.
        # The body is sheet data — intake rows, names, form free-text — and it
        # is outside the `--redact` surface, so a run masking every report line
        # would otherwise print raw rows on this one error. Not even a prefix
        # is safe: a leading row is the likeliest thing to be there. Length and
        # first character are enough to tell an HTML login page from truncated
        # JSON, and the operator can re-run to see the body locally.
        raise GwsError("gws returned non-JSON output for %s (%s): %d chars "
                       "beginning %r — re-run to see it"
                       % (label, exc, len(text), text[:1]))


def values(sheet_id, rng, retries=RETRIES):
    """Read one A1 range as a list of rows. An empty range is `[]`.

    A response that is not a batchGet response RAISES rather than reading as
    zero rows. The distinction is the whole point: callers act on "no rows" —
    `prune_organizers.read_keeplist` treats it as "this chapter has no
    keep-list" and every keep-listed organizer is then reported for removal.
    Turning a failed read into that answer is how a live roster becomes an
    empty one, and the version this replaced raised (`res["valueRanges"]`)
    precisely so it could not happen.
    """
    res = json_out("sheets", "spreadsheets", "values", "batchGet",
                   params={"spreadsheetId": sheet_id, "ranges": [rng]},
                   retries=retries, what="%s!%s" % (sheet_id, rng))
    if not isinstance(res, dict) or "valueRanges" not in res:
        raise GwsError("gws: no valueRanges reading %s!%s — this is not a "
                       "batchGet response, and must not read as zero rows"
                       % (sheet_id, rng))
    ranges = res["valueRanges"] or [{}]
    return ranges[0].get("values", [])


def download(file_id, path):
    """Fetch a Drive file's bytes to `path` and return them."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    run(["gws", "drive", "files", "get", "--params",
         json.dumps({"fileId": file_id, "supportsAllDrives": True, "alt": "media"}),
         "--output", os.path.basename(path)], cwd=os.path.dirname(path) or ".")
    with open(path, "rb") as fh:
        return fh.read()


def upload(file_id, path, raw, content_type):
    """Replace a Drive file's content with `raw` (staged at `path` for gws)."""
    with open(path, "wb") as fh:
        fh.write(raw)
    run(["gws", "drive", "files", "update", "--params",
         json.dumps({"fileId": file_id, "supportsAllDrives": True}),
         "--upload", os.path.basename(path), "--upload-content-type", content_type],
        cwd=os.path.dirname(path) or ".")
