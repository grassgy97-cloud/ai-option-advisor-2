from __future__ import annotations

from typing import Dict, List, Tuple

from app.models.schemas import ResolvedStrategy
from app.strategy.greeks_monitor import compute_strategy_net_greeks


# ============================================================
# 通用辅助
# ============================================================

def _avg_rel_spread(strategy: ResolvedStrategy) -> float:
    spreads = []
    for leg in strategy.legs:
        if leg.mid is not None and leg.mid > 0 and leg.bid is not None and leg.ask is not None:
            spreads.append((leg.ask - leg.bid) / leg.mid)
    if not spreads:
        return 1.0
    return sum(spreads) / len(spreads)


def _liquidity_score(strategy: ResolvedStrategy) -> float:
    """相对价差越小越好。"""
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
    通用成本评分（多腿结构用）：
    - credit strategy：净收入越高越好
    - debit strategy：净支出越低越好
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


def _calc_calendar_signal_score(iv_diff: float | None) -> float:
    """
    long calendar: sell near / buy far
    希望 near_iv >= far_iv，iv_diff = far_iv - near_iv <= 0
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
    return 0.0  # far 更贵，不应做


def _calc_calendar_cost_score(net_debit: float | None, spot_price: float | None) -> float:
    """calendar/diagonal 按 debit/spot 相对成本打分。"""
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
    """strike 越接近 ATM 越好（calendar 专用）。"""
    if strike is None or spot_price is None or spot_price <= 0:
        return 0.0
    dist = abs(strike / spot_price - 1.0)
    if dist <= 0.03:
        return 1.0
    if dist <= 0.05:
        return 0.8
    if dist <= 0.08:
        return 0.6
    if dist <= 0.12:
        return 0.3
    return 0.0


def _extract_prior_weight(strategy: ResolvedStrategy) -> float:
    """从 metadata 里取 prior_weight，兼容两种存放位置。"""
    if not strategy.metadata:
        return 1.0
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


# ============================================================
# 各类型专属 scorer
# ============================================================

def _score_calendar_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """call_calendar / put_calendar：ATM 同 strike 卖近买远。"""
    if len(strategy.legs) < 2:
        return 0.0, {
            "signal_score": 0.0, "liquidity_score": 0.0,
            "cost_score": 0.0, "moneyness_score": 0.0, "iv_diff": None,
        }

    near_leg = strategy.legs[0]
    far_leg  = strategy.legs[1]

    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv

    signal_score    = _calc_calendar_signal_score(iv_diff)
    liquidity_score = _liquidity_score(strategy)
    cost_score      = _calc_calendar_cost_score(strategy.net_debit, strategy.spot_price)
    moneyness_score = _calc_calendar_moneyness_score(near_leg.strike, strategy.spot_price)

    total_score = (
        0.35 * signal_score
        + 0.20 * liquidity_score
        + 0.20 * cost_score
        + 0.25 * moneyness_score
    )

    return total_score, {
        "signal_score":    round(signal_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "cost_score":      round(cost_score, 4),
        "moneyness_score": round(moneyness_score, 4),
        "iv_diff":         round(iv_diff, 6) if iv_diff is not None else None,
    }


def _score_diagonal_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    diagonal_call / diagonal_put：卖近月虚值腿，买远月 ATM 腿。

    信号维度：
      1. iv_diff_score     — 期限结构（near IV 更贵更好）
      2. near_delta_score  — 近腿虚值程度（0.25~0.35 最理想）
      3. delta_spread_score — 两腿 delta 差（0.15~0.25 理想，体现方向性敞口）
      4. cost_score        — 净权利金 / spot
      5. liquidity_score
    """
    if len(strategy.legs) < 2:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    near_leg = strategy.legs[0]  # SELL near
    far_leg  = strategy.legs[1]  # BUY far

    # 1. iv_diff
    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv
    if iv_diff is None:
        iv_diff_score = 0.3
    elif iv_diff <= -0.01:
        iv_diff_score = 1.0
    elif iv_diff <= -0.005:
        iv_diff_score = 0.8
    elif iv_diff < 0:
        iv_diff_score = 0.6
    elif iv_diff <= 0.005:
        iv_diff_score = 0.3
    else:
        iv_diff_score = 0.1  # far 更贵，diagonal 方向性可补偿，给最低分而非 0

    # 2. near_delta
    near_abs_delta = abs(near_leg.delta) if near_leg.delta is not None else None
    if near_abs_delta is None:
        near_delta_score = 0.3
    elif 0.25 <= near_abs_delta <= 0.35:
        near_delta_score = 1.0
    elif 0.20 <= near_abs_delta < 0.25:
        near_delta_score = 0.8
    elif 0.35 < near_abs_delta <= 0.45:
        near_delta_score = 0.7
    elif near_abs_delta < 0.20:
        near_delta_score = 0.5
    else:
        near_delta_score = 0.4

    # 3. delta_spread
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
            delta_spread_score = 0.5
        else:
            delta_spread_score = 0.4
    else:
        delta_spread_score = 0.3

    cost_score      = _calc_calendar_cost_score(strategy.net_debit, strategy.spot_price)
    liquidity_score = _liquidity_score(strategy)

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


