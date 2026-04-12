from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Dict, List, Optional

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_client

_client = get_anthropic_client()


_PROFIT_CONDITIONS = {
    "call_calendar": "近月 call IV 回落，同时标的维持小幅波动",
    "put_calendar": "近月 put IV 回落，同时标的维持小幅波动",
    "diagonal_call": "近月 call IV 回落，同时标的温和上涨",
    "diagonal_put": "近月 put IV 回落，同时标的温和下跌",
    "bear_call_spread": "到期时标的低于 short call 行权价",
    "bull_put_spread": "到期时标的高于 short put 行权价",
    "bull_call_spread": "到期时标的高于 long call 行权价",
    "bear_put_spread": "到期时标的低于 long put 行权价",
    "iron_condor": "到期时标的位于两侧 short strike 之间",
    "iron_fly": "到期时标的接近 ATM strike",
    "long_call": "标的上涨且 IV 抬升",
    "long_put": "标的下跌且 IV 抬升",
    "naked_call": "标的不涨或回落，short call 到期归零更有利",
    "naked_put": "标的不跌或上涨，short put 到期归零更有利",
    "covered_call": "标的横盘或温和上涨，备兑 call 收取权利金",
}

_STRONG_RECOMMENDATION_THRESHOLD = 0.80
_REFERENCE_CANDIDATE_THRESHOLD = 0.60
_PER_UNDERLYING_DISPLAY_LIMIT = 5
_INCOMPLETE_ENDINGS = ("(", "（", ":", "：", "、", "-", "—", "，", ",", "/", "…")


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

    top_score = float(items[0].score or 0.0)
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


def _finalize_narrative_text(text: str) -> str:
    narrative = (text or "").strip()
    if not narrative:
        return narrative

    narrative = re.sub(r"\n{3,}", "\n\n", narrative).strip()
    lines = narrative.splitlines()
    if not lines:
        return narrative

    last_line = lines[-1].rstrip()
    trailing_numbered_bullet = bool(re.search(r"(?:^|\s)(?:\d+|[一二三四五])[\.\、:]?\s*$", last_line))
    ends_incomplete = last_line.endswith(_INCOMPLETE_ENDINGS)

    if trailing_numbered_bullet:
        lines = lines[:-1]
    elif ends_incomplete:
        sentence_endings = [last_line.rfind(mark) for mark in ("。", "！", "？", ".", "!", "?", "；", ";")]
        last_complete_idx = max(sentence_endings)
        if last_complete_idx >= 0:
            trimmed_line = last_line[: last_complete_idx + 1].rstrip()
            lines[-1] = trimmed_line
        else:
            trimmed_line = re.sub(r"[\(\)（）:：、,\-/—…\s]+$", "", last_line).strip()
            if trimmed_line:
                if trimmed_line[-1] not in "。！？.!?；;":
                    trimmed_line = f"{trimmed_line}。"
                lines[-1] = trimmed_line
            else:
                lines = lines[:-1]

    narrative = "\n".join(line for line in lines if line.strip()).strip()
    if narrative and narrative[-1] not in "。！？.!?；;":
        narrative = f"{narrative} 其余细节可结合表格进一步判断。"

    return narrative.strip()


def _count_narrative_sections(text: str) -> int:
    if not text:
        return 0
    patterns = [
        r"(^|\n)一[、.]",
        r"(^|\n)二[、.]",
        r"(^|\n)三[、.]",
        r"(^|\n)四[、.]",
        r"(^|\n)五[、.]",
        r"(^|\n)1\.",
        r"(^|\n)2\.",
        r"(^|\n)3\.",
        r"(^|\n)4\.",
        r"(^|\n)5\.",
    ]
    count = 0
    for pattern in patterns:
        if re.search(pattern, text):
            count += 1
    return min(count, 5)


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
    parts: List[str] = []

    if atm_iv is not None:
        parts.append(f"ATM IV={float(atm_iv):.1%}")

    if iv_pct is not None:
        iv_pct_value = float(iv_pct)
        if iv_pct_value >= 0.80:
            level = "隐含波动分位较高"
        elif iv_pct_value <= 0.20:
            level = "隐含波动分位较低"
        else:
            level = "隐含波动分位中性"
        parts.append(f"IV分位={iv_pct_value:.0%}（{level}）")

    if not parts:
        return "IV 信息不足"
    return "；".join(parts)


