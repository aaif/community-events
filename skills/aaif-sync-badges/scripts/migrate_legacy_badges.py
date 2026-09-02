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
    """Reparent one file, and verify Drive actually applied BOTH halves.

    Drive does not guarantee addParents/removeParents apply atomically together
    -- a caller can have permission to add the new parent but not to detach the
    old one. A response that gws returned as a normal 200 could still leave the
    file in both folders; without checking `parents` that half-applied state
    would print as a clean "moved" and later be missed by the --trash-empty
    safety check for the wrong reason (folder "still has files" with no
    explanation why)."""
    resp = sb.gws_json("drive", "files", "update",
                        params={"fileId": file_id, "addParents": new_parent,
                                "removeParents": old_parent, "supportsAllDrives": True,
                                "fields": "id,parents"})
    parents = resp.get("parents") or []
    if new_parent not in parents or old_parent in parents:
        raise RuntimeError(f"reparent for file {file_id} did not fully apply "
                            f"(expected only {new_parent!r}, got parents={parents})")


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
    - orphans: [(name, [file names])] for slugs with no matching chapter AND
      at least one file -- an empty folder matching no chapter has nothing to
      report or act on, so it is silently skipped rather than listed here
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
            if dest_id is None:
                # Re-check right before creating rather than trusting the plan
                # snapshot unconditionally: on a long batch, a concurrent run
                # (another migration invocation, or sync_badges.py --write
                # racing to create this chapter's first badge folder) could
                # have created Badges/ since the plan was made. create_folder
                # is non-idempotent (see sync_badges._gws's retries=1 note),
                # so creating a second one here would go undetected.
                dest_id = sb.find_badges_subfolder(chapters[slug]["folder_id"])
            dest_cache[slug] = dest_id or sb.create_folder(sb.BADGES_SUBFOLDER, chapters[slug]["folder_id"])
            if dest_id is None:
                print(f"created folder {chapter_name}/{sb.BADGES_SUBFOLDER}/ -> {dest_cache[slug]}")
        try:
            move_file(f["id"], dest_cache[slug], legacy_folder_id)
        except RuntimeError as e:
            # Re-run is safe (idempotent: already-moved files are re-detected
            # as "already present" and skipped), so surface exactly which
            # move to investigate rather than a bare stack trace out of gws.
            raise RuntimeError(
                f"failed moving {f['name']!r} for chapter {chapter_name!r} "
                f"(slug={slug!r}) after {moved} successful move(s) this run: {e}") from e
        print(f"  moved    {chapter_name}/{sb.BADGES_SUBFOLDER}/{f['name']}")
        moved += 1
    if moves:
        print(f"\nMoved {moved} file(s).")

    if a.trash_empty:
        # A run of failures sharing the SAME error message points at something
        # systemic (a stale gws credential, a broken API call) rather than
        # per-folder permission differences -- stop instead of silently
        # burning through every remaining folder with a guaranteed failure.
        CONSECUTIVE_FAILURE_LIMIT = 5
        trashed, failed, consecutive_same = 0, [], []
        for slug, folder_id in sorted(all_legacy_folders.items()):
            remaining = sb.list_children(folder_id)
            if remaining:
                print(f"  left in place  {slug}/  (still has {len(remaining)} file(s): "
                      f"{[c['name'] for c in remaining]})")
                continue
            try:
                trash_folder(folder_id)
            except RuntimeError as e:
                err = str(e).splitlines()[0]
                failed.append((slug, err))
                if consecutive_same and consecutive_same[-1] == err:
                    consecutive_same.append(err)
                else:
                    consecutive_same = [err]
                if len(consecutive_same) >= CONSECUTIVE_FAILURE_LIMIT:
                    print(f"\nABORTING trash-empty: {len(consecutive_same)} consecutive "
                          f"folders failed with the identical error -- this looks like a "
                          f"systemic gws/credential problem, not a per-folder permission "
                          f"quirk. Fix the underlying issue and re-run rather than "
                          f"continuing through folders certain to fail the same way.")
                    print(f"Repeated error: {err}")
                    break
                continue
            consecutive_same = []
            print(f"trashed empty legacy folder: {slug}/")
            trashed += 1
        print(f"\nTrashed {trashed} now-empty legacy folder(s).")
        if failed:
            print(f"Could not trash {len(failed)} folder(s) -- files already safely "
                  f"moved regardless, so these are left in place, not lost:")
            for slug, err in failed:
                print(f"  {slug}/: {err}")


if __name__ == "__main__":
    main()
