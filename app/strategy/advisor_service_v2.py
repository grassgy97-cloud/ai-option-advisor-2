from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy.engine import Engine
from app.ai.llm_parser import parse_with_llm
from app.models.schemas import (
    AdvisorRunResponse,
    IntentSpec,
    ResolvedStrategy,
)
from app.strategy.compiler import compile_intent_to_strategies
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.strategy_resolver import (
    load_market_snapshot,
    resolve_strategy_from_snapshot,
)
from app.strategy.greeks_monitor import build_strategy_greeks_report
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.briefing import build_briefing

UNDERLYING_KEYWORDS = {
    "510050": ["上证50", "上证 50", "50etf", "510050", "上证五十", "50 etf"],
    "510300": ["沪深300", "沪深 300", "300etf", "510300", "沪深三百", "300 etf", "沪深300etf"],
    "510500": ["中证500", "中证 500", "500etf", "510500", "中证五百", "500 etf"],
    "588000": ["科创50", "科创 50", "科创板50", "科创板 50", "科创etf", "588000", "科创五十"],
    "588080": ["588080", "科创50易方达", "易方达科创", "易方达588080"],
    "159901": ["深证100", "深证 100", "深100", "100etf", "159901", "深证一百"],
    "159915": ["创业板", "创业板etf", "159915", "创业板100", "创业etf"],
    "159919": ["159919", "沪深300深", "300etf深", "华泰柏瑞", "嘉实300"],
    "159922": ["159922", "中证500深", "嘉实500", "嘉实中证500"],
}

ALL_UNDERLYING_IDS = [
    "510300", "510050", "510500",
    "588000", "588080",
    "159915", "159901", "159919", "159922",
]


def _merge_greeks_with_context(
    greeks_preference: dict,
    market_context: dict,
    user_weight: float = 0.8,
    machine_weight: float = 0.2,
) -> dict:
    if not market_context:
        return greeks_preference

    machine_signals: Dict[str, Dict[str, Any]] = {}

    for uid, ctx in market_context.items():
        trend = ctx.get("trend")
        hv20 = ctx.get("hv20") or 0.0
        skew = ctx.get("put_call_skew") or 0.0

        if trend == "downtrend":
            sig = {"sign": "negative", "strength": 0.4}
        elif trend == "uptrend":
            sig = {"sign": "positive", "strength": 0.4}
        else:
            sig = None

        if sig:
            existing = machine_signals.get("delta")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["delta"] = sig

        if hv20 > 0.25:
            sig = {"sign": "positive", "strength": 0.3}
            existing = machine_signals.get("gamma")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["gamma"] = sig

        if abs(skew) > 0.03:
            sig = {"sign": "positive", "strength": 0.25}
            existing = machine_signals.get("theta")
            if existing is None or sig["strength"] > existing["strength"]:
                machine_signals["theta"] = sig

    merged = dict(greeks_preference)

    for greek, m_pref in machine_signals.items():
        m_sign = m_pref["sign"]
        m_strength = m_pref["strength"]

        if greek in merged:
            u_pref = merged[greek]
            u_sign = u_pref["sign"]
            u_strength = u_pref["strength"]

            if u_sign == m_sign:
                new_strength = min(1.0, u_strength * user_weight + m_strength * machine_weight)
            else:
                new_strength = max(0.0, u_strength * user_weight - m_strength * machine_weight)

            merged[greek] = {"sign": u_sign, "strength": round(new_strength, 3)}
        else:
            merged[greek] = {
                "sign": m_sign,
                "strength": round(m_strength * machine_weight, 3),
            }

    return merged


