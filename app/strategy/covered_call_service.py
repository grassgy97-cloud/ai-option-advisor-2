from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine


def calc_annualized_yield(
    credit: float,
    spot: float,
    dte: int,
    fee_per_share: float = 0.0004,
) -> Optional[float]:
    if spot <= 0 or dte <= 0:
        return None
    net = credit - fee_per_share
    if net <= 0:
        return None
    return net / spot / (dte / 360)


def suggest_limit_price(bid: float, ask: float) -> float:
    """
    卖方挂单建议：偏 bid 方向 1/3 处。
    """
    return round(bid + (ask - bid) / 3, 4)


def fetch_covered_call_candidates(
    eng: Engine,
    underlying_id: str,
    dte_min: int,
    dte_max: int,
) -> List[Dict[str, Any]]:
    sql = text("""
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :uid
        ),
        factors AS (
            SELECT
                f.contract_id,
                f.underlying_id,
                f.option_type,
                f.expiry_date,
                f.strike,
                f.spot_price,
                f.implied_vol,
                f.delta,
                f.gamma,
                f.theta,
                f.vega,
                f.dte_calendar,
                f.fetch_time
            FROM option_factor_snapshots f, latest
            WHERE f.underlying_id = :uid
              AND f.fetch_time = latest.max_fetch_time
              AND f.option_type IN ('C', 'CALL')
              AND f.delta IS NOT NULL
              AND f.implied_vol IS NOT NULL
              AND f.dte_calendar BETWEEN :dte_min AND :dte_max
        ),
        quotes AS (
            SELECT
                q.contract_id,
                q.bid_price1,
                q.ask_price1
            FROM option_quote_snapshots q
            JOIN latest ON q.fetch_time = latest.max_fetch_time
            WHERE q.underlying_id = :uid
        )
        SELECT
            f.*,
            q.bid_price1,
            q.ask_price1,
            (q.bid_price1 + q.ask_price1) / 2.0 AS mid_price
        FROM factors f
        LEFT JOIN quotes q ON f.contract_id = q.contract_id
        WHERE q.bid_price1 IS NOT NULL
          AND q.ask_price1 IS NOT NULL
          AND q.bid_price1 > 0
          AND q.ask_price1 > 0
        ORDER BY f.dte_calendar ASC, ABS(ABS(f.delta) - 0.20) ASC
    """)

    with eng.connect() as conn:
        rows = conn.execute(sql, {
            "uid": underlying_id,
            "dte_min": dte_min,
            "dte_max": dte_max,
        }).mappings().all()

    return [dict(r) for r in rows]


def _match_target_upside_buffer(
    dte: int,
    target_upside_rules: Optional[List[Dict[str, float]]],
) -> Optional[float]:
    """
    按 DTE 分段匹配理想上行保护。
    规则示例：
    [
      {"dte_max": 120, "target_upside_buffer": 0.08},
      {"dte_max": 9999, "target_upside_buffer": 0.10}
    ]
    """
    if not target_upside_rules:
        return None

    valid_rules = []
    for r in target_upside_rules:
        try:
            dte_max = int(r["dte_max"])
            target = float(r["target_upside_buffer"])
            valid_rules.append({
                "dte_max": dte_max,
                "target_upside_buffer": target,
            })
        except Exception:
            continue

    valid_rules.sort(key=lambda x: x["dte_max"])

    for rule in valid_rules:
        if dte <= rule["dte_max"]:
            return rule["target_upside_buffer"]

    return valid_rules[-1]["target_upside_buffer"] if valid_rules else None


def _calc_buffer_score(
    upside_buffer: Optional[float],
    target_upside_buffer: Optional[float],
) -> float:
    """
    对“理想上行保护”打分：
    - 越接近目标越加分
    - 偏离越大越减分
    """
    if upside_buffer is None or target_upside_buffer is None:
        return 0.0

    diff = abs(upside_buffer - target_upside_buffer)

    if diff <= 0.005:
        return 0.15
    elif diff <= 0.01:
        return 0.12
    elif diff <= 0.02:
        return 0.08
    elif diff <= 0.03:
        return 0.03
    elif diff <= 0.05:
        return -0.05
    elif diff <= 0.08:
        return -0.12
    else:
        return -0.22


