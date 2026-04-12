# briefing.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_client

_client = get_anthropic_client()


_PROFIT_CONDITIONS = {
    "call_calendar":   "近月call IV收敛+标的小幅波动",
    "put_calendar":    "近月put IV收敛+标的小幅波动",
    "diagonal_call":   "近月call衰减+标的温和上涨",
    "diagonal_put":    "近月put衰减+标的温和下跌",
    "bear_call_spread":"标的到期低于short call strike",
    "bull_put_spread": "标的到期高于short put strike",
    "bull_call_spread":"标的到期高于long call strike",
    "bear_put_spread": "标的到期低于long put strike",
    "iron_condor":     "标的到期在两个short strike之间",
    "iron_fly":        "标的到期接近ATM strike",
    "long_call":       "标的上涨+IV上升",
    "long_put":        "标的下跌+IV上升",
    "naked_call":      "标的不涨或下跌，call到期虚值",
    "naked_put":       "标的不跌或上涨，put到期虚值",
    "covered_call":    "标的横盘或温和上涨，call到期虚值",
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


def _build_presentation_summary(top: List[Any]) -> Dict[str, Any]:
    if not top:
        return {
            "overall_tier": "no_candidate",
            "overall_label": "无候选",
            "top_score": None,
            "note": "当前没有可展示的候选策略。",
        }

    top_score = float(top[0].score or 0.0)
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


def build_briefing(
    ranked: List,
    intent_text: str,
    top_n: int = 5,
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    生成推荐简报：表格 + LLM 自然语言解释。
    market_context: {uid: ctx_dict}，用于在简报里体现机器判断。
    """
    top = ranked[:top_n]
    presentation = _build_presentation_summary(top)

    # ── 表格部分 ──
    table = []
    for i, s in enumerate(top, 1):
        gr = s.metadata.get("greeks_report", {})
        iv_pct = gr.get("iv_percentile", {})
        ng = gr.get("net_greeks", {})

        if s.net_debit is not None:
            cost_str = f"付权利金 {s.net_debit:.4f}"
        elif s.net_credit is not None:
            cost_str = f"收权利金 {s.net_credit:.4f}"
        else:
            cost_str = "—"

        legs_str = " / ".join(
            f"{'卖' if l.action == 'SELL' else '买'}"
            f"{l.option_type}"
            f" K={l.strike}"
            f" {l.expiry_date}"
            f" Δ={round(l.delta, 2) if l.delta else '—'}"
            f" mid={l.mid}"
            for l in s.legs
        )

        table.append({
            "rank":             i,
            "underlying":       s.underlying_id,
            "strategy":         s.strategy_type,
            "score":            round(s.score, 3),
            "score_tier":       _score_tier(s.score),
            "score_tier_label": _score_tier_label(s.score),
            "cost":             cost_str,
            "legs":             legs_str,
            "net_delta":        round(ng.get("net_delta") or 0, 3),
            "net_vega":         round(ng.get("net_vega") or 0, 4),
            "net_theta":        round(ng.get("net_theta") or 0, 4),
            "iv_label":         iv_pct.get("label", "—"),
            "iv_pct":           iv_pct.get("composite_percentile", "—"),
            "risk_flags":       gr.get("risk_flags", []),
            "profit_condition": _PROFIT_CONDITIONS.get(s.strategy_type, "—"),
        })

    # ── 构建LLM上下文 ──
    ctx_lines = [f"用户输入：{intent_text}"]

    # 加入market_context摘要
    if market_context:
        ctx_lines.append("\n当前市场背景：")
        for uid, ctx in market_context.items():
            summary = ctx.get("summary", "")
            if summary:
                ctx_lines.append(f"• {summary}")

    ctx_lines.append("\n推荐策略（按评分排序）：")
    for row in table:
        ctx_lines.append(
            f"{row['rank']}. {row['underlying']} {row['strategy']} "
            f"score={row['score']} {row['cost']} "
            f"legs=[{row['legs']}] "
            f"IV={row['iv_label']}({row['iv_pct']}) "
            f"delta={row['net_delta']} vega={row['net_vega']} theta={row['net_theta']} "
            f"flags={row['risk_flags']}"
        )
    if presentation["note"]:
        ctx_lines.append(f"\n呈现层级：{presentation['note']}")

    ctx = "\n".join(ctx_lines)

    system = """你是一个A股ETF期权交易助手，面向有经验的交易者。
用简洁专业的语言（不超过250字）解释推荐理由，包含：
1. 当前市场环境判断（结合技术面和期权市场数据，一两句）
2. 首选策略的核心逻辑
3. 机器判断与用户判断若有分歧，简要提示（如"技术面偏空但您判断偏多，注意风险"）
4. 建仓后什么情况下考虑平仓（触发条件，一两句）
不需要科普基础知识，直接讲重点。输出中文。"""

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

    return {
        "table":     table,
        "narrative": narrative,
        "presentation": presentation,
    }
