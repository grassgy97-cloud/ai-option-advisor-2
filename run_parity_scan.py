import argparse
import csv
import json
import math
import time
from collections import defaultdict
from datetime import datetime, date

import requests
import psycopg2
from psycopg2.extras import execute_batch

HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "ai_option_db",
    "user": "postgres",
    "password": "511170"
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

def safe_float(x):
    try:
        x = (x or "").strip()
        if x == "":
            return None
        return float(x)
    except:
        return None

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def bs_price(S, K, T, r, sigma, option_type):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "C":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def bs_greeks(S, K, T, r, sigma, option_type):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf = norm_pdf(d1)

    if option_type == "C":
        delta = norm_cdf(d1)
        theta = (-S * pdf * sigma / (2 * sqrtT) - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0
    else:
        delta = norm_cdf(d1) - 1.0
        theta = (-S * pdf * sigma / (2 * sqrtT) + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365.0

    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT / 100.0

    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta, 6),
        "vega": round(vega, 6),
    }

def solve_iv_bisection(price, S, K, T, r, option_type, low=1e-4, high=3.0, tol=1e-6, max_iter=100):
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    intrinsic = max(S - K * math.exp(-r * T), 0.0) if option_type == "C" else max(K * math.exp(-r * T) - S, 0.0)
    if price < intrinsic - 1e-6:
        return None

    pl = bs_price(S, K, T, r, low, option_type)
    ph = bs_price(S, K, T, r, high, option_type)
    if pl is None or ph is None:
        return None
    if price < pl or price > ph:
        return None

    l, h = low, high
    for _ in range(max_iter):
        m = (l + h) / 2.0
        pm = bs_price(S, K, T, r, m, option_type)
        if pm is None:
            return None
        if abs(pm - price) < tol:
            return round(m, 6)
        if pm > price:
            h = m
        else:
            l = m
    return round((l + h) / 2.0, 6)

def load_contracts(path="contracts_active.json"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    today = date.today().isoformat()
    return {k: v for k, v in data.items() if v.get("expiry_date") and v["expiry_date"] >= today}

def parse_option_line(line, contract_map, fetch_time_str):
    line = line.strip()
    if not line or "CON_OP_" not in line or '""' in line:
        return None
    try:
        code = line.split("CON_OP_")[1].split("=")[0].strip()
        if code not in contract_map:
            return None

        data = line.split('"')[1]
        fields = data.split(",")
        if len(fields) <= 46:
            return None

        contract = contract_map[code]
        bid = safe_float(fields[2])
        ask = safe_float(fields[3])

        rec = {
            "fetch_time": fetch_time_str,
            "contract_id": code,
            "underlying_id": contract.get("underlying"),
            "exchange": contract.get("exchange"),
            "name": contract.get("name"),
            "option_type": contract.get("option_type"),
            "strike": contract.get("strike"),
            "expiry_date": contract.get("expiry_date"),
            "last_price": safe_float(fields[1]),
            "bid_price1": bid,
            "ask_price1": ask,
            "bid_vol1": safe_float(fields[4]),
            "ask_vol1": safe_float(fields[5]),
            "pct_change": safe_float(fields[6]),
            "pre_settle": safe_float(fields[8]),
            "pre_close": safe_float(fields[9]),
        }

        if bid is not None and ask is not None and bid > 0 and ask > 0:
            rec["mid_price"] = round((bid + ask) / 2, 6)
            rec["spread"] = round(ask - bid, 6)
            rec["rel_spread"] = round((ask - bid) / ((bid + ask) / 2), 6) if (bid + ask) > 0 else None
        else:
            rec["mid_price"] = None
            rec["spread"] = None
            rec["rel_spread"] = None

        rec["is_quote_valid"] = bool(
            (rec["bid_price1"] is not None and rec["bid_price1"] > 0) or
            (rec["ask_price1"] is not None and rec["ask_price1"] > 0) or
            (rec["last_price"] is not None and rec["last_price"] > 0)
        )
        rec["is_crossed"] = bool(
            rec["bid_price1"] is not None and
            rec["ask_price1"] is not None and
            rec["bid_price1"] > rec["ask_price1"]
        )
        rec["is_usable_for_scan"] = bool(
            rec["is_quote_valid"] and
            not rec["is_crossed"] and
            rec["bid_price1"] is not None and rec["bid_price1"] > 0 and
            rec["ask_price1"] is not None and rec["ask_price1"] > 0
        )
        return rec
    except:
        return None

def fetch_option_quotes(contract_map, batch_size=80, sleep_sec=0.2):
    codes = sorted(contract_map.keys())
    fetch_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for batch_codes in chunked(codes, batch_size):
        sina_codes = ",".join([f"CON_OP_{c}" for c in batch_codes])
        url = f"https://hq.sinajs.cn/list={sina_codes}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                rec = parse_option_line(line, contract_map, fetch_time_str)
                if rec is not None:
                    rows.append(rec)
        except Exception as e:
            print(f"[ERROR] option batch fetch failed: {e}")
        time.sleep(sleep_sec)
    return rows, fetch_time_str

def market_prefix(code):
    code = str(code)
    if code.startswith(("5", "6")):
        return "sh"
    return "sz"

def fetch_underlyings(contract_map, fetch_time_str):
    underlyings = sorted({v["underlying"] for v in contract_map.values()})
    symbols = [market_prefix(u) + u for u in underlyings]
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)

    r = requests.get(url, headers=HEADERS, timeout=10)
    r.encoding = "gbk"

    rows = []
    for line in r.text.strip().split("\n"):
        if 'hq_str_' not in line or '""' in line:
            continue
        left = line.split("=")[0]
        symbol = left.split("hq_str_")[1]
        data = line.split('"')[1]
        f = data.split(",")

        rows.append({
            "fetch_time": fetch_time_str,
            "symbol": symbol,
            "underlying_id": symbol[2:],
            "name": f[0].strip() if len(f) > 0 else None,
            "open_price": safe_float(f[1]) if len(f) > 1 else None,
            "pre_close": safe_float(f[2]) if len(f) > 2 else None,
            "last_price": safe_float(f[3]) if len(f) > 3 else None,
            "high_price": safe_float(f[4]) if len(f) > 4 else None,
            "low_price": safe_float(f[5]) if len(f) > 5 else None,
            "bid_price1": safe_float(f[6]) if len(f) > 6 else None,
            "ask_price1": safe_float(f[7]) if len(f) > 7 else None,
        })
    return rows

