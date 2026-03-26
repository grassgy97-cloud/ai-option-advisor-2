from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.schemas import ResolvedLeg, ResolvedStrategy, StrategyLegSpec, StrategySpec


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _to_date(x: Any) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()

    s = str(x).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def quote_row_from_mapping(row: Dict[str, Any]) -> Dict[str, Any]:
    contract_id = row.get("contract_id")
    underlying_id = row.get("underlying_id")

    raw_option_type = (row.get("option_type") or "").upper()
    if raw_option_type in ("C", "CALL"):
        option_type = "CALL"
    elif raw_option_type in ("P", "PUT"):
        option_type = "PUT"
    else:
        option_type = raw_option_type

    expiry_date = _to_date(row.get("expiry_date"))

    return {
        "contract_id": str(contract_id) if contract_id is not None else None,
        "underlying_id": str(underlying_id) if underlying_id is not None else None,
        "option_type": option_type,
        "expiry_date": expiry_date,
        "strike": _safe_float(row.get("strike")),
        "spot_price": _safe_float(row.get("spot_price")),
        "price": _safe_float(row.get("option_market_price")),
        "pricing_basis": row.get("pricing_basis"),
        "dte": _safe_int(row.get("dte_calendar")),
        "t_years": _safe_float(row.get("t_years")),
        "rf_rate": _safe_float(row.get("rf_rate")),
        "iv": _safe_float(row.get("implied_vol")),
        "delta": _safe_float(row.get("delta")),
        "gamma": _safe_float(row.get("gamma")),
        "theta": _safe_float(row.get("theta")),
        "vega": _safe_float(row.get("vega")),
    }