def parse_text_to_intent(
    text: str,
    underlying_id: str = "510300",
    market_context: Optional[dict] = None,
) -> IntentSpec:
    result = parse_with_llm(text, market_context=market_context)

    if result is None:
        print("[parse_text_to_intent] LLM failed, falling back to rule parser")
        return _rule_parse_text_to_intent(text, underlying_id)

    underlying_ids = result.get("underlying_ids", [])
    if not underlying_ids:
        underlying_ids = [underlying_id]

    preferred = result.get("preferred_strategies", [])
    allowed_strategies = preferred if preferred else None

    raw_greeks = result.get("greeks_preference", {})
    greeks_preference = {}
    if isinstance(raw_greeks, dict):
        for greek, pref in raw_greeks.items():
            if not isinstance(pref, dict):
                continue
            sign = pref.get("sign")
            strength = pref.get("strength")
            if sign not in ("positive", "negative", "neutral"):
                continue
            try:
                strength = float(strength)
            except (TypeError, ValueError):
                continue
            if not (0.0 <= strength <= 1.0):
                continue
            greeks_preference[greek] = {"sign": sign, "strength": strength}

    if market_context:
        greeks_preference = _merge_greeks_with_context(
            greeks_preference, market_context,
            user_weight=0.85, machine_weight=0.15,
        )

    raw_price_levels = result.get("price_levels", {})
    price_levels = {}
    if isinstance(raw_price_levels, dict):
        for k in ("support", "resistance", "target"):
            v = raw_price_levels.get(k)
            if v is not None:
                try:
                    price_levels[k] = float(v)
                except (TypeError, ValueError):
                    pass

    asymmetry = result.get("asymmetry")
    if asymmetry not in ("upside", "downside", "symmetric", None):
        asymmetry = None

    return IntentSpec(
        underlying_id=underlying_ids[0],
        underlying_ids=underlying_ids,
        market_view=result.get("market_view", "neutral"),
        vol_view=result.get("vol_view", "none"),
        risk_preference=result.get("risk_preference", "low"),
        defined_risk_only=result.get("defined_risk_only", False),
        prefer_multi_leg=result.get("prefer_multi_leg", False),
        dte_min=result.get("dte_min", 20),
        dte_max=result.get("dte_max", 45),
        max_rel_spread=0.03,
        min_quote_size=1,
        allowed_strategies=allowed_strategies,
        banned_strategies=result.get("banned_strategies", []),
        raw_text=text,
        greeks_preference=greeks_preference,
        price_levels=price_levels,
        asymmetry=asymmetry,
        market_context_data=market_context or {},
    )


def _parse_underlying_ids(text: str, default_id: str) -> List[str]:
    t = text.lower()
    found = []
    for uid, keywords in UNDERLYING_KEYWORDS.items():
        if any(k in t for k in keywords):
            found.append(uid)
    return found if found else [default_id]