def build_pairs(option_rows):
    usable = [x for x in option_rows if x.get("is_usable_for_scan")]
    grouped = defaultdict(dict)
    for x in usable:
        key = (x.get("underlying_id"), x.get("expiry_date"), str(x.get("strike")))
        opt_type = x.get("option_type")
        if opt_type in ("C", "P"):
            grouped[key][opt_type] = x

    pairs = []
    for key, d in grouped.items():
        if "C" in d and "P" in d:
            c = d["C"]
            p = d["P"]
            pairs.append({
                "fetch_time": c["fetch_time"],
                "underlying_id": key[0],
                "expiry_date": key[1],
                "strike": float(key[2]),
                "call_contract_id": c["contract_id"],
                "put_contract_id": p["contract_id"],
                "call_bid": c.get("bid_price1"),
                "call_ask": c.get("ask_price1"),
                "put_bid": p.get("bid_price1"),
                "put_ask": p.get("ask_price1"),
                "call_mid": c.get("mid_price"),
                "put_mid": p.get("mid_price"),
                "call_bid_vol1": c.get("bid_vol1"),
                "call_ask_vol1": c.get("ask_vol1"),
                "put_bid_vol1": p.get("bid_vol1"),
                "put_ask_vol1": p.get("ask_vol1"),
                "call_rel_spread": c.get("rel_spread"),
                "put_rel_spread": p.get("rel_spread"),
            })
    return pairs

