from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from app.core.db import engine
from app.ai.llm_commentary import build_monitoring_llm_commentary
from app.models.schemas import PositionMonitorRequest
from app.strategy.position_monitor import monitor_position, monitor_underlying_positions


router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.post("/position")
def monitor_position_endpoint(req: PositionMonitorRequest):
    try:
        result = monitor_position(engine=engine, position=req)
        data = result.model_dump()
        commentary = build_monitoring_llm_commentary(data)
        data["llm_commentary"] = commentary
        data["monitoring_llm_commentary"] = commentary
        return {"ok": True, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/monitor/position failed: {exc}")


@router.get("/underlying/{underlying_id}")
def monitor_underlying_endpoint(underlying_id: str):
    try:
        result = monitor_underlying_positions(engine=engine, underlying_id=underlying_id)
        data = result.model_dump()
        commentary = build_monitoring_llm_commentary(data)
        data["llm_commentary"] = commentary
        data["monitoring_llm_commentary"] = commentary
        return {"ok": True, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/monitor/underlying/{underlying_id} failed: {exc}")
