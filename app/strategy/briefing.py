# briefing.py
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_client

_client = get_anthropic_client()


_PROFIT_CONDITIONS = {
    "call_calendar": "近月call IV收敛+标的小幅波动",
    "put_calendar": "近月put IV收敛+标的小幅波动",
    "diagonal_call": "近月call衰减+标的温和上涨",
    "diagonal_put": "近月put衰减+标的温和下跌",
    "bear_call_spread": "标的到期低于short call strike",
    "bull_put_spread": "标的到期高于short put strike",
    "bull_call_spread": "标的到期高于long call strike",
    "bear_put_spread": "标的到期低于long put strike",
    "iron_condor": "标的到期在两侧short strike之间",
    "iron_fly": "标的到期接近ATM strike",
    "long_call": "标的上涨+IV上升",
    "long_put": "标的下跌+IV上升",
    "naked_call": "标的不涨或下跌，call到期虚值",
    "naked_put": "标的不跌或上涨，put到期虚值",
    "covered_call": "标的横盘或温和上涨，call到期虚值",
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


def _strategy_to_table_row(
    strategy: Any,
    rank: int,
    group_rank: int,
    within_underlying_rank: int,
) -> Dict[str, Any]:
    # rank = flat global display rank
    # group_rank = underlying group order
    # within_underlying_rank = rank inside one underlying group
    greeks_report = strategy.metadata.get("greeks_report", {})
    iv_pct = greeks_report.get("iv_percentile", {})
    net_greeks = greeks_report.get("net_greeks", {})

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
        "score": round(strategy.score, 3),
        "score_tier": _score_tier(strategy.score),
        "score_tier_label": _score_tier_label(strategy.score),
        "cost": cost_str,
        "legs": legs_str,
        "net_delta": round(net_greeks.get("net_delta") or 0, 3),
        "net_vega": round(net_greeks.get("net_vega") or 0, 4),
        "net_theta": round(net_greeks.get("net_theta") or 0, 4),
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
        sorted_items = sorted(items, key=lambda strategy: strategy.score, reverse=True)
        kept_items = sorted_items[:per_underlying_limit]
        strong_recommendation_count = sum(
            1 for strategy in kept_items
            if _score_tier(strategy.score) == "strong_recommendation"
        )
        groups.append({
            "underlying_id": underlying_id,
            "items": kept_items,
            "candidate_count": len(sorted_items),
            "top_score": round(float(kept_items[0].score), 3) if kept_items else None,
            "strong_recommendation_count": strong_recommendation_count,
            "presentation": _build_presentation_summary(kept_items),
        })

    groups.sort(
        key=lambda group: (
            -(group["top_score"] if group["top_score"] is not None else -1),
            -group["strong_recommendation_count"],
            -group["candidate_count"],
            group["underlying_id"],
        ),
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
        ctx = market_context.get(underlying_id, {})
        summary = ctx.get("summary", "")
        top_strategy = group["items"][0] if group["items"] else None
        overview.append({
            "group_rank": group_index,
            "underlying_id": underlying_id,
            "top_score": group["top_score"],
            "strong_recommendation_count": group["strong_recommendation_count"],
            "candidate_count": group["candidate_count"],
            "top_strategy_type": top_strategy.strategy_type if top_strategy is not None else None,
            "presentation": group["presentation"],
            "market_summary": summary,
        })

    extra_context_ids = sorted(
        uid for uid in market_context.keys()
        if uid not in grouped_ids
    )
    for offset, underlying_id in enumerate(extra_context_ids, len(overview) + 1):
        ctx = market_context.get(underlying_id, {})
        overview.append({
            "group_rank": offset,
            "underlying_id": underlying_id,
            "top_score": None,
            "strong_recommendation_count": 0,
            "candidate_count": 0,
            "top_strategy_type": None,
            "presentation": _build_presentation_summary([]),
            "market_summary": ctx.get("summary", ""),
        })

    return overview


def _build_recommendation_groups(recommendation_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []

    for group_index, group in enumerate(recommendation_groups, 1):
        items = sorted(group["items"], key=lambda strategy: strategy.score, reverse=True)
        output.append({
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
        })

    return output


def _build_cross_underlying_summary(
    recommendation_groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not recommendation_groups:
        return {
            "best_underlying_id": None,
            "comparison_text": "当前没有可比较的跨标的推荐。",
        }

    best_group = recommendation_groups[0]
    best_underlying_id = best_group["underlying_id"]
    best_top = best_group["items"][0] if best_group["items"] else None
    best_desc = (
        f"{best_underlying_id} 目前最强，首选是 {best_top.strategy_type}，评分 {best_group['top_score']:.3f}。"
        if best_top is not None and best_group["top_score"] is not None
        else f"{best_underlying_id} 目前是最高优先级标的。"
    )

    if len(recommendation_groups) == 1:
        comparison_text = best_desc
    else:
        second_group = recommendation_groups[1]
        second_top = second_group["items"][0] if second_group["items"] else None
        if second_top is not None and second_group["top_score"] is not None and best_group["top_score"] is not None:
            gap = round(best_group["top_score"] - second_group["top_score"], 3)
            comparison_text = (
                f"{best_desc} 相比之下，{second_group['underlying_id']} 的首选是 "
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
        sorted_items = sorted(group["items"], key=lambda strategy: strategy.score, reverse=True)
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
            summary_suffix = (
                f"；市场背景：{item['market_summary']}"
                if item.get("market_summary")
                else "；市场背景：无市场摘要（不等于缺少数据）"
            )
            ctx_lines.append(
                f"- {item['group_rank']}. {item['underlying_id']} "
                f"coverage={coverage} "
                f"top_score={item['top_score']} candidate_count={item['candidate_count']} "
                f"top_strategy={item.get('top_strategy_type') or '-'}{summary_suffix}"
            )

    if recommendation_groups:
        ctx_lines.append("\n分组推荐：")
        for group in recommendation_groups:
            ctx_lines.append(
                f"- 标的 {group['underlying_id']}（组内Top={group['top_score']}，"
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
    ranked: List,
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

    system = """你是一个A股ETF期权交易助手，面向有经验的交易者。
用简洁专业的语言（不超过250字）解释推荐理由，包含：
1. 当前市场环境判断（结合技术面和期权市场数据，一两句）
2. 首选标的与首选策略的核心逻辑
3. 若不同标的之间强弱有明显差异，简要比较
4. 机器判断与用户判断若有分歧，简要提示
5. 建仓后什么情况下考虑平仓（触发条件，一两句）
不需要科普基础知识，直接讲重点。输出中文。

多标的模式下：
- 要提到所有拥有市场背景信息的标的
- 要明确说明哪个标的当前更优先
- 要区分“有推荐”和“仅作为市场参考的context-only标的”
- 不要把context-only标的表述成“缺少市场数据”
- 不要把多标的内容压缩成单一标的摘要
"""

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
