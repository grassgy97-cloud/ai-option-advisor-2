from typing import Dict, List, Tuple
from app.models.schemas import IntentSpec


def build_strategy_priors(intent: IntentSpec) -> List[Tuple[str, float]]:
    """
    输出：
    [
        ("call_calendar", 0.8),
        ("bear_call_spread", 0.6),
        ...
    ]
    """

    priors: Dict[str, float] = {}

    # ===== 1. 波动率逻辑 =====
    if intent.vol_view == "call_iv_rich":
        priors["call_calendar"] = 1.0  # ✅ 提高（原0.8）
        priors["diagonal_call"] = 0.7  # ✅ 提高（原0.6）
        priors["bear_call_spread"] = 0.7
        # ✅ 新增：call_iv_rich 时 iron 结构信号不对口，主动压低
        priors["iron_condor"] = 0.3
        priors["iron_fly"] = 0.3

    elif intent.vol_view == "put_iv_rich":
        priors["put_calendar"] = 1.0  # ✅ 提高（原0.8）
        priors["diagonal_put"] = 0.7  # ✅ 提高（原0.6）
        priors["bull_put_spread"] = 0.7
        # ✅ 新增：put_iv_rich 时 iron 结构信号不对口，主动压低
        priors["iron_condor"] = 0.3
        priors["iron_fly"] = 0.3

    elif intent.vol_view == "term_front_high":
        priors["call_calendar"] = 0.9
        priors["put_calendar"] = 0.9

    elif intent.vol_view == "term_back_high":
        priors["reverse_calendar"] = 0.7

    # ===== 2. 方向修正 =====
    if intent.market_view == "bullish":
        priors["bull_call_spread"] = priors.get("bull_call_spread", 0.6) + 0.2
        priors["diagonal_call"] = priors.get("diagonal_call", 0.5) + 0.2

    elif intent.market_view == "bearish":
        priors["bear_put_spread"] = priors.get("bear_put_spread", 0.6) + 0.2
        priors["diagonal_put"] = priors.get("diagonal_put", 0.5) + 0.2


    elif intent.market_view == "neutral":
        if intent.vol_view not in ("call_iv_rich", "put_iv_rich"):
            priors["iron_condor"] = priors.get("iron_condor", 0.5) + 0.2
        else:
            # vol_view 已对 iron 有负面信号，维持压低，不叠加 neutral 加成
            priors["iron_condor"] = priors.get("iron_condor", 0.3)

    # ===== 3. 多腿偏好（关键）=====
    if intent.prefer_multi_leg:
        for k in list(priors.keys()):
            if "spread" in k or "calendar" in k or "diagonal" in k:
                priors[k] += 0.1
            else:
                priors[k] -= 0.2

    # ===== 4. 风险约束 =====
    if intent.defined_risk_only:
        for k in list(priors.keys()):
            if "naked" in k or "short_call" in k or "short_put" in k:
                priors[k] = 0.0

    # ===== 5. 清洗 =====
    out = [(k, max(0.0, min(v, 1.0))) for k, v in priors.items()]

    # 排序（只是compile顺序）
    out.sort(key=lambda x: x[1], reverse=True)

    return out