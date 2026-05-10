import unittest

from app.data.trading_calendar import normalize_non_trading_date_tokens


class TradingCalendarTests(unittest.TestCase):
    def test_normalize_accepts_weekdays_and_skips_weekends_duplicates_invalid(self):
        result = normalize_non_trading_date_tokens(
            [
                "2026-05-01",  # Friday
                "2026-05-02",  # Saturday
                "2026-05-01",
                "bad-date",
            ]
        )

        self.assertEqual(result["accepted_dates"], ["2026-05-01"])
        self.assertIn({"date": "2026-05-02", "reason": "weekend_not_needed"}, result["skipped"])
        self.assertIn({"date": "2026-05-01", "reason": "duplicate"}, result["skipped"])
        self.assertIn({"date": "bad-date", "reason": "invalid_date"}, result["skipped"])


if __name__ == "__main__":
    unittest.main()
