---
name: aaif-speaker-bio
description: Write a speaker bio (a 60-80 word bio + a one-liner) for an AAIF in-person event speaker. Use when asked to draft/write a speaker bio for an AAIF event or chapter.
argument-hint: '[speaker name / paste their tracker row]'
---

# AAIF Speaker Bio

Produce TWO versions of a speaker bio for an AAIF in-person event:
1. a **60-80 word** third-person bio, and
2. a **one-line** version, **max 18 words**.

**House voice:** share the practice, never sell the product. Warm, concrete,
builder-to-builder. No hype or superlatives. Signal, not numbers. The draft gets
you ~90% there — edit before it ships.
> **Public-copy rule.** Everything these skills write gets published — a post,
> a slide, an event page, a DM. Never include an email address, a phone number,
> a door code, or an attendee's name that is not already public. A name on an
> intake row is not public. Where a detail is needed but not publishable,
> describe it ("the venue sends door access to everyone who RSVPs") instead of
> printing it.
>
> **This covers your reply, not just the draft.** Saying which detail you left
> out is right; repeating its value to say so is not — "I left out her email"
> and "I left out maya@example.com" leave a very different thing in the
> transcript, and the transcript is copied, pasted and pushed like anything
> else. Name the field, never the value. Do not echo one back to confirm it,
> to explain an omission, or to ask whether it may be used.

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

- Name : `[SPEAKER NAME]`
- Role/team : `[ROLE] @ [COMPANY/TEAM]` OR `[CURRENTLY BUILDING X]`
- Works on : `[WHAT THEY WORK ON]`
- Shipped : `[NOTABLE / SHIPPED WORK]`
- Talk : `[TALK TITLE]`
- Links : `[@HANDLE / URL]`

## Example (tested — match this format and voice)
Maya Chen, Agentic AI Night:

> **Bio:** Maya Chen is a Staff Engineer on a payments platform, where she keeps
> tool-calling agents reliable once they leave the demo and hit real traffic. The
> past year she has lived in what changes past ten million tool calls a day —
> retries, idempotency, and the failure modes nobody warns you about. She brings
> those lessons back to the community, and shares her notes at @mayabuilds.
>
> **One-liner:** Maya Chen, Staff Engineer on payments — tool calling at scale,
> and what broke at 10M requests a day.
