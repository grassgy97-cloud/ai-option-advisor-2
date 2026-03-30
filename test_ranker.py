"""
临时测试脚本：不需要数据库，直接构造 ResolvedStrategy 跑 rank_strategies
用法：在项目根目录执行 python test_ranker.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.models.schemas import ResolvedStrategy, ResolvedLeg
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.greeks_monitor import compute_strategy_net_greeks


def make_leg(
    contract_id: str,
    action: str,
    option_type: str,
    expiry_date: str,
    strike: float,
    bid: float,
    ask: float,
    delta: float,
    vega: float = 0.005,
    iv: float = 0.20,
    dte: int = 30,
    gamma: float = -0.5,
    theta: float = -0.0009,
) -> ResolvedLeg:
    mid = round((bid + ask) / 2, 4)
    return ResolvedLeg(
        contract_id=contract_id,
        action=action,
        option_type=option_type,
        expiry_date=expiry_date,
        strike=strike,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=delta,
        vega=vega,
        iv=iv,
        dte=dte,
        gamma=gamma,
        theta=theta,
        quantity=1,
    )


def make_strategy(
    strategy_type: str,
    legs: list,
    spot: float = 4.00,
    net_credit: float = None,
    net_debit: float = None,
    prior_weight: float = 0.8,
    iv_pct: float = 0.40,
) -> ResolvedStrategy:
    net_premium = (net_credit or 0) - (net_debit or 0)
    return ResolvedStrategy(
        strategy_type=strategy_type,
        underlying_id="510300",
        spot_price=spot,
        legs=legs,
        net_premium=net_premium,
        net_credit=net_credit,
        net_debit=net_debit,
        metadata={
            "prior_weight": prior_weight,
            "iv_pct": iv_pct,
        },
    )


strategies = []

# ── long_call ──────────────────────────────────────────────
# 1. ATM低IV低theta（应最高分）
#    DTE60，delta=0.42，theta=-0.0008（损耗慢），iv_pct=0.12
strategies.append(make_strategy(
    "long_call",
    legs=[make_leg("LC_ATM_LOWIV", "BUY", "CALL", "2025-05-01", 4.00,
                   bid=0.030, ask=0.034, delta=0.42, vega=0.007,
                   iv=0.14, dte=60, theta=-0.0008)],
    spot=4.00, net_debit=0.032, prior_weight=0.85, iv_pct=0.12,
))

# 2. ATM高IV高theta（应低于用例1）
#    DTE60，delta=0.42，theta=-0.0012（损耗快），iv_pct=0.60
strategies.append(make_strategy(
    "long_call",
    legs=[make_leg("LC_ATM_HIGHIV", "BUY", "CALL", "2025-05-01", 4.00,
                   bid=0.060, ask=0.066, delta=0.42, vega=0.007,
                   iv=0.35, dte=60, theta=-0.0012)],
    spot=4.00, net_debit=0.063, prior_weight=0.85, iv_pct=0.60,
))

# 3. 偏虚低IV低theta（delta偏低拉分，theta比ATM小因为虚值）
#    DTE60，delta=0.25，theta=-0.0006，iv_pct=0.12
strategies.append(make_strategy(
    "long_call",
    legs=[make_leg("LC_OTM_LOWIV", "BUY", "CALL", "2025-05-01", 4.20,
                   bid=0.010, ask=0.014, delta=0.25, vega=0.004,
                   iv=0.14, dte=60, theta=-0.0006)],
    spot=4.00, net_debit=0.012, prior_weight=0.85, iv_pct=0.12,
))

# 4. ATM低IV中theta（theta=-0.001，介于用例1和2之间）
strategies.append(make_strategy(
    "long_call",
    legs=[make_leg("LC_ATM_MIDTHETA", "BUY", "CALL", "2025-05-01", 4.00,
                   bid=0.030, ask=0.034, delta=0.42, vega=0.007,
                   iv=0.14, dte=60, theta=-0.001)],
    spot=4.00, net_debit=0.032, prior_weight=0.85, iv_pct=0.12,
))

# ── call_calendar ──────────────────────────────────────────
# 5. 正向结构
strategies.append(make_strategy(
    "call_calendar",
    legs=[
        make_leg("CAL_NEAR", "SELL", "CALL", "2025-02-21", 4.00,
                 bid=0.020, ask=0.024, delta=0.50, iv=0.22, dte=20, vega=0.005),
        make_leg("CAL_FAR",  "BUY",  "CALL", "2025-04-18", 4.00,
                 bid=0.038, ask=0.042, delta=0.50, iv=0.18, dte=60, vega=0.010),
    ],
    spot=4.00, net_debit=0.020, prior_weight=0.9, iv_pct=0.45,
))

# 6. 反向结构
strategies.append(make_strategy(
    "call_calendar",
    legs=[
        make_leg("CAL_REV_NEAR", "SELL", "CALL", "2025-02-21", 4.00,
                 bid=0.020, ask=0.024, delta=0.50, iv=0.18, dte=20, vega=0.005),
        make_leg("CAL_REV_FAR",  "BUY",  "CALL", "2025-04-18", 4.00,
                 bid=0.042, ask=0.046, delta=0.50, iv=0.22, dte=60, vega=0.010),
    ],
    spot=4.00, net_debit=0.024, prior_weight=0.9, iv_pct=0.45,
))

# ── naked_put ──────────────────────────────────────────────
# 7. 甜区delta=0.18
strategies.append(make_strategy(
    "naked_put",
    legs=[make_leg("NP_18", "SELL", "PUT", "2025-02-21", 3.85,
                   bid=0.014, ask=0.018, delta=-0.18, dte=25)],
    spot=4.00, net_credit=0.016, prior_weight=0.7, iv_pct=0.55,
))

# 8. 偏深delta=0.32
strategies.append(make_strategy(
    "naked_put",
    legs=[make_leg("NP_32", "SELL", "PUT", "2025-02-21", 3.90,
                   bid=0.030, ask=0.034, delta=-0.32, dte=25)],
    spot=4.00, net_credit=0.032, prior_weight=0.7, iv_pct=0.55,
))

# ── iron_condor ────────────────────────────────────────────
# 9. 短期DTE=15
strategies.append(make_strategy(
    "iron_condor",
    legs=[
        make_leg("IC_SC", "SELL", "CALL", "2025-02-07", 4.10,
                 bid=0.010, ask=0.014, delta=0.20, gamma=-1.2, dte=15, vega=0.004),
        make_leg("IC_BC", "BUY",  "CALL", "2025-02-07", 4.20,
                 bid=0.005, ask=0.008, delta=0.10, gamma=-0.5, dte=15, vega=0.002),
        make_leg("IC_SP", "SELL", "PUT",  "2025-02-07", 3.90,
                 bid=0.010, ask=0.014, delta=-0.20, gamma=-1.2, dte=15, vega=0.004),
        make_leg("IC_BP", "BUY",  "PUT",  "2025-02-07", 3.80,
                 bid=0.005, ask=0.008, delta=-0.10, gamma=-0.5, dte=15, vega=0.002),
    ],
    spot=4.00, net_credit=0.009, prior_weight=0.75, iv_pct=0.65,
))

# ── covered_call ───────────────────────────────────────────
# 10. 年化约4%甜区
strategies.append(make_strategy(
    "covered_call",
    legs=[make_leg("CC_SWEET", "SELL", "CALL", "2025-04-18", 4.20,
                   bid=0.038, ask=0.044, delta=0.20, dte=90)],
    spot=4.00, net_credit=0.041, prior_weight=0.65, iv_pct=0.50,
))

# ── vertical spreads ───────────────────────────────────────
# 11. debit：买平值卖虚值
strategies.append(make_strategy(
    "bull_call_spread",
    legs=[
        make_leg("BCS_BUY",  "BUY",  "CALL", "2025-03-21", 4.00,
                 bid=0.048, ask=0.052, delta=0.50, dte=30),
        make_leg("BCS_SELL", "SELL", "CALL", "2025-03-21", 4.20,
                 bid=0.018, ask=0.022, delta=0.25, dte=30),
    ],
    spot=4.00, net_debit=0.032, prior_weight=0.7, iv_pct=0.40,
))

# 12. credit：卖虚值买更虚值
strategies.append(make_strategy(
    "bear_call_spread",
    legs=[
        make_leg("BEAR_SELL", "SELL", "CALL", "2025-03-21", 4.10,
                 bid=0.028, ask=0.032, delta=0.30, dte=30),
        make_leg("BEAR_BUY",  "BUY",  "CALL", "2025-03-21", 4.20,
                 bid=0.012, ask=0.016, delta=0.15, dte=30),
    ],
    spot=4.00, net_credit=0.016, prior_weight=0.7, iv_pct=0.40,
))

# ── diagonal ───────────────────────────────────────────────
# 13. 甜区spread=0.20
strategies.append(make_strategy(
    "diagonal_call",
    legs=[
        make_leg("DIAG_NEAR", "SELL", "CALL", "2025-02-21", 4.10,
                 bid=0.022, ask=0.026, delta=0.30, iv=0.22, dte=20, vega=0.005),
        make_leg("DIAG_FAR",  "BUY",  "CALL", "2025-04-18", 4.00,
                 bid=0.040, ask=0.044, delta=0.50, iv=0.18, dte=60, vega=0.010),
    ],
    spot=4.00, net_debit=0.020, prior_weight=0.9, iv_pct=0.40,
))

# 14. spread偏大=0.40
strategies.append(make_strategy(
    "diagonal_call",
    legs=[
        make_leg("DIAG2_NEAR", "SELL", "CALL", "2025-02-21", 4.10,
                 bid=0.022, ask=0.026, delta=0.30, iv=0.22, dte=20, vega=0.005),
        make_leg("DIAG2_FAR",  "BUY",  "CALL", "2025-04-18", 3.90,
                 bid=0.065, ask=0.071, delta=0.70, iv=0.18, dte=60, vega=0.010),
    ],
    spot=4.00, net_debit=0.047, prior_weight=0.9, iv_pct=0.40,
))

# 极端IV差深度虚值calendar（near_iv比far_iv高0.10，strike偏离5%）
# moneyness应被豁免，得分应接近正向ATM calendar
strategies.append(make_strategy(
    "call_calendar",
    legs=[
        make_leg("CAL_EXTREME_NEAR", "SELL", "CALL", "2025-02-21", 4.20,
                 bid=0.018, ask=0.022, delta=0.35, iv=0.35, dte=20, vega=0.005),
        make_leg("CAL_EXTREME_FAR",  "BUY",  "CALL", "2025-04-18", 4.20,
                 bid=0.025, ask=0.029, delta=0.30, iv=0.25, dte=60, vega=0.008),
    ],
    spot=4.00, net_debit=0.008, prior_weight=0.9, iv_pct=0.45,
))

# 对照：同样偏离ATM但IV差只有0.04（不触发豁免，moneyness应惩罚）
strategies.append(make_strategy(
    "call_calendar",
    legs=[
        make_leg("CAL_OTM_NORMAL_NEAR", "SELL", "CALL", "2025-02-21", 4.20,
                 bid=0.018, ask=0.022, delta=0.35, iv=0.22, dte=20, vega=0.005),
        make_leg("CAL_OTM_NORMAL_FAR",  "BUY",  "CALL", "2025-04-18", 4.20,
                 bid=0.025, ask=0.029, delta=0.30, iv=0.18, dte=60, vega=0.008),
    ],
    spot=4.00, net_debit=0.008, prior_weight=0.9, iv_pct=0.45,
))

# ──────────────────────────────────────────────────────────
# 执行
# ──────────────────────────────────────────────────────────

print("=" * 80)
print("rank_strategies 测试")
print("=" * 80)

ranked = rank_strategies(strategies)

print("\n── 最终排序 ──")
print(f"{'#':<3} {'策略类型':<24} {'score':<8} breakdown")
print("-" * 80)

keys = [
    "delta_score", "vega_score", "iv_score", "theta_score", "cost_score",
    "signal_score", "buy_delta_score", "sell_delta_score",
    "near_delta_score", "delta_spread_score",
    "is_debit", "greeks_adj", "prior_adj",
]

for i, s in enumerate(ranked, 1):
    bd = s.score_breakdown
    bd_str = "  ".join(
        f"{k}={bd[k]:.3f}" if isinstance(bd.get(k), float) else f"{k}={bd.get(k)}"
        for k in keys if k in bd
    )
    print(f"{i:<3} {s.strategy_type:<24} {s.score:<8.4f} {bd_str}")

print("\n── long_call theta专项验证 ──")
for s in ranked:
    if s.strategy_type in ("long_call", "long_put"):
        bd = s.score_breakdown
        print(f"  {s.legs[0].contract_id:<20} "
              f"raw_theta={bd.get('raw_theta')}  "
              f"theta_score={bd.get('theta_score')}  "
              f"vega_score={bd.get('vega_score')}  "
              f"iv_score={bd.get('iv_score')}  "
              f"final={s.score}")