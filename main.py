from fastapi import FastAPI
from app.api.advisor_v2 import router as advisor_router
from app.api.scanner_v2 import router as scanner_router

app = FastAPI(title="AI Option Advisor")

app.include_router(advisor_router)
app.include_router(scanner_router)

@app.get("/")
def root():
    return {"message": "AI Option Advisor is running"}