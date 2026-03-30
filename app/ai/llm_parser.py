import json
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
  "banned_strategies": []
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
- 未明确 → "neutral"

vol_view：
- 认购贵/call贵/认购iv高/近月认购偏贵 → "call_iv_rich"
- 认沽贵/put贵/认沽iv高/近月认沽偏贵 → "put_iv_rich"
- 近月贵/近月iv高/前端高/term_front → "term_front_high"
- 远月贵/远月iv高/后端高/term_back → "term_back_high"
- 整体iv高/波动率高/iv高 → "iv_high"
- 未明确 → "none"

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

banned_strategies：用户明确说不做某策略时填入，例如 ["call_calendar"]，否则 []"""


def parse_with_llm(text: str) -> dict:
    """调用 Claude API 解析自然语言意图，返回结构化dict"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}]
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[llm_parser] 调用失败: {e}")
        return None