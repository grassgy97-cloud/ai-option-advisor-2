from __future__ import annotations

from collections import defaultdict
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


def _strategy_to_table_row(
    strategy: Any,
    rank: int,
    group_rank: int,
    within_underlying_rank: int,
) -> Dict[str, Any]:
    # rank = flat global display rank
    # group_rank = underlying group order
    # within_underlying_rank = rank inside one underlying group
    metadata = strategy.metadata or {}
    greeks_report = metadata.get("greeks_report", {}) or {}
    iv_pct = greeks_report.get("iv_percentile", {}) or {}
    net_greeks = greeks_report.get("net_greeks", {}) or {}

    if strategy.net_debit is not None:
        cost_str = f"付权利金 {strategy.net_debit:.4f}"
    elif strategy.net_credit is not None:
        cost_str = f"收权利金 {strategy.net_credit:.4f}"
    else:
        cost_str = "-"

    legs_str = " / ".join(
        f"{'卖' if leg.action == 'SELL' else '买'}"
        f"{leg.option_type}"
        f" K={leg.strike}"
        f" {leg.expiry_date}"
        f" Δ={round(leg.delta, 2) if leg.delta is not None else '-'}"
        f" mid={leg.mid}"
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
        "iv_label": iv_pct.get("label", "-"),
        "iv_pct": iv_pct.get("composite_percentile", "-"),
        "risk_flags": greeks_report.get("risk_flags", []),
        "profit_condition": _PROFIT_CONDITIONS.get(strategy.strategy_type, "-"),
    }


def _group_ranked_by_underlying(ranked: List[Any], per_underlying_limit: int = 3) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for strategy in ranked:
        grouped[strategy.underlying_id].append(strategy)

    groups: List[Dict[str, Any]] = []
    for underlying_id, items in grouped.items():
        sorted_items = sorted(items, key=lambda strategy: float(strategy.score or 0.0), reverse=True)
        kept_items = sorted_items[:per_underlying_limit]
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
        return "IV信息不足"
    return "；".join(parts)


def _format_term_structure_text(ctx: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    def describe(label: str, slope: Optional[float]) -> Optional[str]:
        if slope is None or abs(float(slope)) < 0.01:
            return None
        slope_value = float(slope)
        if slope_value > 0:
            return f"{label}期限结构前高后低：近月IV高于远月 {slope_value:.3f}"
        return f"{label}期限结构后高前低：远月IV高于近月 {abs(slope_value):.3f}"

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
                f"candidate_count={item['candidate_count']} "
                f"top_strategy={item.get('top_strategy_type') or '-'}"
            )
            ctx_lines.append(
                f"  历史波动/Realized：{vol_ctx.get('historical_volatility_text', '历史波动率信息不足')}"
            )
            ctx_lines.append(
                f"  隐含波动/IV：{vol_ctx.get('implied_volatility_text', 'IV信息不足')}"
            )
            term_texts = vol_ctx.get("term_structure_texts") or []
            if term_texts:
                ctx_lines.append(f"  期限结构/Term：{'；'.join(term_texts)}")
            else:
                ctx_lines.append("  期限结构/Term：未见强期限结构信号")
            ctx_lines.append(
                f"  偏斜/Skew：{vol_ctx.get('skew_text', 'skew 信息不足')}"
            )
            if item.get("market_summary"):
                ctx_lines.append(
                    f"  补充市场摘要（如与上面结构化波动率信息冲突，以结构化信息为准）：{item['market_summary']}"
                )

    if recommendation_groups:
        ctx_lines.append("\n分组推荐：")
        for group in recommendation_groups:
            ctx_lines.append(
                f"- 标的 {group['underlying_id']}（组内 top={group['top_score']}，"
                f"层级={group['presentation']['overall_label']}）"
            )
            for item in group["items"]:
                ctx_lines.append(
                    f"  {item['within_underlying_rank']}. {item['strategy']} "
                    f"score={item['score']} {item['cost']} "
                    f"IV={item['iv_label']}({item['iv_pct']}) "
                    f"delta={item['net_delta']} vega={item['net_vega']} theta={item['net_theta']} "
                    f"flags={item['risk_flags']}"
                )

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

    grouped = _group_ranked_by_underlying(ranked, per_underlying_limit=3)
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
你是一个 A 股 ETF 期权交易助手，面向有经验的交易者。请用简洁、专业、克制的中文（不超过250字）解释推荐理由，包含：
1. 当前市场环境判断（结合市场背景与期权结构信号，一两句）
2. 首选标的与首选策略的核心逻辑
3. 若不同标的之间强弱有明显差异，简要比较
4. 若机器判断与用户判断有分歧，简要提示
5. 建仓后应关注什么触发条件决定减仓或平仓

波动率表述规则：
- 历史波动率 / HV / realized volatility 只用于描述近期实际波动大小，例如“历史波动率较高/较低”“近期实际波动更大/更小”
- 不得把 HV 写成“贵/便宜”或“估值高/低”
- “贵/便宜”只可用于 IV 或明确的 IV 结构定价语境
- IV / ATM IV / IV percentile 要与 HV 分开描述
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
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": ctx}],
        )
        narrative = resp.content[0].text.strip()
    except Exception as e:
        print(f"[briefing] LLM failed: {e}")
        narrative = "（简报生成失败）"

    if presentation["note"]:
        narrative = f"{presentation['note']}\n\n{narrative}" if narrative else presentation["note"]

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
