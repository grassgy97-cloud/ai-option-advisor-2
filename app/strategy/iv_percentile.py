"""
iv_percentile.py — IV 百分位计算模块

双轨制设计：
  - 轨道1（先验）：基于A股ETF期权历史经验的绝对区间锚定，数据不足时权重100%
  - 轨道2（历史）：滚动N天历史分位，数据越多权重越高（上限70%）
  - 合成分位 = hist_weight * hist_pct + (1-hist_weight) * prior_pct
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine


# ==============================
# 先验 IV 区间（六档锚点）
# ==============================

IV_PRIOR_RANGE: Dict[str, Dict[str, float]] = {
    "510300": {
        "p10": 0.110, "p25": 0.135, "p50": 0.160,
        "p75": 0.200, "p90": 0.280, "p99": 0.480,
    },
    "510050": {
        "p10": 0.100, "p25": 0.125, "p50": 0.150,
        "p75": 0.190, "p90": 0.265, "p99": 0.460,
    },
    "510500": {
        "p10": 0.140, "p25": 0.170, "p50": 0.210,
        "p75": 0.260, "p90": 0.360, "p99": 0.600,
    },
    "588000": {
        "p10": 0.180, "p25": 0.220, "p50": 0.270,
        "p75": 0.340, "p90": 0.480, "p99": 0.750,
    },
    "159915": {
        "p10": 0.150, "p25": 0.185, "p50": 0.225,
        "p75": 0.290, "p90": 0.400, "p99": 0.650,
    },
    "159901": {
        "p10": 0.110, "p25": 0.135, "p50": 0.160,
        "p75": 0.200, "p90": 0.280, "p99": 0.480,
    },
    "159919": {
        "p10": 0.110, "p25": 0.135, "p50": 0.160,
        "p75": 0.200, "p90": 0.280, "p99": 0.480,
    },
    "159922": {  # 中证500(深)，同510500
        "p10": 0.140, "p25": 0.170, "p50": 0.210,
        "p75": 0.260, "p90": 0.360, "p99": 0.600,
    },
    "588080": {  # 科创50(易方达)，同588000
        "p10": 0.180, "p25": 0.220, "p50": 0.270,
        "p75": 0.340, "p90": 0.480, "p99": 0.750,
    },
}

_DEFAULT_PRIOR = IV_PRIOR_RANGE["510300"]


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


def fetch_historical_atm_iv(
    engine: Engine,
    underlying_id: str,
    lookback_days: int = 90,
) -> List[float]:
    """
    每天取 dte 最小（>10天）且 delta 最接近 0.5 的 CALL 合约 IV。
    注意：INTERVAL 不支持参数绑定，用 f-string 注入 lookback_days。
    """
    sql = text(f"""
        WITH daily_atm AS (
            SELECT
                DATE(fetch_time) AS trade_date,
                implied_vol,
                ROW_NUMBER() OVER (
                    PARTITION BY DATE(fetch_time)
                    ORDER BY dte_calendar ASC, ABS(delta - 0.5) ASC
                ) AS rn
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
              AND option_type IN ('CALL', 'C')
              AND delta IS NOT NULL
              AND implied_vol IS NOT NULL
              AND implied_vol > 0.01
              AND implied_vol < 2.0
              AND dte_calendar > 10
              AND fetch_time >= NOW() - INTERVAL '{lookback_days} days'
        )
        SELECT trade_date, implied_vol
        FROM daily_atm
        WHERE rn = 1
        ORDER BY trade_date ASC
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"underlying_id": underlying_id}).fetchall()
        return [float(r[1]) for r in rows if r[1] is not None]
    except Exception as e:
        print(f"[iv_percentile] fetch_historical_atm_iv failed: {e}")
        return []


def get_current_atm_iv(
    engine: Engine,
    underlying_id: str,
) -> Optional[float]:
    """
    从最新快照取 ATM IV：dte最小（>10天）且 delta 最接近0.5 的 CALL。
    """
    sql = text("""
        WITH latest AS (
            SELECT MAX(fetch_time) AS max_fetch_time
            FROM option_factor_snapshots
            WHERE underlying_id = :underlying_id
        )
        SELECT implied_vol
        FROM option_factor_snapshots
        WHERE underlying_id = :underlying_id
          AND fetch_time = (SELECT max_fetch_time FROM latest)
          AND option_type IN ('CALL', 'C')
          AND delta IS NOT NULL
          AND implied_vol IS NOT NULL
          AND implied_vol > 0.01
          AND dte_calendar > 10
        ORDER BY dte_calendar ASC, ABS(delta - 0.5) ASC
        LIMIT 1
    """)
    try:
        with engine.connect() as conn:
            row = conn.execute(sql, {"underlying_id": underlying_id}).fetchone()
        return float(row[0]) if row else None
    except Exception as e:
        print(f"[iv_percentile] get_current_atm_iv failed: {e}")
        return None


def calc_iv_percentile(
    current_iv: float,
    underlying_id: str,
    engine: Optional[Engine] = None,
    historical_ivs: Optional[List[float]] = None,
    lookback_days: int = 90,
    min_history_days: int = 10,
) -> Dict[str, Any]:
    prior = IV_PRIOR_RANGE.get(underlying_id, _DEFAULT_PRIOR)
    prior_pct = _calc_prior_percentile(current_iv, prior)

    if historical_ivs is None and engine is not None:
        historical_ivs = fetch_historical_atm_iv(engine, underlying_id, lookback_days)
    if historical_ivs is None:
        historical_ivs = []

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
        "underlying_id": underlying_id,
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


def _get_label(pct: float):
    if pct <= 0.15:
        return "极低", "very_low"
    elif pct <= 0.30:
        return "偏低", "low"
    elif pct <= 0.55:
        return "正常", "normal"
    elif pct <= 0.70:
        return "偏高", "elevated"
    elif pct <= 0.85:
        return "高", "high"
    else:
        return "极高", "very_high"


def _get_strategy_hints(pct: float) -> List[str]:
    if pct <= 0.15:
        return [
            "IV极低，远月买单边（long call/put）性价比高",
            "不宜做纯卖方策略，时间价值薄",
        ]
    elif pct <= 0.30:
        return [
            "IV偏低，calendar/diagonal收theta为主",
            "单边卖出需谨慎，权利金较薄",
        ]
    elif pct <= 0.55:
        return [
            "IV正常，calendar/diagonal结构合适",
            "卖方策略风险收益适中",
        ]
    elif pct <= 0.70:
        return [
            "IV偏高，卖方策略有一定优势",
            "虚值卖出或备兑卖出性价比提升",
        ]
    elif pct <= 0.85:
        return [
            "IV高，卖方策略机会明显",
            "可考虑卖虚值单腿或iron condor",
            "注意控制gamma风险，市场可能继续波动",
        ]
    else:
        return [
            "IV极高（极端市场），卖方机会很大但风险同样极大",
            "建议用defined-risk结构（vertical/condor）替代裸卖",
            "不宜单向买入，时间价值衰减极快",
        ]


def build_iv_percentile_report(
    engine: Engine,
    underlying_id: str,
    lookback_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """
    主入口：拉当前ATM IV + 历史序列 + 计算分位。
    供 greeks_monitor.build_strategy_greeks_report 调用。
    """
    current_iv = get_current_atm_iv(engine, underlying_id)
    if current_iv is None:
        print(f"[iv_percentile] 无法获取 {underlying_id} 的当前 ATM IV")
        return None

    return calc_iv_percentile(
        current_iv=current_iv,
        underlying_id=underlying_id,
        engine=engine,
        lookback_days=lookback_days,
    )