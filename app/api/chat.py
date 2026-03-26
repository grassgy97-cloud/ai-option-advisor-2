from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.ai.intent_parser import parse_natural_language
from app.core.db import SessionLocal

router = APIRouter()


class ChatRequest(BaseModel):
    text: str


@router.post("/chat/analyze")
def chat_analyze(req: ChatRequest):
    parsed = parse_natural_language(req.text)

    session = SessionLocal()
    try:
        sql = text("""
            INSERT INTO nl_request (raw_text, parsed_intent_json, status)
            VALUES (:raw_text, CAST(:parsed_intent_json AS jsonb), :status)
            RETURNING request_id, request_time;
        """)

        result = session.execute(
            sql,
            {
                "raw_text": req.text,
                "parsed_intent_json": __import__("json").dumps(parsed.model_dump(), ensure_ascii=False),
                "status": "parsed"
            }
        )
        row = result.fetchone()
        session.commit()

        return {
            "request_id": row[0],
            "request_time": str(row[1]),
            "parsed_intent": parsed.model_dump()
        }
    finally:
        session.close()