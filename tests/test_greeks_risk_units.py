import unittest

from app.models.schemas import ResolvedLeg, ResolvedStrategy
from app.strategy.greeks_monitor import (
    compute_strategy_net_greeks,
    compute_strategy_risk_greeks,
)
from app.strategy.position_monitor import _risk_greeks_from_raw


def make_leg(action, delta, gamma, theta, vega, quantity=1):
    return ResolvedLeg(
        contract_id=f"{action}-{delta}",
        action=action,
        option_type="CALL",
        expiry_date="2026-06-01",
        strike=4.0,
        bid=0.1,
        ask=0.11,
        mid=0.105,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        quantity=quantity,
    )


class GreeksRiskUnitTests(unittest.TestCase):
    def test_risk_greeks_are_added_without_overwriting_raw_greeks(self):
        strategy = ResolvedStrategy(
            strategy_type="bear_call_spread",
            underlying_id="510300",
            spot_price=4.0,
            legs=[
                make_leg("SELL", 0.30, 0.20, -0.0010, 0.0200),
                make_leg("BUY", 0.15, 0.10, -0.0005, 0.0100),
            ],
            net_premium=0.02,
            net_credit=0.02,
        )

        raw = compute_strategy_net_greeks(strategy)
        risk = compute_strategy_risk_greeks(strategy, raw)

        self.assertEqual(raw["net_delta"], -0.15)
        self.assertEqual(raw["net_theta"], 0.0005)
        self.assertEqual(raw["net_vega"], -0.01)
        self.assertEqual(risk["delta_share_equiv"], -1500.0)
        self.assertEqual(risk["delta_rmb_per_1pct"], -60.0)
        self.assertEqual(risk["theta_rmb_per_day"], 5.0)
        self.assertEqual(risk["vega_rmb_per_1vol"], -1.0)
        self.assertEqual(risk["gamma_rmb_per_1pct_move"], -0.8)
        self.assertTrue(risk["gamma_rmb_per_1pct_move_approximate"])

    def test_buy_one_contract_delta_share_equiv(self):
        strategy = ResolvedStrategy(
            strategy_type="long_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("BUY", 0.50, 0.0, 0.0, 0.0)],
            net_premium=-0.10,
            net_debit=0.10,
        )

        raw = compute_strategy_net_greeks(strategy)
        risk = compute_strategy_risk_greeks(strategy, raw)

        self.assertEqual(raw["net_delta"], 0.5)
        self.assertEqual(risk["delta_share_equiv"], 5000.0)
        self.assertEqual(risk["delta_rmb_per_1pct"], 200.0)

    def test_sell_two_contracts_delta_share_equiv(self):
        strategy = ResolvedStrategy(
            strategy_type="naked_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("SELL", 0.30, 0.0, 0.0, 0.0, quantity=2)],
            net_premium=0.10,
            net_credit=0.10,
        )

        raw = compute_strategy_net_greeks(strategy)
        risk = compute_strategy_risk_greeks(strategy, raw)

        self.assertEqual(raw["net_delta"], -0.6)
        self.assertEqual(risk["delta_share_equiv"], -6000.0)
        self.assertEqual(risk["delta_rmb_per_1pct"], -240.0)

    def test_theta_rmb_per_day_respects_buy_sell_direction(self):
        buy_strategy = ResolvedStrategy(
            strategy_type="long_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("BUY", 0.0, 0.0, -0.001, 0.0)],
            net_premium=-0.10,
            net_debit=0.10,
        )
        sell_strategy = ResolvedStrategy(
            strategy_type="naked_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("SELL", 0.0, 0.0, -0.001, 0.0)],
            net_premium=0.10,
            net_credit=0.10,
        )

        buy_risk = compute_strategy_risk_greeks(buy_strategy)
        sell_risk = compute_strategy_risk_greeks(sell_strategy)

        self.assertEqual(buy_risk["theta_rmb_per_day"], -10.0)
        self.assertEqual(sell_risk["theta_rmb_per_day"], 10.0)

    def test_vega_rmb_per_1vol_respects_buy_sell_direction(self):
        buy_strategy = ResolvedStrategy(
            strategy_type="long_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("BUY", 0.0, 0.0, 0.0, 0.02)],
            net_premium=-0.10,
            net_debit=0.10,
        )
        sell_strategy = ResolvedStrategy(
            strategy_type="naked_call",
            underlying_id="510300",
            spot_price=4.0,
            legs=[make_leg("SELL", 0.0, 0.0, 0.0, 0.02)],
            net_premium=0.10,
            net_credit=0.10,
        )

        buy_risk = compute_strategy_risk_greeks(buy_strategy)
        sell_risk = compute_strategy_risk_greeks(sell_strategy)

        self.assertEqual(buy_risk["vega_rmb_per_1vol"], 2.0)
        self.assertEqual(sell_risk["vega_rmb_per_1vol"], -2.0)

    def test_position_monitor_risk_unit_helper_matches_strategy_units(self):
        risk = _risk_greeks_from_raw(
            net_delta=-0.6,
            net_gamma=0.0,
            net_theta=0.001,
            net_vega=-0.02,
            spot=4.0,
        )

        self.assertEqual(risk["delta_share_equiv"], -6000.0)
        self.assertEqual(risk["delta_rmb_per_1pct"], -240.0)
        self.assertEqual(risk["theta_rmb_per_day"], 10.0)
        self.assertEqual(risk["vega_rmb_per_1vol"], -2.0)


if __name__ == "__main__":
    unittest.main()
