---
name: aaif-backup
description: Take a versioned local backup of critical AAIF ops data — the Community Intake Ops sheet by default, or any Drive file / local file you name. Snapshots are immutable and timestamped so you keep a full history. Use when asked to back up / snapshot the intake data (or another file) before a risky edit.
argument-hint: "[driveFileId | ./local/path]"
---

# Backup AAIF Ops Data

> **Tooling rule — `gws` + Python only.** Every read, edit, and write of a Drive
> file goes through the `gws` CLI, driven from Python. **Prefer native Google
> formats**: edit `application/vnd.google-apps.*` files with the Docs/Sheets/
> Slides API. Drop to byte-level OOXML surgery on the `.docx`/`.pptx`/`.xlsx`
> zip parts (embedded fonts and untouched parts survive) only when the file
> genuinely is a stored Office file. **Never use LibreOffice / `soffice`** — not to edit, not to convert,
> and not to render a "just checking it locally" preview: it substitutes local
> system fonts for the brand fonts and drops OOXML it doesn't understand, so its
> output and its renders both misrepresent the real file. Same for `unoconv` and
> any desktop office suite. To *see* a file, render it through the API instead —
> a slide via `aaif_events.slides_export.render_slide_png`, a doc via
> `gws drive files copy` to a Google Doc → `gws drive files export` to PDF →
> trash the copy. Never round-trip a native Doc through `.docx` — it strips
> native features like Tabs.

Snapshot irreplaceable data to **local, versioned files** before risky edits (a bulk
clean, a schema change, a column move). Every run writes a **new immutable file** —
nothing is ever overwritten — so the backup folder is a full version history you can
diff or restore from.

Prereq: the `gws` CLI must be installed and authenticated (`gws-cli-access` memory) for
Drive targets. Local-file backups need no auth.

## Usage (engine: `scripts/backup.py`)

```bash
# default — back up the AAIF Community Intake Ops sheet (exported to .xlsx)
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py

# back up any Drive file by id (native Docs/Sheets/Slides -> .docx/.xlsx/.pptx)
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py <driveFileId>

# back up a local file (copied verbatim)
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py ./path/to/file.xlsx

# write snapshots somewhere other than ./backups
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py --dest /some/dir
```

## Where snapshots land

```
<dest>/<slug>/<UTC-timestamp>.<ext>
   e.g.  backups/aaif-community-intake-ops/2026-07-03T142530Z.xlsx
```

- `<dest>` defaults to **`./backups`** (relative to where you run it — inside the
  `meetups` repo when run from there). `backups/` is **git-ignored**, so the binary
  snapshots never enter the repo — and the script verifies that at run time: it
  **refuses to start** if the destination is committable (not git-ignored, or
  already holding tracked files) inside any repo. A `--dest` outside every repo
  is always fine.
- Filenames are UTC timestamps, so a folder listing is the version history in order.
- The target's type decides the format: a Google Sheet is exported to `.xlsx`, a Doc to
  `.docx`, Slides to `.pptx`; already-binary Drive files and local files are copied as-is.

## Restore (manual)

There is no automated restore — it is a deliberate, eyes-open step.

- [ ] **1.** Pick the snapshot from `backups/<slug>/`. Filenames are UTC
      timestamps, so the listing is the version history in order.
- [ ] **2.** Re-upload it over the live file:
      ```bash
      gws drive files update --params '{"fileId":"<ID>"}' \
        --upload <file> --upload-content-type <mime>
      ```
      **For the Intake Ops sheet, prefer copying values back** over replacing the
      file, so the role-tab formulas and conditional formats stay intact.
- [ ] **3.** Verify the restored file opens and its formulas still compute,
      before telling the user it is done.

## Gotchas

- **Run this BEFORE `aaif-clean-data apply` or any bulk sheet edit.** That is
  the whole point of the skill.
- **The script refuses to start if the destination is committable** — not
  git-ignored, or already holding tracked files, inside any repo. A `--dest`
  outside every repo is always fine. This is a guard, not a nuisance: the
  snapshots are binary copies of real applicant data.
- **Replacing the Intake Ops sheet wholesale destroys its formulas.** The role
  tabs are `ARRAYFORMULA` columns; an `.xlsx` re-upload lands literals over them.
- Default target is the Intake Ops sheet (id `1cWkjCI5AGK9RX_fs23P5jRA4I2nixgnHuapvwHseZ5o`),
  the one source of applicant data that cannot be regenerated.
- **Snapshots accumulate and are never pruned.** Delete old ones by hand once
  they stop being useful — every one is a full copy of real people's data.

## Verify

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/test_backup.py
```
