"""
iv_percentile.py

IV percentile calculation with two goals:
1. expose richer dimensions: ATM / CALL / PUT
2. build historical samples from trading-day observations only

Backward compatibility:
- keep legacy top-level fields such as `current_iv`, `composite_percentile`, `label`
- legacy top-level values are mapped to ATM percentile temporarily
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import time

from sqlalchemy import text
from sqlalchemy.engine import Engine


IV_PRIOR_RANGE: Dict[str, Dict[str, float]] = {
    "510300": {"p10": 0.110, "p25": 0.135, "p50": 0.160, "p75": 0.200, "p90": 0.280, "p99": 0.480},
    "510050": {"p10": 0.100, "p25": 0.125, "p50": 0.150, "p75": 0.190, "p90": 0.265, "p99": 0.460},
    "510500": {"p10": 0.140, "p25": 0.170, "p50": 0.210, "p75": 0.260, "p90": 0.360, "p99": 0.600},
    "588000": {"p10": 0.180, "p25": 0.220, "p50": 0.270, "p75": 0.340, "p90": 0.480, "p99": 0.750},
    "159915": {"p10": 0.150, "p25": 0.185, "p50": 0.225, "p75": 0.290, "p90": 0.400, "p99": 0.650},
    "159901": {"p10": 0.110, "p25": 0.135, "p50": 0.160, "p75": 0.200, "p90": 0.280, "p99": 0.480},
    "159919": {"p10": 0.110, "p25": 0.135, "p50": 0.160, "p75": 0.200, "p90": 0.280, "p99": 0.480},
    "159922": {"p10": 0.140, "p25": 0.170, "p50": 0.210, "p75": 0.260, "p90": 0.360, "p99": 0.600},
    "588080": {"p10": 0.180, "p25": 0.220, "p50": 0.270, "p75": 0.340, "p90": 0.480, "p99": 0.750},
}

_DEFAULT_PRIOR = IV_PRIOR_RANGE["510300"]
_MIN_VALID_IV = 0.01
_MAX_VALID_IV = 2.0
_MIN_DTE = 10
_CALL_TARGET_ABS_DELTA = 0.40
_PUT_TARGET_ABS_DELTA = 0.40
_ATM_TARGET_ABS_DELTA = 0.50


def _calc_prior_percentile(iv: float, prior: Dict[str, float]) -> float:
    anchors = [
        (prior["p10"], 0.10),
        (prior["p25"], 0.25),
        (prior["p50"], 0.50),
        (prior["p75"], 0.75),
        (prior["p90"], 0.90),
        (prior["p99"], 0.99),
    ]

    if iv <= anchors[0][0]:
        return max(0.0, 0.10 * iv / anchors[0][0])

    if iv >= anchors[-1][0]:
        return min(1.0, 0.99 + 0.01 * (iv - anchors[-1][0]) / anchors[-1][0])

    for i in range(len(anchors) - 1):
        iv_lo, pct_lo = anchors[i]
        iv_hi, pct_hi = anchors[i + 1]
        if iv_lo <= iv <= iv_hi:
            ratio = (iv - iv_lo) / (iv_hi - iv_lo)
            return pct_lo + ratio * (pct_hi - pct_lo)

    return 0.5


def _get_label(pct: float) -> tuple[str, str]:
    if pct <= 0.15:
        return "极低", "very_low"
    if pct <= 0.30:
        return "偏低", "low"
    if pct <= 0.55:
        return "正常", "normal"
    if pct <= 0.70:
        return "偏高", "elevated"
    if pct <= 0.85:
        return "较高", "high"
    return "极高", "very_high"


def _get_strategy_hints(pct: float) -> List[str]:
    if pct <= 0.15:
        return [
            "IV 极低，买入单边或低成本方向结构更容易受益。",
            "纯卖方策略的时间价值较薄，吸引力有限。",
        ]
    if pct <= 0.30:
        return [
            "IV 偏低，calendar/diagonal 等结构可以重点观察。",
            "单腿卖方的权利金偏薄，需要更谨慎。",
        ]
    if pct <= 0.55:
        return [
            "IV 处于常态区间，方向与结构质量更重要。",
            "买卖双方策略都可考虑，但要更重视执行质量。",
        ]
    if pct <= 0.70:
        return [
            "IV 偏高，卖方结构开始更有性价比。",
            "虚值卖出或备兑卖出吸引力有所提升。",
        ]
    if pct <= 0.85:
        return [
            "IV 较高，卖方机会更明显。",
            "可优先考虑 defined-risk 卖方结构，注意 gamma 风险。",
        ]
    return [
        "IV 极高，卖方机会与尾部风险同时放大。",
        "更适合 defined-risk 结构，不宜简单裸卖或追涨买入。",
    ]


def _build_percentile_payload(
    current_iv: float,
    underlying_id: str,
    historical_ivs: List[float],
    dimension: str,
    min_history_days: int = 10,
) -> Dict[str, Any]:
    prior = IV_PRIOR_RANGE.get(underlying_id, _DEFAULT_PRIOR)
    prior_pct = _calc_prior_percentile(current_iv, prior)

    n = len(historical_ivs)
    if n >= min_history_days:
        hist_pct = sum(1 for x in historical_ivs if x <= current_iv) / n
        hist_weight = min(0.70, 0.30 + 0.40 * (n - min_history_days) / 50.0)
    else:
        hist_pct = prior_pct
        hist_weight = 0.0

    prior_weight = 1.0 - hist_weight
    composite_pct = hist_weight * hist_pct + prior_weight * prior_pct
    label, label_en = _get_label(composite_pct)

    return {
        "dimension": dimension,
        "current_iv": round(current_iv, 4),
        "composite_percentile": round(composite_pct, 3),
        "hist_percentile": round(hist_pct, 3),
        "prior_percentile": round(prior_pct, 3),
        "hist_weight": round(hist_weight, 2),
        "prior_weight": round(prior_weight, 2),
        "history_days": n,
        "label": label,
        "label_en": label_en,
        "strategy_hints": _get_strategy_hints(composite_pct),
        "prior_anchors": {k: v for k, v in prior.items()},
    }


def _fetch_latest_snapshot_rows(
    engine: Engine,
    underlying_id: str,
    min_dte: int = _MIN_DTE,
) -> List[Dict[str, Any]]:
    sql = text(
        """
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT option_type, implied_vol, delta, dte_calendar, strike, fetch_time
        FROM option_factor_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
          AND delta IS NOT NULL
          AND implied_vol IS NOT NULL
          AND implied_vol > :min_valid_iv
          AND implied_vol < :max_valid_iv
          AND dte_calendar > :min_dte
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "underlying_id": underlying_id,
                "min_valid_iv": _MIN_VALID_IV,
                "max_valid_iv": _MAX_VALID_IV,
                "min_dte": min_dte,
            },
        ).mappings().all()
    return [dict(r) for r in rows]