def score_covered_call_candidates(
    rows: List[Dict[str, Any]],
    hands: int,
    delta_target: float = 0.20,
    delta_tolerance: float = 0.12,
    max_rel_spread: float = 0.05,
    fee_per_share: float = 0.0004,
    target_upside_rules: Optional[List[Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    shares_per_hand = 10000
    total_shares = hands * shares_per_hand

    for r in rows:
        delta = abs(float(r["delta"] or 0))
        bid = float(r["bid_price1"])
        ask = float(r["ask_price1"])
        mid = float(r["mid_price"])
        spot = float(r["spot_price"] or 0)
        dte = int(r["dte_calendar"])
        strike = float(r["strike"])
        iv = float(r["implied_vol"] or 0)

        # 排除非标准合约
        strike_rounded = round(strike * 20) / 20
        if abs(strike - strike_rounded) > 0.001:
            continue

        # delta 过滤
        if abs(delta - delta_target) > delta_tolerance:
            continue

        # 流动性过滤
        if mid <= 0:
            continue
        rel_spread = (ask - bid) / mid
        if rel_spread > max_rel_spread:
            continue

        ann_yield = calc_annualized_yield(
            credit=mid,
            spot=spot,
            dte=dte,
            fee_per_share=fee_per_share,
        )
        if ann_yield is None:
            continue

        limit_price = suggest_limit_price(bid, ask)
        upside_buffer = (strike / spot - 1.0) if spot > 0 else None
        target_upside_buffer = _match_target_upside_buffer(dte, target_upside_rules)

        # 年化收益基础分
        if 0.03 <= ann_yield <= 0.05:
            score = 1.00
        elif 0.05 < ann_yield <= 0.08:
            score = 0.85
        elif 0.02 <= ann_yield < 0.03:
            score = 0.75
        elif 0.08 < ann_yield <= 0.12:
            score = 0.65
        elif ann_yield > 0.12:
            score = 0.50
        else:
            score = 0.30

        # delta 越接近目标越好
        delta_bonus = max(0.0, 0.10 * (1 - abs(delta - delta_target) / delta_tolerance))

        # 流动性加分
        liquidity_bonus = 0.0
        if rel_spread <= 0.01:
            liquidity_bonus = 0.05
        elif rel_spread <= 0.03:
            liquidity_bonus = 0.02

        # 理想上行保护打分
        buffer_score = _calc_buffer_score(
            upside_buffer=upside_buffer,
            target_upside_buffer=target_upside_buffer,
        )

        score = score + delta_bonus + liquidity_bonus + buffer_score

        estimated_total_income_mid = mid * total_shares
        estimated_total_income_limit = limit_price * total_shares

        results.append({
            "contract_id": r["contract_id"],
            "underlying_id": r["underlying_id"],
            "expiry_date": str(r["expiry_date"]),
            "strike": strike,
            "dte": dte,
            "delta": round(delta, 3),
            "iv": round(iv, 4),
            "spot": round(spot, 4),
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "mid": round(mid, 4),
            "limit_price": limit_price,
            "rel_spread": round(rel_spread, 4),
            "ann_yield": round(ann_yield, 4),
            "upside_buffer": round(upside_buffer, 4) if upside_buffer is not None else None,
            "target_upside_buffer": target_upside_buffer,
            "buffer_score": round(buffer_score, 3),
            "score": round(score, 3),
            "estimated_total_income_mid": round(estimated_total_income_mid, 2),
            "estimated_total_income_limit": round(estimated_total_income_limit, 2),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def run_covered_call_scan(
    engine: Engine,
    underlying_id: str,
    hands: int,
    dte_min: int = 60,
    dte_max: int = 180,
    delta_target: float = 0.20,
    delta_tolerance: float = 0.12,
    max_rel_spread: float = 0.05,
    fee_per_share: float = 0.0004,
    top_n: int = 3,
    target_upside_rules: Optional[List[Dict[str, float]]] = None,
) -> Dict[str, Any]:
    rows = fetch_covered_call_candidates(
        eng=engine,
        underlying_id=underlying_id,
        dte_min=dte_min,
        dte_max=dte_max,
    )

    ranked = score_covered_call_candidates(
        rows=rows,
        hands=hands,
        delta_target=delta_target,
        delta_tolerance=delta_tolerance,
        max_rel_spread=max_rel_spread,
        fee_per_share=fee_per_share,
        target_upside_rules=target_upside_rules,
    )

    shares_per_hand = 10000
    total_shares = hands * shares_per_hand

    return {
        "underlying_id": underlying_id,
        "hands": hands,
        "total_shares": total_shares,
        "params": {
            "dte_min": dte_min,
            "dte_max": dte_max,
            "delta_target": delta_target,
            "delta_tolerance": delta_tolerance,
            "max_rel_spread": max_rel_spread,
            "fee_per_share": fee_per_share,
            "top_n": top_n,
            "target_upside_rules": target_upside_rules or [],
        },
        "items": ranked[:top_n],
    }