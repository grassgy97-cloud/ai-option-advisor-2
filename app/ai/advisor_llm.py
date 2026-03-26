import json
import anthropic
from app.core.config import ANTHROPIC_API_KEY
from app.data.underlying_knowledge import get_underlying_info

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

ADVISOR_SYSTEM_PROMPT = """你是一个专业的A股ETF期权交易顾问。

你的任务是根据用户意图、扫描结果、回测摘要，输出严格受数据约束的建议。

硬性规则：
1. 你只能基于输入中明确提供的数据说话，禁止补充未提供的实时行情、盘口、IV曲线、期限结构、成交量、到期月份细节。
2. 如果 scan_opportunities 为空或数量为0：
   - 明确说明“当前测试数据下未发现机会”
   - 不要输出具体交易方案
   - 不要推荐具体合约、月份、方向
   - 不要引用任何其他标的作为当前用户问题的答案主体
   - 只能说明下一步需要补哪些真实数据
3. 如果 scan_opportunities 不为空：
   - 只能讨论 scan_opportunities 中实际出现的标的和策略
   - 不得扩展到未出现在 scan_opportunities 里的标的
   - 不得把测试数据表述成“实时市场已确认”
   - 要明确区分“扫描发现”与“待真实行情验证”
4. 不得编造收益率、年化收益、胜率、Greeks、流动性、到期日结论；除非这些值已经在输入中明确给出。
5. 不得改写、简写、猜测或拼错 underlying_id、reference_key、contract_id，必须原样引用输入数据。
6. 如果 parsed_intent.underlying_specified 为 false，不得表述为“用户要求扫描某单一标的”或“系统聚焦某单一标的”；只能表述为“当前测试数据下扫描到的机会主要/仅来自某标的”。
7. 如果机会中已经包含 rank 和 score：
   - 必须按 rank 从小到大表述
   - 不要自定义星级、S/A/B级、Top级等额外排序体系
   - 优先直接引用 rank、strategy_type、edge_value、score
8. A股ETF期权为欧式期权，但不要基于此延伸出输入中不存在的交易细节。

输出要求：
1. 先用1-2句话概括用户意图
2. 再根据是否有扫描结果分情况输出
3. 若有扫描结果，优先使用“机会概览 / 排名靠前机会 / 风险提示 / 下一步验证”结构
4. 语言简洁、专业、克制
5. 使用中文
"""


def build_market_context(parsed_intent: dict, scan_result: dict, backtest_result: dict) -> str:
    underlying_id = parsed_intent.get("underlying_id") if parsed_intent.get("underlying_specified") else None
    info = get_underlying_info(underlying_id) if underlying_id else {}

    opportunities = scan_result.get("opportunities", []) or []
    opp_summary = []
    for o in opportunities[:5]:
        opp_summary.append({
            "rank": o.get("rank"),
            "type": o.get("strategy_type"),
            "underlying": o.get("underlying_id"),
            "reference_key": o.get("reference_key"),
            "edge": o.get("edge_value"),
            "edge_pct": o.get("edge_pct"),
            "score": o.get("score"),
            "risk": o.get("risk_level"),
            "transaction_cost_est": o.get("transaction_cost_est"),
            "note": o.get("note"),
            "greeks": o.get("greeks", {}),
            "legs": o.get("leg_json", []),
        })

    bt_summary = None
    if backtest_result and backtest_result.get("sample_count", 0) > 0:
        raw = backtest_result.get("raw", {})
        bt_summary = {
            "strategy": raw.get("strategy_type"),
            "underlying_id": raw.get("underlying_id"),
            "metric_name": raw.get("metric_name"),
            "samples": backtest_result.get("sample_count", 0),
            "hit_ratio": backtest_result.get("hit_ratio", 0),
            "avg_value": backtest_result.get("avg_value", 0),
        }

    context = {
        "user_input": parsed_intent.get("raw_view"),
        "parsed_intent": {
            "underlying": underlying_id,
            "underlying_name": info.get("name", ""),
            "underlying_liquidity": info.get("liquidity", ""),
            "vol_view": parsed_intent.get("vol_view"),
            "market_view": parsed_intent.get("market_view"),
            "direction_bias": parsed_intent.get("direction_bias"),
            "defined_risk_only": parsed_intent.get("defined_risk_only"),
            "prefer_multi_leg": parsed_intent.get("prefer_multi_leg"),
            "holding_period_days": parsed_intent.get("holding_period_days"),
            "strategy_whitelist": parsed_intent.get("strategy_whitelist", []),
            "mode": parsed_intent.get("mode"),
            "underlying_specified": parsed_intent.get("underlying_specified"),
        },
        "scan_meta": {
            "factor_rows": scan_result.get("factor_rows", 0),
            "opportunity_count": scan_result.get("opportunity_count", 0),
        },
        "scan_opportunities": opp_summary,
        "backtest": bt_summary,
        "data_note": "当前为测试数据环境，以下结论仅代表当前样本扫描结果，不代表实时市场结论。"
    }

    return json.dumps(context, ensure_ascii=False, indent=2)


def _build_user_prompt(market_context: str, has_opportunities: bool) -> str:
    if not has_opportunities:
        return f"""以下是当前用户意图与测试数据上下文。

请严格遵守以下要求：
- 当前 scan_opportunities 为空时，只能说明“当前测试数据下未发现机会”
- 不要输出任何具体交易策略、月份、腿方向、收益率、实时行情判断
- 不要引用其他标的替代回答
- 总篇幅控制在600字以内，避免重复，不要输出未完成条目。
- 只需要输出：
  1. 对用户意图的简短理解
  2. 当前测试数据结论
  3. 下一步需要补充的真实数据项
  4. 一句谨慎结论

数据如下：
{market_context}
"""
    return f"""以下是当前用户意图与测试数据上下文。

请严格遵守以下要求：
- 只能围绕 scan_opportunities 中已经出现的机会展开
- 若 parsed_intent.underlying_specified 为 false，不要写成“用户要求扫描某单一标的”或“系统聚焦某单一标的”
- 若机会中已有 rank，则必须按 rank 顺序输出
- 不要使用星级、S级、Top级等自定义排序标签
- 不要改写、简写或拼错 underlying_id、contract_id、reference_key
- 不要编造未提供的实时行情、成交量、IV水平、到期选择逻辑
- 可以按“机会概览 + 排名前列机会 + 风险提示 + 下一步验证”输出
- 如果提到具体机会，请明确它来自扫描结果，而非实时确认

数据如下：
{market_context}
"""


def run_advisor_llm(parsed_intent: dict, scan_result: dict, backtest_result: dict) -> str:
    try:
        market_context = build_market_context(parsed_intent, scan_result, backtest_result)
        has_opportunities = (scan_result.get("opportunity_count", 0) > 0)
        user_prompt = _build_user_prompt(market_context, has_opportunities)

        print("[advisor_llm] 开始调用API")
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            system=ADVISOR_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": user_prompt
            }]
        )
        result = response.content[0].text.strip()
        print(f"[advisor_llm] 调用成功，长度: {len(result)}")
        return result

    except Exception as e:
        import traceback
        print(f"[advisor_llm] 调用失败: {e}")
        traceback.print_exc()
        return None