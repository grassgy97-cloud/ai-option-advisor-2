from sqlalchemy import text
from app.core.db import SessionLocal
from app.pricing.iv_solver import solve_iv, calc_dte, calc_greeks


def compute_and_store_factors(underlying_id: str | None = None):
    session = SessionLocal()
    try:
        latest_time_sql = text("""
            SELECT MAX(quote_time) AS qt
            FROM option_quote
            WHERE (:underlying_id IS NULL OR underlying_id = :underlying_id)
        """)
        latest_row = session.execute(latest_time_sql, {
            "underlying_id": underlying_id
        }).fetchone()

        if not latest_row or latest_row.qt is None:
            return {
                "quote_time": None,
                "rows_processed": 0,
                "factors_inserted": 0,
                "message": "no option_quote data"
            }

        latest_qt = latest_row.qt

        pair_sql = text("""
            WITH paired AS (
                SELECT
                    c.quote_time,
                    c.underlying_id,
                    c.expiry_date,
                    c.strike,
                    c.contract_id AS call_contract_id,
                    p.contract_id AS put_contract_id,
                    (c.bid_price1 + c.ask_price1) / 2.0 AS call_mid,
                    (p.bid_price1 + p.ask_price1) / 2.0 AS put_mid,
                    c.implied_vol AS call_iv_raw,
                    p.implied_vol AS put_iv_raw
                FROM option_quote c
                JOIN option_quote p
                  ON c.underlying_id = p.underlying_id
                 AND c.quote_time = p.quote_time
                 AND c.expiry_date = p.expiry_date
                 AND c.strike = p.strike
                WHERE c.option_type = 'C'
                  AND p.option_type = 'P'
                  AND c.quote_time = :quote_time
                  AND p.quote_time = :quote_time
                  AND (:underlying_id IS NULL OR c.underlying_id = :underlying_id)
            )
            SELECT *
            FROM paired
            ORDER BY underlying_id, expiry_date, strike
        """)

        rows = session.execute(pair_sql, {
            "quote_time": latest_qt,
            "underlying_id": underlying_id
        }).fetchall()

        if not rows:
            return {
                "quote_time": latest_qt,
                "rows_processed": 0,
                "factors_inserted": 0,
                "message": "no paired call-put rows found"
            }

        spot_sql = text("""
            SELECT underlying_id, quote_time, last_price
            FROM underlying_quote
            WHERE quote_time = :quote_time
              AND (:underlying_id IS NULL OR underlying_id = :underlying_id)
        """)
        spot_rows = session.execute(spot_sql, {
            "quote_time": latest_qt,
            "underlying_id": underlying_id
        }).fetchall()

        spot_map = {
            (r.underlying_id, r.quote_time): float(r.last_price)
            for r in spot_rows
            if r.last_price is not None
        }

        inserted = 0

        insert_sql = text("""
            INSERT INTO option_factor_snapshot (
                snapshot_time, underlying_id, expiry_date, strike,
                call_contract_id, put_contract_id, spot_price,
                call_mid, put_mid, synthetic_forward, parity_deviation,
                near_iv, far_iv, term_slope, liquidity_score,
                call_delta, call_gamma, call_theta, call_vega,
                put_delta, put_gamma, put_theta, put_vega
            ) VALUES (
                :snapshot_time, :underlying_id, :expiry_date, :strike,
                :call_contract_id, :put_contract_id, :spot_price,
                :call_mid, :put_mid, :synthetic_forward, :parity_deviation,
                :near_iv, :far_iv, :term_slope, :liquidity_score,
                :call_delta, :call_gamma, :call_theta, :call_vega,
                :put_delta, :put_gamma, :put_theta, :put_vega
            )
            ON CONFLICT (snapshot_time, underlying_id, expiry_date, strike)
            DO NOTHING
        """)

        for r in rows:
            spot_price = spot_map.get((r.underlying_id, r.quote_time))
            if spot_price is None:
                continue

            if r.strike is None or r.call_mid is None or r.put_mid is None:
                continue

            strike = float(r.strike)
            call_mid = float(r.call_mid)
            put_mid = float(r.put_mid)

            synthetic_forward = call_mid - put_mid + strike
            parity_deviation = synthetic_forward - spot_price

            dte, T = calc_dte(r.quote_time, r.expiry_date)
            if T is None or T <= 0:
                continue

            call_iv = solve_iv(call_mid, spot_price, strike, T, option_type="C")
            put_iv = solve_iv(put_mid, spot_price, strike, T, option_type="P")

            if call_iv is None and r.call_iv_raw is not None:
                call_iv = float(r.call_iv_raw)
            if put_iv is None and r.put_iv_raw is not None:
                put_iv = float(r.put_iv_raw)

            call_greeks = (
                calc_greeks(spot_price, strike, T, 0.0, call_iv, "C")
                if call_iv is not None else None
            )
            put_greeks = (
                calc_greeks(spot_price, strike, T, 0.0, put_iv, "P")
                if put_iv is not None else None
            )

            result = session.execute(insert_sql, {
                "snapshot_time": r.quote_time,
                "underlying_id": r.underlying_id,
                "expiry_date": r.expiry_date,
                "strike": strike,
                "call_contract_id": r.call_contract_id,
                "put_contract_id": r.put_contract_id,
                "spot_price": spot_price,
                "call_mid": call_mid,
                "put_mid": put_mid,
                "synthetic_forward": synthetic_forward,
                "parity_deviation": parity_deviation,
                "near_iv": call_iv,
                "far_iv": None,
                "term_slope": None,
                "liquidity_score": 1.0,
                "call_delta": call_greeks["delta"] if call_greeks else None,
                "call_gamma": call_greeks["gamma"] if call_greeks else None,
                "call_theta": call_greeks["theta"] if call_greeks else None,
                "call_vega": call_greeks["vega"] if call_greeks else None,
                "put_delta": put_greeks["delta"] if put_greeks else None,
                "put_gamma": put_greeks["gamma"] if put_greeks else None,
                "put_theta": put_greeks["theta"] if put_greeks else None,
                "put_vega": put_greeks["vega"] if put_greeks else None,
            })

            if result.rowcount and result.rowcount > 0:
                inserted += 1

        session.commit()
        return {
            "quote_time": latest_qt,
            "rows_processed": len(rows),
            "factors_inserted": inserted
        }

    finally:
        session.close()


