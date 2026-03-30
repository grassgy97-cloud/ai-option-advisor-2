"""
covered_call_advisor.py
备兑策略专项推荐——针对已持仓标的（510300 x2手，588000 x4手）

使用：python covered_call_advisor.py
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.db import engine

# ── 持仓配置 ──────────────────────────────────────────────
POSITIONS = {
    "510300": {"hands": 2,  "name": "沪深300ETF"},
    "588000": {"hands": 4,  "name": "科创50ETF"},
}

# ── 筛选参数 ──────────────────────────────────────────────
DTE_MIN          = 60       # 最短到期天数
DTE_MAX          = 180      # 最长到期天数
DELTA_TARGET     = 0.20     # 目标delta（虚值程度）
DELTA_TOLERANCE  = 0.12     # delta容差（0.08~0.32都纳入候选）
MAX_REL_SPREAD   = 0.05     # 最大相对价差（流动性过滤）
FEE_PER_SHARE    = 0.0004   # 手续费 4元/手，1手=10000份
TOP_N            = 3        # 每个标的输出几个推荐

# ── 年化计算 ──────────────────────────────────────────────
def calc_annualized_yield(
    credit: float,
    spot: float,
    dte: int,
    fee: float = FEE_PER_SHARE,
) -> Optional[float]:
    if spot <= 0 or dte <= 0:
        return None
    net = credit - fee
    if net <= 0:
        return None
    return net / spot / (dte / 360)


# ── 挂单建议 ──────────────────────────────────────────────
def suggest_limit_price(bid: float, ask: float) -> float:
    """
    卖方挂单建议：偏bid方向1/3处。
    比mid更保守，成交概率合理，不追ask。
    """
    return round(bid + (ask - bid) / 3, 4)


# ── 从DB拉最新合约 ───────────────────────────────────────
def fetch_candidates(eng: Engine, underlying_id: str) -> List[Dict[str, Any]]:
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
        ORDER BY f.dte_calendar ASC, f.delta ASC
    """)

    with eng.connect() as conn:
        rows = conn.execute(sql, {
            "uid": underlying_id,
            "dte_min": DTE_MIN,
            "dte_max": DTE_MAX,
        }).mappings().all()

    return [dict(r) for r in rows]


# ── 筛选+评分 ────────────────────────────────────────────
def score_and_filter(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []

    for r in rows:
        delta    = abs(float(r["delta"] or 0))
        bid      = float(r["bid_price1"])
        ask      = float(r["ask_price1"])
        mid      = float(r["mid_price"])
        spot     = float(r["spot_price"] or 0)
        dte      = int(r["dte_calendar"])
        strike   = float(r["strike"])
        iv       = float(r["implied_vol"] or 0)

        # 行权价过滤：非0.05整数倍的是除权合约，份数不标准，不能做备兑
        strike_rounded = round(strike * 20) / 20  # 最近的0.05整数倍
        if abs(strike - strike_rounded) > 0.001:
            continue

        # delta筛选
        if abs(delta - DELTA_TARGET) > DELTA_TOLERANCE:
            continue

        # 流动性筛选
        if mid <= 0:
            continue
        rel_spread = (ask - bid) / mid
        if rel_spread > MAX_REL_SPREAD:
            continue

        # 年化收益率
        ann_yield = calc_annualized_yield(mid, spot, dte)
        if ann_yield is None:
            continue

        # 挂单建议
        limit_price = suggest_limit_price(bid, ask)

        # 被行权缓冲（strike相对spot的上行空间）
        upside_buffer = (strike / spot - 1.0) if spot > 0 else None

        # 综合评分：年化3-5%甜区给高分，过高过低降分
        if 0.03 <= ann_yield <= 0.05:
            score = 1.0
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

        # delta越接近目标加分
        delta_bonus = max(0, 0.10 * (1 - abs(delta - DELTA_TARGET) / DELTA_TOLERANCE))
        score += delta_bonus

        # 流动性加分
        if rel_spread <= 0.01:
            score += 0.05
        elif rel_spread <= 0.03:
            score += 0.02

        results.append({
            "contract_id":    r["contract_id"],
            "underlying_id":  r["underlying_id"],
            "expiry_date":    r["expiry_date"],
            "strike":         strike,
            "dte":            dte,
            "delta":          round(delta, 3),
            "iv":             round(iv, 4),
            "spot":           round(spot, 4),
            "bid":            round(bid, 4),
            "ask":            round(ask, 4),
            "mid":            round(mid, 4),
            "limit_price":    limit_price,
            "rel_spread":     round(rel_spread, 4),
            "ann_yield":      round(ann_yield, 4),
            "upside_buffer":  round(upside_buffer, 4) if upside_buffer else None,
            "score":          round(score, 3),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── 输出 ─────────────────────────────────────────────────
def print_recommendations(underlying_id: str, hands: int, name: str, candidates: List[Dict]):
    shares_per_hand = 10000
    total_shares = hands * shares_per_hand

    print(f"\n{'='*60}")
    print(f"  {name}（{underlying_id}）× {hands}手 = {total_shares:,}份")
    print(f"{'='*60}")

    if not candidates:
        print("  ⚠️  无符合条件的合约（检查DTE/delta/流动性参数）")
        return

    top = candidates[:TOP_N]
    spot = top[0]["spot"]
    print(f"  当前标的价格：{spot}")
    print()

    for i, c in enumerate(top, 1):
        total_credit    = c["mid"] * total_shares
        total_credit_lp = c["limit_price"] * total_shares
        total_fee       = FEE_PER_SHARE * total_shares * hands  # 每手一次手续费

        print(f"  【推荐{i}】{'★' * max(1, round(c['score']))}  score={c['score']}")
        print(f"    合约：{c['contract_id']}")
        print(f"    到期：{c['expiry_date']}  DTE={c['dte']}天")
        print(f"    行权价：{c['strike']}  （上行缓冲 {c['upside_buffer']:.1%}）")
        print(f"    Delta：{c['delta']}  IV：{c['iv']:.1%}")
        print()
        print(f"    报价：bid={c['bid']}  ask={c['ask']}  mid={c['mid']}")
        print(f"    ▶ 建议挂单价：{c['limit_price']}  （偏bid方向1/3）")
        print(f"    ▶ 预计总收入：{total_credit:.2f}元（按mid）/ {total_credit_lp:.2f}元（按挂单价）")
        print(f"    ▶ 手续费：约{total_fee:.2f}元")
        print(f"    ▶ 年化收益率：{c['ann_yield']:.1%}（按mid）")
        print(f"    ▶ 流动性价差：{c['rel_spread']:.1%}")
        print()


# ── 主入口 ───────────────────────────────────────────────
def main():
    print("\n📊 备兑策略推荐报告")
    print(f"筛选条件：DTE {DTE_MIN}-{DTE_MAX}天，delta目标 {DELTA_TARGET}±{DELTA_TOLERANCE}")

    for uid, pos in POSITIONS.items():
        rows = fetch_candidates(engine, uid)
        candidates = score_and_filter(rows)
        print_recommendations(uid, pos["hands"], pos["name"], candidates)

    print("\n⚠️  注意事项：")
    print("  1. 挂单价为参考，实际可根据市场深度微调")
    print("  2. 被行权时需交割现货，确认持仓数量足够")
    print("  3. 建议在开盘后流动性好的时段挂单")
    print("  4. 如果没有成交，可以在收盘前1小时再次评估")


if __name__ == "__main__":
    main()