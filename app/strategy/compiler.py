from __future__ import annotations

from typing import List

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
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min,
                        dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="next_expiry",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36,
                        dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="sell near call, buy far call",
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
        near_dte_min = max(10, min(intent.dte_min, 35))
        near_dte_max = max(35, intent.dte_max)

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
                    leg_constraints=LegConstraint(
                        dte_min=near_dte_min,
                        dte_max=near_dte_max,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="PUT",
                    expiry_rule="next_expiry",
                    strike=None,
                    delta_target=None,
                    quantity=1,
                    leg_constraints=LegConstraint(
                        dte_min=36,
                        dte_max=120,
                        max_rel_spread=intent.max_rel_spread,
                        min_quote_size=intent.min_quote_size,
                    ),
                ),
            ],
            constraints=common_constraints,
            rationale="sell near put, buy far put",
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
    if strategy_type == "diagonal_call":
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="CALL", expiry_rule="next_expiry", delta_target=0.5),
            ],
            constraints=common_constraints,
            rationale="call diagonal",
            metadata={},
        )

    if strategy_type == "diagonal_put":
        return StrategySpec(
            strategy_type="diagonal_put",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="PUT", expiry_rule="next_expiry", delta_target=0.5),
            ],
            constraints=common_constraints,
            rationale="put diagonal",
            metadata={},
        )

    # ===== vertical =====
    if strategy_type == "bull_call_spread":
        return StrategySpec(
            strategy_type="bull_call_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="BUY", option_type="CALL", expiry_rule="nearest", delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="same_expiry", delta_target=0.3),
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
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="CALL", expiry_rule="same_expiry", delta_target=0.15),
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
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="PUT", expiry_rule="same_expiry", delta_target=0.15),
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
                StrategyLegSpec(action="BUY", option_type="PUT", expiry_rule="nearest", delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="same_expiry", delta_target=0.3),
            ],
            constraints=common_constraints,
            rationale="bear put spread",
            metadata={},
        )

    # ===== condor =====
    if strategy_type == "iron_condor":
        return StrategySpec(
            strategy_type="iron_condor",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="CALL", expiry_rule="same_expiry", delta_target=0.15),
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest", delta_target=0.3),
                StrategyLegSpec(action="BUY", option_type="PUT", expiry_rule="same_expiry", delta_target=0.15),
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
                StrategyLegSpec(action="SELL", option_type="CALL", expiry_rule="nearest", delta_target=0.5),
                StrategyLegSpec(action="SELL", option_type="PUT", expiry_rule="nearest", delta_target=0.5),
                StrategyLegSpec(action="BUY", option_type="CALL", expiry_rule="same_expiry", delta_target=0.2),
                StrategyLegSpec(action="BUY", option_type="PUT", expiry_rule="same_expiry", delta_target=0.2),
            ],
            constraints=common_constraints,
            rationale="iron fly",
            metadata={},
        )

    return None


# ==============================
# main compiler
# ==============================

def compile_intent_to_strategies(intent: IntentSpec) -> List[StrategySpec]:
    candidates: List[tuple[str, float]] = []

    if intent.vol_view == "call_iv_rich":
        candidates += [
            ("call_calendar", 1.0),
            ("diagonal_call", 0.9),
            ("bear_call_spread", 0.75),
            ("bull_call_spread", 0.7),
        ]
    elif intent.vol_view == "put_iv_rich":
        candidates += [
            ("put_calendar", 1.0),
            ("diagonal_put", 0.9),
            ("bear_put_spread", 0.75),
            ("bull_put_spread", 0.7),
        ]
    elif intent.vol_view == "iv_high":
        candidates += [
            ("iron_condor", 0.95),
            ("iron_fly", 0.9),
            ("call_calendar", 0.75),
            ("put_calendar", 0.75),
            ("bear_call_spread", 0.7),
            ("bull_put_spread", 0.7),
        ]

    if intent.market_view == "bullish":
        candidates += [
            ("bull_call_spread", 0.9),
            ("bull_put_spread", 0.85),
        ]
    elif intent.market_view == "bearish":
        candidates += [
            ("bear_call_spread", 0.9),
            ("bear_put_spread", 0.85),
        ]
    else:
        candidates += [
            ("iron_condor", 0.85),
            ("iron_fly", 0.8),
        ]

    if intent.prefer_multi_leg:
        candidates += [
            ("call_calendar", 0.9),
            ("put_calendar", 0.9),
        ]

    best_map = {}
    for s, w in candidates:
        if s not in best_map or w > best_map[s]:
            best_map[s] = w

    specs: List[StrategySpec] = []
    for strategy_type, weight in best_map.items():
        spec = build_strategy_spec(strategy_type, intent)
        if spec is None:
            continue

        spec.metadata = spec.metadata or {}
        spec.metadata["prior_weight"] = weight
        specs.append(spec)

    return specs