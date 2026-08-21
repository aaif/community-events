import contextlib
import io
import json
import os
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import clean  # noqa: E402


def _red_rule():
    """The bright-red error rule AS SHEETS RETURNS IT.

    Blue is 53, not 54: BRIGHT_RED's 0.21*255 = 53.55, and Sheets FLOORS where
    round() rounds. The fixture used to say 54 — the round value — which made
    the suite agree with the bug that `_is_red`'s exact compare could never
    match a rule this module wrote."""
    return {"booleanRule": {
        "format": {"backgroundColor": {"red": 232 / 255, "green": 66 / 255, "blue": 53 / 255}},
        "condition": {"type": "CUSTOM_FORMULA",
                      "values": [{"userEnteredValue": '=$I2<>""'}]}}}


def _our_rule(formula, bg=None):
    """A provenance rule as Sheets returns it. `is_ours` requires one of OUR
    colors as well as a matching formula shape; this defaults to AMBER, so pass
    `bg` explicitly when the specific color is what the test is about."""
    return {"booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": formula}]},
            "format": {"backgroundColor": bg if bg else clean.AMBER}}}


def _plan(cfs, err_formula):
    """color_rule_plan with stderr swallowed. Most tests here assert on the
    PLAN; the warnings themselves are asserted in TestColorRulePlanWarnings."""
    with contextlib.redirect_stderr(io.StringIO()):
        return clean.color_rule_plan(cfs, err_formula)


class TestColletter(unittest.TestCase):
    def test_colletter_arithmetic(self):
        # Pure base-26; deliberately NOT annotated with which city column is
        # where — that mapping moves, and a comment here would rot while the
        # assertion stayed green. It is covered in TestFindCityCols instead.
        self.assertEqual(clean.colletter(8), "H")
        self.assertEqual(clean.colletter(9), "I")
        self.assertEqual(clean.colletter(27), "AA")


class TestFindCityCols(unittest.TestCase):
    HDR_TODAY = ["Status", "Full name", "", "", "Email", "Phone", "LinkedIn",
                 "City (Existing)", "City (New)", "Chapter / city wanted"]
    HDR_LEGACY = ["Status", "Full name", "", "", "Email", "Phone",
                  "City", "Resolved City", "Chapter / city wanted"]

    def test_finds_labeled_pair_at_current_position(self):
        self.assertEqual(clean.find_city_cols(self.HDR_TODAY, "Organizers"), (8, 9))

    def test_finds_unlabeled_pair_at_legacy_position(self):
        # Pre-label header text, and one column to the left of today: the whole
        # point of discovery is that BOTH resolve without a code change.
        self.assertEqual(clean.find_city_cols(self.HDR_LEGACY, "Organizers"), (7, 8))

    def test_aborts_when_pair_absent(self):
        with self.assertRaises(SystemExit):
            clean.find_city_cols(["Status", "Full name", "Email"], "Organizers")

    def test_requires_adjacency(self):
        # City and Resolved City present but separated -> not the emitted pair.
        with self.assertRaises(SystemExit):
            clean.find_city_cols(["City", "Phone", "Resolved City"], "Organizers")


class TestFormulaBuilders(unittest.TestCase):
    def test_violet_tracks_the_discovered_status_column(self):
        self.assertEqual(clean.violet_formula(1), '=$A2="Existing (from MLOps)"')
        self.assertEqual(clean.violet_formula(2), '=$B2="Existing (from MLOps)"')

    def test_find_status_col_by_header(self):
        self.assertEqual(clean.find_status_col(["Status", "x"], "Organizers"), 1)
        self.assertEqual(clean.find_status_col(["NEW", "Status"], "Organizers"), 2)
        with self.assertRaises(SystemExit):
            clean.find_status_col(["x", "y"], "Organizers")

    def test_formulas_track_the_discovered_column(self):
        self.assertEqual(clean.amber_formula(9), '=$I2<>""')
        self.assertEqual(clean.green_formula(8), '=AND($H2<>"",LEFT($H2,5)<>"Other")')
        # and would have produced the old text at the old position
        self.assertEqual(clean.amber_formula(8), '=$H2<>""')
        self.assertEqual(clean.green_formula(7), '=AND($G2<>"",LEFT($G2,5)<>"Other")')


class TestFormulaOf(unittest.TestCase):
    def test_reads_custom_formula(self):
        self.assertEqual(clean.formula_of(_our_rule('=$H2<>""')), '=$H2<>""')

    def test_non_custom_and_empty_return_none(self):
        self.assertIsNone(clean.formula_of({}))
        self.assertIsNone(clean.formula_of({"booleanRule": {"condition": {
            "type": "NUMBER_GREATER", "values": [{"userEnteredValue": "5"}]}}}))


class TestIsRed(unittest.TestCase):
    def test_matches_despite_8bit_quantization(self):
        # The whole point: exact float equality would FAIL here; _is_red must not.
        self.assertTrue(clean._is_red(_red_rule()))

    def test_rejects_other_colors_and_missing_bg(self):
        self.assertFalse(clean._is_red(_our_rule('=$I2<>""')))   # amber, not red
        self.assertFalse(clean._is_red({"booleanRule": {}}))     # no backgroundColor
        self.assertFalse(clean._is_red({"booleanRule": {"format": {
            "backgroundColor": clean.VIOLET}}}))


