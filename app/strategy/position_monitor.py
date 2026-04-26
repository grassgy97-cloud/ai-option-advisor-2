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
from app.strategy.positions_service import list_position_legs
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
    snapshot = load_market_snapshot(engine, underlying_id)
    spot = float(snapshot.spot)

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
        greek_sign = 1.0 if leg.side == "BUY" else -1.0
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
        delta_contrib = greek_sign * delta * qty
        gamma_contrib = greek_sign * gamma * qty
        theta_contrib = greek_sign * theta * qty
        vega_contrib = greek_sign * vega * qty
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
        "pnl_estimate": round(total_pnl, 2),
        "pnl_pct_of_entry_premium": round(pnl_pct, 4) if pnl_pct is not None else None,
        "net_delta": round(net_delta, 6),
        "net_gamma": round(net_gamma, 6),
        "net_theta": round(net_theta, 6),
        "net_vega": round(net_vega, 6),
        "dte": min_dte,
        "risk_flags": sorted(set(risk_flags)),
        "recommended_action": recommended_action,
        "notes": notes,
    }

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
    )
