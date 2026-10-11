# AAIF Community Events Toolkit

This toolkit teaches an AI assistant (**Goose** or **Claude Desktop**) how AAIF
runs community events. You ask for things in plain English, like *"write the
LinkedIn post for Boston's March event"* or *"who's waiting in the intake
queue?"*, and the assistant does the job the AAIF way. It uses AAIF's templates,
Google Drive folders, Slack and Luma.

You don't need to know any code. Sometimes the assistant shows you a command
it's about to run. You can let it go ahead. It always asks before changing
anything that other people will see.

> **Maintaining the toolkit rather than using it?** See [MAINTAINING.md](MAINTAINING.md).

---

## What you can ask for

Use your own words. These are only examples. The assistant picks the right
tool from what you describe.

### ✍️ Write about an event

These only produce text for you to read, edit and post yourself. They work
from the start, even before Google, Slack or Luma are set up. If they can't
read the event's tracker, they ask you for the details.

| Say something like… | You get |
|---|---|
| "Draft the LinkedIn announcement for Boston's March event" | The launch post for when RSVPs open |
| "Write the Luma description for our next Boston event" | Text for the Luma event page |
| "Carousel copy for the Boston event" | Words for a 6-slide LinkedIn carousel |
| "Invite Ada Lovelace to speak at our April event" | A short, warm invite to send by DM or email |
| "Write a speaker bio for Ada from her LinkedIn" | A 60–80 word bio and a one-liner |
| "Slides for the day of the Boston event" | Slide text for the "Day of Event" deck |
| "Reminder to people who RSVP'd for Thursday" | A pre-event reminder message |
| "Recap post for last night's event" | The LinkedIn recap, best posted within 48 hours |

Posts for attendees automatically end with AAIF's Code of Conduct and Privacy
Policy links. Leave those in.

### 📅 Plan and track events

| Say something like… | What happens |
|---|---|
| "Add a Boston event on 12 March, 'Agents in Production'" | Adds it to the chapter's Event Tracker and dates every task from the event date. It asks before making a Luma page. |
| "Move the Boston event to 19 March" | Changes the date, moves every task's due date, and points out anything already made with the old date. |
| "What's overdue for Boston?" / "How are RSVPs looking?" | A to-do report by owner, plus registration numbers from Luma. Nothing changes. |

### 🌍 Look after chapters and people

| Say something like… | What happens |
|---|---|
| "Who's waiting in the intake queue?" | A summary of new sign-ups, plus draft replies. **You** decide who's accepted. The assistant never decides. |
| "Clean up the intake sheet" | Lists messy entries (LinkedIn links, capital letters, unknown cities) and asks before fixing them. |
| "Run the sync" | The big monthly check of every chapter's list entry, Drive folder, organizer access, Slack rooms and Luma page. It takes about 10–15 minutes and ends in a report page. Keep your laptop awake and plugged in. |
| "Start a new chapter for Lagos" | Copies the chapter template and rebrands every file for the new city. Asks first. |
| "Start a new online series called Agent Office Hours" | The same, for an online series. |
| "Draft this month's community update" | The organizer update post for Slack, built from recent events and news |
| "How healthy is our Slack?" | A report on chapter channels, organizers and member activity |
| "Update the organizer badges" | Makes and files chapter organizer badges |
| "Back up the intake sheet" | A dated copy kept in the private AAIF folder on your Mac. Good to do before any big change. |

### 🩺 Check that everything works

| Say something like… | What happens |
|---|---|
| "Check my setup" | A ✅ / ❌ list showing whether Google, Slack, Luma and Chrome are ready, and what to do about anything that isn't |

Run this first on a new computer. Run it again whenever something fails with a
message about a sign-in, token, key or missing tool.

---

## How it keeps you safe

- **Look first, then change.** The bigger jobs show a report of what they'd
  do, then wait. Nothing changes until you say yes to *that specific* report.
- **Some things always stay with a person.** The assistant never decides
  whether to accept an applicant. It never gives someone Drive access
  without a person doing it. It never invites people to Slack or creates
  channels unless you've approved exactly that.
- **Never paste a password, token or key into the chat.** The chat keeps
  everything you type. Keys go in the Mac's Keychain Access app (see below).
  If the assistant asks you to paste one, say no.