def _rule_parse_text_to_intent(text: str, underlying_id: str = "510300") -> IntentSpec:
    t = (text or "").strip().lower()

    market_view = "neutral"
    vol_view = "none"
    risk_preference = "low"
    defined_risk_only = False
    prefer_multi_leg = False
    dte_min = 20
    dte_max = 45
    max_rel_spread = 0.03
    min_quote_size = 1
    banned_strategies: List[str] = []
    allowed_strategies = None

    if any(k in t for k in ["轻微看多", "略看多", "小幅看多", "偏多", "看涨", "bullish"]):
        market_view = "bullish"
    elif any(k in t for k in ["轻微看空", "略看空", "小幅看空", "偏空", "看跌", "bearish"]):
        market_view = "bearish"

    if any(k in t for k in ["波动率偏高", "隐波高", "iv高", "vol high", "high iv"]):
        vol_view = "iv_high"
    elif any(k in t for k in ["波动率偏低", "隐波低", "iv低", "vol low", "low iv"]):
        vol_view = "iv_low"
    elif any(k in t for k in ["认购偏贵", "call贵", "call iv rich", "call_iv_rich"]):
        vol_view = "call_iv_rich"
    elif any(k in t for k in ["认沽偏贵", "put贵", "put iv rich", "put_iv_rich"]):
        vol_view = "put_iv_rich"
    elif any(k in t for k in ["近月更贵", "近月波动率高", "front high", "term_front_high"]):
        vol_view = "term_front_high"
    elif any(k in t for k in ["远月更贵", "远月波动率高", "back high", "term_back_high"]):
        vol_view = "term_back_high"

    if any(k in t for k in ["低风险", "保守", "low risk"]):
        risk_preference = "low"
    elif any(k in t for k in ["高风险", "激进", "high risk"]):
        risk_preference = "high"
    else:
        risk_preference = "medium" if "中风险" in t else "low"

    if any(k in t for k in ["不裸卖", "defined risk", "定义损失", "有限风险"]):
        defined_risk_only = True
    if any(k in t for k in ["多腿", "组合", "spread", "calendar", "diagonal", "跨期", "价差"]):
        prefer_multi_leg = True
    if any(k in t for k in ["近月", "front month"]):
        dte_min, dte_max = 10, 35
    if any(k in t for k in ["中期", "30到60天", "30-60天"]):
        dte_min, dte_max = 30, 60
    if any(k in t for k in ["不做日历", "不要calendar", "no calendar"]):
        banned_strategies.extend(["call_calendar", "put_calendar"])
    if any(k in t for k in ["不做对角", "不要diagonal", "no diagonal"]):
        banned_strategies.extend(["diagonal_call", "diagonal_put"])

    if any(k in t for k in ["备兑", "covered call", "卖备兑"]):
        allowed_strategies = ["covered_call"]

    underlying_ids = _parse_underlying_ids(t, underlying_id)
    return IntentSpec(
        underlying_id=underlying_ids[0],
        underlying_ids=underlying_ids,
        market_view=market_view,
        vol_view=vol_view,
        risk_preference=risk_preference,
        defined_risk_only=defined_risk_only,
        prefer_multi_leg=prefer_multi_leg,
        dte_min=dte_min,
        dte_max=dte_max,
        max_rel_spread=max_rel_spread,
        min_quote_size=min_quote_size,
        allowed_strategies=allowed_strategies,
        banned_strategies=banned_strategies,
        raw_text=text,
        greeks_preference={},
        price_levels={},
        asymmetry=None,
        market_context_data={},
    )


def build_disabled_backtest(resolved_candidates):
    items = []
    for s in resolved_candidates[:5]:
        items.append({
            "strategy_type": s.strategy_type,
            "score": s.score,
            "net_credit": s.net_credit,
            "net_debit": s.net_debit,
            "legs": [
                {
                    "contract_id": leg.contract_id,
                    "action": leg.action,
                    "option_type": leg.option_type,
                    "expiry_date": leg.expiry_date,
                    "strike": leg.strike,
                    "mid": leg.mid,
                    "delta": leg.delta,
                    "gamma": leg.gamma,
                    "theta": leg.theta,
                    "vega": leg.vega,
                    "iv": leg.iv,
                    "dte": leg.dte,
                }
                for leg in s.legs
            ],
            "greeks_report": s.metadata.get("greeks_report", {}),
        })
    return {
        "status": "disabled",
        "summary": "当前阶段暂不做期权回测，重点转向真实选腿、评分统一与 Greeks 监控。",
        "items": items,
    }


