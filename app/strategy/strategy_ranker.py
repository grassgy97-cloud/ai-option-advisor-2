from __future__ import annotations

from typing import List

from app.models.schemas import ResolvedStrategy


def _avg_rel_spread(strategy: ResolvedStrategy) -> float:
    spreads = []
    for leg in strategy.legs:
        if leg.mid and leg.mid > 0:
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
    这里先简化：
    - credit spread 净收入越高越好
    - debit strategy 净支出越低越好
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
    暂时用一个很简化的信号强度：
    - bear_call_spread: 卖腿delta越接近0.30越好
    - bull_put_spread: 卖腿delta越接近0.30越好
    - calendar: 两腿delta越接近越好
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


def rank_strategies(strategies: List[ResolvedStrategy]) -> List[ResolvedStrategy]:
    ranked: List[ResolvedStrategy] = []

    for s in strategies:
        signal_score = _signal_strength(s)
        liquidity_score = _liquidity_score(s)
        cost_score = _cost_score(s)

        total_score = (
            signal_score * 0.4
            + liquidity_score * 0.3
            + cost_score * 0.3
        )

        s.score = round(total_score, 4)
        s.score_breakdown = {
            "signal_score": round(signal_score, 4),
            "liquidity_score": round(liquidity_score, 4),
            "cost_score": round(cost_score, 4),
        }
        ranked.append(s)

    ranked.sort(key=lambda x: x.score or 0.0, reverse=True)
    return ranked