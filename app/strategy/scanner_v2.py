from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

# ===== scoring =====

def calc_moneyness_score(moneyness: float) -> float:
    if moneyness is None:
        return 0.0

    dist = abs(moneyness - 1.0)

    if dist <= 0.03:
        return 1.0
    elif dist <= 0.06:
        return 0.8
    elif dist <= 0.10:
        return 0.6
    elif dist <= 0.15:
        return 0.3
    else:
        return 0.1

def calc_signal_score(iv_diff: float) -> float:
    if iv_diff is None:
        return 0.0
    x = abs(iv_diff)
    if x >= 0.05:
        return 1.0
    elif x >= 0.03:
        return 0.8
    elif x >= 0.02:
        return 0.6
    elif x >= 0.01:
        return 0.4
    else:
        return 0.0


def calc_cost_score(net_debit: float, spot: float) -> float:
    if net_debit is None or spot is None or spot <= 0:
        return 0.0
    ratio = net_debit / spot
    if ratio <= 0.002:
        return 1.0
    elif ratio <= 0.005:
        return 0.8
    elif ratio <= 0.01:
        return 0.6
    elif ratio <= 0.02:
        return 0.4
    else:
        return 0.1


def calc_liquidity_score(bid: float, ask: float) -> float:
    if bid is None or ask is None or ask <= 0:
        return 0.0
    spread = ask - bid
    rel_spread = spread / ask
    if rel_spread <= 0.01:
        return 1.0
    elif rel_spread <= 0.03:
        return 0.7
    elif rel_spread <= 0.05:
        return 0.4
    else:
        return 0.1


def calc_total_score(iv_diff, net_debit, spot, bid, ask, moneyness):
    signal = calc_signal_score(iv_diff)
    cost = calc_cost_score(net_debit, spot)
    liquidity = calc_liquidity_score(bid, ask)
    money = calc_moneyness_score(moneyness)

    total = 0.4 * signal + 0.25 * cost + 0.15 * liquidity + 0.2 * money

    return {
        "total_score": round(total, 4),
        "signal_score": signal,
        "cost_score": cost,
        "liquidity_score": liquidity,
        "moneyness_score": money,
    }


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _to_date(x: Any):
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


def fetch_latest_factor_rows(engine: Engine, underlying_id: str) -> List[Dict[str, Any]]:
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

    out = []
    for r in rows:
        row = dict(r)

        raw_option_type = (row.get("option_type") or "").upper()
        if raw_option_type in ("C", "CALL"):
            row["option_type"] = "CALL"
        elif raw_option_type in ("P", "PUT"):
            row["option_type"] = "PUT"
        else:
            row["option_type"] = raw_option_type

        row["strike"] = _safe_float(row.get("strike"))
        row["spot_price"] = _safe_float(row.get("spot_price"))
        row["option_market_price"] = _safe_float(row.get("option_market_price"))
        row["dte_calendar"] = int(row["dte_calendar"]) if row.get("dte_calendar") is not None else None
        row["t_years"] = _safe_float(row.get("t_years"))
        row["rf_rate"] = _safe_float(row.get("rf_rate"))
        row["implied_vol"] = _safe_float(row.get("implied_vol"))
        row["delta"] = _safe_float(row.get("delta"))
        row["gamma"] = _safe_float(row.get("gamma"))
        row["theta"] = _safe_float(row.get("theta"))
        row["vega"] = _safe_float(row.get("vega"))
        row["expiry_date"] = _to_date(row.get("expiry_date"))
        out.append(row)
    return out

def _group_by_expiry(rows: List[Dict[str, Any]]) -> Dict[Any, List[Dict[str, Any]]]:
    grouped = defaultdict(list)
    for r in rows:
        if r.get("expiry_date") is not None:
            grouped[r["expiry_date"]].append(r)
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))


def _atm_like_rows(rows: List[Dict[str, Any]], target_abs_delta_low=0.40, target_abs_delta_high=0.60):
    out = []
    for r in rows:
        delta = r.get("delta")
        iv = r.get("implied_vol")
        if delta is None or iv is None or iv <= 0:
            continue
        ad = abs(delta)
        if target_abs_delta_low <= ad <= target_abs_delta_high:
            out.append(r)
    return out


