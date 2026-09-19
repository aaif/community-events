# Slack provisioning — channels, invites, directory posts, pruning

> Load this before running pipeline steps 8, 9 or 10 (`provision_channels.py`,
> `invite_organizers.py`, `post_country_directory.py`, `prune_organizers.py`), or when
> a Slack write reports a missing scope, a DISARMED phase, or an UNREADABLE room.

## `--plan`: naming channels that do not exist yet

By default the sheet means *the channel that exists*. `--plan` fills blank cells
with the **convention** name — `<city>`, `<city>-organizers`, `<country>` — even
where nothing of that name exists, turning those cells into a build list for
`provision_channels.py`.

That is a deliberate, temporary state with a real cost: **the organizer audit
aborts** while the sheet names channels that do not resolve. That abort is not a
bug to route around — it is the check that stops a chapter being silently
downgraded to "no channel". So only run `--plan` if the channels really are about
to be created.

Naming, and the one rule that is not obvious:

- **The organizer channel follows the chapter's OWN channel, not the city slug.**
  SF's room is `#bay-area`, so its organizers belong in `#bay-area-organizers` —
  `san-francisco-organizers` would name a room after a chapter that, in Slack,
  does not go by that name.
- **Every chapter gets one**, including those with no accepted organizer yet (26 as of 2026-08-11), so
  the room is ready for a chapter's first organizer rather than something someone
  has to remember to create.
- A **filled cell is never re-planned.** That is what protects `#españa` and the
  deliberate multi-chapter room `#bay-area` (SF + Silicon Valley), which the
  `<city>` convention cannot express at all.

> **Several live channels are deliberately not named `<city>`.** Before
> "correcting" one, read `references/channel-naming-history.md` — the qualified
> slugs (`austin-tx`, `<city>-organizers`) exist because the plain name was
> already taken, and renaming into a squatted name fails or, worse, lands a
> chapter in a stranger's room.

> **The resource columns were opened by a one-shot that has already run.** If a
> column is missing, `references/completed-migrations.md` has the fix; nothing
> in the normal pipeline needs it.

## Creating the planned channels (`provision_channels.py`)

The only script in the repo that **writes to Slack**. It reads the sheet, creates
what the plan names and applies `RENAMES`.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/provision_channels.py               # report
python3 ${CLAUDE_SKILL_DIR}/scripts/provision_channels.py --write --i-have-approval
```

It does **not** share the audit's Slack client. `lib/aaif_events/slack.py` refuses
any method outside `ALLOWED_METHODS`, and that refusal is why a typo in a
30k-member workspace cannot post, invite or archive — widening it for one script
that runs once would remove the guarantee from every audit. So this file carries
its own small write client, with its own allowlist.

What it will never do: **delete** anything, invite a **community member**
(`Organizer Handles` says who belongs where; a human does that inviting, because
a script mass-inviting 100 people to 100 channels is indistinguishable from an
attack and cannot be undone), or create a channel the sheet does not name.

It **does seed ops staff** into organizer channels, added 2026-09-10. An audit
that day found 17 of 87 organizer rooms — every one provisioned on 2026-08-21
and 2026-08-31 — holding neither ops account, because nothing in the repo had
ever put them there: the 70 healthy rooms came from a one-off manual pass, so
each provisioning run silently reopened the hole. Seeding an admin into a room
this script just built is part of building the room; deciding who belongs in the
community is still `invite_organizers.py`'s job, and still a human's call.

The roster is **`Ops staff email` rows on the `Slack Config` tab**, one per
person, read by email (`users.lookupByEmail`) — never a `@handle`, which its
owner can change, and never hardcoded, because this repo is public and the
addresses are real people's. With no such row the phase prints `DISARMED` and
seeds nobody; it never falls back to a default account. Scope is the sheet's
`Organizer Channel` column only — public chapter rooms and country rooms are
public, anyone may join, and staff presence there is not part of building a
room. A private room whose membership the token cannot read is reported
`UNREADABLE` and skipped, never treated as empty.

It **can archive — but only rooms a rename already retired** (authorised
2026-08-17). The deprecated-room sweep closes `*-deprecated` rooms only: public
ones get a farewell pointer post first (no post → no archive), private ones are
archived only once every member is already in the recorded successor room, and
a `-deprecated` room queued for a rename back into service is skipped. A room
with no recorded successor is reported, never archived.

Renames run **before** creates — creating `#bengaluru` first would take the name
and strand `#bangalore`'s members in the old room.

