import unittest
from types import SimpleNamespace

from app.strategy.recommendation_selector import select_recommendations


def make_leg(bid=1.0, ask=1.02, mid=1.01):
    return SimpleNamespace(bid=bid, ask=ask, mid=mid)


def make_strategy(strategy_type, score, execution_quality=0.8, legs=None, execution_guidance=None):
    return SimpleNamespace(
        strategy_type=strategy_type,
        underlying_id="510300",
        score=score,
        legs=legs if legs is not None else [make_leg()],
        score_breakdown={"execution_quality": execution_quality},
        execution_guidance=execution_guidance or {},
        metadata={
            "strategy_family": "vertical",
            "greeks_report": {"risk_flags": []},
        },
    )


def strategy_types(items):
    return [item["strategy_type"] for item in items]


class ExecutionVetoTests(unittest.TestCase):
    def test_low_execution_quality_cannot_be_primary(self):
        ranked = [
            make_strategy("bear_call_spread", 0.95, execution_quality=0.55),
            make_strategy("bear_put_spread", 0.90, execution_quality=0.80),
        ]

        payload = select_recommendations(ranked)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["bear_put_spread"],
        )
        self.assertEqual(
            payload["secondary_recommendations"][0]["strategy_type"],
            "bear_call_spread",
        )
        self.assertEqual(
            payload["secondary_recommendations"][0]["downgrade_reason"],
            "execution_quality_below_primary_threshold",
        )
        self.assertTrue(payload["secondary_recommendations"][0]["execution_veto"])
        self.assertEqual(
            payload["secondary_recommendations"][0]["reason_code"],
            "execution_quality_too_low",
        )
        self.assertEqual(ranked[0].score, 0.95)

    def test_wide_relative_spread_is_watchlisted(self):
        ranked = [
            make_strategy(
                "bear_call_spread",
                0.95,
                execution_quality=0.90,
                legs=[make_leg(bid=1.0, ask=1.20, mid=1.10)],
            ),
            make_strategy("bear_put_spread", 0.90, execution_quality=0.80),
        ]

        payload = select_recommendations(ranked)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["bear_put_spread"],
        )
        self.assertEqual(payload["watchlist"][0]["strategy_type"], "bear_call_spread")
        self.assertEqual(
            payload["watchlist"][0]["downgrade_reason"],
            "relative_spread_too_wide",
        )
        self.assertTrue(payload["watchlist"][0]["execution_veto"])
        self.assertEqual(payload["watchlist"][0]["reason_code"], "spread_too_wide")
        self.assertEqual(ranked[0].score, 0.95)

    def test_relative_spread_uses_intent_max_rel_spread(self):
        ranked = [
            make_strategy(
                "bear_call_spread",
                0.95,
                execution_quality=0.90,
                legs=[make_leg(bid=1.0, ask=1.04, mid=1.02)],
            ),
            make_strategy("bear_put_spread", 0.90, execution_quality=0.80),
        ]
        intent = SimpleNamespace(max_rel_spread=0.03)

        payload = select_recommendations(ranked, intent=intent)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["bear_put_spread"],
        )
        self.assertEqual(payload["watchlist"][0]["strategy_type"], "bear_call_spread")
        self.assertEqual(payload["watchlist"][0]["reason_code"], "spread_too_wide")
        self.assertEqual(ranked[0].score, 0.95)

    def test_missing_quote_is_watchlisted(self):
        ranked = [
            make_strategy(
                "bull_put_spread",
                0.95,
                execution_quality=0.90,
                legs=[make_leg(bid=None, ask=1.02, mid=1.01)],
            ),
            make_strategy("iron_condor", 0.90, execution_quality=0.80),
        ]

        payload = select_recommendations(ranked)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["iron_condor"],
        )
        self.assertEqual(payload["watchlist"][0]["strategy_type"], "bull_put_spread")
        self.assertEqual(payload["watchlist"][0]["downgrade_reason"], "quote_missing")
        self.assertEqual(payload["watchlist"][0]["reason_code"], "quote_invalid")
        self.assertEqual(ranked[0].score, 0.95)

    def test_execution_guidance_do_not_chase_is_watchlisted(self):
        ranked = [
            make_strategy(
                "bear_call_spread",
                0.95,
                execution_quality=0.90,
                execution_guidance={"execution_status": "do_not_chase"},
            ),
            make_strategy("bear_put_spread", 0.90, execution_quality=0.80),
        ]

        payload = select_recommendations(ranked)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["bear_put_spread"],
        )
        self.assertEqual(payload["watchlist"][0]["strategy_type"], "bear_call_spread")
        self.assertEqual(payload["watchlist"][0]["reason_code"], "do_not_chase")
        self.assertEqual(ranked[0].score, 0.95)

    def test_good_execution_high_score_can_remain_primary(self):
        ranked = [
            make_strategy(
                "bear_call_spread",
                0.95,
                execution_quality=0.90,
                legs=[make_leg(bid=1.0, ask=1.02, mid=1.01)],
            ),
            make_strategy("bear_put_spread", 0.90, execution_quality=0.80),
        ]
        intent = SimpleNamespace(max_rel_spread=0.03)

        payload = select_recommendations(ranked, intent=intent)

        self.assertEqual(
            strategy_types(payload["primary_recommendations"]),
            ["bear_call_spread"],
        )
        self.assertTrue(payload["primary_recommendations"][0]["execution_checked"])
        self.assertFalse(payload["primary_recommendations"][0]["execution_veto"])
        self.assertEqual(payload["primary_recommendations"][0]["execution_quality"], 0.9)
        self.assertTrue(payload["primary_recommendations"][0]["spread_check_passed"])
        self.assertFalse(ranked[0].metadata.get("execution_veto", False))
        self.assertEqual(ranked[0].score, 0.95)


if __name__ == "__main__":
    unittest.main()
