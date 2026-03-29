"""
compiler.py — 策略意图编译器

职责：
  1. build_strategy_spec()     根据策略类型 + IntentSpec 构建 StrategySpec（腿定义）
  2. compile_intent_to_strategies()  根据 IntentSpec 生成候选策略列表，并写入 prior_weight

prior_weight 控制逻辑（唯一入口，prior_engine.py 已废弃）：
  - 第一层：vol_view  → 决定哪些策略进候选池、初始权重
  - 第二层：market_view → 方向策略叠加
  - 第三层：prefer_multi_leg → calendar/diagonal 额外加权
  - 第四层：best_map 后置修正 → 交叉信号压制、defined_risk_only 清零
  - best_map 全程取同策略最高权重，后置修正可强制覆盖
"""

from __future__ import annotations

from typing import Dict, List

from app.models.schemas import (
    IntentSpec,
    LegConstraint,
    StrategyConstraint,
    StrategyLegSpec,
    StrategySpec,
)


# ==============================
# strategy spec factory
# ==============================

def build_strategy_spec(strategy_type: str, intent: IntentSpec) -> StrategySpec | None:
    underlying_id = intent.underlying_id

    common_constraints = StrategyConstraint(
        defined_risk_only=intent.defined_risk_only,
        dte_min=intent.dte_min,
        dte_max=intent.dte_max,
        max_rel_spread=intent.max_rel_spread,
        min_quote_size=intent.min_quote_size,
    )

    near_dte_min = max(10, min(intent.dte_min, 35))
    near_dte_max = max(near_dte_min, min(intent.dte_max, 35))
    near_lc = LegConstraint(
        dte_min=near_dte_min,
        dte_max=near_dte_max,
        max_rel_spread=intent.max_rel_spread,
        min_quote_size=intent.min_quote_size,
    )
    far_lc = LegConstraint(
        dte_min=36,
        dte_max=120,
        max_rel_spread=intent.max_rel_spread,
        min_quote_size=intent.min_quote_size,
    )

    # ===== calendar =====
    if strategy_type == "call_calendar":
        return StrategySpec(
            strategy_type="call_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=near_lc,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="next_expiry",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=far_lc,
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 call calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min,
                "near_dte_max": near_dte_max,
                "far_dte_min": 36,
                "far_dte_max": 120,
                "atm_moneyness_low": 0.9,
                "atm_moneyness_high": 1.1,
            },
        )

    if strategy_type == "put_calendar":
        return StrategySpec(
            strategy_type="put_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="PUT",
                    expiry_rule="nearest",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=near_lc,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="PUT",
                    expiry_rule="next_expiry",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=far_lc,
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 put calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min,
                "near_dte_max": near_dte_max,
                "far_dte_min": 36,
                "far_dte_max": 120,
                "atm_moneyness_low": 0.9,
                "atm_moneyness_high": 1.1,
            },
        )

    # ===== diagonal =====
    # 核心策略：卖近月低delta（收theta），买远月高delta（持方向+vega）
    # near leg: SELL，delta ~0.25~0.35（虚值，收权利金）
    # far  leg: BUY，delta ~0.45~0.55（近ATM，持vega敞口）
    # 允许不同 strike，方向性比 calendar 更强
    if strategy_type == "diagonal_call":
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    delta_target=0.30,   # 近月虚值，收theta
                    quantity=1,
                    leg_constraints=near_lc,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="next_expiry",
                    delta_target=0.50,   # 远月ATM，持vega
                    quantity=1,
                    leg_constraints=far_lc,
                ),
            ],
            constraints=common_constraints,
            rationale="call diagonal：卖近月虚值call（delta~0.3），买远月ATM call（delta~0.5），轻度看涨+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.30,
                "far_delta_target": 0.50,
                "near_dte_min": near_dte_min,
                "near_dte_max": near_dte_max,
                "far_dte_min": 36,
                "far_dte_max": 120,
            },
        )

    if strategy_type == "diagonal_put":
        return StrategySpec(
            strategy_type="diagonal_put",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="PUT",
                    expiry_rule="nearest",
                    delta_target=0.30,   # 近月虚值put，收theta（注意resolver用abs(delta)）
                    quantity=1,
                    leg_constraints=near_lc,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="PUT",
                    expiry_rule="next_expiry",
                    delta_target=0.50,   # 远月ATM put，持vega
                    quantity=1,
                    leg_constraints=far_lc,
                ),
            ],
            constraints=common_constraints,
            rationale="put diagonal：卖近月虚值put（delta~0.3），买远月ATM put（delta~0.5），轻度看跌+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.30,
                "far_delta_target": 0.50,
                "near_dte_min": near_dte_min,
                "near_dte_max": near_dte_max,
                "far_dte_min": 36,
                "far_dte_max": 120,
            },
        )

    # ===== vertical spreads =====
    if strategy_type == "bull_call_spread":
        return StrategySpec(
            strategy_type="bull_call_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="nearest",     delta_target=0.50),
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="same_expiry",  delta_target=0.30),
            ],
            constraints=common_constraints,
            rationale="bull call spread",
            metadata={},
        )

    if strategy_type == "bear_call_spread":
        return StrategySpec(
            strategy_type="bear_call_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",     delta_target=0.30),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry",  delta_target=0.15),
            ],
            constraints=common_constraints,
            rationale="bear call spread",
            metadata={},
        )

    if strategy_type == "bull_put_spread":
        return StrategySpec(
            strategy_type="bull_put_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest",     delta_target=0.30),
                StrategyLegSpec(action="BUY",  option_type="PUT", expiry_rule="same_expiry",  delta_target=0.15),
            ],
            constraints=common_constraints,
            rationale="bull put spread",
            metadata={},
        )

    if strategy_type == "bear_put_spread":
        return StrategySpec(
            strategy_type="bear_put_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="BUY",  option_type="PUT", expiry_rule="nearest",     delta_target=0.50),
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="same_expiry",  delta_target=0.30),
            ],
            constraints=common_constraints,
            rationale="bear put spread",
            metadata={},
        )

    # ===== iron structures =====
    if strategy_type == "iron_condor":
        return StrategySpec(
            strategy_type="iron_condor",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.30),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.15),
                StrategyLegSpec(action="SELL", option_type="PUT",  expiry_rule="nearest",    delta_target=0.30),
                StrategyLegSpec(action="BUY",  option_type="PUT",  expiry_rule="same_expiry", delta_target=0.15),
            ],
            constraints=common_constraints,
            rationale="iron condor",
            metadata={},
        )

    if strategy_type == "iron_fly":
        return StrategySpec(
            strategy_type="iron_fly",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.50),
                StrategyLegSpec(action="SELL", option_type="PUT",  expiry_rule="nearest",    delta_target=0.50),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.20),
                StrategyLegSpec(action="BUY",  option_type="PUT",  expiry_rule="same_expiry", delta_target=0.20),
            ],
            constraints=common_constraints,
            rationale="iron fly",
            metadata={},
        )

    return None


