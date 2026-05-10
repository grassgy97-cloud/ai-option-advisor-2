from __future__ import annotations

import traceback
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import engine
from app.data.trading_calendar import (
    delete_manual_non_trading_day,
    list_manual_non_trading_days,
    upsert_manual_non_trading_days,
)


router = APIRouter(prefix="/admin/calendar", tags=["admin-calendar"])


class NonTradingDaysUpsertRequest(BaseModel):
    dates: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


@router.get("/non-trading-days")
def get_non_trading_days(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
):
    try:
        return {
            "ok": True,
            "data": list_manual_non_trading_days(
                engine,
                start_date=start_date,
                end_date=end_date,
            ),
        }
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/admin/calendar/non-trading-days failed: {exc}")


@router.post("/non-trading-days")
def upsert_non_trading_days(req: NonTradingDaysUpsertRequest):
    try:
        return {
            "ok": True,
            "data": upsert_manual_non_trading_days(engine, req.dates, reason=req.reason),
        }
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/admin/calendar/non-trading-days failed: {exc}")


@router.delete("/non-trading-days/{trade_date}")
def delete_non_trading_day(trade_date: str):
    try:
        return {
            "ok": True,
            "data": delete_manual_non_trading_day(engine, trade_date),
        }
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"/admin/calendar/non-trading-days/{trade_date} failed: {exc}")
