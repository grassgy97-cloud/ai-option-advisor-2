from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, HTTPException

from app.strategy.advisor_service_v2 import run_advisor
from app.core.db import engine
from app.models.schemas import AdvisorRunRequest
from app.strategy.strategy_ranker import rank_strategies
from app.strategy.briefing import build_briefing
from app.strategy.compiler import compile_intent_to_strategies
from app.strategy.strategy_resolver import resolve_strategy
from app.strategy.iv_percentile import build_iv_percentile_report
from app.strategy.greeks_monitor import build_strategy_greeks_report

router = APIRouter(prefix="/advisor", tags=["advisor"])

ALL_UNDERLYINGS = [
    "510300", "510050", "510500",
    "588000", "588080",
    "159915", "159901", "159919", "159922",
]


def _run_single_forced(uid: str, text: str):
    """
    ALL模式专用：强制锁定标的，跳过LLM标的识别。
    只调LLM做意图解析（market_view/vol_view等），但标的固定为uid。
    """
    try:
        # 先用任意标的跑一次拿到intent
        result = run_advisor(engine=engine, text=text, underlying_id=uid)

        # 强制把intent里的标的覆盖成uid，过滤掉LLM乱识别的其他标的
        candidates = [
            s for s in (result.resolved_candidates or [])
            if s.underlying_id == uid
        ]

        if not candidates:
            return uid, None

        result.resolved_candidates = candidates
        return uid, result

    except Exception as e:
        print(f"[advisor_v2] {uid} failed: {e}")
        return uid, None


@router.post("/run")
def advisor_run(req: AdvisorRunRequest):
    try:
        uid = req.underlying_id or "510300"

        if uid == "ALL":
            all_resolved = []
            base_result = None

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(_run_single_forced, u, req.text): u
                    for u in ALL_UNDERLYINGS
                }
                for future in as_completed(futures):
                    u, result = future.result()
                    if result is None:
                        continue
                    if base_result is None:
                        base_result = result
                    if result.resolved_candidates:
                        all_resolved.extend(result.resolved_candidates)

            if base_result is None:
                raise HTTPException(status_code=500, detail="所有标的均解析失败")

            # 跨标的统一排序，取top10，greeks report已在各自run_advisor里附上
            ranked = rank_strategies(all_resolved)[:10]
            base_result.resolved_candidates = ranked
            base_result.briefing = build_briefing(ranked, req.text)

            return {"ok": True, "data": base_result.model_dump()}

        else:
            result = run_advisor(
                engine=engine,
                text=req.text,
                underlying_id=uid,
            )
            return {"ok": True, "data": result.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/advisor/run failed: {e}")