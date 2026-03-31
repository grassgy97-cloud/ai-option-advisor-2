"""
一次性回填历史日K线数据（新浪财经接口）
用法：python scripts/backfill_kline.py
"""
import requests
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
import time

# ---- 配置 ----
DB_URL = "postgresql://postgres:511170@127.0.0.1:5432/ai_option_db"

UNDERLYING_MAP = {
    "510300": "sh510300",
    "510050": "sh510050",
    "510500": "sh510500",
    "588000": "sh588000",
    "588080": "sh588080",
    "159915": "sz159915",
    "159901": "sz159901",
    "159919": "sz159919",
    "159922": "sz159922",
}

SINA_KLINE_URL = (
    "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
    "?symbol={symbol}&scale=240&ma=no&datalen=90"
)

HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0",
}


def fetch_kline(sina_symbol: str) -> list[dict]:
    url = SINA_KLINE_URL.format(symbol=sina_symbol)
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = []
    for row in data:
        try:
            result.append({
                "trade_date": datetime.strptime(row["d"], "%Y-%m-%d").date(),
                "open_price": float(row["o"]),
                "high_price": float(row["h"]),
                "low_price":  float(row["l"]),
                "close_price": float(row["c"]),
                "volume": int(float(row.get("v", 0))),
            })
        except Exception as e:
            print(f"  跳过异常行 {row}: {e}")
    return result


def upsert_kline(conn, underlying_id: str, rows: list[dict]):
    if not rows:
        return 0
    records = [
        (
            underlying_id,
            r["trade_date"],
            r["open_price"],
            r["high_price"],
            r["low_price"],
            r["close_price"],
            r["volume"],
        )
        for r in rows
    ]
    sql = """
        INSERT INTO underlying_daily_kline
            (underlying_id, trade_date, open_price, high_price, low_price, close_price, volume)
        VALUES %s
        ON CONFLICT (underlying_id, trade_date) DO UPDATE SET
            open_price  = EXCLUDED.open_price,
            high_price  = EXCLUDED.high_price,
            low_price   = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price,
            volume      = EXCLUDED.volume
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, records)
    conn.commit()
    return len(records)


def main():
    conn = psycopg2.connect(DB_URL)
    print(f"已连接数据库，开始回填...\n")

    for uid, sina_sym in UNDERLYING_MAP.items():
        print(f"[{uid}] 拉取 {sina_sym} ...")
        try:
            rows = fetch_kline(sina_sym)
            n = upsert_kline(conn, uid, rows)
            print(f"  写入 {n} 条")
        except Exception as e:
            print(f"  失败: {e}")
        time.sleep(0.5)  # 避免请求过快

    conn.close()
    print("\n回填完成。")


if __name__ == "__main__":
    main()