"""Where the toolkit keeps what it writes: reports, caches, backups.

Two homes, chosen by how the toolkit is installed:

- **A developer checkout** keeps everything at the repo root, where
  `.gitignore` already covers `sync-reports/`, `.slack-audit-cache/`,
  `**/backups/*` and the rest. This is how the estate has always run.
- **An installed plugin** — Goose (`~/.agents/plugins/<name>/`), Claude
  (`~/.claude/plugins/…/<version>/`), or any copy without `.git` — writes to a
  per-user folder instead. The install folder is the wrong place twice over:
  Claude keeps one folder per plugin *version*, so the next update orphans a
  backup taken "before a risky edit"; and the session's working folder, the
  other obvious choice, is the operator's own and often a synced Documents
  folder.

`AAIF_DATA_DIR` overrides both. Never the cwd: from Goose or Claude Desktop the
cwd belongs to the operator, and a member directory must not land there.

`aaif-sync/scripts/sync.py` and `aaif-backup/scripts/backup.py` stay free of
`lib` and carry a copy of `data_dir()`; `tests/test_pinned_copies.py` pins
both to this one.
"""

import os
import sys

ENV = "AAIF_DATA_DIR"
#: The toolkit root: lib/aaif_events/ → lib/ → root.
TOOLKIT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", ".."))
#: Path segments that mean "a client installed this", whatever the root.
PLUGIN_HOMES = (os.sep + os.path.join(".agents", "plugins") + os.sep,
                os.sep + os.path.join(".claude", "plugins") + os.sep)


def is_checkout(toolkit=TOOLKIT):
    """True for a developer's git checkout, False for an installed plugin."""
    return (os.path.exists(os.path.join(toolkit, ".git"))
            and not any(home in toolkit + os.sep for home in PLUGIN_HOMES))


def user_data_dir(platform=sys.platform, environ=os.environ):
    """The per-user folder an installed plugin writes to."""
    home = os.path.expanduser("~")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "AAIF")
    base = environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, "aaif")


def data_dir(toolkit=TOOLKIT, environ=os.environ, platform=sys.platform):
    """The folder this install writes reports, caches and backups under.

    A relative `AAIF_DATA_DIR` is ignored, with a warning: it would resolve
    against the cwd this module exists to avoid, and `sync.py` runs each step
    with that folder as its cwd, so every step would resolve it again one
    level deeper."""
    override = os.path.expanduser(environ.get(ENV, "").strip())
    if override and os.path.isabs(override):
        return override
    if override:
        print("aaif: ignoring %s=%s — it must be an absolute path"
              % (ENV, override), file=sys.stderr)
    if is_checkout(toolkit):
        return toolkit
    return user_data_dir(platform, environ)


def path(*parts):
    """`parts` joined under data_dir()."""
    return os.path.join(data_dir(), *parts)


def resolve(p):
    """An absolute `p` (after `~`) unchanged; a relative one under data_dir(),
    never the cwd. An agent may pass `~/x` quoted, so the shell never expands it."""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else path(p)


if __name__ == "__main__":
    # `PYTHONPATH=<toolkit>/lib python3 -m aaif_events.datadir` — the folder,
    # for a cleanup command or for telling the operator where a report is.
    print(data_dir())
