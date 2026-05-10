from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine


MANUAL_NON_TRADING_DAYS_TABLE = "manual_non_trading_days"


def ensure_manual_non_trading_days_table(engine: Engine) -> None:
    sql = text(
        f"""
        CREATE TABLE IF NOT EXISTS {MANUAL_NON_TRADING_DAYS_TABLE} (
            trade_date DATE PRIMARY KEY,
            reason TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    with engine.begin() as conn:
        conn.execute(sql)


def _parse_date_token(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty date")
    return datetime.strptime(raw, "%Y-%m-%d").date()


def normalize_non_trading_date_tokens(values: Iterable[Any]) -> dict[str, Any]:
    accepted: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[date] = set()

    for value in values:
        try:
            trade_date = _parse_date_token(value)
        except Exception:
            skipped.append({"date": str(value), "reason": "invalid_date"})
            continue
        if trade_date in seen:
            skipped.append({"date": trade_date.isoformat(), "reason": "duplicate"})
            continue
        seen.add(trade_date)
        if trade_date.isoweekday() >= 6:
            skipped.append({"date": trade_date.isoformat(), "reason": "weekend_not_needed"})
            continue
        accepted.append(trade_date.isoformat())

    return {"accepted_dates": accepted, "skipped": skipped}


def upsert_manual_non_trading_days(
    engine: Engine,
    dates: Iterable[Any],
    reason: str | None = None,
) -> dict[str, Any]:
    ensure_manual_non_trading_days_table(engine)
    normalized = normalize_non_trading_date_tokens(dates)
    accepted = normalized["accepted_dates"]
    if not accepted:
        return {
            "inserted_or_updated": [],
            "skipped": normalized["skipped"],
            "count": 0,
        }

    sql = text(
        f"""
        INSERT INTO {MANUAL_NON_TRADING_DAYS_TABLE} (trade_date, reason, source)
        VALUES (:trade_date, :reason, 'manual')
        ON CONFLICT (trade_date) DO UPDATE
        SET reason = EXCLUDED.reason,
            source = 'manual',
            updated_at = NOW()
        """
    )
    with engine.begin() as conn:
        for item in accepted:
            conn.execute(sql, {"trade_date": item, "reason": reason})

    return {
        "inserted_or_updated": accepted,
        "skipped": normalized["skipped"],
        "count": len(accepted),
    }


def list_manual_non_trading_days(
    engine: Engine,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    ensure_manual_non_trading_days_table(engine)
    clauses = []
    params: dict[str, Any] = {}
    if start_date:
        clauses.append("trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("trade_date <= :end_date")
        params["end_date"] = end_date
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = text(
        f"""
        SELECT trade_date, reason, source, created_at, updated_at
        FROM {MANUAL_NON_TRADING_DAYS_TABLE}
        {where}
        ORDER BY trade_date DESC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [
        {
            "trade_date": row["trade_date"].isoformat() if hasattr(row["trade_date"], "isoformat") else str(row["trade_date"]),
            "reason": row.get("reason"),
            "source": row.get("source"),
            "created_at": row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else str(row.get("created_at")),
            "updated_at": row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else str(row.get("updated_at")),
        }
        for row in rows
    ]


def delete_manual_non_trading_day(engine: Engine, trade_date: str) -> dict[str, Any]:
    ensure_manual_non_trading_days_table(engine)
    parsed = _parse_date_token(trade_date)
    sql = text(
        f"""
        DELETE FROM {MANUAL_NON_TRADING_DAYS_TABLE}
        WHERE trade_date = :trade_date
        """
    )
    with engine.begin() as conn:
        result = conn.execute(sql, {"trade_date": parsed.isoformat()})
    return {"trade_date": parsed.isoformat(), "deleted": int(result.rowcount or 0)}