Two prerequisites, neither in place by default: a token with `channels:write`,
`groups:write` and `chat:write` in `$AAIF_SLACK_WRITE_TOKEN` (user-token scopes
for create/rename and the farewell pointer; `channels:join` is worth adding so
the sweep can join a public room before posting in it), and `--i-have-approval`
alongside `--write`. Since 2026-08-22 the **read-only report also prefers
`$AAIF_SLACK_WRITE_TOKEN`** — the Slack CLI credential expired for good and
cannot be re-scoped, so without the env var the run falls back to a dead
credential and says so on stderr.

### The Drive folder link (2026-09-10)

Every chapter's Drive folder link is posted in its **organizer** channel and
pinned. Never the public chapter room: the folder holds the CRM, budgets and
trackers and is shared per-organizer, so a public link would not leak anything
(Drive still refuses everyone else) but would generate Request Access clicks
from members who cannot have it — already a recurring support load. The message
says as much, because the usual cause is being signed into the wrong Google
account, not a missing grant.

The posted URL is **rebuilt from the validated folder id**, never the sheet cell
— the cell is spreadsheet text an editor controls, and Slack renders
`<url|anchor>` as a link and `<!channel>` as a notification, under the ops
admin's own token.

Idempotency is by folder id and needs **both** signals: `pins.list` answers "is
it pinned", the channel history answers "is it posted", and only the second
decides whether to send another message. So a room whose history cannot be read
is SKIPPED in every mode — reported, never posted into.

Scopes: `pins:read`/`pins:write` are **optional**. Without them links are posted
unpinned and every run says so. Pinning that backlog later needs **both** —
with `pins:write` alone a run cannot tell a pinned message from an unpinned one,
so it leaves existing posts alone. `channels:history`/`groups:history` are what
make the check work at all; without them every chapter is skipped rather than
risk a duplicate. All five are listed in `OPTIONAL_SCOPES` and printed when
absent.

The write half resolves that token from the environment **then the repo-root
`.env`** (2026-09-10), the same two sources in the same order as the read half —
though not the same resolution: `load_token()` prefers `AAIF_SLACK_READ_TOKEN`,
so the two can land on different credentials. Scope questions about a write call
are therefore asked of the write token, never of the read client.
Before that it read the environment only, so an estate keeping the token in
`.env` — which is how this one stores it — planned fine and then failed at the
write, and the obvious workaround was `export AAIF_SLACK_WRITE_TOKEN=…` on the
command line. Never do that: shell history and the terminal transcript both keep
it (see CLAUDE.md).

## Adding organizers to their channel (`invite_organizers.py`)

Answers "who is missing from their organizer channel", and behind a second gate,
adds them.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py            # who is missing
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py --city Berlin
python3 ${CLAUDE_SKILL_DIR}/scripts/invite_organizers.py --write --i-have-approval
```

**Identity comes from the intake, not from the handles column.**
`Organizer Handles` is the human-readable mirror; the authoritative chain is
intake row → email → `users.lookupByEmail` → user id, the same chain that filled
the column. A Slack handle is a display name a person can change, so resolving
`@someone` back to an account would break the day they rename themselves — and an
organizer would quietly stop being invited to their own chapter's room.

## Keeping each country channel's chapter directory current (`post_country_directory.py`)

A shared Country Channel needs its own standing post pointing members at their
city's channel — `#india` serves 8 chapters, and a newcomer landing there needs
to know which of the 8 is theirs. This script keeps that post complete as
chapters are added, without ever rewriting the wording a human chose:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/post_country_directory.py                          # what would change
python3 ${CLAUDE_SKILL_DIR}/scripts/post_country_directory.py --write --i-have-approval
```

**Additive only — never `chat.update`.** The workspace already carries at
least three different human phrasings of this post (a plain "This country has
N chapters" template, a "The Nordics have N chapters" variant, and a "we've
opened a dedicated city channel for X" announcement). Picking one canonical
wording and overwriting the others would replace text a human chose on purpose
for no functional gain. So a channel with no directory-style post at all gets a
brand-new one; a channel whose existing self-authored post is missing a chapter
gets a short add-on message naming only what's new; a post authored by someone
else is reported and left alone. Run it after `invite_organizers.py --scope
country` (or `both`) — the channel needs its people before it needs a sign
pointing new arrivals at them.

## Removing non-organizers (`prune_organizers.py`)

The gated counterpart to "adds, never removes": the ONLY script that removes
people, and only from organizer channels. Driven by an explicit keep-list (tab
`Organizer Keeplist`; prefer Slack user IDs over @handles — a handle is
self-service and the sheet is world-readable), never by a heuristic: someone
whose display name matches an intake row lands in a REVIEW bucket that is
reported and never touched. Report-only by default; `--write --i-have-approval`
acts on the REMOVE bucket alone, and it refuses entirely while no keep-list tab
exists. The script's own docstring carries the full design.
