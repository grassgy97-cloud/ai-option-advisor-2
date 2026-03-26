from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


_ENGINE: Engine | None = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL is not set")
        _ENGINE = create_engine(db_url, pool_pre_ping=True)
    return _ENGINE