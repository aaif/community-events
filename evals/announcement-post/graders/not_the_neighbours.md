---
type: regex
pattern: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-(?:recap-post|luma-description|attendee-reminder)"'
match: not_contains
target: trace
arm: with-only
---
No neighbouring content skill should fire for this prompt. A recap is written
after the event, a Luma description is the event page, and a reminder goes to
people who have already RSVP'd — each one here would be the wrong document in
the right voice, which is the failure hardest to notice in review.
