---
name: aaif-audit-slack
description: Audit the community Slack workspace, from either side. The organizer engine checks, for every chapter on the Chapters List, whether it has a public city channel and a private organizers channel, whether the organizers we accepted are actually in them, and who is in each organizers channel that we never accepted. The member engine reports channel counts, sizes, descriptions and lifecycle, plus how many accounts behind the headline member count are active, deactivated, guests or bots. Each produces a self-contained HTML report and a PDF. Use when asked about Slack coverage for chapters, whether organizers are in their channels, who is in the -organizers channels, workspace health, channel clutter, inactive channels or accounts, or the newcomer experience.
argument-hint: '[organizers|members|both] [--refresh] [--out NAME] [--no-pdf]'
---

# AAIF Slack Audit

Two engines over one workspace, same house rules — **everything is read-only**,
and neither engine can measure message activity (see the ceiling below).

| Engine | Script | Answers |
|---|---|---|
| Organizers | `audit_organizers.py` | does each chapter have a home, and are the people we accepted in it? |
| Members | `audit_members.py` | what does the workspace look like to an ordinary member? |

Run whichever the user asked for. "Audit Slack" with no side named means **both**
— run the organizer engine first; it is the one with decisions attached.

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

Applied here: the organizer engine reads two Drive sheets through `gws` and
**only ever reads** them — this skill writes nothing to Drive. The reports are
HTML, not Office files, and render to PDF through **headless Chrome**;
`lib/aaif_events/report_style.to_pdf` does that correctly, so call it rather than
shelling out to a converter yourself.

Prereqs: the Slack CLI authenticated (`slack auth login`); for the organizer
engine, `gws` installed and authenticated (see the user's `gws-cli-access`
memory). The Slack token is read from `~/.slack/credentials.json` and never
printed.

---

# The ceiling — state this in any summary you write

**Neither engine can measure activity.** The Slack CLI token carries
`channels:read, groups:read, users:read, users:read.email, team:read`. There is
no `channels:history` and no `search:read`, and `conversations.history` returns
`missing_scope` even for public channels. *Last message posted*, *messages per
week* and *is this channel alive* are **unreadable**, not merely unmeasured.

Never substitute a proxy:

- **A channel's `updated` field is not activity.** A bulk migration reset it in
  blocks — 54 channels share one identical value, 8 share another. Useless as a
  staleness signal; the reports don't use it. `topic.last_set` /
  `purpose.last_set` are genuine human edits and are used instead.
- **A member's `updated` field is not engagement.** It moves on any profile or
  settings change, so a long-departed member and a content lurker look identical.
  The report frames it as a *floor on staleness* and says so on the page.

**For real activity data**, point the user at the admin **Analytics → Channels /
Members** CSV export — per-channel last-activity and messages-posted, per-member
last-active. A workspace admin downloads it from the UI; no scope change, no API.
It is the first recommendation in the member report for that reason.

**Private channels are undercounted.** `conversations.list` returns only the
private channels the *token owner* belongs to. `users.conversations(user=…)`
looks like a way around this and is not — its results are filtered to the
caller's own visibility (verified: probing 101 staff, organizers and admins
returned exactly the caller's own 23 private channels, nothing more). A
workspace-wide list needs **Enterprise Grid** (`admin.conversations.search`,
org-level token) or the admin UI's *Show all private channels*, which is
Business+/Enterprise and held by the Workspace Primary Owner.

---

# 1. Organizer engine