def scan_iv_skew(
    engine: Engine,
    underlying_id: str,
    max_rel_spread: float = 0.05,
) -> List[Dict[str, Any]]:
    """
    同一到期下比较 call / put ATM-like IV
    """
    rows = fetch_latest_factor_rows(engine, underlying_id)
    rows = [r for r in rows if r.get("option_market_price") is not None and r["option_market_price"] > 0]

    grouped = _group_by_expiry(rows)
    results: List[Dict[str, Any]] = []

    for expiry, grp in grouped.items():
        call_rows = [r for r in grp if r.get("option_type") == "CALL"]
        put_rows = [r for r in grp if r.get("option_type") == "PUT"]

        call_atm = _atm_like_rows(call_rows)
        put_atm = _atm_like_rows(put_rows)

        if not call_atm or not put_atm:
            continue

        call_iv = sum(r["implied_vol"] for r in call_atm) / len(call_atm)
        put_iv = sum(r["implied_vol"] for r in put_atm) / len(put_atm)
        skew = call_iv - put_iv

        signal = "none"
        if skew >= 0.02:
            signal = "call_iv_rich"
        elif skew <= -0.02:
            signal = "put_iv_rich"

        results.append(
            {
                "scan_type": "iv_skew",
                "underlying_id": underlying_id,
                "expiry_date": expiry.isoformat(),
                "call_iv_avg": round(call_iv, 6),
                "put_iv_avg": round(put_iv, 6),
                "skew": round(skew, 6),
                "signal": signal,
            }
        )

    results.sort(key=lambda x: abs(x["skew"]), reverse=True)
    return results


def scan_term_structure(
    engine: Engine,
    underlying_id: str,
    option_type: str = "CALL",
    max_rel_spread: float = 0.05,
) -> List[Dict[str, Any]]:
    """
    比较近月 ATM-like IV 与 次近月 ATM-like IV
    """
    rows = fetch_latest_factor_rows(engine, underlying_id)
    rows = [
        r for r in rows
        if r.get("option_type") == option_type
           and r.get("option_market_price") is not None
           and r["option_market_price"] > 0
    ]

    grouped = _group_by_expiry(rows)
    expiries = list(grouped.keys())

    results: List[Dict[str, Any]] = []
    if len(expiries) < 2:
        return results

    for i in range(len(expiries) - 1):
        near_expiry = expiries[i]
        far_expiry = expiries[i + 1]

        near_rows = _atm_like_rows(grouped[near_expiry])
        far_rows = _atm_like_rows(grouped[far_expiry])

        if not near_rows or not far_rows:
            continue

        near_iv = sum(r["implied_vol"] for r in near_rows) / len(near_rows)
        far_iv = sum(r["implied_vol"] for r in far_rows) / len(far_rows)
        term_diff = near_iv - far_iv

        signal = "none"
        if term_diff >= 0.02:
            signal = "term_front_high"
        elif term_diff <= -0.02:
            signal = "term_back_high"

        results.append(
            {
                "scan_type": "term_structure",
                "underlying_id": underlying_id,
                "option_type": option_type,
                "near_expiry": near_expiry.isoformat(),
                "far_expiry": far_expiry.isoformat(),
                "near_iv_avg": round(near_iv, 6),
                "far_iv_avg": round(far_iv, 6),
                "term_diff": round(term_diff, 6),
                "signal": signal,
            }
        )

    results.sort(key=lambda x: abs(x["term_diff"]), reverse=True)
    return results


def scan_static(
    engine: Engine,
    underlying_id: str = "510300",
) -> Dict[str, Any]:
    """
    给 /scan/static 用的统一入口
    """
    factor_rows = fetch_latest_factor_rows(engine, underlying_id)

    return {
        "underlying_id": underlying_id,
        "factor_row_count": len(factor_rows),
        "iv_skew": scan_iv_skew(engine, underlying_id),
        "term_structure_call": scan_term_structure(engine, underlying_id, option_type="CALL"),
        "term_structure_put": scan_term_structure(engine, underlying_id, option_type="PUT"),
    }

