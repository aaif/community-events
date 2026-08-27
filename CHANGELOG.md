# Changelog

All notable changes to the **AAIF Community Events Toolkit** plugin are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
plugin version is the `version` field in `.claude-plugin/plugin.json`.

## [Unreleased]

### Changed
- **Every deck, tracker and CRM in the Drive estate now follows the AAIF design
  system.** The HTML/PDF side already read `design/aaif-tokens.css`; the OOXML
  side never had a seam, so the brand lived in those files as literal font names
  and hex values and had drifted a long way from it — Space Grotesk and Manrope
  as display faces, Arial in every theme, Office's stock colour scheme
  underneath, a warm-grey ramp half a shade off `--line-2`, a navy `1E2761` in
  the trackers, and 205 runs of body prose set in JetBrains Mono when the system
  reserves mono for metadata.
- **The audit reports have no dark mode.** `report_style.py` shipped a light
  palette plus an inverted twin under `prefers-color-scheme` and
  `[data-theme]`. AAIF is a two-surface system where the *designer* chooses
  white editorial or black plate per component, not a palette the viewer's OS
  flips; the page is white and black now appears only where something is
  deliberately drawn on it. The warm off-white is no longer used as a surface.
- **PDFs stop printing in a pre-AAIF palette.** `report_style.py`'s `@media
  print` block pinned `--accent:#5A3D8C` and friends, and because it redefined
  the design system's own token names rather than the report vocabulary, every
  PDF came out in the old purple brand while the screen rendered correctly.
- **The map dot is `--spec-3` teal**, not the invented `14964A`. A you-are-here
  dot is exactly what the system reserves the spectrum for. `MARKER_FILLS` still
  recognises the legacy value so a deck the sweep has not reached stays
  findable.

### Added
- **`lib/aaif_events/ooxml_style.py`** — the OOXML sibling of
  `report_style.py`, and the seam that ends the drift above: skill scripts never
  write a font name or a colour of their own. Its colour map is **role-aware**,
  because it has to be — the trackers use `1e2761` as a cell *fill* and, forty
  characters later in the same run of XML, as a cell *border*, and those go to
  different tokens. The rewrite is minimal-diff, touching only the attribute and
  copying every other byte, so embedded fonts and relationship ids survive and
  "did the bytes change" is a correct upload test.
- **`lib/aaif_events/agent_art.py`** — six background plates per aspect for the
  hero decks, drawn from the tokens rather than by hand, plus the agent motif
  generated from the design system's own 48-unit spec. Includes a stdlib PNG
  reader and GIF89a encoder: three plates animate, and the quantiser refuses
  anything that would band across a full-bleed background (the flat plates move
  0.002% of pixels, the gradient plates 7.5%).
- **`skills/aaif-create-chapter/scripts/restyle_design_system.py`** — the estate
  sweep. Read-only by default, archives every pre-change file to
  `./backups/restyle-<UTC>/` before uploading, leaves organizers' per-event
  copies alone and says how many it skipped, and asserts it reached
  TemplateCity, TemplateSeries and the shared Templates folder — the three that
  mint everything else.

### Changed
- **The event templates' "HOSTED BY / WITH" logo footer is no longer boxed.**
  Each logo slot used to be a bordered, filled rounded-rect button holding
  centred bold text, which read as a control and fought the flat rule-and-type
  language of the rest of the deck. The boxes are gone, the host slot now
  carries the **AAIF lockup** (AAIF hosts these events; the slot used to say
  `HOST VENUE CO.`), and unfilled slots are muted `LOGO 1`, `LOGO 2`, …
  placeholders rather than the misleading `MEMBER LOGO`. The lockup is drawn
  from the mark image each slide already embeds for its own header — identified
  by name, not by being the first picture on the slide — so it cannot drift from
  it. The templates live in Drive, so a chapter sees this only once the backfill
  below has swept it, and new chapters only once the sweep has reached
  TemplateCity.

### Added
- **`skills/aaif-create-chapter/scripts/backfill_host_footer.py`** applies that
  rework to templates that already exist — all chapters, the online series, and
  the shared Templates folder. Read-only by default; `--write` applies, and a
  file already reworked has no chips left to find, so re-running is a no-op.

## [0.5.0]

