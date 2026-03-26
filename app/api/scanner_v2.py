from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.db import engine
from app.strategy.scanner_v2 import scan_static


router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("/static")
def run_static_scan(underlying_id: str = "510300"):
    try:
        result = scan_static(engine=engine, underlying_id=underlying_id)
        return {
            "ok": True,
            "data": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/scan/static failed: {e}")