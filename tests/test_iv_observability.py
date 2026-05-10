import unittest
from datetime import datetime
from unittest.mock import patch

from app.strategy.iv_percentile import (
    _build_percentile_payload,
    _choose_representative_iv,
    build_iv_percentile_report,
)


class IvObservabilityTests(unittest.TestCase):
    def test_report_exposes_latest_weekday_snapshot_and_dimension_percentiles(self):
        latest = datetime(2026, 4, 30, 15, 5, 1)  # Thursday

        with patch(
            "app.strategy.iv_percentile.get_current_representative_ivs",
            return_value={"atm": 0.135, "call": 0.13, "put": 0.14},
        ), patch(
            "app.strategy.iv_percentile.fetch_historical_representative_ivs",
            return_value={
                "atm": [0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20, 0.21],
                "call": [0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20, 0.21],
                "put": [0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20, 0.21],
            },
        ), patch(
            "app.strategy.iv_percentile._fetch_latest_snapshot_rows",
            return_value=[
                {"fetch_time": latest, "implied_vol": 0.135, "delta": 0.5, "dte_calendar": 20},
            ],
        ):
            report = build_iv_percentile_report(engine=object(), underlying_id="510300")

        self.assertEqual(report["dte_bucket"], "near")
        self.assertEqual(report["latest_fetch_time"], "2026-04-30T15:05:01")
        self.assertEqual(report["latest_trade_date"], "2026-04-30")
        self.assertEqual(latest.isoweekday(), 4)
        self.assertEqual(report["sample_method"], "weekday_latest_snapshot_per_date_dte_bucket_then_delta_target")
        self.assertEqual(report["history_days"], 10)
        self.assertIn("atm_percentile", report)
        self.assertIn("call_percentile", report)
        self.assertIn("put_percentile", report)

    def test_current_representative_iv_prefers_target_dte_before_delta(self):
        rows = [
            {"option_type": "CALL", "implied_vol": 0.10, "delta": 0.40, "dte_calendar": 12},
            {"option_type": "CALL", "implied_vol": 0.20, "delta": 0.35, "dte_calendar": 23},
        ]

        iv = _choose_representative_iv(rows, "CALL", 0.40, target_dte=22.5)

        self.assertEqual(iv, 0.20)

    def test_history_weight_ramps_from_15pct_at_10_days_to_70pct_at_180_days(self):
        payload_10 = _build_percentile_payload(
            current_iv=0.16,
            underlying_id="510300",
            historical_ivs=[0.15] * 10,
            dimension="atm",
        )
        payload_24 = _build_percentile_payload(
            current_iv=0.16,
            underlying_id="510300",
            historical_ivs=[0.15] * 24,
            dimension="atm",
        )
        payload_180 = _build_percentile_payload(
            current_iv=0.16,
            underlying_id="510300",
            historical_ivs=[0.15] * 180,
            dimension="atm",
        )

        self.assertEqual(payload_10["hist_weight"], 0.15)
        self.assertEqual(payload_24["hist_weight"], 0.2)
        self.assertEqual(payload_180["hist_weight"], 0.7)


if __name__ == "__main__":
    unittest.main()
