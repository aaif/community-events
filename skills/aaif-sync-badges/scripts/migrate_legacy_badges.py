#!/usr/bin/env python3
"""One-time migration: move badges out of the old shared chapter-badges
folder and into each chapter's own `Badges/` subfolder (see sync_badges.py).

For each `<slug>/` subfolder under the legacy parent, this MOVES (Drive
reparent -- addParents/removeParents; no re-upload, no lost revision history)
every file into the matching chapter's `Badges/` subfolder, creating that
subfolder if it doesn't exist yet. A file already present at the destination
(by name) is left alone and reported, never overwritten here -- re-run
sync_badges.py --regenerate afterward if you also want content refreshed.

Once a legacy slug folder is fully emptied by the move, it can optionally be
trashed (Drive trash, not permanent delete) with --trash-empty.

A legacy slug folder that matches no canonical chapter (renamed/retired chapter,
or a stray item) is reported and never touched -- resolve those by hand.

Usage:
    # Plan (default) -- nothing is moved:
    python migrate_legacy_badges.py

    # Apply the moves:
    python migrate_legacy_badges.py --write

    # Also trash (Drive trash, recoverable) legacy folders left empty by the move:
    python migrate_legacy_badges.py --write --trash-empty
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_badges as sb  # noqa: E402

LEGACY_BADGES_PARENT = "1ViKjLZh-4KrMBVihOGQyAL2SVsXcI3B9"  # the old chapter-badges Drive folder


def move_file(file_id, new_parent, old_parent):
    sb.gws_json("drive", "files", "update",
                params={"fileId": file_id, "addParents": new_parent,
                        "removeParents": old_parent, "supportsAllDrives": True})


def trash_folder(folder_id):
    sb.gws_json("drive", "files", "update",
                params={"fileId": folder_id, "supportsAllDrives": True},
                body={"trashed": True})


def plan_legacy_folders(chapters):
    """Returns (legacy_with_files, all_legacy_folders, orphans):
    - legacy_with_files: {slug: {"id", "files"}} -- only folders with something to move
    - all_legacy_folders: {slug: folder_id} -- every legacy folder matching a chapter,
      including ones already emptied by a prior run (needed so --trash-empty has
      something to act on even when there is nothing left to migrate)
    - orphans: [(name, [file names])] for slugs with no matching chapter
    """
    legacy_with_files = {}
    all_legacy_folders = {}
    orphans = []
    for f in sb.list_children(LEGACY_BADGES_PARENT):
        if f["mimeType"] != sb.FOLDER:
            continue  # e.g. a stray .DS_Store -- not this script's business
        files = sb.list_children(f["id"])
        if f["name"] not in chapters:
            if files:
                orphans.append((f["name"], [c["name"] for c in files]))
            continue
        all_legacy_folders[f["name"]] = f["id"]
        if files:
            legacy_with_files[f["name"]] = {"id": f["id"], "files": files}
    return legacy_with_files, all_legacy_folders, orphans


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="LIVE WRITE: move files. Without it this only plans.")
    ap.add_argument("--trash-empty", action="store_true",
                     help="Also trash (Drive trash, recoverable) a legacy folder left empty by the move")
    a = ap.parse_args()
    if a.trash_empty and not a.write:
        sys.exit("ABORT: --trash-empty only applies together with --write.")

    chapters = sb.canonical_chapters()
    legacy, all_legacy_folders, orphans = plan_legacy_folders(chapters)

    print(f"Legacy chapter folders with files to move: {len(legacy)}")
    if orphans:
        print(f"Legacy folders with no matching chapter (left alone): "
              f"{[name for name, _ in orphans]}")
    print()

    moves = []  # (chapter_name, slug, legacy_folder_id, file, dest_folder_id_or_None)
    if not legacy:
        print("Nothing to migrate.")
    else:
        for slug, entry in sorted(legacy.items()):
            chapter = chapters[slug]
            dest_id = sb.find_badges_subfolder(chapter["folder_id"])
            existing_names = {c["name"] for c in sb.list_children(dest_id)} if dest_id else set()
            for f in entry["files"]:
                if f["name"] in existing_names:
                    print(f"  skip     {chapter['name']}/Badges/{f['name']}  (already present)")
                    continue
                moves.append((chapter["name"], slug, entry["id"], f, dest_id))
                verb = "move" if dest_id else "move (creates Badges/)"
                print(f"  {verb:<22} {chapter['name']}/{sb.BADGES_SUBFOLDER}/{f['name']}")

    if not a.write:
        if moves:
            print("\nPlan only -- re-run with --write to move.")
        return

    dest_cache = {}  # slug -> resolved Badges folder id, created at most once per chapter
    moved = 0
    for chapter_name, slug, legacy_folder_id, f, dest_id in moves:
        if slug not in dest_cache:
            dest_cache[slug] = dest_id or sb.create_folder(sb.BADGES_SUBFOLDER, chapters[slug]["folder_id"])
            if dest_id is None:
                print(f"created folder {chapter_name}/{sb.BADGES_SUBFOLDER}/ -> {dest_cache[slug]}")
        move_file(f["id"], dest_cache[slug], legacy_folder_id)
        print(f"  moved    {chapter_name}/{sb.BADGES_SUBFOLDER}/{f['name']}")
        moved += 1
    if moves:
        print(f"\nMoved {moved} file(s).")

    if a.trash_empty:
        trashed, failed = 0, []
        for slug, folder_id in sorted(all_legacy_folders.items()):
            if sb.list_children(folder_id):
                continue  # still has files -- not this run's business
            try:
                trash_folder(folder_id)
            except RuntimeError as e:
                # One folder's permissions (owned/shared differently than its
                # siblings) must not stop the rest from being cleaned up --
                # the files were already safely moved regardless of this step.
                failed.append((slug, str(e)))
                continue
            print(f"trashed empty legacy folder: {slug}/")
            trashed += 1
        print(f"\nTrashed {trashed} now-empty legacy folder(s).")
        if failed:
            print(f"Could not trash {len(failed)} folder(s) (left in place, harmless):")
            for slug, err in failed:
                print(f"  {slug}/: {err.splitlines()[0]}")


if __name__ == "__main__":
    main()
