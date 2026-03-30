from __future__ import annotations

import logging

from typing import Dict, List, Tuple

from app.models.schemas import ResolvedStrategy
from app.strategy.greeks_monitor import compute_strategy_net_greeks


# ============================================================
# 通用辅助
# ============================================================

logger = logging.getLogger(__name__)


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
    iv_diff = far_iv - near_iv（负数代表近月更贵，是我们想要的）
    连续函数：近月溢价越大得分越高，上限在溢价5个vol点饱和
    """
    if iv_diff is None:
        return 0.0
    near_premium = -iv_diff
    if near_premium >= 0.05:
        return 1.0
    elif near_premium > 0:
        return 0.4 + 0.6 * (near_premium / 0.05)
    elif near_premium >= -0.005:
        return 0.3
    else:
        return 0.1


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


def _extract_iv_pct(strategy: ResolvedStrategy) -> float:
    """从 metadata 里取 iv_pct，找不到返回 0.5（中性假设）。"""
    if not strategy.metadata:
        return 0.5
    if "iv_pct" in strategy.metadata:
        try:
            return float(strategy.metadata.get("iv_pct", 0.5) or 0.5)
        except Exception:
            return 0.5
    sm = strategy.metadata.get("strategy_metadata") or {}
    try:
        return float(sm.get("iv_pct", 0.5) or 0.5)
    except Exception:
        return 0.5


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

    # 极端IV差豁免moneyness惩罚：
    # near_premium >= 0.08时（近月IV比远月高8个vol点以上），
    # 说明是定价偏离套利机会，strike偏离ATM是可接受的
    # 将moneyness_score至少提升到0.8
    if iv_diff is not None and (-iv_diff) >= 0.08:
        moneyness_score = max(moneyness_score, 0.8)

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
      1. iv_diff_score      — 期限结构（near IV 更贵更好），连续函数
      2. near_delta_score   — 近腿虚值程度（0.25~0.35 最理想）
      3. delta_spread_score — 两腿 delta 差，连续函数（峰值0.15-0.20，两侧线性衰减）
      4. cost_score         — 净权利金 / spot
      5. liquidity_score
    """
    if len(strategy.legs) < 2:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    near_leg = strategy.legs[0]  # SELL near
    far_leg  = strategy.legs[1]  # BUY far

    # 1. iv_diff（连续函数，复用calendar逻辑）
    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv
    if iv_diff is None:
        iv_diff_score = 0.3
    else:
        near_premium = -iv_diff
        if near_premium >= 0.05:
            iv_diff_score = 1.0
        elif near_premium > 0:
            iv_diff_score = 0.4 + 0.6 * (near_premium / 0.05)
        elif near_premium >= -0.005:
            iv_diff_score = 0.3
        else:
            iv_diff_score = 0.1

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

    # 3. delta_spread：连续函数，峰值在0.15-0.20，两侧线性衰减
    far_abs_delta = abs(far_leg.delta) if far_leg.delta is not None else None
    if near_abs_delta is not None and far_abs_delta is not None:
        d_spread = far_abs_delta - near_abs_delta
        if d_spread < 0:
            delta_spread_score = 0.2
        elif d_spread <= 0.10:
            delta_spread_score = 0.3 + 0.5 * (d_spread / 0.10)
        elif d_spread <= 0.20:
            delta_spread_score = 0.8 + 0.2 * ((d_spread - 0.10) / 0.10)
        elif d_spread <= 0.30:
            delta_spread_score = 1.0 - 0.2 * ((d_spread - 0.20) / 0.10)
        elif d_spread <= 0.40:
            delta_spread_score = 0.8 - 0.3 * ((d_spread - 0.30) / 0.10)
        else:
            delta_spread_score = max(0.3, 0.5 - 0.2 * ((d_spread - 0.40) / 0.10))
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

    dte = None
    for leg in strategy.legs:
        if leg.dte is not None:
            dte = min(dte, leg.dte) if dte is not None else leg.dte

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

    if net_theta is None:
        theta_score = 0.4
    elif net_theta > 0:
        theta_score = 1.0
    else:
        theta_score = 0.4

    if net_gamma is None or dte is None:
        gamma_score = 0.4
    else:
        gamma_per_day = net_gamma / max(dte, 1)
        if gamma_per_day >= -0.05:
            gamma_score = 1.0
        elif gamma_per_day >= -0.10:
            gamma_score = 0.7
        elif gamma_per_day >= -0.15:
            gamma_score = 0.4
        else:
            gamma_score = 0.1

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
        "gamma_per_day":   round(net_gamma / max(dte, 1), 6)
                           if net_gamma is not None and dte is not None else None,
    }


