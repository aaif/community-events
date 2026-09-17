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

## Input (from the event tracker)

> **If the details were not pasted, fetch them — do not ask for a paste and
> stop.** The event's tracker entry is what these fields come from, and
> `aaif-event-status` reads it: run that skill for the chapter or series and
> take the entry for the event named. `aaif-create-event` and
> `aaif-update-event` know the same layout if the event is being written in the
> same session. Ask the user only for what the tracker genuinely does not hold,
> and name the missing fields rather than asking again in general.

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
