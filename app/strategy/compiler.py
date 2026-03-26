from __future__ import annotations

from typing import List

from app.models.schemas import (
    IntentSpec,
    LegConstraint,
    StrategyConstraint,
    StrategyLegSpec,
    StrategySpec,
)


def compile_intent_to_strategies(intent: IntentSpec) -> List[StrategySpec]:
    """
    纯规则版 compiler：
    把用户观点映射成候选 StrategySpec。

    设计原则：
    1. vertical spread：继续用 delta_target 选腿
    2. calendar：不再依赖 delta_target，而是交给 resolver 按“近ATM + 同strike”处理
    """

    base_constraints = StrategyConstraint(
        defined_risk_only=intent.defined_risk_only,
        dte_min=intent.dte_min,
        dte_max=intent.dte_max,
        max_rel_spread=intent.max_rel_spread,
        min_quote_size=intent.min_quote_size,
    )

    candidates: List[StrategySpec] = []

    allowed = set(intent.allowed_strategies or [])
    banned = set(intent.banned_strategies or [])

    def _is_allowed(strategy_type: str) -> bool:
        if strategy_type in banned:
            return False
        if allowed and strategy_type not in allowed:
            return False
        return True

    def _build_calendar_metadata(option_type: str) -> dict:
        near_dte_min = intent.dte_min
        near_dte_max = intent.dte_max

        # 远月腿默认比近月更晚，并给足上限
        far_dte_min = max(intent.dte_max + 1, intent.dte_min + 1)
        far_dte_max = max(far_dte_min + 30, 120)

        return {
            "source": "rule_based_compiler",
            "calendar_option_type": option_type,
            "near_dte_min": near_dte_min,
            "near_dte_max": near_dte_max,
            "far_dte_min": far_dte_min,
            "far_dte_max": far_dte_max,
            # 供 resolver 使用的选腿提示
            "selection_mode": "atm_like_same_strike_calendar",
            "atm_moneyness_low": 0.90,
            "atm_moneyness_high": 1.10,
        }

    # 1) call iv 偏贵
    if intent.vol_view == "call_iv_rich":
        if intent.defined_risk_only and _is_allowed("bear_call_spread"):
            candidates.append(
                StrategySpec(
                    strategy_type="bear_call_spread",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="CALL",
                            expiry_rule="nearest",
                            delta_target=0.30,
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="CALL",
                            expiry_rule="same_expiry",
                            delta_target=0.15,
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="认购隐波偏贵，优先考虑定义损失的 bear call spread。",
                    metadata={"source": "rule_based_compiler"},
                )
            )

        if intent.prefer_multi_leg and _is_allowed("call_calendar"):
            cal_meta = _build_calendar_metadata("CALL")
            candidates.append(
                StrategySpec(
                    strategy_type="call_calendar",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="CALL",
                            expiry_rule="nearest",
                            delta_target=None,  # calendar 不靠 delta 选腿
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["near_dte_min"],
                                dte_max=cal_meta["near_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="CALL",
                            expiry_rule="next_expiry",
                            delta_target=None,  # calendar 不靠 delta 选腿
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["far_dte_min"],
                                dte_max=cal_meta["far_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="近月认购相对偏贵，可考虑卖近买远、同strike的 call calendar。",
                    metadata=cal_meta,
                )
            )

    # 2) put iv 偏贵
    if intent.vol_view == "put_iv_rich":
        if intent.defined_risk_only and _is_allowed("bull_put_spread"):
            candidates.append(
                StrategySpec(
                    strategy_type="bull_put_spread",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="PUT",
                            expiry_rule="nearest",
                            delta_target=0.30,
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="PUT",
                            expiry_rule="same_expiry",
                            delta_target=0.15,
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="认沽隐波偏贵，优先考虑定义损失的 bull put spread。",
                    metadata={"source": "rule_based_compiler"},
                )
            )

        if intent.prefer_multi_leg and _is_allowed("put_calendar"):
            cal_meta = _build_calendar_metadata("PUT")
            candidates.append(
                StrategySpec(
                    strategy_type="put_calendar",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="PUT",
                            expiry_rule="nearest",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["near_dte_min"],
                                dte_max=cal_meta["near_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="PUT",
                            expiry_rule="next_expiry",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["far_dte_min"],
                                dte_max=cal_meta["far_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="近月认沽相对偏贵，可考虑卖近买远、同strike的 put calendar。",
                    metadata=cal_meta,
                )
            )

    # 3) term front high：近月波动率高于远月
    if intent.vol_view == "term_front_high":
        if _is_allowed("call_calendar"):
            cal_meta = _build_calendar_metadata("CALL")
            candidates.append(
                StrategySpec(
                    strategy_type="call_calendar",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="CALL",
                            expiry_rule="nearest",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["near_dte_min"],
                                dte_max=cal_meta["near_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="CALL",
                            expiry_rule="next_expiry",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["far_dte_min"],
                                dte_max=cal_meta["far_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="近月波动率高于远月，优先考虑卖近买远、同strike的 call calendar。",
                    metadata=cal_meta,
                )
            )

        if _is_allowed("put_calendar"):
            cal_meta = _build_calendar_metadata("PUT")
            candidates.append(
                StrategySpec(
                    strategy_type="put_calendar",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="PUT",
                            expiry_rule="nearest",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["near_dte_min"],
                                dte_max=cal_meta["near_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="PUT",
                            expiry_rule="next_expiry",
                            delta_target=None,
                            leg_constraints=LegConstraint(
                                dte_min=cal_meta["far_dte_min"],
                                dte_max=cal_meta["far_dte_max"],
                                max_rel_spread=intent.max_rel_spread,
                                min_quote_size=intent.min_quote_size,
                            ),
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="近月波动率高于远月，也可考虑卖近买远、同strike的 put calendar。",
                    metadata=cal_meta,
                )
            )

    # 4) 默认兜底
    if not candidates:
        if intent.market_view == "neutral" and _is_allowed("bear_call_spread"):
            candidates.append(
                StrategySpec(
                    strategy_type="bear_call_spread",
                    underlying_id=intent.underlying_id,
                    legs=[
                        StrategyLegSpec(
                            action="SELL",
                            option_type="CALL",
                            expiry_rule="nearest",
                            delta_target=0.30,
                        ),
                        StrategyLegSpec(
                            action="BUY",
                            option_type="CALL",
                            expiry_rule="same_expiry",
                            delta_target=0.15,
                        ),
                    ],
                    constraints=base_constraints,
                    rationale="默认中性低风险兜底策略。",
                    metadata={"source": "rule_based_compiler"},
                )
            )

    return candidates