def _score_vertical_spread(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    bear_call_spread / bull_put_spread  → credit spread：卖方收权利金，保护型
    bull_call_spread / bear_put_spread  → debit spread：买方博方向，进攻型

    credit：卖腿delta目标0.30，买腿delta目标0.15，cost用credit/spot相对值
    debit：买腿delta目标0.45-0.55，卖腿delta目标0.20-0.30，cost用debit/spot相对值
    """
    sell_legs = [l for l in strategy.legs if l.action == "SELL"]
    buy_legs  = [l for l in strategy.legs if l.action == "BUY"]

    is_debit = strategy.strategy_type in ("bull_call_spread", "bear_put_spread")

    if is_debit:
        # 买腿是主力，接近平值；卖腿是上限，偏虚值
        if buy_legs and buy_legs[0].delta is not None:
            d = abs(buy_legs[0].delta)
            if 0.40 <= d <= 0.60:
                buy_delta_score = 1.0
            elif 0.35 <= d < 0.40 or 0.60 < d <= 0.70:
                buy_delta_score = 0.8
            elif 0.25 <= d < 0.35 or 0.70 < d <= 0.80:
                buy_delta_score = 0.6
            else:
                buy_delta_score = 0.3
        else:
            buy_delta_score = 0.3

        if sell_legs and sell_legs[0].delta is not None:
            d = abs(sell_legs[0].delta)
            if 0.20 <= d <= 0.30:
                sell_delta_score = 1.0
            elif 0.15 <= d < 0.20 or 0.30 < d <= 0.35:
                sell_delta_score = 0.8
            elif 0.10 <= d < 0.15 or 0.35 < d <= 0.45:
                sell_delta_score = 0.6
            else:
                sell_delta_score = 0.3
        else:
            sell_delta_score = 0.3

        # debit成本：净支出/spot，越低越好
        spot  = strategy.spot_price or 0
        debit = strategy.net_debit
        if debit is None or spot <= 0:
            cost_score = 0.3
        else:
            ratio = debit / spot
            if ratio <= 0.005:
                cost_score = 1.0
            elif ratio <= 0.010:
                cost_score = 0.85
            elif ratio <= 0.020:
                cost_score = 0.65
            elif ratio <= 0.035:
                cost_score = 0.45
            else:
                cost_score = 0.2

        signal_score = 0.65 * buy_delta_score + 0.35 * sell_delta_score

    else:
        # 卖腿是核心收权利金，买腿是便宜保护
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

        # credit成本：净收入/spot，越高越好
        spot   = strategy.spot_price or 0
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

        signal_score = 0.60 * sell_delta_score + 0.40 * buy_delta_score

    liquidity_score = _liquidity_score(strategy)

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
        "is_debit":          is_debit,
    }


def _score_single_leg(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
    """
    naked_call / naked_put / covered_call / long_call / long_put

    买方（long）：delta + vega + iv_pct + cost
    卖方（naked/covered）：delta/yield + cost + liquidity
    """
    if not strategy.legs:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    leg = strategy.legs[0]
    is_sell   = (leg.action == "SELL")
    abs_delta = abs(leg.delta) if leg.delta is not None else None

    vega_score: float  = 0.0
    iv_score: float    = 0.0
    theta_score: float = 0.0

    # ── 核心信号评分 ──
    if strategy.strategy_type in ("naked_call", "naked_put"):
        if abs_delta is None:
            delta_score = 0.3
        elif abs_delta <= 0.15:                # 太虚，权利金太薄
            delta_score = 0.75
        elif abs(abs_delta - 0.18) <= 0.03:   # 0.15~0.21，精准命中甜区
            delta_score = 1.0
        elif abs_delta <= 0.28:                # 0.21~0.28，可接受
            delta_score = 0.85
        elif abs_delta <= 0.35:                # 0.28~0.35，偏深
            delta_score = 0.6
        else:                                  # >0.35，太深
            delta_score = 0.3

    elif strategy.strategy_type == "covered_call":
        FEE_PER_SHARE = 0.0004
        spot   = strategy.spot_price or 0
        credit = strategy.net_credit
        dte    = leg.dte or 0
        if credit is not None and spot > 0 and dte > 0:
            net_credit_after_fee = max(0.0, credit - FEE_PER_SHARE)
            ann_yield = net_credit_after_fee / spot / (dte / 360)
            if 0.03 <= ann_yield <= 0.05:
                delta_score = 1.0
            elif 0.05 < ann_yield <= 0.08:
                delta_score = 0.85
            elif 0.02 <= ann_yield < 0.03:
                delta_score = 0.75
            elif 0.08 < ann_yield <= 0.12:
                delta_score = 0.65
            elif 0.01 <= ann_yield < 0.02:
                delta_score = 0.4
            elif ann_yield > 0.12:
                delta_score = 0.5
            else:
                delta_score = 0.1
        else:
            if abs_delta is None:
                delta_score = 0.3
            elif abs_delta <= 0.15:
                delta_score = 0.6
            elif abs_delta <= 0.25:
                delta_score = 0.8
            elif abs_delta <= 0.35:
                delta_score = 0.6
            else:
                delta_score = 0.3

    elif strategy.strategy_type in ("long_call", "long_put"):
        if abs_delta is None:
            delta_score = 0.3
        elif 0.35 <= abs_delta <= 0.50:
            delta_score = 1.0
        elif 0.50 < abs_delta <= 0.65:
            delta_score = 0.85
        elif 0.25 <= abs_delta < 0.35:
            delta_score = 0.75
        elif 0.65 < abs_delta <= 0.75:
            delta_score = 0.65
        elif abs_delta < 0.25:
            delta_score = 0.5
        else:
            delta_score = 0.5

        raw_vega   = leg.vega if leg.vega is not None else 0.0
        vega_score = min(1.0, raw_vega / 0.008)

        iv_pct   = _extract_iv_pct(strategy)
        iv_score = 1.0 - iv_pct

        # theta：买方希望时间损耗慢，abs(theta)越小越好
        # 0.001以下极低损耗→1.0，0.008以上高损耗→0.0，线性插值
        raw_theta   = abs(leg.theta) if leg.theta is not None else 0.004
        theta_score = max(0.0, 1.0 - raw_theta / 0.0015)

    else:
        delta_score = 0.5

    # ── 成本评分 ──
    if is_sell:
        if strategy.strategy_type == "covered_call":
            cost_score = delta_score
        else:
            spot   = strategy.spot_price or 0
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
        spot  = strategy.spot_price or 0
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

    # ── 权重合并 ──
    if is_sell:
        total_score = (
            0.35 * delta_score
            + 0.35 * cost_score
            + 0.30 * liquidity_score
        )
    elif strategy.strategy_type in ("long_call", "long_put"):
        total_score = (
            0.35 * delta_score
            + 0.25 * vega_score
            + 0.20 * iv_score
            + 0.10 * theta_score
            + 0.10 * cost_score
        )
    else:
        total_score = (
            0.45 * delta_score
            + 0.25 * cost_score
            + 0.30 * liquidity_score
        )

    breakdown: Dict = {
        "signal_score":    round(delta_score, 4),
        "delta_score":     round(delta_score, 4),
        "cost_score":      round(cost_score, 4),
        "liquidity_score": round(liquidity_score, 4),
        "abs_delta":       round(abs_delta, 4) if abs_delta is not None else None,
        "is_sell":         is_sell,
    }
    if strategy.strategy_type in ("long_call", "long_put"):
        breakdown["vega_score"]  = round(vega_score, 4)
        breakdown["iv_score"]    = round(iv_score, 4)
        breakdown["theta_score"] = round(theta_score, 4)
        breakdown["raw_vega"]    = round(leg.vega, 6) if leg.vega is not None else None
        breakdown["raw_theta"]   = round(leg.theta, 6) if leg.theta is not None else None

    return total_score, breakdown


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

_SINGLE_LEG_TYPES   = ("naked_call", "naked_put", "covered_call", "long_call", "long_put")
_VERTICAL_TYPES     = ("bear_call_spread", "bull_put_spread", "bull_call_spread", "bear_put_spread")
_DEBIT_VERTICAL     = ("bull_call_spread", "bear_put_spread")
_CALENDAR_TYPES     = ("call_calendar", "put_calendar")
_DIAGONAL_TYPES     = ("diagonal_call", "diagonal_put")
_IRON_TYPES         = ("iron_condor", "iron_fly")


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

        dte = None
        for leg in strategy.legs:
            if leg.dte is not None:
                dte = min(dte, leg.dte) if dte is not None else leg.dte

        adj = 1.0

        # delta偏移惩罚：diagonal、单腿、所有vertical全部豁免
        if (net_delta is not None
                and st not in _DIAGONAL_TYPES
                and st not in _SINGLE_LEG_TYPES
                and st not in _VERTICAL_TYPES):
            if abs(net_delta) > 0.15:
                adj *= 0.7
            elif abs(net_delta) > 0.08:
                adj *= 0.85

        # calendar追加vega/gamma adj
        if st in _CALENDAR_TYPES:
            if net_vega is not None and net_vega <= 0:
                adj *= 0.7
            if net_gamma is not None and net_gamma < -1.0:
                adj *= 0.85

        # iron追加极端vega/gamma adj（gamma用归一化值）
        if st in _IRON_TYPES:
            if net_vega is not None and net_vega < -0.30:
                adj *= 0.75
            if net_gamma is not None and dte is not None:
                gamma_per_day = net_gamma / max(dte, 1)
                if gamma_per_day < -0.15:
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