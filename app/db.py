from __future__ import annotations

from sqlalchemy.engine import Engine

from app.core.db import SessionLocal, engine, test_connection


def get_engine() -> Engine:
    """
    Legacy compatibility shim.

    The repository's supported DB entrypoints are app.core.config and
    app.core.db. Keep this wrapper only so older imports continue to work
    while sharing the same engine/session objects as the active path.
    """
    return engine