def calc_parity(pairs, underlying_rows):
    spot_map = {x["underlying_id"]: x.get("last_price") for x in underlying_rows}
    out = []
    for x in pairs:
        spot = spot_map.get(x["underlying_id"])
        strike = x.get("strike")
        call_mid = x.get("call_mid")
        put_mid = x.get("put_mid")
        call_ask = x.get("call_ask")
        call_bid = x.get("call_bid")
        put_ask = x.get("put_ask")
        put_bid = x.get("put_bid")

        if strike is None or spot is None:
            continue

        y = dict(x)
        y["spot_price"] = spot

        if call_mid is not None and put_mid is not None:
            syn_mid = round(call_mid - put_mid + strike, 6)
            y["synthetic_forward_mid"] = syn_mid
            y["parity_deviation_mid"] = round(syn_mid - spot, 6)
        else:
            y["synthetic_forward_mid"] = None
            y["parity_deviation_mid"] = None

        if call_ask is not None and put_bid is not None:
            syn_buy = round(call_ask - put_bid + strike, 6)
            y["synthetic_forward_buy"] = syn_buy
            y["parity_deviation_buy"] = round(syn_buy - spot, 6)
        else:
            y["synthetic_forward_buy"] = None
            y["parity_deviation_buy"] = None

        if call_bid is not None and put_ask is not None:
            syn_sell = round(call_bid - put_ask + strike, 6)
            y["synthetic_forward_sell"] = syn_sell
            y["parity_deviation_sell"] = round(syn_sell - spot, 6)
        else:
            y["synthetic_forward_sell"] = None
            y["parity_deviation_sell"] = None

        out.append(y)

    out.sort(key=lambda z: abs(z["parity_deviation_mid"]) if z["parity_deviation_mid"] is not None else -1, reverse=True)
    return out

def build_trade_candidates(parity_rows, min_abs_dev=0.02, max_rel_spread=0.05, min_quote_vol=1):
    out = []
    for x in parity_rows:
        cbv = x.get("call_bid_vol1")
        cav = x.get("call_ask_vol1")
        pbv = x.get("put_bid_vol1")
        pav = x.get("put_ask_vol1")
        crs = x.get("call_rel_spread")
        prs = x.get("put_rel_spread")

        if any(v is None for v in [cbv, cav, pbv, pav, crs, prs]):
            continue
        if min(cbv, cav, pbv, pav) < min_quote_vol:
            continue
        if crs > max_rel_spread or prs > max_rel_spread:
            continue

        buy_dev = x.get("parity_deviation_buy")
        sell_dev = x.get("parity_deviation_sell")

        if buy_dev is not None and abs(buy_dev) >= min_abs_dev:
            y = dict(x)
            y["signal_side"] = "BUY_SYNTHETIC"
            y["signal_value"] = buy_dev
            out.append(y)

        if sell_dev is not None and abs(sell_dev) >= min_abs_dev:
            y = dict(x)
            y["signal_side"] = "SELL_SYNTHETIC"
            y["signal_value"] = sell_dev
            out.append(y)

    out.sort(key=lambda z: abs(z["signal_value"]), reverse=True)
    return out

def build_option_factors(option_rows, underlying_rows, rf_rate):
    spot_map = {x["underlying_id"]: x.get("last_price") for x in underlying_rows}
    factor_rows = []

    for x in option_rows:
        if not x.get("is_usable_for_scan"):
            continue

        S = spot_map.get(x["underlying_id"])
        K = x.get("strike")
        option_type = x.get("option_type")
        price = x.get("mid_price")
        expiry_str = x.get("expiry_date")
        fetch_time_str = x.get("fetch_time")

        if S is None or K is None or price is None or option_type not in ("C", "P") or not expiry_str:
            continue

        try:
            exp_d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            fetch_d = datetime.strptime(fetch_time_str, "%Y-%m-%d %H:%M:%S").date()
        except:
            continue

        dte_calendar = (exp_d - fetch_d).days
        t_years = max(dte_calendar / 365.0, 1e-6)

        iv = solve_iv_bisection(price, S, K, t_years, rf_rate, option_type)
        if iv is None:
            continue

        greeks = bs_greeks(S, K, t_years, rf_rate, iv, option_type)
        if greeks is None:
            continue

        factor_rows.append({
            "fetch_time": fetch_time_str,
            "contract_id": x["contract_id"],
            "underlying_id": x["underlying_id"],
            "option_type": option_type,
            "expiry_date": expiry_str,
            "strike": K,
            "spot_price": S,
            "option_market_price": price,
            "pricing_basis": "mid",
            "dte_calendar": float(dte_calendar),
            "t_years": round(t_years, 8),
            "rf_rate": rf_rate,
            "implied_vol": iv,
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "vega": greeks["vega"],
        })

    return factor_rows

