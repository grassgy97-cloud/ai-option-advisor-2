from app.ai.intent_parser import parse_natural_language
from app.strategy.router import route_strategies
from app.strategy.scanner import scan_static_opportunities
from app.backtest.engine import run_simple_backtest
from app.ai.advisor_llm import run_advisor_llm


def run_advisor(user_text: str) -> dict:
    parsed = parse_natural_language(user_text)
    parsed_dict = parsed.model_dump()
    parsed_dict = _force_mode_if_needed(parsed_dict)

    mode = parsed_dict.get("mode", "user_driven")
    underlying_id = _resolved_underlying_id(parsed_dict)

    strategy_candidates = []
    backtest_result = _empty_backtest_result()
    scan_result = _empty_scan_result()
    summary = ""

    if mode == "system_scan":
        scan_result = _normalize_scan_result(
            scan_static_opportunities(underlying_id=None)
        )
        # 双保险：如果解析器还带了 underlying_id，就把结果过滤到该标的
        scan_result = _filter_scan_result_by_underlying(scan_result, underlying_id)

        summary = build_summary(
            parsed_intent=parsed_dict,
            strategy_candidates=strategy_candidates,
            scan_result=scan_result,
            backtest_result=backtest_result,
        )
    else:
        scan_result = _normalize_scan_result(
            scan_static_opportunities(underlying_id=underlying_id)
        )
        strategy_candidates = route_strategies(parsed_dict) or []
        backtest_result = pick_and_run_backtest(parsed_dict)

        summary = build_summary(
            parsed_intent=parsed_dict,
            strategy_candidates=strategy_candidates,
            scan_result=scan_result,
            backtest_result=backtest_result,
        )

    try:
        llm_advice = run_advisor_llm(parsed_dict, scan_result, backtest_result)
    except Exception as e:
        print(f"[advisor] llm error: {e}")
        llm_advice = None

    return {
        "mode": mode,
        "parsed_intent": parsed_dict,
        "strategy_candidates": strategy_candidates,
        "scan_result": scan_result,
        "backtest_result": backtest_result,
        "summary": summary,
        "llm_advice": llm_advice,
    }


def _force_mode_if_needed(parsed_intent: dict) -> dict:
    raw_view = (parsed_intent.get("raw_view") or "").strip()
    underlying_specified = parsed_intent.get("underlying_specified", False)

    user_driven_keywords = [
        "帮我看", "看看", "分析", "判断", "有没有", "机会", "推荐", "帮我看看", "帮我分析"
    ]

    if underlying_specified and any(k in raw_view for k in user_driven_keywords):
        parsed_intent["mode"] = "user_driven"

    return parsed_intent


def _resolved_underlying_id(parsed_intent: dict) -> str | None:
    if not parsed_intent.get("underlying_specified"):
        return None

    uid = parsed_intent.get("underlying_id")
    if uid is None:
        return None

    uid = str(uid).strip()
    return uid or None


def _empty_scan_result() -> dict:
    return {
        "factor_rows": 0,
        "opportunity_count": 0,
        "opportunities": [],
    }


def _normalize_scan_result(scan_result: dict | None) -> dict:
    if not isinstance(scan_result, dict):
        return _empty_scan_result()

    return {
        "factor_rows": scan_result.get("factor_rows", 0),
        "opportunity_count": scan_result.get("opportunity_count", 0),
        "opportunities": scan_result.get("opportunities", []) or [],
    }


def _filter_scan_result_by_underlying(scan_result: dict, underlying_id: str | None) -> dict:
    if not underlying_id:
        return scan_result

    opportunities = [
        x for x in scan_result.get("opportunities", [])
        if x.get("underlying_id") == underlying_id
    ]

    return {
        "factor_rows": scan_result.get("factor_rows", 0),
        "opportunity_count": len(opportunities),
        "opportunities": opportunities,
    }


def _empty_backtest_result(reason: str | None = None) -> dict:
    result = {
        "sample_count": 0,
        "hit_ratio": 0.0,
        "avg_value": 0.0,
    }
    if reason:
        result["reason"] = reason
    return result


