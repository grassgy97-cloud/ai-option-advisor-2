from __future__ import annotations

from typing import Any, Iterable, Optional

from app.models.schemas import ResolvedStrategy


DEFAULT_FEE_PER_CONTRACT_RMB = 4.0
CONTRACT_MULTIPLIER = 10000


def _round_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(max(0.0, float(value)), 4)


def _leg_mid_to_natural_gap(strategy: ResolvedStrategy) -> tuple[float, float]:
    mid_total = 0.0
    natural_total = 0.0
    for leg in strategy.legs or []:
        qty = int(getattr(leg, "quantity", 1) or 1)
        mid = float(getattr(leg, "mid", 0.0) or 0.0)
        bid = float(getattr(leg, "bid", 0.0) or 0.0)
        ask = float(getattr(leg, "ask", 0.0) or 0.0)
        if str(leg.action).upper() == "BUY":
            mid_total -= mid * qty
            natural_total -= ask * qty
        else:
            mid_total += mid * qty
            natural_total += bid * qty
    return mid_total, natural_total


def _spread_quality(mid_amount: float, natural_amount: float) -> str:
    if mid_amount <= 0:
        return "poor"
    relative_gap = abs(natural_amount - mid_amount) / max(mid_amount, 1e-6)
    if relative_gap <= 0.10:
        return "good"
    if relative_gap <= 0.25:
        return "acceptable"
    return "poor"


def _fee_note(pricing_type: str, fee_per_contract: float, total_contracts: int) -> str:
    estimated_fee = fee_per_contract * max(total_contracts, 1)
    if pricing_type == "credit":
        return (
            f"按 {fee_per_contract:.1f} 元/手估算，本组合单套约 {estimated_fee:.1f} 元手续费；"
            "若净权利金过薄，手续费会明显侵蚀收入，不宜为成交继续降价。"
        )
    return (
        f"按 {fee_per_contract:.1f} 元/手估算，本组合单套约 {estimated_fee:.1f} 元手续费；"
        "手续费会抬高实际建仓成本，付权利金策略不宜追高成交。"
    )


def build_execution_guidance(
    strategy: ResolvedStrategy,
    fee_per_contract: float = DEFAULT_FEE_PER_CONTRACT_RMB,
) -> dict[str, Any]:
    mid_total, natural_total = _leg_mid_to_natural_gap(strategy)
    total_contracts = sum(abs(int(getattr(leg, "quantity", 1) or 1)) for leg in strategy.legs or [])
    fee_premium_equiv = (fee_per_contract * max(total_contracts, 1)) / CONTRACT_MULTIPLIER

    if strategy.net_credit is not None or mid_total > 0:
        pricing_type = "credit"
        strategy_mid = float(strategy.net_credit if strategy.net_credit is not None else max(mid_total, 0.0))
        strategy_natural = max(natural_total, 0.0)
        gap = max(strategy_mid - strategy_natural, 0.0)
        good_limit = strategy_mid - gap * 0.25
        acceptable_limit = strategy_mid - gap * 0.50
        do_not_chase = strategy_mid - gap * 0.75
    else:
        pricing_type = "debit"
        strategy_mid = float(strategy.net_debit if strategy.net_debit is not None else abs(min(mid_total, 0.0)))
        strategy_natural = abs(min(natural_total, 0.0))
        gap = max(strategy_natural - strategy_mid, 0.0)
        good_limit = strategy_mid + gap * 0.25
        acceptable_limit = strategy_mid + gap * 0.50
        do_not_chase = strategy_mid + gap * 0.75

    return {
        "pricing_type": pricing_type,
        "pricing_type_label": "收权利金" if pricing_type == "credit" else "付权利金",
        "strategy_mid": _round_price(strategy_mid),
        "strategy_natural": _round_price(strategy_natural),
        "good_limit": _round_price(good_limit),
        "acceptable_limit": _round_price(acceptable_limit),
        "do_not_chase_beyond": _round_price(do_not_chase),
        "spread_quality": _spread_quality(strategy_mid, strategy_natural),
        "fee_per_contract": float(fee_per_contract),
        "estimated_fee_total": round(fee_per_contract * max(total_contracts, 1), 2),
        "fee_premium_equiv": round(fee_premium_equiv, 6),
        "fee_note": _fee_note(pricing_type, fee_per_contract, total_contracts),
    }


def attach_execution_guidance(
    strategies: Iterable[ResolvedStrategy],
    fee_per_contract: float = DEFAULT_FEE_PER_CONTRACT_RMB,
) -> None:
    for strategy in strategies:
        strategy.execution_guidance = build_execution_guidance(
            strategy,
            fee_per_contract=fee_per_contract,
        )
