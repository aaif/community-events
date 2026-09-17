---
type: regex
pattern: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-announcement-post"'
match: not_contains
target: trace
arm: with-only
---
The announcement skill must not fire for an event that already happened.
