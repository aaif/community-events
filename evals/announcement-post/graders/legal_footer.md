---
type: regex
pattern: "code-of-conduct[\\s\\S]*privacy-policy|privacy-policy[\\s\\S]*code-of-conduct"
match: contains
target: last_message
arm: with-only
---
Attendee-facing copy carries BOTH standing AAIF links by default. The pattern
requires the two of them, in either order: asserting only the Code of Conduct
would pass a post that had quietly dropped the Privacy Policy, which is half of
what footer drift means.
