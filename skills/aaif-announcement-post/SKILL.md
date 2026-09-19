---
name: aaif-announcement-post
description: Write the LinkedIn launch/announcement post for an AAIF event when RSVPs open. Use when asked to draft the announcement, launch post, or "RSVPs are open" post for an AAIF event.
argument-hint: '[event title / paste tracker entry]'
---

# AAIF Event Announcement (LinkedIn)

The launch post for when RSVPs open. Structure: **one-line hook**, the
**what/when/where**, **speakers + topic with one takeaway**, **RSVP CTA**.
~120 words, scannable, **at most one emoji, max 3 hashtags**.

**House voice:** share the practice, never sell the product. Specific over grand,
builder-to-builder. Signal, not numbers. The RSVP line MUST link the event's RSVP
page — Luma for chapter events; some online events use Gradual.AI. Edit the draft
before it ships.
> **Public-copy rule.** Everything these skills write gets published — a post,
> a slide, an event page, a DM. Never include an email address, a phone number,
> a door code, or an attendee's name that is not already public. A name on an
> intake row is not public. Where a detail is needed but not publishable,
> describe it ("the venue sends door access to everyone who RSVPs") instead of
> printing it.

> **Standard footer (always include).** Close with one quiet line carrying the
> two standing AAIF attendee links — Code of Conduct
> (https://events.linuxfoundation.org/about/code-of-conduct) and Privacy Policy
> (https://www.linuxfoundation.org/legal/privacy-policy). It sits outside any
> word count given above, and it is a default: leave it out only if the user
> says to. Running your own chapter? Swap both URLs in every skill carrying
> this banner.

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

- Chapter : `[CHAPTER]`
- Event : `[EVENT TITLE] ([SERIES])`
- Theme : `[THEME ONE-LINER]`
- When : `[DATE & TIME]`
- Venue : `[VENUE / CITY]`
- RSVP : `[LUMA URL]`
- Speakers : `[SPEAKER + TOPIC; DEMO NAMES]`

## Example (tested — match this format and voice)
Agentic AI Night:

> Agents in production: what's working at scale, and what the demos never showed.
>
> AAIF San Francisco is back with Agentic AI Night — our Launch Series — on Wed,
> June 24, 17:30 in SoMa.
>
> Maya Chen (payments) opens with tool calling at 10M requests a day: the retries,
> the idempotency, the things that only break in prod. Then three 5-minute
> community demos from Diego Alvarez, Priya Nair, and one open slot.
>
> Vendor-neutral, curated, builder-first. No pitches — just people who ship. Seats
> are limited.
>
> RSVP → lu.ma/aaif-sanfrancisco
> #AgenticAI #MCP
>
> Code of Conduct: https://events.linuxfoundation.org/about/code-of-conduct ·
> Privacy: https://www.linuxfoundation.org/legal/privacy-policy
