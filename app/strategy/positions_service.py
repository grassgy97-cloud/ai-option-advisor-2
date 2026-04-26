from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.schemas import PositionLegRecord, PositionLegUpsertRequest, PositionLegUpsertResponse


def _row_to_leg(row: Any) -> PositionLegRecord:
    data = dict(row)
    return PositionLegRecord(
        leg_id=int(data["leg_id"]),
        underlying_id=str(data["underlying_id"]),
        contract_id=str(data["contract_id"]),
        option_type=str(data["option_type"]),
        strike=float(data["strike"]),
        expiry_date=str(data["expiry_date"]),
        side=str(data["side"]),
        quantity=int(data["quantity"]),
        avg_entry_price=float(data["avg_entry_price"]),
        strategy_bucket=data.get("strategy_bucket"),
        group_id=data.get("group_id"),
        tag=data.get("tag"),
        include_in_portfolio_greeks=bool(data.get("include_in_portfolio_greeks")),
        status=str(data["status"]),
        opened_at=str(data["opened_at"]) if data.get("opened_at") is not None else None,
        updated_at=str(data["updated_at"]) if data.get("updated_at") is not None else None,
        note=data.get("note"),
    )


def _trade_action(old_quantity: int, new_quantity: int) -> str:
    delta = new_quantity - old_quantity
    if new_quantity == 0 and old_quantity > 0:
        return "CLOSE"
    if delta > 0:
        return "ADD"
    if delta < 0:
        return "REDUCE"
    return "UPDATE"


def _normalize_option_type(value: Any) -> str:
    option_type = str(value).upper()
    if option_type == "C":
        return "CALL"
    if option_type == "P":
        return "PUT"
    return option_type


def get_contract_meta(engine: Engine, contract_id: str) -> Optional[dict[str, Any]]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    contract_id,
                    underlying_id,
                    option_type,
                    strike,
                    expiry_date,
                    dte_calendar,
                    fetch_time
                FROM option_factor_snapshots
                WHERE contract_id = :contract_id
                ORDER BY fetch_time DESC
                LIMIT 1
                """
            ),
            {"contract_id": contract_id},
        ).mappings().first()

        if row is None:
            quote_columns = set(
                conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'option_quote_snapshots'
                        """
                    )
                ).scalars().all()
            )
            quote_meta_columns = {"contract_id", "underlying_id", "option_type", "strike", "expiry_date", "fetch_time"}
        if row is None and quote_meta_columns.issubset(quote_columns):
            row = conn.execute(
                text(
                    """
                    SELECT
                        contract_id,
                        underlying_id,
                        option_type,
                        strike,
                        expiry_date,
                        NULL AS dte_calendar,
                        fetch_time
                    FROM option_quote_snapshots
                    WHERE contract_id = :contract_id
                    ORDER BY fetch_time DESC
                    LIMIT 1
                    """
                ),
                {"contract_id": contract_id},
            ).mappings().first()

    if row is None:
        return None

    return {
        "contract_id": str(row["contract_id"]),
        "underlying_id": str(row["underlying_id"]),
        "option_type": _normalize_option_type(row["option_type"]),
        "strike": float(row["strike"]),
        "expiry_date": str(row["expiry_date"]),
        "dte_calendar": int(row["dte_calendar"]) if row.get("dte_calendar") is not None else None,
        "fetch_time": str(row["fetch_time"]) if row.get("fetch_time") is not None else None,
    }


def _enrich_upsert_request(engine: Engine, req: PositionLegUpsertRequest) -> PositionLegUpsertRequest:
    meta = get_contract_meta(engine, req.contract_id) if req.contract_id else None
    merged = req.model_dump()
    if meta:
        for key in ("underlying_id", "option_type", "strike", "expiry_date"):
            merged[key] = meta.get(key)

    missing = [key for key in ("underlying_id", "option_type", "strike", "expiry_date") if merged.get(key) in (None, "")]
    if missing:
        raise ValueError(f"missing required contract fields after enrichment: {', '.join(missing)}")
    return PositionLegUpsertRequest(**merged)