def fetch_latest_quote_rows(engine: Engine, underlying_id: str) -> List[Dict[str, Any]]:
    sql = text(
        """
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_quote_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT
            contract_id,
            underlying_id,
            option_type,
            expiry_date,
            strike,
            last_price,
            bid_price1,
            ask_price1,
            bid_vol1,
            ask_vol1,
            fetch_time
        FROM option_quote_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(sql, {"underlying_id": underlying_id}).mappings().all()

    out = []
    for r in rows:
        row = dict(r)

        raw_option_type = (row.get("option_type") or "").upper()
        if raw_option_type in ("C", "CALL"):
            row["option_type"] = "CALL"
        elif raw_option_type in ("P", "PUT"):
            row["option_type"] = "PUT"
        else:
            row["option_type"] = raw_option_type

        row["strike"] = _safe_float(row.get("strike"))
        row["last_price"] = _safe_float(row.get("last_price"))
        row["bid_price1"] = _safe_float(row.get("bid_price1"))
        row["ask_price1"] = _safe_float(row.get("ask_price1"))
        row["bid_vol1"] = _safe_float(row.get("bid_vol1"))
        row["ask_vol1"] = _safe_float(row.get("ask_vol1"))
        row["expiry_date"] = _to_date(row.get("expiry_date"))

        bp = row.get("bid_price1")
        ap = row.get("ask_price1")
        row["mid_price"] = (bp + ap) / 2 if bp is not None and ap is not None else None

        out.append(row)

    return out

def build_merged_rows(engine: Engine, underlying_id: str) -> List[Dict[str, Any]]:
    factor_rows = fetch_latest_factor_rows(engine, underlying_id)
    quote_rows = fetch_latest_quote_rows(engine, underlying_id)

    qmap = {r["contract_id"]: r for r in quote_rows}

    merged = []
    for f in factor_rows:
        q = qmap.get(f["contract_id"], {})
        row = dict(f)
        row.update({
            "last_price": q.get("last_price"),
            "bid_price1": q.get("bid_price1"),
            "ask_price1": q.get("ask_price1"),
            "bid_vol1": q.get("bid_vol1"),
            "ask_vol1": q.get("ask_vol1"),
            "mid_price": q.get("mid_price"),
            "quote_fetch_time": q.get("fetch_time"),
        })
        merged.append(row)

    return merged

def scan_calendar_spread_quotes(
    engine: Engine,
    underlying_id: str,
    option_type: str = "CALL",
    top_n: int = 3,
) -> List[Dict[str, Any]]:

    rows = build_merged_rows(engine, underlying_id)

    # ===== 基础过滤 =====
    rows = [
        r for r in rows
        if r.get("option_type") == option_type
        and r.get("expiry_date") is not None
        and r.get("strike") is not None
        and r.get("implied_vol") is not None
        and r.get("bid_price1") is not None
        and r.get("ask_price1") is not None
        and r.get("spot_price") is not None
    ]

    grouped = _group_by_expiry(rows)
    expiries = list(grouped.keys())
    if len(expiries) < 2:
        return []

    near_expiry = expiries[0]
    far_expiry = expiries[1]

    near_rows = grouped[near_expiry]
    far_rows = grouped[far_expiry]

    near_map = {r["strike"]: r for r in near_rows}
    far_map = {r["strike"]: r for r in far_rows}

    common_strikes = sorted(set(near_map.keys()) & set(far_map.keys()))
    if not common_strikes:
        return []

    # ===== 关键：找最接近 ATM 的 strike =====
    spot = near_rows[0].get("spot_price")

    sorted_strikes = sorted(
        common_strikes,
        key=lambda k: abs(k / spot - 1.0)
    )

    # 只取最接近的几个
    selected_strikes = sorted_strikes[:top_n]

    results = []

    for strike in selected_strikes:
        near = near_map[strike]
        far = far_map[strike]

        # ===== 计算 =====
        net_debit = None
        if far.get("ask_price1") is not None and near.get("bid_price1") is not None:
            net_debit = far["ask_price1"] - near["bid_price1"]

        iv_diff = far["implied_vol"] - near["implied_vol"]

        moneyness = strike / spot if spot else None
        if moneyness is None or abs(moneyness - 1.0) > 0.15:
            continue

        if abs(iv_diff) < 0.01:
            continue

        # ===== 打分 =====
        score_dict = calc_total_score(
            iv_diff,
            net_debit,
            spot,
            near.get("bid_price1"),
            near.get("ask_price1"),
            moneyness,
        )

        results.append({
            "scan_type": "calendar_spread_quote_aware",
            "underlying_id": underlying_id,
            "option_type": option_type,

            "strike": strike,
            "moneyness": round(moneyness, 4) if moneyness else None,

            "near_expiry": near_expiry.isoformat(),
            "far_expiry": far_expiry.isoformat(),

            "near_contract_id": near["contract_id"],
            "far_contract_id": far["contract_id"],

            "near_bid": near.get("bid_price1"),
            "near_ask": near.get("ask_price1"),
            "near_mid": near.get("mid_price"),

            "far_bid": far.get("bid_price1"),
            "far_ask": far.get("ask_price1"),
            "far_mid": far.get("mid_price"),

            "near_iv": round(near["implied_vol"], 6),
            "far_iv": round(far["implied_vol"], 6),
            "iv_diff": round(iv_diff, 6),

            "net_debit_buy_far_sell_near": round(net_debit, 6) if net_debit is not None else None,

            **score_dict,
        })

    # ===== 按评分排序 =====
    results.sort(key=lambda x: x["total_score"], reverse=True)

    return results


def scan_static(
    engine: Engine,
    underlying_id: str = "510300",
) -> Dict[str, Any]:
    factor_rows = fetch_latest_factor_rows(engine, underlying_id)

    return {
        "underlying_id": underlying_id,
        "factor_row_count": len(factor_rows),
        "iv_skew": scan_iv_skew(engine, underlying_id),
        "term_structure_call": scan_term_structure(engine, underlying_id, option_type="CALL"),
        "term_structure_put": scan_term_structure(engine, underlying_id, option_type="PUT"),
        "calendar_spread_call_quote_aware": scan_calendar_spread_quotes(engine, underlying_id, option_type="CALL"),
    }
