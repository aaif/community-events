#!/usr/bin/env python3
"""Sync AAIF organizer badges (SVG + PNG) into each chapter's own Drive folder.

Reads the canonical chapter list from the "Chapters" Drive folder, generates
each chapter's badge files -- 4 from make_badges.py (colour/white ring badge)
plus 2 from make_agent_badge.py (the chapter's own agent mascot, in the real
AAIF design-system tokens) -- and uploads whatever that chapter's own
`Badges/` subfolder is missing. Existing files are left untouched unless
--regenerate is passed.

Chapters that still have badges under the old shared chapter-badges parent
folder (pre per-chapter layout) are not touched here -- see
migrate_legacy_badges.py.

Usage:
    # Plan (default) -- nothing is created/uploaded, just reported:
    python sync_badges.py

    # Apply -- create missing chapter subfolders and upload missing files:
    python sync_badges.py --write

    # Regenerate every file (design refresh) and overwrite what's already there:
    python sync_badges.py --write --regenerate

    # One chapter only (matches the Drive chapter folder name, case-insensitive):
    python sync_badges.py --chapter "Mexico City" --write
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_agent_badge, make_badges  # noqa: E402

CHAPTERS_PARENT = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"   # the "Chapters" Drive folder
BADGES_SUBFOLDER = "Badges"                              # per-chapter subfolder name
FOLDER = "application/vnd.google-apps.folder"
# Not a real chapter -- the clone source create_chapter.py rebrands from.
NOT_A_CHAPTER = {"templatecity"}

MIME_BY_EXT = {".svg": "image/svg+xml", ".png": "image/png"}

# Each badge style: its generator MODULE (looked up as module.build(name,
# outroot, slug) at call time, not bound to the function object here, so a
# style's generator can still be mocked/swapped after import) and the
# filename suffixes it produces (organizer_badge_<slug>_<suffix>). Keeping
# them apart means a chapter only missing an agent-style file doesn't need
# make_badges' cairosvg dependency invoked, and vice versa.
STYLES = (
    (make_badges, ("colour.svg", "white.svg", "colour_1000.png", "white_1000.png")),
    (make_agent_badge, ("agent.svg", "agent_1000.png")),
)
# styles_needed_for() matches filenames by suffix across ALL styles at once --
# a future style whose suffixes overlap another's would make it silently
# select the wrong module. Assert that stays impossible rather than relying on
# every future editor to notice.
assert len({s for _module, suffixes in STYLES for s in suffixes}) == \
    sum(len(suffixes) for _module, suffixes in STYLES), \
    "STYLES suffixes must be disjoint across styles -- see styles_needed_for()"


def _scrubbed_env():
    """os.environ minus the Slack/Luma secrets. gws never needs them, and a child
    process inherits everything by default. Local (not lib) so this script stays
    standalone."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("AAIF_SLACK_") and k.endswith("_TOKEN"))
            and k != "LUMA_API_KEY"}


_TRANSIENT = ("timed out", "Connection reset", "503", "502", "429")


def _gws(cmd, cwd=None, retries=5):
    """Run a gws command, retrying transient-looking failures.

    retries=1 (no retry) is REQUIRED for any non-idempotent write (a Drive
    `files.create`, folder or file): if the create actually succeeded
    server-side but the response looked like a timeout, retrying it creates a
    second folder/file with the same name, and nothing on the destination side
    detects that duplicate. Reads and `files.update` (by file id) are safe to
    retry at the default."""
    for i in range(retries):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_scrubbed_env())
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < retries - 1 and any(k in msg for k in _TRANSIENT):
            time.sleep(2 * (i + 1))
            continue
        raise RuntimeError("gws failed (%s): %s" % (r.returncode, msg.strip()[:400]))


def gws_json(*args, params=None, body=None, retries=5):
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = _gws(cmd, retries=retries)
    s = "\n".join(l for l in out.split("\n") if "keyring backend" not in l).strip()
    if not s:
        raise RuntimeError("gws produced no JSON output for: %s" % " ".join(args))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise RuntimeError("gws returned non-JSON output for %s: %s" % (" ".join(args), s[:200]))


