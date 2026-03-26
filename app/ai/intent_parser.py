from app.models.schemas import ParsedIntent
from app.ai.llm_parser import parse_with_llm


def parse_natural_language(text: str) -> ParsedIntent:
    # 先尝试 LLM 解析
    llm_result = parse_with_llm(text)

    if llm_result:
        try:
            return ParsedIntent(
                underlying_id=llm_result.get("underlying_id", "510300"),
                underlying_specified=llm_result.get("underlying_specified", False),
                mode=llm_result.get("mode", "user_driven"),
                raw_view=text,
                market_view=llm_result.get("market_view"),
                vol_view=llm_result.get("vol_view"),
                direction_bias=llm_result.get("direction_bias", "neutral"),
                holding_period_days=llm_result.get("holding_period_days", 3),
                risk_preference=llm_result.get("risk_preference", "medium"),
                defined_risk_only=llm_result.get("defined_risk_only", False),
                prefer_multi_leg=llm_result.get("prefer_multi_leg", False),
                allow_single_leg=llm_result.get("allow_single_leg", True),
                strategy_whitelist=llm_result.get("strategy_whitelist", []),
                strategy_blacklist=llm_result.get("strategy_blacklist", []),
                target_greeks_json={},
                scenario_filters_json={},
                status="parsed_llm"
            )
        except Exception as e:
            print(f"[intent_parser] LLM结果构建失败: {e}")

    # LLM失败则降级到规则版
    print("[intent_parser] 降级到规则版")
    return _parse_rule_based(text)


def _parse_rule_based(text: str) -> ParsedIntent:
    text_lower = text.lower()
    underlying = "510300" if "300etf" in text_lower or "300 etf" in text_lower else "510050"
    vol_view = None
    if "认购偏贵" in text or "call偏贵" in text_lower:
        vol_view = "call_iv_rich"
    elif "认沽偏贵" in text or "put偏贵" in text_lower:
        vol_view = "put_iv_rich"

    defined_risk_only = any(x in text for x in ["低风险", "风险可控", "不想裸卖"])
    prefer_multi_leg = any(x in text for x in ["组合", "价差", "跨期"])
    allow_single_leg = not prefer_multi_leg
    risk_preference = "low" if defined_risk_only else "medium"

    if vol_view in ["call_iv_rich", "put_iv_rich"] and defined_risk_only:
        strategy_whitelist = ["vertical_spread", "calendar_spread", "diagonal_spread"]
    else:
        strategy_whitelist = ["long_call_put", "vertical_spread", "calendar_spread"]

    return ParsedIntent(
        underlying_id=underlying,
        underlying_specified=False,
        mode="user_driven",
        raw_view=text,
        market_view="neutral",
        vol_view=vol_view,
        direction_bias="neutral",
        holding_period_days=3,
        risk_preference=risk_preference,
        defined_risk_only=defined_risk_only,
        prefer_multi_leg=prefer_multi_leg,
        allow_single_leg=allow_single_leg,
        strategy_whitelist=strategy_whitelist,
        strategy_blacklist=["naked_short"] if defined_risk_only else [],
        target_greeks_json={},
        scenario_filters_json={},
        status="parsed_rule_based"
    )