def insert_option_quotes(conn, rows):
    sql = """
    INSERT INTO option_quote_snapshots (
        fetch_time, contract_id, underlying_id, exchange, name, option_type, strike, expiry_date,
        last_price, bid_price1, ask_price1, bid_vol1, ask_vol1, pct_change, pre_settle, pre_close,
        mid_price, spread, is_quote_valid, is_crossed, is_usable_for_scan
    ) VALUES (
        %(fetch_time)s, %(contract_id)s, %(underlying_id)s, %(exchange)s, %(name)s, %(option_type)s, %(strike)s, %(expiry_date)s,
        %(last_price)s, %(bid_price1)s, %(ask_price1)s, %(bid_vol1)s, %(ask_vol1)s, %(pct_change)s, %(pre_settle)s, %(pre_close)s,
        %(mid_price)s, %(spread)s, %(is_quote_valid)s, %(is_crossed)s, %(is_usable_for_scan)s
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=500)

def insert_underlyings(conn, rows):
    sql = """
    INSERT INTO underlying_quote_snapshots (
        fetch_time, symbol, underlying_id, name, open_price, pre_close, last_price,
        high_price, low_price, bid_price1, ask_price1
    ) VALUES (
        %(fetch_time)s, %(symbol)s, %(underlying_id)s, %(name)s, %(open_price)s, %(pre_close)s, %(last_price)s,
        %(high_price)s, %(low_price)s, %(bid_price1)s, %(ask_price1)s
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=200)

def append_today_kline(conn, underlying_rows):
    """每日15:05收盘后追加当日K线，上午采样跳过"""
    if datetime.now().hour < 14:
        print("上午采样，跳过K线写入")
        return

    sql = """
        INSERT INTO underlying_daily_kline
            (underlying_id, trade_date, open_price, high_price, low_price, close_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (underlying_id, trade_date) DO UPDATE SET
            open_price  = EXCLUDED.open_price,
            high_price  = EXCLUDED.high_price,
            low_price   = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price
    """
    today = date.today()
    records = []
    for row in underlying_rows:
        uid = row.get("underlying_id")
        open_p = row.get("open_price")
        high_p = row.get("high_price")
        low_p  = row.get("low_price")
        last_p = row.get("last_price")
        if uid and last_p:
            records.append((uid, today, open_p, high_p, low_p, last_p))

    if records:
        with conn.cursor() as cur:
            cur.executemany(sql, records)
        print(f"K线写入 {len(records)} 条")

def insert_parity(conn, rows):
    sql = """
    INSERT INTO parity_scan_results (
        fetch_time, underlying_id, expiry_date, strike, call_contract_id, put_contract_id,
        spot_price, call_bid, call_ask, put_bid, put_ask, call_mid, put_mid,
        synthetic_forward_mid, parity_deviation_mid,
        synthetic_forward_buy, parity_deviation_buy,
        synthetic_forward_sell, parity_deviation_sell
    ) VALUES (
        %(fetch_time)s, %(underlying_id)s, %(expiry_date)s, %(strike)s, %(call_contract_id)s, %(put_contract_id)s,
        %(spot_price)s, %(call_bid)s, %(call_ask)s, %(put_bid)s, %(put_ask)s, %(call_mid)s, %(put_mid)s,
        %(synthetic_forward_mid)s, %(parity_deviation_mid)s,
        %(synthetic_forward_buy)s, %(parity_deviation_buy)s,
        %(synthetic_forward_sell)s, %(parity_deviation_sell)s
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=500)

def insert_candidates(conn, rows):
    if not rows:
        return
    sql = """
    INSERT INTO parity_trade_candidates (
        fetch_time, underlying_id, expiry_date, strike, call_contract_id, put_contract_id,
        spot_price, call_bid, call_ask, put_bid, put_ask, call_mid, put_mid,
        call_bid_vol1, call_ask_vol1, put_bid_vol1, put_ask_vol1,
        call_rel_spread, put_rel_spread,
        synthetic_forward_mid, parity_deviation_mid,
        synthetic_forward_buy, parity_deviation_buy,
        synthetic_forward_sell, parity_deviation_sell,
        signal_side, signal_value
    ) VALUES (
        %(fetch_time)s, %(underlying_id)s, %(expiry_date)s, %(strike)s, %(call_contract_id)s, %(put_contract_id)s,
        %(spot_price)s, %(call_bid)s, %(call_ask)s, %(put_bid)s, %(put_ask)s, %(call_mid)s, %(put_mid)s,
        %(call_bid_vol1)s, %(call_ask_vol1)s, %(put_bid_vol1)s, %(put_ask_vol1)s,
        %(call_rel_spread)s, %(put_rel_spread)s,
        %(synthetic_forward_mid)s, %(parity_deviation_mid)s,
        %(synthetic_forward_buy)s, %(parity_deviation_buy)s,
        %(synthetic_forward_sell)s, %(parity_deviation_sell)s,
        %(signal_side)s, %(signal_value)s
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=500)

