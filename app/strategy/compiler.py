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
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
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
        return StrategySpec(
            strategy_type="diagonal_put",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.3, quantity=1,
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
        return StrategySpec(
            strategy_type="bull_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="same_expiry", delta_target=0.3),
            ],
            constraints=common_constraints, rationale="bull call spread", metadata={},
        )

    if strategy_type == "bear_call_spread":
        return StrategySpec(
            strategy_type="bear_call_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.3),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.15),
            ],
            constraints=common_constraints, rationale="bear call spread", metadata={},
        )

    if strategy_type == "bull_put_spread":
        return StrategySpec(
            strategy_type="bull_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest",    delta_target=0.3),
                StrategyLegSpec(action="BUY",  option_type="PUT", expiry_rule="same_expiry", delta_target=0.15),
            ],
            constraints=common_constraints, rationale="bull put spread", metadata={},
        )

    if strategy_type == "bear_put_spread":
        return StrategySpec(
            strategy_type="bear_put_spread", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="BUY",  option_type="PUT", expiry_rule="nearest",    delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="same_expiry", delta_target=0.3),
            ],
            constraints=common_constraints, rationale="bear put spread", metadata={},
        )

    # ===== condor =====
    if strategy_type == "iron_condor":
        return StrategySpec(
            strategy_type="iron_condor", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest",    delta_target=0.3),
                StrategyLegSpec(action="BUY",  option_type="CALL", expiry_rule="same_expiry", delta_target=0.15),
                StrategyLegSpec(action="SELL", option_type="PUT",  expiry_rule="nearest",    delta_target=0.3),
                StrategyLegSpec(action="BUY",  option_type="PUT",  expiry_rule="same_expiry", delta_target=0.15),
            ],
            constraints=common_constraints, rationale="iron condor", metadata={},
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
            constraints=common_constraints, rationale="iron fly", metadata={},
        )

    # ===== 新策略：单腿买方 =====

    if strategy_type == "long_call":
        return StrategySpec(
            strategy_type="long_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.30, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月虚值call（delta~0.3），以较低权利金博弹性",
            metadata={"selection_mode": "long_single"},
        )

    if strategy_type == "long_put":
        return StrategySpec(
            strategy_type="long_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="BUY", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.30, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=45, dte_max=90,
                        max_rel_spread=0.04, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="IV极低时买远月虚值put（delta~0.3），以较低权利金博下行保护",
            metadata={"selection_mode": "long_single"},
        )

    # ===== 新策略：单腿卖方 =====

    if strategy_type == "naked_call":
        return StrategySpec(
            strategy_type="naked_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.22, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值call（delta~0.22），收theta，适合IV偏高+中性偏空市场",
            metadata={"selection_mode": "naked_single"},
        )

    if strategy_type == "naked_put":
        return StrategySpec(
            strategy_type="naked_put", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="PUT", expiry_rule="nearest",
                    strike=None, delta_target=0.22, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="卖虚值put（delta~0.22），收theta，适合IV偏高+中性偏多市场",
            metadata={"selection_mode": "naked_single"},
        )

    if strategy_type == "covered_call":
        return StrategySpec(
            strategy_type="covered_call", underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL", option_type="CALL", expiry_rule="nearest",
                    strike=None, delta_target=0.25, quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=10, dte_max=35,
                        max_rel_spread=0.03, min_quote_size=1,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="备兑卖出虚值call（delta~0.25），持有标的基础上增强收益",
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
            ("iron_condor",      0.95),
            ("iron_fly",         0.90),
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
            ("bull_call_spread", 0.9),
            ("bull_put_spread",  0.85),
            ("naked_put",        0.65),
        ]
    elif intent.market_view == "bearish":
        candidates += [
            ("bear_call_spread", 0.9),
            ("bear_put_spread",  0.85),
            ("naked_call",       0.65),
        ]
    else:  # neutral
        candidates += [
            ("iron_condor",  0.85),
            ("iron_fly",     0.80),
            ("covered_call", 0.60),
        ]

    # ===== prefer_multi_leg 驱动 =====
    if intent.prefer_multi_leg:
        candidates += [
            ("call_calendar", 0.9),
            ("put_calendar",  0.9),
            ("diagonal_call", 0.85),
            ("diagonal_put",  0.85),
        ]

    # ===== best_map：各策略取最高prior =====
    best_map: dict[str, float] = {}
    for s, w in candidates:
        if s not in best_map or w > best_map[s]:
            best_map[s] = w

    # ===== call/put_iv_rich 时强制压低 iron prior（不受 market_view 影响）=====
    if intent.vol_view in ("call_iv_rich", "put_iv_rich"):
        for k in ("iron_condor", "iron_fly"):
            if k in best_map:
                best_map[k] = min(best_map[k], 0.25)

    # ===== IV percentile 驱动调整 =====
    if iv_pct is not None:
        if iv_pct <= 0.15:
            # IV极低：压制卖方，激活买单边
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "bear_put_spread", "bull_call_spread",
                      "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(best_map[k], 0.20)
            best_map["long_call"] = max(best_map.get("long_call", 0), 0.85)
            best_map["long_put"]  = max(best_map.get("long_put",  0), 0.85)

        elif iv_pct <= 0.30:
            # IV偏低：iron和裸卖降权
            for k in ("iron_condor", "iron_fly", "naked_call", "naked_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.7, 3)

        elif iv_pct >= 0.85:
            # IV极高：卖方大幅加权，买单边移除
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.4, 3))
            best_map.pop("long_call", None)
            best_map.pop("long_put", None)

        elif iv_pct >= 0.70:
            # IV偏高：卖方加权，买单边降权
            for k in ("iron_condor", "iron_fly", "bear_call_spread",
                      "bull_put_spread", "naked_call", "naked_put", "covered_call"):
                if k in best_map:
                    best_map[k] = min(1.0, round(best_map[k] * 1.2, 3))
            for k in ("long_call", "long_put"):
                if k in best_map:
                    best_map[k] = round(best_map[k] * 0.5, 3)

    # ===== banned_strategies 过滤 =====
    for banned in (intent.banned_strategies or []):
        best_map.pop(banned, None)

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