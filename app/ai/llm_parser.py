import json
import time
from typing import Optional

import anthropic

from app.core.config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """你是一个A股ETF期权交易意图解析器。
用户会用自然语言描述市场判断和交易需求，解析成结构化JSON。

输出必须是合法JSON，不要有任何其他文字，格式如下：
{
  "underlying_ids": ["510300"],
  "market_view": "neutral",
  "vol_view": "none",
  "risk_preference": "low",
  "defined_risk_only": false,
  "prefer_multi_leg": true,
  "dte_min": 20,
  "dte_max": 45,
  "banned_strategies": [],
  "preferred_strategies": [],
  "greeks_preference": {},
  "price_levels": {},
  "asymmetry": null
}

标的识别（可多个）：
- 沪深300/300ETF/510300 → "510300"
- 上证50/50ETF/510050 → "510050"
- 中证500/500ETF/510500 → "510500"
- 科创50/科创ETF/588000 → "588000"
- 创业板/创业板ETF/159915 → "159915"
- 深证100/100ETF/159901 → "159901"
- 沪深300(深交所)/159919 → "159919"
- 中证500(深)/嘉实500/159922 → "159922"
- 科创50ETF易方达/588080 → "588080"
- 未提及任何标的 → []

market_view：
- 看多/看涨/偏多/后市乐观/bullish → "bullish"
- 看空/看跌/偏空/后市悲观/bearish → "bearish"
- 震荡/中性/没有方向/neutral → "neutral"
- 未明确 → 结合market_context的trend字段判断：
    uptrend → "bullish"，downtrend → "bearish"，sideways → "neutral"
- 用户意图优先，market_context作为辅助参考，不强制覆盖用户判断

vol_view：
- 认购贵/call贵/认购iv高/近月认购偏贵 → "call_iv_rich"
- 认沽贵/put贵/认沽iv高/近月认沽偏贵 → "put_iv_rich"
- 近月贵/近月iv高/前端高/term_front → "term_front_high"
- 远月贵/远月iv高/后端高/term_back → "term_back_high"
- 整体iv高/波动率高/iv高 → "iv_high"
- 未明确时结合market_context：
    put_call_skew > 0.005 → "put_iv_rich"
    put_call_skew < -0.005 → "call_iv_rich"
    否则 → "none"

risk_preference：
- 低风险/保守/风险可控 → "low"
- 高风险/激进 → "high"
- 未明确 → "low"

defined_risk_only：
- 不裸卖/不想裸卖/有限风险/defined risk → true
- 未明确 → false

prefer_multi_leg：
- 跨期/组合/spread/calendar/diagonal/多腿/价差 → true
- 单腿/直接买/直接卖 → false
- 未明确 → false

dte_min/dte_max：
- 近月/短期 → dte_min:10, dte_max:35
- 中期/30到60天 → dte_min:30, dte_max:60
- 未明确 → dte_min:20, dte_max:45

banned_strategies：用户明确说不做某策略时填入，否则 []

preferred_strategies：用户明确指定或强烈暗示某类策略时填入，否则 []
- 备兑/备兑增收/covered call/卖备兑 → ["covered_call"]
- 裸卖put/卖虚值put/naked put → ["naked_put"]
- 裸卖call/卖虚值call/naked call → ["naked_call"]
- 日历/跨期价差/calendar → ["call_calendar", "put_calendar"]
- 对角/diagonal → ["diagonal_call", "diagonal_put"]
- 铁鹰/iron condor → ["iron_condor"]
- 牛市价差/bull spread → ["bull_call_spread", "bull_put_spread"]
- 熊市价差/bear spread → ["bear_call_spread", "bear_put_spread"]
- 买call/long call → ["long_call"]
- 买put/long put → ["long_put"]
- 未明确 → []

greeks_preference：
从用户描述中提取对delta/gamma/vega/theta的偏好，只输出用户明确表达了偏好的Greek。
每个Greek格式为：{"sign": "positive"|"negative"|"neutral", "strength": 0.0-1.0}

strength提取规则（从措辞强度判断）：
- "肯定/明显/强烈/大概率/显著" → 0.8-1.0
- "偏向/相对/可能/倾向" → 0.4-0.6
- "略微/不排除/弱/稍微" → 0.1-0.3

Greek映射规则：
- delta（方向性）：
  - 看涨/看多/上涨受益 → positive
  - 看跌/看空/下跌受益 → negative
  - 中性/无方向 → neutral
- gamma（大幅波动受益）：
  - 预期大幅波动/双向都可能动/行情会有大动作 → positive，strength根据措辞强度
  - 震荡/窄幅/不会大动 → negative
  - 未提及 → 不输出
- vega（IV变化受益）：
  - 预期波动率上升/iv会涨/市场恐慌 → positive
  - 预期波动率下降/iv会跌/市场平静 → negative
  - 未提及 → 不输出
- theta（时间价值收益）：
  - 卖方思维/收权利金/时间价值流逝受益 → positive
  - 买方思维/时间不够/需要快速兑现 → negative
  - 未提及 → 不输出

结合market_context补充greeks判断（仅在用户未明确表达时参考）：
- trend=downtrend且用户未说方向 → delta加negative，strength=0.4
- trend=uptrend且用户未说方向 → delta加positive，strength=0.4
- hv20偏高（>0.25）且用户未说波动预期 → gamma加positive，strength=0.3

示例：
- "双向都可能动，但下行空间更大" →
  {"gamma": {"sign": "positive", "strength": 0.8}, "delta": {"sign": "negative", "strength": 0.3}}
- "震荡市，想收权利金" →
  {"gamma": {"sign": "negative", "strength": 0.6}, "theta": {"sign": "positive", "strength": 0.7}}
- "明显看多，波动率会上升" →
  {"delta": {"sign": "positive", "strength": 0.9}, "vega": {"sign": "positive", "strength": 0.7}}

price_levels：
从用户描述中提取价格水平，用相对当前价的百分比表示（负数=下方，正数=上方）。
只输出用户明确提到的价位，未提及的不输出。

字段说明：
- "support"：支撑位/保底位/下方关键位（例如"有-12%保底" → -0.12）
- "resistance"：压力位/上方阻力（例如"上方8%有压力" → 0.08）
- "target"：目标位/预期运行区间中点（例如"预计涨5%" → 0.05）

⚠️ 价位必须是相对百分比（如-0.08、0.05），不接受绝对价格。

asymmetry：
描述用户预期的涨跌空间是否对称：
- "上涨空间大/上行为主/偏多" → "upside"
- "下跌空间大/下行为主/偏空但有保底" → "downside"
- "双向对称/涨跌都可能且幅度相近" → "symmetric"
- 未明确 → null"""

