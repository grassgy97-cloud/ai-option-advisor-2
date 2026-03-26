from fastapi import APIRouter
from app.pricing.factor_engine import compute_and_store_factors, update_term_slope, update_greeks

router = APIRouter()

@router.post("/factor/compute")
def compute_factors():
    insert_result = compute_and_store_factors()
    slope_result = update_term_slope()
    greeks_result = update_greeks()
    return {**insert_result, **slope_result, **greeks_result}