class TestColorRulePlan(unittest.TestCase):
    def test_base_1_and_no_stale_when_only_red_present(self):
        stale, base = _plan([_red_rule()], None)
        self.assertEqual(base, 1)      # our rules go BELOW the red rule
        self.assertEqual(stale, [])

    def test_base_0_when_no_red(self):
        _, base = _plan([], None)
        self.assertEqual(base, 0)

    def test_stale_lists_only_our_rules_descending(self):
        cfs = [_red_rule(),
               _our_rule(clean.violet_formula(1), clean.VIOLET),
               _our_rule(clean.amber_formula(9), clean.AMBER)]
        stale, base = _plan(cfs, None)
        self.assertEqual(stale, [2, 1])   # descending, red at index 0 untouched
        self.assertEqual(base, 1)

    def test_base_follows_red_when_not_first(self):
        # red is NOT at index 0 (a Status color rule precedes it); we must still
        # insert just below red, not at index 1.
        other = _our_rule('=$A2="In progress"')   # not ours, not red -> not stale
        stale, base = _plan([other, _red_rule()], None)
        self.assertEqual(stale, [])
        self.assertEqual(base, 2)                  # just below red at index 1

    def test_base_accounts_for_stale_deleted_above_red(self):
        # a stale rule sits above red; deleting it shifts red up by one.
        stale, base = _plan(
            [_our_rule(clean.amber_formula(9), clean.AMBER), _red_rule()], None)
        self.assertEqual(stale, [0])
        self.assertEqual(base, 1)                  # red -> index 0 after delete, insert at 1


class TestIsOurs(unittest.TestCase):
    """The idempotency contract: every rule install_colors writes must be
    recognised again on the next run, at whatever column it landed on."""

    def test_recognises_installed_rules_at_any_column(self):
        for col in (7, 8, 9, 30):
            self.assertTrue(clean.is_ours(_our_rule(clean.amber_formula(col), clean.AMBER), None),
                            f"amber at col {col}")
            self.assertTrue(clean.is_ours(_our_rule(clean.green_formula(col), clean.GREEN), None),
                            f"green at col {col}")
        self.assertTrue(clean.is_ours(_our_rule(clean.violet_formula(1), clean.VIOLET), None))

    def test_recognises_legacy_green(self):
        # Installed by an earlier release; must be deleted on refresh, not
        # stacked next to the new rule.
        legacy = _our_rule('=AND($G2<>"",$G2<>"Other")', clean.GREEN)
        stale, base = _plan([_red_rule(), legacy], None)
        self.assertEqual(stale, [1])
        self.assertEqual(base, 1)

    def test_does_not_claim_the_bright_red_issues_rule(self):
        # The regression that matters: the amber pattern (=$X2<>"") also matches
        # the Issues rule. Only the color test keeps us from deleting it and
        # silently dropping error highlighting.
        self.assertFalse(clean.is_ours(_red_rule(), None))
        stale, _ = _plan([_red_rule()], None)
        self.assertEqual(stale, [])

    def test_ignores_unrelated_and_uncolored_rules(self):
        self.assertFalse(clean.is_ours(_our_rule('=$A2="In progress"', clean.AMBER), None))
        self.assertFalse(clean.is_ours({"booleanRule": {"condition": {
            "type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": '=$I2<>""'}]}}}, None))  # our shape, no color

    def test_recognises_rules_after_sheets_floors_the_color(self):
        """The idempotency regression: Sheets FLOORS float->8-bit where round()
        rounds, so our own colors come back one unit low. Exact matching then
        finds nothing and a refresh stacks duplicates instead of replacing."""
        as_stored = {"red": 153 / 255, "green": 51 / 255, "blue": 229 / 255}   # violet, -1 blue
        self.assertNotEqual(clean._rgb8(as_stored), clean._rgb8(clean.VIOLET))
        self.assertTrue(clean.is_ours(_our_rule(clean.violet_formula(1), as_stored), None))
        amber_stored = {"red": 252 / 255, "green": 193 / 255, "blue": 76 / 255}
        self.assertTrue(clean.is_ours(_our_rule(clean.amber_formula(9), amber_stored), None))

    def test_color_eq_still_rejects_a_genuinely_different_color(self):
        self.assertFalse(clean._color_eq(clean.AMBER, clean.GREEN))
        self.assertFalse(clean._color_eq(clean.BRIGHT_RED, {"red": 214 / 255,
                                                            "green": 28 / 255,
                                                            "blue": 30 / 255}))


