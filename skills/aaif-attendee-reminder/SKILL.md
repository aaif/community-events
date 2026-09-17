---
name: aaif-attendee-reminder
description: Write the pre-event reminder to people who RSVP'd to an AAIF event (sent ~1 week out and the morning of). Use when asked to draft the attendee reminder / "see you tomorrow" note for an AAIF event.
argument-hint: '[event title / paste tracker entry]'
---

# AAIF Attendee Reminder

Sent to RSVPs ~1 week out and the morning of. Short, logistics-first (~70 words).
**Lead with date, time, and exact venue / entry.** One line on the speaker. **End
by asking them to update their RSVP if plans change** so the seat can be released.

**House voice:** warm, concrete, builder-to-builder. Signal, not numbers.
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
> stop.** The event's tracker entry is what these fields come from, and
> `aaif-event-status` reads it: run that skill for the chapter or series and
> take the entry for the event named. `aaif-create-event` and
> `aaif-update-event` know the same layout if the event is being written in the
> same session. Ask the user only for what the tracker genuinely does not hold,
> and name the missing fields rather than asking again in general.

- Event : `[EVENT TITLE]`   When: `[DATE], doors [TIME]`
- Venue : `[VENUE / ENTRY NOTES]`   Speaker: `[SPEAKER + TOPIC]`

## Example (tested — match this format and voice)
Agentic AI Night:

> You're set for Agentic AI Night this Wednesday, June 24 — doors 17:30 in SoMa, San
> Francisco (exact address and door code land in your inbox the morning of). Maya
> Chen opens with tool calling at 10M requests a day, then three quick community
> demos. If your plans change, please update your RSVP so we can pass your seat to
> the waitlist. See you there.
>
> Reminder: our Code of Conduct (https://events.linuxfoundation.org/about/code-of-conduct)
> and Privacy Policy (https://www.linuxfoundation.org/legal/privacy-policy) apply.
