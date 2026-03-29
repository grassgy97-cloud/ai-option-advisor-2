from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.engine import Engine

from app.models.schemas import (
    AdvisorRunResponse,
    IntentSpec,
    ResolvedStrategy,
)
from app.strategy.compiler import compile_intent_to_strategies
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.strategy_resolver import resolve_strategy
from app.strategy.greeks_monitor import build_strategy_greeks_report


def parse_text_to_intent(text: str, underlying_id: str = "510300") -> IntentSpec:
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

    return IntentSpec(
        underlying_id=underlying_id,
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


def build_disabled_backtest(resolved_candidates: List[ResolvedStrategy]) -> Dict[str, Any]:
    """
    当前阶段暂不做期权回测。
    backtest_result 仅保留接口占位与说明。
    """
    items = []
    for s in resolved_candidates[:5]:
        items.append(
            {
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
                "greeks_report": build_strategy_greeks_report(s),
            }
        )

    return {
        "status": "disabled",
        "summary": "当前阶段暂不做期权回测，重点转向真实选腿、评分统一与 Greeks 监控。",
        "items": items,
    }


def run_advisor(engine: Engine, text: str, underlying_id: str = "510300") -> AdvisorRunResponse:
    intent = parse_text_to_intent(text=text, underlying_id=underlying_id)

    candidate_specs = compile_intent_to_strategies(intent)

    resolved: List[ResolvedStrategy] = []
    for spec in candidate_specs:
        try:
            rs = resolve_strategy(engine, spec)
            if rs is not None:
                resolved.append(rs)
        except Exception as e:
            print(f"[run_advisor] resolve_strategy failed: {spec.strategy_type}, err={e}")

    ranked = rank_strategies(resolved)
    backtest_result = build_disabled_backtest(ranked)

    resp = AdvisorRunResponse(
        parsed_intent=intent,
        candidate_strategies=candidate_specs,
        resolved_candidates=ranked,
        backtest_result=backtest_result,
    )

    # 当前阶段收敛输出，不再维护平行的 calendar_recommendations 推荐链
    resp.calendar_recommendations = []
    return resp