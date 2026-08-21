#!/usr/bin/env python3
"""Snapshot critical AAIF ops data to local, versioned files.

Default target is the AAIF Community Intake Ops sheet. Pass a target to back up any
other file: a Google Drive fileId (native Docs/Sheets/Slides are exported to
.docx/.xlsx/.pptx; already-binary Drive files are downloaded as-is) or a local path
(copied verbatim).

Every run writes a NEW immutable file — nothing is ever overwritten:
    <dest>/<slug>/<UTC-timestamp>.<ext>
so the folder is a full version history you can diff or restore from (two runs of the
same target within one second get a `-1`, `-2`, … suffix so neither clobbers the other).
<dest> defaults to ./backups (kept out of git); restore is manual (re-upload the
snapshot via gws).

Usage:
    backup.py                       # back up the Intake Ops sheet
    backup.py <driveFileId>         # back up any Drive file by id
    backup.py ./path/to/file.xlsx   # back up a local file
    backup.py --dest /some/dir      # write snapshots under a different root
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys

# The one irreplaceable source of intake data — the default backup target.
INTAKE_OPS_ID = "1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o"

# Google-native mime type -> (export mime type, file extension).
EXPORT = {
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
}
# Binary mime type -> extension, for files downloaded via alt=media as-is.
BINARY_EXT = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/pdf": "pdf",
}


def gws_json(args):
    """Run a gws command expecting JSON on stdout (skips the keyring banner line)."""
    out = subprocess.run(["gws"] + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gws error: {' '.join(args[:4])}...\n{out.stderr.strip()}")
    txt = out.stdout
    i = txt.find("{")
    if i < 0:
        sys.exit(f"gws returned no JSON (got: {txt.strip()[:200]!r})")
    return json.loads(txt[i:])


def gws_download(args, out_path):
    """Run a gws command that writes bytes to --output; fail loudly on error AND
    on an empty result. A backup that silently produced a 0-byte file (e.g. Drive's
    ~10MB export limit, or a dropped connection returning exit 0) is worse than an
    error, so verify the snapshot is non-empty before we ever report success."""
    out = subprocess.run(["gws"] + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gws error: {' '.join(args[:4])}...\n{out.stderr.strip()}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        sys.exit(f"ABORT: backup wrote no data to {out_path} — the source may exceed "
                 f"the export limit or the transfer failed. Snapshot NOT trustworthy.")


# A Drive fileId is a run of URL-safe chars with no path separators or dots;
# used to tell a fileId apart from a (possibly mistyped) local path.
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _repo_root(path):
    """Git repo root containing `path`, or None when it lives outside any repo.

    Resolved from the destination itself, not the process cwd — where the script
    is run from must not decide whether the *snapshots* are protected. Walks up
    to the nearest existing dir first, since the destination may not exist yet."""
    probe_dir = os.path.abspath(path)
    while not os.path.isdir(probe_dir) and probe_dir != os.sep:
        probe_dir = os.path.dirname(probe_dir) or os.sep
    try:
        proc = subprocess.run(["git", "-C", probe_dir, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("REFUSING TO RUN: git is not installed, so this cannot verify "
                 f"that {path} is ignored. Snapshots hold every applicant's name "
                 "and email; install git or pass --dest outside any repository.")
    if proc.returncode == 0:
        return proc.stdout.strip()
    # Mirrors report_style._repo_root (the reference implementation): only
    # git's own "not a git repository" answer means outside-a-repo. Any other
    # failure (dubious ownership, corrupt .git, ...) aborts — mapping it to
    # None would silently disengage the PII guard.
    stderr = (proc.stderr or "").strip()
    if "not a git repository" in stderr.lower():
        return None
    sys.exit(f"REFUSING TO RUN: `git rev-parse` failed in {probe_dir} (exit "
             f"{proc.returncode}: {stderr[:200]}), so this cannot verify that "
             f"{path} is ignored. Fix the git error, or pass --dest outside "
             "any repository.")


def assert_dest_git_safe(dest_root):
    """Refuse to write snapshots git would happily commit.

    The default target is the full Intake Ops export — every applicant's name,
    email and free-text answers — and the repo this usually runs in is PUBLIC,
    so `.gitignore` coverage is a safety control, not tidiness. Verified at run
    time (git check-ignore) rather than trusted to a `.gitignore` rule that a
    rename or an unanchored pattern can silently stop matching. Mirrors the
    audit skills' assert_git_ignored; inlined because this script is
    deliberately self-contained (see AGENTS.md on the lib coupling).

    A destination outside every repository is fine — there is nothing to commit
    it to. `check-ignore` exits 128 (not 1) there, which is why the repo root is
    resolved first instead of treating that exit as "unignored"."""
    root = _repo_root(dest_root)
    if root is None:
        return                       # outside any repo — nothing to leak into
    # Probe through a child: `check-ignore` answers differently for a bare
    # directory name, and this works before the directory exists.
    probe = os.path.join(dest_root, "probe")
    ignored = subprocess.run(["git", "-C", root, "check-ignore", "-q", probe],
                             capture_output=True).returncode == 0
    # .gitignore has no effect on already-tracked files, so a snapshot committed
    # before the rules landed would still ride along on `git add -A` while
    # check-ignore reports the folder as ignored.
    tracked = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch",
                              dest_root], capture_output=True).returncode == 0
    if tracked:
        sys.exit(f"REFUSING TO RUN: {dest_root} already holds files TRACKED by "
                 f"the repo at {root} — git rm --cached them first. Snapshots "
                 "hold every applicant's name and email.")
    if not ignored:
        sys.exit(f"REFUSING TO RUN: {dest_root} is committable — not ignored in "
                 f"{root}. Snapshots hold every applicant's name and email. Add "
                 "the path to .gitignore, or pass --dest to a location outside "
                 "any git repository.")


def slugify(name):
    s = re.sub(r"[^\w.-]+", "-", name.strip()).strip("-.")
    return (s or "backup").lower()


def timestamp():
    # UTC, filename-safe, sorts chronologically. Second resolution is plenty.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def snapshot_path(dest_root, slug, ext):
    """A fresh, non-colliding path. Never returns a path that already exists, so
    two runs in the same second can't overwrite each other (breaking "immutable")."""
    d = os.path.join(dest_root, slug)
    os.makedirs(d, exist_ok=True)
    stamp = timestamp()
    p = os.path.join(d, f"{stamp}.{ext}")
    n = 1
    while os.path.exists(p):
        p = os.path.join(d, f"{stamp}-{n}.{ext}")
        n += 1
    return p