Joins the **Chapters List**, the **Intake Ops** organizer decisions and **Slack**.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/audit_organizers.py
```

Writes `slack-organizers-audit.html` + `.pdf`, and a machine-readable
`audit.json` in the cache directory.

| Source | Read | Used for |
|---|---|---|
| Chapters List `Chapters & Teams` | `City`, `Organizers` | the chapter roster |
| Intake Ops `Organizers` | `Status`, `Full name`, `Email`, `Chapter`, `City (New)`, `City (Existing)` | who was accepted, for which city |
| Slack | `conversations.list`, `conversations.members`, `users.lookupByEmail`, `users.info` | channels and membership |

- **Status filter is exact-string**: `Accepted` and `Existing (from MLOps)` only.
  Matching a prefix like `Existing` once missed all 23 MLOps rows.
- **City precedence matches `aaif-sync-chapters`**: the human's `Chapter`
  assignment wins, then `City (New)`, then `City (Existing)` unless it is an
  `Other…` placeholder.
- **Duplicate intake rows** for the same person and city are dropped, first wins,
  count reported.
- **Organizers are matched to Slack by email** — the weak link, see below.

## `channel_map.json` is the part you maintain

Channel naming is not consistent, so matching is config-driven. Edit
`scripts/channel_map.json`, never the script:

- **`public`** — a city whose own channel breaks the plain-slug convention
  (`Denver → colorado`, `Munich → munchen`, `New York → nyc`,
  `Washington DC → washington-dc-the-capital`). Map a city to `null` to record
  that no channel exists and stop the matcher guessing.
- **`regional`** — a channel that *serves* a city without being its own
  (`Chennai → india`, `Lagos → africa`). Reported as **regional only**, never
  counted as chapter coverage, because a member there has no local room.
- **`organizers`** — organizer channels not named `<city>-organizers`.

**Never auto-map an alias.** Every entry in the map is a human judgement that a
named channel really is that chapter's channel. When the report flags a
near-miss, or you spot a likely match yourself, **propose it and wait** — show
the user the chapter, the candidate channel, its member count and why you think
they correspond, and let them confirm before anything is written. Do not add the
entry on your own authority, and do not widen the matcher to catch it.

The audit is allowed to answer "no channel found"; that is correct and someone
will notice. A wrong alias is neither — it reports a chapter as covered when it
has no room, and nothing downstream ever re-checks it. Prefer the flagged unknown
every time.

`_provenance` at the top of the map records that the current entries were
inferred by an agent on the first run and never confirmed by anyone who runs
these chapters. Until a human signs them off, say so when you report coverage
numbers. Delete that block once they are checked.

## Reading the results

- **Email is the only identity join.** "No Slack account" means *no account under
  the address we hold*; the person may well be in Slack under another. Treat it
  as an upper bound on the gap and reconcile by name before acting on anyone
  individually.
- **A public `-organizers` channel is the highest-severity finding here.** The
  report tags it `public!` — organizer coordination (venue costs, budgets,
  speaker problems) readable by the whole workspace. Surface it first.
- **Chapters with an organizers channel but zero accepted organizers appear in
  the rosters section**, and should — their whole roster is people nobody
  reviewed.
- **Fix data in the source, not the report.** An unresolved city belongs in the
  intake row (`aaif-clean-data`); a missing chapter row belongs in the Chapters
  List (`aaif-sync-chapters`). Re-run afterwards.

---

# 2. Member engine

Structural audit of channels and accounts. Touches no Drive file.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/audit_members.py
```

Writes `slack-members-audit.html` + `.pdf`.

Sections: channel counts and size distribution · how long since anyone set a
topic or purpose · membership concentration in the auto-join channels vs
elective joins · channels created per year and the small-channel list · account
state (active / deactivated / bot / guest / admin) and profile completeness ·
email-domain breakdown · the limits, restated on the page.

## Reading the results

- **Empty channels are rarely the problem.** On a healthy community workspace
  almost nothing is abandoned by membership; the dead weight is *metadata*. The
  "never had a topic or purpose" count is the number worth acting on.
- **Auto-join channels distort every membership figure.** Always quote the
  elective number alongside the total, or the workspace looks far more engaged
  than it is.
- **Zero guest accounts is a governance fact, not a bug** — but it means every
  member can be added to any private channel, which is why the organizer
  channels need care. The two engines meet on this point.

---

# Shared flags

| Flag | Effect |
|---|---|
| `--refresh` | Re-fetch from the API instead of reusing the cache |
| `--out NAME` | Output basename |
| `--no-pdf` | HTML only — skip the Chrome render |
| `--cache DIR` | Where raw pulls are stored (default `.slack-audit-cache`) |
| `--map FILE` | Organizer engine only — alternate `channel_map.json` |

**Both engines cache their raw pulls**, and the first run of each is slow:
`users.list` pages 200 at a time (~20 min on a 30k-member workspace) and
`users.lookupByEmail` is ~1.5s per organizer. Run the first one in the background
and do something else. Don't pass `--refresh` unless the data is genuinely stale.

# House rules

- **Never write a channel alias without a human confirming it.** Propose, wait,
  then edit `channel_map.json`. "No channel found" is an acceptable answer; a
  wrong alias silently reports a chapter as covered.
- **Read-only by construction.** `lib/aaif_events/slack.py` refuses any method
  outside its `ALLOWED_METHODS` allowlist, so a typo cannot post, invite or
  archive. Keep it that way — if a future need is genuinely read-only, add the
  method to the allowlist rather than bypassing the client.
- **Never print the token**, and never copy it into a file under the repo.
- **Don't hand-edit the generated HTML.** Change the script or
  `lib/aaif_events/report_style.py` and re-run, so the next run keeps the fix.
- **Report what was measured.** Where a number comes from a proxy, label it as
  one in the summary you write for the user — the pages do this, and the summary
  must match.