def _choose_representative_iv(
    rows: List[Dict[str, Any]],
    option_side: str,
    abs_delta_target: float,
) -> Optional[float]:
    if option_side == "CALL":
        candidates = [r for r in rows if str(r.get("option_type")).upper() in ("CALL", "C")]
    elif option_side == "PUT":
        candidates = [r for r in rows if str(r.get("option_type")).upper() in ("PUT", "P")]
    else:
        candidates = rows

    if not candidates:
        return None

    ordered = sorted(
        candidates,
        key=lambda r: (
            int(r.get("dte_calendar") or 999999),
            abs(abs(float(r.get("delta") or 0.0)) - abs_delta_target),
        ),
    )
    iv = ordered[0].get("implied_vol")
    return float(iv) if iv is not None else None


def get_current_representative_ivs(engine: Engine, underlying_id: str) -> Dict[str, Optional[float]]:
    try:
        rows = _fetch_latest_snapshot_rows(engine, underlying_id)
        if not rows:
            rows = _fetch_latest_snapshot_rows(engine, underlying_id, min_dte=0)
            print(f"[iv_percentile] uid={underlying_id} relaxed_current_iv_dte_filter rows={len(rows)}")
        current_call = _choose_representative_iv(rows, "CALL", _CALL_TARGET_ABS_DELTA)
        current_put = _choose_representative_iv(rows, "PUT", _PUT_TARGET_ABS_DELTA)
        atm_call = _choose_representative_iv(rows, "CALL", _ATM_TARGET_ABS_DELTA)
        atm_put = _choose_representative_iv(rows, "PUT", _ATM_TARGET_ABS_DELTA)

        atm_values = [v for v in (atm_call, atm_put) if v is not None]
        current_atm = sum(atm_values) / len(atm_values) if atm_values else (current_call or current_put)

        return {
            "atm": round(current_atm, 4) if current_atm is not None else None,
            "call": round(current_call, 4) if current_call is not None else None,
            "put": round(current_put, 4) if current_put is not None else None,
        }
    except Exception as e:
        print(f"[iv_percentile] get_current_representative_ivs failed: {e}")
        return {"atm": None, "call": None, "put": None}


