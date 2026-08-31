#!/usr/bin/env python3
"""Self-tests for the organizer audit's pure logic.

Standalone (not pytest) to match the other skills' script tests, which CI picks
up via `for t in skills/*/scripts/test_*.py`.

What is covered is deliberate: the functions that decide whether a chapter is
reported as *covered* and who its organizers are. A bug in the Slack client
fails loudly; a bug in `match_channels` fails silently and authoritatively, in a
PDF that goes to community leadership and that nothing downstream re-checks.

Not covered: `render()` and the HTML f-strings (asserting on markup would break
on every copy edit), and the `gws`/Chrome subprocess paths.
"""

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_organizers as ao  # noqa: E402

# ao's own import shims lib/ onto sys.path, so this resolves after it.
from aaif_events.slack import SlackError  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))


def check_raises(label, fn, needle=""):
    try:
        fn()
    except SystemExit as exc:
        if needle and needle not in str(exc):
            FAILS.append("%s: aborted, but message lacked %r:\n     %s"
                         % (label, needle, exc))
        return
    except SlackError as exc:  # pragma: no cover - defensive
        if needle and needle not in str(exc):
            FAILS.append("%s: raised, but message lacked %r" % (label, needle))
        return
    FAILS.append("%s: expected an abort, got a clean return" % label)


def chan(name, private=False, archived=False, members=10):
    return {"name": name, "id": "C_" + name, "is_private": private,
            "is_archived": archived, "num_members": members}


BASE_CFG = {
    "public": {}, "regional": {}, "organizers": {},
    "public_prefixes": ["", "meetup-"],
    "organizer_suffixes": ["-organizers", "-chapter-leads"],
    "staff_email_domain": "mlops.community",
}


def cfg(**over):
    out = json.loads(json.dumps(BASE_CFG))
    out.update(over)
    return out


# ---------------------------------------------------------------- matching ---

def test_exact_slug_match():
    rows = ao.match_channels([{"city": "Boston"}], [chan("boston")], cfg())
    check("exact slug matches", (rows[0]["public"], rows[0]["public_how"]),
          ("boston", "exact"))


def test_null_alias_stops_the_matcher():
    """The map's one escape hatch: null means a human checked, so do not guess."""
    rows = ao.match_channels([{"city": "Wellington"}], [chan("wellington")],
                             cfg(public={"Wellington": None}))
    check("null alias suppresses the slug guess",
          (rows[0]["public"], rows[0]["public_how"]), (None, "known-none"))


def test_null_alias_stops_the_organizer_suffix_scan():
    rows = ao.match_channels([{"city": "Pune"}], [chan("pune-organizers", private=True)],
                             cfg(organizers={"Pune": None}))
    check("null organizer alias suppresses the suffix scan",
          (rows[0]["organizers_channel"], rows[0]["organizers_how"]), (None, "known-none"))


def test_broken_alias_does_not_fall_back_to_a_guess():
    rows = ao.match_channels([{"city": "Denver"}], [chan("denver")],
                             cfg(public={"Denver": "colorado"}))
    check("broken alias does not silently match the slug",
          (rows[0]["public"], rows[0]["public_how"]),
          (None, "alias-missing:colorado"))


def test_broken_alias_aborts_the_run():
    rows = ao.match_channels([{"city": "Denver"}], [chan("denver")],
                             cfg(public={"Denver": "colorado"}))
    check_raises("broken alias aborts",
                 lambda: ao.assert_aliases_resolve(rows, "map.json"), "colorado")


def test_archived_alias_counts_as_broken():
    rows = ao.match_channels([{"city": "Denver"}], [chan("colorado", archived=True)],
                             cfg(public={"Denver": "colorado"}))
    check("archived alias target is not coverage", rows[0]["public"], None)


def test_prefix_precedence_is_config_order_not_api_order():
    """Same workspace + same config must give the same answer either way round."""
    both = [chan("meetup-boston"), chan("boston")]
    forward = ao.match_channels([{"city": "Boston"}], both, cfg())[0]["public"]
    reverse = ao.match_channels([{"city": "Boston"}], both[::-1], cfg())[0]["public"]
    check("prefix precedence is deterministic", (forward, reverse), ("boston", "boston"))


def test_private_channel_is_never_a_public_match():
    rows = ao.match_channels([{"city": "Boston"}], [chan("boston", private=True)], cfg())
    check("a private channel is not the city channel", rows[0]["public"], None)


