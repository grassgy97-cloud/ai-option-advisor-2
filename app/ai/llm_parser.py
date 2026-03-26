import json
import anthropic
from app.core.config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """你是一个A股ETF期权交易意图解析器。
用户会用自然语言描述他的市场判断和交易需求，你需要将其解析成结构化JSON。

输出必须是合法JSON，不要有任何其他文字，格式如下：
{
  "underlying_id": "510300",
  "underlying_specified": false,
  "mode": "user_driven",
  "market_view": "neutral",
  "vol_view": null,
  "direction_bias": "neutral",
  "holding_period_days": 3,
  "risk_preference": "low",
  "defined_risk_only": true,
  "prefer_multi_leg": true,
  "allow_single_leg": false,
  "strategy_whitelist": ["calendar_spread", "vertical_spread"],
  "strategy_blacklist": []
}

mode 判断规则：
- 用户有明确看法/意图（认购偏贵/看多/看空/后市判断/发现异常现象/想做某策略）→ mode: user_driven
- 用户让系统主动找机会，没有任何方向性看法（有没有机会/帮我扫/今天有什么可以做/有没有套利）→ mode: system_scan
- 注意：有方向性看法但没指定标的，仍然是 user_driven，不是 system_scan

underlying_specified 判断规则：
- 用户明确提到标的名称（300ETF/50ETF/科创50/500ETF等）→ underlying_specified: true
- 没有提到具体标的 → underlying_specified: false
- underlying_specified: false 时，underlying_id 默认填 510300

strategy_whitelist 必须从以下6个中选，不能为空：
long_call_put, vertical_spread, calendar_spread, diagonal_spread, parity_arb, calendar_arb

strategy_whitelist 选择规则：
- vol_view=call_iv_rich 且 defined_risk_only=true → ["vertical_spread", "calendar_spread", "diagonal_spread"]
- vol_view=put_iv_rich 且 defined_risk_only=true → ["vertical_spread", "calendar_spread", "diagonal_spread"]
- vol_view=call_iv_rich 且 defined_risk_only=false → ["calendar_arb", "parity_arb", "vertical_spread"]
- vol_view=put_iv_rich 且 defined_risk_only=false → ["calendar_arb", "parity_arb", "vertical_spread"]
- market_view=bullish → ["long_call_put", "vertical_spread", "diagonal_spread"]
- market_view=bearish → ["long_call_put", "vertical_spread", "diagonal_spread"]
- market_view=neutral 且 prefer_multi_leg=true → ["calendar_spread", "diagonal_spread", "vertical_spread"]
- mode=system_scan → ["parity_arb", "calendar_arb", "vertical_spread"]
- 其他情况 → ["long_call_put", "vertical_spread", "calendar_spread"]

其他解析规则：
- 认购偏贵/call贵 → vol_view: call_iv_rich
- 认沽偏贵/put贵 → vol_view: put_iv_rich
- 波动率高/iv高/波动大 → vol_view: iv_high
- 波动率低/iv低 → vol_view: iv_low
- 看多/看涨/后市乐观 → market_view: bullish, direction_bias: bullish
- 看空/看跌/后市悲观 → market_view: bearish, direction_bias: bearish
- 震荡/中性/没有方向 → market_view: neutral
- 低风险/风险可控/不裸卖/不想裸卖 → defined_risk_only: true, risk_preference: low
- 组合/价差/跨期 → prefer_multi_leg: true, allow_single_leg: false
- 单腿/直接买/直接卖 → allow_single_leg: true, prefer_multi_leg: false
- 300ETF/沪深300 → underlying_id: 510300, underlying_specified: true
- 50ETF/上证50 → underlying_id: 510050, underlying_specified: true
- 500ETF/中证500 → underlying_id: 510500, underlying_specified: true
- 科创50/科创 → underlying_id: 588000, underlying_specified: true
- 创业板/创业板ETF → underlying_id: 159915, underlying_specified: true"""


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