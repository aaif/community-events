# Measurement limits — what this audit cannot see

> Load this before writing the summary that goes with a report, when a number looks
> implausible, or when someone asks for activity data the API will not give.
> The short version is in SKILL.md; this is the full argument and the evidence.

**Which ceiling applies depends on the token.** The Slack CLI token — the
expired credentials-file fallback — carries no `channels:history` and no
`search:read`, and `conversations.history` returns `missing_scope` even for
public channels: on a token like that, *last message posted* and *is this
channel alive* are **unreadable**, and the organizer/member reports print
exactly that. The **AAIF app token** — the standing token, see SKILL.md's Preflight —
DOES carry `channels:history`/`groups:history`
— `conversations.history` is in the client's allowlist for it, and
`audit_activity.py` is the engine built on it. Even then the numbers are
**floors**: thread replies are invisible to `conversations.history`, and the
reports say so on their face. (`lib/aaif_events/slack.py` holds the
authoritative scope list, and `Slack.scopes()` reports the live one — trust
those over any list written down here or there.)

Both engines check their required scopes **before** collecting anything —
including `groups:read`, which the private-channel half of `conversations.list`
needs — so a revoked scope aborts in the first second. That matters more than it sounds:
without the check, a missing `users:read.email` made every organizer lookup fail,
and each failure was recorded as "this person has no Slack account" — rendering
the report's headline, its funnel and its top recommendation as confident
fiction. Failures that mean *the audit is broken* must never be reported as
findings about people.

Never substitute a proxy:

- **A channel's `updated` field is not activity.** A bulk migration reset it in
  blocks — 54 channels share one identical value, 8 share another. Useless as a
  staleness signal; the reports don't use it. `topic.last_set` /
  `purpose.last_set` are genuine human edits and are used instead.
- **A member's `updated` field is not engagement.** It moves on any profile or
  settings change, so a long-departed member and a content lurker look identical.
  The report frames it as a *floor on staleness* and says so on the page.

**For real activity data**, point the user at the admin **Analytics → Channels /
Members** CSV export — per-channel last-activity and messages-posted, per-member
last-active. A workspace admin downloads it from the UI; no scope change, no API.
It is the first recommendation in the member report for that reason.

**Private channels are undercounted.** `conversations.list` returns only the
private channels the *token owner* belongs to. `users.conversations(user=…)`
looks like a way around this and is not — its results are filtered to the
caller's own visibility (verified: probing 101 staff, organizers and admins
returned exactly the caller's own 23 private channels, nothing more). A
workspace-wide list needs **Enterprise Grid** (`admin.conversations.search`,
org-level token) or the admin UI's *Show all private channels*, which is
Business+/Enterprise and held by the Workspace Primary Owner.
