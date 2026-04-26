"""
greeks_monitor.py - strategy Greeks report generation.

This module only summarizes already resolved strategy Greeks. It does not
change strategy selection, ranking, or numerical outputs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.engine import Engine

from app.models.schemas import ResolvedStrategy
from app.strategy.iv_percentile import build_iv_percentile_report


def _leg_sign(action: str) -> int:
    return 1 if action == "BUY" else -1


def compute_strategy_net_greeks(strategy: ResolvedStrategy) -> Dict[str, float | None]:
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    has_delta = has_gamma = has_theta = has_vega = False

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
        return {"near_iv": None, "far_iv": None, "iv_diff": None}

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

    if strategy.strategy_type in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        if net_vega is not None and net_vega <= 0:
            flags.append("vega_not_positive_for_calendar")
        if net_theta is not None and net_theta < -0.002:
            flags.append("theta_too_negative")
        if iv_diff is not None and iv_diff > -0.002:
            flags.append("term_signal_weak")

    return flags


def build_greeks_commentary(
    strategy: ResolvedStrategy,
    iv_pct_report: Optional[Dict[str, Any]] = None,
) -> str:
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
            parts.append("组合整体接近 delta 中性")
        elif net_delta > 0:
            parts.append("组合略偏多 delta")
        else:
            parts.append("组合略偏空 delta")

    if net_vega is not None:
        parts.append("组合为净多 vega" if net_vega > 0 else "组合为净空 vega")

    if net_theta is not None:
        parts.append("组合 theta 为正" if net_theta > 0 else "组合 theta 为负")

    if net_gamma is not None:
        if net_gamma < -0.5:
            parts.append("组合呈现较明显净空 gamma 特征")
        elif net_gamma < 0:
            parts.append("组合轻微净空 gamma")
        elif net_gamma > 0.5:
            parts.append("组合呈现较明显净多 gamma 特征")

    if iv_diff is not None:
        if iv_diff <= -0.005:
            parts.append("近远月隐波结构对 calendar 较为有利")
        elif iv_diff < 0:
            parts.append("近远月隐波结构略偏有利")
        else:
            parts.append("近远月隐波结构信号偏弱")

    if iv_pct_report:
        label = iv_pct_report.get("label", "")
        pct = iv_pct_report.get("composite_percentile")
        if pct is not None:
            parts.append(f"当前 ATM IV 处于{label}水平（{pct:.0%}分位）")

    return "，".join(parts) + "。"


def build_strategy_greeks_report(
    strategy: ResolvedStrategy,
    engine: Optional[Engine] = None,
    iv_pct_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a strategy Greeks report."""
    final_iv_pct_report = iv_pct_report

    if final_iv_pct_report is None and engine is not None:
        try:
            final_iv_pct_report = build_iv_percentile_report(
                engine=engine,
                underlying_id=strategy.underlying_id,
            )
        except Exception as exc:
            print(f"[greeks_monitor] iv_percentile failed: {exc}")

    report: Dict[str, Any] = {
        "strategy_type": strategy.strategy_type,
        "underlying_id": strategy.underlying_id,
        "spot_price": strategy.spot_price,
        "net_greeks": compute_strategy_net_greeks(strategy),
        "risk_flags": build_risk_flags(strategy),
        "commentary": build_greeks_commentary(strategy, final_iv_pct_report),
    }

    if strategy.strategy_type in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        report["term_structure"] = build_calendar_term_structure(strategy)

    if final_iv_pct_report is not None:
        report["iv_percentile"] = final_iv_pct_report

    return report
