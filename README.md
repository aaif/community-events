# AAIF Community Events Toolkit

Agent Skills for running **AAIF (Agentic AI Foundation)** in‑person and online
events — from writing the LinkedIn announcement to spinning up a brand‑new city
chapter or online event series.

Packaged as a **Claude Code plugin** (and a one‑plugin marketplace) so any
organizer can install the whole toolkit in two commands. The skills are plain
[Agent Skills](https://code.claude.com/docs/en/skills) (`SKILL.md` files), so they
also work in claude.ai and the Claude Agent SDK — see [Using in other tools](#using-in-other-tools).

---

## Install (Claude Code)

```bash
/plugin marketplace add aaif/community-events
/plugin install aaif-events@aaif
```

`marketplace add aaif/community-events` reads `.claude-plugin/marketplace.json` from this
repo; `@aaif` is the marketplace name. After installing, the skills auto‑activate
when you describe a matching task (e.g. “draft the announcement post for our July
event”), or invoke one explicitly with `/aaif-<skill>` (e.g.
`/aaif-announcement-post`).

### Quickstart (step‑by‑step)

Prefer the guided UI flow? Run these inside Claude Code:

1. **Add the marketplace** (the full git URL is equivalent to the `aaif/community-events`
   shorthand used in [Install](#install-claude-code) above):
   ```bash
   /plugin marketplace add https://github.com/aaif/community-events.git#main
   ```
2. **Enable it:** run `/plugin`, tab to **Marketplaces**, and enable the **aaif**
   marketplace.
3. **Turn on auto‑update** for the marketplace so you always get the latest skills.
4. **Install the plugin:** in that marketplace, browse plugins and install
   **aaif‑events**.
5. **Reload:**
   ```bash
   /reload-plugins
   ```
6. **Start using the skills:** type `/aaif-` to autocomplete the toolkit's
   commands (e.g. `/aaif-announcement-post`), or just describe your task and the
   matching skill auto‑activates.

   ![Typing /aaif- surfaces the toolkit's commands autocompleting in Claude Code](assets/aaif-skills-autocomplete.png)

---

## What's inside

### ✍️ Content skills — no setup required
Pure writing skills. They take the event details you give them and produce copy.

| Skill | What it writes |
|---|---|
| `aaif-announcement-post` | LinkedIn launch post for when RSVPs open |
| `aaif-carousel-copy` | 6‑slide LinkedIn carousel announcing an event |
| `aaif-luma-description` | Luma event‑page description |
| `aaif-speaker-invite` | Warm speaker‑invite DM / email |
| `aaif-speaker-bio` | 60–80 word speaker bio + one‑liner |
| `aaif-dayof-slides` | Slide text for the “Day of Event” deck |
| `aaif-attendee-reminder` | Pre‑event reminder to people who RSVP'd |
| `aaif-recap-post` | Post‑event LinkedIn recap (within 48h) |

> **Attendee legal defaults.** The attendee‑facing skills (`aaif-announcement-post`,
> `aaif-luma-description`, `aaif-attendee-reminder`) append two standing links by
> default — the [Code of Conduct](https://events.linuxfoundation.org/about/code-of-conduct/)
> and [Privacy Policy](https://www.linuxfoundation.org/legal/privacy-policy).
> Running your own chapter? Swap these URLs in each skill's `SKILL.md`.

### 🛠️ Ops skills — need Google Workspace access
These drive Google Drive / Sheets through the `gws` CLI (see below); a few also
talk to Slack or Luma.

| Skill | What it does | Touches |
|---|---|---|
| `aaif-create-chapter` | Clone the **TemplateCity** folder and rebrand every asset for a new city | Google Drive |
| `aaif-create-online-series` | Clone the **TemplateSeries** folder under **Online/** and rebrand it for a new online series | Google Drive |
| `aaif-triage-intake` | Summarize who's awaiting review in the Community Intake sheet + draft outreach | Google Sheets |
| `aaif-clean-data` | Normalize/flag data quality in the Intake sheet (LinkedIn, casing, City=Other…) | Google Sheets |
| `aaif-backup` | Versioned local snapshots of critical data (Intake sheet by default, or any file) before risky edits | Google Drive |
| `aaif-create-event` | Add an event to a chapter/series Event Tracker (due-dates stamped from the event date), optionally creating the live Luma page on approval | Google Drive, Luma |
| `aaif-update-event` | Edit an event's details or move its date (recomputing all task due-dates), flag stale assets, optionally sync the change to Luma | Google Drive, Luma |
| `aaif-event-status` | Report overdue / due-soon event tasks by owner, plus read-only Luma registration stats | Google Drive, Luma |
| `aaif-sync-chapters` | Push intake decisions to the Chapters List, About docs, chapter CRMs, per-chapter Drive access and the resource map (report/propose by default) | Google Sheets/Drive/Docs, Slack |
| `aaif-audit-slack` | Audit the community Slack workspace — chapter/organizer channel coverage and member/channel health — as self-contained HTML + PDF reports | Slack, Google Sheets |

> **Heads up — these ship with AAIF's own IDs.** The ops skills reference AAIF's
> Google resources (the Chapters Drive, the Intake Ops spreadsheet ID, Luma slug
> conventions). To run your own chapter, fork and edit the constants at the top of
> each skill's `scripts/*.py` and the IDs in its `SKILL.md`.

---

## Google Workspace access (for the ops skills)

The ops skills read and write Google Drive/Sheets through **one** route: the
**`gws` CLI**, driven from **Python**. There is no connector or MCP alternative —
every skill, script, and manual step goes through `gws`, so behaviour is the same
whether a human or an agent runs it.

> **`gws` is not an official Google tool.** It's a third‑party command‑line
> client for the Google Workspace APIs, not published or supported by Google, and
> not affiliated with AAIF or the Linux Foundation. You are granting it OAuth
> scopes over your own Workspace data — vet the source and pin a version you trust
> before pointing it at anything that matters.

> **Work in native Google formats wherever possible.** Prefer the Docs, Sheets,
> and Slides APIs against native `application/vnd.google-apps.*` files. Only fall
> back to byte-level OOXML surgery in Python (`.docx`/`.pptx`/`.xlsx` zip parts)
> when the file genuinely *is* a stored Office file — a `.docx` round-trip of a
> native Doc silently strips native features like Tabs. And never use LibreOffice
> /`soffice`, `unoconv`, or any desktop office suite, not even to render a local
> preview: it substitutes local system fonts for the brand fonts and drops OOXML it
> doesn't understand, so its output and its renders both misrepresent the real file.
> To see a file, render it through the API
> (`aaif_events.slides_export.render_slide_png` for a slide;
> `gws drive files copy` → `export` to PDF → trash the copy for a doc).

Install `gws` (a Google Workspace command‑line tool), then authenticate with one of:

- **Interactive OAuth:** `gws auth login`
- **Credentials file:** set `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=/path/to/oauth_credentials.json`
- **Pre‑obtained token:** set `GOOGLE_WORKSPACE_CLI_TOKEN=<access_token>`
- **Client app:** set `GOOGLE_WORKSPACE_CLI_CLIENT_ID` and `GOOGLE_WORKSPACE_CLI_CLIENT_SECRET`, then `gws auth login`

On your Google Cloud project, enable the **Sheets**, **Docs**, **Slides**,
**Drive**, and **Forms** APIs.

**OAuth scopes.** Grant **read-write** scopes for anything you write to —
read-only access passes the verify step below but then fails on the first write
(form responses are read-only by nature, hence the `.readonly` scope). The
bundled scripts use the Sheets, Drive, and Slides scopes (the shared
`aaif_events.slides_export` helper renders deck previews through the Slides
API); the Docs and Forms scopes are for operating on those assets directly —
the chapter/series source docs and the intake form:

- `https://www.googleapis.com/auth/spreadsheets` — read/write the intake sheet *(scripts)*
- `https://www.googleapis.com/auth/drive` — copy, create, and update Drive files,
  incl. chapter/series asset clones *(scripts)*
- `https://www.googleapis.com/auth/documents` — read/edit chapter/series Docs
- `https://www.googleapis.com/auth/presentations` — read/edit day-of / series
  Slides, and render slide previews *(scripts)*
- `https://www.googleapis.com/auth/forms.body` — read/edit the intake form
- `https://www.googleapis.com/auth/forms.responses.readonly` — read form responses

Verify with:

```bash
gws sheets spreadsheets get --params '{"spreadsheetId":"<your-sheet-id>"}'
```

---

## Using in other tools

These skills are portable `SKILL.md` files, but not every tool consumes a Claude
Code *plugin*:

- **Claude Code** — install as the plugin above (native).
- **claude.ai / Claude Agent SDK** — zip a skill folder (the dir containing
  `SKILL.md`) and upload it as a Skill. **Caveat:** skills whose scripts import
  the shared `lib/aaif_events` package — `aaif-audit-slack`,
  `aaif-sync-chapters`, `aaif-create-event`, `aaif-update-event`,
  `aaif-event-status` — do **not** work zipped standalone; they need the full
  checkout (or plugin install), since the zip won't contain `lib/`.
- **Cursor** — Cursor uses its own `.cursor/rules/*.mdc` format and does **not**
  consume Claude Code plugins. You can copy a `SKILL.md`'s instructions into a
  Cursor rule, but it won't run the bundled scripts the same way.

The portable unit is the `SKILL.md`; the *plugin/marketplace* packaging is
Claude‑Code‑specific.

---

## Repo layout

The repo root is **both** the marketplace and the single plugin — the marketplace
entry's `source` is `"./"`, so there's no extra `plugins/<name>/` nesting.

```
meetups/
├── .claude-plugin/
│   ├── marketplace.json          # one-plugin marketplace ("aaif")
│   └── plugin.json               # plugin manifest (aaif-events)
├── lib/
│   └── aaif_events/              # shared stdlib-only modules + their tests
├── scripts/                      # repo tooling (e.g. the banner-drift check)
├── skills/
│   ├── aaif-announcement-post/SKILL.md
│   ├── aaif-create-chapter/{SKILL.md, scripts/}
│   └── …  (18 skills total)
└── README.md
```

Bundled scripts are referenced from `SKILL.md` via `${CLAUDE_SKILL_DIR}/scripts/…`
so they resolve correctly once installed.

---

## Contributing

Issues and PRs welcome — new chapter‑ops skills, content variants, and
genericizing the AAIF‑specific IDs into config are all fair game. Keep each skill's
`description` action‑oriented (“Use when asked to …”) so it auto‑activates well.

## License

[MIT](LICENSE) © AAIF
