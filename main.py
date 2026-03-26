from fastapi import FastAPI
from app.api.chat import router as chat_router
from app.api.intent import router as intent_router
from app.api.scanner_v2 import router as scanner_router
from app.api.advisor_v2 import router as advisor_router
from app.api.backtest import router as backtest_router
from app.api.factor import router as factor_router

app = FastAPI(title="AI Option Advisor")

app.include_router(chat_router)
app.include_router(intent_router)
app.include_router(scanner_router)
app.include_router(advisor_router)
app.include_router(backtest_router)
app.include_router(factor_router)

@app.get("/")
def root():
    return {"message": "AI Option Advisor is running"}