def get_current_atm_iv(engine: Engine, underlying_id: str) -> Optional[float]:
    return get_current_representative_ivs(engine, underlying_id).get("atm")


def _fetch_trading_day_series(
    engine: Engine,
    underlying_id: str,
    option_side: str,
    abs_delta_target: float,
    lookback_days: int,
) -> List[tuple[Any, float]]:
    option_type_filter = "('CALL', 'C')" if option_side == "CALL" else "('PUT', 'P')"
    sql = text(
        f"""
        WITH recent_dates AS (
            SELECT trade_date
            FROM (
                SELECT DISTINCT DATE(fetch_time) AS trade_date
                FROM option_factor_snapshots
                WHERE underlying_id = :underlying_id
            ) d
            ORDER BY trade_date DESC
            LIMIT :lookback_days
        ),
        ranked AS (
            SELECT
                DATE(o.fetch_time) AS trade_date,
                o.implied_vol,
                ROW_NUMBER() OVER (
                    PARTITION BY DATE(o.fetch_time)
                    ORDER BY o.dte_calendar ASC, ABS(ABS(o.delta) - :abs_delta_target) ASC
                ) AS rn
            FROM option_factor_snapshots o
            JOIN recent_dates d
              ON DATE(o.fetch_time) = d.trade_date
            WHERE o.underlying_id = :underlying_id
              AND o.option_type IN {option_type_filter}
              AND o.delta IS NOT NULL
              AND o.implied_vol IS NOT NULL
              AND o.implied_vol > :min_valid_iv
              AND o.implied_vol < :max_valid_iv
              AND o.dte_calendar > :min_dte
        )
        SELECT trade_date, implied_vol
        FROM ranked
        WHERE rn = 1
        ORDER BY trade_date ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "underlying_id": underlying_id,
                "lookback_days": lookback_days,
                "abs_delta_target": abs_delta_target,
                "min_valid_iv": _MIN_VALID_IV,
                "max_valid_iv": _MAX_VALID_IV,
                "min_dte": _MIN_DTE,
            },
        ).fetchall()
    return [(r[0], float(r[1])) for r in rows if r[1] is not None]


def fetch_historical_atm_iv(
    engine: Engine,
    underlying_id: str,
    lookback_days: int = 90,
) -> List[float]:
    try:
        call_series = _fetch_trading_day_series(
            engine=engine,
            underlying_id=underlying_id,
            option_side="CALL",
            abs_delta_target=_ATM_TARGET_ABS_DELTA,
            lookback_days=lookback_days,
        )
        put_series = _fetch_trading_day_series(
            engine=engine,
            underlying_id=underlying_id,
            option_side="PUT",
            abs_delta_target=_ATM_TARGET_ABS_DELTA,
            lookback_days=lookback_days,
        )

        call_map = {trade_date: iv for trade_date, iv in call_series}
        put_map = {trade_date: iv for trade_date, iv in put_series}
        merged: List[float] = []
        for trade_date in sorted(set(call_map) | set(put_map)):
            values = [iv for iv in (call_map.get(trade_date), put_map.get(trade_date)) if iv is not None]
            if values:
                merged.append(sum(values) / len(values))
        return merged
    except Exception as e:
        print(f"[iv_percentile] fetch_historical_atm_iv failed: {e}")
        return []


def fetch_historical_representative_ivs(
    engine: Engine,
    underlying_id: str,
    lookback_days: int = 90,
) -> Dict[str, List[float]]:
    try:
        atm_history = fetch_historical_atm_iv(engine, underlying_id, lookback_days)
        call_history = _fetch_trading_day_series(
            engine=engine,
            underlying_id=underlying_id,
            option_side="CALL",
            abs_delta_target=_CALL_TARGET_ABS_DELTA,
            lookback_days=lookback_days,
        )
        put_history = _fetch_trading_day_series(
            engine=engine,
            underlying_id=underlying_id,
            option_side="PUT",
            abs_delta_target=_PUT_TARGET_ABS_DELTA,
            lookback_days=lookback_days,
        )
        return {
            "atm": atm_history,
            "call": [iv for _, iv in call_history],
            "put": [iv for _, iv in put_history],
        }
    except Exception as e:
        print(f"[iv_percentile] fetch_historical_representative_ivs failed: {e}")
        return {"atm": [], "call": [], "put": []}


def calc_iv_percentile(
    current_iv: float,
    underlying_id: str,
    engine: Optional[Engine] = None,
    historical_ivs: Optional[List[float]] = None,
    lookback_days: int = 90,
    min_history_days: int = 10,
) -> Dict[str, Any]:
    if historical_ivs is None and engine is not None:
        historical_ivs = fetch_historical_atm_iv(engine, underlying_id, lookback_days)
    if historical_ivs is None:
        historical_ivs = []

    payload = _build_percentile_payload(
        current_iv=current_iv,
        underlying_id=underlying_id,
        historical_ivs=historical_ivs,
        dimension="atm",
        min_history_days=min_history_days,
    )
    payload["underlying_id"] = underlying_id
    return payload


def build_iv_percentile_report(
    engine: Engine,
    underlying_id: str,
    lookback_days: int = 90,
) -> Optional[Dict[str, Any]]:
    t0_all = time.perf_counter()

    t0 = time.perf_counter()
    current_ivs = get_current_representative_ivs(engine, underlying_id)
    print(f"[timing] {underlying_id} get_current_atm_iv = {time.perf_counter() - t0:.3f}s")

    current_atm = current_ivs.get("atm")
    if current_atm is None:
        fallback_values = [v for v in (current_ivs.get("call"), current_ivs.get("put")) if v is not None]
        if fallback_values:
            current_atm = round(sum(fallback_values) / len(fallback_values), 4)
            current_ivs["atm"] = current_atm
            print(f"[iv_percentile] uid={underlying_id} atm_missing_use_side_average={current_atm}")
        else:
            print(f"[iv_percentile] 无法获取 {underlying_id} 的当前 ATM/CALL/PUT IV")
            return None

    t0 = time.perf_counter()
    history = fetch_historical_representative_ivs(
        engine=engine,
        underlying_id=underlying_id,
        lookback_days=lookback_days,
    )
    print(
        f"[iv_pct_sample] uid={underlying_id} sample_mode=trading_day "
        f"sample_count=atm:{len(history['atm'])},call:{len(history['call'])},put:{len(history['put'])}"
    )

    atm_report = _build_percentile_payload(
        current_iv=current_atm,
        underlying_id=underlying_id,
        historical_ivs=history["atm"],
        dimension="atm",
    )

    current_call = current_ivs.get("call")
    call_report = (
        _build_percentile_payload(
            current_iv=current_call,
            underlying_id=underlying_id,
            historical_ivs=history["call"],
            dimension="call",
        )
        if current_call is not None
        else None
    )

    current_put = current_ivs.get("put")
    put_report = (
        _build_percentile_payload(
            current_iv=current_put,
            underlying_id=underlying_id,
            historical_ivs=history["put"],
            dimension="put",
        )
        if current_put is not None
        else None
    )

    report = {
        "underlying_id": underlying_id,
        # legacy-compatible top-level ATM fields
        "current_iv": atm_report["current_iv"],
        "composite_percentile": atm_report["composite_percentile"],
        "hist_percentile": atm_report["hist_percentile"],
        "prior_percentile": atm_report["prior_percentile"],
        "hist_weight": atm_report["hist_weight"],
        "prior_weight": atm_report["prior_weight"],
        "history_days": atm_report["history_days"],
        "label": atm_report["label"],
        "label_en": atm_report["label_en"],
        "strategy_hints": atm_report["strategy_hints"],
        "prior_anchors": atm_report["prior_anchors"],
        # richer dimensions
        "atm_iv_percentile": atm_report,
        "call_iv_percentile": call_report,
        "put_iv_percentile": put_report,
        "current_atm_iv": current_ivs.get("atm"),
        "current_call_iv": current_ivs.get("call"),
        "current_put_iv": current_ivs.get("put"),
    }

    print(
        f"[iv_pct] uid={underlying_id} "
        f"atm={atm_report['composite_percentile']} "
        f"call={call_report['composite_percentile'] if call_report else None} "
        f"put={put_report['composite_percentile'] if put_report else None} "
        f"hist_days={atm_report['history_days']}"
    )
    print(
        f"[iv_pct_restore] uid={underlying_id} "
        f"current_iv={report['current_iv']} "
        f"composite_percentile={report['composite_percentile']} "
        f"atm={report['atm_iv_percentile']['composite_percentile']} "
        f"call={report['call_iv_percentile']['composite_percentile'] if report['call_iv_percentile'] else None} "
        f"put={report['put_iv_percentile']['composite_percentile'] if report['put_iv_percentile'] else None}"
    )
    print(f"[timing] {underlying_id} calc_iv_percentile = {time.perf_counter() - t0:.3f}s")
    print(f"[timing] {underlying_id} build_iv_percentile_report total = {time.perf_counter() - t0_all:.3f}s")
    return report
