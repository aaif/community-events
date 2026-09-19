---
name: aaif-speaker-invite
description: Write a short, warm speaker-invite DM or email for an AAIF in-person event. Use when asked to draft a speaker invite, outreach DM, or ask-someone-to-speak message for an AAIF event.
argument-hint: '[speaker name + event / paste tracker entry]'
---

# AAIF Speaker Outreach / Invite

A short, friendly DM or email to invite a speaker (~90 words). Make the ask
**specific and easy to accept**: talk length, topic, date, venue, audience size,
and an offer to flex on timing. **Builder-to-builder, no corporate tone.**

**House voice:** share the practice, never sell the product. Specific over grand.
Signal, not numbers.
> **Public-copy rule.** Everything these skills write gets published — a post,
> a slide, an event page, a DM. Never include an email address, a phone number,
> a door code, or an attendee's name that is not already public. A name on an
> intake row is not public. Where a detail is needed but not publishable,
> describe it ("the venue sends door access to everyone who RSVPs") instead of
> printing it.

## Input (from the event tracker)

> **If the details were not pasted, fetch them — do not ask for a paste and
> stop.** One command reads the event's tracker entry:
>
> ```bash
> python3 skills/aaif-event-status/scripts/fetch_tracker.py "<Chapter or Series>" \
>     --event "<Event Title>"     # or --event next / --event latest
> ```
>
> It resolves the Drive folder, downloads the tracker to a private temp
> directory and prints the fields below. `--event` takes a title, a **unique**
> substring of one, or `next`/`latest`; an ambiguous substring raises rather
> than guessing, because a draft written for the wrong event reads exactly like
> a correct one. Contact details (`SPEAKER EMAIL`, `DOOR CODE`, venue contact)
> are deliberately **withheld** and only named — you never need them to write
> copy, and the public-copy rule above forbids publishing them. Delete the temp
> directory when done (`--print-cleanup` prints the command).
>
> `aaif-create-event` and `aaif-update-event` know the same layout if the event
> is being written in the same session. Ask the user only for what the tracker
> genuinely does not hold, and name the missing fields rather than asking again
> in general.

- Speaker : `[SPEAKER NAME]`   Chapter: `[CHAPTER]`
- Topic : `[TOPIC WE'D LOVE]`   When/where: `[DATE], [VENUE]`
- Audience : `[CAPACITY] [WHO ATTENDS]`

## Example (tested — match this format and voice)
Maya Chen, Agentic AI Night:

> Hi Maya — I help run AAIF San Francisco, a curated, vendor-neutral event for
> people building agents (no pitches, just folks who ship). I'd love to have you
> open our June night with a 25-minute talk on tool calling at scale — Wed June
> 24, evening, in SoMa, ~120 builders. Slides optional; a live walkthrough works
> great too. Would June 24 work for you? If that week's tight, happy to flex on
> the date.