def _format_term_structure_text(ctx: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    def describe(label: str, slope: Optional[float]) -> Optional[str]:
        if slope is None or abs(float(slope)) < 0.01:
            return None
        slope_value = float(slope)
        if slope_value > 0:
            return f"{label} 期限结构前高后低：近月 IV 高于远月 {slope_value:.3f}"
        return f"{label} 期限结构后高前低：远月 IV 高于近月 {abs(slope_value):.3f}"

    call_text = describe("call", ctx.get("term_slope_call"))
    put_text = describe("put", ctx.get("term_slope_put"))
    if call_text:
        lines.append(call_text)
    if put_text:
        lines.append(put_text)
    return lines


def _format_skew_text(ctx: Dict[str, Any]) -> str:
    skew = ctx.get("put_call_skew")
    if skew is None:
        return "skew 信息不足"
    skew_value = float(skew)
    if skew_value > 0.005:
        desc = "put 端 IV 高于 call 端"
    elif skew_value < -0.005:
        desc = "call 端 IV 高于 put 端"
    else:
        desc = "put/call 两端 IV 大致均衡"
    return f"put-call skew={skew_value:.3f}，{desc}"


def _build_volatility_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "historical_volatility_text": _format_hv_text(ctx),
        "implied_volatility_text": _format_iv_text(ctx),
        "term_structure_texts": _format_term_structure_text(ctx),
        "skew_text": _format_skew_text(ctx),
    }


def _strategy_to_table_row(
    strategy: Any,
    rank: int,
    group_rank: int,
    within_underlying_rank: int,
) -> Dict[str, Any]:
    metadata = strategy.metadata or {}
    greeks_report = metadata.get("greeks_report", {}) or {}
    iv_pct = greeks_report.get("iv_percentile", {}) or {}
    net_greeks = greeks_report.get("net_greeks", {}) or {}
    strategy_iv_context = _build_strategy_iv_context(strategy, greeks_report)

    if strategy.net_debit is not None:
        cost_str = f"付权利金 {strategy.net_debit:.4f}"
    elif strategy.net_credit is not None:
        cost_str = f"收权利金 {strategy.net_credit:.4f}"
    else:
        cost_str = "-"

    legs_str = " / ".join(
        f"{'卖' if leg.action == 'SELL' else '买'}"
        f"{leg.option_type} K={leg.strike} {leg.expiry_date} "
        f"Δ={round(leg.delta, 2) if leg.delta is not None else '-'} mid={leg.mid}"
        for leg in strategy.legs
    )

    return {
        "rank": rank,
        "group_rank": group_rank,
        "within_underlying_rank": within_underlying_rank,
        "underlying": strategy.underlying_id,
        "strategy": strategy.strategy_type,
        "score": round(float(strategy.score or 0.0), 3),
        "score_tier": _score_tier(float(strategy.score or 0.0)),
        "score_tier_label": _score_tier_label(float(strategy.score or 0.0)),
        "cost": cost_str,
        "legs": legs_str,
        "net_delta": _safe_round(net_greeks.get("net_delta"), 3),
        "net_vega": _safe_round(net_greeks.get("net_vega"), 4),
        "net_theta": _safe_round(net_greeks.get("net_theta"), 4),
        "iv_label": iv_pct.get("label", strategy_iv_context.get("atm_iv_label", "-")),
        "iv_pct": iv_pct.get("composite_percentile", strategy_iv_context.get("atm_iv_pct", "-")),
        "atm_iv_pct": strategy_iv_context.get("atm_iv_pct"),
        "call_iv_pct": strategy_iv_context.get("call_iv_pct"),
        "put_iv_pct": strategy_iv_context.get("put_iv_pct"),
        "iv_triplet_display": " / ".join(
            [
                _pct_display(strategy_iv_context.get("atm_iv_pct")),
                _pct_display(strategy_iv_context.get("call_iv_pct")),
                _pct_display(strategy_iv_context.get("put_iv_pct")),
            ]
        ),
        "iv_focus_dimension": strategy_iv_context.get("focus_dimension"),
        "iv_focus_percentile": strategy_iv_context.get("focus_percentile"),
        "iv_focus_text": strategy_iv_context.get("focus_text"),
        "iv_skew_comparison_text": strategy_iv_context.get("skew_comparison_text"),
        "risk_flags": greeks_report.get("risk_flags", []),
        "profit_condition": _PROFIT_CONDITIONS.get(strategy.strategy_type, "-"),
    }


