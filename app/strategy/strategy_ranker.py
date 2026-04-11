from __future__ import annotations

import logging

from typing import Any, Dict, List, Optional, Tuple

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
    if not strategy.metadata:
        return 0.5
    # 优先从greeks_report里取（这是实际存放的位置）
    gr = strategy.metadata.get("greeks_report", {})
    iv_pct_data = gr.get("iv_percentile", {})
    if isinstance(iv_pct_data, dict):
        v = iv_pct_data.get("composite_percentile")
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    # 兼容旧路径
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


def _extract_greeks_preference(strategy: ResolvedStrategy) -> Dict[str, Any]:
    """
    从metadata里取greeks_preference。
    由advisor_service_v2在run_advisor里把intent.greeks_preference写入metadata。
    """
    if not strategy.metadata:
        return {}
    gp = strategy.metadata.get("greeks_preference")
    if isinstance(gp, dict):
        return gp
    sm = strategy.metadata.get("strategy_metadata") or {}
    gp = sm.get("greeks_preference")
    if isinstance(gp, dict):
        return gp
    return {}


# ============================================================
# Greeks意图调整（greeks_intent_adj）
# ============================================================

def _calc_greeks_intent_adj(
    strategy: ResolvedStrategy,
    greeks_preference: Dict[str, Any],
    net_greeks: Dict[str, Any],
) -> float:
    """
    根据用户的Greeks意图偏好调整评分，最多±25%。

    greeks_preference格式：
    {
        "gamma": {"sign": "positive", "strength": 0.9},
        "delta": {"sign": "negative", "strength": 0.3},
        ...
    }

    sign匹配：实际Greek符号与用户期望符号一致 → match=1.0
    sign不匹配 → match=-0.5（惩罚幅度低于奖励，避免过度压制）
    sign为neutral → match=0.0（忽略该维度）

    最终公式：
    intent_score = 加权平均(strength × match) / 加权平均(strength)
    greeks_adj = 0.75 + 0.25 × intent_score
    范围：[0.75, 1.0]（只奖不罚到底，最低0.75）

    strike_forced的腿不参与delta匹配判断：
    如果所有腿都是strike_forced，delta维度跳过。
    """
    if not greeks_preference:
        return 1.0

    greek_map = {
        "delta": net_greeks.get("net_delta"),
        "gamma": net_greeks.get("net_gamma"),
        "vega":  net_greeks.get("net_vega"),
        "theta": net_greeks.get("net_theta"),
    }

    # delta特殊处理：如果所有腿都是strike_forced，跳过delta维度
    all_strike_forced = all(getattr(leg, "strike_forced", False) for leg in strategy.legs)

    total_weight = 0.0
    weighted_match = 0.0

    for greek, pref in greeks_preference.items():
        if not isinstance(pref, dict):
            continue

        sign = pref.get("sign")
        strength = pref.get("strength", 0.5)

        # neutral意图不参与计算
        if sign == "neutral":
            continue

        # delta：全腿strike_forced时跳过
        if greek == "delta" and all_strike_forced:
            continue

        actual_value = greek_map.get(greek)
        if actual_value is None:
            continue

        # 判断符号是否匹配
        if sign == "positive":
            match = 1.0 if actual_value > 0 else -0.5
        elif sign == "negative":
            match = 1.0 if actual_value < 0 else -0.5
        else:
            continue

        total_weight += strength
        weighted_match += strength * match

    if total_weight <= 0:
        return 1.0

    intent_score = weighted_match / total_weight
    # intent_score范围：[-0.5, 1.0]
    # 映射到adj：[-0.5→0.75, 1.0→1.0]
    greeks_adj = 0.70 + 0.30 * max(-1.0, intent_score)
    return round(max(0.75, greeks_adj), 4)


# ============================================================
# 各类型专属 scorer
# ============================================================

