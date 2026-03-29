from __future__ import annotations

from typing import Dict, List, Tuple

from app.models.schemas import ResolvedStrategy
from app.strategy.greeks_monitor import compute_strategy_net_greeks

def _avg_rel_spread(strategy: ResolvedStrategy) -> float:
    spreads = []
    for leg in strategy.legs:
        if leg.mid is not None and leg.mid > 0 and leg.bid is not None and leg.ask is not None:
            spreads.append((leg.ask - leg.bid) / leg.mid)
    if not spreads:
        return 1.0
    return sum(spreads) / len(spreads)


def _liquidity_score(strategy: ResolvedStrategy) -> float:
    """
    spread 越小越好，分数越高。
    """
    avg_spread = _avg_rel_spread(strategy)

    if avg_spread <= 0.01:
        return 1.0
    if avg_spread <= 0.02:
        return 0.8
    if avg_spread <= 0.03:
        return 0.6
    if avg_spread <= 0.05:
        return 0.3
    return 0.0


def _cost_score(strategy: ResolvedStrategy) -> float:
    """
    通用成本评分：
    - credit strategy: 净收入越高越好
    - debit strategy: 净支出越低越好
    """
    if strategy.net_credit is not None:
        if strategy.net_credit >= 0.08:
            return 1.0
        if strategy.net_credit >= 0.05:
            return 0.8
        if strategy.net_credit >= 0.03:
            return 0.6
        if strategy.net_credit > 0:
            return 0.4
        return 0.0

    if strategy.net_debit is not None:
        if strategy.net_debit <= 0.03:
            return 1.0
        if strategy.net_debit <= 0.05:
            return 0.8
        if strategy.net_debit <= 0.08:
            return 0.6
        if strategy.net_debit > 0:
            return 0.3
        return 0.0

    return 0.0


def _signal_strength(strategy: ResolvedStrategy) -> float:
    """
    通用信号强度：
    - bear_call_spread / bull_put_spread: 卖腿 delta 越接近 0.30 越好
    - 其他旧逻辑先保留
    """
    if not strategy.legs:
        return 0.0

    if strategy.strategy_type in ("bear_call_spread", "bull_put_spread"):
        sell_legs = [leg for leg in strategy.legs if leg.action == "SELL"]
        if not sell_legs or sell_legs[0].delta is None:
            return 0.3

        diff = abs(abs(sell_legs[0].delta) - 0.30)
        if diff <= 0.03:
            return 1.0
        if diff <= 0.07:
            return 0.8
        if diff <= 0.12:
            return 0.6
        return 0.3

    if strategy.strategy_type in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        if len(strategy.legs) < 2:
            return 0.3
        d1 = strategy.legs[0].delta
        d2 = strategy.legs[1].delta
        if d1 is None or d2 is None:
            return 0.3

        diff = abs(abs(d1) - abs(d2))
        if diff <= 0.03:
            return 1.0
        if diff <= 0.08:
            return 0.8
        if diff <= 0.15:
            return 0.6
        return 0.3

    return 0.5


def _calc_calendar_signal_score(iv_diff: float | None) -> float:
    """
    对 sell near / buy far 的 calendar，
    更希望 near IV > far IV，即 far - near 为负。
    """
    if iv_diff is None:
        return 0.0

    if iv_diff >= 0:
        return max(0.0, 0.4 - min(iv_diff, 0.1) * 4)

    return min(1.0, 0.4 + min(abs(iv_diff), 0.1) * 6)


def _calc_calendar_cost_score(net_debit: float | None, spot_price: float | None) -> float:
    """
    calendar 更适合按 debit / spot 的相对成本打分。
    """
    if net_debit is None or spot_price is None or spot_price <= 0:
        return 0.0

    ratio = net_debit / spot_price

    if ratio <= 0.005:
        return 1.0
    if ratio <= 0.01:
        return 0.8
    if ratio <= 0.02:
        return 0.6
    if ratio <= 0.03:
        return 0.4
    return 0.2


