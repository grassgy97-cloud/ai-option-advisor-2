from typing import Optional
from fastapi import APIRouter, Query
from app.strategy.scanner import scan_static_opportunities

router = APIRouter()

@router.post("/scan/static")
def run_static_scan(underlying_id: Optional[str] = Query(default=None)):
    return scan_static_opportunities(underlying_id=underlying_id)