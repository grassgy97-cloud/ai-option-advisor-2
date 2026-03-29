from app.core.db import engine
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.advisor_service_v2 import run_advisor

print(build_iv_percentile_report(engine, "510300"))
res = run_advisor(engine, "我觉得近月认购偏贵，想找低风险跨期组合", "510300")
first = res.resolved_candidates[0]
iv_pct = first.metadata.get("greeks_report", {}).get("iv_percentile")
print(iv_pct)