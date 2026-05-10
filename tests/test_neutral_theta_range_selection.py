import unittest

from app.models.schemas import IntentSpec, ResolvedStrategy
from app.strategy.advisor_service_v2 import _rule_parse_text_to_intent
from app.strategy.recommendation_selector import select_recommendations


def make_intent(range_bias="strict_range"):
    return IntentSpec(
        underlying_id="510300",
        market_view="neutral",
        vol_view="none",
        risk_preference="medium",
        defined_risk_only=False,
        prefer_multi_leg=True,
        dte_min=20,
        dte_max=45,
        max_rel_spread=0.03,
        min_quote_size=1,
        raw_text="我中性，想收 theta，认为上下都有边界",
        require_positive_theta=True,
        prefer_income_family=True,
        range_bias=range_bias,
        greeks_preference={"theta": {"sign": "positive", "strength": 0.9}},
    )


def make_strategy(strategy_type, score):
    return ResolvedStrategy(
        strategy_type=strategy_type,
        underlying_id="510300",
        spot_price=4.0,
        legs=[],
        net_premium=0.0,
        score=score,
        score_breakdown={"execution_quality": 0.8},
        metadata={
            "strategy_family": "iron"
            if strategy_type in ("iron_condor", "iron_fly")
            else "vertical",
            "greeks_report": {"risk_flags": []},
        },
    )


class NeutralThetaRangeSelectionTests(unittest.TestCase):
    def test_rule_parser_detects_strict_range_theta_income(self):
        intent = _rule_parse_text_to_intent(
            "我中性，想收 theta，认为上下都有边界",
            underlying_id="510300",
        )

        self.assertEqual(intent.market_view, "neutral")
        self.assertTrue(intent.require_positive_theta)
        self.assertEqual(intent.range_bias, "strict_range")
        self.assertEqual(intent.greeks_preference["theta"]["sign"], "positive")

    def test_strict_range_theta_income_prefers_iron_primary(self):
        ranked = [
            make_strategy("bear_call_spread", 0.95),
            make_strategy("bull_put_spread", 0.93),
            make_strategy("iron_condor", 0.80),
            make_strategy("iron_fly", 0.78),
        ]

        payload = select_recommendations(ranked, intent=make_intent())

        primary_types = [
            item["strategy_type"]
            for item in payload["primary_recommendations"]
        ]
        secondary_types = [
            item["strategy_type"]
            for item in payload["secondary_recommendations"]
        ]

        self.assertEqual(primary_types, ["iron_condor"])
        self.assertIn("bear_call_spread", secondary_types)
        self.assertIn("bull_put_spread", secondary_types)
        self.assertIn(
            "strict_range_income_prefers_iron",
            ranked[2].metadata.get("reason_codes", []),
        )
        for item in payload["secondary_recommendations"]:
            if item["strategy_type"] in ("bear_call_spread", "bull_put_spread"):
                self.assertEqual(
                    item.get("downgrade_reason"),
                    "single_side_credit_downgraded_for_strict_range",
                )

    def test_parsed_strict_range_theta_income_prefers_iron_primary(self):
        intent = _rule_parse_text_to_intent(
            "我中性，想收 theta，认为上下都有边界",
            underlying_id="510300",
        )
        ranked = [
            make_strategy("bear_call_spread", 0.95),
            make_strategy("bull_put_spread", 0.93),
            make_strategy("iron_condor", 0.80),
            make_strategy("iron_fly", 0.78),
        ]

        payload = select_recommendations(ranked, intent=intent)

        primary_types = [
            item["strategy_type"]
            for item in payload["primary_recommendations"]
        ]

        self.assertIn(primary_types[0], ("iron_condor", "iron_fly"))
        self.assertNotIn("bear_call_spread", primary_types)
        self.assertNotIn("bull_put_spread", primary_types)

    def test_weak_bearish_range_is_not_forced_to_iron(self):
        ranked = [
            make_strategy("bear_call_spread", 0.95),
            make_strategy("iron_condor", 0.80),
        ]

        payload = select_recommendations(
            ranked,
            intent=make_intent(range_bias="weak_bearish_range"),
        )

        primary_types = [
            item["strategy_type"]
            for item in payload["primary_recommendations"]
        ]

        self.assertEqual(primary_types, ["bear_call_spread"])


if __name__ == "__main__":
    unittest.main()
