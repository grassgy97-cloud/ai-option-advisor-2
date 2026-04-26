from __future__ import annotations

import logging

from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import ResolvedStrategy
from app.strategy.greeks_monitor import compute_strategy_net_greeks


# ============================================================
# 通用辅助
# ============================================================

logger = logging.getLogger(__name__)

_OPPORTUNITY_COMPONENT_KEYS = (
    "signal_score",
    "iv_diff_score",
    "iv_alignment_score",
)
_STRUCTURE_COMPONENT_KEYS = (
    "moneyness_score",
    "near_delta_score",
    "delta_spread_score",
    "delta_score",
    "theta_score",
    "gamma_score",
    "vega_score",
    "sell_delta_score",
    "buy_delta_score",
)
_EXECUTION_COMPONENT_KEYS = (
    "liquidity_score",
    "cost_score",
)

_CALL_SIDE_STRATEGIES = {
    "long_call",
    "bull_call_spread",
    "bear_call_spread",
    "call_calendar",
    "diagonal_call",
    "naked_call",
    "covered_call",
}
_PUT_SIDE_STRATEGIES = {
    "long_put",
    "bear_put_spread",
    "bull_put_spread",
    "put_calendar",
    "diagonal_put",
    "naked_put",
}
_LONG_PREMIUM_STRATEGIES = {
    "long_call",
    "long_put",
    "bull_call_spread",
    "bear_put_spread",
}
_SHORT_PREMIUM_STRATEGIES = {
    "naked_call",
    "naked_put",
    "covered_call",
    "bear_call_spread",
    "bull_put_spread",
    "iron_condor",
    "iron_fly",
}
_DEFINED_RISK_INCOME_STRATEGIES = {
    "bear_call_spread",
    "bull_put_spread",
    "iron_condor",
    "iron_fly",
}
_DIRECTIONAL_DEBIT_STRATEGIES = {
    "bull_call_spread",
    "bear_put_spread",
    "long_call",
    "long_put",
}
_NAKED_SHORT_STRATEGIES = {
    "naked_call",
    "naked_put",
}
_RANGE_INCOME_STRATEGIES = {
    "iron_condor",
    "iron_fly",
}
_SINGLE_SIDE_CREDIT_STRATEGIES = {
    "bear_call_spread",
    "bull_put_spread",
}


def _avg_rel_spread(strategy: ResolvedStrategy) -> float:
    spreads = []
    for leg in strategy.legs:
        if leg.mid is not None and leg.mid > 0 and leg.bid is not None and leg.ask is not None:
            spreads.append((leg.ask - leg.bid) / leg.mid)
    if not spreads:
        return 1.0
    return sum(spreads) / len(spreads)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _collect_component_values(
    breakdown: Dict[str, Any],
    keys: Tuple[str, ...],
) -> List[float]:
    values: List[float] = []
    for key in keys:
        value = breakdown.get(key)
        if value is None:
            continue
        try:
            values.append(_clamp01(float(value)))
        except Exception:
            continue
    return values


def _average_component(values: List[float], default: float = 0.5) -> float:
    if not values:
        return round(default, 4)
    return round(sum(values) / len(values), 4)


def _build_ranking_components(
    breakdown: Dict[str, Any],
    greeks_adj: float,
    greeks_intent_adj: float,
    prior_adj: float,
) -> Dict[str, float]:
    opportunity_values = _collect_component_values(breakdown, _OPPORTUNITY_COMPONENT_KEYS)
    opportunity_values.extend([
        _clamp01(greeks_intent_adj),
        _clamp01(prior_adj),
    ])

    structure_values = _collect_component_values(breakdown, _STRUCTURE_COMPONENT_KEYS)
    structure_values.append(_clamp01(greeks_adj))
    if not structure_values and "signal_score" in breakdown:
        try:
            structure_values.append(_clamp01(float(breakdown["signal_score"])))
        except Exception:
            pass

    execution_values = _collect_component_values(breakdown, _EXECUTION_COMPONENT_KEYS)

    return {
        "opportunity_fit": _average_component(opportunity_values),
        "structure_quality": _average_component(structure_values),
        "execution_quality": _average_component(execution_values),
    }


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


