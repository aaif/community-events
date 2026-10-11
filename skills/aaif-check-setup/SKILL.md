---
name: aaif-check-setup
description: Check that this computer is ready to run the AAIF toolkit — Python, the full plugin install, the gws Google Workspace tool and its Google sign-in, edit access to the Community Intake sheet, the Slack token, the Luma key, and Chrome for PDF reports — and explain in plain language how to fix anything missing. Read-only. Use when asked to check my setup, test whether the toolkit works, after installing it on a new computer, or when another AAIF skill fails because a tool, sign-in, token or key is missing.
compatibility: Requires Python 3. Runs from a standalone skill zip too, where it reports that the full plugin is missing. Network access for the Google, Slack and Luma checks; macOS keychain lookups are skipped elsewhere.
---

# Check the AAIF setup

Paths in this skill are relative to this skill directory. Resolve `<skill-root>`
from the loaded `SKILL.md`; it is a placeholder, not an environment variable.

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

The person asking is often **not technical**. Run the check, then answer in
plain words: what works, what does not, and the next step for each problem.
Skip jargon (PATH, OAuth, environment variable) unless they ask.

## Run it

```bash
python3 <skill-root>/scripts/check_setup.py
```

Read-only. It reads one Google Sheet's metadata, calls Slack `auth.test` and
Luma `calendars/get`. It never prints a token or key — only where it found one
and whether it works. Exit `0` means the required parts are ready; `1` means
at least one ❌ line.

## Reading the result

- ✅ ready · ❌ needed before the Drive/Slack/Luma jobs run · ⚪ optional, only
  the jobs named on that line need it.
- **Writing jobs need none of this.** Announcement posts, Luma descriptions,
  bios, invites, reminders and recaps work from pasted details even when every
  line is ❌. Say so, so nobody thinks the whole toolkit is broken.

## Fixing what it finds

- **Not signed in, signed in without Drive permission, or a sign-in that
  "can no longer be read"** — all three take the same sign-in, with the
  scopes named. The script prints the exact command when a Google line is ❌;
  run that, not a bare `gws auth login` (the default scopes can leave Drive
  out) and not `-s` (an interactive picker this session cannot answer). For
  the unreadable case, the person first deletes every `gws-cli` entry in
  Keychain Access (sign-out leaves them, and a login over a mismatched key
  does not stick), then you run `gws auth logout`. Never run `gws` with
  `USER` unset — it names its key after `$USER` and mints a second one. It opens a browser; the
  person signs in with their AAIF Google account and allows everything asked.
  If `gws` says it has no OAuth client configured, stop: that one-time setup
  belongs to the person who handed the toolkit over, not to this session.
- **`gws` installed but not visible** — the app was opened before `gws` was
  installed. Quitting the app fully and reopening it is the fix; do not edit
  shell profiles on the person's behalf.
- **A token or key is missing** — relay the script's Keychain Access steps.
  **Never ask the person to paste a token or key into the chat**, and never
  put one in a command: the chat transcript and the shell history both keep
  it. The terminal form (`security add-generic-password ... -w`) prompts for
  the secret on a keyboard this session does not have, so do not run it here.
- **Only one skill installed** — the toolkit was uploaded as a single skill.
  Point them to the README's *Set up once* section to install the plugin.

Re-run the check after each fix and report the new result.