def update_greeks():
    """回填历史数据中缺失的 Greeks"""
    session = SessionLocal()
    try:
        rows = session.execute(text("""
            SELECT id, spot_price, strike, snapshot_time, expiry_date,
                   call_mid, put_mid, near_iv
            FROM option_factor_snapshot
            WHERE call_delta IS NULL AND near_iv IS NOT NULL
        """)).fetchall()

        updated = 0
        for r in rows:
            dte, T = calc_dte(r.snapshot_time, r.expiry_date)
            if T is None or T <= 0:
                continue

            call_iv = float(r.near_iv)
            put_iv = call_iv

            call_g = calc_greeks(float(r.spot_price), float(r.strike), T, 0.0, call_iv, "C")
            put_g = calc_greeks(float(r.spot_price), float(r.strike), T, 0.0, put_iv, "P")

            if call_g and put_g:
                session.execute(text("""
                    UPDATE option_factor_snapshot
                    SET call_delta=:cd, call_gamma=:cg, call_theta=:ct, call_vega=:cv,
                        put_delta=:pd, put_gamma=:pg, put_theta=:pt, put_vega=:pv
                    WHERE id=:id
                """), {
                    "cd": call_g["delta"], "cg": call_g["gamma"],
                    "ct": call_g["theta"], "cv": call_g["vega"],
                    "pd": put_g["delta"], "pg": put_g["gamma"],
                    "pt": put_g["theta"], "pv": put_g["vega"],
                    "id": r.id
                })
                updated += 1

        session.commit()
        return {"greeks_updated": updated}
    finally:
        session.close()


def update_term_slope():
    """单独回填 term_slope，用 BS 反推的 near_iv"""
    session = SessionLocal()
    try:
        result = session.execute(text("""
            UPDATE option_factor_snapshot AS nf
            SET
                far_iv = ff.near_iv,
                term_slope = ff.near_iv - nf.near_iv
            FROM option_factor_snapshot AS ff
            WHERE nf.underlying_id = ff.underlying_id
              AND nf.strike = ff.strike
              AND nf.snapshot_time = ff.snapshot_time
              AND nf.expiry_date < ff.expiry_date
              AND nf.near_iv IS NOT NULL
              AND ff.near_iv IS NOT NULL
        """))
        session.commit()
        return {"updated_rows": result.rowcount}
    finally:
        session.close()