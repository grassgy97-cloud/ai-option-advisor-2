from typing import Dict, Any, List
from app.strategy.template_service import get_templates_by_ids


def route_strategies(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    whitelist = intent.get("strategy_whitelist", [])
    templates = get_templates_by_ids(whitelist)

    results = []
    for t in templates:
        reason = "符合当前意图"
        if t["template_id"] == "vertical_spread":
            reason = "适合有方向但希望控制风险的场景"
        elif t["template_id"] == "calendar_spread":
            reason = "适合做近远月隐波差和期限结构"
        elif t["template_id"] == "diagonal_spread":
            reason = "兼顾轻微方向和期限结构判断"
        elif t["template_id"] == "parity_arb":
            reason = "适合检测 put-call parity 偏离后的套利机会"
        elif t["template_id"] == "calendar_arb":
            reason = "适合检测跨期结构异常的套利机会"
        elif t["template_id"] == "long_call_put":
            reason = "适合明确方向判断的单腿交易"

        results.append({
            "template_id": t["template_id"],
            "strategy_name": t["strategy_name"],
            "category": t["category"],
            "description": t["description"],
            "reason": reason
        })

    return results