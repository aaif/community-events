---
name: aaif-luma-description
description: Write the Luma event-page description for an AAIF in-person event. Use when asked to draft the Luma description / event page copy for an AAIF event.
argument-hint: '[event title / paste tracker entry]'
---

# AAIF Luma Event Description

Goes on the Luma page — a little longer; include the agenda and who should come.
Sections: **short intro**, **"What we'll cover"**, **"Who should come"**, a simple
**agenda with times**. ~180 words. **End on a line about AAIF being vendor-neutral
and builder-first.**

**House voice:** share the practice, never sell the product. Specific over grand,
builder-to-builder. Signal, not numbers.
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

One quiet line is enough; it sits below the vendor-neutral line, not in the body.

## Input (from the event tracker)

> **If the details were not pasted, fetch them — do not ask for a paste and
> stop.** The event's tracker entry is what these fields come from, and
> `aaif-event-status` reads it: run that skill for the chapter or series and
> take the entry for the event named. `aaif-create-event` and
> `aaif-update-event` know the same layout if the event is being written in the
> same session. Ask the user only for what the tracker genuinely does not hold,
> and name the missing fields rather than asking again in general.

- Event : `[EVENT TITLE] ([SERIES]) — [THEME]`
- When : `[DATE & TIME]`   Where: `[VENUE / CITY]`
- Speaker : `[SPEAKER + TALK]`   Demos: `[DEMO COUNT]`
- Agenda : `[RUN-OF-SHOW TIMES]`   For: `[WHO IT'S FOR]`

## Example (tested — match this format and voice)
Agentic AI Night:

> Agentic AI Night kicks off our Launch Series: one night on what it actually
> takes to run agents in production.
>
> **WHAT WE'LL COVER** — Maya Chen on tool calling at 10M requests a day, then
> three short community demos with Q&A.
>
> **WHO SHOULD COME** — engineers shipping agents, and anyone curious what breaks
> past the demo. (Mention prerequisites here.)
>
> **AGENDA** — 17:30 doors & networking | 18:00 why we're here | 18:05 talk one |
> 18:30 demos ×3 | 18:55 wrap | 19:00 social till late.
>
> AAIF events are vendor-neutral and builder-first: no paid slots, no pitches.
> Curated, RSVP-based — signal, not numbers.
>
> By attending you agree to our Code of Conduct
> (https://events.linuxfoundation.org/about/code-of-conduct) and Privacy Policy
> (https://www.linuxfoundation.org/legal/privacy-policy).
