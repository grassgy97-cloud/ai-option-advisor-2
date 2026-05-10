import unittest

from app.strategy.position_monitor import (
    _build_covered_call_coverage,
    _build_underlying_monitor_v2,
    _leg_greek_contribution,
)


def make_monitored_leg(
    *,
    leg_id=1,
    side="SELL",
    option_type="CALL",
    strike=100.0,
    expiry_date="2026-06-01",
    dte=20,
    quantity=1,
    group_id="g1",
    strategy_bucket="iron_condor",
    delta=0.0,
    gamma=0.0,
    theta=0.0,
    vega=0.0,
):
    return {
        "leg_id": leg_id,
        "contract_id": f"OPT-{leg_id}",
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "expiry_date": expiry_date,
        "quantity": quantity,
        "pnl_estimate_rmb": 0.0,
        "delta_contribution": _leg_greek_contribution(side, delta, quantity),
        "gamma_contribution": _leg_greek_contribution(side, gamma, quantity),
        "theta_contribution": _leg_greek_contribution(side, theta, quantity),
        "vega_contribution": _leg_greek_contribution(side, vega, quantity),
        "dte": dte,
        "strategy_bucket": strategy_bucket,
        "group_id": group_id,
    }


def build_report(legs, spot=100.0):
    net_delta = sum(float(leg.get("delta_contribution") or 0.0) for leg in legs)
    net_gamma = sum(float(leg.get("gamma_contribution") or 0.0) for leg in legs)
    net_theta = sum(float(leg.get("theta_contribution") or 0.0) for leg in legs)
    net_vega = sum(float(leg.get("vega_contribution") or 0.0) for leg in legs)
    return _build_underlying_monitor_v2(
        spot=spot,
        monitored_legs=legs,
        total_pnl=0.0,
        pnl_pct=None,
        net_delta=net_delta,
        net_gamma=net_gamma,
        net_theta=net_theta,
        net_vega=net_vega,
    )


def make_underlying_risk_leg(shares, spot=100.0):
    return {
        "leg_id": None,
        "contract_id": "UNDERLYING_SHARES",
        "side": "BUY",
        "option_type": "UNDERLYING",
        "strike": None,
        "expiry_date": "underlying",
        "quantity": shares,
        "pnl_estimate_rmb": 0.0,
        "delta_contribution": shares / 10000,
        "gamma_contribution": 0.0,
        "theta_contribution": 0.0,
        "vega_contribution": 0.0,
        "dte": None,
        "strategy_bucket": "underlying_position",
        "group_id": "underlying_position",
        "spot": spot,
    }


