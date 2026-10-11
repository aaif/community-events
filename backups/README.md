# backups/

Local, versioned snapshots written by the **`aaif-backup`** skill. Everything in this
folder **except this README is git-ignored** — the binary `.xlsx`/`.docx`/`.pptx`
snapshots are deliberately kept out of the published plugin repo.

## Layout

```
backups/<slug>/<UTC-timestamp>.<ext>
   e.g.  backups/aaif-community-intake-ops/2026-07-03T142530Z.xlsx
```

Each run of the skill adds a **new immutable file** — nothing is overwritten — so a
folder listing is the full version history, in chronological order.

## Make a backup

```bash
# default: the AAIF Community Intake Ops sheet
python3 <toolkit>/skills/aaif-backup/scripts/backup.py

# any Drive file, or a local file
python3 <toolkit>/skills/aaif-backup/scripts/backup.py <driveFileId>
python3 <toolkit>/skills/aaif-backup/scripts/backup.py ./path/to/file.xlsx
```

## Restore

Manual and deliberate — see the "Restore" section of `skills/aaif-backup/SKILL.md`.
For the Intake Ops sheet, copy values back from the snapshot rather than replacing the
file, so the role-tab formulas and conditional formats survive.
