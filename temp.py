from app.strategy.advisor_service_v2 import run_advisor
from app.core.db import engine

result = run_advisor(engine, "510050偏空，认沽IV可能增长，下方-8%有支撑保底", "510050")
for s in result.resolved_candidates[:3]:
    bd = s.score_breakdown
    if s.strategy_type == "long_put":
        print(f"iv_score={bd.get('iv_score')}  cost_score={bd.get('cost_score')}")