def _extract_strategy_aware_iv_signal(strategy: ResolvedStrategy) -> Dict[str, Any]:
    iv_side = "atm"
    st = strategy.strategy_type
    if st in _CALL_SIDE_STRATEGIES:
        iv_side = "call"
    elif st in _PUT_SIDE_STRATEGIES:
        iv_side = "put"

    expression = "neutral"
    if st in _LONG_PREMIUM_STRATEGIES:
        expression = "long_premium"
    elif st in _SHORT_PREMIUM_STRATEGIES:
        expression = "short_premium"

    iv_pct = None
    if strategy.metadata:
        gr = strategy.metadata.get("greeks_report", {}) or {}
        iv_pct_data = gr.get("iv_percentile", {}) or {}
        if isinstance(iv_pct_data, dict):
            side_key = f"{iv_side}_iv_percentile"
            side_payload = iv_pct_data.get(side_key)
            if isinstance(side_payload, dict):
                v = side_payload.get("composite_percentile")
                if v is not None:
                    try:
                        iv_pct = float(v)
                    except Exception:
                        iv_pct = None
            if iv_pct is None:
                v = iv_pct_data.get("composite_percentile")
                if v is not None:
                    try:
                        iv_pct = float(v)
                    except Exception:
                        iv_pct = None

    if iv_pct is None:
        iv_pct = _extract_iv_pct(strategy)

    if expression == "neutral" or iv_pct is None:
        return {
            "iv_side_used": iv_side,
            "iv_percentile_used": round(float(iv_pct), 4) if iv_pct is not None else None,
            "iv_signal_strength": 0.0,
            "iv_alignment_score": 0.5,
            "iv_expression": expression,
        }

    if 0.35 <= iv_pct <= 0.65:
        return {
            "iv_side_used": iv_side,
            "iv_percentile_used": round(iv_pct, 4),
            "iv_signal_strength": 0.0,
            "iv_alignment_score": 0.5,
            "iv_expression": expression,
        }

    if expression == "long_premium":
        if iv_pct < 0.35:
            signal_strength = min(1.0, (0.35 - iv_pct) / 0.20)
            alignment_score = 0.5 + 0.5 * signal_strength
        else:
            signal_strength = min(1.0, (iv_pct - 0.65) / 0.20)
            alignment_score = 0.5 - 0.20 * signal_strength
    else:
        if iv_pct > 0.65:
            signal_strength = min(1.0, (iv_pct - 0.65) / 0.20)
            alignment_score = 0.5 + 0.5 * signal_strength
        else:
            signal_strength = min(1.0, (0.35 - iv_pct) / 0.20)
            alignment_score = 0.5 - 0.20 * signal_strength

    return {
        "iv_side_used": iv_side,
        "iv_percentile_used": round(iv_pct, 4),
        "iv_signal_strength": round(signal_strength, 4),
        "iv_alignment_score": round(_clamp01(alignment_score), 4),
        "iv_expression": expression,
    }


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


def _extract_intent_constraints(strategy: ResolvedStrategy) -> Dict[str, Any]:
    if not strategy.metadata:
        return {}
    constraints = strategy.metadata.get("intent_constraints")
    if isinstance(constraints, dict):
        return constraints
    sm = strategy.metadata.get("strategy_metadata") or {}
    constraints = sm.get("intent_constraints")
    if isinstance(constraints, dict):
        return constraints
    return {}


def _calc_semantic_intent_adj(
    strategy: ResolvedStrategy,
    constraints: Dict[str, Any],
    net_greeks: Dict[str, Any],
) -> float:
    if not constraints:
        return 1.0

    st = strategy.strategy_type
    adj = 1.0
    net_theta = net_greeks.get("net_theta")
    net_delta = net_greeks.get("net_delta")
    income_bias = bool(constraints.get("require_positive_theta") or constraints.get("prefer_income_family"))
    ban_naked = bool(constraints.get("ban_naked_short") or constraints.get("defined_risk_only"))
    directional_backup = bool(constraints.get("prefer_directional_backup"))
    range_bias = constraints.get("range_bias")
    neutral_income = bool(
        income_bias
        and range_bias in ("strict_range", "weak_bearish_range", "weak_bullish_range")
    )

    if ban_naked and st in _NAKED_SHORT_STRATEGIES:
        adj *= 0.55

    if income_bias:
        if st in _DEFINED_RISK_INCOME_STRATEGIES:
            adj *= 1.08 if (net_theta is not None and net_theta > 0) else 1.03
        elif st in _DIRECTIONAL_DEBIT_STRATEGIES:
            adj *= 0.92 if directional_backup else 0.78
            if net_theta is not None and net_theta < 0:
                adj *= 0.94

    if neutral_income:
        abs_delta = abs(float(net_delta or 0.0))
        if range_bias == "strict_range" and st in _RANGE_INCOME_STRATEGIES:
            if abs_delta <= 0.12:
                adj = max(adj, 1.10)
            elif abs_delta <= 0.20:
                adj = max(adj, 1.08)
            else:
                adj = max(adj, 1.06)
        elif range_bias == "strict_range" and st in _SINGLE_SIDE_CREDIT_STRATEGIES:
            adj = min(adj, 0.82 if abs_delta > 0.12 else 0.90)
        elif range_bias == "weak_bearish_range":
            if st == "bear_call_spread":
                adj = min(1.10, max(adj, 1.04) * 1.02)
            elif st in _RANGE_INCOME_STRATEGIES:
                adj = min(adj, 1.02)
            elif st == "bull_put_spread":
                adj = min(adj, 0.94)
        elif range_bias == "weak_bullish_range":
            if st == "bull_put_spread":
                adj = min(1.10, max(adj, 1.04) * 1.02)
            elif st in _RANGE_INCOME_STRATEGIES:
                adj = min(adj, 1.02)
            elif st == "bear_call_spread":
                adj = min(adj, 0.94)

    return round(max(0.70, min(1.10, adj)), 4)