def list_children(folder_id):
    """Every child of `folder_id`, following pagination to the end."""
    out, token = [], None
    while True:
        params = {
            "q": "'%s' in parents and trashed=false" % folder_id,
            "fields": "nextPageToken, files(id,name,mimeType,size)", "pageSize": 1000,
            "supportsAllDrives": True, "includeItemsFromAllDrives": True}
        if token:
            params["pageToken"] = token
        res = gws_json("drive", "files", "list", params=params)
        out.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            return out


def create_folder(name, parent):
    return gws_json("drive", "files", "create", retries=1,  # non-idempotent write, see _gws
                     params={"supportsAllDrives": True},
                     body={"name": name, "mimeType": FOLDER, "parents": [parent]})["id"]


def _mime_and_dir(local_path):
    # gws rejects --upload paths outside its cwd, so callers run it in the file's dir.
    return MIME_BY_EXT[os.path.splitext(local_path)[1]], os.path.dirname(local_path) or "."


def upload_new(name, parent, local_path):
    mime, d = _mime_and_dir(local_path)
    _gws(["gws", "drive", "files", "create",
          "--params", json.dumps({"supportsAllDrives": True}),
          "--json", json.dumps({"name": name, "parents": [parent]}),
          "--upload", os.path.basename(local_path),
          "--upload-content-type", mime], cwd=d, retries=1)  # non-idempotent write, see _gws


def upload_update(file_id, local_path):
    mime, d = _mime_and_dir(local_path)
    _gws(["gws", "drive", "files", "update",
          "--params", json.dumps({"fileId": file_id, "supportsAllDrives": True}),
          "--upload", os.path.basename(local_path),
          "--upload-content-type", mime], cwd=d)


# --- planning ----------------------------------------------------------------

def canonical_chapters():
    """{slug: {"name": display name, "folder_id": Drive folder id}} for every
    real chapter folder, keyed by make_badges.slugify -- the same slug
    convention every generated filename uses (e.g. "Delhi NCR" -> "delhi_ncr",
    "Mexico City" -> "mexico_city")."""
    chapters = {}
    collisions = {}
    for f in list_children(CHAPTERS_PARENT):
        if f["mimeType"] != FOLDER:
            continue
        if f["name"].strip().lower() in NOT_A_CHAPTER:
            continue
        slug = make_badges.slugify(f["name"])
        if slug in chapters and chapters[slug]["name"] != f["name"]:
            collisions.setdefault(slug, {chapters[slug]["name"]}).add(f["name"])
        chapters[slug] = {"name": f["name"], "folder_id": f["id"]}
    if collisions:
        raise SystemExit("ABORT: chapter names collide on the same badge slug: %s"
                          % ", ".join("%s -> %s" % (s, sorted(names))
                                       for s, names in collisions.items()))
    return chapters


def find_badges_subfolder(chapter_folder_id):
    """The chapter's own `Badges/` subfolder id, or None if it doesn't exist yet."""
    for f in list_children(chapter_folder_id):
        if f["mimeType"] == FOLDER and f["name"] == BADGES_SUBFOLDER:
            return f["id"]
    return None


def needed_filenames(slug):
    return [f"organizer_badge_{slug}_{suffix}" for _builder, suffixes in STYLES for suffix in suffixes]


def styles_needed_for(files):
    """Which STYLES entries must run to produce every filename in `files`."""
    return [(module, suffixes) for module, suffixes in STYLES
            if any(fn.endswith(suffix) for fn in files for suffix in suffixes)]


