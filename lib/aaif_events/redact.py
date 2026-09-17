"""One redaction surface for every ops script.

Nine scripts used to carry their own byte-identical copy of these helpers, and
the duplication was deliberate: an earlier refactor moved `redact_email` into a
sibling module but left `REDACT` and `set_redaction` behind, so `--redact`
became a no-op for the one line that printed an address — the flag was set in
one module and read in another, and a real address reached a CI log on a public
repo.

That bug is a property of *splitting* the flag from the helpers, not of sharing
them. Here the flag and every function that reads it live in the same module, so
a caller that does

    from aaif_events.redact import add_redact_flag, redact_email, set_redaction

gets a `set_redaction` that governs the `redact_email` it just imported. The
rule that keeps it that way: **a skill script must never define its own
`REDACT`, `set_redaction` or `redact_*`.** `scripts/check_no_local_redaction.py`
enforces it at commit time, because the failure mode is silent — masked output
looks identical to output that was never sensitive.

Redaction masks; it does not promise anonymity. A first initial plus a chapter
still narrows a small roster. These helpers exist so a *log* does not publish an
identifier, not so a report can be handed to someone who should not have it.
"""

import argparse
import os
import sys

#: True when this looks like a CI run, which is where redaction most needs to
#: default on: a workflow log on a public repo is world-readable forever. The
#: parse is strict — `CI=0` or `CI=false` is not CI — so a local shell that
#: happens to export `CI` for another tool does not silently mask a report the
#: operator is reading.
CI_REDACT_DEFAULT = os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")

#: The live flag. Read at call time by every helper below, so a test may set it
#: directly (`redact.REDACT = True`) instead of going through `set_redaction`,
#: which prints. Never shadow this in a consumer module.
REDACT = False


def redacting():
    """True when masking is on. For a caller that formats its own line."""
    return REDACT


def redact_email(e):
    """`ada@example.com` -> `a***@***.com`: first initial and TLD only.

    The TLD survives because "this address is at a .edu" is often the thing a
    reader needs, and it identifies nobody.
    """
    if not REDACT or not e or "@" not in e:
        return e
    local, _, domain = e.partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else "***"
    return "%s***@***.%s" % (local[:1], tld)


def redact_name(n):
    """`ada lovelace` -> `A.`: a first initial, upper-cased."""
    if not REDACT or not n or not n.strip():
        return n
    return n.strip()[0].upper() + "."


def redact_id(i):
    """`U0AAAAAAAAA` -> `U0…AA`. A Slack id is an identifier like any other.

    The shape survives so a reader can still tell two ids apart inside one
    line; the account does not.
    """
    if not REDACT or not i:
        return i
    return "%s…%s" % (i[:2], i[-2:]) if len(i) > 5 else "***"


def redact_text(v):
    """Free-form text is replaced wholesale, not trimmed.

    A form answer (or the repr of a malformed cell) may quote a person in any
    position, so there is no prefix that is safe to keep.
    """
    return "[redacted]" if REDACT and v else v


def redact_doc_text(t):
    """Document text -> `<N chars>`, keeping only its length.

    Distinct from `redact_text` because the caller prints a `repr()` when
    masking is off: these strings are document bodies, and a chapter CRM's are
    member rows.
    """
    if not REDACT or not t:
        return repr(t)
    return "<%d chars>" % len(t)


def redact_names_cell(cell):
    """A `'; '`-joined list of names -> the same list, each masked."""
    if not REDACT or not cell:
        return cell
    return "; ".join(redact_name(x.strip()) for x in cell.split(";"))


def add_redact_flag(ap, masks="emails (a***@***.tld) and names (first initial)"):
    """Add `--redact` / `--no-redact`, defaulting on under CI.

    `masks` names what this particular script actually masks, so its `--help`
    does not promise redaction of a field it never prints.
    """
    ap.add_argument("--redact", action=argparse.BooleanOptionalAction,
                    default=CI_REDACT_DEFAULT,
                    help="mask %s on stdout; default on when CI is set" % masks)


def set_redaction(on):
    """Apply the parsed flag; one stderr line says so when masking is on."""
    global REDACT
    REDACT = bool(on)
    if REDACT:
        print("redaction ON (CI set; pass --no-redact to disable)"
              if CI_REDACT_DEFAULT else "redaction ON (--redact)", file=sys.stderr)
