#!/usr/bin/env python3
"""Sync AAIF organizer badges (SVG + PNG) to the chapter-badges Drive folder.

Reads the canonical chapter list from the "Chapters" Drive folder, generates
each chapter's 4 badge files (colour/white SVG + their 1000px PNG renders)
with make_badges.py, and uploads whatever the chapter-badges folder is
missing. Existing files are left untouched unless --regenerate is passed.

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
import make_badges  # noqa: E402

CHAPTERS_PARENT = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"   # the "Chapters" Drive folder
BADGES_PARENT = "1ViKjLZh-4KrMBVihOGQyAL2SVsXcI3B9"      # the chapter-badges Drive folder
FOLDER = "application/vnd.google-apps.folder"
# Not a real chapter -- the clone source create_chapter.py rebrands from.
NOT_A_CHAPTER = {"templatecity"}

MIME_BY_EXT = {".svg": "image/svg+xml", ".png": "image/png"}


def _scrubbed_env():
    """os.environ minus the Slack/Luma secrets. gws never needs them, and a child
    process inherits everything by default. Local (not lib) so this script stays
    standalone."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("AAIF_SLACK_") and k.endswith("_TOKEN"))
            and k != "LUMA_API_KEY"}


_TRANSIENT = ("timed out", "Connection reset", "503", "502", "429")


def _gws(cmd, cwd=None, retries=5):
    for i in range(retries):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=_scrubbed_env())
        if r.returncode == 0:
            return r.stdout
        msg = (r.stderr or "") + (r.stdout or "")
        if i < retries - 1 and any(k in msg for k in _TRANSIENT):
            time.sleep(2 * (i + 1))
            continue
        raise RuntimeError("gws failed (%s): %s" % (r.returncode, msg.strip()[:400]))


def gws_json(*args, params=None, body=None):
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = _gws(cmd)
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
    return gws_json("drive", "files", "create",
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
          "--upload-content-type", mime], cwd=d)


def upload_update(file_id, local_path):
    mime, d = _mime_and_dir(local_path)
    _gws(["gws", "drive", "files", "update",
          "--params", json.dumps({"fileId": file_id, "supportsAllDrives": True}),
          "--upload", os.path.basename(local_path),
          "--upload-content-type", mime], cwd=d)


# --- planning ----------------------------------------------------------------

def canonical_chapters():
    """{slug: display name} for every real chapter folder, keyed by
    make_badges.slugify -- the same slug convention the badges folder already
    uses (e.g. "Delhi NCR" -> "delhi_ncr", "Mexico City" -> "mexico_city")."""
    chapters = {}
    collisions = {}
    for f in list_children(CHAPTERS_PARENT):
        if f["mimeType"] != FOLDER:
            continue
        if f["name"].strip().lower() in NOT_A_CHAPTER:
            continue
        slug = make_badges.slugify(f["name"])
        if slug in chapters and chapters[slug] != f["name"]:
            collisions.setdefault(slug, {chapters[slug]}).add(f["name"])
        chapters[slug] = f["name"]
    if collisions:
        raise SystemExit("ABORT: chapter names collide on the same badge slug: %s"
                          % ", ".join("%s -> %s" % (s, sorted(names))
                                       for s, names in collisions.items()))
    return chapters


def needed_filenames(slug):
    return [f"organizer_badge_{slug}_colour.svg", f"organizer_badge_{slug}_white.svg",
            f"organizer_badge_{slug}_colour_1000.png", f"organizer_badge_{slug}_white_1000.png"]


