from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


_PROFIT_CONDITIONS = {
    "call_calendar": "近月 call IV 回落，同时标的维持小幅波动",
    "put_calendar": "近月 put IV 回落，同时标的维持小幅波动",
    "diagonal_call": "近月 call IV 回落，同时标的温和上行",
    "diagonal_put": "近月 put IV 回落，同时标的温和下行",
    "bear_call_spread": "到期时标的低于 short call 行权价",
    "bull_put_spread": "到期时标的高于 short put 行权价",
    "bull_call_spread": "到期时标的高于 long call 行权价",
    "bear_put_spread": "到期时标的低于 long put 行权价",
    "iron_condor": "到期时标的位于两侧 short strike 之间",
    "iron_fly": "到期时标的接近 ATM strike",
    "long_call": "标的上涨并伴随 IV 抬升",
    "long_put": "标的下跌并伴随 IV 抬升",
    "naked_call": "标的不涨或回落，short call 到期归零更有利",
    "naked_put": "标的不跌或上涨，short put 到期归零更有利",
    "covered_call": "标的横盘或温和上涨，备兑 call 收取权利金",
}

_STRONG_RECOMMENDATION_THRESHOLD = 0.80
_REFERENCE_CANDIDATE_THRESHOLD = 0.60
_PER_UNDERLYING_DISPLAY_LIMIT = 5


def _score_tier(score: float) -> str:
    if score >= _STRONG_RECOMMENDATION_THRESHOLD:
        return "strong_recommendation"
    if score >= _REFERENCE_CANDIDATE_THRESHOLD:
        return "reference_candidate"
    return "weak_match_watchlist"


def _score_tier_label(score: float) -> str:
    tier = _score_tier(score)
    if tier == "strong_recommendation":
        return "强推荐"
    if tier == "reference_candidate":
        return "参考候选"
    return "弱匹配/观察"


def _build_presentation_summary(items: List[Any]) -> Dict[str, Any]:
    if not items:
        return {
            "overall_tier": "no_candidate",
            "overall_label": "无候选",
            "top_score": None,
            "note": "当前没有可展示的候选策略。",
        }

    top_score = float(getattr(items[0], "score", 0.0) or 0.0)
    tier = _score_tier(top_score)
    if tier == "strong_recommendation":
        note = ""
        label = "强推荐"
    elif tier == "reference_candidate":
        note = "当前没有特别强的匹配，以下策略更适合作为参考候选，而不是明确主推。"
        label = "参考候选"
    else:
        note = "当前没有强匹配，以下返回策略仅属于弱匹配/观察清单，不宜视为明确推荐。"
        label = "弱匹配/观察"

    return {
        "overall_tier": tier,
        "overall_label": label,
        "top_score": round(top_score, 3),
        "note": note,
    }


def _safe_round(value: Optional[float], digits: int = 3) -> float:
    return round(float(value or 0.0), digits)


def _safe_pct_value(value: Any) -> Optional[float]:
    if value is None or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_display(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.0f}%"