def test_regional_only_applies_when_there_is_no_own_channel():
    rows = ao.match_channels([{"city": "Chennai"}], [chan("india")],
                             cfg(regional={"Chennai": "india"}))
    check("regional fills in when uncovered",
          (rows[0]["public"], rows[0]["regional"]), (None, "india"))
    rows = ao.match_channels([{"city": "Chennai"}], [chan("chennai"), chan("india")],
                             cfg(regional={"Chennai": "india"}))
    check("regional is None once the chapter has its own room",
          (rows[0]["public"], rows[0]["regional"]), ("chennai", None))


def test_near_miss_is_reported_not_matched():
    rows = ao.match_channels([{"city": "Cape Town"}], [chan("cape-town-ai")], cfg())
    check("near miss is a candidate, not coverage",
          (rows[0]["public"], rows[0]["public_candidates"]), (None, ["cape-town-ai"]))


# ------------------------------------------------------------------- intake ---

def test_status_filter_is_exact_after_stripping():
    """A prefix match here once missed all 23 MLOps rows — but surrounding
    whitespace is a spreadsheet artifact, not a different status, so cell()
    strips before the comparison. `Accepted ` counts; `Accepted ✅` does not."""
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "A", "a@x.com", "", "Boston", ""],
            ["Accepted ", "B", "b@x.com", "", "Boston", ""],
            ["Existing (from MLOps)", "C", "c@x.com", "", "Boston", ""],
            ["Accepted ✅", "D", "d@x.com", "", "Boston", ""],
            ["Denied", "E", "e@x.com", "", "Boston", ""],
            ["Interviewing", "F", "f@x.com", "", "Boston", ""]]
    people, _, _ = _intake(rows)
    check("only exact statuses sync", sorted(p["name"] for p in people), ["A", "B", "C"])


def test_city_precedence_chapter_beats_new_beats_existing():
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "A", "a@x.com", "Old", "New", "Assigned"],
            ["Accepted", "B", "b@x.com", "Old", "New", ""],
            ["Accepted", "C", "c@x.com", "Old", "", ""],
            ["Accepted", "D", "d@x.com", "Other (please specify)", "", ""]]
    people, _, _ = _intake(rows)
    check("city precedence", [p["city"] for p in people],
          ["Assigned", "New", "Old", ""])


def test_duplicate_rows_dedupe_on_email_but_blanks_stay_distinct():
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "A", "a@x.com", "", "Boston", ""],
            ["Accepted", "A again", "a@x.com", "", "Boston", ""],
            ["Accepted", "No email 1", "", "", "Boston", ""],
            ["Accepted", "No email 2", "", "", "Boston", ""]]
    people, dupes, _ = _intake(rows)
    check("emailless people are not collapsed",
          (sorted(p["name"] for p in people), dupes),
          (["A", "No email 1", "No email 2"], 1))


def test_zero_accepted_aborts_rather_than_reporting_zero():
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Approved", "A", "a@x.com", "", "Boston", ""]]
    check_raises("zero accepted aborts", lambda: _intake(rows), "Approved")


def _intake(rows):
    original = ao.gws_values
    ao.gws_values = lambda *a, **k: rows
    try:
        return ao.read_intake()
    finally:
        ao.gws_values = original


# ------------------------------------------------- the map, read off the sheet ---

def _chapters(rows):
    original = ao.gws_values
    ao.gws_values = lambda *a, **k: rows
    try:
        return ao.read_chapters()
    finally:
        ao.gws_values = original


#: A Chapters List header row, resource block included. Only the columns
#: read_chapters() touches need to be real.
_CH_HEADERS = ["City", "Slack Channel", "Organizer Channel", "Country Channel"]


def test_channel_columns_become_the_three_tables():
    """The sheet replaces channel_map's public/organizers/regional wholesale."""
    _, tables = _chapters([_CH_HEADERS,
                           ["Denver", "colorado", "denver-organizers", "usa"]])
    check("Slack Channel -> public", tables["public"], {"Denver": "colorado"})
    check("Organizer Channel -> organizers", tables["organizers"],
          {"Denver": "denver-organizers"})
    check("Country Channel -> regional", tables["regional"], {"Denver": "usa"})


