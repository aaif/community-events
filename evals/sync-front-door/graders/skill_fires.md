---
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-sync"'
min: 1
---
The front door should fire for a whole-estate request. `aaif-sync` owns the
pipeline and its order; the four phase skills each own one engine. This prompt
names no single engine — it describes the drift, the way an operator actually
would — so routing it to a phase skill would run one step of an eight-step
pipeline and report the estate as handled.