def upsert_position_leg(engine: Engine, req: PositionLegUpsertRequest) -> PositionLegUpsertResponse:
    req = _enrich_upsert_request(engine, req)
    new_quantity = max(int(req.quantity), 0)
    status = "CLOSED" if new_quantity == 0 else "OPEN"

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT *
                FROM position_legs
                WHERE underlying_id = :underlying_id
                  AND contract_id = :contract_id
                  AND side = :side
                ORDER BY leg_id DESC
                LIMIT 1
                FOR UPDATE
                """
            ),
            {
                "underlying_id": req.underlying_id,
                "contract_id": req.contract_id,
                "side": req.side,
            },
        ).mappings().first()

        old_quantity = int(existing["quantity"]) if existing else 0
        quantity_delta = new_quantity - old_quantity
        action = _trade_action(old_quantity, new_quantity)

        params = {
            "underlying_id": req.underlying_id,
            "contract_id": req.contract_id,
            "option_type": req.option_type,
            "strike": req.strike,
            "expiry_date": req.expiry_date,
            "side": req.side,
            "quantity": new_quantity,
            "avg_entry_price": req.avg_entry_price,
            "strategy_bucket": req.strategy_bucket,
            "group_id": req.group_id,
            "tag": req.tag,
            "include_in_portfolio_greeks": req.include_in_portfolio_greeks,
            "status": status,
            "note": req.note,
        }

        if existing:
            leg_id = int(existing["leg_id"])
            row = conn.execute(
                text(
                    """
                    UPDATE position_legs
                    SET option_type = :option_type,
                        strike = :strike,
                        expiry_date = :expiry_date,
                        quantity = :quantity,
                        avg_entry_price = :avg_entry_price,
                        strategy_bucket = :strategy_bucket,
                        group_id = :group_id,
                        tag = :tag,
                        include_in_portfolio_greeks = :include_in_portfolio_greeks,
                        status = :status,
                        updated_at = now(),
                        note = :note
                    WHERE leg_id = :leg_id
                    RETURNING *
                    """
                ),
                {**params, "leg_id": leg_id},
            ).mappings().one()
        else:
            row = conn.execute(
                text(
                    """
                    INSERT INTO position_legs (
                        underlying_id, contract_id, option_type, strike, expiry_date, side,
                        quantity, avg_entry_price, strategy_bucket, group_id, tag,
                        include_in_portfolio_greeks, status, note
                    )
                    VALUES (
                        :underlying_id, :contract_id, :option_type, :strike, :expiry_date, :side,
                        :quantity, :avg_entry_price, :strategy_bucket, :group_id, :tag,
                        :include_in_portfolio_greeks, :status, :note
                    )
                    RETURNING *
                    """
                ),
                params,
            ).mappings().one()
            leg_id = int(row["leg_id"])

        trade_row = conn.execute(
            text(
                """
                INSERT INTO position_leg_trades (
                    leg_id, action, quantity_delta, trade_price, fee_rmb, reason
                )
                VALUES (
                    :leg_id, :action, :quantity_delta, :trade_price, :fee_rmb, :reason
                )
                RETURNING trade_id, leg_id, trade_time, action, quantity_delta, trade_price, fee_rmb, reason
                """
            ),
            {
                "leg_id": leg_id,
                "action": action,
                "quantity_delta": quantity_delta,
                "trade_price": req.avg_entry_price,
                "fee_rmb": req.fee_rmb,
                "reason": req.reason or "manual_position_upsert",
            },
        ).mappings().one()

    return PositionLegUpsertResponse(leg=_row_to_leg(row), trade=dict(trade_row))


def list_position_legs(
    engine: Engine,
    underlying_id: Optional[str] = None,
    status: Optional[str] = "OPEN",
) -> list[PositionLegRecord]:
    where = []
    params: dict[str, Any] = {}
    if underlying_id:
        where.append("underlying_id = :underlying_id")
        params["underlying_id"] = underlying_id
    if status:
        where.append("status = :status")
        params["status"] = status
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT *
                FROM position_legs
                {where_sql}
                ORDER BY underlying_id, expiry_date, option_type, strike, side, leg_id
                """
            ),
            params,
        ).mappings().all()
    return [_row_to_leg(row) for row in rows]


def delete_position_leg(engine: Engine, leg_id: int) -> dict[str, Any]:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM position_leg_trades WHERE leg_id = :leg_id"), {"leg_id": leg_id})
        result = conn.execute(text("DELETE FROM position_legs WHERE leg_id = :leg_id"), {"leg_id": leg_id})
    return {
        "leg_id": leg_id,
        "deleted": result.rowcount > 0,
        "note": "Deleted as manual cleanup; this is not a normal trade semantic.",
    }
