from app.core.db import engine
from sqlalchemy import text

with engine.connect() as conn:
    r = conn.execute(text(
        "SELECT underlying_id, COUNT(*) as cnt, MAX(fetch_time) as latest "
        "FROM option_factor_snapshots GROUP BY underlying_id ORDER BY underlying_id"
    )).fetchall()
    for row in r:
        print(row)