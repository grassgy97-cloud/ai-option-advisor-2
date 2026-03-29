# test_ranker.py
from app.strategy.strategy_ranker import (
    _calc_calendar_signal_score,
    _score_iron_structure,
)
import app.strategy.strategy_ranker as ranker_module
from unittest.mock import patch
from app.models.schemas import ResolvedStrategy

# ===== 测试1：calendar signal_score =====
print("=== calendar signal_score ===")
cases = [
    (-0.02,  "应得1.0，near明显更贵"),
    (-0.008, "应得0.8"),
    (-0.003, "应得0.6"),
    (0.003,  "应得0.3，基本持平"),
    (0.01,   "应得0.0，far更贵，不该做"),
    (None,   "应得0.0"),
]
for iv_diff, desc in cases:
    score = _calc_calendar_signal_score(iv_diff)
    iv_str = f"{iv_diff:>7}" if iv_diff is not None else "   None"
    print(f"  iv_diff={iv_str} → score={score:.1f}  ({desc})")

print()

# ===== 测试2：iron structure vega惩罚 =====
# 构造一个假的 ResolvedStrategy，net_vega 极度负
from app.models.schemas import ResolvedStrategy, ResolvedLeg
from unittest.mock import patch

# mock compute_strategy_net_greeks 返回不同的Greeks组合
import app.strategy.strategy_ranker as ranker_module

print("=== iron structure Greeks惩罚 ===")

greek_cases = [
    ({"net_delta": 0.02, "net_gamma": -0.8, "net_theta": 0.05, "net_vega": -0.03},  "健康condor，应得高分"),
    ({"net_delta": 0.02, "net_gamma": -2.5, "net_theta": 0.05, "net_vega": -0.03},  "gamma过度short，应降分"),
    ({"net_delta": 0.02, "net_gamma": -0.8, "net_theta": 0.05, "net_vega": -0.40},  "vega过度short，应降分"),
    ({"net_delta": 0.02, "net_gamma": -2.5, "net_theta": 0.05, "net_vega": -0.40},  "gamma+vega都过度，应大幅降分"),
]

dummy_strategy = ResolvedStrategy(
    strategy_type="iron_condor",
    underlying_id="510300",
    spot_price=4.0,      # ✅ 补上
    net_premium=0.0,     # ✅ 补上
    legs=[],
    metadata={},
)

for greeks, desc in greek_cases:
    with patch.object(ranker_module, "compute_strategy_net_greeks", return_value=greeks):
        score, breakdown = ranker_module._score_iron_structure(dummy_strategy)
    print(f"  {desc}")
    print(f"    signal={breakdown['signal_score']}  vega={breakdown.get('vega_score','N/A')}  gamma={breakdown['gamma_score']}  → total={score:.4f}")