def _calc_horizon_alignment_adj(
    strategy: ResolvedStrategy,
    constraints: Dict[str, Any],
) -> float:
    horizon_views = constraints.get("horizon_views") if isinstance(constraints, dict) else None
    if not isinstance(horizon_views, dict):
        return 1.0

    st = strategy.strategy_type
    if st not in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        return 1.0

    short = horizon_views.get("short_term") if isinstance(horizon_views.get("short_term"), dict) else {}
    medium = horizon_views.get("medium_term") if isinstance(horizon_views.get("medium_term"), dict) else {}
    short_direction = short.get("direction", "unknown")
    medium_direction = medium.get("direction", "unknown")
    short_vol = short.get("vol_bias", "unknown")
    medium_vol = medium.get("vol_bias", "unknown")

    direction_divergence = short_direction in ("bearish", "bullish") and medium_direction in ("neutral", "unknown")
    vol_term_signal = short_vol == "up" and medium_vol in ("down", "flat", "unknown")
    if not direction_divergence and not vol_term_signal:
        return 1.0

    adj = 1.0
    if vol_term_signal:
        adj *= 1.05 if st in ("call_calendar", "put_calendar") else 1.03

    if direction_divergence:
        preferred_side = "put" if short_direction == "bearish" else "call"
        if preferred_side == "put" and st in ("put_calendar", "diagonal_put"):
            adj *= 1.07 if st == "diagonal_put" else 1.05
        elif preferred_side == "call" and st in ("call_calendar", "diagonal_call"):
            adj *= 1.07 if st == "diagonal_call" else 1.05
        else:
            adj *= 0.98

    return round(max(0.95, min(1.08, adj)), 4)


def _calc_vol_detail_alignment_adj(
    strategy: ResolvedStrategy,
    constraints: Dict[str, Any],
) -> float:
    detail = constraints.get("vol_view_detail") if isinstance(constraints, dict) else None
    if not isinstance(detail, dict):
        return 1.0

    st = strategy.strategy_type
    atm = detail.get("atm") if isinstance(detail.get("atm"), dict) else {}
    call = detail.get("call") if isinstance(detail.get("call"), dict) else {}
    put = detail.get("put") if isinstance(detail.get("put"), dict) else {}
    skew = detail.get("skew") if isinstance(detail.get("skew"), dict) else {}
    term = detail.get("term") if isinstance(detail.get("term"), dict) else {}

    atm_short_up = (
        atm.get("expected_change") == "up"
        and atm.get("horizon", "unknown") in ("short_term", "unknown")
    )
    term_front_rich = term.get("front") == "rich"
    put_rich = put.get("level") == "rich" or skew.get("direction") == "put_rich"
    call_flat_down = call.get("expected_change") in ("flat", "down")
    call_cheap = call.get("level") == "cheap"

    adj = 1.0
    if st in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        if atm_short_up:
            adj *= 1.05 if st in ("call_calendar", "put_calendar") else 1.03
        if term_front_rich:
            adj *= 1.04 if st in ("call_calendar", "put_calendar") else 1.02

    if put_rich:
        if st == "bear_call_spread":
            adj *= 1.04
        elif st in ("bull_put_spread", "naked_put"):
            adj *= 0.96
        elif st in ("bear_put_spread", "long_put"):
            adj *= 0.98

    if call_flat_down:
        if st == "bear_call_spread":
            adj *= 1.03
        elif st in ("bull_call_spread", "long_call"):
            adj *= 0.96

    if call_cheap and st in ("bull_call_spread", "long_call", "diagonal_call"):
        adj *= 1.04

    return round(max(0.94, min(1.08, adj)), 4)


# ============================================================
# Greeks意图调整（greeks_intent_adj）
# ============================================================

