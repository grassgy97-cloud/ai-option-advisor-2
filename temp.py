from __future__ import annotations

import json
import time

from app.core.db import engine
from app.strategy.advisor_service_v2 import run_advisor


def main():
    cases = [
        {
            "text": "我对510300轻微看空，认购偏贵，想做有限风险的多腿策略，期限一个月左右",
            "underlying_id": "510300",
        },
        {
            "text": "我对510300轻微看空，认购偏贵，想做有限风险的多腿策略，期限一个月左右",
            "underlying_id": "ALL",
        },
    ]

    for i, case in enumerate(cases, 1):
        print("\n" + "=" * 80)
        print(f"[case {i}] underlying_id={case['underlying_id']}")
        print(f"[case {i}] text={case['text']}")

        t0 = time.perf_counter()
        resp = run_advisor(
            engine=engine,
            text=case["text"],
            underlying_id=case["underlying_id"],
        )
        elapsed = time.perf_counter() - t0

        print(f"[case {i}] total elapsed = {elapsed:.3f}s")
        print(f"[case {i}] resolved_candidates = {len(resp.resolved_candidates)}")

        top = []
        for s in resp.resolved_candidates[:5]:
            top.append({
                "strategy_type": s.strategy_type,
                "underlying_id": s.underlying_id,
                "score": s.score,
                "net_credit": s.net_credit,
                "net_debit": s.net_debit,
            })

        print("[case top5]")
        print(json.dumps(top, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()