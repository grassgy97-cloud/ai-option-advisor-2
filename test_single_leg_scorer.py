# test_single_leg_scorer.py
from unittest.mock import patch
from app.models.schemas import ResolvedStrategy, ResolvedLeg
import app.strategy.strategy_ranker as ranker_module

def make_strategy(strategy_type, delta, net_credit=None, net_debit=None, mid=0.05, spot=4.0):
    leg = ResolvedLeg(
        contract_id="test",
        action="SELL" if strategy_type in ("naked_call", "naked_put", "covered_call") else "BUY",
        quantity=1,
        option_type="CALL",
        strike=4.0,
        expiry_date="2026-05-28",
        bid=mid * 0.9,
        ask=mid * 1.1,
        mid=mid,
        delta=delta,
        gamma=0.1,
        theta=-0.001,
        vega=0.05,
        iv=0.18,
        dte=60,
    )
    return ResolvedStrategy(
        strategy_type=strategy_type,
        underlying_id="510300",
        spot_price=spot,
        legs=[leg],
        net_premium=net_credit or -(net_debit or 0),
        net_credit=net_credit,
        net_debit=net_debit,
        metadata={"prior_weight": 1.0},
    )

print("=== long_call delta 评分 ===")
cases = [
    (0.50, "平值核心，应得1.0"),
    (0.45, "平值区，应得1.0"),
    (0.35, "轻虚值，应得0.85"),
    (0.25, "虚值，应得0.7"),
    (0.15, "太虚，应得0.5"),
    (0.65, "略深，应得0.8"),
    (0.80, "深值，应得0.6"),
]
for delta, desc in cases:
    s = make_strategy("long_call", delta=delta, net_debit=0.08, spot=4.0)
    score, bd = ranker_module._score_single_leg(s)
    print(f"  delta={delta:.2f} → delta_score={bd['delta_score']} total={score:.3f}  ({desc})")

print()
print("=== naked_call delta 评分 ===")
cases = [
    (0.22, "目标虚值，应得1.0"),
    (0.18, "略偏虚，应得0.9"),
    (0.28, "略偏深，应得0.8"),
    (0.40, "太深，应得0.3"),
]
for delta, desc in cases:
    s = make_strategy("naked_call", delta=delta, net_credit=0.03, spot=4.0)
    score, bd = ranker_module._score_single_leg(s)
    print(f"  delta={delta:.2f} → delta_score={bd['delta_score']} total={score:.3f}  ({desc})")

print()
print("=== rank_strategies 含单腿（prior=1.0）===")
strategies = [
    make_strategy("long_call",   delta=0.50, net_debit=0.08),
    make_strategy("long_call",   delta=0.25, net_debit=0.05),
    make_strategy("naked_call",  delta=0.22, net_credit=0.03),
    make_strategy("covered_call",delta=0.25, net_credit=0.025),
]
ranked = ranker_module.rank_strategies(strategies)
print("排序结果：")
for s in ranked:
    print(f"  {s.strategy_type:<15} delta={s.legs[0].delta:.2f} score={s.score:.4f}")

print()
print("=== covered_call 年化收益率评分 ===")
# spot=4.0, 手续费0.0003，年化 = (credit-0.0003)/4.0/(dte/360)
cc_cases = [
    # credit,  dte,  预期年化,          预期分
    (0.044,   90,  "≈3.9% → 1.0"),   # (0.044-0.0003)/4.0/(90/360) ≈ 3.9%
    (0.022,   90,  "≈1.9% → 0.4"),   # 太低
    (0.060,   90,  "≈5.4% → 0.85"),  # 略高
    (0.100,   90,  "≈9.0% → 0.65"),  # delta 深
    (0.003,   90,  "≈0.2% → 0.1"),   # 无意义
    (0.044,  180,  "≈1.9% → 0.4"),   # 同 credit 但 DTE 翻倍，年化腰斩
    (0.088,  180,  "≈3.9% → 1.0"),   # DTE 180 需要更多权利金才能达标
]
for credit, dte, desc in cc_cases:
    s = make_strategy("covered_call", delta=0.20, net_credit=credit, spot=4.0)
    s.legs[0].dte = dte
    score, bd = ranker_module._score_single_leg(s)
    ann = (credit - 0.0003) / 4.0 / (dte / 360)
    print(f"  credit={credit:.3f} dte={dte:3d} 年化={ann:.1%} → delta_score={bd['delta_score']} total={score:.3f}  {desc}")