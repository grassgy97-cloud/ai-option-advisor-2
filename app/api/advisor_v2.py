from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from app.core.db import engine
from app.models.schemas import AdvisorRunRequest
from app.strategy.advisor_service_v2 import run_advisor


router = APIRouter(prefix="/advisor", tags=["advisor"])


@router.post("/run")
def advisor_run(req: AdvisorRunRequest):
    try:
        print(
            "[multi_run_check] "
            f"api_request underlying_id={req.underlying_id} "
            f"underlying_ids={req.underlying_ids}"
        )
        result = run_advisor(
            engine=engine,
            text=req.text,
            underlying_id=req.underlying_id or "510300",
            underlying_ids=req.underlying_ids,
        )
        return {"ok": True, "data": result.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/advisor/run failed: {e}")
