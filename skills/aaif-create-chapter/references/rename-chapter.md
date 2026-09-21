# Renaming an existing chapter — `rename_chapter.py`

> Load this when asked to rename a chapter, or when a feed row's `Chapter Folder` link
> resolves to a folder with a different name (a half-finished rename).

Creating a chapter rebrands San Francisco -> `<City>` while cloning. Renaming one
that already exists is the same transform between two arbitrary cities, applied
in place — and it is a **four-surface** job, which is what the capital-city
migration (2026-08) missed:

| Surface | Example |
|---|---|
| the chapter folder | `Scotland` -> `Edinburgh` |
| file / subfolder names | `Scotland CRM.xlsx`, `Icons/Scotland Agent.gif` |
| OOXML text in `.docx`/`.pptx`/`.xlsx` | `AAIF Scotland` / `SCOTLAND · CHAPTER` |
| document metadata (`docProps`) | the chapter label |

That migration renamed only the **Chapters List rows**, so the feed said
`Edinburgh` while every file an organizer opened said `Scotland`, and the folder
name no longer matched the city — which is what `aaif-sync-chapters` matches
chapters to folders on. The shape to look for: a feed row whose `Chapter Folder`
link resolves to a folder with a different name.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/rename_chapter.py --from Scotland --to Edinburgh
python3 ${CLAUDE_SKILL_DIR}/scripts/rename_chapter.py --from Scotland --to Edinburgh --write
# a folder already renamed, contents not (a half-finished rename):
python3 ${CLAUDE_SKILL_DIR}/scripts/rename_chapter.py --folder Madison \
    --from "Madison, WI" --to Madison --slug-from madisonwi --slug-to madison --write
```

**The Luma slug is NOT renamed unless you ask** (`--slug-from` / `--slug-to`). A
chapter's name and its Luma page are two identities, and the page does not move
just because the chapter was renamed — a renamed chapter routinely keeps serving
from its original slug, so rewriting those links would replace a working URL
with a 404. Check the live page, then pass the pair only once it has actually
moved on luma.com.

For the same reason the name pass is kept **out of** `aaif-<slug>` tokens
entirely: `SCOTLAND` is a substring of `AAIF-SCOTLAND`, so a naive replace breaks
a live link as a side effect of a rename that was told not to touch it. A test
pins this.

Also deliberate:

- **`.rels` receives the slug substitution ONLY**, never the city-name pass —
  the same narrowing `create_chapter` applies. A relationship part holds ids and
  targets: rewriting a `Target="../embeddings/Utah_Data.xlsx"` while the zip
  member keeps its name dangles the relationship and the document opens as
  corrupt, and the repack validates CRCs, not relationships.
- **Every string that would change is printed before the write**, including from
  the CRM. A chapter's CRM is member data, and a row whose own text happens to
  name the old city would be rewritten too — a human decides that, not the script.
- **Originals are copied to `backups/rename-<UTC>/`** before anything uploads.
- **The selection test and the verification test are the same function**, and it
  asks `rename_part` itself whether a part would change. A predicate that only
  looked at visible text missed `docProps` and `.rels` — files were never
  queued, and the verify, sharing the blindness, printed "Verified" over them.
- **The run re-reads every file from Drive afterwards** and exits non-zero if any
  file name, or any text in a `.docx`/`.pptx`/`.xlsx`, still carries the old
  name — or if any part could not be decoded, since a part the verifier could
  not read is one it cannot vouch for.
- **A rename whose names contain one another is refused** (`York` -> `New York`):
  the transform would not be idempotent, so the verify would fail on a correct
  run and a re-run would double-apply it.
- **A mid-run upload failure says exactly what landed** — which files were
  uploaded, that no names were changed, and where the originals are.
- **The Chapters List row, the intake's city cells and Slack channels are NOT
  touched.** The first two are `aaif-sync-chapters`' surfaces and must be updated
  for the engines to keep matching the chapter to its folder; a channel rename
  needs its own per-channel consent.
