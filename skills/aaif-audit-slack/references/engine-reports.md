# Per-engine detail — sources, sections, and how to read each report

> Load the section for whichever engine you are running or explaining. Needed to
> interpret a report, not to produce one.

## 1. Organizer engine (`audit_organizers.py`)


Writes `slack-organizers-audit.html`, and a machine-readable `audit.json` in
the cache directory.

`--planned-ok` exists for the **pre-provisioning** state: the Chapters List
names the channel each chapter *will* have before `provision_channels.py` has
created it, and without the flag every such name aborts the run as a
rename/archive bug. With it, they are downgraded to "planned" in the
data-quality notes and the chapter truthfully reports as having no channel.
The flag also covers the sibling pre-convert state: a `Slack Channel` cell
naming a room that exists but is still **private** (a squat awaiting an
admin-UI convert) is downgraded to "held private" instead of aborting, and the
chapter reports as having no public channel yet.
Drop the flag once provisioning has run, so the abort protects the map again.

| Source | Read | Used for |
|---|---|---|
| Chapters List `Chapters & Teams` | `City`, `Slack Channel`, `Organizer Channel`, `Country Channel` | the chapter roster **and the channel map** |
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

> **The channel map lives on the Chapters List**, not in this repo: five
> columns per row name that chapter's Drive folder and its public, organizer
> and country channels. `aaif-sync-slack`' resource engine fills them and
> this engine only reads them, so the two can never disagree about which room
> is whose. The columns, the `none` sentinel and the never-auto-map rule are in
> `references/chapters-list-schema.md`.

### Reading the results

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


## 2. Topics engine (`audit_topics.py`)

The subject-matter rooms — `#kubernetes`, `#coding-agents`, `#llmops` — as
opposed to the chapter rooms (engine 1) and the plumbing (`#general`, `#random`,
`#job-posts`). Touches no Drive file except to **read** the classification.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/audit_topics.py
```

Writes `slack-topics-audit.html`.

Sections: where the subjects sit — **one table per kind, topics first, then
software**, each room carrying its theme, members, last human message, poster
count and three yes/no flags (active · 5+ posters · purpose set), with rooms
dead a year or more tinted · quiet and dead topics by last **human** message ·
proposed overlapping rooms · rooms carried by one or two posters · rooms with no
purpose set · unclassified rooms · the limits.

**A flag is tri-state and "?" is not "no".** `is_alive` reads `state_of` and
never re-derives dormancy from the window counts, even where they look
sufficient: a room whose scan never reached a human message has an unknown
*date* while its *window* may be fully measured, and answering from the window
put 77 rooms in the column against the 64 the same page calls quiet — one
document, two numbers for one thing. (`dormancy()` records the sibling rule: the
scan cap is hit by *busy* rooms, so UNKNOWN must never read as silence.)
`has_contributors` is the mirror image — a truncated scan reports a floor, so it
can prove "5 or more" but never "fewer than 5", and returns `None` there.

> **The classification lives on the Chapters List too**, on a `Topics` tab —
> which rooms are subject-matter rooms, which are software projects, and which
> are something else. See `references/chapters-list-schema.md`. It was seeded
> by an agent and is unconfirmed, so a headline resting on it says so.

### Reading the results

- **Quiet is not the same as archive-me.** A 3,000-member room silent for a year
  is a decision to make — revive it with a prompt, or retire it deliberately —
  not an automatic cleanup. The report ranks by members precisely so the cost of
  getting it wrong is visible.
- **Overlaps are proposals, never actions.** Merging two rooms destroys history
  and splits a membership. The page names the pair and why; a human decides.
- **Membership is not readership.** The concentration section is the honest
  number: a room with 6,000 members and two posters is two people talking.


## 3. Member engine (`audit_members.py`)

Structural audit of channels and accounts. Touches no Drive file.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/audit_members.py
```

Writes `slack-members-audit.html`.

Sections: channel counts and size distribution · how long since anyone set a
topic or purpose · membership concentration in the auto-join channels vs
elective joins · channels created per year and the small-channel list · account
state (active / deactivated / bot / guest / admin) and profile completeness ·
email-domain breakdown · the limits, restated on the page.

### Reading the results

- **Empty channels are rarely the problem.** On a healthy community workspace
  almost nothing is abandoned by membership; the dead weight is *metadata*. The
  "never had a topic or purpose" count is the number worth acting on.
- **Auto-join channels distort every membership figure.** Always quote the
  elective number alongside the total, or the workspace looks far more engaged
  than it is.
- **Zero guest accounts is a governance fact, not a bug** — but it means every
  member can be added to any private channel, which is why the organizer
  channels need care. The two engines meet on this point.


## 4. Activity engine (`audit_activity.py`)

Per live channel: last *human* message (plumbing subtypes and bots excluded),
message volume, distinct posters and join noise over a trailing window
(`--days`, default 90). **No message text is ever retained** — timestamps,
counts and poster ids only, in the same 0600 cache as everything else.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/audit_activity.py
```

Writes `slack-activity-audit.html`. Reuses the shared channel cache;
its per-channel pulls are resumable within a UTC day, so an interrupted sweep
continues instead of restarting. When `audit.json` from the organizer engine is
present, the report includes a per-chapter activity table joined from it.

### Reading the results

- **Every count is a floor.** Thread replies are invisible except broadcasts; a
  channel that lives in threads under-counts. The page states this.
- **A truncated scan is marked**, not ranked as fully measured.
- **The member report picks up this engine's cache** and turns the union of
  poster ids into "posted in the last N days" — writers only, so it is an
  engagement floor, never a lurker count.