def test_blank_cell_leaves_the_city_out_so_the_matcher_still_scans():
    """Blank is 'nobody looked', not 'no channel' — the scan must still run."""
    _, tables = _chapters([_CH_HEADERS, ["Boston", "", "", ""]])
    check("blank is absent from every table",
          [tables[t] for t in ("public", "organizers", "regional")], [{}, {}, {}])
    # And end-to-end: a blank row still reaches the prefix scan.
    rows = ao.match_channels([{"city": "Boston"}], [chan("boston")],
                             cfg(**tables))
    check("blank row still auto-matches", rows[0]["public"], "boston")


def test_none_sentinel_becomes_a_null_and_stops_the_matcher():
    """The sheet's stand-in for JSON null must still stop the guessing."""
    _, tables = _chapters([_CH_HEADERS, ["Wellington", ao.NO_RESOURCE, "", ""]])
    check("'none' becomes None", tables["public"], {"Wellington": None})
    rows = ao.match_channels([{"city": "Wellington"}], [chan("wellington")],
                             cfg(**tables))
    check("a channel that exists is NOT claimed", rows[0]["public"], None)
    check("and the reason is recorded", rows[0]["public_how"], "known-none")


def test_a_typed_hash_prefix_is_tolerated():
    """Humans type '#berlin' into spreadsheets about half the time."""
    _, tables = _chapters([_CH_HEADERS, ["Berlin", "#berlin", "", ""]])
    check("leading # stripped", tables["public"], {"Berlin": "berlin"})
    rows = ao.match_channels([{"city": "Berlin"}], [chan("berlin")], cfg(**tables))
    check("and it resolves", rows[0]["public"], "berlin")


def test_the_sentinel_matches_the_sync_engine():
    """One spelling of 'none' across the two skills, or the sheet lies to one."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "..", "aaif-sync-chapters", "scripts"))
    import sync_chapters
    check("NO_RESOURCE agrees", ao.NO_RESOURCE, sync_chapters.NO_RESOURCE)
    # The audit reads only the three CHANNEL columns. Chapter Folder and
    # Organizer Handles live in the same block but say nothing about matching, so
    # they are excluded by name here rather than by "everything else" — a sixth
    # resource column added later must fail this and be classified deliberately.
    check("the columns audit reads are the channel columns sync writes",
          sorted(ao.CHANNEL_COLUMNS),
          sorted(set(sync_chapters.RESOURCE_COLUMNS)
                 - {"Chapter Folder", "Organizer Handles"}))


def _config(rows):
    original = ao.gws_values
    ao.gws_values = lambda *a, **k: rows
    try:
        return ao.load_config()
    finally:
        ao.gws_values = original


_CFG_HEADERS = ["Setting", "Value", "Notes"]
_GOOD_CFG = [_CFG_HEADERS,
             ["Public channel prefix", ao.EMPTY_VALUE, "note"],
             ["Public channel prefix", "meetup-"],
             ["Organizer channel suffix", "-organizers"],
             ["Staff email domain", "x.com"]]


def test_config_comes_off_the_sheet_in_row_order():
    """Order is load-bearing: the bare slug must be tried before 'meetup-'."""
    cfg = _config(_GOOD_CFG)
    check("the sentinel becomes the empty prefix, first",
          cfg["public_prefixes"], ["", "meetup-"])
    check("suffixes and scalars load too",
          (cfg["organizer_suffixes"], cfg["staff_email_domain"]),
          (["-organizers"], "x.com"))


def test_a_genuinely_blank_value_is_not_read_as_the_bare_prefix():
    """A half-typed row must not silently widen the matcher."""
    check_raises(
        "a trailing blank value aborts",
        lambda: _config(_GOOD_CFG + [["Public channel prefix", ""]]),
        "blank")


def test_an_unknown_setting_label_aborts():
    """A typo'd label would drop a prefix and quietly change what matches."""
    check_raises("unknown label aborts",
                 lambda: _config(_GOOD_CFG + [["Public channel prefixes", "x-"]]),
                 "name no setting")


def test_a_missing_setting_aborts():
    check_raises(
        "a setting with no rows aborts",
        lambda: _config([_CFG_HEADERS, ["Public channel prefix", "meetup-"],
                         ["Staff email domain", "x.com"]]),
        "Organizer channel suffix")


def test_an_absent_config_tab_names_the_migration():
    check_raises("an empty tab aborts with the fix named",
                 lambda: _config([]), "migrate_resource_columns")


