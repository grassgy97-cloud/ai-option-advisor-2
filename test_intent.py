from app.core.db import engine
from app.data.market_context import build_market_context

ctx = build_market_context(engine, "510300")
print(ctx)