def plan(chapters, badges_folders, children_by_slug, regenerate):
    """Returns (folders_to_create: {slug: name}, files_to_upload: {slug: (folder_id_or_None, [filenames])}).

    `children_by_slug` must already hold an entry for every slug in `chapters`
    that also has a matching `badges_folders` entry -- membership in it (not an
    optional key on `badges_folders`) is what means "already fetched"."""
    folders_to_create = {}
    files_to_upload = {}
    for slug, name in sorted((s, c["name"]) for s, c in chapters.items()):
        folder_id = badges_folders.get(slug)
        need = needed_filenames(slug)
        if folder_id is None:
            folders_to_create[slug] = name
            files_to_upload[slug] = (None, need)
            continue
        existing_names = {c["name"] for c in children_by_slug[slug]}
        missing = need if regenerate else [n for n in need if n not in existing_names]
        if missing:
            files_to_upload[slug] = (folder_id, missing)
    return folders_to_create, files_to_upload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                     help="LIVE WRITE: create folders/upload files. Without it this only plans.")
    ap.add_argument("--regenerate", action="store_true",
                     help="Re-generate and overwrite every existing badge file too "
                          "(use after a design change). Requires --write to actually "
                          "overwrite; harmless to pass without it (just widens the plan).")
    ap.add_argument("--chapter", help="Only sync one chapter (Drive folder name, case-insensitive substring match)")
    a = ap.parse_args()

    all_chapters = canonical_chapters()
    chapters = all_chapters
    if a.chapter:
        needle = a.chapter.strip().lower()
        chapters = {s: c for s, c in all_chapters.items() if needle in c["name"].lower()}
        if not chapters:
            sys.exit(f"ABORT: no chapter folder matches {a.chapter!r}")

    badges_folders = {}       # slug -> Badges subfolder id (only where it exists)
    children_by_slug = {}     # slug -> that subfolder's children (only where fetched)
    for slug, chapter in chapters.items():
        folder_id = find_badges_subfolder(chapter["folder_id"])
        if folder_id is not None:
            badges_folders[slug] = folder_id
            children_by_slug[slug] = list_children(folder_id)

    folders_to_create, files_to_upload = plan(chapters, badges_folders, children_by_slug, a.regenerate)

    print(f"Chapters (canonical): {len(chapters)}")
    print(f"Chapters with a {BADGES_SUBFOLDER}/ subfolder already: {len(badges_folders)}")
    print()

    if not folders_to_create and not files_to_upload:
        print("Up to date -- nothing to create or upload.")
        return

    for slug, name in sorted(folders_to_create.items()):
        print(f"+ create folder  {name}/{BADGES_SUBFOLDER}/")
    for slug, (folder_id, files) in sorted(files_to_upload.items()):
        name = chapters[slug]["name"]
        # Per-file, not per-chapter: under --regenerate a partially-complete
        # folder still has files that were never there to "overwrite".
        existing_names = {c["name"] for c in children_by_slug.get(slug, [])}
        for fn in files:
            verb = "overwrite" if fn in existing_names else "upload"
            print(f"  {verb:<9} {name}/{BADGES_SUBFOLDER}/{fn}")

    if not a.write:
        print("\nPlan only -- re-run with --write to create/upload.")
        return

    tmp = tempfile.mkdtemp(prefix="aaif-badges-")
    try:
        total = 0
        for slug, (folder_id, files) in sorted(files_to_upload.items()):
            name = chapters[slug]["name"]
            if slug in folders_to_create:
                folder_id = create_folder(BADGES_SUBFOLDER, chapters[slug]["folder_id"])
                print(f"created folder {name}/{BADGES_SUBFOLDER}/ -> {folder_id}")
                existing_by_name = {}
            else:
                existing_by_name = {c["name"]: c["id"] for c in children_by_slug[slug]}
            for module, _suffixes in styles_needed_for(files):
                module.build(name, tmp, slug)
            for fn in files:
                local_path = os.path.join(tmp, slug, fn)
                existing_id = existing_by_name.get(fn)
                if existing_id:
                    upload_update(existing_id, local_path)
                    print(f"  updated  {name}/{BADGES_SUBFOLDER}/{fn}")
                else:
                    upload_new(fn, folder_id, local_path)
                    print(f"  uploaded {name}/{BADGES_SUBFOLDER}/{fn}")
                total += 1
        print(f"\nDone. {total} file(s) written.")
    finally:
        try:
            shutil.rmtree(tmp)
        except OSError as e:
            print(f"warning: could not remove temp dir {tmp}: {e}")


if __name__ == "__main__":
    main()
