from __future__ import annotations

from typing import Any, Dict, List

from app.models.schemas import ResolvedStrategy


def _leg_sign(action: str) -> int:
    return 1 if action == "BUY" else -1


def compute_strategy_net_greeks(strategy: ResolvedStrategy) -> Dict[str, float | None]:
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    has_delta = False
    has_gamma = False
    has_theta = False
    has_vega = False

    for leg in strategy.legs:
        sign = _leg_sign(leg.action)
        qty = leg.quantity or 1

        if leg.delta is not None:
            net_delta += sign * qty * leg.delta
            has_delta = True

        if leg.gamma is not None:
            net_gamma += sign * qty * leg.gamma
            has_gamma = True

        if leg.theta is not None:
            net_theta += sign * qty * leg.theta
            has_theta = True

        if leg.vega is not None:
            net_vega += sign * qty * leg.vega
            has_vega = True

    return {
        "net_delta": round(net_delta, 6) if has_delta else None,
        "net_gamma": round(net_gamma, 6) if has_gamma else None,
        "net_theta": round(net_theta, 6) if has_theta else None,
        "net_vega": round(net_vega, 6) if has_vega else None,
    }


def build_calendar_term_structure(strategy: ResolvedStrategy) -> Dict[str, float | None]:
    if len(strategy.legs) < 2:
        return {
            "near_iv": None,
            "far_iv": None,
            "iv_diff": None,
        }

    near_leg = strategy.legs[0]
    far_leg = strategy.legs[1]

    iv_diff = None
    if near_leg.iv is not None and far_leg.iv is not None:
        iv_diff = far_leg.iv - near_leg.iv

    return {
        "near_iv": round(near_leg.iv, 6) if near_leg.iv is not None else None,
        "far_iv": round(far_leg.iv, 6) if far_leg.iv is not None else None,
        "iv_diff": round(iv_diff, 6) if iv_diff is not None else None,
    }


def build_risk_flags(strategy: ResolvedStrategy) -> List[str]:
    flags: List[str] = []

    greeks = compute_strategy_net_greeks(strategy)
    ts = build_calendar_term_structure(strategy)

    net_delta = greeks["net_delta"]
    net_gamma = greeks["net_gamma"]
    net_theta = greeks["net_theta"]
    net_vega = greeks["net_vega"]
    iv_diff = ts["iv_diff"]

    if net_delta is not None and abs(net_delta) > 0.10:
        flags.append("delta_not_neutral")

    if net_gamma is not None and net_gamma < -0.5:
        flags.append("short_gamma")

    if strategy.strategy_type in ("call_calendar", "put_calendar"):
        if net_vega is not None and net_vega <= 0:
            flags.append("vega_not_positive_for_calendar")

        if net_theta is not None and net_theta < -0.002:
            flags.append("theta_too_negative")

        if iv_diff is not None and iv_diff > -0.002:
            flags.append("term_signal_weak")

    return flags


def build_strategy_greeks_report(strategy: ResolvedStrategy) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "strategy_type": strategy.strategy_type,
        "underlying_id": strategy.underlying_id,
        "spot_price": strategy.spot_price,
        "net_greeks": compute_strategy_net_greeks(strategy),
        "risk_flags": build_risk_flags(strategy),
        "commentary": build_greeks_commentary(strategy),
    }

    if strategy.strategy_type in ("call_calendar", "put_calendar"):
        report["term_structure"] = build_calendar_term_structure(strategy)

    return report

def build_greeks_commentary(strategy: ResolvedStrategy) -> str:
    greeks = compute_strategy_net_greeks(strategy)
    ts = build_calendar_term_structure(strategy)

    parts = []

    net_delta = greeks.get("net_delta")
    net_gamma = greeks.get("net_gamma")
    net_theta = greeks.get("net_theta")
    net_vega = greeks.get("net_vega")
    iv_diff = ts.get("iv_diff")

    if net_delta is not None:
        if abs(net_delta) <= 0.05:
            parts.append("组合整体接近delta中性")
        elif net_delta > 0:
            parts.append("组合略偏多delta")
        else:
            parts.append("组合略偏空delta")

    if net_vega is not None:
        if net_vega > 0:
            parts.append("组合为净多vega")
        elif net_vega < 0:
            parts.append("组合为净空vega")

    if net_theta is not None:
        if net_theta > 0:
            parts.append("组合theta为正")
        elif net_theta < 0:
            parts.append("组合theta为负")

    if net_gamma is not None:
        if net_gamma < -0.5:
            parts.append("组合呈现较明显净空gamma特征")
        elif net_gamma < 0:
            parts.append("组合轻微净空gamma")
        elif net_gamma > 0.5:
            parts.append("组合呈现较明显净多gamma特征")

    if iv_diff is not None:
        if iv_diff <= -0.005:
            parts.append("近远月隐波结构对该calendar较为有利")
        elif iv_diff < 0:
            parts.append("近远月隐波结构略偏有利")
        else:
            parts.append("近远月隐波结构信号偏弱")

    return "，".join(parts) + "。"