# ==============================
# prior weight 控制逻辑
# ==============================

def _build_prior_map(intent: IntentSpec) -> Dict[str, float]:
    """
    唯一的 prior_weight 计算入口。
    返回 {strategy_type: weight}，weight ∈ [0, 1]。

    设计原则：
      - 第一层 vol_view：决定核心候选池和基础权重
      - 第二层 market_view：方向策略叠加（bullish/bearish/neutral）
      - 第三层 prefer_multi_leg：跨期结构额外奖励
      - 第四层 后置修正：交叉信号压制（如 call_iv_rich 时 iron 降权）
      - 第五层 defined_risk_only：裸腿清零（当前无裸策略，保留扩展）
      - best_map 取最高值；后置修正强制覆盖（bypass best_map）
    """
    candidates: List[tuple[str, float]] = []

    # ===== 第一层：vol_view =====
    if intent.vol_view == "call_iv_rich":
        # 近月call IV偏贵 → calendar/diagonal是首选，vertical次之
        candidates += [
            ("call_calendar",   1.00),
            ("diagonal_call",   0.90),
            ("bear_call_spread", 0.75),
            ("bull_call_spread", 0.65),
        ]

    elif intent.vol_view == "put_iv_rich":
        candidates += [
            ("put_calendar",    1.00),
            ("diagonal_put",    0.90),
            ("bear_put_spread",  0.75),
            ("bull_put_spread",  0.65),
        ]

    elif intent.vol_view == "term_front_high":
        # 近月整体IV偏高（call+put）→ 两种calendar都合适
        candidates += [
            ("call_calendar",  0.95),
            ("put_calendar",   0.95),
            ("diagonal_call",  0.80),
            ("diagonal_put",   0.80),
        ]

    elif intent.vol_view == "term_back_high":
        # 远月IV偏高 → calendar方向不对，不推荐
        # 这里不加calendar，只加方向策略兜底
        pass

    elif intent.vol_view == "iv_high":
        # 整体IV高但无方向偏好 → iron结构优先，calendar次之
        candidates += [
            ("iron_condor",    0.90),
            ("iron_fly",       0.85),
            ("call_calendar",  0.70),
            ("put_calendar",   0.70),
        ]

    # vol_view == "none" 或其他：不加vol_view候选，由market_view兜底

    # ===== 第二层：market_view =====
    if intent.market_view == "bullish":
        candidates += [
            ("bull_call_spread", 0.90),
            ("bull_put_spread",  0.85),
            ("diagonal_call",    0.80),  # diagonal看涨方向合适
        ]
    elif intent.market_view == "bearish":
        candidates += [
            ("bear_call_spread", 0.90),
            ("bear_put_spread",  0.85),
            ("diagonal_put",     0.80),
        ]
    else:  # neutral
        # neutral + 非iv_rich场景才推iron
        if intent.vol_view not in ("call_iv_rich", "put_iv_rich"):
            candidates += [
                ("iron_condor", 0.85),
                ("iron_fly",    0.80),
            ]
        else:
            # vol_view已明确 call/put_iv_rich → iron信号不对口，低权进池
            candidates += [
                ("iron_condor", 0.30),
                ("iron_fly",    0.25),
            ]

    # ===== 第三层：prefer_multi_leg =====
    if intent.prefer_multi_leg:
        # 跨期结构额外奖励
        candidates += [
            ("call_calendar",  0.90),
            ("put_calendar",   0.90),
            ("diagonal_call",  0.85),
            ("diagonal_put",   0.85),
        ]

    # ===== best_map：取同策略最高权重 =====
    best_map: Dict[str, float] = {}
    for s, w in candidates:
        if s not in best_map or w > best_map[s]:
            best_map[s] = w

    # ===== 第四层：后置修正（强制覆盖，bypass best_map）=====

    # 修正1：call_iv_rich + prefer_multi_leg → iron 进一步压低
    if intent.vol_view in ("call_iv_rich", "put_iv_rich") and intent.prefer_multi_leg:
        for k in ("iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(best_map[k], 0.25)

    # 修正2：bullish 时 put_calendar 意义不大，降权
    if intent.market_view == "bullish" and "put_calendar" in best_map:
        best_map["put_calendar"] = min(best_map["put_calendar"], 0.50)

    # 修正3：bearish 时 call_calendar 意义不大，降权
    if intent.market_view == "bearish" and "call_calendar" in best_map:
        best_map["call_calendar"] = min(best_map["call_calendar"], 0.50)

    # ===== 第五层：defined_risk_only 清零裸腿 =====
    if intent.defined_risk_only:
        for k in ("short_call", "short_put", "naked_call", "naked_put"):
            best_map.pop(k, None)

    # clamp 到 [0, 1]
    return {k: max(0.0, min(1.0, v)) for k, v in best_map.items()}


# ==============================
# main compiler
# ==============================

def compile_intent_to_strategies(intent: IntentSpec) -> List[StrategySpec]:
    prior_map = _build_prior_map(intent)

    specs: List[StrategySpec] = []
    for strategy_type, weight in prior_map.items():
        spec = build_strategy_spec(strategy_type, intent)
        if spec is None:
            continue
        spec.metadata = spec.metadata or {}
        spec.metadata["prior_weight"] = weight
        specs.append(spec)

    return specs