---
type: regex
pattern: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-(?:recap-post|luma-description|attendee-reminder)"'
match: not_contains
target: trace
arm: with-only
---
No neighbouring content skill should fire for this prompt. A recap is written
after the event and a Luma description is the event page, so either one here
would be the wrong document in the right voice — the failure that is hardest
to notice in review.