class TestErrorRuleIdentification(unittest.TestCase):
    HDR = ["Status", "Full name", "", "", "Email", "Phone", "LinkedIn",
           "City (Existing)", "City (New)", "Issues"]

    def test_formula_derived_from_issues_column(self):
        self.assertEqual(clean.error_rule_formula(self.HDR), '=$J2<>""')
        self.assertIsNone(clean.error_rule_formula(["Status", "Email"]))

    def test_error_rule_found_by_formula_when_its_color_has_drifted(self):
        # The live sheet's error rule is (214,28,30), not BRIGHT_RED — colour
        # matching misses it, so our rules would land ABOVE it and outrank the
        # error highlight. Formula matching still places them below.
        drifted = {"booleanRule": {
            "format": {"backgroundColor": {"red": 214 / 255, "green": 28 / 255, "blue": 30 / 255}},
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": '=$J2<>""'}]}}}
        err = clean.error_rule_formula(self.HDR)
        self.assertFalse(clean._is_red(drifted))            # colour says "not red"
        stale, base = _plan([drifted], err)
        self.assertEqual(stale, [])                          # never claimed as ours
        self.assertEqual(base, 1)                            # inserted BELOW it

    def test_error_rule_never_deleted_even_though_shape_matches_amber(self):
        drifted = {"booleanRule": {
            "format": {"backgroundColor": {"red": 252 / 255, "green": 193 / 255, "blue": 76 / 255}},
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": '=$J2<>""'}]}}}
        # worst case: error rule shaped like amber AND coloured like amber
        self.assertFalse(clean.is_ours(drifted, clean.error_rule_formula(self.HDR)))


class TestIsRedFlooredRoundTrip(unittest.TestCase):
    """`_is_red` decides rule PRIORITY. If it cannot recognise the red rule this
    module itself wrote, color_rule_plan falls through to base=0 and installs
    the provenance rules ABOVE the error rule — the whole-row violet then hides
    error highlighting on exactly the rows most likely to have errors."""

    def test_recognises_its_own_red_after_sheets_floors_it(self):
        self.assertTrue(clean._is_red(_red_rule()))          # blue 53, floored

    def test_exact_8bit_equality_would_have_missed_it(self):
        stored = _red_rule()["booleanRule"]["format"]["backgroundColor"]
        self.assertNotEqual(clean._rgb8(stored), clean._rgb8(clean.BRIGHT_RED))
        self.assertTrue(clean._color_eq(stored, clean.BRIGHT_RED))

    def test_plan_puts_rules_below_a_floored_red_rule(self):
        stale, base = _plan([_red_rule()], None)
        self.assertEqual((stale, base), ([], 1))   # 1 = below, 0 would be above


class TestVioletSurvivesColumnInsert(unittest.TestCase):
    """Sheets REWRITES stored conditional-format formulas on a column insert —
    the event this whole module defends against. The violet pattern was pinned
    to $A2 while amber/green accepted any column, so a single insert left of
    Status made violet unrecognisable and it stacked a duplicate every run."""

    def test_violet_recognised_after_the_insert_shifts_it(self):
        shifted = '=$B2="Existing (from MLOps)"'
        self.assertTrue(clean.is_ours(_our_rule(shifted, clean.VIOLET), None))

    def test_violet_still_recognised_where_we_install_it(self):
        self.assertTrue(clean.is_ours(_our_rule(clean.violet_formula(1), clean.VIOLET), None))

    def test_shifted_violet_is_refreshed_not_stacked(self):
        shifted = _our_rule('=$B2="Existing (from MLOps)"', clean.VIOLET)
        stale, _ = _plan([_red_rule(), shifted], None)
        self.assertEqual(stale, [1])


class TestGreenPatternBindsOneColumn(unittest.TestCase):
    def test_rejects_a_rule_testing_two_different_columns(self):
        # green_formula can never emit this; the pattern should not accept it.
        self.assertFalse(clean.is_ours(
            _our_rule('=AND($H2<>"",LEFT($Z2,5)<>"Other")', clean.GREEN), None))
        self.assertFalse(clean.is_ours(
            _our_rule('=AND($G2<>"",$Z2<>"Other")', clean.GREEN), None))

    def test_still_accepts_the_matched_pair(self):
        self.assertTrue(clean.is_ours(_our_rule(clean.green_formula(8), clean.GREEN), None))
        self.assertTrue(clean.is_ours(
            _our_rule('=AND($G2<>"",$G2<>"Other")', clean.GREEN), None))


class TestColorEqTolerance(unittest.TestCase):
    def test_tolerance_is_pinned_at_one(self):
        """The pre-existing negative case (BRIGHT_RED vs the drifted UI red) has
        a minimum per-channel gap of 18, so it still passed at tol=18 and did
        not constrain the bound at all. Off-by-2 in a single channel does."""
        off_by_two = {"red": 252 / 255, "green": 192 / 255, "blue": 76 / 255}
        self.assertEqual(clean._rgb8(clean.AMBER), (252, 194, 76))
        self.assertFalse(clean._color_eq(clean.AMBER, off_by_two))
        off_by_one = {"red": 252 / 255, "green": 193 / 255, "blue": 76 / 255}
        self.assertTrue(clean._color_eq(clean.AMBER, off_by_one))

    def test_our_colors_never_collide_at_this_tolerance(self):
        # Sanity check only — the closest pair is 20 apart in its nearest channel,
        # so this still passes at tol=19 and does NOT pin the bound. That job
        # belongs to test_tolerance_is_pinned_at_one above.
        for a, b in ((clean.AMBER, clean.GREEN), (clean.AMBER, clean.VIOLET),
                     (clean.GREEN, clean.VIOLET), (clean.AMBER, clean.BRIGHT_RED)):
            self.assertFalse(clean._color_eq(a, b))

    def test_rgb8_treats_omitted_channels_as_zero(self):
        # The Sheets Color proto omits zero-valued channels, so .get(k, 0) is
        # load-bearing: pure red really does arrive as {"red": 1.0}.
        self.assertEqual(clean._rgb8({"red": 1.0}), (255, 0, 0))
        self.assertEqual(clean._rgb8({}), (0, 0, 0))


class TestFindCityColsAmbiguity(unittest.TestCase):
    def test_refuses_two_candidate_pairs(self):
        # A migration that left a stale block beside the live one: taking the
        # leftmost would relabel and recolor the stale pair.
        with self.assertRaises(SystemExit):
            clean.find_city_cols(
                ["City", "Resolved City", "x", "City (Existing)", "City (New)"], "Organizers")

    def test_refuses_a_half_labeled_pair(self):
        # Can only arise from a run that died between the two header writes.
        with self.assertRaises(SystemExit):
            clean.find_city_cols(["a", "City (Existing)", "Resolved City"], "Organizers")
        with self.assertRaises(SystemExit):
            clean.find_city_cols(["a", "City", "City (New)"], "Organizers")


class TestAutofixNote(unittest.TestCase):
    """The dedupe that stops 'city resolved | city resolved | ...' accumulating.
    Previously inline in apply(), so it had no coverage at all."""

    def test_empty_prior_takes_the_note_verbatim(self):
        self.assertEqual(clean.autofix_note("", ["city resolved"]), "city resolved")

    def test_returns_none_when_the_cell_already_says_it(self):
        self.assertIsNone(clean.autofix_note("city resolved", ["city resolved"]))

    def test_splits_prior_on_both_separators(self):
        self.assertIsNone(clean.autofix_note("a; city resolved", ["city resolved"]))
        self.assertIsNone(clean.autofix_note("a | city resolved", ["city resolved"]))

    def test_keeps_only_the_genuinely_new_phrase(self):
        self.assertEqual(clean.autofix_note("email normalized", ["email normalized", "x"]),
                         "email normalized | x")

    def test_collapses_duplicates_within_one_run(self):
        self.assertEqual(clean.autofix_note("", ["x", "x", "y"]), "x; y")

    def test_a_second_edit_to_the_same_field_is_not_deduped_away(self):
        # apply() carries the new value in the phrase precisely so this appends
        # rather than silently recording nothing.
        self.assertEqual(clean.autofix_note("city extracted -> Zurich",
                                            ["city extracted -> Zürich"]),
                         "city extracted -> Zurich | city extracted -> Zürich")


class TestInstallColorsWiring(unittest.TestCase):
    """install_colors had zero coverage: hardcoding the pair back to (7, 8), or
    swapping a rule's painted range against its formula, passed the whole suite.
    These drive it with gws stubbed and assert on the emitted requests."""

    HDR = ["Status", "Full name", "Email", "LinkedIn", "City", "Resolved City", "Issues"]

    def _run(self, hdr, extra_rules=(), cfs=None):
        calls = []
        # A red rule that references THIS header's Issues column, so the run
        # takes the primary formula-match path rather than the color fallback.
        red = _red_rule()
        red["booleanRule"]["condition"]["values"][0]["userEnteredValue"] = \
            clean.error_rule_formula(hdr)
        table = {1: [red] + list(extra_rules)} if cfs is None else cfs
        # addCleanup restores even if an assertion below raises, and each target
        # is named once — a positional re-unpack got the order wrong too easily.
        for name in ("gws", "read_tab", "_all_conditional_formats", "ROLE_TABS"):
            self.addCleanup(setattr, clean, name, getattr(clean, name))
        clean.gws = lambda args: calls.append(args) or {}
        clean.read_tab = lambda tab: (hdr, [])
        clean._all_conditional_formats = lambda: table
        clean.ROLE_TABS = {"Organizers": 1}
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            clean.install_colors()
        labels = json.loads(calls[0][calls[0].index("--json") + 1])["data"]
        rules = json.loads(calls[1][calls[1].index("--json") + 1])["requests"]
        return labels, rules

    def test_labels_and_rules_track_the_discovered_columns(self):
        labels, rules = self._run(self.HDR)          # pair at E/F -> cols 5,6
        self.assertEqual([d["range"] for d in labels], ["Organizers!E1", "Organizers!F1"])
        self.assertEqual([d["values"][0][0] for d in labels],
                         ["City (Existing)", "City (New)"])
        adds = [r["addConditionalFormatRule"] for r in rules if "addConditionalFormatRule" in r]
        self.assertEqual(len(adds), 3)
        # amber paints City (New) = col 6, and its formula must test that SAME column
        amber = adds[1]["rule"]
        self.assertEqual(amber["ranges"][0]["startColumnIndex"], 5)
        self.assertEqual(amber["ranges"][0]["endColumnIndex"], 6)
        self.assertEqual(amber["booleanRule"]["condition"]["values"][0]["userEnteredValue"],
                         clean.amber_formula(6))
        # green paints City (Existing) = col 5, formula on the same column
        green = adds[2]["rule"]
        self.assertEqual(green["ranges"][0]["startColumnIndex"], 4)
        self.assertEqual(green["ranges"][0]["endColumnIndex"], 5)
        self.assertEqual(green["booleanRule"]["condition"]["values"][0]["userEnteredValue"],
                         clean.green_formula(5))

    def test_everything_shifts_when_a_column_is_inserted_upstream(self):
        labels, rules = self._run(["NEW"] + self.HDR)   # pair now at F/G -> 6,7
        self.assertEqual([d["range"] for d in labels], ["Organizers!F1", "Organizers!G1"])
        adds = [r["addConditionalFormatRule"] for r in rules if "addConditionalFormatRule" in r]
        self.assertEqual(adds[1]["rule"]["ranges"][0]["startColumnIndex"], 6)
        self.assertEqual(adds[1]["rule"]["booleanRule"]["condition"]["values"][0]
                         ["userEnteredValue"], clean.amber_formula(7))

    def test_rules_are_installed_below_the_error_rule(self):
        _, rules = self._run(self.HDR)
        adds = [r["addConditionalFormatRule"] for r in rules if "addConditionalFormatRule" in r]
        self.assertEqual([a["index"] for a in adds], [1, 2, 3])   # red sits at 0

    def test_row_range_is_unbounded(self):
        _, rules = self._run(self.HDR)
        for r in rules:
            if "addConditionalFormatRule" in r:
                self.assertNotIn("endRowIndex", r["addConditionalFormatRule"]["rule"]["ranges"][0])

    def test_each_rule_gets_the_colour_is_ours_recognises(self):
        # Unasserted before, so swapping AMBER/GREEN between the two calls — or
        # passing a colour is_ours does not know, which makes the rule invisible
        # to every later run and stacks a duplicate each time — both passed.
        _, rules = self._run(self.HDR)
        adds = [r["addConditionalFormatRule"] for r in rules if "addConditionalFormatRule" in r]
        got = [a["rule"]["booleanRule"]["format"]["backgroundColor"] for a in adds]
        self.assertEqual(got, [clean.VIOLET, clean.AMBER, clean.GREEN])
        for c in got:
            self.assertTrue(any(clean._color_eq(c, k)
                                for k in (clean.VIOLET, clean.AMBER, clean.GREEN)))

    def test_violet_spans_the_row_and_tests_the_discovered_status_column(self):
        _, rules = self._run(self.HDR)
        violet = [r["addConditionalFormatRule"] for r in rules
                  if "addConditionalFormatRule" in r][0]["rule"]
        rng = violet["ranges"][0]
        self.assertEqual((rng["startColumnIndex"], rng["endColumnIndex"]), (0, len(self.HDR)))
        # Literal, not clean.violet_formula(1): comparing against the builder
        # under test makes a builder mutation change both sides of the assertion.
        self.assertEqual(violet["booleanRule"]["condition"]["values"][0]["userEnteredValue"],
                         '=$A2="Existing (from MLOps)"')
        self.assertTrue(violet["booleanRule"]["format"]["textFormat"]["bold"])

    def test_violet_follows_status_when_a_column_is_inserted_before_it(self):
        # Widening STALE_PATTERNS fixed recognition; this is correctness — a
        # pinned builder would reinstall a rule testing column A, not Status.
        _, rules = self._run(["NEW"] + self.HDR)
        violet = [r["addConditionalFormatRule"] for r in rules
                  if "addConditionalFormatRule" in r][0]["rule"]
        self.assertEqual(violet["booleanRule"]["condition"]["values"][0]["userEnteredValue"],
                         '=$B2="Existing (from MLOps)"')   # literal: Status moved to B

    def test_stale_rules_are_deleted_in_the_same_batch(self):
        # The fixture had no stale rules, so `dels` was always [] and the delete
        # path never reached the batch: `dels = []` passed.
        stale = [_our_rule(clean.violet_formula(1), clean.VIOLET),
                 _our_rule(clean.amber_formula(6), clean.AMBER)]
        _, rules = self._run(self.HDR, extra_rules=stale)
        dels = [r["deleteConditionalFormatRule"]["index"]
                for r in rules if "deleteConditionalFormatRule" in r]
        self.assertEqual(dels, [2, 1], "descending, so earlier deletes don't shift later ones")
        adds = [r["addConditionalFormatRule"]["index"]
                for r in rules if "addConditionalFormatRule" in r]
        self.assertEqual(adds, [1, 2, 3])

    def test_aborts_when_a_role_tab_sheetid_is_missing(self):
        with self.assertRaises(SystemExit):
            self._run(self.HDR, cfs={})

    def test_no_tab_is_written_when_a_later_tab_fails_preflight(self):
        calls = []
        orig = (clean.gws, clean.read_tab, clean._all_conditional_formats, clean.ROLE_TABS)
        clean.gws = lambda args: calls.append(args) or {}
        clean.read_tab = lambda tab: (self.HDR if tab == "Organizers" else ["Status"], [])
        clean._all_conditional_formats = lambda: {1: [_red_rule()], 2: [_red_rule()]}
        clean.ROLE_TABS = {"Organizers": 1, "Hosts": 2}
        try:
            with self.assertRaises(SystemExit):
                clean.install_colors()
        finally:
            (clean.gws, clean.read_tab, clean._all_conditional_formats,
             clean.ROLE_TABS) = orig
        self.assertEqual(calls, [], "Organizers must not be written when Hosts fails preflight")


class TestInstallFlagsRedRule(unittest.TestCase):
    """The endRowIndex fix never reached the live sheet: the red rule was only
    ever ADDED when Issues was absent, and Issues is present on every role tab,
    so the one rule that flags broken emails stayed pinned at row 1000 while the
    provenance colors became unbounded."""

    BASE = ["Status", "Timestamp", "Email", "LinkedIn", "City", "Resolved City"]

    def _run(self, hdr, existing=()):
        calls = []
        for name in ("gws", "read_tab", "_all_conditional_formats",
                     "ROLE_TABS", "install_colors"):
            self.addCleanup(setattr, clean, name, getattr(clean, name))
        clean.gws = lambda args: calls.append(args) or {}
        clean.read_tab = lambda tab: (hdr, [])
        clean._all_conditional_formats = lambda: {1: list(existing)}
        clean.ROLE_TABS = {"Organizers": 1}
        clean.install_colors = lambda: None
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            clean.install_flags()
        batch = [c for c in calls if "batchUpdate" in c and "spreadsheets" in c]
        return [json.loads(c[c.index("--json") + 1]) for c in batch]

    def test_new_rule_is_unbounded(self):
        payloads = self._run(self.BASE)
        req = [r for p in payloads for r in p.get("requests", [])
               if "addConditionalFormatRule" in r][0]
        rng = req["addConditionalFormatRule"]["rule"]["ranges"][0]
        self.assertNotIn("endRowIndex", rng)

    def test_existing_rule_has_its_range_repointed_keeping_its_colour(self):
        hdr = self.BASE + ["Issues"]
        drifted = {"booleanRule": {          # the live red has been re-picked in the UI
            "format": {"backgroundColor": {"red": 214 / 255, "green": 28 / 255, "blue": 30 / 255}},
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": '=$G2<>""'}]}},
            "ranges": [{"sheetId": 1, "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": 0, "endColumnIndex": 7}]}
        payloads = self._run(hdr, existing=[drifted])
        req = [r for p in payloads for r in p.get("requests", [])
               if "updateConditionalFormatRule" in r][0]["updateConditionalFormatRule"]
        self.assertEqual(req["index"], 0)
        self.assertNotIn("endRowIndex", req["rule"]["ranges"][0])
        self.assertEqual(req["rule"]["booleanRule"]["format"]["backgroundColor"]["red"],
                         214 / 255, "the operator's colour choice must be preserved")

    def test_no_duplicate_rule_is_added_when_one_already_exists(self):
        hdr = self.BASE + ["Issues"]
        existing = [{"booleanRule": {"format": {"backgroundColor": clean.BRIGHT_RED},
                     "condition": {"type": "CUSTOM_FORMULA",
                                   "values": [{"userEnteredValue": '=$G2<>""'}]}},
                     "ranges": [{"sheetId": 1}]}]
        payloads = self._run(hdr, existing=existing)
        adds = [r for p in payloads for r in p.get("requests", [])
                if "addConditionalFormatRule" in r]
        self.assertEqual(adds, [])


class TestInstallFlagsPreflight(unittest.TestCase):
    """The Issues formula ships USER_ENTERED, so a missing 'Timestamp' used to
    install a literal `$None2:$None` formula live, and a renamed Email/LinkedIn
    silently shrank the coverage the bright-red rule still claims. Every tab is
    validated before ANY write, mirroring install_colors."""

    GOOD = ["Status", "Timestamp", "Email", "LinkedIn", "City", "Resolved City"]

    def _run(self, headers_by_tab, calls=None):
        # `calls` may be passed in so an aborting run's writes stay observable —
        # a list created here is unreachable once SystemExit propagates.
        calls = [] if calls is None else calls
        for name in ("gws", "read_tab", "_all_conditional_formats",
                     "ROLE_TABS", "install_colors"):
            self.addCleanup(setattr, clean, name, getattr(clean, name))
        clean.gws = lambda args: calls.append(args) or {}
        clean.read_tab = lambda tab: (headers_by_tab[tab], [])
        clean._all_conditional_formats = lambda: {i: [] for i in range(1, 4)}
        clean.ROLE_TABS = {t: i for i, t in enumerate(headers_by_tab, start=1)}
        clean.install_colors = lambda: None
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            clean.install_flags()
        return calls

    def test_aborts_naming_the_missing_headers_before_any_write(self):
        calls = []
        with self.assertRaises(SystemExit) as e:
            self._run({"Organizers": ["Status", "Email", "LinkedIn"]}, calls)
        self.assertIn("'Timestamp'", str(e.exception))
        self.assertIn("No tab modified", str(e.exception))
        self.assertEqual(calls, [], "nothing may be written on a preflight abort")

    def test_a_renamed_email_or_linkedin_is_an_abort_not_shrunk_coverage(self):
        for gone in ("Email", "LinkedIn"):
            hdr = [h for h in self.GOOD if h != gone]
            with self.assertRaises(SystemExit) as e:
                self._run({"Organizers": hdr})
            self.assertIn(repr(gone), str(e.exception))

    def test_no_tab_is_written_when_a_later_tab_fails_preflight(self):
        calls = []
        with self.assertRaises(SystemExit) as e:
            self._run({"Organizers": self.GOOD,
                       "Hosts": ["Status", "Email", "LinkedIn"]}, calls)
        self.assertIn("Hosts", str(e.exception))
        self.assertEqual(calls, [], "Organizers must not be written when Hosts fails")

    def test_a_clean_tab_still_installs(self):
        calls = self._run({"Organizers": self.GOOD})
        self.assertTrue(calls, "the happy path must still write")


class TestAutofixNoteSeparators(unittest.TestCase):
    """The regression the value-carrying phrase introduced: a value containing a
    separator could not be split back out of `prior`, so it never matched `seen`
    and re-appended on every run — unbounded growth, the exact bug autofix_note
    exists to prevent. Values like "Frankfurt; Germany" are ordinary here."""

    def test_a_value_containing_a_separator_still_dedupes(self):
        for value in ("Washington, DC; USA", "Washington | DC", "A;B|C"):
            p = f"city resolved -> {value}"
            first = clean.autofix_note("", [p])
            self.assertIsNone(clean.autofix_note(first, [p]), f"re-appended for {value!r}")

    def test_separators_are_stripped_from_the_stored_note(self):
        note = clean.autofix_note("", ["city resolved -> Frankfurt; Germany"])
        self.assertEqual(note, "city resolved -> Frankfurt, Germany")

    def test_does_not_grow_across_repeated_runs(self):
        p = "city resolved -> Frankfurt; Germany"
        cell = clean.autofix_note("", [p])
        for _ in range(5):
            nxt = clean.autofix_note(cell, [p])
            if nxt is not None:
                cell = nxt
        self.assertEqual(cell.count("city resolved"), 1)

    def test_trailing_whitespace_is_not_a_different_phrase(self):
        self.assertIsNone(clean.autofix_note("x", ["x "]))

    def test_empty_phrase_never_writes_a_blank_cell(self):
        self.assertIsNone(clean.autofix_note("", [""]))
        self.assertIsNone(clean.autofix_note("", ["   "]))


class TestColorRulePlanWarnings(unittest.TestCase):
    """base=0 and a mislocated error rule both mean "provenance colors outrank
    error highlighting". Neither is visible in the stdout success line, so the
    stderr warning is the only signal — and round 1 wired it to the branches
    that mattered least."""

    ERR = '=$W2<>""'

    def _plan(self, cfs, err):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            stale, base = clean.color_rule_plan(cfs, err)
        return stale, base, buf.getvalue()

    def _rule(self, f, bg):
        return {"booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                "values": [{"userEnteredValue": f}]}, "format": {"backgroundColor": bg}}}

    def test_a_non_red_rule_on_the_issues_column_does_not_outrank_the_real_one(self):
        # An operator's own grey "rows with issues" rule sitting above the red
        # one used to win the formula match, putting our rules ABOVE the red.
        grey = self._rule(self.ERR, {"red": 0.8, "green": 0.8, "blue": 0.8})
        red = _red_rule()          # same Issues column, so both are formula hits
        red["booleanRule"]["condition"]["values"][0]["userEnteredValue"] = self.ERR
        stale, base, err = self._plan([grey, red], self.ERR)
        self.assertEqual(base, 2, "must insert below the REAL red rule at index 1")
        self.assertIn("2 rules reference the Issues column", err)

    def test_warns_when_the_issues_rule_is_not_red(self):
        grey = self._rule(self.ERR, {"red": 0.8, "green": 0.8, "blue": 0.8})
        _, base, err = self._plan([grey], self.ERR)
        self.assertEqual(base, 1)
        self.assertIn("is not bright red", err)

    def test_warns_when_the_issues_header_is_missing(self):
        # err_formula=None means the Issues header was deleted or renamed — the
        # state most worth reporting, and the one round 1 left silent.
        _, base, err = self._plan([_red_rule()], None)
        self.assertEqual(base, 1)
        self.assertIn("no Issues header found", err)

    def test_warns_when_no_red_rule_exists_at_all(self):
        _, base, err = self._plan([], self.ERR)
        self.assertEqual(base, 0)
        self.assertIn("ABOVE any error highlighting", err)

    def test_silent_on_a_genuinely_fresh_tab(self):
        _, base, err = self._plan([], None)
        self.assertEqual((base, err), (0, ""))


class TestReadTabRange(unittest.TestCase):
    def test_reads_the_bare_tab_name_with_no_bound(self):
        """The A1:CJ window silently dropped the newest column (Autofixes at CK),
        which is what made apply() overwrite prior notes instead of appending."""
        seen = {}
        orig = clean.gws
        clean.gws = lambda args: seen.update(
            json.loads(args[args.index("--params") + 1])) or {"values": [["A", "B"], ["1"]]}
        try:
            hdr, rows = clean.read_tab("Form Responses")
        finally:
            clean.gws = orig
        self.assertEqual(seen["range"], "Form Responses")
        self.assertNotIn("!", seen["range"])
        self.assertEqual((hdr, rows), (["A", "B"], [["1", ""]]))   # short rows padded


class TestApplyEndToEnd(unittest.TestCase):
    """apply() had no coverage at all: the empty-write guard, the annotated-row
    count, and the value-carrying phrase could each be deleted with a green
    suite — and each is a silent data-integrity failure on a live sheet."""

    # Uses Extracted City, not Resolved City: the latter is derived by an
    # ARRAYFORMULA and apply() now refuses it outright (see the guard test
    # below). These cases are about the provenance machinery, not the column.
    HDR = ["Timestamp", "Full name", "Extracted City", "Autofixes"]

    def _apply(self, wanted, rows):
        calls, out = [], io.StringIO()
        orig_gws, orig_read = clean.gws, clean.read_tab
        clean.gws = lambda args: calls.append(args) or {}
        clean.read_tab = lambda tab: (self.HDR, [list(r) for r in rows])
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(wanted, fh); fh.close()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                clean.apply(fh.name)
        finally:
            clean.gws, clean.read_tab = orig_gws, orig_read
            os.unlink(fh.name)
        return calls, out.getvalue()

    @staticmethod
    def _payload(call):
        return json.loads(call[call.index("--json") + 1])

    def test_the_note_carries_the_new_value(self):
        calls, _ = self._apply([{"row": 2, "header": "Extracted City", "value": "Zurich"}],
                               [["t", "n", "", ""]])
        note = self._payload(calls[1])["data"][0]["values"][0][0]
        self.assertEqual(note, "city extracted -> Zurich")

    def test_a_second_edit_to_the_same_field_is_recorded(self):
        # Without the value in the phrase this dedupes to None and the edit is
        # applied to the sheet with NO provenance — the bug the value fixes.
        calls, _ = self._apply([{"row": 2, "header": "Extracted City", "value": "Zürich"}],
                               [["t", "n", "", "city extracted -> Zurich"]])
        note = self._payload(calls[1])["data"][0]["values"][0][0]
        self.assertEqual(note, "city extracted -> Zurich | city extracted -> Zürich")

    def test_no_second_write_when_every_note_is_already_present(self):
        calls, out = self._apply([{"row": 2, "header": "Extracted City", "value": "Zurich"}],
                                 [["t", "n", "", "city extracted -> Zurich"]])
        self.assertEqual(len(calls), 1, "must not fire an empty-data batchUpdate")
        self.assertIn("on 0 row(s)", out)
        self.assertIn("1 row(s) already noted", out)

    def test_counts_report_rows_actually_annotated(self):
        calls, out = self._apply(
            [{"row": 2, "header": "Extracted City", "value": "Zurich"},
             {"row": 3, "header": "Extracted City", "value": "Bern"}],
            [["t", "n", "", "city extracted -> Zurich"], ["t", "n", "", ""]])
        self.assertIn("annotated 'Autofixes' on 1 row(s) (1 row(s) already noted).", out)

    def test_apply_refuses_a_derived_column(self):
        # Resolved City is an ARRAYFORMULA; a literal anywhere in its spill range
        # collapses the whole column to #REF!, and writing one there used to be
        # the DOCUMENTED way to fix a city. Nothing may be written at all.
        with self.assertRaises(SystemExit) as e:
            self._apply([{"row": 2, "header": "Resolved City", "value": "Zurich"}],
                        [["t", "n", "", ""]])
        self.assertIn("derived", str(e.exception))

    def test_values_are_written_RAW_not_USER_ENTERED(self):
        # USER_ENTERED turns a re-cased "=IMPORTXML(...)" from the public form
        # into a live formula that can exfiltrate the row.
        calls, _ = self._apply([{"row": 2, "header": "Full name", "value": "=importxml(1)"}],
                               [["t", "n", "", ""]])
        self.assertEqual(self._payload(calls[0])["valueInputOption"], "RAW")

    def test_aborts_on_the_header_row(self):
        with self.assertRaises(SystemExit):
            self._apply([{"row": 1, "header": "Full name", "value": "X"}], [["t", "n", "", ""]])

    def test_aborts_past_the_last_data_row(self):
        with self.assertRaises(SystemExit):
            self._apply([{"row": 99, "header": "Full name", "value": "X"}], [["t", "n", "", ""]])

    def test_aborts_when_no_requested_header_exists(self):
        # Distinct from "already clean": a stale change list must not exit 0.
        with self.assertRaises(SystemExit):
            self._apply([{"row": 2, "header": "Nope", "value": "X"}], [["t", "n", "", ""]])


if __name__ == "__main__":
    unittest.main()
