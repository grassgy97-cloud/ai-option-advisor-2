"""
market_context.py
从 underlying_daily_kline 计算技术指标，生成市场背景摘要供 LLM 参考。

指标：
  - MA20 / MA60：价格相对均线位置
  - 近5日/近20日涨跌幅
  - 波动率：近20日日收益率标准差×sqrt(252)（历史波动率HV20）
  - skew：同DTE下 put IV - call IV（近月ATM附近）

输出格式（结构化dict，同时提供自然语言摘要）：
{
  "underlying_id": "510300",
  "last_close": 4.463,
  "ma20": 4.52,
  "ma60": 4.48,
  "vs_ma20_pct": -1.26,   # 相对MA20的百分比，负=在MA20下方
  "vs_ma60_pct": -0.38,
  "ret5d_pct": -2.1,       # 近5日涨跌幅%
  "ret20d_pct": -4.3,
  "hv20": 0.185,           # 历史波动率（年化）
  "iv_pct": 0.62,          # 来自iv_percentile
  "put_call_skew": 0.012,  # put IV - call IV（正=put偏贵）
  "trend": "downtrend",    # uptrend / downtrend / sideways
  "summary": "沪深300处于MA20下方1.3%，近20日下跌4.3%，短期趋势偏空..."
}
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _fetch_kline(engine: Engine, underlying_id: str, n: int = 65) -> List[Dict[str, Any]]:
    """取最近n条日K，按日期升序。"""
    sql = text("""
        SELECT trade_date, open_price, high_price, low_price, close_price
        FROM underlying_daily_kline
        WHERE underlying_id = :uid
        ORDER BY trade_date DESC
        LIMIT :n
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"uid": underlying_id, "n": n}).mappings().all()
    # 反转为升序
    return list(reversed([dict(r) for r in rows]))


def _fetch_iv_skew(engine: Engine, underlying_id: str) -> Optional[float]:
    """
    计算近月ATM附近的put/call IV差（put_iv - call_iv）。
    正值=put偏贵（市场偏空），负值=call偏贵（市场偏多）。
    """
    sql = text("""
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :uid
        ),
        atm AS (
            SELECT
                option_type,
                implied_vol,
                ABS(delta) AS abs_delta,
                dte_calendar
            FROM option_factor_snapshots
            WHERE underlying_id = :uid
              AND fetch_time = (SELECT max_fetch_time FROM latest)
              AND dte_calendar BETWEEN 10 AND 45
              AND implied_vol IS NOT NULL
              AND ABS(delta) BETWEEN 0.35 AND 0.65
        )
        SELECT
            AVG(CASE WHEN option_type = 'P' THEN implied_vol END) AS avg_put_iv,
            AVG(CASE WHEN option_type = 'C' THEN implied_vol END) AS avg_call_iv
        FROM atm
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"uid": underlying_id}).mappings().first()
    if not row:
        return None
    put_iv = row.get("avg_put_iv")
    call_iv = row.get("avg_call_iv")
    if put_iv is None or call_iv is None:
        return None
    return round(float(put_iv) - float(call_iv), 4)


def _calc_ma(closes: List[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 4)


def _calc_hv(closes: List[float], n: int = 20) -> Optional[float]:
    """年化历史波动率（日收益率标准差×sqrt(252)）。"""
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(variance) * math.sqrt(252), 4)


def _infer_trend(vs_ma20: float, vs_ma60: float, ret20d: float) -> str:
    """
    简单趋势判断：
    - uptrend：在MA20/MA60上方且近20日为正
    - downtrend：在MA20/MA60下方且近20日为负
    - sideways：其他
    """
    above_ma20 = vs_ma20 > 0
    above_ma60 = vs_ma60 > 0
    if above_ma20 and above_ma60 and ret20d > 1.0:
        return "uptrend"
    if not above_ma20 and not above_ma60 and ret20d < -1.0:
        return "downtrend"
    return "sideways"


def _fetch_term_slopes(engine: Engine, underlying_id: str) -> Dict[str, Optional[float]]:
    """
    分别计算call和put的期限结构斜率（近月ATM IV - 远月ATM IV）。
    正值=近月贵（前高），利于calendar/diagonal。
    负值=远月贵（后高），calendar不利。
    返回 {"call": float|None, "put": float|None}
    """
    sql = text("""
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :uid
        ),
        ranked AS (
            SELECT
                option_type,
                implied_vol,
                CASE
                    WHEN dte_calendar <= 35 THEN 'near'
                    WHEN dte_calendar >= 36 THEN 'far'
                END AS term
            FROM option_factor_snapshots
            WHERE underlying_id = :uid
              AND fetch_time = (SELECT max_fetch_time FROM latest)
              AND dte_calendar BETWEEN 10 AND 90
              AND implied_vol IS NOT NULL
              AND ABS(delta) BETWEEN 0.35 AND 0.65
        )
        SELECT
            AVG(CASE WHEN term = 'near' AND option_type = 'C' THEN implied_vol END) AS near_call_iv,
            AVG(CASE WHEN term = 'far'  AND option_type = 'C' THEN implied_vol END) AS far_call_iv,
            AVG(CASE WHEN term = 'near' AND option_type = 'P' THEN implied_vol END) AS near_put_iv,
            AVG(CASE WHEN term = 'far'  AND option_type = 'P' THEN implied_vol END) AS far_put_iv
        FROM ranked
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"uid": underlying_id}).mappings().first()
    if not row:
        return {"call": None, "put": None}

    near_call = row.get("near_call_iv")
    far_call  = row.get("far_call_iv")
    near_put  = row.get("near_put_iv")
    far_put   = row.get("far_put_iv")

    slope_call = round(float(near_call) - float(far_call), 4) if near_call and far_call else None
    slope_put  = round(float(near_put)  - float(far_put),  4) if near_put  and far_put  else None

    return {"call": slope_call, "put": slope_put}


