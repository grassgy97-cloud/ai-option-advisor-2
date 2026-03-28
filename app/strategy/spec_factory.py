from app.models.schemas import StrategySpec, StrategyLegSpec, StrategyConstraint


def build_strategy_spec(strategy_type: str, intent):

    underlying_id = intent.underlying_id

    # ===== CALL CALENDAR =====
    if strategy_type == "call_calendar":
        return StrategySpec(
            strategy_type="call_calendar",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    quantity=1,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="next_expiry",
                    quantity=1,
                ),
            ],
            constraints=StrategyConstraint(
                dte_min=intent.dte_min,
                dte_max=intent.dte_max,
                max_rel_spread=intent.max_rel_spread,
                min_quote_size=intent.min_quote_size,
                defined_risk_only=intent.defined_risk_only,
            ),
            rationale="calendar（卖近买远）",
            metadata={}
        )

    # ===== BEAR CALL SPREAD =====
    if strategy_type == "bear_call_spread":
        return StrategySpec(
            strategy_type="bear_call_spread",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    delta_target=0.3,
                    quantity=1,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="same_expiry",
                    delta_target=0.15,
                    quantity=1,
                ),
            ],
            constraints=StrategyConstraint(
                dte_min=intent.dte_min,
                dte_max=intent.dte_max,
                max_rel_spread=intent.max_rel_spread,
                min_quote_size=intent.min_quote_size,
                defined_risk_only=True,
            ),
            rationale="bear call spread",
            metadata={}
        )

    # ===== DIAGONAL =====
    if strategy_type == "diagonal_call":
        return StrategySpec(
            strategy_type="diagonal_call",
            underlying_id=underlying_id,
            legs=[
                StrategyLegSpec(
                    action="SELL",
                    option_type="CALL",
                    expiry_rule="nearest",
                    quantity=1,
                ),
                StrategyLegSpec(
                    action="BUY",
                    option_type="CALL",
                    expiry_rule="next_expiry",
                    quantity=1,
                ),
            ],
            constraints=StrategyConstraint(
                dte_min=intent.dte_min,
                dte_max=intent.dte_max,
                max_rel_spread=intent.max_rel_spread,
                min_quote_size=intent.min_quote_size,
            ),
            rationale="diagonal call",
            metadata={}
        )

    return None