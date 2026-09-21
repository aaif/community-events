---
name: aaif-recap-post
description: Write the post-event LinkedIn recap for an AAIF event (posted within 48 hours, with photos). Use when asked to draft the recap, thank-you, or wrap-up post after an AAIF event.
argument-hint: '[event title / paste tracker entry]'
---

# AAIF Post-Event Recap (LinkedIn)

Posted within 48 hours with photos, while the conversation is still warm (~110
words). **Thank the speaker and venue by name**, share **1-2 concrete takeaways**,
**mention turnout**, and **tease the next event with a link**. Warm and genuine,
not promotional, **one emoji max**.

**House voice:** share the practice, never sell the product. Signal, not numbers.
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

- Event/speaker : `[EVENT TITLE] — [SPEAKER + TOPIC]`
- Thank : `[VENUE / HOST]`
- Takeaways : `[1-2 TAKEAWAYS]`   Turnout: `[TURNOUT]`
- Next : `[NEXT EVENT + DATE]`   RSVP: `[LUMA URL]`

## Example (tested — match this format and voice)
Agentic AI Night:

> Full room for Agentic AI Night last night — thank you to everyone who came to
> talk agents in production.
>
> Maya Chen opened with tool calling at 10M requests a day, and the takeaway
> stuck: past a certain scale, retries and idempotency matter more than which
> model you picked. Then Diego Alvarez and Priya Nair ran demos that were equal
> parts impressive and honest about the rough edges.
>
> Thanks to Host Venue Co. for the space, and our founding members for keeping
> this vendor-neutral.
>
> Next: "AI in Finance," July 22 → lu.ma/aaif-sanfrancisco
>
> Code of Conduct: https://events.linuxfoundation.org/about/code-of-conduct ·
> Privacy: https://www.linuxfoundation.org/legal/privacy-policy
