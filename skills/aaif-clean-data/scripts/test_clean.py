import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import clean  # noqa: E402


def _red_rule():
    # Sheets round-trips BRIGHT_RED (0.91,0.26,0.21) at 8-bit: 232/66/54 over 255.
    return {"booleanRule": {
        "format": {"backgroundColor": {"red": 232 / 255, "green": 66 / 255, "blue": 54 / 255}},
        "condition": {"type": "CUSTOM_FORMULA",
                      "values": [{"userEnteredValue": '=$I2<>""'}]}}}


def _our_rule(formula, bg=None):
    """A provenance rule as Sheets returns it. `is_ours` requires one of OUR
    colors as well as a matching formula shape, so tests must supply one."""
    return {"booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": formula}]},
            "format": {"backgroundColor": bg if bg else clean.AMBER}}}


class TestColletter(unittest.TestCase):
    def test_city_columns_and_wraparound(self):
        self.assertEqual(clean.colletter(8), "H")   # City (Existing), today
        self.assertEqual(clean.colletter(9), "I")   # City (New), today
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
        stale, base = clean.color_rule_plan([_red_rule()])
        self.assertEqual(base, 1)      # our rules go BELOW the red rule
        self.assertEqual(stale, [])

    def test_base_0_when_no_red(self):
        _, base = clean.color_rule_plan([])
        self.assertEqual(base, 0)

    def test_stale_lists_only_our_rules_descending(self):
        cfs = [_red_rule(),
               _our_rule(clean.VIOLET_FORMULA, clean.VIOLET),
               _our_rule(clean.amber_formula(9), clean.AMBER)]
        stale, base = clean.color_rule_plan(cfs)
        self.assertEqual(stale, [2, 1])   # descending, red at index 0 untouched
        self.assertEqual(base, 1)

    def test_base_follows_red_when_not_first(self):
        # red is NOT at index 0 (a Status color rule precedes it); we must still
        # insert just below red, not at index 1.
        other = _our_rule('=$A2="In progress"')   # not ours, not red -> not stale
        stale, base = clean.color_rule_plan([other, _red_rule()])
        self.assertEqual(stale, [])
        self.assertEqual(base, 2)                  # just below red at index 1

    def test_base_accounts_for_stale_deleted_above_red(self):
        # a stale rule sits above red; deleting it shifts red up by one.
        stale, base = clean.color_rule_plan(
            [_our_rule(clean.amber_formula(9), clean.AMBER), _red_rule()])
        self.assertEqual(stale, [0])
        self.assertEqual(base, 1)                  # red -> index 0 after delete, insert at 1


class TestIsOurs(unittest.TestCase):
    """The idempotency contract: every rule install_colors writes must be
    recognised again on the next run, at whatever column it landed on."""

    def test_recognises_installed_rules_at_any_column(self):
        for col in (7, 8, 9, 30):
            self.assertTrue(clean.is_ours(_our_rule(clean.amber_formula(col), clean.AMBER)),
                            f"amber at col {col}")
            self.assertTrue(clean.is_ours(_our_rule(clean.green_formula(col), clean.GREEN)),
                            f"green at col {col}")
        self.assertTrue(clean.is_ours(_our_rule(clean.VIOLET_FORMULA, clean.VIOLET)))

    def test_recognises_legacy_green(self):
        # Installed by an earlier release; must be deleted on refresh, not
        # stacked next to the new rule.
        legacy = _our_rule('=AND($G2<>"",$G2<>"Other")', clean.GREEN)
        stale, base = clean.color_rule_plan([_red_rule(), legacy])
        self.assertEqual(stale, [1])
        self.assertEqual(base, 1)

    def test_does_not_claim_the_bright_red_issues_rule(self):
        # The regression that matters: the amber pattern (=$X2<>"") also matches
        # the Issues rule. Only the color test keeps us from deleting it and
        # silently dropping error highlighting.
        self.assertFalse(clean.is_ours(_red_rule()))
        stale, _ = clean.color_rule_plan([_red_rule()])
        self.assertEqual(stale, [])

    def test_ignores_unrelated_and_uncolored_rules(self):
        self.assertFalse(clean.is_ours(_our_rule('=$A2="In progress"', clean.AMBER)))
        self.assertFalse(clean.is_ours({"booleanRule": {"condition": {
            "type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": '=$I2<>""'}]}}}))   # our shape, no color

    def test_recognises_rules_after_sheets_floors_the_color(self):
        """The idempotency regression: Sheets FLOORS float->8-bit where round()
        rounds, so our own colors come back one unit low. Exact matching then
        finds nothing and a refresh stacks duplicates instead of replacing."""
        as_stored = {"red": 153 / 255, "green": 51 / 255, "blue": 229 / 255}   # violet, -1 blue
        self.assertNotEqual(clean._rgb8(as_stored), clean._rgb8(clean.VIOLET))
        self.assertTrue(clean.is_ours(_our_rule(clean.VIOLET_FORMULA, as_stored)))
        amber_stored = {"red": 252 / 255, "green": 193 / 255, "blue": 76 / 255}
        self.assertTrue(clean.is_ours(_our_rule(clean.amber_formula(9), amber_stored)))

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
        stale, base = clean.color_rule_plan([drifted], err)
        self.assertEqual(stale, [])                          # never claimed as ours
        self.assertEqual(base, 1)                            # inserted BELOW it

    def test_error_rule_never_deleted_even_though_shape_matches_amber(self):
        drifted = {"booleanRule": {
            "format": {"backgroundColor": {"red": 252 / 255, "green": 193 / 255, "blue": 76 / 255}},
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": '=$J2<>""'}]}}}
        # worst case: error rule shaped like amber AND coloured like amber
        self.assertFalse(clean.is_ours(drifted, clean.error_rule_formula(self.HDR)))


if __name__ == "__main__":
    unittest.main()
