from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.schemas import (
    PositionLegInput,
    PositionMonitorRequest,
    PositionMonitorResponse,
    UnderlyingMonitorResponse,
)
from app.strategy.positions_service import list_position_legs, list_underlying_positions
from app.strategy.strategy_resolver import load_market_snapshot


CONTRACT_MULTIPLIER = 10000


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _to_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def _quote_mid(row: Dict[str, Any]) -> Optional[float]:
    mid = _safe_float(row.get("mid_price"))
    if mid is not None:
        return mid
    bid = _safe_float(row.get("bid_price1"))
    ask = _safe_float(row.get("ask_price1"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return _safe_float(row.get("option_market_price"))


def _leg_greek_contribution(side: str, greek_value: float, quantity: int) -> float:
    greek_sign = 1.0 if side == "BUY" else -1.0
    return greek_sign * float(greek_value or 0.0) * int(quantity or 0)


def _risk_greeks_from_raw(
    net_delta: float,
    net_gamma: float,
    net_theta: float,
    net_vega: float,
    spot: float,
) -> Dict[str, Any]:
    gamma_rmb_per_1pct_move = None
    if spot > 0:
        move = spot * 0.01
        gamma_rmb_per_1pct_move = 0.5 * net_gamma * move * move * CONTRACT_MULTIPLIER
    return {
        "contract_multiplier": CONTRACT_MULTIPLIER,
        "delta_share_equiv": round(net_delta * CONTRACT_MULTIPLIER, 2),
        "delta_rmb_per_1pct": round(net_delta * CONTRACT_MULTIPLIER * spot * 0.01, 2),
        "theta_rmb_per_day": round(net_theta * CONTRACT_MULTIPLIER, 2),
        "vega_rmb_per_1vol": round(net_vega * CONTRACT_MULTIPLIER * 0.01, 2),
        "gamma_rmb_per_1pct_move": round(gamma_rmb_per_1pct_move, 2) if gamma_rmb_per_1pct_move is not None else None,
        "gamma_rmb_per_1pct_move_approximate": gamma_rmb_per_1pct_move is not None,
    }


def _distance_risk_status(distance_pct: Optional[float]) -> str:
    if distance_pct is None:
        return "normal"
    if distance_pct <= 0.01:
        return "alert"
    if distance_pct <= 0.03:
        return "watch"
    return "normal"


def _expiry_risk_status(min_dte: Optional[int], net_gamma: float) -> tuple[str, int, str]:
    score = 0
    if min_dte is not None:
        if min_dte <= 3:
            score += 3
        elif min_dte <= 7:
            score += 2
        elif min_dte <= 14:
            score += 1
    if net_gamma <= -2.0:
        score += 3
    elif net_gamma <= -1.0:
        score += 2
    elif net_gamma < 0:
        score += 1

    if score >= 4:
        return "alert", score, "near_expiry_short_gamma"
    if score >= 2:
        return "watch", score, "expiry_gamma_watch"
    return "normal", score, "expiry_risk_normal"


def _coverage_status(covered_ratio: float) -> str:
    if covered_ratio >= 1.0:
        return "covered"
    if covered_ratio > 0:
        return "partially_covered"
    return "uncovered"


def _underlying_summary_from_positions(
    positions: list[Any],
    spot: float,
) -> Dict[str, Any]:
    active = [
        pos
        for pos in positions
        if getattr(pos, "include_in_portfolio_greeks", True)
        and str(getattr(pos, "status", "OPEN")).upper() == "OPEN"
        and int(getattr(pos, "shares", 0) or 0) > 0
    ]
    shares = sum(int(getattr(pos, "shares", 0) or 0) for pos in active)
    entry_value = sum(
        int(getattr(pos, "shares", 0) or 0) * float(getattr(pos, "avg_entry_price", 0.0) or 0.0)
        for pos in active
    )
    avg_entry = entry_value / shares if shares > 0 else None
    pnl_estimate = (spot - avg_entry) * shares if avg_entry is not None else 0.0
    return {
        "underlying_position_count": len(active),
        "shares": shares,
        "avg_entry_price": round(avg_entry, 6) if avg_entry is not None else None,
        "spot": round(spot, 4),
        "pnl_estimate_rmb": round(pnl_estimate, 2),
        "include_in_portfolio_greeks": bool(active),
        "delta_share_equiv": shares,
        "delta_rmb_per_1pct": round(shares * spot * 0.01, 2),
        "theta_rmb_per_day": 0.0,
        "vega_rmb_per_1vol": 0.0,
        "gamma_rmb_per_1pct_move": 0.0,
    }


def _underlying_risk_leg(summary: Dict[str, Any]) -> Optional[dict[str, Any]]:
    shares = int(summary.get("shares") or 0)
    if shares <= 0:
        return None
    return {
        "leg_id": None,
        "contract_id": "UNDERLYING_SHARES",
        "side": "BUY",
        "option_type": "UNDERLYING",
        "strike": None,
        "expiry_date": "underlying",
        "quantity": shares,
        "pnl_estimate_rmb": summary.get("pnl_estimate_rmb") or 0.0,
        "delta_contribution": shares / CONTRACT_MULTIPLIER,
        "gamma_contribution": 0.0,
        "theta_contribution": 0.0,
        "vega_contribution": 0.0,
        "dte": None,
        "strategy_bucket": "underlying_position",
        "group_id": "underlying_position",
        "tag": "underlying_shares",
    }


def _build_covered_call_coverage(
    option_legs: list[dict[str, Any]],
    underlying_shares: int,
) -> Dict[str, Any]:
    short_calls = [
        leg
        for leg in option_legs
        if leg.get("side") == "SELL"
        and str(leg.get("option_type") or "").upper() in ("CALL", "C")
    ]
    total_short_contracts = sum(int(leg.get("quantity") or 0) for leg in short_calls)
    required_shares = total_short_contracts * CONTRACT_MULTIPLIER
    covered_ratio = underlying_shares / required_shares if required_shares > 0 else None
    uncovered_contracts = 0.0
    if required_shares > 0:
        uncovered_shares = max(required_shares - underlying_shares, 0)
        uncovered_contracts = uncovered_shares / CONTRACT_MULTIPLIER
    aggregate_status = _coverage_status(float(covered_ratio or 0.0)) if required_shares > 0 else "no_short_call"

    rows: list[dict[str, Any]] = []
    remaining_shares = max(underlying_shares, 0)
    for leg in sorted(short_calls, key=lambda item: (item.get("expiry_date") or "", float(item.get("strike") or 0.0))):
        contracts = int(leg.get("quantity") or 0)
        required = contracts * CONTRACT_MULTIPLIER
        covered_shares = min(remaining_shares, required)
        leg_ratio = covered_shares / required if required > 0 else 0.0
        remaining_shares -= covered_shares
        rows.append({
            "leg_id": leg.get("leg_id"),
            "contract_id": leg.get("contract_id"),
            "strategy_bucket": leg.get("strategy_bucket"),
            "group_id": leg.get("group_id"),
            "strike": leg.get("strike"),
            "expiry_date": leg.get("expiry_date"),
            "short_call_contracts": contracts,
            "required_shares": required,
            "covered_shares": covered_shares,
            "covered_ratio": round(leg_ratio, 4),
            "uncovered_short_call_contracts": round(max(required - covered_shares, 0) / CONTRACT_MULTIPLIER, 4),
            "coverage_status": _coverage_status(leg_ratio),
        })

    return {
        "rows": rows,
        "covered_ratio": round(covered_ratio, 4) if covered_ratio is not None else None,
        "uncovered_short_call_contracts": round(uncovered_contracts, 4),
        "covered_call_risk_status": aggregate_status,
        "underlying_shares": underlying_shares,
        "short_call_contracts": total_short_contracts,
        "required_shares": required_shares,
    }


def _aggregate_leg_risk(
    legs: list[dict[str, Any]],
    spot: float,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for leg in legs:
        if key_name == "strategy_group":
            key = str(leg.get("group_id") or leg.get("strategy_bucket") or "ungrouped")
        else:
            key = str(leg.get(key_name) or "ungrouped")
        item = grouped.setdefault(
            key,
            {
                key_name: key,
                "leg_count": 0,
                "short_leg_count": 0,
                "pnl_estimate_rmb": 0.0,
                "net_delta": 0.0,
                "net_gamma": 0.0,
                "net_theta": 0.0,
                "net_vega": 0.0,
                "min_dte": None,
            },
        )
        item["leg_count"] += 1
        if leg.get("side") == "SELL":
            item["short_leg_count"] += 1
        item["pnl_estimate_rmb"] += float(leg.get("pnl_estimate_rmb") or 0.0)
        item["net_delta"] += float(leg.get("delta_contribution") or 0.0)
        item["net_gamma"] += float(leg.get("gamma_contribution") or 0.0)
        item["net_theta"] += float(leg.get("theta_contribution") or 0.0)
        item["net_vega"] += float(leg.get("vega_contribution") or 0.0)
        dte = leg.get("dte")
        if dte is not None:
            dte = int(dte)
            item["min_dte"] = dte if item["min_dte"] is None else min(item["min_dte"], dte)

    output: list[dict[str, Any]] = []
    for item in grouped.values():
        risk_greeks = _risk_greeks_from_raw(
            item["net_delta"],
            item["net_gamma"],
            item["net_theta"],
            item["net_vega"],
            spot,
        )
        expiry_status, expiry_score, reason_code = _expiry_risk_status(item["min_dte"], item["net_gamma"])
        output.append({
            **item,
            "group_id": item.get("strategy_group") if key_name == "strategy_group" else item.get("group_id"),
            "pnl_estimate_rmb": round(item["pnl_estimate_rmb"], 2),
            "net_delta": round(item["net_delta"], 6),
            "net_gamma": round(item["net_gamma"], 6),
            "net_theta": round(item["net_theta"], 6),
            "net_vega": round(item["net_vega"], 6),
            **risk_greeks,
            "expiry_risk_status": expiry_status,
            "expiry_risk_score": expiry_score,
            "reason_code": reason_code,
        })
    return sorted(output, key=lambda x: (-x["expiry_risk_score"], str(x.get(key_name))))


def _build_short_strike_risk_map(
    legs: list[dict[str, Any]],
    spot: float,
    covered_call_coverage: Optional[Dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    coverage_by_leg = {
        row.get("leg_id"): row
        for row in (covered_call_coverage or {}).get("rows", [])
        if row.get("leg_id") is not None
    }
    risks: list[dict[str, Any]] = []
    for leg in legs:
        if leg.get("side") != "SELL":
            continue
        option_type = str(leg.get("option_type") or "").upper()
        if option_type not in ("CALL", "PUT", "C", "P"):
            continue
        strike = _safe_float(leg.get("strike"))
        if strike is None or spot <= 0:
            continue
        distance_pct = abs(strike - spot) / spot
        status = _distance_risk_status(distance_pct)
        coverage = coverage_by_leg.get(leg.get("leg_id"), {})
        is_short_call = option_type in ("CALL", "C")
        row = {
            "leg_id": leg.get("leg_id"),
            "contract_id": leg.get("contract_id"),
            "option_type": option_type,
            "strike": strike,
            "expiry_date": leg.get("expiry_date"),
            "dte": leg.get("dte"),
            "group_id": leg.get("group_id"),
            "strategy_bucket": leg.get("strategy_bucket"),
            "delta_contribution": leg.get("delta_contribution"),
            "gamma_contribution": leg.get("gamma_contribution"),
            "distance_to_short_strike": round(distance_pct, 6),
            "status": status,
            "reason_code": f"short_strike_distance_{status}",
        }
        if is_short_call and coverage:
            row.update({
                "covered_ratio": coverage.get("covered_ratio"),
                "coverage_status": coverage.get("coverage_status"),
                "uncovered_short_call_contracts": coverage.get("uncovered_short_call_contracts"),
                "covered_call_risk_status": coverage.get("coverage_status"),
                "assignment_risk": status in ("alert", "watch"),
                "assignment_risk_note": (
                    "spot_near_short_call_strike_review_assignment_roll_or_accept_delivery"
                    if status in ("alert", "watch")
                    else None
                ),
            })
        risks.append(row)
    return sorted(risks, key=lambda x: (x["distance_to_short_strike"], x.get("dte") if x.get("dte") is not None else 999999))


def _build_management_suggestions(
    portfolio_summary: dict[str, Any],
    group_breakdown: list[dict[str, Any]],
    expiry_breakdown: list[dict[str, Any]],
    short_strike_map: list[dict[str, Any]],
    pnl_pct: Optional[float],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    largest_delta_group = max(group_breakdown, key=lambda g: abs(float(g.get("net_delta") or 0.0)), default={})

    if abs(float(portfolio_summary.get("net_delta") or 0.0)) >= 1.0:
        suggestions.append({
            "goal": "reduce_delta",
            "trigger": f"net_delta={portfolio_summary.get('net_delta')}",
            "suggested_action": "review directional exposure and reduce delta concentration if it no longer matches the plan",
            "related_group_id": largest_delta_group.get("group_id") or largest_delta_group.get("strategy_bucket"),
            "reason_code": "portfolio_delta_high",
        })

    alert_short = next((item for item in short_strike_map if item["status"] == "alert"), None)
    alert_expiry = next((item for item in expiry_breakdown if item["expiry_risk_status"] == "alert"), None)
    covered_call_alert = next(
        (
            item
            for item in short_strike_map
            if item.get("option_type") in ("CALL", "C")
            and item.get("coverage_status") in ("covered", "partially_covered")
            and item.get("status") in ("alert", "watch")
        ),
        None,
    )
    if covered_call_alert:
        suggestions.append({
            "goal": "take_profit_or_roll",
            "trigger": "covered short call is close to strike",
            "suggested_action": "review assignment risk, whether to roll up/out, or whether accepting delivery is intended",
            "related_leg_id": covered_call_alert.get("leg_id"),
            "related_group_id": covered_call_alert.get("group_id"),
            "reason_code": "covered_call_assignment_or_roll_watch",
        })
    if alert_short or alert_expiry:
        suggestions.append({
            "goal": "reduce_gamma",
            "trigger": "short strike is close or near-expiry short gamma is elevated",
            "suggested_action": "review whether gamma exposure should be reduced or rolled before expiry pressure increases",
            "related_leg_id": alert_short.get("leg_id") if alert_short else None,
            "related_group_id": alert_expiry.get("expiry_date") if alert_expiry else None,
            "reason_code": "short_gamma_or_short_strike_alert",
        })

    if abs(float(portfolio_summary.get("net_vega") or 0.0)) >= 0.10:
        suggestions.append({
            "goal": "reduce_vega",
            "trigger": f"net_vega={portfolio_summary.get('net_vega')}",
            "suggested_action": "review volatility exposure and avoid unintended concentration in one vol direction",
            "related_group_id": largest_delta_group.get("group_id") or largest_delta_group.get("strategy_bucket"),
            "reason_code": "portfolio_vega_exposure_high",
        })

    watch_expiry = next((item for item in expiry_breakdown if item["expiry_risk_status"] in ("alert", "watch")), None)
    if (pnl_pct is not None and pnl_pct >= 0.50) or watch_expiry:
        suggestions.append({
            "goal": "take_profit_or_roll",
            "trigger": f"pnl_pct={round(pnl_pct, 4) if pnl_pct is not None else None}, expiry_risk={watch_expiry.get('expiry_risk_status') if watch_expiry else None}",
            "suggested_action": "review whether to take profit, reduce size, or roll before expiry risk dominates",
            "related_group_id": watch_expiry.get("expiry_date") if watch_expiry else None,
            "reason_code": "profit_or_expiry_roll_watch",
        })

    if not suggestions:
        suggestions.append({
            "goal": "hold_and_watch",
            "trigger": "no major portfolio risk threshold triggered",
            "suggested_action": "continue monitoring spot distance, DTE, Greeks, and liquidity",
            "related_group_id": None,
            "reason_code": "no_action_threshold_triggered",
        })
    return suggestions


def _build_underlying_monitor_v2(
    *,
    spot: float,
    monitored_legs: list[dict[str, Any]],
    total_pnl: float,
    pnl_pct: Optional[float],
    net_delta: float,
    net_gamma: float,
    net_theta: float,
    net_vega: float,
    covered_call_coverage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    total_risk_greeks = _risk_greeks_from_raw(net_delta, net_gamma, net_theta, net_vega, spot)
    portfolio_risk_summary = {
        "spot": round(spot, 4),
        "monitored_leg_count": len(monitored_legs),
        "pnl_estimate": round(total_pnl, 2),
        "pnl_pct_of_entry_premium": round(pnl_pct, 4) if pnl_pct is not None else None,
        "net_delta": round(net_delta, 6),
        "net_gamma": round(net_gamma, 6),
        "net_theta": round(net_theta, 6),
        "net_vega": round(net_vega, 6),
        "total_risk_greeks": total_risk_greeks,
    }
    group_breakdown = _aggregate_leg_risk(monitored_legs, spot, "strategy_group")
    expiry_breakdown = _aggregate_leg_risk(monitored_legs, spot, "expiry_date")
    short_strike_map = _build_short_strike_risk_map(monitored_legs, spot, covered_call_coverage)
    most_dangerous_legs = short_strike_map[:5]
    portfolio_risk_summary["most_dangerous_legs"] = most_dangerous_legs
    portfolio_risk_summary["distance_to_short_strike"] = most_dangerous_legs[0] if most_dangerous_legs else None
    portfolio_risk_summary["expiry_risk"] = expiry_breakdown[0] if expiry_breakdown else None

    return {
        "portfolio_risk_summary": portfolio_risk_summary,
        "group_risk_breakdown": group_breakdown,
        "expiry_risk_breakdown": expiry_breakdown,
        "short_strike_risk_map": short_strike_map,
        "management_suggestions": _build_management_suggestions(
            portfolio_risk_summary,
            group_breakdown,
            expiry_breakdown,
            short_strike_map,
            pnl_pct,
        ),
    }


def _find_current_quote(snapshot: Any, leg: PositionLegInput) -> Optional[Dict[str, Any]]:
    leg_contract_id = str(leg.contract_id) if leg.contract_id is not None else None
    if leg_contract_id:
        for row in snapshot.merged_quotes:
            if str(row.get("contract_id")) == leg_contract_id:
                return row

    target_expiry = _to_date(leg.expiry)
    for row in snapshot.merged_quotes:
        if str(row.get("option_type", "")).upper() != leg.option_type:
            continue
        if abs(float(row.get("strike") or 0.0) - float(leg.strike)) > 1e-6:
            continue
        if target_expiry is not None and _to_date(row.get("expiry_date")) != target_expiry:
            continue
        return row
    return None


def _leg_ratio(leg_quantity: int, position_quantity: int) -> float:
    q = abs(int(leg_quantity or 1))
    pq = abs(int(position_quantity or 1))
    if pq > 0 and q >= pq:
        return q / pq
    return float(q)


def _entry_premium_per_unit(position: PositionMonitorRequest) -> float:
    if position.entry_credit_or_debit is not None:
        amount = abs(float(position.entry_credit_or_debit))
        pricing_type = position.pricing_type
        if pricing_type is None:
            pricing_type = "credit" if _looks_like_credit_strategy(position.strategy_type) else "debit"
        return amount if pricing_type == "credit" else -amount

    total = 0.0
    for leg in position.legs:
        if leg.entry_price is None:
            continue
        ratio = _leg_ratio(leg.quantity, position.quantity)
        value = float(leg.entry_price) * ratio
        total += value if leg.side == "SELL" else -value
    return total


def _looks_like_credit_strategy(strategy_type: str) -> bool:
    return strategy_type in {
        "bear_call_spread",
        "bull_put_spread",
        "iron_condor",
        "iron_fly",
        "naked_call",
        "naked_put",
        "covered_call",
    }


def _status_from_flags(flags: list[str]) -> str:
    alert_flags = {"dte_critical", "spot_at_short_strike", "delta_exposure_high"}
    if any(flag in alert_flags for flag in flags):
        return "alert"
    return "watch" if flags else "normal"


def _recommended_action(status: str, pnl_pct: Optional[float], pricing_type: str) -> str:
    if status == "alert":
        return "reduce_risk"
    if pnl_pct is not None and pnl_pct >= 0.50:
        return "consider_take_profit"
    if status == "watch":
        return "watch"
    return "hold"


def monitor_position(engine: Engine, position: PositionMonitorRequest) -> PositionMonitorResponse:
    snapshot = load_market_snapshot(engine, position.underlying_id)
    spot = float(snapshot.spot)

    current_legs: list[dict[str, Any]] = []
    current_premium_per_unit = 0.0
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0
    min_dte: Optional[int] = None
    short_distances: list[float] = []
    missing_quotes: list[str] = []

    for leg in position.legs:
        row = _find_current_quote(snapshot, leg)
        ratio = _leg_ratio(leg.quantity, position.quantity)
        premium_sign = 1.0 if leg.side == "SELL" else -1.0
        greek_sign = 1.0 if leg.side == "BUY" else -1.0
        if row is None:
            missing_quotes.append(leg.contract_id or f"{leg.option_type}-{leg.strike}-{leg.expiry}")
            continue

        mid = _quote_mid(row)
        if mid is None:
            missing_quotes.append(str(row.get("contract_id")))
            continue

        current_premium_per_unit += premium_sign * mid * ratio
        delta = _safe_float(row.get("delta"), 0.0) or 0.0
        gamma = _safe_float(row.get("gamma"), 0.0) or 0.0
        theta = _safe_float(row.get("theta"), 0.0) or 0.0
        vega = _safe_float(row.get("vega"), 0.0) or 0.0
        net_delta += greek_sign * delta * ratio
        net_gamma += greek_sign * gamma * ratio
        net_theta += greek_sign * theta * ratio
        net_vega += greek_sign * vega * ratio

        dte = row.get("dte_calendar")
        if dte is not None:
            dte = int(dte)
            min_dte = dte if min_dte is None else min(min_dte, dte)
        else:
            expiry_date = _to_date(row.get("expiry_date") or leg.expiry)
            if expiry_date is not None:
                dte = max((expiry_date - date.today()).days, 0)
                min_dte = dte if min_dte is None else min(min_dte, dte)

        distance_pct = None
        if leg.side == "SELL" and spot > 0:
            distance_pct = abs(float(leg.strike) - spot) / spot
            short_distances.append(distance_pct)

        current_legs.append({
            "contract_id": str(row.get("contract_id")),
            "side": leg.side,
            "option_type": leg.option_type,
            "strike": float(leg.strike),
            "expiry": str(row.get("expiry_date") or leg.expiry),
            "entry_price": leg.entry_price,
            "current_mid": round(mid, 6),
            "quantity": leg.quantity,
            "dte": dte,
            "delta": round(delta, 6),
            "gamma": round(gamma, 6),
            "theta": round(theta, 6),
            "vega": round(vega, 6),
            "distance_to_spot_pct": round(distance_pct, 4) if distance_pct is not None else None,
        })

    entry_premium_per_unit = _entry_premium_per_unit(position)
    position_qty = abs(int(position.quantity or 1))
    pnl_per_unit = entry_premium_per_unit - current_premium_per_unit
    pnl_estimate = pnl_per_unit * position_qty * CONTRACT_MULTIPLIER
    pricing_type = position.pricing_type or ("credit" if entry_premium_per_unit >= 0 else "debit")
    pnl_base = abs(entry_premium_per_unit) * position_qty * CONTRACT_MULTIPLIER
    pnl_pct = pnl_estimate / pnl_base if pnl_base > 0 else None

    risk_flags: list[str] = []
    notes: list[str] = []
    if missing_quotes:
        risk_flags.append("quote_missing")
        notes.append(f"{len(missing_quotes)} 条腿未匹配到最新报价，需要人工复核。")
    if min_dte is not None:
        if min_dte <= 3:
            risk_flags.append("dte_critical")
            notes.append("距离到期不足或等于 3 天，时间衰减和尾部风险都需要重点处理。")
        elif min_dte <= 7:
            risk_flags.append("dte_decay_fast")
            notes.append("距离到期不足或等于 7 天，建议提高监控频率。")
    if short_distances:
        nearest = min(short_distances)
        if nearest <= 0.01:
            risk_flags.append("spot_at_short_strike")
            notes.append("现价已经非常接近卖方行权价，组合方向风险上升。")
        elif nearest <= 0.03:
            risk_flags.append("spot_near_short_strike")
            notes.append("现价接近卖方行权价，建议观察是否需要减仓或调整。")
    if abs(net_delta) >= 0.50:
        risk_flags.append("delta_exposure_high")
        notes.append("组合净 delta 暴露偏高，当前更像方向性持仓。")
    elif abs(net_delta) >= 0.30:
        risk_flags.append("delta_exposure_watch")
        notes.append("组合净 delta 有所抬升，需关注标的继续单边移动。")
    if pnl_pct is not None and pnl_pct >= 0.50:
        notes.append("估算盈利已超过入场权利金/成本的 50%，可考虑止盈或降低风险。")
    if not notes:
        notes.append("当前未触发主要风险阈值，可继续按原计划持有并定期复核。")

    status = _status_from_flags(risk_flags)
    action = _recommended_action(status, pnl_pct, pricing_type)
    risk_greeks = _risk_greeks_from_raw(net_delta, net_gamma, net_theta, net_vega, spot)

    summary = {
        "status": status,
        "spot": round(spot, 4),
        "pricing_type": pricing_type,
        "entry_premium_per_unit": round(entry_premium_per_unit, 6),
        "current_premium_per_unit": round(current_premium_per_unit, 6),
        "pnl_estimate": round(pnl_estimate, 2),
        "pnl_pct_of_max_profit": round(pnl_pct, 4) if pnl_pct is not None else None,
        "net_delta": round(net_delta, 6),
        "net_gamma": round(net_gamma, 6),
        "net_theta": round(net_theta, 6),
        "net_vega": round(net_vega, 6),
        **risk_greeks,
        "dte": min_dte,
        "risk_flags": risk_flags,
        "recommended_action": action,
        "notes": notes,
    }

    return PositionMonitorResponse(
        position_id=position.position_id,
        underlying_id=position.underlying_id,
        strategy_type=position.strategy_type,
        monitoring_summary=summary,
        current_legs=current_legs,
    )


def _monitor_status(flags: list[str]) -> str:
    if any(flag in {"spot_at_short_strike", "dte_critical", "portfolio_delta_high"} for flag in flags):
        return "alert"
    return "watch" if flags else "normal"


def _monitor_action(status: str, pnl_pct: Optional[float]) -> str:
    if status == "alert":
        return "reduce_risk"
    if pnl_pct is not None and pnl_pct >= 0.50:
        return "consider_take_profit"
    if status == "watch":
        return "watch"
    return "hold"


def monitor_underlying_positions(engine: Engine, underlying_id: str) -> UnderlyingMonitorResponse:
    position_legs = [
        leg
        for leg in list_position_legs(engine, underlying_id=underlying_id, status="OPEN")
        if leg.include_in_portfolio_greeks and int(leg.quantity or 0) > 0
    ]
    underlying_positions = list_underlying_positions(engine, underlying_id=underlying_id, status="OPEN")
    snapshot = load_market_snapshot(engine, underlying_id)
    spot = float(snapshot.spot)
    underlying_position_summary = _underlying_summary_from_positions(underlying_positions, spot)
    underlying_risk_leg = _underlying_risk_leg(underlying_position_summary)

    monitored_legs: list[dict[str, Any]] = []
    risk_contributors: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    notes: list[str] = []
    total_pnl = 0.0
    total_entry_abs = 0.0
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0
    min_dte: Optional[int] = None
    short_distances: list[float] = []
    if underlying_risk_leg is not None:
        net_delta += float(underlying_risk_leg["delta_contribution"])
        total_pnl += float(underlying_position_summary.get("pnl_estimate_rmb") or 0.0)
        avg_entry = underlying_position_summary.get("avg_entry_price")
        shares = int(underlying_position_summary.get("shares") or 0)
        if avg_entry is not None:
            total_entry_abs += abs(float(avg_entry)) * shares

    for leg in position_legs:
        leg_input = PositionLegInput(
            contract_id=leg.contract_id,
            side=leg.side,  # type: ignore[arg-type]
            option_type=leg.option_type,  # type: ignore[arg-type]
            strike=leg.strike,
            expiry=leg.expiry_date,
            entry_price=leg.avg_entry_price,
            quantity=leg.quantity,
        )
        row = _find_current_quote(snapshot, leg_input)
        if row is None:
            risk_flags.append("quote_missing")
            risk_contributors.append({
                "leg_id": leg.leg_id,
                "contract_id": leg.contract_id,
                "reason": "quote_missing",
                "suggested_action": "manual_review",
            })
            continue

        mid = _quote_mid(row)
        if mid is None:
            risk_flags.append("quote_missing")
            continue

        qty = int(leg.quantity or 0)
        entry = float(leg.avg_entry_price)
        leg_pnl = (
            (mid - entry) * qty * CONTRACT_MULTIPLIER
            if leg.side == "BUY"
            else (entry - mid) * qty * CONTRACT_MULTIPLIER
        )
        total_pnl += leg_pnl
        total_entry_abs += abs(entry) * qty * CONTRACT_MULTIPLIER

        delta = _safe_float(row.get("delta"), 0.0) or 0.0
        gamma = _safe_float(row.get("gamma"), 0.0) or 0.0
        theta = _safe_float(row.get("theta"), 0.0) or 0.0
        vega = _safe_float(row.get("vega"), 0.0) or 0.0
        delta_contrib = _leg_greek_contribution(leg.side, delta, qty)
        gamma_contrib = _leg_greek_contribution(leg.side, gamma, qty)
        theta_contrib = _leg_greek_contribution(leg.side, theta, qty)
        vega_contrib = _leg_greek_contribution(leg.side, vega, qty)
        risk_greeks_contrib = _risk_greeks_from_raw(
            delta_contrib,
            gamma_contrib,
            theta_contrib,
            vega_contrib,
            spot,
        )
        net_delta += delta_contrib
        net_gamma += gamma_contrib
        net_theta += theta_contrib
        net_vega += vega_contrib

        dte = row.get("dte_calendar")
        if dte is not None:
            dte = int(dte)
        else:
            expiry_date = _to_date(row.get("expiry_date") or leg.expiry_date)
            dte = max((expiry_date - date.today()).days, 0) if expiry_date is not None else None
        if dte is not None:
            min_dte = dte if min_dte is None else min(min_dte, dte)

        distance_pct = None
        if leg.side == "SELL" and spot > 0:
            distance_pct = abs(float(leg.strike) - spot) / spot
            short_distances.append(distance_pct)
            if distance_pct <= 0.01:
                risk_contributors.append({
                    "leg_id": leg.leg_id,
                    "contract_id": leg.contract_id,
                    "reason": "spot_near_short_strike",
                    "suggested_action": "reduce_or_close",
                })
            elif distance_pct <= 0.03:
                risk_contributors.append({
                    "leg_id": leg.leg_id,
                    "contract_id": leg.contract_id,
                    "reason": "spot_near_short_strike",
                    "suggested_action": "watch_or_reduce",
                })

        if gamma_contrib <= -1.0:
            risk_contributors.append({
                "leg_id": leg.leg_id,
                "contract_id": leg.contract_id,
                "reason": "short_gamma_heavy",
                "suggested_action": "reduce_or_close",
            })
        if delta_contrib <= -0.50:
            risk_contributors.append({
                "leg_id": leg.leg_id,
                "contract_id": leg.contract_id,
                "reason": "large_negative_delta",
                "suggested_action": "reduce_or_hedge",
            })
        elif delta_contrib >= 0.50:
            risk_contributors.append({
                "leg_id": leg.leg_id,
                "contract_id": leg.contract_id,
                "reason": "large_positive_delta",
                "suggested_action": "reduce_or_hedge",
            })

        monitored_legs.append({
            "leg_id": leg.leg_id,
            "contract_id": leg.contract_id,
            "side": leg.side,
            "option_type": leg.option_type,
            "strike": leg.strike,
            "expiry_date": leg.expiry_date,
            "quantity": qty,
            "avg_entry_price": round(entry, 6),
            "current_mid": round(mid, 6),
            "pnl_estimate_rmb": round(leg_pnl, 2),
            "delta_contribution": round(delta_contrib, 6),
            "gamma_contribution": round(gamma_contrib, 6),
            "theta_contribution": round(theta_contrib, 6),
            "vega_contribution": round(vega_contrib, 6),
            "delta_share_equiv": risk_greeks_contrib["delta_share_equiv"],
            "delta_rmb_per_1pct": risk_greeks_contrib["delta_rmb_per_1pct"],
            "theta_rmb_per_day": risk_greeks_contrib["theta_rmb_per_day"],
            "vega_rmb_per_1vol": risk_greeks_contrib["vega_rmb_per_1vol"],
            "gamma_rmb_per_1pct_move": risk_greeks_contrib["gamma_rmb_per_1pct_move"],
            "gamma_rmb_per_1pct_move_approximate": risk_greeks_contrib["gamma_rmb_per_1pct_move_approximate"],
            "dte": dte,
            "distance_to_spot_pct": round(distance_pct, 4) if distance_pct is not None else None,
            "strategy_bucket": leg.strategy_bucket,
            "group_id": leg.group_id,
            "tag": leg.tag,
        })

    if not position_legs:
        risk_flags.append("no_monitored_open_legs")
        notes.append("No OPEN legs are included in portfolio Greeks for this underlying.")

    if min_dte is not None:
        if min_dte <= 3:
            risk_flags.append("dte_critical")
            notes.append("Nearest expiry is within 3 days; monitor expiry risk closely.")
        elif min_dte <= 7:
            risk_flags.append("dte_decay_fast")
            notes.append("Nearest expiry is within 7 days; time decay and gamma risk can change quickly.")

    if short_distances:
        nearest = min(short_distances)
        if nearest <= 0.01:
            risk_flags.append("spot_at_short_strike")
            notes.append("Spot is very close to at least one short strike.")
        elif nearest <= 0.03:
            risk_flags.append("spot_near_short_strike")
            notes.append("Spot is close to at least one short strike.")

    covered_call_snapshot = _build_covered_call_coverage(
        monitored_legs,
        int(underlying_position_summary.get("shares") or 0),
    )
    covered_call_coverage = covered_call_snapshot["rows"]
    covered_ratio = covered_call_snapshot["covered_ratio"]
    uncovered_short_call_contracts = covered_call_snapshot["uncovered_short_call_contracts"]
    covered_call_risk_status = covered_call_snapshot["covered_call_risk_status"]
    coverage_by_leg = {
        row.get("leg_id"): row
        for row in covered_call_coverage
        if row.get("leg_id") is not None
    }
    for contributor in risk_contributors:
        coverage = coverage_by_leg.get(contributor.get("leg_id"))
        if coverage and contributor.get("reason") == "spot_near_short_strike":
            if coverage.get("coverage_status") in ("covered", "partially_covered"):
                contributor["reason"] = "covered_call_assignment_risk"
                contributor["suggested_action"] = "review_roll_up_out_or_accept_delivery"
            elif coverage.get("coverage_status") == "uncovered":
                contributor["reason"] = "uncovered_short_call_near_strike"
                contributor["suggested_action"] = "reduce_or_cover_short_call"
    if covered_call_snapshot["short_call_contracts"] > 0:
        if covered_call_risk_status == "covered":
            notes.append("Short call exposure is covered by ETF shares; monitor assignment and capped-upside risk near strike.")
        elif covered_call_risk_status == "partially_covered":
            risk_flags.append("covered_call_partially_covered")
            notes.append("Short call exposure is only partially covered by ETF shares.")
        elif covered_call_risk_status == "uncovered":
            risk_flags.append("short_call_uncovered")
            notes.append("Some short call exposure is not covered by ETF shares.")

    if abs(net_delta) >= 1.0:
        risk_flags.append("portfolio_delta_high")
        notes.append("Portfolio net delta is high for this underlying.")
    elif abs(net_delta) >= 0.50:
        risk_flags.append("portfolio_delta_watch")
        notes.append("Portfolio net delta deserves monitoring.")

    if net_gamma <= -2.0:
        risk_flags.append("portfolio_short_gamma")
        notes.append("Portfolio has notable short gamma exposure.")
    if net_vega <= -0.05:
        risk_flags.append("portfolio_short_vega")
        notes.append("Portfolio has notable short vega exposure.")
    if not notes:
        notes.append("No major portfolio risk threshold is currently triggered.")

    pnl_pct = total_pnl / total_entry_abs if total_entry_abs > 0 else None
    status = _monitor_status(risk_flags)
    recommended_action = _monitor_action(status, pnl_pct)
    risk_greeks = _risk_greeks_from_raw(net_delta, net_gamma, net_theta, net_vega, spot)

    hedge_suggestions: list[dict[str, str]] = []
    if net_delta <= -1.0:
        hedge_suggestions.append({
            "goal": "reduce_negative_delta",
            "suggestion": "consider adding a small positive-delta hedge or reducing bearish legs on the same underlying",
        })
    elif net_delta >= 1.0:
        hedge_suggestions.append({
            "goal": "reduce_positive_delta",
            "suggestion": "consider adding a small negative-delta hedge or reducing bullish legs on the same underlying",
        })
    if net_gamma <= -2.0:
        hedge_suggestions.append({
            "goal": "reduce_negative_gamma",
            "suggestion": "consider adding a small long-gamma hedge on the same underlying",
        })
    if net_vega <= -0.05:
        hedge_suggestions.append({
            "goal": "reduce_short_vega",
            "suggestion": "consider reducing short-vol legs or adding limited long-vega exposure",
        })

    summary = {
        "status": status,
        "spot": round(spot, 4),
        "monitored_leg_count": len(monitored_legs),
        "underlying_shares": underlying_position_summary.get("shares", 0),
        "covered_ratio": covered_ratio,
        "uncovered_short_call_contracts": uncovered_short_call_contracts,
        "covered_call_risk_status": covered_call_risk_status,
        "pnl_estimate": round(total_pnl, 2),
        "pnl_pct_of_entry_premium": round(pnl_pct, 4) if pnl_pct is not None else None,
        "net_delta": round(net_delta, 6),
        "net_gamma": round(net_gamma, 6),
        "net_theta": round(net_theta, 6),
        "net_vega": round(net_vega, 6),
        **risk_greeks,
        "dte": min_dte,
        "risk_flags": sorted(set(risk_flags)),
        "recommended_action": recommended_action,
        "notes": notes,
    }
    risk_legs_for_v2 = list(monitored_legs)
    if underlying_risk_leg is not None:
        risk_legs_for_v2.append(underlying_risk_leg)
    monitor_v2 = _build_underlying_monitor_v2(
        spot=spot,
        monitored_legs=risk_legs_for_v2,
        total_pnl=total_pnl,
        pnl_pct=pnl_pct,
        net_delta=net_delta,
        net_gamma=net_gamma,
        net_theta=net_theta,
        net_vega=net_vega,
        covered_call_coverage=covered_call_snapshot,
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO position_monitor_logs (
                    underlying_id, monitored_leg_count, status, recommended_action,
                    spot_price, pnl_estimate_rmb, net_delta, net_gamma, net_theta, net_vega,
                    risk_flags, notes
                )
                VALUES (
                    :underlying_id, :monitored_leg_count, :status, :recommended_action,
                    :spot_price, :pnl_estimate_rmb, :net_delta, :net_gamma, :net_theta, :net_vega,
                    :risk_flags, :notes
                )
                """
            ),
            {
                "underlying_id": underlying_id,
                "monitored_leg_count": len(monitored_legs),
                "status": status,
                "recommended_action": recommended_action,
                "spot_price": spot,
                "pnl_estimate_rmb": total_pnl,
                "net_delta": net_delta,
                "net_gamma": net_gamma,
                "net_theta": net_theta,
                "net_vega": net_vega,
                "risk_flags": json.dumps(sorted(set(risk_flags)), ensure_ascii=False),
                "notes": json.dumps(notes, ensure_ascii=False),
            },
        )

    return UnderlyingMonitorResponse(
        underlying_id=underlying_id,
        monitoring_summary=summary,
        monitored_legs=monitored_legs,
        risk_contributors=risk_contributors,
        hedge_suggestions=hedge_suggestions,
        portfolio_risk_summary=monitor_v2["portfolio_risk_summary"],
        group_risk_breakdown=monitor_v2["group_risk_breakdown"],
        expiry_risk_breakdown=monitor_v2["expiry_risk_breakdown"],
        short_strike_risk_map=monitor_v2["short_strike_risk_map"],
        management_suggestions=monitor_v2["management_suggestions"],
        underlying_position_summary=underlying_position_summary,
        covered_call_coverage=covered_call_coverage,
        covered_ratio=covered_ratio,
        uncovered_short_call_contracts=uncovered_short_call_contracts,
        covered_call_risk_status=covered_call_risk_status,
    )
