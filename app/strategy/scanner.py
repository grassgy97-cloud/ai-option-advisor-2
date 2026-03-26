import json
from sqlalchemy import text
from app.core.db import SessionLocal
from app.pricing.factor_engine import compute_and_store_factors


def scan_static_opportunities(underlying_id: str = None):
    session = SessionLocal()
    try:
        compute_and_store_factors(underlying_id=underlying_id)

        factor_sql = text("""
            SELECT
                f.snapshot_time, f.underlying_id, f.expiry_date, f.strike,
                f.call_contract_id, f.put_contract_id, f.spot_price,
                f.call_mid, f.put_mid, f.synthetic_forward, f.parity_deviation,
                f.call_delta, f.call_gamma, f.call_theta, f.call_vega,
                f.put_delta, f.put_gamma, f.put_theta, f.put_vega,
                c.implied_vol AS call_iv,
                p.implied_vol AS put_iv
            FROM option_factor_snapshot f
            LEFT JOIN option_quote c
              ON c.contract_id = f.call_contract_id
             AND c.quote_time = f.snapshot_time
            LEFT JOIN option_quote p
              ON p.contract_id = f.put_contract_id
             AND p.quote_time = f.snapshot_time
            WHERE (:underlying_id IS NULL OR f.underlying_id = :underlying_id)
            ORDER BY f.snapshot_time, f.expiry_date, f.strike
        """)

        rows = session.execute(factor_sql, {"underlying_id": underlying_id}).fetchall()
        results = []

        # -------------------------
        # 1) parity_arb 扫描
        # -------------------------
        for r in rows:
            parity_deviation = float(r.parity_deviation) if r.parity_deviation is not None else 0.0
            spot_price = float(r.spot_price) if r.spot_price is not None else 1.0

            if abs(parity_deviation) <= 0.01:
                continue

            if parity_deviation > 0:
                legs = [
                    {"contract_id": r.call_contract_id, "side": "sell", "qty": 1},
                    {"contract_id": r.put_contract_id, "side": "buy", "qty": 1}
                ]
                note = "static parity deviation check; synthetic_rich_vs_spot"
            else:
                legs = [
                    {"contract_id": r.call_contract_id, "side": "buy", "qty": 1},
                    {"contract_id": r.put_contract_id, "side": "sell", "qty": 1}
                ]
                note = "static parity deviation check; synthetic_cheap_vs_spot"

            edge_value = abs(parity_deviation)
            transaction_cost_est = 0.0020
            score = edge_value - transaction_cost_est

            results.append({
                "detect_time": r.snapshot_time,
                "strategy_type": "parity_arb",
                "underlying_id": r.underlying_id,
                "reference_key": f"{r.underlying_id}_{r.expiry_date}_{r.strike}",
                "leg_json": legs,
                "edge_value": edge_value,
                "edge_pct": edge_value / spot_price if spot_price else None,
                "annualized_return": None,
                "transaction_cost_est": transaction_cost_est,
                "score": round(score, 6),
                "risk_level": "low",
                "note": note,
                "greeks": {
                    "call_delta": float(r.call_delta) if r.call_delta is not None else None,
                    "call_gamma": float(r.call_gamma) if r.call_gamma is not None else None,
                    "call_theta": float(r.call_theta) if r.call_theta is not None else None,
                    "call_vega": float(r.call_vega) if r.call_vega is not None else None,
                    "put_delta": float(r.put_delta) if r.put_delta is not None else None,
                }
            })

        # -------------------------
        # 2) calendar_arb 扫描
        # -------------------------
        grouped = {}
        for r in rows:
            key = (r.underlying_id, float(r.strike), r.snapshot_time)
            grouped.setdefault(key, []).append(r)

        for key, items in grouped.items():
            items = sorted(items, key=lambda x: x.expiry_date)
            if len(items) < 2:
                continue

            near = items[0]
            far = items[1]

            if near.call_iv is None or far.call_iv is None:
                continue

            term_slope = float(far.call_iv) - float(near.call_iv)

            if abs(term_slope) <= 0.02:
                continue

            if term_slope > 0:
                legs = [
                    {"contract_id": near.call_contract_id, "side": "buy", "qty": 1},
                    {"contract_id": far.call_contract_id, "side": "sell", "qty": 1}
                ]
                note = "static term structure deviation check; far_iv_richer_than_near"
            else:
                legs = [
                    {"contract_id": near.call_contract_id, "side": "sell", "qty": 1},
                    {"contract_id": far.call_contract_id, "side": "buy", "qty": 1}
                ]
                note = "static term structure deviation check; near_iv_richer_than_far"

            edge_value = abs(term_slope)
            transaction_cost_est = 0.0030
            score = edge_value - transaction_cost_est

            results.append({
                "detect_time": near.snapshot_time,
                "strategy_type": "calendar_arb",
                "underlying_id": near.underlying_id,
                "reference_key": f"{near.underlying_id}_{near.strike}_{near.expiry_date}_{far.expiry_date}",
                "leg_json": legs,
                "edge_value": edge_value,
                "edge_pct": None,
                "annualized_return": None,
                "transaction_cost_est": transaction_cost_est,
                "score": round(score, 6),
                "risk_level": "low",
                "note": note,
                "greeks": {
                    "near_call_delta": float(near.call_delta) if near.call_delta is not None else None,
                    "near_call_theta": float(near.call_theta) if near.call_theta is not None else None,
                    "far_call_delta": float(far.call_delta) if far.call_delta is not None else None,
                    "far_call_theta": float(far.call_theta) if far.call_theta is not None else None,
                    "term_vega_diff": round(float(far.call_vega or 0) - float(near.call_vega or 0), 4),
                }
            })

        # -------------------------
        # 3) 排序 + rank
        # -------------------------
        results.sort(
            key=lambda x: (
                x.get("score", float("-inf")),
                x.get("edge_value", 0.0)
            ),
            reverse=True
        )

        for idx, item in enumerate(results, start=1):
            item["rank"] = idx

        # -------------------------
        # 4) 写入套利机会表
        # -------------------------
        insert_opp_sql = text("""
            INSERT INTO arb_opportunity (
                detect_time, strategy_type, underlying_id, reference_key, leg_json,
                edge_value, edge_pct, annualized_return, transaction_cost_est,
                risk_level, note, valid_flag
            ) VALUES (
                :detect_time, :strategy_type, :underlying_id, :reference_key, CAST(:leg_json AS jsonb),
                :edge_value, :edge_pct, :annualized_return, :transaction_cost_est,
                :risk_level, :note, true
            )
            ON CONFLICT (detect_time, strategy_type, reference_key) DO NOTHING
        """)

        for item in results:
            session.execute(insert_opp_sql, {
                "detect_time": item["detect_time"],
                "strategy_type": item["strategy_type"],
                "underlying_id": item["underlying_id"],
                "reference_key": item["reference_key"],
                "leg_json": json.dumps(item["leg_json"], ensure_ascii=False),
                "edge_value": item["edge_value"],
                "edge_pct": item["edge_pct"],
                "annualized_return": item["annualized_return"],
                "transaction_cost_est": item["transaction_cost_est"],
                "risk_level": item["risk_level"],
                "note": item["note"],
            })

        session.commit()
        return {
            "factor_rows": len(rows),
            "opportunity_count": len(results),
            "opportunities": results
        }

    finally:
        session.close()