def pick_and_run_backtest(parsed_intent: dict) -> dict:
    underlying_id = _resolved_underlying_id(parsed_intent)
    if not underlying_id:
        return _empty_backtest_result("underlying_not_specified")

    vol_view = parsed_intent.get("vol_view")

    try:
        if vol_view in ["call_iv_rich", "put_iv_rich"]:
            result = run_simple_backtest(
                strategy_type="calendar_arb",
                underlying_id=underlying_id,
                params={"min_term_slope": 0.02},
            )
        else:
            result = run_simple_backtest(
                strategy_type="parity_arb",
                underlying_id=underlying_id,
                params={"min_parity_deviation": 0.01},
            )
    except Exception as e:
        print(f"[advisor] backtest error: {e}")
        return _empty_backtest_result("backtest_error")

    if not isinstance(result, dict):
        return _empty_backtest_result("invalid_backtest_result")

    return {
        "sample_count": result.get("sample_count", 0),
        "hit_ratio": result.get("hit_ratio", 0.0),
        "avg_value": result.get("avg_value", 0.0),
        **({"raw": result} if any(k not in {"sample_count", "hit_ratio", "avg_value"} for k in result.keys()) else {}),
    }


def build_summary(parsed_intent: dict, strategy_candidates: list, scan_result: dict, backtest_result: dict) -> str:
    underlying = _resolved_underlying_id(parsed_intent)
    vol_view = parsed_intent.get("vol_view")
    defined_risk_only = parsed_intent.get("defined_risk_only", False)
    opp_count = scan_result.get("opportunity_count", 0)
    opportunities = scan_result.get("opportunities", []) or []

    parts = []

    if underlying:
        parts.append(f"当前解析标的为 {underlying}。")
    else:
        parts.append("当前未锁定单一标的，系统按泛化顾问逻辑处理。")

    if vol_view == "call_iv_rich":
        parts.append("系统将你的观点理解为认购隐波相对偏贵。")
    elif vol_view == "put_iv_rich":
        parts.append("系统将你的观点理解为认沽隐波相对偏贵。")
    elif vol_view == "iv_high":
        parts.append("当前波动率偏高，系统优先考虑卖方或价差类策略。")
    elif vol_view == "iv_low":
        parts.append("当前波动率偏低，系统优先考虑买方或跨式类策略。")
    else:
        parts.append("系统暂未识别出明确的波动率判断。")

    if defined_risk_only:
        parts.append("由于你偏好有限风险结构，系统优先考虑价差类和跨期类策略。")

    if strategy_candidates:
        top_names = [x.get("strategy_name", "unknown") for x in strategy_candidates[:3]]
        parts.append(f"当前优先候选策略包括：{', '.join(top_names)}。")

    parts.append(f"本次静态扫描共发现 {opp_count} 个测试机会。")

    if opportunities:
        first = opportunities[0]
        parts.append(
            f"其中排名第一的机会类型为 {first.get('strategy_type', 'unknown')}，"
            f"参考偏离值为 {float(first.get('edge_value', 0.0)):.4f}，"
            f"综合评分为 {float(first.get('score', 0.0)):.4f}。"
        )
    elif underlying:
        parts.append("当前该标的下未发现测试机会。")

    if backtest_result and backtest_result.get("sample_count", 0) > 0:
        parts.append(
            f"历史样本层面，本次对应回测口径下共找到 {backtest_result.get('sample_count', 0)} 个样本，"
            f"正值占比约 {backtest_result.get('hit_ratio', 0.0):.2%}，"
            f"平均指标值约 {backtest_result.get('avg_value', 0.0):.4f}。"
        )
    elif backtest_result.get("reason") == "underlying_not_specified":
        parts.append("由于未明确指定标的，当前未执行定向回测。")

    parts.append("当前结果仍属于简化版顾问输出，尚未纳入真实成交约束、手续费细化、逐笔路径回测和Greeks驱动评分。")

    return "".join(parts)