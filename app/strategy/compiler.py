from __future__ import annotations

from typing import List, Optional

from app.models.schemas import (
    IntentSpec,
    StrategySpec,
    StrategyLegSpec,
    StrategyConstraint,
    LegConstraint,
)


# ==============================
# 辅助：从price_levels提取strike_pct_target
# ==============================

def _get_support(intent: IntentSpec) -> Optional[float]:
    """下方支撑/保底位，负数百分比"""
    return intent.price_levels.get("support")

def _get_resistance(intent: IntentSpec) -> Optional[float]:
    """上方压力位，正数百分比"""
    return intent.price_levels.get("resistance")

def _get_target(intent: IntentSpec) -> Optional[float]:
    """目标价位，正负均可"""
    return intent.price_levels.get("target")


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

    support = _get_support(intent)
    resistance = _get_resistance(intent)
    target = _get_target(intent)

    # ===== calendar =====
    if strategy_type == "call_calendar":
        near_dte_min = max(10, min(intent.dte_min, 35))
        near_dte_max = max(35, intent.dte_max)
        return StrategySpec(
            strategy_type="call_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min, dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="next_expiry",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 call calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min, "near_dte_max": near_dte_max,
                "far_dte_min": 36, "far_dte_max": 120,
                "atm_moneyness_low": 0.9, "atm_moneyness_high": 1.1,
            },
        )

    if strategy_type == "put_calendar":
        near_dte_min = max(10, min(intent.dte_min, 35))
        near_dte_max = max(35, intent.dte_max)
        return StrategySpec(
            strategy_type="put_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min, dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="next_expiry",
                    strike=None, delta_target=None, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖近买远 put calendar（sell near / buy far，同strike ATM）",
            metadata={
                "selection_mode": "atm_like_same_strike_calendar",
                "near_dte_min": near_dte_min, "near_dte_max": near_dte_max,
                "far_dte_min": 36, "far_dte_max": 120,
                "atm_moneyness_low": 0.9, "atm_moneyness_high": 1.1,
            },
        )

    # ===== diagonal =====
    if strategy_type == "diagonal_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="next_expiry",
                    strike=None, delta_target=0.5, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="call diagonal：卖近月虚值call（delta~0.3），买远月ATM call（delta~0.5），轻度看涨+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.3, "far_delta_target": 0.5,
                "near_dte_min": 10, "near_dte_max": 35,
                "far_dte_min": 36, "far_dte_max": 120,
            },
        )

    if strategy_type == "diagonal_put":
        short_pct = support
        return StrategySpec(
            strategy_type="diagonal_put",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="next_expiry",
                    strike=None, delta_target=0.5, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36, dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="put diagonal：卖近月虚值put（delta~0.3），买远月ATM put（delta~0.5），轻度看跌+收theta",
            metadata={
                "selection_mode": "diagonal",
                "near_delta_target": 0.3, "far_delta_target": 0.5,
                "near_dte_min": 10, "near_dte_max": 35,
                "far_dte_min": 36, "far_dte_max": 120,
            },
        )

    # ===== vertical =====
    if strategy_type == "bull_call_spread":
        buy_pct = target
        return StrategySpec(
            strategy_type="bull_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.5,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.3,
                    strike_pct_target=resistance,
                    strike_forced=(resistance is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bull call spread（debit，买平值卖虚值）", metadata={},
        )

    if strategy_type == "bear_call_spread":
        short_pct = resistance
        return StrategySpec(
            strategy_type="bear_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
            ],
            constraints=common_constraints,
            rationale="bear call spread（credit，卖虚值买更虚值）", metadata={},
        )

    if strategy_type == "bull_put_spread":
        buy_pct = support
        return StrategySpec(
            strategy_type="bull_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.3,
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.15,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bull put spread（credit，卖虚值买更虚值）", metadata={},
        )

    if strategy_type == "bear_put_spread":
        sell_pct = support
        buy_pct = target
        return StrategySpec(
            strategy_type="bear_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.5,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                ),
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.3,
                    strike_pct_target=sell_pct,
                    strike_forced=(sell_pct is not None),
                ),
            ],
            constraints=common_constraints,
            rationale="bear put spread（debit，买平值卖虚值）", metadata={},
        )

    # ===== condor =====
    if strategy_type == "iron_condor":
        return StrategySpec(
            strategy_type="iron_condor", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=resistance,
                    strike_forced=(resistance is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    delta_target=0.3,
                    strike_pct_target=support,
                    strike_forced=(support is not None),
                ),
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="same_expiry",
                    delta_target=0.15,
                ),
            ],
            constraints=common_constraints,
            rationale="iron condor（IV高+中性，卖双侧虚值）", metadata={},
        )

    if strategy_type == "iron_fly":
        return StrategySpec(
            strategy_type="iron_fly", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="PUT",  expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.2),
                StrategyLegSpec(action="BUY",  option_type="PUT",  expiry_rule="same_expiry", delta_target=0.2),
            ],
            constraints=common_constraints,
            rationale="iron fly（IV高+极度中性，卖平值双侧）", metadata={},
        )

    # ===== 单腿买方 =====
    if strategy_type == "long_call":
        buy_pct = target if target and target > 0 else None
        return StrategySpec(
            strategy_type="long_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.50, quantity=1,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月平值偏虚call（delta~0.5），持有方向性敞口",
            metadata={"selection_mode": "long_single"},
        )

    if strategy_type == "long_put":
        buy_pct = target if target and target < 0 else None
        return StrategySpec(
            strategy_type="long_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.50, quantity=1,
                    strike_pct_target=buy_pct,
                    strike_forced=(buy_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月平值偏虚put（delta~0.5），持有方向性敞口",
            metadata={"selection_mode": "long_single"},
        )

    # ===== 单腿卖方 =====
    if strategy_type == "naked_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="naked_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.18, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值call（delta~0.18），收theta，适合IV偏高+中性偏空市场",
            metadata={"selection_mode": "naked_single"},
        )

    if strategy_type == "naked_put":
        short_pct = support
        return StrategySpec(
            strategy_type="naked_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.18, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值put（delta~0.18），收theta，适合IV偏高+中性偏多市场",
            metadata={"selection_mode": "naked_single"},
        )

    if strategy_type == "covered_call":
        short_pct = resistance
        return StrategySpec(
            strategy_type="covered_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.20, quantity=1,
                    strike_pct_target=short_pct,
                    strike_forced=(short_pct is not None),
                    leg_constraints=LegConstraint(
                        dte_min=60, dte_max=180,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="备兑卖出虚值call（DTE 60-180天），目标年化收益率3-5%",
            metadata={"selection_mode": "covered_call"},
        )

    return None


# ==============================
# main compiler
# ==============================

def compile_intent_to_strategies(
    intent: IntentSpec,
    iv_pct: Optional[float] = None,
) -> List[StrategySpec]:
    candidates: List[tuple[str, float]] = []

    # ===== vol_view 驱动 =====
    if intent.vol_view == "call_iv_rich":
        candidates += [
            ("call_calendar",    1.0),
            ("diagonal_call",    0.9),
            ("diagonal_put",     0.85),
            ("put_calendar",     0.90),
            ("bear_call_spread", 0.75),
            ("bull_call_spread", 0.7),
        ]
    elif intent.vol_view == "put_iv_rich":
        candidates += [
            ("put_calendar",    1.0),
            ("diagonal_put",    0.9),
            ("diagonal_call",   0.85),
            ("call_calendar",   0.90),
            ("bear_put_spread", 0.75),
            ("bull_put_spread", 0.7),
        ]
    elif intent.vol_view == "iv_high":
        candidates += [
            ("iron_condor",      0.80),
            ("iron_fly",         0.75),
            ("call_calendar",    0.75),
            ("put_calendar",     0.75),
            ("bear_call_spread", 0.70),
            ("bull_put_spread",  0.70),
            ("naked_call",       0.70),
            ("naked_put",        0.70),
            ("covered_call",     0.65),
        ]

    # ===== market_view 驱动 =====
    if intent.market_view == "bullish":
        candidates += [
            ("diagonal_call",    0.95),
            ("bull_call_spread", 0.70),
            ("bull_put_spread",  0.65),
            ("naked_put",        0.50),
        ]
    elif intent.market_view == "bearish":
        candidates += [
            ("bear_call_spread", 0.9),
            ("bear_put_spread",  0.85),
            ("naked_call",       0.65),
            ("long_put",         0.70),
        ]
    else:  # neutral
        candidates += [
            ("iron_condor",  0.70),
            ("iron_fly",     0.65),
            ("covered_call", 0.60),
            ("naked_put",    0.65),
        ]

    # ===== asymmetry 驱动 =====
    if intent.asymmetry == "downside":
        candidates += [
            ("bear_put_spread", 0.80),
            ("long_put",        0.65),
            ("diagonal_put",    0.75),
        ]
    elif intent.asymmetry == "upside":
        candidates += [
            ("bull_call_spread", 0.80),
            ("long_call",        0.65),
            ("diagonal_call",    0.75),
        ]
    elif intent.asymmetry == "symmetric":
        candidates += [
            ("long_call", 0.70),
            ("long_put",  0.70),
        ]

    # ===== prefer_multi_leg 驱动 =====
    if intent.prefer_multi_leg:
        candidates += [
            ("diagonal_call", 0.80),
            ("diagonal_put",  0.80),
        ]

    # ===== best_map：各策略取最高prior =====
    best_map: dict[str, float] = {}
    for s, w in candidates:
        if s not in best_map or w > best_map[s]:
            best_map[s] = w

    # ===== call/put_iv_rich 时强制压低 iron、covered_call、买方debit spread =====
    if intent.vol_view in ("call_iv_rich", "put_iv_rich"):
        for k in ("iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(best_map[k], 0.25)
        if "covered_call" in best_map:
            best_map["covered_call"] = min(best_map["covered_call"], 0.45)
        if intent.vol_view == "call_iv_rich":
            if "bull_call_spread" in best_map:
                best_map["bull_call_spread"] = min(best_map["bull_call_spread"], 0.40)
        if intent.vol_view == "put_iv_rich":
            if "bear_put_spread" in best_map:
                best_map["bear_put_spread"] = min(best_map["bear_put_spread"], 0.40)

    # ===== IV percentile 驱动调整 =====
    if iv_pct is not None:
        if iv_pct <= 0.15:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(best_map[k], 0.20)
            best_map["long_call"] = max(best_map.get("long_call", 0), 0.85)
            best_map["long_put"]  = max(best_map.get("long_put",  0), 0.85)

        elif iv_pct <= 0.30:
            for k in ("iron_condor", "iron_fly", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.7, 3)

        elif iv_pct >= 0.85:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.4, 3))
            best_map.pop("long_call", None)
            best_map.pop("long_put", None)

        elif iv_pct >= 0.70:
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.2, 3))
            for k in ("long_call", "long_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.5, 3)

    # ===== allowed_strategies 提权 =====
    if intent.allowed_strategies:
        for k in intent.allowed_strategies:
            if k in best_map:
                best_map[k] = max(best_map[k], 0.90)
            else:
                best_map[k] = 0.90

    # ===== banned_strategies 过滤 =====
    for banned in (intent.banned_strategies or []):
        best_map.pop(banned, None)

    # ===== defined_risk_only 过滤 =====
    if intent.defined_risk_only:
        for k in ("naked_call", "naked_put"):
            best_map.pop(k, None)

    # ===== 构建 StrategySpec 列表 =====
    specs: List[StrategySpec] = []
    for strategy_type, weight in best_map.items():
        spec = build_strategy_spec(strategy_type, intent)
        if spec is None:
            continue
        spec.metadata = spec.metadata or {}
        spec.metadata["prior_weight"] = weight
        specs.append(spec)

    return specs