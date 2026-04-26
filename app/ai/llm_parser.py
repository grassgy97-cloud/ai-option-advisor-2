from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.ai.client import DEFAULT_ANTHROPIC_MODEL, get_anthropic_client

client = get_anthropic_client()

SYSTEM_PROMPT = """你是一个 A 股 ETF 期权交易意图解析器。
请把用户自然语言输入解析成 JSON，不要输出任何额外说明。

输出格式必须是合法 JSON，例如：
{
  "underlying_ids": ["510300"],
  "market_view": "neutral",
  "vol_view": "none",
  "risk_preference": "low",
  "defined_risk_only": false,
  "prefer_multi_leg": false,
  "require_positive_theta": false,
  "prefer_income_family": false,
  "ban_naked_short": false,
  "prefer_directional_backup": false,
  "prefer_neutral_structure": false,
  "range_bias": null,
  "market_view_strength": 0.5,
  "horizon_views": null,
  "vol_view_detail": null,
  "dte_min": 20,
  "dte_max": 45,
  "banned_strategies": [],
  "preferred_strategies": [],
  "greeks_preference": {},
  "price_levels": {},
  "asymmetry": null
}

字段规则：
- underlying_ids: 可识别多个标的；未识别时返回 []
- market_view: bullish / bearish / neutral
- vol_view: none / iv_high / iv_low / call_iv_rich / put_iv_rich / term_front_high / term_back_high
- risk_preference: low / medium / high
- defined_risk_only:
  - 用户明确说“风险可控 / 有限风险 / defined risk / 不想裸卖 / 不裸卖 / 禁止裸卖”时为 true
- prefer_multi_leg:
  - 用户说 spread / calendar / diagonal / 跨期 / 多腿 / 组合 / 价差 时为 true
- require_positive_theta:
  - 用户说“theta 为正 / 正 theta / 收 theta / time decay income / 收时间价值”时为 true
- prefer_income_family:
  - 用户表达“优先收 theta / 收权利金 / 权利金收入 / income”时为 true
- ban_naked_short:
  - 用户说“不想裸卖 / 不裸卖 / 禁止裸卖 / no naked short”时为 true
- prefer_directional_backup:
  - 用户说“保留一个方向性备选 / 方向性备选 / directional backup”时为 true
- prefer_neutral_structure:
  - 用户说“没有明确方向判断 / 中性 / 震荡 / 区间 / 不确定涨跌方向”时为 true
  - 该字段只表示更偏好 neutral/range structure，不替代 market_view
- range_bias:
  - “没有明确方向 / 中性 / 震荡 / 区间” -> "strict_range"
  - “震荡偏弱 / 上方空间有限 / 不会大涨 / 有压力 / 有 cap” -> "weak_bearish_range"
  - “震荡偏强 / 下方空间有限 / 不会大跌 / 有支撑” -> "weak_bullish_range"
  - 未表达区间或弱方向时 -> null
- market_view_strength:
  - 轻微看多 / 轻微看空 / 略看多 / 略看空 -> 0.35
  - 偏多 / 偏空 / 看多 / 看空 -> 0.65
  - 强烈看多 / 强烈看空 / 明显看多 / 明显看空 -> 0.90
- horizon_views:
  - 只在用户明确区分短期/近期/近月/本周/未来几天 与 中期/后续/未来一个月/一两个月/中远期时填写
  - 最多包含 short_term 和 medium_term，不要输出 long_term
  - 每个 horizon 使用 {"direction": "bullish|bearish|neutral|unknown", "direction_strength": 0.0-1.0, "vol_bias": "up|down|flat|unknown"}
  - “短期偏空，中期不悲观” -> short_term.direction=bearish, medium_term.direction=neutral
  - “近期波动抬头，中期回落” -> short_term.vol_bias=up, medium_term.vol_bias=down
- vol_view_detail:
  - 只在用户明确表达更细波动率观点时填写，否则为 null
  - 支持 atm/call/put/skew/term 五个槽位，可部分填写
  - atm: {"level": "high|normal|low|unknown", "expected_change": "up|down|flat|unknown", "horizon": "short_term|medium_term|unknown"}
  - call/put: {"level": "rich|cheap|normal|unknown", "expected_change": "up|down|flat|unknown"}
  - skew: {"direction": "put_rich|call_rich|neutral|unknown", "expected_change": "steepen|flatten|stable|unknown"}
  - term: {"front": "rich|cheap|normal|unknown", "back": "rich|cheap|normal|unknown", "expected_shape_change": "steepen|flatten|unknown"}
- dte:
  - 近月/短期 -> 10~35
  - 中期/30到60天 -> 30~60
  - 否则 -> 20~45
- preferred_strategies:
  - 只在用户明确点名策略类型时填写
- banned_strategies:
  - 只在用户明确禁止某策略时填写
- greeks_preference:
  - 仅在用户明确表达 Greek 偏好时填写
  - 例如“theta 为正” -> {"theta": {"sign": "positive", "strength": 0.9}}
  - “看空” -> {"delta": {"sign": "negative", "strength": 0.7}}

标的识别：
- 510300 / 沪深300 / 300ETF -> 510300
- 510050 / 上证50 / 50ETF -> 510050
- 510500 / 中证500 / 500ETF -> 510500
- 588000 / 科创50 / 科创 ETF -> 588000
- 588080 -> 588080
- 159915 / 创业板 ETF -> 159915
- 159901 / 深证100 -> 159901
- 159919 -> 159919
- 159922 -> 159922
""".strip()


def _extract_first_json_object(raw: str) -> str:
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    depth = 0
    for index in range(start, len(raw)):
        ch = raw[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:index + 1]

    raise ValueError("No complete JSON object found")


def _strip_fenced_json(raw: str) -> str:
    return raw.replace("```json", "").replace("```", "").strip()


def _build_user_message(text: str, market_context: Optional[dict]) -> str:
    if not market_context:
        return text

    ctx_lines = ["【当前市场背景（仅供参考，用户意图优先）】"]
    for uid, ctx in (market_context or {}).items():
        summary = (ctx or {}).get("summary")
        if summary:
            ctx_lines.append(f"- {uid}: {summary}")

    if len(ctx_lines) == 1:
        return text
    return "\n".join(ctx_lines) + f"\n\n【用户输入】\n{text}"


def parse_with_llm(
    text: str,
    market_context: Optional[dict] = None,
) -> Optional[dict]:
    """
    调用 Claude 解析用户交易意图。
    失败时返回 None，由上层走 deterministic fallback。
    """
    t0 = time.perf_counter()
    user_message = _build_user_message(text, market_context)

    try:
        response = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=768,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = _strip_fenced_json(response.content[0].text.strip())
        parsed = json.loads(_extract_first_json_object(raw))
        print(f"[timing] parse_with_llm total = {time.perf_counter() - t0:.3f}s")
        return parsed
    except Exception as exc:
        print(f"[llm_parser] parse failed: {exc}")
        print(f"[timing] parse_with_llm failed after = {time.perf_counter() - t0:.3f}s")
        return None
