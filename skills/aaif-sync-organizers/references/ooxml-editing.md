# Editing the docs and workbooks as OOXML

> Load this before changing how `sync_about.py` or `sync_crm.py` writes a file, or when
> a written About doc or CRM comes back malformed. Not needed to run either engine.

## About docs (`.docx`) — `sync_about.py`

The About docs are **stored `.docx`**, not native Google Docs, so they are edited
as OOXML — and specifically by **byte-level surgery on `word/document.xml`**, not
through ElementTree. Re-serializing the part reorders every namespace declaration
on `<w:document>` and rewrites markup the engine never meant to touch; splicing
one paragraph range leaves the rest of the part, and every other zip member
(the embedded brand fonts among them, stored uncompressed), byte-identical.

- Paragraphs are found by **depth-counted** `<w:p>` scanning. A flat
  `<w:p>.*?</w:p>` match ends at the first inner `</w:p>` of a textbox paragraph
  and splices the document in half.
- The block runs from the heading to the **first non-list paragraph** — the
  `Luma & Socials` heading in every chapter doc.
- New bullets are cloned from an existing one so they inherit its `<w:pPr>`, and
  `w14:paraId` is stripped from the clone (it is meant to be unique per
  paragraph). When the block has no bullet left to clone, `MODEL_BULLET` rebuilds
  it — and it carries **`numId 1`**, the Organizers list. The `Luma & Socials`
  list below it is `numId 4`, so cloning the nearest bullet renumbers the list.


## Chapter CRMs (`.xlsx`) — `sync_crm.py`

The CRMs are **stored `.xlsx`**, not native Sheets, so they are edited as OOXML
zip parts: download → rewrite `Attendees`' sheet XML → upload. Every part the
script doesn't touch is repacked byte-for-byte, and values are written as
**inline strings**, which Excel and Sheets both treat as literal text — a name
starting with `=` can never become a formula, so there is no RAW-vs-`USER_ENTERED`
hazard here.

**Write order used to be load-bearing, and is not any more.**
`Attendees.serialize()` rewrites the sheet part wholesale from the element tree,
so a bytes-level edit applied *before* it is silently discarded — and the run
still reports it as applied, which is what once shipped to a probe workbook.
`sync_crm` no longer makes any bytes-level edit: schema belongs to
`migrate_interested_in.py`, and `finalize()` only writes rows and serializes.
The hazard still applies to **the migrations**, which do both — each does every
structural edit on the element tree and only then touches the Guide part, which
`serialize()` does not rewrite.