def _extract_first_json_object(raw: str) -> str:
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    depth = 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]

    raise ValueError("No complete JSON object found")

def parse_with_llm(
    text: str,
    market_context: Optional[dict] = None,
) -> Optional[dict]:
    """
    调用 Claude API 解析自然语言意图，返回结构化dict。

    market_context: build_market_context_multi()的输出，格式为 {uid: ctx_dict}。
    拼入user message供LLM参考，用户意图仍然优先。
    """
    t0 = time.perf_counter()

    user_message = text

    if market_context:
        ctx_lines = ["【当前市场背景（供参考，用户意图优先）】"]
        for uid, ctx in market_context.items():
            summary = ctx.get("summary", "")
            if summary:
                ctx_lines.append(f"• {summary}")
        if len(ctx_lines) > 1:
            user_message = "\n".join(ctx_lines) + "\n\n【用户输入】\n" + text

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=768,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        raw_json = _extract_first_json_object(raw)
        parsed = json.loads(raw_json)

        print(f"[timing] parse_with_llm total = {time.perf_counter() - t0:.3f}s")
        return parsed

    except Exception as e:
        print(f"[llm_parser] 调用失败: {e}")
        print(f"[timing] parse_with_llm failed after = {time.perf_counter() - t0:.3f}s")
        return None