from fastapi import APIRouter
from app.models.schemas import ChatRequest
from app.ai.advisor_service import run_advisor

router = APIRouter()


@router.post("/advisor/run")
def advisor_run(req: ChatRequest):
    return run_advisor(req.text)