def fetch_latest_option_factors(engine: Engine, underlying_id: str) -> List[Dict[str, Any]]:
    sql = text(
        """
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT
            contract_id,
            underlying_id,
            option_type,
            expiry_date,
            strike,
            spot_price,
            option_market_price,
            pricing_basis,
            dte_calendar,
            t_years,
            rf_rate,
            implied_vol,
            delta,
            gamma,
            theta,
            vega,
            fetch_time
        FROM option_factor_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(sql, {"underlying_id": underlying_id}).mappings().all()

    return [quote_row_from_mapping(dict(r)) for r in rows]


def fetch_latest_spot(engine: Engine, underlying_id: str) -> float:
    sql = text(
        """
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT spot_price
        FROM option_factor_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
          AND spot_price IS NOT NULL
        LIMIT 1
        """
    )

    with engine.connect() as conn:
        row = conn.execute(sql, {"underlying_id": underlying_id}).mappings().first()

    if not row:
        raise ValueError(f"No latest spot found for underlying_id={underlying_id}")

    spot = _safe_float(row.get("spot_price"))
    if spot is None or spot <= 0:
        raise ValueError(f"Invalid spot found for underlying_id={underlying_id}")

    return spot


def filter_quotes_for_constraints(
    quotes: List[Dict[str, Any]],
    option_type: str,
    dte_min: int,
    dte_max: int,
    max_rel_spread: float,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for q in quotes:
        if q["option_type"] != option_type:
            continue
        if q["contract_id"] is None:
            continue
        if q["expiry_date"] is None or q["strike"] is None:
            continue
        if q["price"] is None or q["price"] <= 0:
            continue
        if q["dte"] is None or q["dte"] < dte_min or q["dte"] > dte_max:
            continue
        out.append(q)
    return out


def group_by_expiry(quotes: List[Dict[str, Any]]) -> Dict[date, List[Dict[str, Any]]]:
    grouped: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        if q["expiry_date"] is not None:
            grouped[q["expiry_date"]].append(q)
    for expiry in grouped:
        grouped[expiry] = sorted(grouped[expiry], key=lambda x: x["strike"])
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))


def choose_expiry(
    grouped: Dict[date, List[Dict[str, Any]]],
    expiry_rule: str,
    reference_expiry: Optional[date] = None,
) -> Optional[date]:
    expiries = list(grouped.keys())
    if not expiries:
        return None

    if expiry_rule == "nearest":
        return expiries[0]

    if expiry_rule == "same_expiry":
        return reference_expiry

    if expiry_rule == "next_expiry":
        if reference_expiry is None:
            return expiries[1] if len(expiries) >= 2 else None
        later = [e for e in expiries if e > reference_expiry]
        return later[0] if later else None

    if expiry_rule == "farther_expiry":
        return expiries[-1]

    return None


def choose_by_delta_target(
    quotes: List[Dict[str, Any]],
    delta_target: Optional[float],
) -> Optional[Dict[str, Any]]:
    if not quotes:
        return None
    if delta_target is None:
        return quotes[0]

    valid = [q for q in quotes if q["delta"] is not None]
    if not valid:
        return None

    return min(valid, key=lambda q: abs(abs(q["delta"]) - delta_target))


def choose_same_expiry_leg(
    quotes: List[Dict[str, Any]],
    option_type: str,
    expiry: date,
    delta_target: Optional[float],
) -> Optional[Dict[str, Any]]:
    pool = [q for q in quotes if q["option_type"] == option_type and q["expiry_date"] == expiry]
    return choose_by_delta_target(pool, delta_target)


def choose_vertical_buy_leg(
    quotes: List[Dict[str, Any]],
    sell_leg: Dict[str, Any],
    delta_target: Optional[float],
) -> Optional[Dict[str, Any]]:
    option_type = sell_leg["option_type"]
    expiry = sell_leg["expiry_date"]
    sell_strike = sell_leg["strike"]

    same_expiry = [q for q in quotes if q["option_type"] == option_type and q["expiry_date"] == expiry]

    if option_type == "CALL":
        candidates = [q for q in same_expiry if q["strike"] > sell_strike]
    else:
        candidates = [q for q in same_expiry if q["strike"] < sell_strike]

    if not candidates:
        return None

    picked = choose_by_delta_target(candidates, delta_target)
    if picked:
        return picked

    return min(candidates, key=lambda q: abs(q["strike"] - sell_strike))


def choose_atm_like_leg(
    quotes: List[Dict[str, Any]],
    option_type: str,
    expiry: date,
    spot: float,
) -> Optional[Dict[str, Any]]:
    pool = [
        q for q in quotes
        if q["option_type"] == option_type
        and q["expiry_date"] == expiry
        and q["strike"] is not None
        and spot is not None
        and spot > 0
    ]
    if not pool:
        return None

    return min(pool, key=lambda q: abs(q["strike"] / spot - 1.0))


def choose_same_strike_leg(
    quotes: List[Dict[str, Any]],
    option_type: str,
    expiry: date,
    strike: float,
    delta_target: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    pool = [
        q for q in quotes
        if q["option_type"] == option_type
        and q["expiry_date"] == expiry
        and q["strike"] is not None
    ]
    if not pool:
        return None

    exact = [q for q in pool if q["strike"] == strike]
    if exact:
        if delta_target is not None:
            picked = choose_by_delta_target(exact, delta_target)
            if picked is not None:
                return picked
        return exact[0]

    nearest_strike = min(pool, key=lambda q: abs(q["strike"] - strike))["strike"]
    near_pool = [q for q in pool if q["strike"] == nearest_strike]

    if delta_target is not None:
        picked = choose_by_delta_target(near_pool, delta_target)
        if picked is not None:
            return picked
    return near_pool[0] if near_pool else None


def choose_calendar_near_leg(
    near_quotes: List[Dict[str, Any]],
    far_quotes: List[Dict[str, Any]],
    option_type: str,
    near_expiry: date,
    far_expiry: date,
    spot: float,
) -> Optional[Dict[str, Any]]:
    near_pool = [
        q for q in near_quotes
        if q["option_type"] == option_type
        and q["expiry_date"] == near_expiry
        and q["strike"] is not None
    ]
    far_pool = [
        q for q in far_quotes
        if q["option_type"] == option_type
        and q["expiry_date"] == far_expiry
        and q["strike"] is not None
    ]

    if not near_pool or not far_pool or spot is None or spot <= 0:
        return None

    far_strikes = {q["strike"] for q in far_pool}
    matchable_near = [q for q in near_pool if q["strike"] in far_strikes]
    if not matchable_near:
        return None

    return min(matchable_near, key=lambda q: abs(q["strike"] / spot - 1.0))


def build_resolved_leg(q: Dict[str, Any], action: str, quantity: int = 1) -> ResolvedLeg:
    px = float(q["price"])
    return ResolvedLeg(
        contract_id=q["contract_id"],
        action=action,
        option_type=q["option_type"],
        expiry_date=q["expiry_date"].isoformat(),
        strike=float(q["strike"]),
        bid=px,
        ask=px,
        mid=px,
        delta=q["delta"],
        iv=q["iv"],
        dte=q["dte"],
        quantity=quantity,
    )


def calc_net_premium(legs: List[ResolvedLeg]) -> Tuple[float, Optional[float], Optional[float]]:
    total = 0.0
    for leg in legs:
        leg_value = leg.mid * leg.quantity
        if leg.action == "BUY":
            total -= leg_value
        else:
            total += leg_value

    net_credit = total if total > 0 else None
    net_debit = -total if total < 0 else None
    return total, net_credit, net_debit


def _get_leg_dte_bounds(strategy: StrategySpec, leg: StrategyLegSpec, leg_index: int) -> Tuple[int, int]:
    """
    优先级：
    1) leg.leg_constraints
    2) strategy.metadata 中 near/far dte
    3) strategy.constraints
    """
    if getattr(leg, "leg_constraints", None) is not None:
        lc = leg.leg_constraints
        dte_min = lc.dte_min if lc.dte_min is not None else strategy.constraints.dte_min
        dte_max = lc.dte_max if lc.dte_max is not None else strategy.constraints.dte_max
        return dte_min, dte_max

    md = strategy.metadata or {}
    if strategy.strategy_type in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
        if leg_index == 0:
            dte_min = md.get("near_dte_min", strategy.constraints.dte_min)
            dte_max = md.get("near_dte_max", strategy.constraints.dte_max)
            return int(dte_min), int(dte_max)
        elif leg_index == 1:
            dte_min = md.get("far_dte_min", strategy.constraints.dte_min)
            dte_max = md.get("far_dte_max", strategy.constraints.dte_max)
            return int(dte_min), int(dte_max)

    return strategy.constraints.dte_min, strategy.constraints.dte_max


def _filter_quotes_for_leg(
    quotes: List[Dict[str, Any]],
    strategy: StrategySpec,
    leg: StrategyLegSpec,
    leg_index: int,
) -> List[Dict[str, Any]]:
    dte_min, dte_max = _get_leg_dte_bounds(strategy, leg, leg_index)
    max_rel_spread = strategy.constraints.max_rel_spread

    return filter_quotes_for_constraints(
        quotes=quotes,
        option_type=leg.option_type,
        dte_min=dte_min,
        dte_max=dte_max,
        max_rel_spread=max_rel_spread,
    )


def resolve_strategy(engine: Engine, strategy: StrategySpec) -> Optional[ResolvedStrategy]:
    quotes = fetch_latest_option_factors(engine, strategy.underlying_id)
    print(f"[resolver] total quotes = {len(quotes)}")
    if not quotes:
        return None

    spot = fetch_latest_spot(engine, strategy.underlying_id)

    print(f"[resolver] strategy_type = {strategy.strategy_type}")

    unique_option_types = sorted({str(q.get("option_type")) for q in quotes})
    print(f"[resolver] option types in DB = {unique_option_types}")

    resolved_legs: List[ResolvedLeg] = []

    # calendar 预览用
    calendar_second_filtered: Optional[List[Dict[str, Any]]] = None
    calendar_second_expiry: Optional[date] = None

    # ===== 第一腿 =====
    first_leg_spec: StrategyLegSpec = strategy.legs[0]
    first_dte_min, first_dte_max = _get_leg_dte_bounds(strategy, first_leg_spec, 0)
    print(f"[resolver] first leg option_type = {first_leg_spec.option_type}")
    print(f"[resolver] first leg dte range = {first_dte_min} ~ {first_dte_max}")

    first_filtered = _filter_quotes_for_leg(quotes, strategy, first_leg_spec, 0)
    print(f"[resolver] first leg filtered quotes = {len(first_filtered)}")

    if first_filtered:
        expiries = sorted({q["expiry_date"].isoformat() for q in first_filtered if q.get("expiry_date") is not None})
        print(f"[resolver] first leg expiries = {expiries[:10]}")
        deltas = [q["delta"] for q in first_filtered if q.get("delta") is not None]
        if deltas:
            print(f"[resolver] first leg delta min/max = {min(deltas)} / {max(deltas)}")

    if not first_filtered:
        return None

    first_grouped = group_by_expiry(first_filtered)
    first_expiry = choose_expiry(first_grouped, first_leg_spec.expiry_rule)
    print(f"[resolver] first_expiry = {first_expiry}")
    if first_expiry is None:
        return None

    if strategy.strategy_type in ("call_calendar", "put_calendar"):
        if len(strategy.legs) < 2:
            return None

        second_leg_spec: StrategyLegSpec = strategy.legs[1]
        calendar_second_filtered = _filter_quotes_for_leg(quotes, strategy, second_leg_spec, 1)
        calendar_second_grouped = group_by_expiry(calendar_second_filtered)
        calendar_second_expiry = choose_expiry(
            calendar_second_grouped,
            second_leg_spec.expiry_rule,
            reference_expiry=first_expiry,
        )
        print(f"[resolver] preview second_expiry = {calendar_second_expiry}")

        if calendar_second_expiry is None:
            return None

        first_leg_quote = choose_calendar_near_leg(
            near_quotes=first_filtered,
            far_quotes=calendar_second_filtered,
            option_type=first_leg_spec.option_type,
            near_expiry=first_expiry,
            far_expiry=calendar_second_expiry,
            spot=spot,
        )
    elif strategy.strategy_type in ("diagonal_call", "diagonal_put"):
        first_leg_quote = choose_atm_like_leg(
            first_filtered,
            option_type=first_leg_spec.option_type,
            expiry=first_expiry,
            spot=spot,
        )
    else:
        first_leg_quote = choose_same_expiry_leg(
            first_filtered,
            option_type=first_leg_spec.option_type,
            expiry=first_expiry,
            delta_target=first_leg_spec.delta_target,
        )

    print(f"[resolver] first_leg_quote found = {first_leg_quote is not None}")
    if first_leg_quote is None:
        return None

    print(f"[resolver] first_leg_quote strike = {first_leg_quote['strike']}, delta = {first_leg_quote['delta']}")

    resolved_legs.append(
        build_resolved_leg(
            first_leg_quote,
            action=first_leg_spec.action,
            quantity=first_leg_spec.quantity,
        )
    )

    # ===== 第二腿 =====
    if len(strategy.legs) >= 2:
        second_leg_spec: StrategyLegSpec = strategy.legs[1]
        second_dte_min, second_dte_max = _get_leg_dte_bounds(strategy, second_leg_spec, 1)
        print(f"[resolver] second leg option_type = {second_leg_spec.option_type}")
        print(f"[resolver] second leg dte range = {second_dte_min} ~ {second_dte_max}")

        if strategy.strategy_type in ("bear_call_spread", "bull_put_spread"):
            second_leg_quote = choose_vertical_buy_leg(
                quotes=first_filtered,
                sell_leg=first_leg_quote,
                delta_target=second_leg_spec.delta_target,
            )

        else:
            if strategy.strategy_type in ("call_calendar", "put_calendar"):
                second_filtered = calendar_second_filtered or []
                second_expiry = calendar_second_expiry

                print(f"[resolver] second leg filtered quotes = {len(second_filtered)}")
                if second_filtered:
                    expiries = sorted({
                        q["expiry_date"].isoformat()
                        for q in second_filtered
                        if q.get("expiry_date") is not None
                    })
                    print(f"[resolver] second leg expiries = {expiries[:10]}")
                    deltas = [q["delta"] for q in second_filtered if q.get("delta") is not None]
                    if deltas:
                        print(f"[resolver] second leg delta min/max = {min(deltas)} / {max(deltas)}")

            else:
                second_filtered = _filter_quotes_for_leg(quotes, strategy, second_leg_spec, 1)
                print(f"[resolver] second leg filtered quotes = {len(second_filtered)}")

                if second_filtered:
                    expiries = sorted({
                        q["expiry_date"].isoformat()
                        for q in second_filtered
                        if q.get("expiry_date") is not None
                    })
                    print(f"[resolver] second leg expiries = {expiries[:10]}")
                    deltas = [q["delta"] for q in second_filtered if q.get("delta") is not None]
                    if deltas:
                        print(f"[resolver] second leg delta min/max = {min(deltas)} / {max(deltas)}")

                if not second_filtered:
                    return None

                second_grouped = group_by_expiry(second_filtered)
                second_expiry = choose_expiry(
                    second_grouped,
                    second_leg_spec.expiry_rule,
                    reference_expiry=first_expiry,
                )

            print(f"[resolver] second_expiry = {second_expiry}")
            if second_expiry is None:
                return None

            if strategy.strategy_type in ("call_calendar", "put_calendar", "diagonal_call", "diagonal_put"):
                second_leg_quote = choose_same_strike_leg(
                    second_filtered,
                    option_type=second_leg_spec.option_type,
                    expiry=second_expiry,
                    strike=first_leg_quote["strike"],
                    delta_target=second_leg_spec.delta_target,
                )
            else:
                second_leg_quote = choose_same_expiry_leg(
                    second_filtered,
                    option_type=second_leg_spec.option_type,
                    expiry=second_expiry,
                    delta_target=second_leg_spec.delta_target,
                )

        print(f"[resolver] second_leg_quote found = {second_leg_quote is not None}")
        if second_leg_quote is None:
            return None

        print(f"[resolver] second_leg_quote strike = {second_leg_quote['strike']}, delta = {second_leg_quote['delta']}")

        resolved_legs.append(
            build_resolved_leg(
                second_leg_quote,
                action=second_leg_spec.action,
                quantity=second_leg_spec.quantity,
            )
        )

    net_premium, net_credit, net_debit = calc_net_premium(resolved_legs)

    return ResolvedStrategy(
        strategy_type=strategy.strategy_type,
        underlying_id=strategy.underlying_id,
        spot_price=spot,
        legs=resolved_legs,
        net_premium=net_premium,
        net_credit=net_credit,
        net_debit=net_debit,
        rationale=strategy.rationale,
        metadata={"source": "strategy_resolver", "strategy_metadata": strategy.metadata},
    )