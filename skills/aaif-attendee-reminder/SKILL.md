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

**Standard footer (always include).** After the closing update-your-RSVP sentence, append one quiet line
with the two standing AAIF attendee links (defaults on every reminder), e.g.
*"Reminder: our Code of Conduct
(https://events.linuxfoundation.org/about/code-of-conduct) and Privacy Policy
(https://www.linuxfoundation.org/legal/privacy-policy) apply."* This sits outside the ~70-word
body count.

## Input (from the event tracker)
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
