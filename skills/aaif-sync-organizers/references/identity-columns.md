# Identity columns — `resolve_slack_ids.py`, `track_drive_email.py`

> Load this when an organizer reports "no Slack account", when a grant lands on the
> wrong address, when `Drive Email` shows `(no grant)`, or before editing either script.

One person reaches this estate under up to three different addresses: the one
they typed on the form, the one Drive actually granted, and the one their Slack
account carries. Treating those as one address is what produced the standing
"organizer has no Slack" and "organizer requests access to a folder they already
have" confusions. Three columns on `Form Responses` record them side by side —
and **none of them ever overwrites the intake `Email`**, which is what the person
told us and what Drive grants are keyed to.

| Column | Written by | Is |
|---|---|---|
| `Slack ID` | `resolve_slack_ids.py` | the immutable `U…` account id — the durable key |
| `Slack Email` | `resolve_slack_ids.py` | the address that Slack account carries |
| `Drive Email` | `track_drive_email.py`, **and a human** | the address on their chapter folder's ACL — and the address `sync_access.py` grants |

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_slack_ids.py            # report
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_slack_ids.py --write    # fill by email lookup
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_slack_ids.py --suggest  # + name candidates
python3 ${CLAUDE_SKILL_DIR}/scripts/resolve_slack_ids.py --apply ids.json --write
python3 ${CLAUDE_SKILL_DIR}/scripts/track_drive_email.py --write    # after step 6
```

**An id, never a handle.** A handle is a display name its owner can change, so an
invite keyed off one breaks silently — the same argument `invite_organizers.py`
makes, and the reason `Organizer Handles` on the Chapters List is a mirror rather
than a source.

**`--write` fills only what an email lookup resolved.** A name match is a
*suggestion*, reported for review and written only through `--apply --write`, because
two people genuinely share a name. That guard has already earned itself: a
`Denied` Lagos applicant shares a full name with an AAIF ops staffer, and the
automatic pass would have stamped the ops account's id onto her row.

**The columns are read back by the engines that need them.** `sync_resources.py`
(Organizer Handles), `invite_organizers.py` and the organizer audit all consult
`Slack ID` for people an email lookup cannot reach, which is what turns a
reconciled identity into an actual invite. A **live lookup always outranks the
column** — a hit is a hard fact about an address, while a cell can predate
someone changing their Slack email — and a disagreement is printed as a
`CONFLICT`, never silently resolved. The `@handle` is deliberately NOT stored:
it is fetched via `users.info` at display time, because a cached handle rots
into a wrong `@mention`.

**A `(no grant)` in `Drive Email` on an accepted organizer is the finding** — no
permission on their chapter folder matches any spelling of their address, so
they cannot open it and an access request is coming. Run `track_drive_email.py`
after step 6, when the grants are current.

**`Drive Email` is the one identity column that is also an input.** `Slack ID`
redirects who gets *invited*; `Drive Email` redirects who gets *granted*.
`sync_access.py` grants the address recorded there in preference to the intake
`Email`, which is the supported fix for the case that otherwise has none: an
address with **no Google account** behind it, which Drive refuses to share with
outright, so the only alternative is an unsolicited invitation mail. Write the
person's Google address into the cell and re-run `sync_access.py` — the grant
lands silently and the intake `Email` stays untouched.

Three rules make a hand-editable cell safe to grant from:

- **The authority is still the intake row.** The column redirects *where* a
  grant lands; the acceptance and the chapter come from `Email`, so the cell
  cannot create a grant, only move one. It also cannot move one to **someone the
  intake refused**: a target matching a `Denied`/`New`/`Tentative` row aborts the
  whole run. A target on *no* intake row is allowed — that is the feature, a
  personal Google account the form never saw — which is why every redirect is
  printed in the report for a human to read.
- **A recorded address is an instruction, not a tiebreak.** Someone already
  granted under an older address is granted the recorded one, and the older
  grant is named as superseded. Preferring the existing grant meant the common
  case — a person who changed Google accounts — had their recorded address
  ignored by this engine and overwritten by `track_drive_email`.
- **Only an override counts as one.** A blank cell, the `(no grant)` sentinel,
  and a value that merely restates the intake address are ignored silently; free
  text, two addresses in one cell (including the newline Alt+Enter produces), a
  display-form `Name <a@x.io>`, a non-ASCII homoglyph domain and a
  group-hosting address are ignored with a line naming the row. The report
  prints those once, next to the grants they affected.
- **Two rows disagreeing about one person drop the override entirely**, loudly.
  A person can hold several intake rows, so a disagreement is a real question
  about which address is theirs, and guessing is where this estate's identity
  bugs come from — the shared-full-name collision the `Slack ID`
  rules above describe, and the Gmail-dot lookup bug.

`track_drive_email.py` therefore no longer owns every cell: an address a human
recorded that Drive has not granted **yet** is reported and left alone, not
overwritten with `(no grant)`. Everything already on the ACL is still written
from the ACL, including a grant made under the recorded address.
