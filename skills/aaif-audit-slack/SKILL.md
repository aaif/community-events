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

**Neither engine can measure activity.** The Slack CLI token carries no
`channels:history` and no `search:read`, and `conversations.history` returns
`missing_scope` even for public channels. *Last message posted*, *messages per
week* and *is this channel alive* are **unreadable**, not merely unmeasured.
(`lib/aaif_events/slack.py` holds the authoritative scope list, and
`Slack.scopes()` reports the live one — trust those over any list written down
here or there.)

Both engines check their required scopes **before** collecting anything, so a
revoked scope aborts in the first second. That matters more than it sounds:
without the check, a missing `users:read.email` made every organizer lookup fail,
and each failure was recorded as "this person has no Slack account" — rendering
the report's headline, its funnel and its top recommendation as confident
fiction. Failures that mean *the audit is broken* must never be reported as
findings about people.

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

The engine enforces this rather than trusting it:

- **`null` genuinely stops the matcher** in both the `public` and `organizers`
  maps. It records "a human checked and there is no channel", which is an
  answer, so no slug guess follows.
- **An alias that no longer resolves aborts the run.** A renamed or archived
  channel is a configuration bug; left alone it downgrades the chapter to "no
  channel at all" and generates advice to create a room it already has.
- **The report says how each chapter matched.** Aliased chapters and deliberate
  `null`s are listed in *Data quality*, so coverage that rests on the map is
  visible rather than indistinguishable from a name match.

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
and do something else.

Caches are written atomically and stamped, and every reuse prints the age
(`reusing users.json (32543 records, fetched 3 days ago)`), so you can judge
staleness instead of guessing. `--refresh` is rarely needed: the organizer engine
**reconciles** rather than reusing wholesale, so organizers accepted since the
last run are looked up automatically. Reach for `--refresh` when the *channel*
list is stale — someone created, renamed or archived a channel.

Cache files hold member names, email addresses and 2FA/admin flags, so they are
written 0600 inside a 0700 directory, and **both engines refuse to start unless
their cache and output paths are git-ignored.** That check is not tidiness: this
repo is public, and one `git add -A` would publish the workspace directory
irreversibly.

# House rules

- **Never write a channel alias without a human confirming it.** Propose, wait,
  then edit `channel_map.json`. "No channel found" is an acceptable answer; a
  wrong alias silently reports a chapter as covered.
- **Read-only.** `lib/aaif_events/slack.py` refuses any method outside its
  `ALLOWED_METHODS` allowlist, so a typo cannot post, invite or archive. The
  allowlist is the exact set of methods this repo calls, not "everything
  read-only" — adding an entry is a real decision, since `conversations.history`
  is read-only and would falsify the "no message data" caveat both reports print.
  `Slack.scopes()` is the one request that does not go through `call()` (it needs
  the response headers) and is hardcoded to `auth.test`; that is the sanctioned
  exception, not a precedent.
- **Never report a failure as a finding.** Where a number could mean "we measured
  zero" or "we failed to measure", the engines abort or label it — a zero-organizer
  intake, a short user pull, a member whose name would not resolve. The reports
  are persuasive documents aimed at leadership, so a silent failure here becomes
  a confident wrong recommendation rather than a visible error.
- **Never print the token**, and never copy it into a file under the repo.
- **Don't hand-edit the generated HTML.** Change the script or
  `lib/aaif_events/report_style.py` and re-run, so the next run keeps the fix.
- **Report what was measured.** Where a number comes from a proxy, label it as
  one in the summary you write for the user — the pages do this, and the summary
  must match.