class PositionMonitorV2Tests(unittest.TestCase):
    def test_short_strike_distance_within_one_percent_is_alert(self):
        report = build_report([
            make_monitored_leg(leg_id=1, strike=100.5),
        ])

        self.assertEqual(report["short_strike_risk_map"][0]["status"], "alert")
        self.assertEqual(
            report["short_strike_risk_map"][0]["reason_code"],
            "short_strike_distance_alert",
        )

    def test_short_strike_distance_within_three_percent_is_watch(self):
        report = build_report([
            make_monitored_leg(leg_id=1, strike=102.0),
        ])

        self.assertEqual(report["short_strike_risk_map"][0]["status"], "watch")
        self.assertEqual(
            report["short_strike_risk_map"][0]["reason_code"],
            "short_strike_distance_watch",
        )

    def test_buy_sell_greeks_aggregate_with_direction(self):
        legs = [
            make_monitored_leg(leg_id=1, side="BUY", delta=0.5, theta=-0.001, vega=0.02),
            make_monitored_leg(leg_id=2, side="SELL", delta=0.3, theta=-0.001, vega=0.02, quantity=2),
        ]

        report = build_report(legs)
        summary = report["portfolio_risk_summary"]

        self.assertAlmostEqual(summary["net_delta"], -0.1)
        self.assertAlmostEqual(summary["net_theta"], 0.001)
        self.assertAlmostEqual(summary["net_vega"], -0.02)
        self.assertEqual(summary["total_risk_greeks"]["delta_share_equiv"], -1000.0)
        self.assertEqual(summary["total_risk_greeks"]["theta_rmb_per_day"], 10.0)
        self.assertEqual(summary["total_risk_greeks"]["vega_rmb_per_1vol"], -2.0)

    def test_group_id_aggregation(self):
        legs = [
            make_monitored_leg(leg_id=1, group_id="g1", delta=0.4),
            make_monitored_leg(leg_id=2, group_id="g1", side="BUY", delta=0.1),
            make_monitored_leg(leg_id=3, group_id="g2", delta=0.2),
        ]

        report = build_report(legs)
        groups = {item["group_id"]: item for item in report["group_risk_breakdown"]}

        self.assertEqual(groups["g1"]["leg_count"], 2)
        self.assertAlmostEqual(groups["g1"]["net_delta"], -0.3)
        self.assertEqual(groups["g2"]["leg_count"], 1)
        self.assertAlmostEqual(groups["g2"]["net_delta"], -0.2)

    def test_expiry_aggregation(self):
        legs = [
            make_monitored_leg(leg_id=1, expiry_date="2026-06-01", dte=10, delta=0.2),
            make_monitored_leg(leg_id=2, expiry_date="2026-06-01", dte=12, delta=0.3),
            make_monitored_leg(leg_id=3, expiry_date="2026-07-01", dte=40, delta=0.1),
        ]

        report = build_report(legs)
        expiries = {item["expiry_date"]: item for item in report["expiry_risk_breakdown"]}

        self.assertEqual(expiries["2026-06-01"]["leg_count"], 2)
        self.assertEqual(expiries["2026-06-01"]["min_dte"], 10)
        self.assertAlmostEqual(expiries["2026-06-01"]["net_delta"], -0.5)
        self.assertEqual(expiries["2026-07-01"]["leg_count"], 1)

    def test_near_expiry_short_gamma_risk_above_far_expiry(self):
        legs = [
            make_monitored_leg(
                leg_id=1,
                expiry_date="2026-06-01",
                dte=3,
                gamma=1.2,
            ),
            make_monitored_leg(
                leg_id=2,
                expiry_date="2026-07-01",
                dte=30,
                gamma=1.2,
            ),
        ]

        report = build_report(legs)
        expiries = {item["expiry_date"]: item for item in report["expiry_risk_breakdown"]}

        self.assertGreater(
            expiries["2026-06-01"]["expiry_risk_score"],
            expiries["2026-07-01"]["expiry_risk_score"],
        )
        self.assertEqual(expiries["2026-06-01"]["expiry_risk_status"], "alert")

    def test_no_short_leg_does_not_emit_short_strike_risk(self):
        report = build_report([
            make_monitored_leg(leg_id=1, side="BUY", strike=100.5, delta=0.5),
        ])

        self.assertEqual(report["short_strike_risk_map"], [])
        self.assertIsNone(report["portfolio_risk_summary"]["distance_to_short_strike"])

    def test_covered_call_ratio_full_coverage(self):
        legs = [
            make_monitored_leg(leg_id=1, side="SELL", option_type="CALL", quantity=2),
        ]

        coverage = _build_covered_call_coverage(legs, underlying_shares=20000)

        self.assertEqual(coverage["covered_ratio"], 1.0)
        self.assertEqual(coverage["uncovered_short_call_contracts"], 0.0)
        self.assertEqual(coverage["covered_call_risk_status"], "covered")
        self.assertEqual(coverage["rows"][0]["coverage_status"], "covered")

    def test_covered_call_ratio_partial_coverage(self):
        legs = [
            make_monitored_leg(leg_id=1, side="SELL", option_type="CALL", quantity=2),
        ]

        coverage = _build_covered_call_coverage(legs, underlying_shares=10000)

        self.assertEqual(coverage["covered_ratio"], 0.5)
        self.assertEqual(coverage["uncovered_short_call_contracts"], 1.0)
        self.assertEqual(coverage["covered_call_risk_status"], "partially_covered")
        self.assertEqual(coverage["rows"][0]["coverage_status"], "partially_covered")

    def test_covered_call_ratio_uncovered(self):
        legs = [
            make_monitored_leg(leg_id=1, side="SELL", option_type="CALL", quantity=1),
        ]

        coverage = _build_covered_call_coverage(legs, underlying_shares=0)

        self.assertEqual(coverage["covered_ratio"], 0.0)
        self.assertEqual(coverage["uncovered_short_call_contracts"], 1.0)
        self.assertEqual(coverage["covered_call_risk_status"], "uncovered")
        self.assertEqual(coverage["rows"][0]["coverage_status"], "uncovered")

    def test_covered_call_near_strike_emits_assignment_roll_risk(self):
        option_legs = [
            make_monitored_leg(
                leg_id=1,
                side="SELL",
                option_type="CALL",
                strike=100.5,
                quantity=1,
                strategy_bucket="covered_call",
                group_id="cc",
                delta=0.4,
                gamma=0.5,
                theta=-0.002,
                vega=0.02,
            ),
        ]
        coverage = _build_covered_call_coverage(option_legs, underlying_shares=10000)
        legs = option_legs + [make_underlying_risk_leg(10000)]
        net_delta = sum(float(leg.get("delta_contribution") or 0.0) for leg in legs)
        net_gamma = sum(float(leg.get("gamma_contribution") or 0.0) for leg in legs)
        net_theta = sum(float(leg.get("theta_contribution") or 0.0) for leg in legs)
        net_vega = sum(float(leg.get("vega_contribution") or 0.0) for leg in legs)

        report = _build_underlying_monitor_v2(
            spot=100.0,
            monitored_legs=legs,
            total_pnl=0.0,
            pnl_pct=None,
            net_delta=net_delta,
            net_gamma=net_gamma,
            net_theta=net_theta,
            net_vega=net_vega,
            covered_call_coverage=coverage,
        )

        short_risk = report["short_strike_risk_map"][0]
        reason_codes = {item["reason_code"] for item in report["management_suggestions"]}

        self.assertEqual(short_risk["coverage_status"], "covered")
        self.assertTrue(short_risk["assignment_risk"])
        self.assertIn("covered_call_assignment_or_roll_watch", reason_codes)
        self.assertNotIn("naked", str(report).lower())

    def test_underlying_shares_delta_is_in_portfolio_risk_summary(self):
        option_legs = [
            make_monitored_leg(
                leg_id=1,
                side="SELL",
                option_type="CALL",
                quantity=2,
                delta=0.4,
            ),
        ]
        legs = option_legs + [make_underlying_risk_leg(20000)]
        net_delta = sum(float(leg.get("delta_contribution") or 0.0) for leg in legs)
        coverage = _build_covered_call_coverage(option_legs, underlying_shares=20000)

        report = _build_underlying_monitor_v2(
            spot=100.0,
            monitored_legs=legs,
            total_pnl=0.0,
            pnl_pct=None,
            net_delta=net_delta,
            net_gamma=0.0,
            net_theta=0.0,
            net_vega=0.0,
            covered_call_coverage=coverage,
        )

        self.assertAlmostEqual(report["portfolio_risk_summary"]["net_delta"], 1.2)
        self.assertEqual(
            report["portfolio_risk_summary"]["total_risk_greeks"]["delta_share_equiv"],
            12000.0,
        )


if __name__ == "__main__":
    unittest.main()
