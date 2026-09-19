---
type: regex
pattern: '"skill"\s*:\s*"(?:[\w-]+:)?aaif-sync-(?:chapters|organizers|slack)"'
match: not_contains
target: trace
arm: with-only
---
No phase skill should fire on its own for this prompt. The four sync skills sit
very close together in description space — they share "sync", "chapter" and
"organizer" — and the split is only worth having if the general request reaches
the general skill. A phase skill firing here is the failure the split creates
and nothing else would catch.
