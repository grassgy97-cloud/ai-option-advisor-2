from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.db import engine
from app.strategy.covered_call_service import run_covered_call_scan

router = APIRouter(prefix="/advisor", tags=["covered-call"])


class TargetUpsideRule(BaseModel):
    dte_max: int = Field(..., ge=1, description="该规则适用的最大DTE")
    target_upside_buffer: float = Field(..., ge=0.0, le=1.0, description="该期限下的理想上行保护")


class CoveredCallScanRequest(BaseModel):
    underlying_id: str = Field(..., description="标的代码，如 510300")
    hands: int = Field(..., ge=1, description="持仓手数，1手=10000份")
    dte_min: int = Field(60, ge=1)
    dte_max: int = Field(180, ge=1)
    delta_target: float = Field(0.20, ge=0.01, le=0.99)
    delta_tolerance: float = Field(0.12, ge=0.01, le=0.50)
    max_rel_spread: float = Field(0.05, ge=0.001, le=1.0)
    fee_per_share: float = Field(0.0004, ge=0.0)
    top_n: int = Field(3, ge=1, le=20)
    target_upside_rules: Optional[List[TargetUpsideRule]] = Field(
        None,
        description="按DTE分段的理想上行保护规则",
    )


@router.post("/covered-call")
def covered_call_scan(req: CoveredCallScanRequest) -> Dict[str, Any]:
    """
    Authoritative API surface for the dedicated covered-call scan flow.

    This route intentionally delegates to app.strategy.covered_call_service.
    The generic advisor-path covered_call strategy expression and scoring stay
    in app.strategy.compiler and app.strategy.strategy_ranker.
    """
    rules = [r.model_dump() for r in req.target_upside_rules] if req.target_upside_rules else None

    data = run_covered_call_scan(
        engine=engine,
        underlying_id=req.underlying_id,
        hands=req.hands,
        dte_min=req.dte_min,
        dte_max=req.dte_max,
        delta_target=req.delta_target,
        delta_tolerance=req.delta_tolerance,
        max_rel_spread=req.max_rel_spread,
        fee_per_share=req.fee_per_share,
        top_n=req.top_n,
        target_upside_rules=rules,
    )
    return {"ok": True, "data": data}
