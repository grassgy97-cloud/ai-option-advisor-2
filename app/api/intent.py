import json
from fastapi import APIRouter
from sqlalchemy import text

from app.ai.intent_parser import parse_natural_language
from app.core.db import SessionLocal
from app.models.schemas import ChatRequest
from app.strategy.router import route_strategies

router = APIRouter()


@router.post("/parse_intent")
def parse_intent(req: ChatRequest):
    parsed = parse_natural_language(req.text)
    parsed_dict = parsed.model_dump()

    session = SessionLocal()
    try:
        insert_request_sql = text("""
            INSERT INTO nl_request (raw_text, parsed_intent_json, status)
            VALUES (:raw_text, CAST(:parsed_intent_json AS jsonb), :status)
            RETURNING request_id;
        """)

        req_row = session.execute(
            insert_request_sql,
            {
                "raw_text": req.text,
                "parsed_intent_json": json.dumps(parsed_dict, ensure_ascii=False),
                "status": "parsed"
            }
        ).fetchone()

        request_id = req_row[0]

        insert_intent_sql = text("""
            INSERT INTO strategy_intent (
                request_id, underlying_id, market_view, vol_view, direction_bias,
                holding_period_days, risk_preference, defined_risk_only,
                prefer_multi_leg, allow_single_leg, strategy_whitelist,
                strategy_blacklist, target_greeks_json, scenario_filters_json, status
            ) VALUES (
                :request_id, :underlying_id, :market_view, :vol_view, :direction_bias,
                :holding_period_days, :risk_preference, :defined_risk_only,
                :prefer_multi_leg, :allow_single_leg, CAST(:strategy_whitelist AS jsonb),
                CAST(:strategy_blacklist AS jsonb), CAST(:target_greeks_json AS jsonb),
                CAST(:scenario_filters_json AS jsonb), :status
            )
            RETURNING intent_id;
        """)

        intent_row = session.execute(
            insert_intent_sql,
            {
                "request_id": request_id,
                "underlying_id": parsed_dict["underlying_id"],
                "market_view": parsed_dict["market_view"],
                "vol_view": parsed_dict["vol_view"],
                "direction_bias": parsed_dict["direction_bias"],
                "holding_period_days": parsed_dict["holding_period_days"],
                "risk_preference": parsed_dict["risk_preference"],
                "defined_risk_only": parsed_dict["defined_risk_only"],
                "prefer_multi_leg": parsed_dict["prefer_multi_leg"],
                "allow_single_leg": parsed_dict["allow_single_leg"],
                "strategy_whitelist": json.dumps(parsed_dict["strategy_whitelist"], ensure_ascii=False),
                "strategy_blacklist": json.dumps(parsed_dict["strategy_blacklist"], ensure_ascii=False),
                "target_greeks_json": json.dumps(parsed_dict["target_greeks_json"], ensure_ascii=False),
                "scenario_filters_json": json.dumps(parsed_dict["scenario_filters_json"], ensure_ascii=False),
                "status": parsed_dict["status"]
            }
        ).fetchone()

        session.commit()

        candidates = route_strategies(parsed_dict)

        return {
            "request_id": request_id,
            "intent_id": intent_row[0],
            "parsed_intent": parsed_dict,
            "strategy_candidates": candidates
        }
    finally:
        session.close()