import unittest
from types import SimpleNamespace

from app.models.schemas import IntentSpec
from app.strategy.advisor_service_v2 import _rule_parse_text_to_intent
from app.strategy.recommendation_selector import select_recommendations


def make_leg(bid=1.0, ask=1.02, mid=1.01):
    return SimpleNamespace(bid=bid, ask=ask, mid=mid)


def make_strategy(strategy_type, score):
    return SimpleNamespace(
        strategy_type=strategy_type,
        underlying_id="510300",
        score=score,
        legs=[make_leg()],
        score_breakdown={"execution_quality": 0.8},
        execution_guidance={},
        metadata={
            "strategy_family": "naked_short"
            if strategy_type in ("naked_call", "naked_put")
            else "vertical",
            "greeks_report": {"risk_flags": []},
        },
    )


def make_intent(horizon_views):
    return IntentSpec(
        underlying_id="510300",
        market_view="bearish",
        vol_view="none",
        risk_preference="medium",
        defined_risk_only=False,
        prefer_multi_leg=False,
        dte_min=20,
        dte_max=45,
        max_rel_spread=0.03,
        min_quote_size=1,
        raw_text="horizon test",
        horizon_views=horizon_views,
    )


def strategy_types(items):
    return [item["strategy_type"] for item in items]


class HorizonViewsSelectionTests(unittest.TestCase):
    def test_rule_parser_detects_short_upside_medium_bearish(self):
        intent = _rule_parse_text_to_intent(
            "短期可能上冲，但中期偏空",
            underlying_id="510300",
        )

        self.assertEqual(intent.market_view, "bearish")
        self.assertEqual((intent.dte_min, intent.dte_max), (30, 60))
        self.assertEqual(intent.horizon_views["short_term"]["direction"], "bullish")
        self.assertEqual(intent.horizon_views["medium_term"]["direction"], "bearish")

    def test_rule_parser_detects_medium_bearish_long_bullish(self):
        intent = _rule_parse_text_to_intent(
            "中期偏空，但长期看好",
            underlying_id="510300",
        )

        self.assertEqual(intent.market_view, "bearish")
        self.assertEqual((intent.dte_min, intent.dte_max), (30, 60))
        self.assertEqual(intent.horizon_views["medium_term"]["direction"], "bearish")
        self.assertEqual(intent.horizon_views["long_term"]["direction"], "bullish")

    def test_rule_parser_detects_three_horizons(self):
        intent = _rule_parse_text_to_intent(
            "短期上冲、中期偏空、长期看好",
            underlying_id="510300",
        )

        self.assertEqual(intent.horizon_views["short_term"]["direction"], "upside_risk")
        self.assertEqual(intent.horizon_views["medium_term"]["direction"], "bearish")
        self.assertEqual(intent.horizon_views["long_term"]["direction"], "bullish")

    def test_short_term_upside_risk_blocks_naked_call_primary(self):
        intent = make_intent(
            {
                "short_term": {"direction": "bullish", "direction_strength": 0.7},
                "medium_term": {"direction": "bearish", "direction_strength": 0.6},
            }
        )
        ranked = [
            make_strategy("naked_call", 0.95),
            make_strategy("bear_call_spread", 0.90),
        ]

        payload = select_recommendations(ranked, intent=intent)

        self.assertEqual(strategy_types(payload["primary_recommendations"]), ["bear_call_spread"])
        self.assertNotIn("naked_call", strategy_types(payload["primary_recommendations"]))
        self.assertEqual(
            payload["secondary_recommendations"][0]["reason_code"],
            "short_term_upside_risk_blocks_naked_call",
        )
        self.assertIn(
            "horizon_conflict_detected",
            payload["secondary_recommendations"][0]["reason_codes"],
        )

    def test_long_term_opposite_view_blocks_naked_call_primary(self):
        intent = make_intent(
            {
                "medium_term": {"direction": "bearish", "direction_strength": 0.65},
                "long_term": {"direction": "bullish", "direction_strength": 0.65},
            }
        )
        ranked = [
            make_strategy("naked_call", 0.95),
            make_strategy("bear_put_spread", 0.90),
        ]

        payload = select_recommendations(ranked, intent=intent)

        self.assertEqual(strategy_types(payload["primary_recommendations"]), ["bear_put_spread"])
        self.assertNotIn("naked_call", strategy_types(payload["primary_recommendations"]))
        self.assertEqual(
            payload["secondary_recommendations"][0]["reason_code"],
            "long_term_opposite_view_prefers_defined_risk",
        )

    def test_three_horizon_conflict_blocks_naked_call_primary(self):
        intent = make_intent(
            {
                "short_term": {"direction": "upside_risk", "direction_strength": 0.75},
                "medium_term": {"direction": "bearish", "direction_strength": 0.65},
                "long_term": {"direction": "bullish", "direction_strength": 0.65},
            }
        )
        ranked = [
            make_strategy("naked_call", 0.95),
            make_strategy("bear_call_spread", 0.90),
        ]

        payload = select_recommendations(ranked, intent=intent)

        self.assertEqual(strategy_types(payload["primary_recommendations"]), ["bear_call_spread"])
        self.assertNotIn("naked_call", strategy_types(payload["primary_recommendations"]))
        self.assertEqual(
            payload["secondary_recommendations"][0]["reason_code"],
            "short_term_upside_risk_blocks_naked_call",
        )


if __name__ == "__main__":
    unittest.main()