def _calc_greeks_intent_adj(
    strategy: ResolvedStrategy,
    greeks_preference: Dict[str, Any],
    net_greeks: Dict[str, Any],
) -> float:
    """
    根据用户的Greeks意图偏好调整评分，给匹配项有限奖励、给错配项保留惩罚。

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
    greeks_adj 范围：[0.70, 1.10]，匹配最多+10%，错配最多-30%。

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

    intent_score = max(-0.5, min(1.0, weighted_match / total_weight))
    if intent_score >= 0:
        greeks_adj = 1.0 + 0.10 * intent_score
    else:
        greeks_adj = 1.0 + 0.60 * intent_score
    return round(max(0.70, min(1.10, greeks_adj)), 4)


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
    iv_score: float    = 0.5
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

        iv_signal = _extract_strategy_aware_iv_signal(strategy)
        iv_alignment_score = float(iv_signal.get("iv_alignment_score", 0.5) or 0.5)
        iv_opportunity_adj = 1.0 + 0.20 * (iv_alignment_score - 0.5)
        base_score *= iv_opportunity_adj

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
        intent_constraints = _extract_intent_constraints(strategy)
        semantic_intent_adj = _calc_semantic_intent_adj(strategy, intent_constraints, greeks)
        horizon_alignment_adj = _calc_horizon_alignment_adj(strategy, intent_constraints)
        vol_detail_alignment_adj = _calc_vol_detail_alignment_adj(strategy, intent_constraints)

        # ── 4. prior weight ──
        prior     = _extract_prior_weight(strategy)
        prior_adj = 0.7 + 0.3 * prior

        # ── 5. final score ──
        # 公式：base × greeks_adj × greeks_intent_adj × semantic_intent_adj × horizon_alignment_adj × vol_detail_alignment_adj × prior_adj
        final_score = base_score * adj * greeks_intent_adj * semantic_intent_adj * horizon_alignment_adj * vol_detail_alignment_adj * prior_adj
        ranking_components = _build_ranking_components(
            breakdown=breakdown,
            greeks_adj=adj,
            greeks_intent_adj=greeks_intent_adj,
            prior_adj=prior_adj,
        )

        breakdown["greeks_adj"]        = round(adj, 4)
        breakdown["greeks_intent_adj"] = round(greeks_intent_adj, 4)
        breakdown["semantic_intent_adj"] = round(semantic_intent_adj, 4)
        breakdown["horizon_alignment_adj"] = round(horizon_alignment_adj, 4)
        breakdown["vol_detail_alignment_adj"] = round(vol_detail_alignment_adj, 4)
        breakdown["prior"]             = round(prior, 4)
        breakdown["prior_adj"]         = round(prior_adj, 4)
        breakdown["iv_side_used"]      = iv_signal["iv_side_used"]
        breakdown["iv_percentile_used"] = iv_signal["iv_percentile_used"]
        breakdown["iv_signal_strength"] = iv_signal["iv_signal_strength"]
        breakdown["iv_alignment_score"] = iv_signal["iv_alignment_score"]
        breakdown["iv_expression"]      = iv_signal["iv_expression"]
        breakdown["iv_opportunity_adj"] = round(iv_opportunity_adj, 4)
        if "iv_score" in breakdown:
            breakdown["iv_score"] = round(iv_alignment_score, 4)
        breakdown["opportunity_fit"]   = ranking_components["opportunity_fit"]
        breakdown["structure_quality"] = ranking_components["structure_quality"]
        breakdown["execution_quality"] = ranking_components["execution_quality"]

        strategy.score           = round(final_score, 4)
        strategy.score_breakdown = breakdown
        strategy.metadata = strategy.metadata or {}
        strategy.metadata["ranking_components"] = ranking_components
        strategy.metadata["ranking_components"].update({
            "iv_side_used": iv_signal["iv_side_used"],
            "iv_percentile_used": iv_signal["iv_percentile_used"],
            "iv_signal_strength": iv_signal["iv_signal_strength"],
            "iv_alignment_score": iv_signal["iv_alignment_score"],
            "iv_expression": iv_signal["iv_expression"],
            "vol_detail_alignment_adj": round(vol_detail_alignment_adj, 4),
        })

        print(
            f"[rank] {st:<22} base={base_score:.3f} "
            f"adj={adj:.3f} intent_adj={greeks_intent_adj:.3f} "
            f"semantic_adj={semantic_intent_adj:.3f} "
            f"horizon_adj={horizon_alignment_adj:.3f} "
            f"vol_detail_adj={vol_detail_alignment_adj:.3f} "
            f"prior={prior:.2f} final={final_score:.3f}"
        )
        print(
            f"[rank_explain] strategy={st} "
            f"opp={ranking_components['opportunity_fit']:.3f} "
            f"structure={ranking_components['structure_quality']:.3f} "
            f"exec={ranking_components['execution_quality']:.3f} "
            f"final={final_score:.3f}"
        )
        print(
            f"[rank_iv_refine] strategy={st} "
            f"iv_side={iv_signal['iv_side_used']} "
            f"iv_pct={iv_signal['iv_percentile_used']} "
            f"iv_signal={iv_signal['iv_signal_strength']:.3f} "
            f"final={final_score:.3f}"
        )

        ranked.append(strategy)

    ranked.sort(key=lambda x: x.score or 0.0, reverse=True)
    return ranked