def _score_calendar_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
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
    if len(strategy.legs) < 2:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    near_leg = strategy.legs[0]
    far_leg  = strategy.legs[1]

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
    sell_legs = [l for l in strategy.legs if l.action == "SELL"]
    buy_legs  = [l for l in strategy.legs if l.action == "BUY"]

    is_debit = strategy.strategy_type in ("bull_call_spread", "bear_put_spread")

    if is_debit:
        # 买腿：strike_forced时跳过delta评分
        buy_leg = buy_legs[0] if buy_legs else None
        if buy_leg and not getattr(buy_leg, "strike_forced", False) and buy_leg.delta is not None:
            d = abs(buy_leg.delta)
            if 0.40 <= d <= 0.60:
                buy_delta_score = 1.0
            elif 0.35 <= d < 0.40 or 0.60 < d <= 0.70:
                buy_delta_score = 0.8
            elif 0.25 <= d < 0.35 or 0.70 < d <= 0.80:
                buy_delta_score = 0.6
            else:
                buy_delta_score = 0.3
        else:
            buy_delta_score = 0.7  # strike_forced时给中性分，不惩罚

        # 卖腿：strike_forced时跳过delta评分
        sell_leg = sell_legs[0] if sell_legs else None
        if sell_leg and not getattr(sell_leg, "strike_forced", False) and sell_leg.delta is not None:
            d = abs(sell_leg.delta)
            if 0.20 <= d <= 0.30:
                sell_delta_score = 1.0
            elif 0.15 <= d < 0.20 or 0.30 < d <= 0.35:
                sell_delta_score = 0.8
            elif 0.10 <= d < 0.15 or 0.35 < d <= 0.45:
                sell_delta_score = 0.6
            else:
                sell_delta_score = 0.3
        else:
            sell_delta_score = 0.7

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
        # 卖腿：strike_forced时跳过delta评分
        sell_leg = sell_legs[0] if sell_legs else None
        if sell_leg and not getattr(sell_leg, "strike_forced", False) and sell_leg.delta is not None:
            diff = abs(abs(sell_leg.delta) - 0.30)
            if diff <= 0.03:
                sell_delta_score = 1.0
            elif diff <= 0.07:
                sell_delta_score = 0.8
            elif diff <= 0.12:
                sell_delta_score = 0.6
            else:
                sell_delta_score = 0.3
        else:
            sell_delta_score = 0.7

        # 买腿：strike_forced时跳过delta评分
        buy_leg = buy_legs[0] if buy_legs else None
        if buy_leg and not getattr(buy_leg, "strike_forced", False) and buy_leg.delta is not None:
            diff = abs(abs(buy_leg.delta) - 0.15)
            if diff <= 0.03:
                buy_delta_score = 1.0
            elif diff <= 0.07:
                buy_delta_score = 0.8
            elif diff <= 0.12:
                buy_delta_score = 0.6
            else:
                buy_delta_score = 0.3
        else:
            buy_delta_score = 0.7

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
    if not strategy.legs:
        return 0.0, {"signal_score": 0.0, "liquidity_score": 0.0, "cost_score": 0.0}

    leg = strategy.legs[0]
    is_sell   = (leg.action == "SELL")
    abs_delta = abs(leg.delta) if leg.delta is not None else None

    # strike_forced时单腿delta评分给中性分
    leg_strike_forced = getattr(leg, "strike_forced", False)

    vega_score: float  = 0.0
    iv_score: float    = 0.0
    theta_score: float = 0.0

    if strategy.strategy_type in ("naked_call", "naked_put"):
        if leg_strike_forced or abs_delta is None:
            delta_score = 0.7  # 用户指定strike，不评delta
        elif abs_delta <= 0.15:
            delta_score = 0.75
        elif abs(abs_delta - 0.18) <= 0.03:
            delta_score = 1.0
        elif abs_delta <= 0.28:
            delta_score = 0.85
        elif abs_delta <= 0.35:
            delta_score = 0.6
        else:
            delta_score = 0.3

    # Advisor-path covered_call scoring only.
    # Dedicated covered-call scan ranking lives in app.strategy.covered_call_service.
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

        if leg_strike_forced or abs_delta is None:
            delta_score = 0.7
        else:
            # 连续递减：峰值在0.40-0.50，两侧线性衰减
            # 0.40-0.50 → 1.0
            # 0.35 → 0.90，0.30 → 0.75，0.25 → 0.55，<0.25 → 0.4
            # 0.55 → 0.90，0.60 → 0.80，0.65 → 0.70，0.70 → 0.55，>0.70 → 0.4
            d = abs_delta
            if 0.40 <= d <= 0.50:
                delta_score = 1.0
            elif 0.35 <= d < 0.40:
                delta_score = 0.90 + (d - 0.35) / 0.05 * 0.10
            elif 0.30 <= d < 0.35:
                delta_score = 0.75 + (d - 0.30) / 0.05 * 0.15
            elif 0.25 <= d < 0.30:
                delta_score = 0.55 + (d - 0.25) / 0.05 * 0.20
            elif d < 0.25:
                delta_score = max(0.3, 0.55 - (0.25 - d) / 0.05 * 0.10)
            elif 0.50 < d <= 0.55:
                delta_score = 0.90 + (0.55 - d) / 0.05 * 0.10
            elif 0.55 < d <= 0.60:
                delta_score = 0.80 + (0.60 - d) / 0.05 * 0.10
            elif 0.60 < d <= 0.65:
                delta_score = 0.70 + (0.65 - d) / 0.05 * 0.10
            elif 0.65 < d <= 0.70:
                delta_score = 0.55 + (0.70 - d) / 0.05 * 0.15
            else:
                delta_score = max(0.3, 0.55 - (d - 0.70) / 0.05 * 0.10)

        raw_vega = leg.vega if leg.vega is not None else 0.0
        spot = strategy.spot_price or 1.0
        vega_normalized = raw_vega / spot
        vega_score = min(1.0, vega_normalized / 0.0018)

        iv_pct   = _extract_iv_pct(strategy)
        iv_score = 1.0 - iv_pct

        raw_theta   = abs(leg.theta) if leg.theta is not None else 0.004
        theta_score = max(0.0, 1.0 - raw_theta / 0.0015)

    else:
        delta_score = 0.5

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
        "strike_forced":   leg_strike_forced,
    }
    if strategy.strategy_type in ("long_call", "long_put"):
        breakdown["vega_score"]  = round(vega_score, 4)
        breakdown["iv_score"]    = round(iv_score, 4)
        breakdown["theta_score"] = round(theta_score, 4)
        breakdown["raw_vega"]    = round(leg.vega, 6) if leg.vega is not None else None
        breakdown["raw_theta"]   = round(leg.theta, 6) if leg.theta is not None else None

    return total_score, breakdown


