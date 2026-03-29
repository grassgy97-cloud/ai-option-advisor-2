from app.core.db import engine
from app.strategy.advisor_service_v2 import run_advisor

# 场景3：多标的
print("【场景3】多标的测试")
res3 = run_advisor(engine, "沪深300和上证50近月认购都偏贵，想做跨期组合", "510300")
print(f"识别标的: {res3.parsed_intent.underlying_ids}")
print(f"vol_view: {res3.parsed_intent.vol_view}")
for s in (res3.resolved_candidates or [])[:5]:
    print(f"  {s.underlying_id:<10} {s.strategy_type:<20} score={s.score:.4f}")