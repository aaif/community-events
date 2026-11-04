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

This takes about 30 minutes and you don't need to be technical. You create your
own Google connection and your own Slack and Luma keys. AAIF's Slack app already
exists. If nobody has set it up yet, start with
[first-time setup](MAINTAINING.md#first-time-setup).

To write posts, bios, invites and recaps, you only need steps 1 and 2.

Some steps use **Terminal**. Press ⌘-Space and type "Terminal" to open it. To
run a line, copy it, paste it into Terminal and press Return.

### 1. Install the assistant

Pick **one**:

- **Goose**: download it from [goose-docs.ai](https://goose-docs.ai) and sign
  in with the AI account AAIF gives you.
- **Claude Desktop**: download it from [claude.ai/download](https://claude.ai/download)
  and sign in. You'll use its **Code** tab, at the top of the window. The
  ordinary chat can write posts, but it can't reach AAIF's Google Drive.

### 2. Add the AAIF toolkit

**In Goose:** run this in Terminal, then quit Goose and open it again. The
toolkit updates itself from then on.

```bash
/Applications/Goose.app/Contents/Resources/bin/goose plugin install --auto-update https://github.com/aaif/community-events.git
```

**In Claude Desktop:**

1. Open the **Code** tab. When it asks for a folder, make a new one called
   `AAIF` in Documents and choose it.
2. Click **+** next to the message box → **Plugins** → **Browse plugins**.
3. Click **Add** → **Add marketplace**, type `aaif/community-events`, and
   click **Sync**.
4. Under **Discover**, find **aaif-events** and click **Add**.

### 3. Create your Google connection file

Sign in to Google with your AAIF account first. Each person makes their own file,
because it can be limited to one organization.

1. [Create a project](https://console.cloud.google.com/projectcreate) named
   `aaif-toolkit`. It's free. If Google won't let you, ask your organization's
   Google admin to allow it.
2. [Turn on the Google services](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com,sheets.googleapis.com,docs.googleapis.com,slides.googleapis.com,forms.googleapis.com)
   the toolkit uses. Check that `aaif-toolkit` is the selected project.
3. [Set up the sign-in screen](https://console.cloud.google.com/auth/overview):
   name it `AAIF Toolkit`, use your own email address, and choose **Internal**.
   If Internal isn't offered, choose **External** and add yourself as a test user
   on the [Audience page](https://console.cloud.google.com/auth/audience). With
   External, Google asks you to sign in again about every 7 days.
4. [Create a client](https://console.cloud.google.com/auth/clients), choose
   **Desktop app**, then **Download JSON**. Leave the file in Downloads and don't
   rename it. Keep it private.

### 4. Connect Google

The toolkit reaches Google Drive through a small tool called `gws`. It's made
by a third party, not by Google or AAIF.

1. Install it by running this in Terminal:

   ```bash
   brew install googleworkspace-cli
   ```

   If Terminal says `command not found: brew`, go to [brew.sh](https://brew.sh),
   paste the line on that page into Terminal, and follow the "Next steps" it
   prints at the end. Then run the line above again.
2. Move your Google connection file into place by running this in Terminal. Only
   use a file you downloaded yourself in step 3, never one someone sends you.

   ```bash
   mkdir -p ~/.config/gws && mv ~/Downloads/client_secret*.json ~/.config/gws/client_secret.json && chmod 600 ~/.config/gws/client_secret.json && echo Done
   ```

   It should print `Done`. If it doesn't:
   - *`no matches found`:* the file isn't in Downloads, or its name doesn't start
     with `client_secret`. Move or rename it, then run the line again.
   - *`not a directory`:* Downloads has more than one copy. Delete the extras,
     then run the line again.
3. Quit your assistant completely (⌘-Q), open it again, and ask it to *"check
   my setup and sign me in to Google"*. A browser window opens. Sign in with
   your AAIF Google account. If Google warns that the app isn't verified, click
   **Advanced**, then continue. The screen must name **AAIF Toolkit**, the name
   you gave it in step 3. If it names anything else, stop and ask an AAIF admin.
   Otherwise, allow everything it asks for. If the check then says this account
   can't open the Community Intake sheet, that's expected until you finish step 5,
   so don't sign in again.

Only approve a Google sign-in that you started from your assistant, whoever
sends you a link.

### 5. Get your access and create your keys

Only AAIF can give you access, and you only ask once. Send this to an AAIF admin:

> I'm setting up the AAIF toolkit. My Google address is ___. Please give me edit
> access to the Chapters folder and the Community Intake Ops sheet, add me as a
> collaborator on the AAIF Slack app and to the private organizer rooms, and give
> me admin access to the AAIF Luma calendar.

Leave out Slack or Luma if you won't use them. You need a Slack token to run the
sync, the Slack audit or the community update. Without a Luma key, the toolkit
gives you event-page text to paste into Luma by hand.

**Create your Slack token.** It acts as you and can read what you can, including
your DMs, so keep it to yourself.

1. Open [Slack's app page](https://api.slack.com/apps) and choose the AAIF app.
2. Open **OAuth & Permissions**, click **Install to Workspace** or **Reinstall to
   Workspace**, then **Allow**.
3. Copy the **User OAuth Token**. It starts with `xoxp-`.

Your token only sees private rooms you're in. If the sync or a Slack audit says
organizer rooms are missing, don't approve creating them. Ask an AAIF admin to
add you to those rooms, then run it again.

**Create your Luma key.**

1. On your [calendars page](https://luma.com/home/calendars), open the AAIF
   calendar.
2. Open **Settings → Developer**. Under **API Keys**, click **Create** and name it
   after yourself.
3. Copy the key straight away, because Luma shows it only once.

**Save each key** in your Mac's Keychain, *not* in the assistant chat, because the
chat keeps everything you type.

1. Open **Keychain Access**. Press ⌘-Space and type "Keychain Access".
2. Choose **File → New Password Item**.
3. Fill in:

   | Keychain Item Name | Account Name | Password |
   |---|---|---|
   | `aaif-slack-write-token` | `aaif` | the Slack token |
   | `luma-api-key` | `aaif` | the Luma key |

4. Click **Add**. The first time a job uses the key, your Mac asks whether
   to allow it. Click **Always Allow**.

To replace a key later, don't add a second item with the same name. Double-click
the existing item in Keychain Access, tick **Show password**, paste the new key
and click **Save Changes**.

### 6. Check it

Ask your assistant to **"check my setup"**. Each ❌ line comes with what to do
next. You're done when every line is ✅ or ⚪. A ⚪ line is optional and only
matters for the jobs it names. If you'll run the sync or the Slack jobs,
the Slack line needs to be ✅. To publish event pages to Luma, the Luma line
does too.

---

## When something goes wrong

1. **Ask "check my setup".** Most problems are an expired sign-in or a missing
   key, and the check says which one.
2. **Any Google line with ❌** (not signed in, no permission to use Drive,
   or a sign-in that can't be read): say *"sign me in to Google again"*. The
   setup check has given the assistant the exact sign-in to use. A browser
   opens; sign in with your AAIF Google account and allow everything it asks
   for. Then check your setup again. If the line says this account can't open
   the Community Intake sheet and you haven't finished step 5, do that first. If
   the line says the connection file is
   missing, signing in can't work yet, so do steps 3 and 4 of *Set up once*
   instead.
3. **"Installed but this app cannot see it":** quit the assistant
   completely (⌘-Q) and open it again.
4. **The assistant seems to be guessing or doing it differently each time:**
   name the job, e.g. *"use the aaif-recap-post skill"*. Typing `/` in
   Claude Desktop lists every skill.
5. **Google shows an error:**
   - *"Access blocked" when you sign in:* if you chose Internal, sign in with the
     account that created the file. If you chose External, add yourself as a test
     user (step 3, part 3).
   - *`redirect_uri_mismatch` when you sign in:* your client isn't a **Desktop
     app**. Create a new one and move it into place (step 3, part 4, and step 4).
   - *The setup check says a Google service is turned off:* turn them on (step 3,
     part 2). Signing in again won't fix this.
6. **Google asks you to sign in again about once a week:** that's normal if you
   chose External in step 3. Say *"sign me in to Google again"*.
7. **Still stuck:** copy the last message the assistant showed you (it never
   includes your keys) and send it to an AAIF admin.

---

## Keeping it up to date

**Goose** updates the toolkit by itself. **Claude Desktop**: click **+** →
**Plugins**, and if **aaif-events** offers an update, take it. If someone
tells you an update is out and you don't see it, quit the app and open it
again.

## License

[MIT](LICENSE) © AAIF