def _score_generic_strategy(strategy: ResolvedStrategy) -> Tuple[float, Dict]:
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
# covered_call stays in the general advisor-path single-leg set.
# This does not replace the dedicated covered-call scan service.
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

        # ── 2. Greeks结构调整（基于策略本身的Greeks质量）──
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

        # iron追加极端vega/gamma adj
        if st in _IRON_TYPES:
            if net_vega is not None and net_vega < -0.30:
                adj *= 0.75
            if net_gamma is not None and dte is not None:
                gamma_per_day = net_gamma / max(dte, 1)
                if gamma_per_day < -0.15:
                    adj *= 0.80

        # ── 3. Greeks意图调整（基于用户意图与实际Greeks的匹配度）──
        greeks_preference = _extract_greeks_preference(strategy)
        greeks_intent_adj = _calc_greeks_intent_adj(strategy, greeks_preference, greeks)

        # ── 4. prior weight ──
        prior     = _extract_prior_weight(strategy)
        prior_adj = 0.7 + 0.3 * prior

        # ── 5. final score ──
        # 公式：base × greeks_adj × greeks_intent_adj × prior_adj
        final_score = base_score * adj * greeks_intent_adj * prior_adj

        breakdown["greeks_adj"]        = round(adj, 4)
        breakdown["greeks_intent_adj"] = round(greeks_intent_adj, 4)
        breakdown["prior"]             = round(prior, 4)
        breakdown["prior_adj"]         = round(prior_adj, 4)

        strategy.score           = round(final_score, 4)
        strategy.score_breakdown = breakdown

        print(
            f"[rank] {st:<22} base={base_score:.3f} "
            f"adj={adj:.3f} intent_adj={greeks_intent_adj:.3f} "
            f"prior={prior:.2f} final={final_score:.3f}"
        )

        ranked.append(strategy)

    ranked.sort(key=lambda x: x.score or 0.0, reverse=True)
    return ranked
