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

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_organizers as ao  # noqa: E402

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
    except ao.SlackError as exc:  # pragma: no cover - defensive
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
    people, _ = _intake(rows)
    check("only exact statuses sync", sorted(p["name"] for p in people), ["A", "B", "C"])


def test_city_precedence_chapter_beats_new_beats_existing():
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "A", "a@x.com", "Old", "New", "Assigned"],
            ["Accepted", "B", "b@x.com", "Old", "New", ""],
            ["Accepted", "C", "c@x.com", "Old", "", ""],
            ["Accepted", "D", "d@x.com", "Other (please specify)", "", ""]]
    people, _ = _intake(rows)
    check("city precedence", [p["city"] for p in people],
          ["Assigned", "New", "Old", ""])


def test_duplicate_rows_dedupe_on_email_but_blanks_stay_distinct():
    rows = [["Status", "Full name", "Email", "City (Existing)", "City (New)", "Chapter"],
            ["Accepted", "A", "a@x.com", "", "Boston", ""],
            ["Accepted", "A again", "a@x.com", "", "Boston", ""],
            ["Accepted", "No email 1", "", "", "Boston", ""],
            ["Accepted", "No email 2", "", "", "Boston", ""]]
    people, dupes = _intake(rows)
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


def test_header_index_aborts_on_missing_and_duplicate():
    check_raises("missing column aborts",
                 lambda: ao.header_index(["A"], "T", "B"), "no 'B'")
    check_raises("duplicate column aborts",
                 lambda: ao.header_index(["A", "A"], "T", "A"), "twice")


# -------------------------------------------------------------------- join ---

def _row(city, public=None, org=None):
    return {"city": city, "regional": None, "public_how": "", "organizers_how": "",
            "public": public, "public_id": "C1", "public_members": 5,
            "public_candidates": [], "organizers_channel": org,
            "organizers_id": "C2", "organizers_channel_members": 3,
            "organizers_private": True}


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


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("audit_organizers: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