def _build_summary(ctx: Dict[str, Any]) -> str:
    """生成自然语言摘要，供拼入LLM prompt。"""
    uid = ctx["underlying_id"]
    close = ctx["last_close"]
    vs20 = ctx.get("vs_ma20_pct")
    vs60 = ctx.get("vs_ma60_pct")
    ret5 = ctx.get("ret5d_pct")
    ret20 = ctx.get("ret20d_pct")
    hv20 = ctx.get("hv20")
    skew = ctx.get("put_call_skew")
    term_slope_call = ctx.get("term_slope_call")
    term_slope_put  = ctx.get("term_slope_put")
    trend = ctx.get("trend", "sideways")

    parts = [f"{uid}当前收盘价{close}"]

    if vs20 is not None:
        direction = "上方" if vs20 > 0 else "下方"
        parts.append(f"在MA20{direction}{abs(vs20):.1f}%")
    if vs60 is not None:
        direction = "上方" if vs60 > 0 else "下方"
        parts.append(f"在MA60{direction}{abs(vs60):.1f}%")

    if ret5 is not None:
        parts.append(f"近5日{'+' if ret5 >= 0 else ''}{ret5:.1f}%")
    if ret20 is not None:
        parts.append(f"近20日{'+' if ret20 >= 0 else ''}{ret20:.1f}%")

    if hv20 is not None:
        parts.append(f"HV20={hv20:.1%}")

    trend_map = {"uptrend": "短期趋势偏多", "downtrend": "短期趋势偏空", "sideways": "短期横盘震荡"}
    parts.append(trend_map.get(trend, ""))

    if skew is not None:
        if skew > 0.005:
            parts.append(f"put偏贵(skew={skew:.3f})，市场隐含偏空情绪")
        elif skew < -0.005:
            parts.append(f"call偏贵(skew={skew:.3f})，市场隐含偏多情绪")
        else:
            parts.append(f"put/call IV基本对称(skew={skew:.3f})")

    # call期限结构
    if term_slope_call is not None:
        if term_slope_call > 0.01:
            parts.append(f"call近月IV高于远月{term_slope_call:.3f}，call_calendar/diagonal_call有利")
        elif term_slope_call < -0.01:
            parts.append(f"call远月IV高于近月{abs(term_slope_call):.3f}，call_calendar不利")

    # put期限结构
    if term_slope_put is not None:
        if term_slope_put > 0.01:
            parts.append(f"put近月IV高于远月{term_slope_put:.3f}，put_calendar/diagonal_put有利")
        elif term_slope_put < -0.01:
            parts.append(f"put远月IV高于近月{abs(term_slope_put):.3f}，put_calendar不利")

    return "，".join(p for p in parts if p) + "。"


def build_market_context(
    engine: Engine,
    underlying_id: str,
    iv_pct: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    计算单个标的的市场背景指标。
    返回None表示数据不足（K线少于20条）。
    """
    rows = _fetch_kline(engine, underlying_id, n=65)
    if len(rows) < 20:
        return None

    closes = [r["close_price"] for r in rows if r.get("close_price") is not None]
    if len(closes) < 20:
        return None

    last_close = closes[-1]
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    hv20 = _calc_hv(closes, 20)

    vs_ma20_pct = round((last_close / ma20 - 1) * 100, 2) if ma20 else None
    vs_ma60_pct = round((last_close / ma60 - 1) * 100, 2) if ma60 else None

    ret5d_pct  = round((last_close / closes[-6]  - 1) * 100, 2) if len(closes) >= 6  else None
    ret20d_pct = round((last_close / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None

    trend = _infer_trend(
        vs_ma20_pct or 0,
        vs_ma60_pct or 0,
        ret20d_pct or 0,
    )

    skew = _fetch_iv_skew(engine, underlying_id)
    term_slopes = _fetch_term_slopes(engine, underlying_id)

    ctx: Dict[str, Any] = {
        "underlying_id":   underlying_id,
        "last_close":      round(last_close, 4),
        "ma20":            ma20,
        "ma60":            ma60,
        "vs_ma20_pct":     vs_ma20_pct,
        "vs_ma60_pct":     vs_ma60_pct,
        "ret5d_pct":       ret5d_pct,
        "ret20d_pct":      ret20d_pct,
        "hv20":            hv20,
        "iv_pct":          iv_pct,
        "put_call_skew":   skew,
        "term_slope_call": term_slopes["call"],
        "term_slope_put":  term_slopes["put"],
        "trend":           trend,
    }
    ctx["summary"] = _build_summary(ctx)
    return ctx


def build_market_context_multi(
    engine: Engine,
    underlying_ids: List[str],
    iv_pcts: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, Any]]:
    """批量计算多个标的的市场背景，返回 {uid: ctx}。"""
    result = {}
    for uid in underlying_ids:
        iv_pct = (iv_pcts or {}).get(uid)
        ctx = build_market_context(engine, uid, iv_pct=iv_pct)
        if ctx:
            result[uid] = ctx
    return result