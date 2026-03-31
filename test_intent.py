from app.strategy.advisor_service_v2 import run_advisor
from app.core.db import engine

result = run_advisor(
    engine=engine,
    text="沪深300下行空间较大，下方4.2有支撑保底，想做熊市价差或裸卖put",
    underlying_id="510300"
)

# 看选腿结果
for s in result.resolved_candidates[:5]:
    print(f"\n{s.strategy_type}  score={s.score:.4f}  spot={s.spot_price}")
    for i, leg in enumerate(s.legs):
        print(f"  腿{i+1} {leg.action} {leg.option_type} "
              f"strike={leg.strike} delta={leg.delta:.3f} "
              f"strike_forced={leg.strike_forced}")