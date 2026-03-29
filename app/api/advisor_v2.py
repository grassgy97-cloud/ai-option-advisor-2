from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.strategy.advisor_service_v2 import run_advisor
from app.core.db import engine
from app.models.schemas import AdvisorRunRequest


router = APIRouter(prefix="/advisor", tags=["advisor"])


@router.post("/run")
def advisor_run(req: AdvisorRunRequest):
    try:
        result = run_advisor(
            engine=engine,
            text=req.text,
            underlying_id=req.underlying_id or "510300",
        )
        return {
            "ok": True,
            "data": result.model_dump(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/advisor/run failed: {e}")