---
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-triage-intake"'
min: 1
---
The triage skill should fire for a request to see the intake queue.

The first draft of this prompt pasted a full intake row inline and asked
"where does this stand". The skill did not fire, and it was right not to:
with the whole row in the prompt there is nothing to read from the sheet, and
the case scored identically with and without the plugin. A case whose delta is
zero is measuring the base model. Ask for the queue, not about a row you have
already quoted.
