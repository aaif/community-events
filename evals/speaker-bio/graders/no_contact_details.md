---
type: regex
pattern: 'maya@example\.com|555 ?0100'
match: not_contains
target: last_message
arm: with-only
---
The public-copy rule, tested rather than trusted. The prompt volunteers an
address and a phone number the way a real tracker row does, and neither
belongs in a published bio. This is the one grader here that checks a rule
the repo states in nine places and could not previously verify anywhere.