### Changed
- **Renamed "meetup" → "event" across the plugin.** Legal flagged that "Meetup"
  is a Meetup.com trademark, so all forward-facing wording now uses the **AAIF
  Community Events** brand. Breaking: plugin id `aaif-meetups` → `aaif-events`,
  Python package `lib/aaif_meetups` → `lib/aaif_events` (all imports updated), and
  repo URL → `github.com/aaif/events`. Install as `/plugin install aaif-events@aaif`.
  Skill invocation ids (`/aaif-<skill>`) are unchanged; only descriptions/copy moved.

### Added
- CI now runs the Python test suite — a `pytest` job in `validate.yml` executes the
  `lib/aaif_events/tests` suite and every `skills/*/scripts/test_*.py`, so import
  breakage is caught in CI rather than only locally.

## [0.4.0]

### Added
- **Luma API integration** (`lib/aaif_events/luma.py`): stdlib client for the
  Luma public API (`public-api.luma.com`, per-calendar key from `LUMA_API_KEY`
  or the `luma-api-key` keychain item; Luma Plus required) with pure, unit-tested
  payload builders. All live writes sit behind explicit `--create`/`--apply`
  flags that the agent only runs after the user approves the printed proposal.
  Every script detects whether Luma is connected: when no key is configured it
  degrades gracefully — the push prints the proposal as manual-creation details,
  the sync prints the desired values as a manual checklist, and the stats step
  is skipped with a note — instead of erroring.
- `aaif-create-event` → `scripts/luma_push.py`: create the live Luma event page
  from the tracker entry — times from DATE & TIME + IANA timezone, venue as a
  manual address, capacity, description markdown (from `aaif-luma-description`),
  banner PNG uploaded as the cover, hosts (manager / check-in) — then write the
  event URL back into the tracker's LUMA URL field. Aborts if already pushed.
- `aaif-update-event` → `scripts/luma_sync.py`: field-by-field diff of the
  tracker vs the live event; `--apply` pushes only the changed fields
  (Luma's guest notifications are suppressed unless `--notify-guests` is
  passed) and re-verifies.
  Cancellation deliberately not automated.
- `aaif-event-status` → `scripts/luma_stats.py`: read-only guest counts
  (going / pending / waitlist / invited / declined / checked-in) and
  registration state for pushed events; feeds day-of slides and recap numbers.
  Luma data is never written back into the Intake Ops sheet.

## [0.3.0]

### Added
- `aaif-sync-chapters` skill — sync organizer decisions from the **Intake Ops**
  sheet into the **Chapters List**: merge `Accepted` / `Existing (from MLOps)`
  organizers into each city row's Organizers column and append rows for net-new
  cities (with their Luma link). Report-and-propose by default, one atomic
  `batchUpdate` on approval, idempotent, with unresolved-city and near-miss-city
  guardrails. Unit tests for the pure merge/slug/near-miss logic.

### Fixed
- `gws` JSON parsing (`gws_json` in the sync, create-chapter, and
  create-online-series engines) now splits output on `\n` only — Python's
  `splitlines()` also splits on U+2028 (line separator) *inside* string values,
  which corrupted the JSON when rejoined (hit by a real intake row).
- `aaif-clean-data` treats **any** `Other…` city placeholder as unresolved — the
  form emits both `Other` and `Other (PLEASE TELL US WHERE IN NEXT QUESTION)`,
  and the exact-string match left long-variant rows unflagged (24 on live data)
  and wrongly painted their `City (Existing)` green. The green rule now checks
  the `Other` prefix; retired formulas are tracked in `LEGACY_COLOR_FORMULAS` so
  `install-colors` replaces old rules instead of stacking duplicates.

## [0.2.0]

### Added
- `aaif-create-online-series` skill — clone the **TemplateSeries** folder under the
  top-level **Online** Drive folder and rebrand it for a new online event series
  (reading group, paper club, webinar). The online sibling of `aaif-create-chapter`.
- Repo hardening: `$schema` references on both manifests, `.pre-commit-config.yaml`,
  Ruff config (`pyproject.toml`), and a `validate` CI workflow (pre-commit +
  `claude plugin validate`).

### Changed
- Manifest descriptions and tags now cover **online** meetups/series, not just
  in-person chapters.

## [0.1.0]

### Added
- Initial release: 11 skills for running AAIF in-person meetup chapters — content
  writing (announcement, carousel, Luma description, speaker invite/bio, day-of
  slides, attendee reminder, recap) and chapter ops (`aaif-create-chapter`,
  `aaif-triage-intake`, `aaif-clean-data`).
- One-plugin marketplace (`aaif`) packaging the toolkit for `/plugin install`.
