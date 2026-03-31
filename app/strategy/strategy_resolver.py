from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.schemas import ResolvedLeg, ResolvedStrategy, StrategyLegSpec, StrategySpec

logger = logging.getLogger(__name__)


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


def fetch_latest_option_quotes(engine, underlying_id: str) -> dict[str, dict]:
    sql = text("""
    WITH latest AS (
      SELECT MAX(fetch_time) AS max_fetch_time
      FROM option_quote_snapshots
      WHERE underlying_id = :underlying_id
    )
    SELECT
      contract_id,
      bid_price1,
      ask_price1,
      bid_vol1,
      ask_vol1,
      fetch_time
    FROM option_quote_snapshots
    WHERE underlying_id = :underlying_id
      AND fetch_time = (SELECT max_fetch_time FROM latest)
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"underlying_id": underlying_id}).mappings().all()

    out = {}
    for r in rows:
        bid = r.get("bid_price1")
        ask = r.get("ask_price1")
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        out[str(r["contract_id"])] = {
            **dict(r),
            "mid_price": mid,
        }
    return out


def fetch_latest_option_factors(engine: Engine, underlying_id: str) -> List[Dict[str, Any]]:
    sql = text("""
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT
            contract_id, underlying_id, option_type, expiry_date, strike,
            spot_price, option_market_price, pricing_basis, dte_calendar,
            t_years, rf_rate, implied_vol, delta, gamma, theta, vega, fetch_time
        FROM option_factor_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"underlying_id": underlying_id}).mappings().all()
    return [quote_row_from_mapping(dict(r)) for r in rows]


def merge_factor_and_quote_rows(factor_rows, quote_rows: dict[str, dict]) -> list[dict]:
    merged = []
    for f in factor_rows:
        contract_id = str(f["contract_id"])
        q = quote_rows.get(contract_id, {})
        merged.append({**f, **q, "contract_id": contract_id})
    return merged