def _extract_iv_percentile_payload(greeks_report: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    iv_report = greeks_report.get("iv_percentile", {}) or {}
    payload = iv_report.get(key)
    if isinstance(payload, dict):
        return payload
    if key == "atm_iv_percentile" and isinstance(iv_report, dict) and "composite_percentile" in iv_report:
        return iv_report
    return None


def _fill_missing_iv_percentiles(
    atm_pct: Optional[float],
    call_pct: Optional[float],
    put_pct: Optional[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if atm_pct is None:
        side_values = [v for v in (call_pct, put_pct) if v is not None]
        if side_values:
            atm_pct = sum(side_values) / len(side_values)
    if call_pct is None:
        call_pct = atm_pct
    if put_pct is None:
        put_pct = atm_pct
    return atm_pct, call_pct, put_pct


def _infer_iv_focus(strategy_type: str) -> str:
    call_focus = {
        "long_call",
        "bull_call_spread",
        "bear_call_spread",
        "call_calendar",
        "diagonal_call",
        "naked_call",
        "covered_call",
    }
    put_focus = {
        "long_put",
        "bear_put_spread",
        "bull_put_spread",
        "put_calendar",
        "diagonal_put",
        "naked_put",
    }
    if strategy_type in call_focus:
        return "call"
    if strategy_type in put_focus:
        return "put"
    return "atm"


def _build_strategy_iv_context(strategy: Any, greeks_report: Dict[str, Any]) -> Dict[str, Any]:
    atm_payload = _extract_iv_percentile_payload(greeks_report, "atm_iv_percentile")
    call_payload = _extract_iv_percentile_payload(greeks_report, "call_iv_percentile")
    put_payload = _extract_iv_percentile_payload(greeks_report, "put_iv_percentile")

    atm_pct = _safe_pct_value((atm_payload or {}).get("composite_percentile"))
    call_pct = _safe_pct_value((call_payload or {}).get("composite_percentile"))
    put_pct = _safe_pct_value((put_payload or {}).get("composite_percentile"))
    atm_pct, call_pct, put_pct = _fill_missing_iv_percentiles(atm_pct, call_pct, put_pct)

    focus = _infer_iv_focus(strategy.strategy_type)
    if focus == "call":
        focus_name = "CALL"
        focus_pct = call_pct if call_pct is not None else atm_pct
        focus_meaning = "上行期权定价强弱"
    elif focus == "put":
        focus_name = "PUT"
        focus_pct = put_pct if put_pct is not None else atm_pct
        focus_meaning = "下行期权定价强弱"
    else:
        focus_name = "ATM"
        focus_pct = atm_pct
        focus_meaning = "整体隐含波动水平"

    skew_text = ""
    if call_pct is not None and put_pct is not None:
        diff = put_pct - call_pct
        if diff >= 0.15:
            skew_text = "PUT 分位明显高于 CALL，说明下行保护需求更强、偏空 skew 更重"
        elif diff <= -0.15:
            skew_text = "CALL 分位明显高于 PUT，说明上行定价更强、偏多投机更活跃"

    focus_text = (
        f"{focus_name} IV 分位 {_pct_display(focus_pct)}，侧重观察{focus_meaning}"
        if focus_pct is not None
        else f"{focus_name} IV 分位信息不足"
    )

    return {
        "atm_iv_pct": atm_pct,
        "call_iv_pct": call_pct,
        "put_iv_pct": put_pct,
        "atm_iv_label": (atm_payload or {}).get("label", "-"),
        "focus_dimension": focus,
        "focus_percentile": focus_pct,
        "focus_text": focus_text,
        "skew_comparison_text": skew_text,
    }


def _format_hv_text(ctx: Dict[str, Any]) -> str:
    hv20 = ctx.get("hv20")
    if hv20 is None:
        return "历史波动率信息不足"
    if hv20 >= 0.30:
        level = "较高"
    elif hv20 <= 0.15:
        level = "较低"
    else:
        level = "中等"
    return f"HV20={float(hv20):.1%}，近期实际波动{level}"


def _format_iv_text(ctx: Dict[str, Any]) -> str:
    atm_iv = ctx.get("atm_iv")
    iv_pct = ctx.get("iv_pct")
    if atm_iv is None and iv_pct is None:
        return "IV 信息不足"
    parts = []
    if atm_iv is not None:
        parts.append(f"ATM IV={float(atm_iv):.1%}")
    if iv_pct is not None:
        parts.append(f"IV 分位={float(iv_pct):.0%}")
    return "，".join(parts)


def _format_term_structure_text(ctx: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    for key, label in (("term_slope_call", "CALL期限斜率"), ("term_slope_put", "PUT期限斜率")):
        value = ctx.get(key)
        if value is None:
            continue
        value = float(value)
        if value >= 0.01:
            texts.append(f"{label}为正，远月 IV 相对更高")
        elif value <= -0.01:
            texts.append(f"{label}为负，近月 IV 相对更高")
        else:
            texts.append(f"{label}较平坦")
    return texts


def _format_skew_text(ctx: Dict[str, Any]) -> str:
    skew = ctx.get("put_call_skew")
    if skew is None:
        return "skew 信息不足"
    skew = float(skew)
    if skew >= 0.03:
        return "put-call skew 偏高，下行保护需求更强"
    if skew <= -0.03:
        return "put-call skew 偏低，上行期权定价相对更强"
    return "put-call skew 接近中性"


def _build_volatility_context(ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = ctx or {}
    return {
        "historical_volatility_text": _format_hv_text(ctx),
        "implied_volatility_text": _format_iv_text(ctx),
        "term_structure_texts": _format_term_structure_text(ctx),
        "skew_text": _format_skew_text(ctx),
    }


def _get_greeks_report(strategy: Any) -> Dict[str, Any]:
    metadata = getattr(strategy, "metadata", {}) or {}
    report = metadata.get("greeks_report", {}) or {}
    return report if isinstance(report, dict) else {}


def _format_leg_value(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _format_strategy_legs(strategy: Any) -> str:
    leg_texts: List[str] = []
    action_label = {"BUY": "买", "SELL": "卖"}

    for leg in getattr(strategy, "legs", []) or []:
        action = action_label.get(str(getattr(leg, "action", "")).upper(), str(getattr(leg, "action", "")))
        option_type = str(getattr(leg, "option_type", "")).upper()
        strike = _format_leg_value(getattr(leg, "strike", None))
        expiry = _format_leg_value(getattr(leg, "expiry_date", None))
        delta = _format_leg_value(getattr(leg, "delta", None), digits=3)
        mid = _format_leg_value(getattr(leg, "mid", None), digits=4)
        leg_texts.append(f"{action}{option_type} K={strike} {expiry} Δ={delta} mid={mid}")

    return " / ".join(leg_texts)


def _format_execution_guidance(strategy: Any) -> str:
    guidance = getattr(strategy, "execution_guidance", None) or {}
    if not isinstance(guidance, dict):
        return "-"
    pricing_type = guidance.get("pricing_type")
    good = guidance.get("good_limit")
    acceptable = guidance.get("acceptable_limit")
    chase = guidance.get("do_not_chase_beyond")
    quality = guidance.get("spread_quality")
    if good is None or acceptable is None or chase is None:
        return "-"
    if pricing_type == "credit":
        return f"收权利金 good>={good:.4f}, acceptable>={acceptable:.4f}, no chase<{chase:.4f}, {quality}"
    return f"付权利金 good<={good:.4f}, acceptable<={acceptable:.4f}, no chase>{chase:.4f}, {quality}"


def _strategy_to_table_row(
    strategy: Any,
    rank: int,
    group_rank: int,
    within_underlying_rank: int,
) -> Dict[str, Any]:
    greeks_report = _get_greeks_report(strategy)
    greeks = greeks_report.get("net_greeks", {}) or {}
    iv_ctx = _build_strategy_iv_context(strategy, greeks_report)
    score = float(getattr(strategy, "score", 0.0) or 0.0)
    legs = getattr(strategy, "legs", []) or []

    return {
        "rank": rank,
        "group_rank": group_rank,
        "within_underlying_rank": within_underlying_rank,
        "underlying": strategy.underlying_id,
        "strategy": strategy.strategy_type,
        "score": round(score, 3),
        "score_tier": _score_tier(score),
        "score_tier_label": _score_tier_label(score),
        "cost": (
            f"收权利金 {strategy.net_credit:.4f}"
            if getattr(strategy, "net_credit", 0.0)
            else f"付权利金 {getattr(strategy, 'net_debit', 0.0):.4f}"
        ),
        "legs": _format_strategy_legs(strategy),
        "leg_count": len(legs),
        "execution": _format_execution_guidance(strategy),
        "net_delta": _safe_round(greeks.get("net_delta")),
        "net_vega": _safe_round(greeks.get("net_vega")),
        "net_theta": _safe_round(greeks.get("net_theta")),
        "iv_label": iv_ctx["atm_iv_label"],
        "iv_pct": iv_ctx["atm_iv_pct"],
        "atm_iv_pct": iv_ctx["atm_iv_pct"],
        "call_iv_pct": iv_ctx["call_iv_pct"],
        "put_iv_pct": iv_ctx["put_iv_pct"],
        "iv_triplet_display": (
            f"{_pct_display(iv_ctx['atm_iv_pct'])} / "
            f"{_pct_display(iv_ctx['call_iv_pct'])} / "
            f"{_pct_display(iv_ctx['put_iv_pct'])}"
        ),
        "iv_focus_dimension": iv_ctx["focus_dimension"],
        "iv_focus_percentile": iv_ctx["focus_percentile"],
        "iv_focus_text": iv_ctx["focus_text"],
        "iv_skew_comparison_text": iv_ctx["skew_comparison_text"],
        "risk_flags": greeks_report.get("risk_flags", []),
        "profit_condition": _PROFIT_CONDITIONS.get(strategy.strategy_type, "-"),
    }


def _group_ranked_by_underlying(
    ranked: List[Any],
    per_underlying_limit: int = _PER_UNDERLYING_DISPLAY_LIMIT,
) -> List[Dict[str, Any]]:
    raw_groups: Dict[str, List[Any]] = defaultdict(list)
    for strategy in ranked:
        raw_groups[str(strategy.underlying_id)].append(strategy)

    group_stats: List[Dict[str, Any]] = []
    for underlying_id, items in raw_groups.items():
        sorted_items = sorted(items, key=lambda item: float(getattr(item, "score", 0.0) or 0.0), reverse=True)
        top_score = float(getattr(sorted_items[0], "score", 0.0) or 0.0) if sorted_items else 0.0
        group_stats.append(
            {
                "underlying_id": underlying_id,
                "all_items": sorted_items,
                "display_items": sorted_items[:per_underlying_limit],
                "candidate_count": len(sorted_items),
                "top_score": round(top_score, 3),
                "strong_recommendation_count": sum(
                    1 for item in sorted_items if float(getattr(item, "score", 0.0) or 0.0) >= _STRONG_RECOMMENDATION_THRESHOLD
                ),
            }
        )

    group_stats.sort(
        key=lambda group: (
            -group["top_score"],
            -group["strong_recommendation_count"],
            -group["candidate_count"],
            group["underlying_id"],
        )
    )

    for index, group in enumerate(group_stats, start=1):
        group["group_rank"] = index
    return group_stats


def _build_market_overview(
    grouped: List[Dict[str, Any]],
    market_context: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    market_context = market_context or {}
    overview: List[Dict[str, Any]] = []
    grouped_by_uid = {group["underlying_id"]: group for group in grouped}
    ordered_ids = [group["underlying_id"] for group in grouped]
    for uid in sorted(market_context.keys()):
        if uid not in grouped_by_uid:
            ordered_ids.append(uid)

    for index, uid in enumerate(dict.fromkeys(ordered_ids), start=1):
        group = grouped_by_uid.get(uid)
        ctx = market_context.get(uid, {}) or {}
        overview.append(
            {
                "group_rank": group.get("group_rank") if group else index,
                "underlying_id": uid,
                "top_score": group.get("top_score") if group else None,
                "candidate_count": group.get("candidate_count") if group else 0,
                "strong_recommendation_count": group.get("strong_recommendation_count") if group else 0,
                "top_strategy_type": (
                    group["all_items"][0].strategy_type
                    if group and group.get("all_items")
                    else None
                ),
                "market_summary": ctx.get("summary"),
                "volatility_context": _build_volatility_context(ctx),
            }
        )
    return overview


def _build_recommendation_groups(grouped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    recommendation_groups: List[Dict[str, Any]] = []
    for group in grouped:
        items: List[Dict[str, Any]] = []
        for within_rank, strategy in enumerate(group["display_items"], start=1):
            row = _strategy_to_table_row(
                strategy,
                rank=0,
                group_rank=group["group_rank"],
                within_underlying_rank=within_rank,
            )
            row["rank"] = within_rank
            items.append(row)

        omitted_count = max(0, group["candidate_count"] - len(items))
        recommendation_groups.append(
            {
                "group_rank": group["group_rank"],
                "underlying_id": group["underlying_id"],
                "top_score": group["top_score"],
                "candidate_count": group["candidate_count"],
                "strong_recommendation_count": group["strong_recommendation_count"],
                "presentation": _build_presentation_summary(group["display_items"]),
                "items": items,
                "omitted_count": omitted_count,
                "omitted_note": f"另有 {omitted_count} 个候选未展开。" if omitted_count else "",
            }
        )
    return recommendation_groups


def _build_cross_underlying_summary(grouped: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not grouped:
        return {"best_underlying_id": None, "comparison_text": "当前没有足够候选用于跨标的比较。"}

    best = grouped[0]
    if len(grouped) == 1:
        return {
            "best_underlying_id": best["underlying_id"],
            "comparison_text": f"当前仅有 {best['underlying_id']} 形成有效候选，优先参考该标的组内排序。",
        }

    second = grouped[1]
    gap = round(float(best["top_score"] or 0.0) - float(second["top_score"] or 0.0), 3)
    return {
        "best_underlying_id": best["underlying_id"],
        "comparison_text": (
            f"{best['underlying_id']} 当前 top_score={best['top_score']}，"
            f"领先 {second['underlying_id']} 约 {gap:.3f}，可作为优先关注标的。"
        ),
    }


def _flatten_grouped_rows(grouped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flat_rows: List[Dict[str, Any]] = []
    rank = 1
    for group in grouped:
        for within_rank, strategy in enumerate(group["display_items"], start=1):
            flat_rows.append(
                _strategy_to_table_row(
                    strategy,
                    rank=rank,
                    group_rank=group["group_rank"],
                    within_underlying_rank=within_rank,
                )
            )
            rank += 1
    return flat_rows


def _decision_items(decision_payload: Optional[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    if not decision_payload:
        return []
    value = decision_payload.get(key, []) or []
    return value if isinstance(value, list) else []


def _find_table_row(table: List[Dict[str, Any]], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    uid = str(item.get("underlying_id") or "")
    strategy_type = str(item.get("strategy_type") or "")
    for row in table:
        if str(row.get("underlying")) == uid and str(row.get("strategy")) == strategy_type:
            return row
    return None


def _format_decision_item(item: Dict[str, Any], row: Optional[Dict[str, Any]] = None) -> str:
    uid = item.get("underlying_id") or (row or {}).get("underlying") or "-"
    strategy_type = item.get("strategy_type") or (row or {}).get("strategy") or "-"
    score = item.get("score")
    if score is None and row:
        score = row.get("score")
    family = item.get("family")
    score_text = f"，score={float(score):.3f}" if score is not None else ""
    family_text = f"，family={family}" if family else ""
    return f"{uid} 的 {strategy_type}{score_text}{family_text}"


def _risk_sentence(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return "需要继续关注成交质量、波动率变化和到期前价格路径。"
    flags = row.get("risk_flags") or []
    theta = row.get("net_theta")
    delta = row.get("net_delta")
    if flags:
        return f"风险上需关注 {', '.join(str(flag) for flag in flags[:3])}。"
    if theta is not None and float(theta) < 0:
        return "该结构 theta 为负，若标的未按预期移动，时间流逝会形成拖累。"
    return f"主要监控 delta={delta}、theta={theta} 以及成交价差变化。"


def _build_deterministic_narrative(
    table: List[Dict[str, Any]],
    market_overview: List[Dict[str, Any]],
    recommendation_groups: List[Dict[str, Any]],
    cross_underlying_summary: Dict[str, Any],
    presentation: Dict[str, Any],
    decision_payload: Optional[Dict[str, Any]],
) -> str:
    if not table and not recommendation_groups:
        return "当前没有可展示的候选策略，可先结合市场条件等待更清晰的结构机会。"

    primary = _decision_items(decision_payload, "primary_recommendations")
    secondary = _decision_items(decision_payload, "secondary_recommendations")
    preferred_uid = (decision_payload or {}).get("preferred_underlying_id") or cross_underlying_summary.get("best_underlying_id")
    decision_notes = (decision_payload or {}).get("decision_notes", {}) or {}
    gap = decision_notes.get("cross_underlying_gap")

    primary_item = primary[0] if primary else {}
    primary_row = _find_table_row(table, primary_item) if primary_item else (table[0] if table else None)
    if not primary_item and primary_row:
        primary_item = {
            "underlying_id": primary_row.get("underlying"),
            "strategy_type": primary_row.get("strategy"),
            "score": primary_row.get("score"),
            "decision_reason": "table_top_rank",
        }

    market_ids = [item.get("underlying_id") for item in market_overview if item.get("underlying_id")]
    multi_underlying = len(set(market_ids)) > 1 or len(recommendation_groups) > 1
    top_summary = _format_decision_item(primary_item, primary_row)
    reason = primary_item.get("decision_reason") or "当前排序和执行质量综合领先"
    cost = primary_row.get("cost") if primary_row else "-"
    iv_focus = primary_row.get("iv_focus_text") if primary_row else ""

    if multi_underlying:
        gap_text = f"，领先幅度约 {float(gap):.3f}" if gap is not None else ""
        market_view = (
            f"本次覆盖 {', '.join(str(uid) for uid in market_ids)}；"
            f"当前 preferred_underlying_id 为 {preferred_uid}{gap_text}。"
        )
    else:
        only_uid = preferred_uid or (market_ids[0] if market_ids else (primary_item.get("underlying_id") or "-"))
        market_view = f"本次主要评估 {only_uid}；当前候选中 {top_summary} 排在最前。"

    primary_text = (
        f"首选策略是 {top_summary}，成本结构为 {cost}。"
        f"其成为当前焦点的原因是 {reason}；{iv_focus or '波动率维度以表格中的 ATM/CALL/PUT 分位为准'}。"
    )

    if secondary:
        formatted_secondary = []
        for item in secondary[:3]:
            formatted_secondary.append(_format_decision_item(item, _find_table_row(table, item)))
        alt_text = "备选可关注 " + "；".join(formatted_secondary) + "。这些候选适合作为对照或次优选择，不应直接覆盖首选策略。"
    else:
        alt_text = "当前没有更强的 secondary recommendation，主要关注首选这一组即可。"

    risk_text = _risk_sentence(primary_row)
    monitor_text = (
        "后续重点观察标的是否接近关键行权价、IV 分位是否明显跳变、以及买卖价差是否恶化；"
        "若这些条件转弱，应降低执行优先级。"
    )

    sections = [
        f"一、市场判断：{market_view}",
        f"二、首选策略：{primary_text}",
        f"三、备选对比：{alt_text}",
        f"四、风险提示：{risk_text}",
        f"五、后续关注条件：{monitor_text}",
    ]
    if presentation.get("note"):
        sections.insert(0, presentation["note"])
    return "\n".join(sections)


def _count_narrative_sections(text: str) -> int:
    if not text:
        return 0
    return sum(1 for marker in ("一、", "二、", "三、", "四、", "五、") if marker in text)


def build_briefing(
    ranked: List[Any],
    intent_text: str,
    top_n: int = 5,
    market_context: Optional[Dict[str, Any]] = None,
    decision_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build grouped briefing payload and a deterministic narrative."""
    top = ranked[:top_n]
    presentation = _build_presentation_summary(top)

    grouped = _group_ranked_by_underlying(
        ranked,
        per_underlying_limit=_PER_UNDERLYING_DISPLAY_LIMIT,
    )
    recommendation_groups = _build_recommendation_groups(grouped)
    market_overview = _build_market_overview(grouped, market_context=market_context)
    cross_underlying_summary = _build_cross_underlying_summary(grouped)
    table = _flatten_grouped_rows(grouped)

    market_context_ids = sorted((market_context or {}).keys())
    grouped_ids = [group.get("underlying_id") for group in recommendation_groups if group.get("underlying_id")]
    print(f"[briefing_narrative_check] market_context_ids={market_context_ids}")
    print(f"[briefing_narrative_check] grouped_ids={grouped_ids}")

    narrative = _build_deterministic_narrative(
        table=table,
        market_overview=market_overview,
        recommendation_groups=recommendation_groups,
        cross_underlying_summary=cross_underlying_summary,
        presentation=presentation,
        decision_payload=decision_payload,
    )
    narrative_tail = (narrative or "")[-80:]
    section_count = _count_narrative_sections(narrative)
    print(f"[briefing_text_check] narrative_len={len(narrative or '')}")
    print(f"[briefing_text_check] section_count={section_count}")
    print(f"[briefing_text_check] narrative_tail={narrative_tail}")

    briefing = {
        "table": table,
        "narrative": narrative,
        "presentation": presentation,
        "market_overview": market_overview,
        "recommendation_groups": recommendation_groups,
        "cross_underlying_summary": cross_underlying_summary,
    }

    try:
        keys = list(briefing.keys())
        market_ids = [item.get("underlying_id") for item in market_overview if item.get("underlying_id")]
        group_ids = [group.get("underlying_id") for group in recommendation_groups if group.get("underlying_id")]
        cross_best = (cross_underlying_summary or {}).get("best_underlying_id")

        print(f"[briefing_check] keys={','.join(keys)}")
        print(f"[briefing_check] market_overview count={len(market_overview)} ids={market_ids}")
        print(f"[briefing_check] recommendation_groups count={len(recommendation_groups)} ids={group_ids}")

        for group in recommendation_groups:
            print(
                "[briefing_check] "
                f"group rank={group.get('group_rank')} "
                f"uid={group.get('underlying_id')} "
                f"top_score={group.get('top_score')} "
                f"items={len(group.get('items', []))}"
            )

        print(f"[briefing_check] cross best_underlying_id={cross_best}")

        for row in table[:6]:
            print(
                "[briefing_check] "
                f"table row rank={row.get('rank')} "
                f"group_rank={row.get('group_rank')} "
                f"within={row.get('within_underlying_rank')} "
                f"uid={row.get('underlying')} "
                f"strategy={row.get('strategy')} "
                f"score={row.get('score')}"
            )
    except Exception as exc:
        print(f"[briefing_check] logging_failed: {exc}")

    return briefing
