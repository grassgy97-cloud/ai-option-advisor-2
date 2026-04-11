from __future__ import annotations

"""
Legacy/manual helper for populating old underlying snapshot tables.

This module is not part of the active advisor data path. It writes to the
legacy `underlying_quote` table and remains only for compatibility/manual use.

The current advisor pipeline uses the newer snapshot-based data flow instead.
"""

from datetime import datetime
from decimal import Decimal
import os

import akshare as ak
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


TARGET_ETFS = {
    "510050": "上证50ETF",
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "588000": "科创50ETF",
    "159901": "深证100ETF",
    "159915": "创业板ETF",
    "159919": "沪深300ETF(深)",
}


def _get_db_url() -> str:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL 未配置")
    return db_url


def _normalize_quote_time(val: object) -> datetime:
    if val is None or str(val).strip() == "":
        return datetime.now().replace(second=0, microsecond=0)
    try:
        return pd.to_datetime(val).to_pydatetime()
    except Exception:
        return datetime.now().replace(second=0, microsecond=0)


def _safe_decimal(val: object) -> Decimal | None:
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def fetch_underlying_snapshot() -> dict:
    """
    Legacy/manual fetch entrypoint for the old `underlying_quote` table.
    """
    df = ak.fund_etf_spot_ths()
    if df is None or df.empty:
        return {"rows_fetched": 0, "rows_written": 0, "message": "AKShare 未返回ETF数据"}

    # 常见字段：基金代码 / 基金名称 / 最新-单位净值 / 查询日期
    code_col = "基金代码"
    name_col = "基金名称"
    price_col = "最新-单位净值"
    time_col = "查询日期"

    missing = [c for c in [code_col, name_col, price_col, time_col] if c not in df.columns]
    if missing:
        raise RuntimeError(f"AKShare返回字段异常，缺少: {missing}; 实际字段: {list(df.columns)}")

    df = df[df[code_col].astype(str).isin(TARGET_ETFS.keys())].copy()
    if df.empty:
        return {"rows_fetched": 0, "rows_written": 0, "message": "目标ETF未命中"}

    db_url = _get_db_url()
    engine = create_engine(db_url)
    rows_written = 0

    upsert_sql = text("""
        INSERT INTO underlying_quote (
            underlying_id,
            quote_time,
            last_price
        ) VALUES (
            :underlying_id,
            :quote_time,
            :last_price
        )
        ON CONFLICT DO NOTHING
    """)

    with engine.begin() as conn:
        for _, row in df.iterrows():
            underlying_id = str(row[code_col]).strip()
            last_price = _safe_decimal(row[price_col])
            quote_time = _normalize_quote_time(row[time_col])

            if not underlying_id or last_price is None:
                continue

            result = conn.execute(
                upsert_sql,
                {
                    "underlying_id": underlying_id,
                    "quote_time": quote_time,
                    "last_price": last_price,
                },
            )
            if result.rowcount and result.rowcount > 0:
                rows_written += 1

    return {
        "rows_fetched": len(df),
        "rows_written": rows_written,
        "quote_time_min": str(df[time_col].min()) if not df.empty else None,
        "quote_time_max": str(df[time_col].max()) if not df.empty else None,
    }


if __name__ == "__main__":
    result = fetch_underlying_snapshot()
    print(result)