def _calc_calendar_moneyness_score(strike: float | None, spot_price: float | None) -> float:
    """
    strike 越接近 ATM 越好。
    """
    if strike is None or spot_price is None or spot_price <= 0:
        return 0.0

    m = strike / spot_price
    dist = abs(m - 1.0)

    if dist <= 0.03:
        return 1.0
    if dist <= 0.05:
        return 0.8
    if dist <= 0.08:
        return 0.6
    if dist <= 0.12:
        return 0.3
    return 0.0


def _score_calendar_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    if len(strategy.legs) < 2:
        return 0.0, {
            "signal_score": 0.0,
            "liquidity_score": 0.0,
            "cost_score": 0.0,
            "moneyness_score": 0.0,
            "iv_diff": None,
        }

    near_leg = strategy.legs[0]
    far_leg = strategy.legs[1]

    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv

    signal_score = _calc_calendar_signal_score(iv_diff)
    liquidity_score = _liquidity_score(strategy)
    cost_score = _calc_calendar_cost_score(strategy.net_debit, strategy.spot_price)
    moneyness_score = _calc_calendar_moneyness_score(near_leg.strike, strategy.spot_price)

    total_score = (
        0.35 * signal_score
        + 0.20 * liquidity_score
        + 0.20 * cost_score
        + 0.25 * moneyness_score
    )

    return total_score, {
        "signal_score": round(signal_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "cost_score": round(cost_score, 4),
        "moneyness_score": round(moneyness_score, 4),
        "iv_diff": round(iv_diff, 6) if iv_diff is not None else None,
    }


def _score_generic_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    signal_score = _signal_strength(strategy)
    liquidity_score = _liquidity_score(strategy)
    cost_score = _cost_score(strategy)

    total_score = (
        signal_score * 0.4
        + liquidity_score * 0.3
        + cost_score * 0.3
    )

    return total_score, {
        "signal_score": round(signal_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "cost_score": round(cost_score, 4),
    }


def rank_strategies(strategies: List[ResolvedStrategy]) -> List[ResolvedStrategy]:
    ranked: List[ResolvedStrategy] = []

    for strategy in strategies:
        if strategy.strategy_type in ("call_calendar", "put_calendar"):
            base_score, breakdown = _score_calendar_strategy(strategy)
        else:
            base_score, breakdown = _score_generic_strategy(strategy)

        greeks = compute_strategy_net_greeks(strategy)

        adj = 1.0
        net_delta = greeks.get("net_delta")
        if net_delta is not None:
            if abs(net_delta) > 0.15:
                adj *= 0.7
            elif abs(net_delta) > 0.08:
                adj *= 0.85

        if strategy.strategy_type in ("call_calendar", "put_calendar"):
            net_vega = greeks.get("net_vega")
            net_gamma = greeks.get("net_gamma")
            if net_vega is not None and net_vega <= 0:
                adj *= 0.7
            if net_gamma is not None and net_gamma < -1.0:
                adj *= 0.85

        prior = 1.0
        if strategy.metadata:
            prior = strategy.metadata.get("prior_weight", 1.0)

        prior_adj = 0.7 + 0.3 * prior
        final_score = base_score * adj * prior_adj

        breakdown["greeks_adj"] = round(adj, 4)
        breakdown["prior"] = round(prior, 4)
        breakdown["prior_adj"] = round(prior_adj, 4)

        strategy.score = round(final_score, 4)
        strategy.score_breakdown = breakdown

        print(
            f"[rank] {strategy.strategy_type} "
            f"base={base_score:.3f} adj={adj:.3f} "
            f"prior={prior:.2f} final={final_score:.3f}"
        )

        ranked.append(strategy)

    ranked.sort(key=lambda x: x.score or 0.0, reverse=True)
    return ranked