def fetch_latest_spot(engine: Engine, underlying_id: str) -> float:
    sql = text("""
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
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"underlying_id": underlying_id}).mappings().first()
    if not row:
        raise ValueError(f"No latest spot found for underlying_id={underlying_id}")
    spot = _safe_float(row.get("spot_price"))
    if spot is None or spot <= 0:
        raise ValueError(f"Invalid spot found for underlying_id={underlying_id}")
    return spot


def filter_quotes_for_constraints(
    quotes: list[dict],
    option_type: str | None = None,
    dte_min: int | None = None,
    dte_max: int | None = None,
    max_rel_spread: float | None = None,
    min_quote_size: int | None = None,
) -> list[dict]:
    out = []

    for q in quotes:
        if option_type and q.get("option_type") != option_type:
            continue

        dte = q.get("dte")
        if dte is None:
            dte = q.get("dte_calendar")
        if dte is None:
            continue
        if dte_min is not None and dte < dte_min:
            continue
        if dte_max is not None and dte > dte_max:
            continue

        price = q.get("price")
        if price is None:
            price = q.get("option_market_price")
        if price is None or price <= 0:
            continue

        bid = q.get("bid_price1")
        ask = q.get("ask_price1")
        bid_vol = q.get("bid_vol1")
        ask_vol = q.get("ask_vol1")

        if min_quote_size is not None:
            if bid_vol is None or ask_vol is None:
                continue
            if bid_vol < min_quote_size or ask_vol < min_quote_size:
                continue

        if max_rel_spread is not None:
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                continue
            mid = q.get("mid_price")
            if mid is None:
                mid = (bid + ask) / 2
            if mid <= 0:
                continue
            rel_spread = (ask - bid) / mid
            if rel_spread > max_rel_spread:
                continue

        # 行权价过滤：非0.05整数倍的是除权合约
        strike = q.get("strike")
        if strike is not None:
            strike_rounded = round(float(strike) * 20) / 20
            if abs(float(strike) - strike_rounded) > 0.001:
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


def choose_by_strike_pct(
    quotes: List[Dict[str, Any]],
    option_type: str,
    expiry: date,
    spot: float,
    strike_pct: float,
) -> Optional[Dict[str, Any]]:
    """
    按用户指定的strike百分比（相对spot）选腿。
    strike_pct为负表示下方（如-0.12表示spot×0.88），正表示上方。
    找最近的合规strike合约。
    """
    target_strike = spot * (1.0 + strike_pct)
    pool = [
        q for q in quotes
        if q["option_type"] == option_type
        and q["expiry_date"] == expiry
        and q["strike"] is not None
    ]
    if not pool:
        return None
    return min(pool, key=lambda q: abs(q["strike"] - target_strike))


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
    first_leg: Dict[str, Any],
    strategy_type: str,
    delta_target: Optional[float],
) -> Optional[Dict[str, Any]]:
    option_type = first_leg["option_type"]
    expiry = first_leg["expiry_date"]
    first_strike = first_leg["strike"]

    same_expiry = [
        q for q in quotes
        if q["option_type"] == option_type and q["expiry_date"] == expiry
    ]
    if not same_expiry:
        return None

    if strategy_type == "bear_call_spread":
        candidates = [q for q in same_expiry if q["strike"] > first_strike]
    elif strategy_type == "bull_put_spread":
        candidates = [q for q in same_expiry if q["strike"] < first_strike]
    elif strategy_type == "bull_call_spread":
        candidates = [q for q in same_expiry if q["strike"] > first_strike]
    elif strategy_type == "bear_put_spread":
        candidates = [q for q in same_expiry if q["strike"] < first_strike]
    else:
        return None

    if not candidates:
        return None

    picked = choose_by_delta_target(candidates, delta_target)
    if picked is not None:
        return picked
    return min(candidates, key=lambda q: abs(q["strike"] - first_strike))


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


def build_resolved_leg(
    q: dict,
    action: str,
    quantity: int = 1,
    strike_forced: bool = False,
) -> ResolvedLeg:
    """
    构造ResolvedLeg。
    strike_forced=True时ranker会跳过该腿的delta评分。
    """
    price = q.get("price")
    if price is None:
        price = q.get("option_market_price")

    bid = q.get("bid_price1")
    ask = q.get("ask_price1")
    mid = q.get("mid_price")

    if bid is None: bid = price
    if ask is None: ask = price
    if mid is None: mid = price

    iv = q.get("iv")
    if iv is None:
        iv = q.get("implied_vol")

    dte = q.get("dte")
    if dte is None:
        dte = q.get("dte_calendar")

    expiry_raw = q.get("expiry_date")
    expiry_date = expiry_raw.isoformat() if expiry_raw is not None else None

    return ResolvedLeg(
        contract_id=str(q["contract_id"]),
        action=action,
        quantity=quantity,
        option_type=q.get("option_type"),
        strike=q.get("strike"),
        expiry_date=expiry_date,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=q.get("delta"),
        gamma=q.get("gamma"),
        theta=q.get("theta"),
        vega=q.get("vega"),
        iv=iv,
        dte=dte,
        strike_forced=strike_forced,
    )


def _choose_leg_quote(
    quotes: List[Dict[str, Any]],
    leg_spec: StrategyLegSpec,
    expiry: date,
    spot: float,
) -> Optional[Dict[str, Any]]:
    """
    统一选腿入口：有strike_pct_target时按百分比选，否则按delta_target选。
    """
    if leg_spec.strike_pct_target is not None:
        return choose_by_strike_pct(
            quotes=quotes,
            option_type=leg_spec.option_type,
            expiry=expiry,
            spot=spot,
            strike_pct=leg_spec.strike_pct_target,
        )
    return choose_same_expiry_leg(
        quotes=quotes,
        option_type=leg_spec.option_type,
        expiry=expiry,
        delta_target=leg_spec.delta_target,
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
    return filter_quotes_for_constraints(
        quotes=quotes,
        option_type=leg.option_type,
        dte_min=dte_min,
        dte_max=dte_max,
        max_rel_spread=strategy.constraints.max_rel_spread,
        min_quote_size=strategy.constraints.min_quote_size,
    )


def resolve_strategy(engine: Engine, strategy: StrategySpec) -> Optional[ResolvedStrategy]:
    if strategy.strategy_type in ("iron_condor", "iron_fly"):
        return _resolve_iron_structure(engine, strategy)

    factor_rows = fetch_latest_option_factors(engine, strategy.underlying_id)
    quote_rows = fetch_latest_option_quotes(engine, strategy.underlying_id)
    quotes = merge_factor_and_quote_rows(factor_rows, quote_rows)

    _OT_MAP = {"C": "CALL", "P": "PUT"}
    for q in quotes:
        q["option_type"] = _OT_MAP.get(q.get("option_type", ""), q.get("option_type", ""))

    spot = fetch_latest_spot(engine, strategy.underlying_id)

    logger.debug(f"[resolver] total quotes = {len(quotes)}")
    logger.debug(f"[resolver] strategy_type = {strategy.strategy_type}")

    if not quotes:
        return None

    resolved_legs: List[ResolvedLeg] = []
    calendar_second_filtered: Optional[List[Dict[str, Any]]] = None
    calendar_second_expiry: Optional[date] = None

    first_leg_spec: StrategyLegSpec = strategy.legs[0]
    first_dte_min, first_dte_max = _get_leg_dte_bounds(strategy, first_leg_spec, 0)
    logger.debug(f"[resolver] first leg dte range = {first_dte_min} ~ {first_dte_max}")

    first_filtered = _filter_quotes_for_leg(quotes, strategy, first_leg_spec, 0)
    logger.debug(f"[resolver] first leg filtered quotes = {len(first_filtered)}")

    if not first_filtered:
        return None

    first_grouped = group_by_expiry(first_filtered)
    first_expiry = choose_expiry(first_grouped, first_leg_spec.expiry_rule)
    logger.debug(f"[resolver] first_expiry = {first_expiry}")
    if first_expiry is None:
        return None

    if strategy.strategy_type in ("call_calendar", "put_calendar"):
        if len(strategy.legs) < 2:
            return None
        second_leg_spec: StrategyLegSpec = strategy.legs[1]
        calendar_second_filtered = _filter_quotes_for_leg(quotes, strategy, second_leg_spec, 1)
        calendar_second_grouped = group_by_expiry(calendar_second_filtered)
        calendar_second_expiry = choose_expiry(
            calendar_second_grouped, second_leg_spec.expiry_rule, reference_expiry=first_expiry,
        )
        logger.debug(f"[resolver] preview second_expiry = {calendar_second_expiry}")
        if calendar_second_expiry is None:
            return None
        first_leg_quote = choose_calendar_near_leg(
            near_quotes=first_filtered, far_quotes=calendar_second_filtered,
            option_type=first_leg_spec.option_type,
            near_expiry=first_expiry, far_expiry=calendar_second_expiry, spot=spot,
        )
    elif strategy.strategy_type in ("diagonal_call", "diagonal_put"):
        first_leg_quote = _choose_leg_quote(first_filtered, first_leg_spec, first_expiry, spot)
    else:
        first_leg_quote = _choose_leg_quote(first_filtered, first_leg_spec, first_expiry, spot)

    logger.debug(f"[resolver] first_leg_quote found = {first_leg_quote is not None}")
    if first_leg_quote is None:
        return None

    resolved_legs.append(build_resolved_leg(
        first_leg_quote,
        action=first_leg_spec.action,
        quantity=first_leg_spec.quantity,
        strike_forced=first_leg_spec.strike_forced,
    ))

    if len(strategy.legs) >= 2:
        second_leg_spec: StrategyLegSpec = strategy.legs[1]
        second_dte_min, second_dte_max = _get_leg_dte_bounds(strategy, second_leg_spec, 1)
        logger.debug(f"[resolver] second leg dte range = {second_dte_min} ~ {second_dte_max}")

        if strategy.strategy_type in ("bear_call_spread", "bull_put_spread", "bull_call_spread", "bear_put_spread"):
            # vertical的第二腿：有strike_pct_target时直接按百分比选，否则按原逻辑
            if second_leg_spec.strike_pct_target is not None:
                second_leg_quote = choose_by_strike_pct(
                    quotes=first_filtered,
                    option_type=second_leg_spec.option_type,
                    expiry=first_expiry,
                    spot=spot,
                    strike_pct=second_leg_spec.strike_pct_target,
                )
            else:
                second_leg_quote = choose_vertical_buy_leg(
                    quotes=first_filtered, first_leg=first_leg_quote,
                    strategy_type=strategy.strategy_type,
                    delta_target=second_leg_spec.delta_target,
                )
        else:
            if strategy.strategy_type in ("call_calendar", "put_calendar"):
                second_filtered = calendar_second_filtered or []
                second_expiry = calendar_second_expiry
            else:
                second_filtered = _filter_quotes_for_leg(quotes, strategy, second_leg_spec, 1)
                logger.debug(f"[resolver] second leg filtered quotes = {len(second_filtered)}")
                if not second_filtered:
                    return None
                second_grouped = group_by_expiry(second_filtered)
                second_expiry = choose_expiry(
                    second_grouped, second_leg_spec.expiry_rule, reference_expiry=first_expiry
                )

            logger.debug(f"[resolver] second_expiry = {second_expiry}")
            if second_expiry is None:
                return None

            if strategy.strategy_type in ("call_calendar", "put_calendar"):
                second_leg_quote = choose_same_strike_leg(
                    second_filtered, option_type=second_leg_spec.option_type,
                    expiry=second_expiry, strike=first_leg_quote["strike"],
                    delta_target=second_leg_spec.delta_target,
                )
            elif strategy.strategy_type in ("diagonal_call", "diagonal_put"):
                second_leg_quote = _choose_leg_quote(
                    second_filtered, second_leg_spec, second_expiry, spot
                )
            else:
                second_leg_quote = _choose_leg_quote(
                    second_filtered, second_leg_spec, second_expiry, spot
                )

        logger.debug(f"[resolver] second_leg_quote found = {second_leg_quote is not None}")
        if second_leg_quote is None:
            return None

        resolved_legs.append(build_resolved_leg(
            second_leg_quote,
            action=second_leg_spec.action,
            quantity=second_leg_spec.quantity,
            strike_forced=second_leg_spec.strike_forced,
        ))

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


def _resolve_iron_structure(engine: Engine, strategy: StrategySpec) -> Optional[ResolvedStrategy]:
    factor_rows = fetch_latest_option_factors(engine, strategy.underlying_id)
    quote_rows = fetch_latest_option_quotes(engine, strategy.underlying_id)
    quotes = merge_factor_and_quote_rows(factor_rows, quote_rows)

    _OT_MAP = {"C": "CALL", "P": "PUT"}
    for q in quotes:
        q["option_type"] = _OT_MAP.get(q.get("option_type", ""), q.get("option_type", ""))

    spot = fetch_latest_spot(engine, strategy.underlying_id)

    if not quotes:
        return None

    filtered = filter_quotes_for_constraints(
        quotes=quotes,
        dte_min=strategy.constraints.dte_min,
        dte_max=strategy.constraints.dte_max,
        max_rel_spread=strategy.constraints.max_rel_spread,
        min_quote_size=strategy.constraints.min_quote_size,
    )
    if not filtered:
        return None

    grouped = group_by_expiry(filtered)
    expiry = choose_expiry(grouped, "nearest")
    if expiry is None:
        return None

    same_expiry = [q for q in filtered if q["expiry_date"] == expiry]
    calls = [q for q in same_expiry if q["option_type"] == "CALL"]
    puts = [q for q in same_expiry if q["option_type"] == "PUT"]

    if not calls or not puts:
        return None

    if strategy.strategy_type == "iron_condor":
        call_sell = choose_by_delta_target(calls, 0.30)
        put_sell  = choose_by_delta_target(puts,  0.30)
        call_buy  = choose_by_delta_target([q for q in calls if q["strike"] > call_sell["strike"]], 0.15) if call_sell else None
        put_buy   = choose_by_delta_target([q for q in puts  if q["strike"] < put_sell["strike"]],  0.15) if put_sell  else None
    else:  # iron_fly
        call_sell = choose_by_delta_target(calls, 0.50)
        put_sell  = choose_by_delta_target(puts,  0.50)
        call_buy  = choose_by_delta_target([q for q in calls if q["strike"] > call_sell["strike"]], 0.20) if call_sell else None
        put_buy   = choose_by_delta_target([q for q in puts  if q["strike"] < put_sell["strike"]],  0.20) if put_sell  else None

    if not all([call_sell, call_buy, put_sell, put_buy]):
        return None

    legs = [
        build_resolved_leg(call_sell, "SELL"),
        build_resolved_leg(call_buy,  "BUY"),
        build_resolved_leg(put_sell,  "SELL"),
        build_resolved_leg(put_buy,   "BUY"),
    ]

    net_premium, net_credit, net_debit = calc_net_premium(legs)

    return ResolvedStrategy(
        strategy_type=strategy.strategy_type,
        underlying_id=strategy.underlying_id,
        spot_price=spot,
        legs=legs,
        net_premium=net_premium,
        net_credit=net_credit,
        net_debit=net_debit,
        rationale=strategy.rationale,
        metadata={"source": "strategy_resolver", "strategy_metadata": strategy.metadata},
    )