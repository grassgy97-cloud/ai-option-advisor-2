from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any

from app.backtest.engine import run_simple_backtest

router = APIRouter()


class BacktestRequest(BaseModel):
    strategy_type: str
    underlying_id: str
    params: Dict[str, Any] = {}


@router.post("/backtest/run")
def backtest_run(req: BacktestRequest):
    return run_simple_backtest(req.strategy_type, req.underlying_id, req.params)