def insert_factors(conn, rows):
    if not rows:
        return
    sql = """
    INSERT INTO option_factor_snapshots (
        fetch_time, contract_id, underlying_id, option_type, expiry_date, strike,
        spot_price, option_market_price, pricing_basis,
        dte_calendar, t_years, rf_rate, implied_vol,
        delta, gamma, theta, vega
    ) VALUES (
        %(fetch_time)s, %(contract_id)s, %(underlying_id)s, %(option_type)s, %(expiry_date)s, %(strike)s,
        %(spot_price)s, %(option_market_price)s, %(pricing_basis)s,
        %(dte_calendar)s, %(t_years)s, %(rf_rate)s, %(implied_vol)s,
        %(delta)s, %(gamma)s, %(theta)s, %(vega)s
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=500)

def save_candidates_csv(rows, path):
    if not rows:
        return
    cols = [
        "fetch_time","underlying_id","expiry_date","strike",
        "call_contract_id","put_contract_id","spot_price",
        "call_bid","call_ask","put_bid","put_ask",
        "call_bid_vol1","call_ask_vol1","put_bid_vol1","put_ask_vol1",
        "call_rel_spread","put_rel_spread",
        "synthetic_forward_mid","parity_deviation_mid",
        "synthetic_forward_buy","parity_deviation_buy",
        "synthetic_forward_sell","parity_deviation_sell",
        "signal_side","signal_value"
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})

def run_once(args):
    contract_map = load_contracts(args.contracts)
    print(f"已加载未过期合约数: {len(contract_map)}")

    option_rows, fetch_time_str = fetch_option_quotes(contract_map, batch_size=args.batch_size, sleep_sec=args.sleep)
    print(f"期权行情: {len(option_rows)} 条")

    underlying_rows = fetch_underlyings(contract_map, fetch_time_str)
    print(f"标的行情: {len(underlying_rows)} 条")

    pair_rows = build_pairs(option_rows)
    print(f"call-put pairs: {len(pair_rows)} 组")

    parity_rows = calc_parity(pair_rows, underlying_rows)
    print(f"parity rows: {len(parity_rows)} 条")

    candidate_rows = build_trade_candidates(
        parity_rows,
        min_abs_dev=args.min_abs_dev,
        max_rel_spread=args.max_rel_spread,
        min_quote_vol=args.min_quote_vol,
    )
    print(f"trade candidates: {len(candidate_rows)} 条")

    factor_rows = build_option_factors(option_rows, underlying_rows, args.rf_rate)
    print(f"option factors: {len(factor_rows)} 条")

    conn = get_conn()
    try:
        insert_option_quotes(conn, option_rows)
        insert_underlyings(conn, underlying_rows)
        append_today_kline(conn, underlying_rows)
        insert_parity(conn, parity_rows)
        insert_candidates(conn, candidate_rows)
        insert_factors(conn, factor_rows)
        conn.commit()
    finally:
        conn.close()

    save_candidates_csv(candidate_rows, args.out_csv)
    print("已写入数据库")
    print(f"已输出 CSV: {args.out_csv}")

    print("\nTop 10 factors:")
    for x in factor_rows[:10]:
        print(
            x["contract_id"],
            x["option_type"],
            "IV=", x["implied_vol"],
            "delta=", x["delta"],
            "gamma=", x["gamma"],
            "theta=", x["theta"],
            "vega=", x["vega"]
        )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", default="contracts_active.json")
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--min-abs-dev", type=float, default=0.02)
    parser.add_argument("--max-rel-spread", type=float, default=0.05)
    parser.add_argument("--min-quote-vol", type=float, default=1)
    parser.add_argument("--rf-rate", type=float, default=0.015)
    parser.add_argument("--out-csv", default="parity_candidates_latest.csv")
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    if args.once:
        run_once(args)
        return

    round_no = 0
    while True:
        round_no += 1
        print(f"\n========== 第 {round_no} 轮开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==========")
        run_once(args)
        print(f"========== 第 {round_no} 轮结束，休眠 {args.interval} 秒 ==========")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()