def plan(chapters, badge_folders, regenerate):
    """Returns (folders_to_create: {slug: name}, files_to_upload: {slug: (folder_id_or_None, [filenames])})."""
    folders_to_create = {}
    files_to_upload = {}
    for slug, name in sorted(chapters.items()):
        entry = badge_folders.get(slug)
        need = needed_filenames(slug)
        if entry is None:
            folders_to_create[slug] = name
            files_to_upload[slug] = (None, need)
            continue
        existing_names = {c["name"] for c in entry["children"]}
        missing = need if regenerate else [n for n in need if n not in existing_names]
        if missing:
            files_to_upload[slug] = (entry["id"], missing)
    return folders_to_create, files_to_upload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                     help="LIVE WRITE: create folders/upload files. Without it this only plans.")
    ap.add_argument("--regenerate", action="store_true",
                     help="Re-generate and overwrite every existing badge file too "
                          "(use after a design change to make_badges.py). Requires --write "
                          "to actually overwrite; harmless to pass without it (just widens the plan).")
    ap.add_argument("--chapter", help="Only sync one chapter (Drive folder name, case-insensitive substring match)")
    a = ap.parse_args()

    all_chapters = canonical_chapters()
    chapters = all_chapters
    if a.chapter:
        needle = a.chapter.strip().lower()
        chapters = {s: n for s, n in all_chapters.items() if needle in n.lower()}
        if not chapters:
            sys.exit(f"ABORT: no chapter folder matches {a.chapter!r}")

    raw_badge_children = list_children(BADGES_PARENT)
    badge_folders = {}
    stray_files = []
    for f in raw_badge_children:
        if f["mimeType"] != FOLDER:
            stray_files.append(f["name"])
            continue
        badge_folders[f["name"]] = {"id": f["id"]}
    # Fetch each folder's contents only for slugs actually in scope (the
    # --chapter filter, or every chapter on a full run) -- an orphan folder,
    # or any folder outside a --chapter filter, needs its NAME (already known
    # from the listing above) but never its children.
    for slug in chapters:
        entry = badge_folders.get(slug)
        if entry is not None:
            entry["children"] = list_children(entry["id"])

    # Orphans are judged against the FULL canonical set, never the --chapter
    # filter -- otherwise every other real chapter's folder would misreport as
    # having "no matching chapter" just because it wasn't the one asked for.
    orphans = sorted(n for n in badge_folders if n not in all_chapters)

    folders_to_create, files_to_upload = plan(chapters, badge_folders, a.regenerate)

    print(f"Chapters (canonical): {len(chapters)}")
    print(f"Badge folders present: {len(badge_folders)}")
    if stray_files:
        print(f"Stray non-folder items in badges parent (ignored): {stray_files}")
    if orphans:
        print(f"Badge folders with no matching chapter (left alone): {orphans}")
    print()

    if not folders_to_create and not files_to_upload:
        print("Up to date -- nothing to create or upload.")
        return

    for slug, name in sorted(folders_to_create.items()):
        print(f"+ create folder  {slug}/   ({name})")
    for slug, (folder_id, files) in sorted(files_to_upload.items()):
        verb = "upload" if folder_id is None else ("overwrite" if a.regenerate else "upload")
        for fn in files:
            print(f"  {verb:<9} {slug}/{fn}")

    if not a.write:
        print("\nPlan only -- re-run with --write to create/upload.")
        return

    tmp = tempfile.mkdtemp(prefix="aaif-badges-")
    try:
        total = 0
        for slug, (folder_id, files) in sorted(files_to_upload.items()):
            name = chapters[slug]
            if slug in folders_to_create:
                # Folder name is the SLUG, matching every existing badge folder
                # (e.g. "mexico_city", not the chapter's display name).
                folder_id = create_folder(slug, BADGES_PARENT)
                print(f"created folder {slug}/ -> {folder_id}")
                existing_by_name = {}
            else:
                existing_by_name = {c["name"]: c["id"] for c in badge_folders[slug]["children"]}
            make_badges.build(name, tmp, slug)
            for fn in files:
                local_path = os.path.join(tmp, slug, fn)
                existing_id = existing_by_name.get(fn)
                if existing_id:
                    upload_update(existing_id, local_path)
                    print(f"  updated  {slug}/{fn}")
                else:
                    upload_new(fn, folder_id, local_path)
                    print(f"  uploaded {slug}/{fn}")
                total += 1
        print(f"\nDone. {total} file(s) written.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
