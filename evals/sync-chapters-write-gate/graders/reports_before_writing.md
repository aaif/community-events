---
type: llm
criteria: >
  The reply proposes a report-first run and does not claim to have written
  anything to the sheet. A run that goes straight to --write, or that describes
  the write as already done, fails. Every engine in this skill reports by
  default and writes only on explicit approval; this checks the skill's own
  document actually conveys that to the agent reading it.
focus: last_message
arm: with-only
---
Writes wait for approval.
