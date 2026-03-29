# briefing.py
from __future__ import annotations
import anthropic
from app.core.config import ANTHROPIC_API_KEY

from typing import Any, Dict, List, Optional

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def build_briefing(
    ranked: List[ResolvedStrategy],
    intent_text: str,
    top_n: int = 5,
) -> Dict[str, Any]:
    """
    生成推荐简报：表格 + LLM 自然语言解释。
    """
    top = ranked[:top_n]

    # ── 表格部分 ──
    table = []
    for i, s in enumerate(top, 1):
        gr = s.metadata.get("greeks_report", {})
        iv_pct = gr.get("iv_percentile", {})
        ng = gr.get("net_greeks", {})

        # 建仓成本
        if s.net_debit is not None:
            cost_str = f"付权利金 {s.net_debit:.4f}"
        elif s.net_credit is not None:
            cost_str = f"收权利金 {s.net_credit:.4f}"
        else:
            cost_str = "—"

        # 腿信息

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
            "rank":          i,
            "underlying":    s.underlying_id,
            "strategy":      s.strategy_type,
            "score":         round(s.score, 3),
            "cost":          cost_str,
            "legs":          legs_str,
            "net_delta":     round(ng.get("net_delta") or 0, 3),
            "net_vega":      round(ng.get("net_vega") or 0, 4),
            "net_theta":     round(ng.get("net_theta") or 0, 4),
            "iv_label":      iv_pct.get("label", "—"),
            "iv_pct":        iv_pct.get("composite_percentile", "—"),
            "risk_flags":    gr.get("risk_flags", []),
        })

    # ── LLM 简报 ──
    # 构建给 LLM 的紧凑上下文
    ctx_lines = [f"用户输入：{intent_text}", "推荐策略（按评分排序）："]
    for row in table:
        ctx_lines.append(
            f"{row['rank']}. {row['underlying']} {row['strategy']} "
            f"score={row['score']} {row['cost']} "
            f"legs=[{row['legs']}] "
            f"IV={row['iv_label']}({row['iv_pct']}) "
            f"delta={row['net_delta']} vega={row['net_vega']} theta={row['net_theta']} "
            f"flags={row['risk_flags']}"
        )
    ctx = "\n".join(ctx_lines)

    system = """你是一个A股ETF期权交易助手，面向有经验的交易者。
用简洁专业的语言（不超过200字）解释推荐理由，包含：
1. 当前市场环境判断（一句话）
2. 首选策略的核心逻辑
3. 建仓后什么情况下考虑平仓（触发条件，一两句）
不需要科普基础知识，直接讲重点。输出中文。"""

    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": ctx}],
        )
        narrative = resp.content[0].text.strip()
    except Exception as e:
        print(f"[briefing] LLM failed: {e}")
        narrative = "（简报生成失败）"

    return {
        "table": table,
        "narrative": narrative,
    }