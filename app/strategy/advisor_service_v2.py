from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.engine import Engine
from app.ai.llm_parser import parse_with_llm
from app.models.schemas import (
    AdvisorRunResponse,
    IntentSpec,
    ResolvedStrategy,
)
from app.strategy.compiler import compile_intent_to_strategies
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.strategy_resolver import resolve_strategy
from app.strategy.greeks_monitor import build_strategy_greeks_report
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.briefing import build_briefing

UNDERLYING_KEYWORDS = {
    # 上证50
    "510050": [
        "上证50", "上证 50", "50etf", "510050",
        "上证五十", "50 etf",
    ],
    # 沪深300（上交所）
    "510300": [
        "沪深300", "沪深 300", "300etf", "510300",
        "沪深三百", "300 etf", "沪深300etf",
    ],
    # 中证500
    "510500": [
        "中证500", "中证 500", "500etf", "510500",
        "中证五百", "500 etf",
    ],
    # 科创50
    "588000": [
        "科创50", "科创 50", "科创板50", "科创板 50",
        "科创etf", "588000", "科创五十",
    ],
    # 深证100
    "159901": [
        "深证100", "深证 100", "深100", "100etf",
        "159901", "深证一百",
    ],
    # 创业板
    "159915": [
        "创业板", "创业板etf", "159915",
        "创业板100", "创业etf",
    ],
    # 沪深300（深交所）
    "159919": [
        "159919", "沪深300深", "300etf深",
        "华泰柏瑞", "嘉实300",  # 常见简称
    ],
}


def parse_text_to_intent(text: str, underlying_id: str = "510300") -> IntentSpec:
    result = parse_with_llm(text)

    if result is None:
        # LLM 调用失败，fallback 到规则解析
        print("[parse_text_to_intent] LLM failed, falling back to rule parser")
        return _rule_parse_text_to_intent(text, underlying_id)

    underlying_ids = result.get("underlying_ids", [underlying_id])
    if not underlying_ids:
        underlying_ids = [underlying_id]

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
        banned_strategies=result.get("banned_strategies", []),
        raw_text=text,
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

    # 市场方向
    if any(k in t for k in ["轻微看多", "略看多", "小幅看多", "偏多", "看涨", "bullish"]):
        market_view = "bullish"
    elif any(k in t for k in ["轻微看空", "略看空", "小幅看空", "偏空", "看跌", "bearish"]):
        market_view = "bearish"
    else:
        market_view = "neutral"

    # 波动率 / 结构观点
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

    # 风险偏好
    if any(k in t for k in ["低风险", "保守", "low risk"]):
        risk_preference = "low"
    elif any(k in t for k in ["高风险", "激进", "high risk"]):
        risk_preference = "high"
    else:
        risk_preference = "medium" if "中风险" in t else "low"

    # 定义损失 / 多腿偏好
    if any(k in t for k in ["不裸卖", "defined risk", "定义损失", "有限风险"]):
        defined_risk_only = True

    if any(k in t for k in ["多腿", "组合", "spread", "calendar", "diagonal", "跨期", "价差"]):
        prefer_multi_leg = True

    # DTE 提示
    if any(k in t for k in ["近月", "front month"]):
        dte_min = 10
        dte_max = 35

    if any(k in t for k in ["中期", "30到60天", "30-60天"]):
        dte_min = 30
        dte_max = 60

    # 黑名单
    if any(k in t for k in ["不做日历", "不要calendar", "no calendar"]):
        banned_strategies.extend(["call_calendar", "put_calendar"])

    if any(k in t for k in ["不做对角", "不要diagonal", "no diagonal"]):
        banned_strategies.extend(["diagonal_call", "diagonal_put"])

    underlying_ids = _parse_underlying_ids(t, underlying_id)
    return IntentSpec(
        underlying_id=underlying_ids[0],  # 兼容旧字段
        underlying_ids=underlying_ids,  # 新字段
        market_view=market_view,          # type: ignore[arg-type]
        vol_view=vol_view,                # type: ignore[arg-type]
        risk_preference=risk_preference,  # type: ignore[arg-type]
        defined_risk_only=defined_risk_only,
        prefer_multi_leg=prefer_multi_leg,
        dte_min=dte_min,
        dte_max=dte_max,
        max_rel_spread=max_rel_spread,
        min_quote_size=min_quote_size,
        allowed_strategies=allowed_strategies,
        banned_strategies=banned_strategies,
        raw_text=text,
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
            # ✅ 直接从 metadata 读，不重复计算
            "greeks_report": s.metadata.get("greeks_report", {}),
        })
    return {
        "status": "disabled",
        "summary": "当前阶段暂不做期权回测，重点转向真实选腿、评分统一与 Greeks 监控。",
        "items": items,
    }


def run_advisor(engine: Engine, text: str, underlying_id: str = "510300") -> AdvisorRunResponse:
    # 先 parse（会识别出多标的）
    intent = parse_text_to_intent(text=text, underlying_id=underlying_id)
    target_ids = intent.effective_underlying_ids

    all_resolved: List[ResolvedStrategy] = []

    for uid in target_ids:
        uid_intent = intent.model_copy(update={"underlying_id": uid})

        # 拿该标的 IV percentile
        iv_report = build_iv_percentile_report(engine, uid)
        iv_pct = iv_report["composite_percentile"] if iv_report else None

        candidate_specs = compile_intent_to_strategies(uid_intent, iv_pct=iv_pct)

        for spec in candidate_specs:
            try:
                rs = resolve_strategy(engine, spec)
                if rs is not None:
                    all_resolved.append(rs)
            except Exception as e:
                print(f"[run_advisor] {uid} {spec.strategy_type} failed: {e}")

    # 统一排序
    ranked = rank_strategies(all_resolved)

    # greeks_report 挂到 metadata
    for s in ranked:
        s.metadata["greeks_report"] = build_strategy_greeks_report(s, engine=engine)

    backtest_result = build_disabled_backtest(ranked)

    resp = AdvisorRunResponse(
        parsed_intent=intent,
        candidate_strategies=[],   # 多标的下 candidate_strategies 意义不大，留空
        resolved_candidates=ranked,
        backtest_result=backtest_result,
    )
    resp.calendar_recommendations = []
    resp.briefing = build_briefing(ranked, text)  # ← 加这行
    return resp