def test_the_config_labels_match_the_migration_that_wrote_them():
    """Both skills must spell the row labels and sentinel the same way."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "..", "aaif-sync-chapters", "scripts"))
    import migrate_resource_columns as mig
    check("labels agree", sorted(ao.CONFIG_LABELS),
          sorted(mig.CONFIG_LABELS.values()))
    check("keys agree", sorted(ao.CONFIG_LABELS.values()),
          sorted(mig.CONFIG_LABELS))
    check("empty-value sentinel agrees", ao.EMPTY_VALUE, mig.EMPTY_VALUE)
    check("config tab name agrees", ao.SLACK_CONFIG_TAB, mig.CONFIG_TAB)


def test_header_index_aborts_on_missing_and_duplicate():
    check_raises("missing column aborts",
                 lambda: ao.header_index(["A"], "T", "B"), "no 'B'")
    check_raises("duplicate column aborts",
                 lambda: ao.header_index(["A", "A"], "T", "A"), "twice")


# -------------------------------------------------------------------- join ---

def _row(city, public=None, org=None, country_channel=None):
    return {"city": city, "regional": None, "public_how": "", "organizers_how": "",
            "regional_how": "",
            "public": public, "public_id": "C1", "public_members": 5,
            "public_candidates": [], "organizers_channel": org,
            "organizers_id": "C2", "organizers_channel_members": 3,
            "organizers_private": True,
            "country_channel": country_channel,
            "country_channel_id": "C3" if country_channel else None}


def test_colliding_chapter_names_abort():
    """Montreal/Montréal would otherwise merge, leaving one showing zero."""
    check_raises(
        "fold collision aborts",
        lambda: ao.build_audit([_row("Montreal"), _row("Montréal")], [], {}, {}, {}, "x"),
        "same key")


def test_membership_and_staff_split():
    rows = [_row("Boston", public="boston", org="boston-organizers")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"},
              {"name": "B", "email": "b@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}, "b@x.com": {"id": None, "error": "users_not_found"}}
    membership = {"boston": ["U1"], "boston-organizers": ["U1", "U9", "U8"]}
    directory = {"U9": {"real_name": "Staff", "email": "s@mlops.community"},
                 "U8": {"real_name": "Outsider", "email": "o@gmail.com"}}
    audit, orphans = ao.build_audit(rows, people, slack_ids, membership, directory,
                                    "mlops.community")
    a, b = audit[0]["accepted"]
    check("resolved organizer is placed", (a["slack_account"], a["in_public"],
                                           a["in_organizers"]), (True, True, True))
    check("unresolved organizer is absent everywhere",
          (b["slack_account"], b["in_public"], b["in_organizers"]), (False, False, False))
    check("staff and outsiders are split",
          [(x["name"], x["is_staff"]) for x in audit[0]["unaccounted"]],
          [("Outsider", False), ("Staff", True)])
    check("no orphans", orphans, {})


def test_local_champs_unpulled_reads_as_unknown_not_absent():
    """`local_champs_ids=None` (the pull was skipped) must not collapse to
    "confirmed not a member" — that exact collapse is the bug this suite
    guards against (main() once computed an empty set instead of None)."""
    rows = [_row("Boston", public="boston", org="boston-organizers")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}}
    membership = {"boston": ["U1"], "boston-organizers": ["U1"]}
    audit, _ = ao.build_audit(rows, people, slack_ids, membership, {},
                              "mlops.community", local_champs_ids=None)
    (a,) = audit[0]["accepted"]
    check("unpulled local-champs reads as None, not False",
          a["in_local_champs"], None)


def test_local_champs_known_set_marks_real_membership():
    rows = [_row("Boston", public="boston", org="boston-organizers")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"},
              {"name": "B", "email": "b@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}, "b@x.com": {"id": "U2"}}
    membership = {"boston": ["U1", "U2"], "boston-organizers": ["U1", "U2"]}
    audit, _ = ao.build_audit(rows, people, slack_ids, membership, {},
                              "mlops.community", local_champs_ids={"U1"})
    a, b = audit[0]["accepted"]
    check("member of local-champs reads True", a["in_local_champs"], True)
    check("non-member reads False, not None", b["in_local_champs"], False)


def test_country_channel_is_none_when_chapter_has_none():
    rows = [_row("Boston", public="boston", org="boston-organizers")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}}
    membership = {"boston": ["U1"], "boston-organizers": ["U1"]}
    audit, _ = ao.build_audit(rows, people, slack_ids, membership, {}, "mlops.community")
    (a,) = audit[0]["accepted"]
    check("no country channel on the chapter reads as None, not False",
          a["in_country_channel"], None)


def test_country_channel_membership_is_a_real_bool_when_present():
    rows = [_row("Boston", public="boston", org="boston-organizers",
                 country_channel="united-states")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"},
              {"name": "B", "email": "b@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}, "b@x.com": {"id": "U2"}}
    membership = {"boston": ["U1", "U2"], "boston-organizers": ["U1", "U2"],
                  "united-states": ["U1"]}
    audit, _ = ao.build_audit(rows, people, slack_ids, membership, {}, "mlops.community")
    a, b = audit[0]["accepted"]
    check("in the country channel reads True", a["in_country_channel"], True)
    check("not in the country channel reads False", b["in_country_channel"], False)


def test_person_issues_flags_absence_and_no_slack_account():
    rows = [_row("Boston", public="boston", org="boston-organizers")]
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Boston"},
              {"name": "B", "email": "b@x.com", "status": "Accepted", "city": "Boston"}]
    slack_ids = {"a@x.com": {"id": "U1"}}
    membership = {"boston": [], "boston-organizers": ["U1"]}
    audit, _ = ao.build_audit(rows, people, slack_ids, membership, {}, "mlops.community")
    a, b = audit[0]["accepted"]
    check("no Slack account is the only issue reported for B",
          ao.person_issues(b, audit[0], True),
          ["no Slack account under their intake email"])
    check("present in organizers but absent from public channel is flagged for A",
          ao.person_issues(a, audit[0], True),
          ["not in #boston (their chapter's public channel)"])
    check("local-champs absence is never flagged as an issue",
          any("local-champs" in x or "local_champs" in x
              for x in ao.person_issues(a, audit[0], True)), False)


def test_unnamed_member_is_marked_unresolved_not_accused():
    rows = [_row("Boston", org="boston-organizers")]
    audit, _ = ao.build_audit(rows, [], {}, {"boston-organizers": ["U7"]}, {},
                              "mlops.community")
    (x,) = audit[0]["unaccounted"]
    check("unnamed member is flagged, not filed as unaccounted",
          (x["unresolved"], x["is_staff"], x["name"]), (True, False, "U7"))


def test_unmapped_intake_city_becomes_an_orphan():
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted", "city": "Atlantis"}]
    audit, orphans = ao.build_audit([_row("Boston")], people, {}, {}, {}, "x")
    check("unmapped city is reported, not silently dropped",
          (list(orphans), len(audit[0]["accepted"])), (["Atlantis"], 0))


def test_folding_matches_accent_and_punctuation_variants():
    people = [{"name": "A", "email": "a@x.com", "status": "Accepted",
               "city": "Washington, DC"}]
    audit, orphans = ao.build_audit([_row("Washington DC")], people, {}, {}, {}, "x")
    check("intake spelling joins the chapter row",
          (len(audit[0]["accepted"]), orphans), (1, {}))


#: A floor, not a target. Without it, renaming the `test_` prefix or losing the
#: functions in a bad merge prints "all checks passed" having run nothing.
def test_an_applicant_in_the_room_is_distinguished_from_a_stranger():
    """"We never accepted them" collapsed two opposite situations into one line.

    Bengaluru 2026-08-27: the chapter's de-facto lead — first invited the minute
    the organizers room was created, running the venue/date planning since July
    — sat at Prospect on the intake. Acceptance is what triggers the Drive
    grant, so he was locked out of his own chapter's folder and said so in the
    channel. The audit DID list him, under the same label as a total stranger.
    """
    rows = [_row("Boston", org="boston-organizers")]
    directory = {"U7": {"real_name": "Ada", "email": "ada@x.io"},
                 "U8": {"real_name": "Zed", "email": "zed@x.io"},
                 "U9": {"real_name": "Cy", "email": "cy@x.io"}}
    applicants = {"ada@x.io": {"status": "Prospect", "name": "Ada", "city": "Boston"},
                  "cy@x.io": {"status": "Denied", "name": "Cy", "city": "Boston"}}
    audit, _ = ao.build_audit(rows, [], {},
                              {"boston-organizers": ["U7", "U8", "U9"]},
                              directory, "staff.org", applicants)
    by_email = {x["email"]: x for x in audit[0]["unaccounted"]}
    check("an applicant carries their intake status",
          by_email["ada@x.io"]["intake_status"], "Prospect")
    check("a stranger carries none", by_email["zed@x.io"]["intake_status"], "")
    check("a decided-no carries theirs too",
          by_email["cy@x.io"]["intake_status"], "Denied")
    # The render splits on exactly this, and a decided NO is not "awaiting".
    pend = [x for x in audit[0]["unaccounted"]
            if x.get("intake_status") and x["intake_status"] not in ao.DECIDED_NO]
    check("only the live applicant counts as awaiting a decision",
          [x["email"] for x in pend], ["ada@x.io"])
    check("Denied is not awaiting", "cy@x.io" in [x["email"] for x in pend], False)


def test_applicants_map_covers_every_row_not_just_accepted_ones():
    """read_intake's third return is the whole sheet — the accepted filter is
    what made a pending organizer indistinguishable from a stranger."""
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "Ada", "ada@x.io", "", "", "Boston"],
            ["Prospect", "Bo", "bo@x.io", "", "", "Boston"],
            ["Denied", "Cy", "cy@x.io", "", "", "Boston"]]
    people, _, applicants = _intake(rows)
    check("only the accepted reach `people`", [p["email"] for p in people], ["ada@x.io"])
    check("but every row reaches `applicants`",
          sorted(applicants), ["ada@x.io", "bo@x.io", "cy@x.io"])
    check("with its real status", applicants["bo@x.io"]["status"], "Prospect")


MIN_TESTS = 45


def test_regional_alias_that_no_longer_resolves_aborts():
    """The regional map is the largest of the three and had no protection: a
    renamed target silently flipped a chapter to 'No channel at all' and into
    the 'give them a room' action."""
    rows = ao.match_channels([{"city": "Chennai"}], [chan("india-old")],
                             cfg(regional={"Chennai": "india"}))
    check("broken regional alias is recorded",
          rows[0]["regional_how"], "alias-missing:india")
    check_raises("broken regional alias aborts",
                 lambda: ao.assert_aliases_resolve(rows, "map.json"), "india")


def test_null_regional_alias_is_respected():
    rows = ao.match_channels([{"city": "Chennai"}], [chan("india")],
                             cfg(regional={"Chennai": None}))
    check("null regional suppresses the fallback",
          (rows[0]["regional"], rows[0]["regional_how"]), (None, "known-none"))


def test_organizer_suffix_precedence_is_config_order():
    """Mirrors the public prefix rule: -organizers must beat -chapter-leads
    regardless of which city variant sorts first."""
    chans = [chan("cape-town-chapter-leads", private=True),
             chan("capetown-organizers", private=True)]
    forward = ao.match_channels([{"city": "Cape Town"}], chans, cfg())[0]
    reverse = ao.match_channels([{"city": "Cape Town"}], chans[::-1], cfg())[0]
    check("organizer suffix precedence follows config",
          (forward["organizers_channel"], reverse["organizers_channel"]),
          ("capetown-organizers", "capetown-organizers"))


def test_a_public_alias_pointing_at_a_private_channel_aborts():
    """The auto path refuses private channels; an alias must not bypass it."""
    rows = ao.match_channels([{"city": "Boston"}], [chan("secret", private=True)],
                             cfg(public={"Boston": "secret"}))
    check("private public-alias is recorded, not matched",
          (rows[0]["public"], rows[0]["public_how"]),
          (None, "alias-private:secret"))
    check_raises("private public-alias aborts by default",
                 lambda: ao.assert_aliases_resolve(rows, "map.json"), "PRIVATE")


def test_a_private_public_alias_downgrades_under_planned_ok():
    """--planned-ok reports the pre-convert state truthfully instead of dying."""
    rows = ao.match_channels([{"city": "Boston"}], [chan("secret", private=True)],
                             cfg(public={"Boston": "secret"}))
    planned, held = ao.mark_planned_aliases(rows)
    check("held-private downgrade is distinct from planned",
          (planned, held), ([], [("Boston", "secret")]))
    check("downgraded row still reports no public channel",
          (rows[0]["public"], rows[0]["public_how"]),
          (None, "held-private:secret"))
    ao.assert_aliases_resolve(rows, "map.json")  # must no longer raise


def test_unidentified_members_are_excluded_from_the_accusing_counts():
    """build_audit marks them; the aggregates must not re-merge them into
    'people we never accepted'."""
    rows = [_row("Boston", org="boston-organizers")]
    audit, _ = ao.build_audit(rows, [], {}, {"boston-organizers": ["U7", "U8"]},
                              {"U8": {"real_name": "Known", "email": "k@gmail.com"}},
                              "mlops.community")
    flags = sorted(x["unresolved"] for x in audit[0]["unaccounted"])
    check("one identified, one not", flags, [False, True])
    html = ao.render(audit, {}, 0, dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc))
    check("the identified person appears in the person-by-person table",
          "Known" in html, True)
    check("the unidentified member's raw id is not rendered as a roster row",
          "<td>U7</td>" in html, False)
    check("the unidentified are surfaced in Data quality",
          "could not be identified" in html, True)


# ------------------------------------------------- membership short-pull floor ---

def _floor(membership, cached_chans, chans_cached, fresh_chans=None):
    """Run check_membership_floor over fixture channel lists; returns the
    number of refetches made (or the SystemExit, via check_raises at callers)."""
    calls = []

    def refetch():
        calls.append(1)
        return fresh_chans if fresh_chans is not None else cached_chans

    ao.check_membership_floor(membership, cached_chans, chans_cached, refetch,
                              note=lambda *_: None)
    return len(calls)


def test_short_against_cached_size_passes_when_fresh_matches():
    """The person-left-since-the-cache case: one refetch, then a clean pass."""
    n = _floor({"a": ["U1"]}, [chan("a", members=2)], True,
               fresh_chans=[chan("a", members=1)])
    check("stale cache resolves with exactly one refetch", n, 1)


def test_short_against_a_fresh_list_aborts_without_refetching():
    check_raises("fresh+short aborts",
                 lambda: _floor({"a": ["U1"]}, [chan("a", members=2)], False),
                 "membership came back short")


def test_still_short_after_the_refetch_aborts():
    check_raises("still-short-after-refetch aborts",
                 lambda: _floor({"a": ["U1"]}, [chan("a", members=2)], True,
                                fresh_chans=[chan("a", members=2)]),
                 "#a (1 of 2)")


def test_a_channel_renamed_between_snapshots_is_not_silently_passed():
    """The old code's sizes.get(n)-is-None skip let a renamed channel escape
    the floor entirely; it must abort naming the vanished channel instead."""
    check_raises("renamed channel aborts, not passes",
                 lambda: _floor({"a": ["U1"]}, [chan("a", members=2)], True,
                                fresh_chans=[chan("a-renamed", members=2)]),
                 "#a")


def test_a_join_after_the_pull_is_not_a_short_pull():
    """Channel `b` triggers the refetch; `a` passed the cached floor and only
    looks short against the FRESH size because someone joined in between —
    re-checking it would turn the join race into a spurious abort."""
    n = _floor({"a": ["U1"], "b": ["U1"]},
               [chan("a", members=1), chan("b", members=2)], True,
               fresh_chans=[chan("a", members=2), chan("b", members=1)])
    check("a join race on an already-verified channel passes", n, 1)


def test_settled_and_inflight_states_get_no_create_advice():
    """`held-private:` / `planned:` / `known-none` chapters already have their
    answer surfaced in Data quality; a create recommendation against the
    squatting (or deliberately absent) room is guaranteed-bad advice."""
    def c(city, how, n=2):
        return dict(_row(city), public_how=how,
                    accepted=[{"slack_account": True}] * n, unaccounted=[])
    audit = [c("Boston", ""), c("Bern", "planned:bern"),
             c("Graz", "held-private:graz"), c("Wellington", "known-none"),
             c("Pune", "", n=1)]
    check("only the genuinely uncovered 2+ chapter gets create advice",
          [x["city"] for x in ao.rooms_to_create(audit)], ["Boston"])


def main():
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                ran += 1
            except Exception as exc:      # keep going: one raise must not hide the rest
                FAILS.append("%s raised %s: %s" % (name, type(exc).__name__, exc))
    if ran < MIN_TESTS:
        print("FAIL: only %d tests ran, expected at least %d — did the "
              "collection break?" % (ran, MIN_TESTS))
        return 1
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("audit_organizers: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
