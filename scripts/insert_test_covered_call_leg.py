"""
TEST ONLY: insert one local validation covered-call short CALL leg.

This script is for validating /monitor/underlying/510300 covered_call_coverage.
It is not a trading workflow and should not be used for real trade booking.
It is idempotent for OPEN position_legs with tag='test_covered_call'.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import engine  # noqa: E402


UNDERLYING_ID = "510300"
TEST_TAG = "test_covered_call"
GROUP_ID = "test_cc_510300"
QUANTITY = 2


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _mid(row: dict[str, Any]) -> Optional[float]:
    bid = _safe_float(row.get("bid_price1"))
    ask = _safe_float(row.get("ask_price1"))
    if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    return _safe_float(row.get("option_market_price"))


def main() -> int:
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT
                    leg_id, contract_id, option_type, strike, expiry_date, side,
                    quantity, avg_entry_price, status, tag
                FROM position_legs
                WHERE underlying_id = :underlying_id
                  AND tag = :tag
                  AND status = 'OPEN'
                ORDER BY leg_id DESC
                LIMIT 1
                """
            ),
            {"underlying_id": UNDERLYING_ID, "tag": TEST_TAG},
        ).mappings().first()
        if existing:
            print("Existing TEST ONLY covered-call leg found; no insert performed.")
            print(
                "contract_id={contract_id} strike={strike} expiry_date={expiry_date} "
                "quantity={quantity} avg_entry_price={avg_entry_price} leg_id={leg_id}".format(**dict(existing))
            )
            return 0

        candidate = conn.execute(
            text(
                """
                WITH latest AS (
                    SELECT MAX(fetch_time) AS max_fetch_time
                    FROM option_factor_snapshots
                    WHERE underlying_id = :underlying_id
                ),
                factor_candidates AS (
                    SELECT
                        contract_id,
                        underlying_id,
                        option_type,
                        strike,
                        expiry_date,
                        dte_calendar,
                        option_market_price,
                        delta,
                        fetch_time
                    FROM option_factor_snapshots
                    WHERE underlying_id = :underlying_id
                      AND fetch_time = (SELECT max_fetch_time FROM latest)
                      AND option_type IN ('CALL', 'C')
                      AND dte_calendar BETWEEN 20 AND 60
                      AND delta BETWEEN 0.20 AND 0.35
                ),
                quote_latest AS (
                    SELECT MAX(fetch_time) AS max_fetch_time
                    FROM option_quote_snapshots
                    WHERE underlying_id = :underlying_id
                ),
                quote_rows AS (
                    SELECT contract_id, bid_price1, ask_price1
                    FROM option_quote_snapshots
                    WHERE underlying_id = :underlying_id
                      AND fetch_time = (SELECT max_fetch_time FROM quote_latest)
                )
                SELECT
                    f.contract_id,
                    f.option_type,
                    f.strike,
                    f.expiry_date,
                    f.dte_calendar,
                    f.option_market_price,
                    f.delta,
                    f.fetch_time,
                    q.bid_price1,
                    q.ask_price1,
                    CASE
                        WHEN q.bid_price1 IS NOT NULL
                         AND q.ask_price1 IS NOT NULL
                         AND q.bid_price1 > 0
                         AND q.ask_price1 > 0
                         AND q.ask_price1 >= q.bid_price1
                        THEN 1 ELSE 0
                    END AS has_valid_quote
                FROM factor_candidates f
                LEFT JOIN quote_rows q ON q.contract_id = f.contract_id
                ORDER BY
                    has_valid_quote DESC,
                    ABS(f.delta - 0.275) ASC,
                    f.dte_calendar ASC,
                    f.strike ASC
                LIMIT 1
                """
            ),
            {"underlying_id": UNDERLYING_ID},
        ).mappings().first()

        if not candidate:
            print("No suitable 510300 CALL candidate found for TEST ONLY covered-call leg.")
            return 1

        row = dict(candidate)
        entry_price = _mid(row)
        if entry_price is None or entry_price <= 0:
            print(f"Candidate has no usable mid/bid price: contract_id={row.get('contract_id')}")
            return 1

        inserted = conn.execute(
            text(
                """
                INSERT INTO position_legs (
                    underlying_id, contract_id, option_type, strike, expiry_date, side,
                    quantity, avg_entry_price, strategy_bucket, group_id, tag,
                    include_in_portfolio_greeks, status, note
                )
                VALUES (
                    :underlying_id, :contract_id, 'CALL', :strike, :expiry_date, 'SELL',
                    :quantity, :avg_entry_price, 'covered_call', :group_id, :tag,
                    TRUE, 'OPEN', :note
                )
                RETURNING leg_id
                """
            ),
            {
                "underlying_id": UNDERLYING_ID,
                "contract_id": str(row["contract_id"]),
                "strike": row["strike"],
                "expiry_date": row["expiry_date"],
                "quantity": QUANTITY,
                "avg_entry_price": entry_price,
                "group_id": GROUP_ID,
                "tag": TEST_TAG,
                "note": "TEST ONLY: local covered-call monitor validation leg",
            },
        ).mappings().one()

    print("Inserted TEST ONLY covered-call leg.")
    print(
        "leg_id={leg_id} contract_id={contract_id} strike={strike} expiry_date={expiry_date} "
        "delta={delta} mid={mid}".format(
            leg_id=inserted["leg_id"],
            contract_id=row["contract_id"],
            strike=row["strike"],
            expiry_date=row["expiry_date"],
            delta=row["delta"],
            mid=round(float(entry_price), 6),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
