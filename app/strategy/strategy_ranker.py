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
    long calendar: sell near / buy far
    希望 near_iv >= far_iv，即 iv_diff = far - near <= 0
    iv_diff <= -0.01  → 1.0  （near明显更贵，最理想）
    iv_diff <= -0.005 → 0.8
    iv_diff < 0       → 0.6
    iv_diff <= 0.005  → 0.3  （基本持平，勉强可做）
    iv_diff > 0.005   → 0.0  （far更贵，反向，不应做）
    """
    if iv_diff is None:
        return 0.0
    if iv_diff <= -0.01:
        return 1.0
    if iv_diff <= -0.005:
        return 0.8
    if iv_diff < 0:
        return 0.6
    if iv_diff <= 0.005:
        return 0.3
    return 0.0


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

def _score_diagonal_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    diagonal scorer：卖近月虚值腿（收theta），买远月ATM腿（持vega+方向）

    信号维度：
      1. iv_diff_score  - 期限结构（同 calendar 逻辑，near IV贵更好）
      2. near_delta_score - near腿虚值程度（delta 0.25~0.35 最理想，太深或太虚都不好）
      3. delta_spread_score - 两腿 delta 差（体现方向性敞口，差值 0.15~0.25 为理想区间）
      4. cost_score     - 净权利金成本（debit/spot）
      5. liquidity_score
    """
    if len(strategy.legs) < 2:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    near_leg = strategy.legs[0]  # SELL near
    far_leg  = strategy.legs[1]  # BUY far

    # ── 1. iv_diff：near IV 贵更好（同 calendar）──
    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv  # 负值 = near 更贵

    if iv_diff is None:
        iv_diff_score = 0.3  # 无数据给中性分
    elif iv_diff <= -0.01:
        iv_diff_score = 1.0
    elif iv_diff <= -0.005:
        iv_diff_score = 0.8
    elif iv_diff < 0:
        iv_diff_score = 0.6
    elif iv_diff <= 0.005:
        iv_diff_score = 0.3
    else:
        iv_diff_score = 0.1  # far 更贵，结构不理想但 diagonal 仍可接受（方向性补偿）

    # ── 2. near_delta：near腿虚值程度 ──
    # 用 abs(delta)，SELL near 理想区间 0.25~0.35
    near_abs_delta = abs(near_leg.delta) if near_leg.delta is not None else None
    if near_abs_delta is None:
        near_delta_score = 0.3
    elif 0.25 <= near_abs_delta <= 0.35:
        near_delta_score = 1.0   # 理想虚值
    elif 0.20 <= near_abs_delta < 0.25:
        near_delta_score = 0.8   # 略偏虚
    elif 0.35 < near_abs_delta <= 0.45:
        near_delta_score = 0.7   # 略偏深
    elif near_abs_delta < 0.20:
        near_delta_score = 0.5   # 太虚，theta 收益有限
    else:
        near_delta_score = 0.4   # 太深，gamma 风险高

    # ── 3. delta_spread：两腿 delta 差（方向性敞口）──
    # far delta - near delta（均用 abs），理想 0.15~0.25
    far_abs_delta = abs(far_leg.delta) if far_leg.delta is not None else None
    if near_abs_delta is not None and far_abs_delta is not None:
        d_spread = far_abs_delta - near_abs_delta
        if 0.15 <= d_spread <= 0.25:
            delta_spread_score = 1.0
        elif 0.10 <= d_spread < 0.15:
            delta_spread_score = 0.8
        elif 0.25 < d_spread <= 0.35:
            delta_spread_score = 0.7
        elif d_spread < 0.10:
            delta_spread_score = 0.5   # 两腿太接近，方向性弱
        else:
            delta_spread_score = 0.4   # 敞口过大
    else:
        delta_spread_score = 0.3

    # ── 4. cost_score：净权利金/spot ──
    cost_score = _calc_calendar_cost_score(strategy.net_debit, strategy.spot_price)

    # ── 5. liquidity ──
    liquidity_score = _liquidity_score(strategy)

    # ── 加权合成 ──
    # iv_diff 权重略低于 calendar（diagonal 有方向性补偿）
    signal_score = (
        0.30 * iv_diff_score
        + 0.30 * near_delta_score
        + 0.25 * delta_spread_score
        + 0.15 * cost_score
    )

    total_score = (
        0.50 * signal_score
        + 0.25 * liquidity_score
        + 0.25 * cost_score
    )

    return total_score, {
        "signal_score":       round(signal_score, 4),
        "iv_diff_score":      round(iv_diff_score, 4),
        "near_delta_score":   round(near_delta_score, 4),
        "delta_spread_score": round(delta_spread_score, 4),
        "liquidity_score":    round(liquidity_score, 4),
        "cost_score":         round(cost_score, 4),
        "iv_diff":            round(iv_diff, 6) if iv_diff is not None else None,
        "near_delta":         round(near_abs_delta, 4) if near_abs_delta is not None else None,
        "far_delta":          round(far_abs_delta, 4) if far_abs_delta is not None else None,
        "delta_spread":       round(far_abs_delta - near_abs_delta, 4)
                              if near_abs_delta is not None and far_abs_delta is not None else None,
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

def _extract_prior_weight(strategy: ResolvedStrategy) -> float:
    if not strategy.metadata:
        return 1.0

    # 兼容两种位置：
    # 1) strategy.metadata["prior_weight"]
    # 2) strategy.metadata["strategy_metadata"]["prior_weight"]
    if "prior_weight" in strategy.metadata:
        try:
            return float(strategy.metadata.get("prior_weight", 1.0) or 1.0)
        except Exception:
            return 1.0

    sm = strategy.metadata.get("strategy_metadata") or {}
    try:
        return float(sm.get("prior_weight", 1.0) or 1.0)
    except Exception:
        return 1.0


def _score_iron_structure(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    liquidity_score = _liquidity_score(strategy)
    cost_score = _cost_score(strategy)

    greeks = compute_strategy_net_greeks(strategy)
    net_delta = greeks.get("net_delta")
    net_gamma = greeks.get("net_gamma")
    net_theta = greeks.get("net_theta")
    net_vega  = greeks.get("net_vega")   # ✅ 新增

    # delta 越接近中性越好
    if net_delta is None:
        delta_score = 0.4
    elif abs(net_delta) <= 0.05:
        delta_score = 1.0
    elif abs(net_delta) <= 0.10:
        delta_score = 0.8
    elif abs(net_delta) <= 0.15:
        delta_score = 0.6
    else:
        delta_score = 0.3

    # theta 正值加分，但不宜给太高权重
    if net_theta is None:
        theta_score = 0.4
    elif net_theta > 0:
        theta_score = 1.0
    else:
        theta_score = 0.4

    # gamma：short gamma 是 iron 结构本质，但过度 short 要惩罚
    if net_gamma is None:
        gamma_score = 0.4
    elif net_gamma >= -1.0:
        gamma_score = 1.0
    elif net_gamma >= -1.5:
        gamma_score = 0.7    # ✅ 收紧（原0.8）
    elif net_gamma >= -2.0:
        gamma_score = 0.4    # ✅ 收紧（原0.6）
    else:
        gamma_score = 0.1    # ✅ 收紧（原0.3）

    # ✅ 新增 vega_score：iron 结构 vega 天然为负，过度 short vega 要惩罚
    if net_vega is None:
        vega_score = 0.4
    elif net_vega >= -0.05:
        vega_score = 1.0     # vega 接近中性，安全
    elif net_vega >= -0.15:
        vega_score = 0.7
    elif net_vega >= -0.30:
        vega_score = 0.4
    else:
        vega_score = 0.1     # vega 极度 short，高风险

    # signal_score：加入 vega，重新分配权重
    signal_score = (
        0.35 * delta_score
        + 0.25 * theta_score
        + 0.25 * gamma_score
        + 0.15 * vega_score   # ✅ 新增
    )

    total_score = (
        0.45 * signal_score
        + 0.25 * liquidity_score
        + 0.30 * cost_score
    )

    return total_score, {
        "signal_score":    round(signal_score, 4),
        "delta_score":     round(delta_score, 4),
        "theta_score":     round(theta_score, 4),
        "gamma_score":     round(gamma_score, 4),
        "vega_score":      round(vega_score, 4),    # ✅ 新增
        "liquidity_score": round(liquidity_score, 4),
        "cost_score":      round(cost_score, 4),
    }


def rank_strategies(strategies: List[ResolvedStrategy]) -> List[ResolvedStrategy]:
    ranked: List[ResolvedStrategy] = []

    for strategy in strategies:
        if strategy.strategy_type in ("call_calendar", "put_calendar"):
            base_score, breakdown = _score_calendar_strategy(strategy)
        elif strategy.strategy_type in ("diagonal_call", "diagonal_put"):  # ✅ 新增
            base_score, breakdown = _score_diagonal_strategy(strategy)
        elif strategy.strategy_type in ("iron_condor", "iron_fly"):
            base_score, breakdown = _score_iron_structure(strategy)
        else:
            base_score, breakdown = _score_generic_strategy(strategy)

        # ===== 2. Greeks adjustment =====
        greeks = compute_strategy_net_greeks(strategy)

        adj = 1.0
        net_delta = greeks.get("net_delta")
        if net_delta is not None:
            # diagonal 天然有方向性 delta 敞口，不做惩罚
            if strategy.strategy_type not in ("diagonal_call", "diagonal_put"):
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

        # ✅ 新增：iron 结构的额外 Greeks adj
        if strategy.strategy_type in ("iron_condor", "iron_fly"):
            net_vega = greeks.get("net_vega")
            net_gamma = greeks.get("net_gamma")
            if net_vega is not None and net_vega < -0.30:
                adj *= 0.75  # vega 极度 short
            if net_gamma is not None and net_gamma < -2.0:
                adj *= 0.80  # gamma 极度 short

        # ===== 3. prior =====
        prior = _extract_prior_weight(strategy)
        prior_adj = 0.7 + 0.3 * prior

        # ===== 4. final score =====
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