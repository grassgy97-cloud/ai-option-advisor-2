from app.core.db import engine
from app.strategy.advisor_service_v2 import run_advisor

res = run_advisor(engine, "我觉得近月认购偏贵，想找低风险跨期组合", "510300")
print(res)

print(res)
print(getattr(res, "calendar_recommendations", None))