def _group_ranked_by_underlying(
    ranked: List[Any],
    per_underlying_limit: int = _PER_UNDERLYING_DISPLAY_LIMIT,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for strategy in ranked:
        grouped[strategy.underlying_id].append(strategy)

    groups: List[Dict[str, Any]] = []
    for underlying_id, items in grouped.items():
        sorted_items = sorted(items, key=lambda strategy: float(strategy.score or 0.0), reverse=True)
        kept_items = sorted_items[:per_underlying_limit]
        omitted_count = max(0, len(sorted_items) - len(kept_items))
        strong_recommendation_count = sum(
            1
            for strategy in kept_items
            if _score_tier(float(strategy.score or 0.0)) == "strong_recommendation"
        )
        groups.append(
            {
                "underlying_id": underlying_id,
                "items": kept_items,
                "candidate_count": len(sorted_items),
                "display_count": len(kept_items),
                "omitted_count": omitted_count,
                "omitted_note": f"另有 {omitted_count} 个候选未展开" if omitted_count > 0 else "",
                "top_score": round(float(kept_items[0].score), 3) if kept_items else None,
                "strong_recommendation_count": strong_recommendation_count,
                "presentation": _build_presentation_summary(kept_items),
            }
        )

    groups.sort(
        key=lambda group: (
            -(group["top_score"] if group["top_score"] is not None else -1.0),
            -group["strong_recommendation_count"],
            -group["candidate_count"],
            group["underlying_id"],
        )
    )
    return groups


def _build_market_overview(
    recommendation_groups: List[Dict[str, Any]],
    market_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    market_context = market_context or {}
    overview: List[Dict[str, Any]] = []
    grouped_ids: List[str] = []

    for group_index, group in enumerate(recommendation_groups, 1):
        underlying_id = group["underlying_id"]
        grouped_ids.append(underlying_id)
        ctx = market_context.get(underlying_id, {}) or {}
        top_strategy = group["items"][0] if group["items"] else None
        overview.append(
            {
                "group_rank": group_index,
                "underlying_id": underlying_id,
                "top_score": group["top_score"],
                "strong_recommendation_count": group["strong_recommendation_count"],
                "candidate_count": group["candidate_count"],
                "top_strategy_type": top_strategy.strategy_type if top_strategy is not None else None,
                "presentation": group["presentation"],
                "market_summary": ctx.get("summary", ""),
                "volatility_context": _build_volatility_context(ctx),
            }
        )

    extra_context_ids = sorted(uid for uid in market_context.keys() if uid not in grouped_ids)
    for offset, underlying_id in enumerate(extra_context_ids, len(overview) + 1):
        ctx = market_context.get(underlying_id, {}) or {}
        overview.append(
            {
                "group_rank": offset,
                "underlying_id": underlying_id,
                "top_score": None,
                "strong_recommendation_count": 0,
                "candidate_count": 0,
                "top_strategy_type": None,
                "presentation": _build_presentation_summary([]),
                "market_summary": ctx.get("summary", ""),
                "volatility_context": _build_volatility_context(ctx),
            }
        )

    return overview


def _build_recommendation_groups(recommendation_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []

    for group_index, group in enumerate(recommendation_groups, 1):
        items = sorted(group["items"], key=lambda strategy: float(strategy.score or 0.0), reverse=True)
        output.append(
            {
                "group_rank": group_index,
                "underlying_id": group["underlying_id"],
                "top_score": group["top_score"],
                "strong_recommendation_count": group["strong_recommendation_count"],
                "candidate_count": group["candidate_count"],
                "display_count": group.get("display_count", len(items)),
                "omitted_count": group.get("omitted_count", 0),
                "omitted_note": group.get("omitted_note", ""),
                "presentation": group["presentation"],
                "items": [
                    _strategy_to_table_row(
                        strategy=item,
                        rank=item_index,
                        group_rank=group_index,
                        within_underlying_rank=item_index,
                    )
                    for item_index, item in enumerate(items, 1)
                ],
            }
        )

    return output


def _build_cross_underlying_summary(recommendation_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not recommendation_groups:
        return {
            "best_underlying_id": None,
            "comparison_text": "当前没有可比较的跨标的推荐。",
        }

    best_group = recommendation_groups[0]
    best_underlying_id = best_group["underlying_id"]
    best_top = best_group["items"][0] if best_group["items"] else None
    if best_top is not None and best_group["top_score"] is not None:
        best_desc = (
            f"{best_underlying_id} 当前优先级最高，首选为 {best_top.strategy_type}，"
            f"评分 {best_group['top_score']:.3f}。"
        )
    else:
        best_desc = f"{best_underlying_id} 当前是优先级最高的标的。"

    if len(recommendation_groups) == 1:
        comparison_text = best_desc
    else:
        second_group = recommendation_groups[1]
        second_top = second_group["items"][0] if second_group["items"] else None
        if (
            second_top is not None
            and second_group["top_score"] is not None
            and best_group["top_score"] is not None
        ):
            gap = round(best_group["top_score"] - second_group["top_score"], 3)
            comparison_text = (
                f"{best_desc} 相比之下，{second_group['underlying_id']} 的首选为 "
                f"{second_top.strategy_type}，评分 {second_group['top_score']:.3f}，"
                f"与第一组相差 {gap:.3f}。"
            )
        else:
            comparison_text = best_desc

    return {
        "best_underlying_id": best_underlying_id,
        "comparison_text": comparison_text,
    }


def _flatten_grouped_rows(recommendation_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flat_rows: List[Dict[str, Any]] = []
    rank = 1
    for group_index, group in enumerate(recommendation_groups, 1):
        sorted_items = sorted(group["items"], key=lambda strategy: float(strategy.score or 0.0), reverse=True)
        for within_underlying_rank, strategy in enumerate(sorted_items, 1):
            flat_rows.append(
                _strategy_to_table_row(
                    strategy=strategy,
                    rank=rank,
                    group_rank=group_index,
                    within_underlying_rank=within_underlying_rank,
                )
            )
            rank += 1
    return flat_rows


def _build_grouped_narrative_context(
    intent_text: str,
    market_overview: List[Dict[str, Any]],
    recommendation_groups: List[Dict[str, Any]],
    cross_underlying_summary: Dict[str, Any],
    flat_table: List[Dict[str, Any]],
    presentation: Dict[str, Any],
) -> str:
    market_context_ids = [item["underlying_id"] for item in market_overview if item.get("underlying_id")]
    recommendation_group_ids = [group["underlying_id"] for group in recommendation_groups if group.get("underlying_id")]
    recommendation_group_id_set = set(recommendation_group_ids)
    context_only_ids = [uid for uid in market_context_ids if uid not in recommendation_group_id_set]

    ctx_lines = [f"用户输入：{intent_text}"]
    ctx_lines.append(f"market context underlyings: {market_context_ids}")
    ctx_lines.append(f"recommendation group underlyings: {recommendation_group_ids}")
    ctx_lines.append(f"context-only underlyings: {context_only_ids}")

    if market_overview:
        ctx_lines.append("\n市场概览：")
        for item in market_overview:
            coverage = "recommended" if item["underlying_id"] in recommendation_group_id_set else "context_only"
            vol_ctx = item.get("volatility_context", {}) or {}
            ctx_lines.append(
                f"- {item['group_rank']}. {item['underlying_id']} "
                f"coverage={coverage} top_score={item['top_score']} "
                f"candidate_count={item['candidate_count']} top_strategy={item.get('top_strategy_type') or '-'}"
            )
            ctx_lines.append(f"  历史波动/Realized：{vol_ctx.get('historical_volatility_text', '历史波动率信息不足')}")
            ctx_lines.append(f"  隐含波动/IV：{vol_ctx.get('implied_volatility_text', 'IV 信息不足')}")
            term_texts = vol_ctx.get("term_structure_texts") or []
            if term_texts:
                ctx_lines.append(f"  期限结构/Term：{'；'.join(term_texts)}")
            else:
                ctx_lines.append("  期限结构/Term：未见强期限结构信号")
            ctx_lines.append(f"  偏斜/Skew：{vol_ctx.get('skew_text', 'skew 信息不足')}")
            if item.get("market_summary"):
                ctx_lines.append(
                    f"  补充市场摘要（若与结构化波动率信息冲突，以结构化信息为准）：{item['market_summary']}"
                )

    if recommendation_groups:
        ctx_lines.append("\n分组推荐：")
        for group in recommendation_groups:
            ctx_lines.append(
                f"- 标的 {group['underlying_id']}（组内 top={group['top_score']}，"
                f"层级={group['presentation']['overall_label']}）"
            )
            if group.get("omitted_note"):
                ctx_lines.append(f"  {group['omitted_note']}")
            for item in group["items"]:
                ctx_lines.append(
                    f"  {item['within_underlying_rank']}. {item['strategy']} "
                    f"score={item['score']} {item['cost']} "
                    f"IV(ATM/C/P)={item['iv_triplet_display']} "
                    f"focus={item.get('iv_focus_text', '-')} "
                    f"delta={item['net_delta']} vega={item['net_vega']} theta={item['net_theta']} "
                    f"flags={item['risk_flags']}"
                )
                if item.get("iv_skew_comparison_text"):
                    ctx_lines.append(f"    skew_hint={item['iv_skew_comparison_text']}")

    if cross_underlying_summary.get("comparison_text"):
        ctx_lines.append(f"\n跨标的比较：{cross_underlying_summary['comparison_text']}")

    if presentation["note"]:
        ctx_lines.append(f"\n呈现层级：{presentation['note']}")

    if flat_table:
        ctx_lines.append("\n扁平排序参考：")
        for row in flat_table:
            ctx_lines.append(
                f"{row['rank']}. group={row['group_rank']} {row['underlying']} {row['strategy']} "
                f"score={row['score']} {row['cost']}"
            )

    return "\n".join(ctx_lines)


def build_briefing(
    ranked: List[Any],
    intent_text: str,
    top_n: int = 5,
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    生成推荐简报：保留旧字段，同时补充按标的分组的推荐结构。
    market_context: {uid: ctx_dict}
    """
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

    ctx = _build_grouped_narrative_context(
        intent_text=intent_text,
        market_overview=market_overview,
        recommendation_groups=recommendation_groups,
        cross_underlying_summary=cross_underlying_summary,
        flat_table=table,
        presentation=presentation,
    )

    system = """
你是一个 A 股 ETF 期权交易助手，面向有经验的交易者。请用简洁、专业、克制的中文输出结构化简报，优先使用以下 5 个小节标题：
一、市场判断
二、首选策略
三、备选对比
四、风险提示
五、后续关注条件

写作要求：
1. 若信息足够，按上述 5 个小节输出；每个小节 1-3 句。
2. 若某一节信息不足，也尽量保留标题并用一句简洁说明，不要省略成单段空泛摘要。
3. 必须明确说明：
   - 为什么首选策略当前更优
   - 至少一个备选为什么更弱或更次优
   - 至少一个具体风险或监控触发条件
4. 内容可以比之前更完整，但不要写成长篇大论；以 280-420 字左右为宜。
5. 不要输出开放式枚举，不要写未完成的编号、括号或半句话。
6. 每一节都必须用完整句子结束，最后一节也必须自然收束。

波动率表述规则：
- 历史波动率 / HV / realized volatility 只用于描述近期实际波动大小，例如“历史波动率较高/较低”“近期实际波动更大/更小”
- 不得把 HV 写成“贵/便宜”或“估值高/低”
- “贵/便宜”只可用于 IV 或明确的 IV 结构定价语境
- ATM IV percentile 代表整体隐含波动水平
- CALL IV percentile 代表上行期权定价是否偏强
- PUT IV percentile 代表下行期权定价是否偏强
- 对中性结构优先参考 ATM IV percentile
- 对 call 侧策略优先参考 CALL IV percentile
- 对 put 侧策略优先参考 PUT IV percentile
- 若 PUT IV percentile 明显高于 CALL IV percentile，可简要提示下行保护需求更强或 bearish skew
- 若 CALL IV percentile 明显高于 PUT IV percentile，可简要提示上行定价更强或 bullish speculation
- 期限结构只在 term_slope_call / term_slope_put 或明确 IV 结构证据支持时再下结论
- skew 要单独描述，不要与 HV 混写

多标的模式下：
- 要提到所有拥有市场背景信息的标的
- 要明确说明哪个标的当前更优先
- 要区分“有推荐”和“仅作为市场参考的 context-only 标的”
- 不要把 context-only 标的表述成“缺少市场数据”
- 不要把多标的内容压缩成单一标的摘要
""".strip()

    try:
        resp = _client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": ctx}],
        )
        narrative = _finalize_narrative_text(resp.content[0].text)
    except Exception as e:
        print(f"[briefing] LLM failed: {e}")
        narrative = "（简报生成失败）"

    if presentation["note"]:
        narrative = f"{presentation['note']}\n\n{narrative}" if narrative else presentation["note"]
    narrative = _finalize_narrative_text(narrative)
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
    except Exception as e:
        print(f"[briefing_check] logging_failed: {e}")

    return briefing