def _score_iron_structure(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """iron_condor / iron_fly：delta 中性卖方，关注 gamma/vega 敞口。"""
    liquidity_score = _liquidity_score(strategy)
    cost_score      = _cost_score(strategy)

    greeks    = compute_strategy_net_greeks(strategy)
    net_delta = greeks.get("net_delta")
    net_gamma = greeks.get("net_gamma")
    net_theta = greeks.get("net_theta")
    net_vega  = greeks.get("net_vega")

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

    # theta 正值加分
    if net_theta is None:
        theta_score = 0.4
    elif net_theta > 0:
        theta_score = 1.0
    else:
        theta_score = 0.4

    # gamma：short gamma 本质，但过度惩罚
    if net_gamma is None:
        gamma_score = 0.4
    elif net_gamma >= -1.0:
        gamma_score = 1.0
    elif net_gamma >= -1.5:
        gamma_score = 0.7
    elif net_gamma >= -2.0:
        gamma_score = 0.4
    else:
        gamma_score = 0.1

    # vega：iron 天然 short vega，过度惩罚
    if net_vega is None:
        vega_score = 0.4
    elif net_vega >= -0.05:
        vega_score = 1.0
    elif net_vega >= -0.15:
        vega_score = 0.7
    elif net_vega >= -0.30:
        vega_score = 0.4
    else:
        vega_score = 0.1

    signal_score = (
        0.35 * delta_score
        + 0.25 * theta_score
        + 0.25 * gamma_score
        + 0.15 * vega_score
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
        "vega_score":      round(vega_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "cost_score":      round(cost_score, 4),
    }


def _score_vertical_spread(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    bear_call_spread / bull_put_spread / bull_call_spread / bear_put_spread

    信号维度：
      - 卖腿 delta 越接近目标（0.30）越好
      - 买腿 delta 越接近目标（0.15）越好
      - credit spread（bear_call/bull_put）收权利金，debit spread（bull_call/bear_put）付权利金
    """
    sell_legs = [l for l in strategy.legs if l.action == "SELL"]
    buy_legs  = [l for l in strategy.legs if l.action == "BUY"]

    # 卖腿 delta 评分
    if sell_legs and sell_legs[0].delta is not None:
        diff = abs(abs(sell_legs[0].delta) - 0.30)
        if diff <= 0.03:
            sell_delta_score = 1.0
        elif diff <= 0.07:
            sell_delta_score = 0.8
        elif diff <= 0.12:
            sell_delta_score = 0.6
        else:
            sell_delta_score = 0.3
    else:
        sell_delta_score = 0.3

    # 买腿 delta 评分（保护腿，越虚越好，目标 0.15）
    if buy_legs and buy_legs[0].delta is not None:
        diff = abs(abs(buy_legs[0].delta) - 0.15)
        if diff <= 0.03:
            buy_delta_score = 1.0
        elif diff <= 0.07:
            buy_delta_score = 0.8
        elif diff <= 0.12:
            buy_delta_score = 0.6
        else:
            buy_delta_score = 0.3
    else:
        buy_delta_score = 0.3

    signal_score    = 0.6 * sell_delta_score + 0.4 * buy_delta_score
    liquidity_score = _liquidity_score(strategy)
    cost_score      = _cost_score(strategy)

    total_score = (
        0.40 * signal_score
        + 0.25 * liquidity_score
        + 0.35 * cost_score
    )

    return total_score, {
        "signal_score":      round(signal_score, 4),
        "sell_delta_score":  round(sell_delta_score, 4),
        "buy_delta_score":   round(buy_delta_score, 4),
        "liquidity_score":   round(liquidity_score, 4),
        "cost_score":        round(cost_score, 4),
    }


def _score_single_leg(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    naked_call / naked_put / covered_call / long_call / long_put

    买方（long）关注：delta 虚值程度、流动性、权利金绝对成本
    卖方（naked/covered）关注：delta 虚值程度、流动性、权利金收入
    """
    if not strategy.legs:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    leg = strategy.legs[0]
    is_sell = (leg.action == "SELL")
    abs_delta = abs(leg.delta) if leg.delta is not None else None

    # ── delta 虚值程度评分 ──
    if strategy.strategy_type in ("naked_call", "naked_put"):
        # 目标 delta ~0.22，越虚值越安全
        # 注意：先判断方向（虚/深），再判断距离，避免容差段截走方向分支
        if abs_delta is None:
            delta_score = 0.3
        elif abs(abs_delta - 0.22) <= 0.03:   # 0.19~0.25，精准命中
            delta_score = 1.0
        elif abs_delta < 0.19:                 # 比目标更虚：安全，给高分
            delta_score = 0.9
        elif abs_delta <= 0.30:                # 略深 0.25~0.30，可接受
            delta_score = 0.8
        elif abs_delta <= 0.35:                # 偏深 0.30~0.35，勉强
            delta_score = 0.6
        else:                                  # 太深 >0.35
            delta_score = 0.3

    elif strategy.strategy_type == "covered_call":
        # covered_call 核心信号：税后年化收益率
        # 公式：(premium - 手续费) / underlying_spot / (dte / 360) * 100%
        # 手续费：A股ETF期权约 0.0003元/份（3元/手，1手=10000份）
        # 目标甜区：3-5%，有方向判断时可接受更高
        FEE_PER_SHARE = 0.0004  # 4元/手，1手=10000份
        spot   = strategy.spot_price or 0
        credit = strategy.net_credit
        dte    = leg.dte or 0
        if credit is not None and spot > 0 and dte > 0:
            net_credit_after_fee = max(0.0, credit - FEE_PER_SHARE)
            ann_yield = net_credit_after_fee / spot / (dte / 360)
            if 0.03 <= ann_yield <= 0.05:
                delta_score = 1.0    # 甜区 3-5%
            elif 0.05 < ann_yield <= 0.08:
                delta_score = 0.85   # 略高，delta 偏深，有方向判断时合理
            elif 0.02 <= ann_yield < 0.03:
                delta_score = 0.75   # 略低但可接受，DTE 较长时常见
            elif 0.08 < ann_yield <= 0.12:
                delta_score = 0.65   # 高收益 = delta 深，被行权风险上升
            elif 0.01 <= ann_yield < 0.02:
                delta_score = 0.4    # 太低，权利金太薄，不值得占用仓位
            elif ann_yield > 0.12:
                delta_score = 0.5    # 极高收益，通常 delta 很深，慎用
            else:
                delta_score = 0.1    # <1%，基本没意义
        else:
            # 缺 credit 或 dte 数据，退回 delta 粗估
            if abs_delta is None:
                delta_score = 0.3
            elif abs_delta <= 0.15:
                delta_score = 0.6    # DTE 长时低 delta 合理
            elif abs_delta <= 0.25:
                delta_score = 0.8
            elif abs_delta <= 0.35:
                delta_score = 0.6
            else:
                delta_score = 0.3

    elif strategy.strategy_type in ("long_call", "long_put"):
        # 偏好平值附近（0.40~0.60），深虚值也可接受，极虚才降分
        if abs_delta is None:
            delta_score = 0.3
        elif 0.40 <= abs_delta <= 0.60:
            delta_score = 1.0             # 平值核心区，弹性最强
        elif 0.30 <= abs_delta < 0.40:
            delta_score = 0.85            # 轻虚值，可接受
        elif 0.20 <= abs_delta < 0.30:
            delta_score = 0.7             # 虚值，弹性弱一点但成本低
        elif abs_delta < 0.20:
            delta_score = 0.5             # 太虚，gamma 太小
        elif 0.60 < abs_delta <= 0.75:
            delta_score = 0.8             # 略深，成本稍高但 delta 敞口大
        else:
            delta_score = 0.6             # 深值，成本高，但用户说可接受

    else:
        delta_score = 0.5

    # ── 成本评分 ──
    if is_sell:
        if strategy.strategy_type == "covered_call":
            # covered_call 年化已在 delta_score 里算过，直接复用
            # 等价于年化权重 0.70，流动性 0.30
            cost_score = delta_score
        else:
            # naked_call / naked_put：用 credit/spot 绝对收益率
            # DTE 较短（10-35天），绝对值比较直观
            spot = strategy.spot_price or 0
            credit = strategy.net_credit
            if credit is None or spot <= 0:
                cost_score = 0.3
            else:
                ratio = credit / spot
                if ratio >= 0.008:
                    cost_score = 1.0
                elif ratio >= 0.005:
                    cost_score = 0.8
                elif ratio >= 0.003:
                    cost_score = 0.6
                elif ratio >= 0.001:
                    cost_score = 0.4
                else:
                    cost_score = 0.2
    else:
        # 买方（long_call / long_put）：权利金支出越低越好（debit / spot）
        spot = strategy.spot_price or 0
        debit = strategy.net_debit
        if debit is None or spot <= 0:
            cost_score = 0.3
        else:
            ratio = debit / spot
            if ratio <= 0.005:
                cost_score = 1.0
            elif ratio <= 0.010:
                cost_score = 0.8
            elif ratio <= 0.020:
                cost_score = 0.6
            elif ratio <= 0.030:
                cost_score = 0.4
            else:
                cost_score = 0.2

    liquidity_score = _liquidity_score(strategy)

    # 买方更看重 delta 选腿（弹性），卖方更看重成本（权利金）
    if is_sell:
        total_score = (
            0.35 * delta_score
            + 0.35 * cost_score
            + 0.30 * liquidity_score
        )
    else:
        total_score = (
            0.45 * delta_score
            + 0.25 * cost_score
            + 0.30 * liquidity_score
        )

    return total_score, {
        "signal_score":    round(delta_score, 4),   # 对齐其他 scorer 的 breakdown key
        "delta_score":     round(delta_score, 4),
        "cost_score":      round(cost_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "abs_delta":       round(abs_delta, 4) if abs_delta is not None else None,
        "is_sell":         is_sell,
    }


def _score_generic_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """兜底：未命中任何专属 scorer 的策略。"""
    sell_legs = [l for l in strategy.legs if l.action == "SELL"]
    if sell_legs and sell_legs[0].delta is not None:
        diff = abs(abs(sell_legs[0].delta) - 0.30)
        signal_score = max(0.3, 1.0 - diff * 5)
    else:
        signal_score = 0.5

    liquidity_score = _liquidity_score(strategy)
    cost_score      = _cost_score(strategy)

    total_score = (
        0.40 * signal_score
        + 0.30 * liquidity_score
        + 0.30 * cost_score
    )

    return total_score, {
        "signal_score":    round(signal_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "cost_score":      round(cost_score, 4),
    }


# ============================================================
# 主排序入口
# ============================================================

_SINGLE_LEG_TYPES = ("naked_call", "naked_put", "covered_call", "long_call", "long_put")
_VERTICAL_TYPES   = ("bear_call_spread", "bull_put_spread", "bull_call_spread", "bear_put_spread")
_CALENDAR_TYPES   = ("call_calendar", "put_calendar")
_DIAGONAL_TYPES   = ("diagonal_call", "diagonal_put")
_IRON_TYPES       = ("iron_condor", "iron_fly")


def rank_strategies(strategies: List[ResolvedStrategy]) -> List[ResolvedStrategy]:
    ranked: List[ResolvedStrategy] = []

    for strategy in strategies:
        st = strategy.strategy_type

        # ── 1. 专属 scorer → base_score ──
        if st in _CALENDAR_TYPES:
            base_score, breakdown = _score_calendar_strategy(strategy)
        elif st in _DIAGONAL_TYPES:
            base_score, breakdown = _score_diagonal_strategy(strategy)
        elif st in _IRON_TYPES:
            base_score, breakdown = _score_iron_structure(strategy)
        elif st in _VERTICAL_TYPES:
            base_score, breakdown = _score_vertical_spread(strategy)
        elif st in _SINGLE_LEG_TYPES:
            base_score, breakdown = _score_single_leg(strategy)
        else:
            base_score, breakdown = _score_generic_strategy(strategy)

        # ── 2. Greeks adjustment ──
        greeks    = compute_strategy_net_greeks(strategy)
        net_delta = greeks.get("net_delta")
        net_vega  = greeks.get("net_vega")
        net_gamma = greeks.get("net_gamma")

        adj = 1.0

        # delta 偏移惩罚（diagonal 和单腿策略豁免，它们本就有方向性）
        if net_delta is not None and st not in _DIAGONAL_TYPES and st not in _SINGLE_LEG_TYPES:
            if abs(net_delta) > 0.15:
                adj *= 0.7
            elif abs(net_delta) > 0.08:
                adj *= 0.85

        # calendar 追加 vega/gamma adj
        if st in _CALENDAR_TYPES:
            if net_vega is not None and net_vega <= 0:
                adj *= 0.7
            if net_gamma is not None and net_gamma < -1.0:
                adj *= 0.85

        # iron 追加极端 vega/gamma adj
        if st in _IRON_TYPES:
            if net_vega is not None and net_vega < -0.30:
                adj *= 0.75
            if net_gamma is not None and net_gamma < -2.0:
                adj *= 0.80

        # ── 3. prior weight ──
        prior     = _extract_prior_weight(strategy)
        prior_adj = 0.7 + 0.3 * prior

        # ── 4. final score ──
        final_score = base_score * adj * prior_adj

        breakdown["greeks_adj"] = round(adj, 4)
        breakdown["prior"]      = round(prior, 4)
        breakdown["prior_adj"]  = round(prior_adj, 4)

        strategy.score           = round(final_score, 4)
        strategy.score_breakdown = breakdown

        print(
            f"[rank] {st:<22} base={base_score:.3f} "
            f"adj={adj:.3f} prior={prior:.2f} final={final_score:.3f}"
        )

        ranked.append(strategy)

    ranked.sort(key=lambda x: x.score or 0.0, reverse=True)
    return ranked