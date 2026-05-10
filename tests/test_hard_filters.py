import unittest

from app.models.schemas import IntentSpec, StrategyConstraint, StrategySpec
from app.strategy.advisor_service_v2 import _text_has_hard_allow_strategy_language
from app.strategy.compiler import apply_hard_filters
from app.strategy.advisor_service_v2 import _rule_parse_text_to_intent


def make_intent(**kwargs):
    data = {
        "underlying_id": "510300",
        "market_view": "neutral",
        "vol_view": "none",
        "risk_preference": "medium",
        "defined_risk_only": False,
        "prefer_multi_leg": False,
        "dte_min": 20,
        "dte_max": 45,
        "max_rel_spread": 0.03,
        "min_quote_size": 1,
    }
    data.update(kwargs)
    return IntentSpec(**data)


def make_strategy(strategy_type):
    return StrategySpec(
        strategy_type=strategy_type,
        underlying_id="510300",
        legs=[],
        constraints=StrategyConstraint(),
    )


def strategy_types(strategies):
    return [strategy.strategy_type for strategy in strategies]


class HardFilterTests(unittest.TestCase):
    def test_preferred_strategy_language_is_not_hard_allow_without_explicit_only(self):
        self.assertFalse(_text_has_hard_allow_strategy_language("偏空，希望风险不要被击穿"))
        self.assertFalse(_text_has_hard_allow_strategy_language("可以考虑熊市价差或者日历"))
        self.assertTrue(_text_has_hard_allow_strategy_language("只做熊市价差"))
        self.assertTrue(_text_has_hard_allow_strategy_language("仅考虑 iron condor"))

    def test_bearish_theta_defined_risk_no_naked_regression(self):
        intent = make_intent(
            raw_text="我偏空，想收 theta，风险可控，不想裸卖",
            market_view="bearish",
            defined_risk_only=True,
            ban_naked_short=True,
            require_positive_theta=True,
            prefer_income_family=True,
        )
        strategies = [
            make_strategy("naked_call"),
            make_strategy("naked_put"),
            make_strategy("bear_call_spread"),
            make_strategy("bear_put_spread"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["bear_call_spread", "bear_put_spread"])
        hard_filter = filtered[0].metadata["hard_filter"]
        self.assertEqual(
            [item["strategy_type"] for item in hard_filter["filtered_strategies"]],
            ["naked_call", "naked_put"],
        )
        self.assertIn("blocked_naked_short_by_defined_risk", hard_filter["reason_codes"])

    def test_defined_risk_bans_naked_short(self):
        intent = make_intent(defined_risk_only=True)
        strategies = [
            make_strategy("naked_call"),
            make_strategy("naked_put"),
            make_strategy("bear_call_spread"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["bear_call_spread"])

    def test_low_risk_bans_naked_short(self):
        intent = make_intent(risk_preference="low")
        strategies = [
            make_strategy("naked_call"),
            make_strategy("iron_condor"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["iron_condor"])

    def test_banned_strategies_are_strictly_excluded(self):
        intent = make_intent(banned_strategies=["bear_call_spread", "put_calendar"])
        strategies = [
            make_strategy("bear_call_spread"),
            make_strategy("put_calendar"),
            make_strategy("bear_put_spread"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["bear_put_spread"])
        hard_filter = filtered[0].metadata["hard_filter"]
        self.assertIn("blocked_by_banned_strategies", hard_filter["reason_codes"])

    def test_allowed_strategies_are_allow_only(self):
        intent = make_intent(allowed_strategies=["covered_call"])
        strategies = [
            make_strategy("covered_call"),
            make_strategy("bull_put_spread"),
            make_strategy("naked_put"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["covered_call"])

    def test_no_calendar_no_diagonal_text_is_strict(self):
        intent = make_intent(raw_text="不做跨期，不做 diagonal")
        strategies = [
            make_strategy("call_calendar"),
            make_strategy("put_calendar"),
            make_strategy("diagonal_call"),
            make_strategy("diagonal_put"),
            make_strategy("iron_condor"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["iron_condor"])
        hard_filter = filtered[0].metadata["hard_filter"]
        self.assertIn("blocked_calendar_by_text_or_ban", hard_filter["reason_codes"])
        self.assertIn("blocked_diagonal_by_text_or_ban", hard_filter["reason_codes"])

    def test_no_calendar_no_diagonal_text_is_visible_in_intent(self):
        intent = _rule_parse_text_to_intent("不做跨期，不做 diagonal")

        self.assertTrue(intent.no_calendar)
        self.assertTrue(intent.no_diagonal)
        self.assertIn("call_calendar", intent.banned_strategies)
        self.assertIn("put_calendar", intent.banned_strategies)
        self.assertIn("diagonal_call", intent.banned_strategies)
        self.assertIn("diagonal_put", intent.banned_strategies)

    def test_hard_filter_noop_records_skipped_reason(self):
        intent = make_intent(raw_text="不想裸卖")
        strategies = [make_strategy("long_put")]

        filtered = apply_hard_filters(intent, strategies)

        hard_filter = filtered[0].metadata["hard_filter"]
        self.assertTrue(hard_filter["applied"])
        self.assertFalse(hard_filter["filtered_any"])
        self.assertEqual(hard_filter["skipped_reason"], "no_blocked_strategy_generated")
        self.assertIn("naked_call", hard_filter["blocked_strategies"])
        self.assertEqual(hard_filter["filtered_strategies"], [])

    def test_no_naked_text_bans_naked_short(self):
        intent = make_intent(raw_text="不想裸卖")
        strategies = [
            make_strategy("naked_call"),
            make_strategy("naked_put"),
            make_strategy("long_put"),
        ]

        filtered = apply_hard_filters(intent, strategies)

        self.assertEqual(strategy_types(filtered), ["long_put"])
        hard_filter = filtered[0].metadata["hard_filter"]
        self.assertIn("blocked_naked_short_by_text", hard_filter["reason_codes"])


if __name__ == "__main__":
    unittest.main()
