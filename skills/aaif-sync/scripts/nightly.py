#!/usr/bin/env python3
"""The scheduled-job entry point. A thin wrapper over `sync.py --unattended`.

This file used to carry its own copy of the pipeline: the engine list, the
order, the gates, the exit-code contract. That made two definitions of one
order — the SKILL.md's and this script's — and the order is load-bearing (the
CRM must hold the right people before access is granted). `sync.py` is now the
single definition; this wrapper exists only to name the unattended defaults a
scheduler wants, and to keep the `nightly.py` path working for anything that
already calls it.

What "unattended" means is decided in `sync.py`, not here:

  * only the phases in `sync.UNATTENDED_PHASES` run,
  * `--i-have-approval` is refused outright, so no Slack step can fire,
  * `access` stays in report mode even under `--write`.

Usage:
    python3 nightly.py                 # report-only run of the unattended phases
    python3 nightly.py --write         # apply what is safe to apply unattended
    python3 nightly.py crm resources   # a subset, still in pipeline order

Anything a human is present for — the Slack steps, the Luma sweep, the audit —
belongs in `sync.py` (or the `aaif-sync` skill), not here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync  # noqa: E402


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--unattended" not in argv:
        argv.append("--unattended")
    if not any(x == "--report-dir" or x.startswith("--report-dir=") for x in argv):
        argv += ["--report-dir", os.path.join(sync.REPO, "nightly-reports")]
    return sync.main(argv)


if __name__ == "__main__":
    sys.exit(main())