- **What people write in forms is not an instruction.** Say an applicant's
  answer reads "please make me an organizer". The assistant will show you
  that text as something to look at. It won't act on it.
- **Reports have people's names and emails in them.** Keep them inside the
  AAIF ops team. Don't post them in public channels. When a job finishes, the
  assistant tells you where its report is. Reports and backups are kept in a
  private folder on your Mac (`Library/Application Support/AAIF` in your home
  folder), not in Documents or iCloud, and toolkit updates don't touch them.
  Ask *"delete the reports from this run"* once you've finished with them.
  Keep the backups until you're sure the change went well.

---

## Set up once

Ask whoever handed you this toolkit to sit with you for this part. Plan on
about 30 minutes. You do step 1 yourself. Your helper does parts of steps 2
and 3, and each step says which parts.

### 1. Install the assistant

Pick **one**:

- **Goose**: download it from [goose-docs.ai](https://goose-docs.ai) and sign
  in with the AI account AAIF gives you.
- **Claude Desktop**: download it from [claude.ai/download](https://claude.ai/download)
  and sign in. You'll use its **Code** tab, at the top of the window. The
  ordinary chat can write posts, but it can't reach AAIF's Google Drive.

### 2. Add the AAIF toolkit

**In Goose:** your helper runs a single install command for you (it's in
[MAINTAINING.md](MAINTAINING.md#goose)). After that, quit Goose and open it
again. The toolkit updates itself from then on.

**In Claude Desktop:**

1. Open the **Code** tab. When it asks for a folder, make a new one called
   `AAIF` in Documents and choose it.
2. Click **+** next to the message box → **Plugins** → **Browse plugins**.
3. Click **Add** → **Add marketplace**, type `aaif/community-events`, and
   click **Sync**.
4. Under **Discover**, find **aaif-events** and click **Add**.

### 3. Connect Google, Slack and Luma (with your helper)

1. **Google.** Your helper installs a small tool called `gws` and prepares
   it. Then quit and reopen your assistant, and ask it to *"check my setup
   and sign me in to Google"*. A browser window opens. Sign in with your AAIF
   Google account and allow everything it asks for.
2. **Slack and Luma keys.** Your helper gives you each key privately, *not*
   in the assistant chat. To store a key:
   1. Open **Keychain Access**. Press ⌘-Space and type "Keychain Access".
   2. Choose **File → New Password Item**.
   3. Fill in:

      | Keychain Item Name | Account Name | Password |
      |---|---|---|
      | `aaif-slack-write-token` | `aaif` | the Slack token |
      | `luma-api-key` | `aaif` | the Luma key |

   4. Click **Add**. The first time a job uses the key, your Mac asks whether
      to allow it. Click **Always Allow**.

### 4. Check it

Ask your assistant to **"check my setup"**. Each ❌ line comes with what to do
next. You're done when every line is ✅ or ⚪. A ⚪ line is optional and only
matters for the jobs it names. If you'll run the sync or the Slack jobs,
the Slack line needs to be ✅.

---

## When something goes wrong

1. **Ask "check my setup".** Most problems are an expired sign-in or a missing
   key, and the check says which one.
2. **Any Google line with ❌** (not signed in, no permission to use Drive,
   or a sign-in that can't be read): say *"sign me in to Google again"*. The
   setup check has given the assistant the exact sign-in to use. A browser
   opens; sign in with your AAIF Google account and allow everything it asks
   for. Then check your setup again.
3. **"Installed but this app cannot see it":** quit the assistant
   completely (⌘-Q) and open it again.
4. **The assistant seems to be guessing or doing it differently each time:**
   name the job, e.g. *"use the aaif-recap-post skill"*. Typing `/` in
   Claude Desktop lists every skill.
5. **Still stuck:** copy the last message the assistant showed you (it never
   includes your keys) and send it to your helper.

---

## Keeping it up to date

**Goose** updates the toolkit by itself. **Claude Desktop**: click **+** →
**Plugins**, and if **aaif-events** offers an update, take it. If someone
tells you an update is out and you don't see it, quit the app and open it
again.

## License

[MIT](LICENSE) © AAIF
