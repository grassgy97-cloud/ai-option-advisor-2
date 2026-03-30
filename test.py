from app.core.db import engine
from app.strategy.advisor_service_v2 import run_advisor
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.briefing import build_briefing

# ── 场景1：近月认购偏贵，单标的 ──
print("=" * 60)
print("【场景1】近月认购偏贵，510300")
res1 = run_advisor(engine, "我觉得近月认购偏贵，想找低风险跨期组合", "510300")
print(f"vol_view: {res1.parsed_intent.vol_view}")
print(f"识别标的: {res1.parsed_intent.underlying_ids}")
for s in (res1.resolved_candidates or [])[:5]:
    print(f"  {s.underlying_id:<10} {s.strategy_type:<20} score={s.score:.4f}")

# ── 场景2：简报 ──
print()
print("【场景1 简报】")
if res1.briefing:
    print(res1.briefing.get("narrative", ""))
    print()
    for row in res1.briefing.get("table", []):
        print(
            f"  #{row['rank']} {row['underlying']} {row['strategy']:<20} "
            f"score={row['score']} {row['cost']}\n"
            f"    legs: {row['legs']}\n"
            f"    IV={row['iv_label']}({row['iv_pct']}) delta={row['net_delta']}"
        )

# ── 场景3：多标的 ──
print()
print("=" * 60)
print("【场景3】多标的测试")
res3 = run_advisor(engine, "沪深300和上证50近月认购都偏贵，想做跨期组合", "510300")
print(f"识别标的: {res3.parsed_intent.underlying_ids}")
print(f"vol_view: {res3.parsed_intent.vol_view}")
for s in (res3.resolved_candidates or [])[:5]:
    print(f"  {s.underlying_id:<10} {s.strategy_type:<20} score={s.score:.4f}")

# ── IV percentile 独立测试 ──
print()
print("=" * 60)
print("【IV percentile】510300")
iv_report = build_iv_percentile_report(engine, "510300")
print(iv_report)