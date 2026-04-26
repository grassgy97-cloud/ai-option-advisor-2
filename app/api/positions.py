from __future__ import annotations

import traceback
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.db import engine
from app.models.schemas import PositionLegUpsertRequest
from app.strategy.positions_service import delete_position_leg, get_contract_meta, list_position_legs, upsert_position_leg


router = APIRouter(prefix="/positions", tags=["positions"])


@router.post("/upsert-leg")
def upsert_leg(req: PositionLegUpsertRequest):
    try:
        result = upsert_position_leg(engine, req)
        return {"ok": True, "data": result.model_dump()}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/positions/upsert-leg failed: {exc}")


@router.get("/legs")
def get_legs(
    underlying_id: Optional[str] = None,
    status: Optional[str] = Query(default="OPEN"),
):
    try:
        legs = list_position_legs(engine, underlying_id=underlying_id, status=status)
        return {"ok": True, "data": [leg.model_dump() for leg in legs]}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/positions/legs failed: {exc}")


@router.delete("/legs/{leg_id}")
def delete_leg(leg_id: int):
    try:
        return {"ok": True, "data": delete_position_leg(engine, leg_id)}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/positions/legs/{leg_id} failed: {exc}")


@router.get("/enrich")
def enrich_contract(contract_id: str = Query(..., min_length=1)):
    try:
        result = get_contract_meta(engine, contract_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"contract_id not found: {contract_id}")
        return {"ok": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/positions/enrich failed: {exc}")


@router.get("/contract-meta/{contract_id}")
def contract_meta(contract_id: str):
    try:
        result = get_contract_meta(engine, contract_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"contract_id not found: {contract_id}")
        return {"ok": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/positions/contract-meta/{contract_id} failed: {exc}")