def run_advisor(engine: Engine, text: str, underlying_id: str = "510300") -> AdvisorRunResponse:
    from app.data.market_context import build_market_context_multi
    import time

    t0_all = time.perf_counter()

    # 扫描模式：先为全部候选标的准备上下文
    # 单标的模式：只准备当前标的
    ctx_ids = ALL_UNDERLYING_IDS if underlying_id == "ALL" else [underlying_id]

    # 先算 iv_percentile：既给 market_context 用，也给后续 greeks_report 复用
    t0 = time.perf_counter()
    iv_reports: Dict[str, Optional[Dict[str, Any]]] = {}
    iv_pcts: Dict[str, Optional[float]] = {}

    for uid in ctx_ids:
        try:
            rpt = build_iv_percentile_report(engine, uid)
            iv_reports[uid] = rpt
            if rpt:
                iv_pcts[uid] = rpt.get("composite_percentile")
            else:
                iv_pcts[uid] = None
        except Exception as e:
            print(f"[run_advisor] iv_percentile failed for {uid}: {e}")
            iv_reports[uid] = None
            iv_pcts[uid] = None

    print(f"[timing] build_iv_percentile_report total = {time.perf_counter() - t0:.3f}s")

    # 计算 market_context
    t0 = time.perf_counter()
    try:
        market_context = build_market_context_multi(engine, ctx_ids, iv_pcts=iv_pcts)
    except Exception as e:
        print(f"[run_advisor] market_context failed: {e}")
        market_context = {}
    print(f"[timing] build_market_context_multi = {time.perf_counter() - t0:.3f}s")

    # 解析意图（含二八合成）
    t0 = time.perf_counter()
    intent = parse_text_to_intent(
        text=text,
        underlying_id=underlying_id,
        market_context=market_context,
    )
    print(f"[timing] parse_text_to_intent = {time.perf_counter() - t0:.3f}s")

    # 这里先保持你当前语义：
    # 单标的请求强制只跑传入 uid；
    # ALL 模式仍由 intent.effective_underlying_ids 决定。
    # （如果你后面要改成“ALL 强制全扫”，那是下一步。）
    if underlying_id != "ALL":
        target_ids = [underlying_id]
    else:
        target_ids = intent.effective_underlying_ids

    all_resolved: List[ResolvedStrategy] = []

    for uid in target_ids:
        t0_uid = time.perf_counter()

        uid_intent = intent.model_copy(update={"underlying_id": uid})
        iv_pct = iv_pcts.get(uid)

        t0 = time.perf_counter()
        candidate_specs = compile_intent_to_strategies(uid_intent, iv_pct=iv_pct)
        print(f"[timing] {uid} compile_intent_to_strategies = {time.perf_counter() - t0:.3f}s, specs={len(candidate_specs)}")

        t0 = time.perf_counter()
        try:
            snapshot = load_market_snapshot(engine, uid)
        except Exception as e:
            print(f"[run_advisor] load snapshot failed for {uid}: {e}")
            continue
        print(f"[timing] {uid} load_market_snapshot = {time.perf_counter() - t0:.3f}s, quotes={len(snapshot.merged_quotes)}")

        resolved_count = 0
        t0 = time.perf_counter()
        for spec in candidate_specs:
            try:
                rs = resolve_strategy_from_snapshot(snapshot, spec)
                if rs is not None:
                    rs.metadata["greeks_preference"] = uid_intent.greeks_preference
                    rs.metadata["iv_pct"] = iv_pct
                    all_resolved.append(rs)
                    resolved_count += 1
            except Exception as e:
                print(f"[run_advisor] {uid} {spec.strategy_type} failed: {e}")
        print(f"[timing] {uid} resolve all specs = {time.perf_counter() - t0:.3f}s, resolved={resolved_count}")

        print(f"[timing] {uid} total = {time.perf_counter() - t0_uid:.3f}s")

    t0 = time.perf_counter()
    ranked = rank_strategies(all_resolved)
    print(f"[timing] rank_strategies = {time.perf_counter() - t0:.3f}s, ranked={len(ranked)}")

    # 这里改为复用前面已经算过的 iv_reports，避免每个策略再次查库
    t0 = time.perf_counter()
    for s in ranked:
        s.metadata["greeks_report"] = build_strategy_greeks_report(
            strategy=s,
            iv_pct_report=iv_reports.get(s.underlying_id),
        )
    print(f"[timing] build_strategy_greeks_report total = {time.perf_counter() - t0:.3f}s")

    t0 = time.perf_counter()
    backtest_result = build_disabled_backtest(ranked)

    resp = AdvisorRunResponse(
        parsed_intent=intent,
        candidate_strategies=[],
        resolved_candidates=ranked,
        backtest_result=backtest_result,
    )
    resp.calendar_recommendations = []
    resp.briefing = build_briefing(ranked, text, market_context=market_context)
    print(f"[timing] build_briefing + response = {time.perf_counter() - t0:.3f}s")

    print(f"[timing] run_advisor TOTAL = {time.perf_counter() - t0_all:.3f}s")
    return resp