def backup_local(path, dest_root):
    if not os.path.isfile(path):
        what = "is a directory" if os.path.isdir(path) else "not found"
        sys.exit(f"ABORT: local file {what}: {path}")
    base = os.path.basename(path)
    slug = slugify(os.path.splitext(base)[0])
    ext = base.rsplit(".", 1)[1] if "." in base else "bin"
    out = snapshot_path(dest_root, slug, ext)
    shutil.copy2(path, out)
    if os.path.getsize(out) == 0:
        sys.exit(f"ABORT: copied 0 bytes from {path} — snapshot NOT trustworthy.")
    return out


def backup_drive(file_id, dest_root):
    meta = gws_json(["drive", "files", "get", "--params",
                     json.dumps({"fileId": file_id, "fields": "name,mimeType"}),
                     "--format", "json"])
    name, mime = meta.get("name", file_id), meta.get("mimeType", "")
    # A Google-native type we can't export (Forms, Drawings, Sites, Jamboard) is
    # not fetchable via alt=media either — refuse rather than write a broken .bin.
    if mime.startswith("application/vnd.google-apps.") and mime not in EXPORT:
        sys.exit(f"ABORT: {name!r} is a {mime} — no export format; can't back it up here.")
    slug = slugify(name)
    if mime in EXPORT:
        export_mime, ext = EXPORT[mime]
        out = snapshot_path(dest_root, slug, ext)
        gws_download(["drive", "files", "export", "--params",
                      json.dumps({"fileId": file_id, "mimeType": export_mime}),
                      "--output", out], out)
    else:
        ext = BINARY_EXT.get(mime, "bin")
        out = snapshot_path(dest_root, slug, ext)
        gws_download(["drive", "files", "get", "--params",
                      json.dumps({"fileId": file_id, "alt": "media"}),
                      "--output", out], out)
    return out


def main():
    ap = argparse.ArgumentParser(description="Snapshot AAIF ops data to versioned files.")
    ap.add_argument("target", nargs="?",
                    help="Drive fileId or local path (default: Intake Ops sheet)")
    ap.add_argument("--dest", default=os.path.join(os.getcwd(), "backups"),
                    help="Root folder for snapshots (default: ./backups)")
    a = ap.parse_args()
    dest_root = os.path.abspath(a.dest)
    assert_dest_git_safe(dest_root)   # before ANY fetch: fail in a second, not after a pull
    # Dispatch explicitly so a mistyped local path can't silently fall through to
    # Drive (and die with a cryptic 404): existing path -> local; a clean Drive-id
    # shape -> Drive; anything else is a mistake, so say so.
    if a.target is None:
        out = backup_drive(INTAKE_OPS_ID, dest_root)
    elif os.path.exists(a.target):
        out = backup_local(a.target, dest_root)
    elif DRIVE_ID_RE.match(a.target):
        out = backup_drive(a.target, dest_root)
    else:
        sys.exit(f"ABORT: {a.target!r} is neither an existing local file nor a "
                 f"Drive fileId. Check the path, or pass a valid fileId.")
    size = os.path.getsize(out)
    print(f"Backed up -> {out}  ({size:,} bytes)")


if __name__ == "__main__":
    main()
