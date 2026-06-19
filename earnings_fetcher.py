#!/usr/bin/env python3
"""Fetch actual earnings data from Finnhub and store in valuation.db."""

import json
import sqlite3
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Optional

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.json"
DB_PATH = PROJECT_DIR / "valuation.db"


def _get_finnhub_key() -> str:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("api_keys", {}).get("finnhub", "")
    return ""


def fetch_earnings(ticker: str) -> list[dict]:
    """Fetch earnings history from Finnhub. Returns list of {period, actual, estimate, surprise}. """
    key = _get_finnhub_key()
    if not key:
        return []

    # Normalize ticker: Finnhub uses e.g. NVDA, not NVDA.US
    symbol = ticker.split(".")[0]

    url = f"https://finnhub.io/api/v1/stock/earnings?symbol={symbol}&token={key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Finnhub error for {symbol}: {e}")
        return []

    if not data:
        return []

    results = []
    for entry in data[:8]:  # last 8 quarters
        period = entry.get("period", "")
        actual = entry.get("actual")
        estimate = entry.get("estimate")
        if actual is not None and actual != 0:
            surprise = round((actual - estimate) / abs(estimate) * 100, 1) if estimate and estimate != 0 else None
            results.append({
                "period": period,
                "eps_actual": actual,
                "eps_estimate": estimate,
                "surprise_pct": surprise,
            })
    return results


def store_earnings(ticker: str, config_name: str) -> int:
    """Fetch and store earnings for a company. Returns number of quarters stored."""
    earnings = fetch_earnings(ticker)
    if not earnings:
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_actuals (
            ticker TEXT NOT NULL,
            company TEXT NOT NULL,
            period TEXT NOT NULL,
            eps_actual REAL,
            eps_estimate REAL,
            surprise_pct REAL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, period)
        )
    """)
    conn.commit()

    count = 0
    now = datetime.now().isoformat()
    for e in earnings:
        conn.execute(
            """INSERT OR REPLACE INTO earnings_actuals
               (ticker, company, period, eps_actual, eps_estimate, surprise_pct, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, config_name, e["period"], e["eps_actual"],
             e["eps_estimate"], e["surprise_pct"], now)
        )
        count += 1
    conn.commit()
    conn.close()

    # Rate limit
    time.sleep(0.5)
    return count


def backfill_all() -> dict:
    """Backfill earnings for all companies in config with US tickers."""
    if not CONFIG_PATH.exists():
        return {"error": "no config"}

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies = cfg.get("tracking", {}).get("companies", [])

    results = {"total": 0, "companies": 0, "quarters": 0}
    for c in companies:
        if not c.get("active", True):
            continue
        ticker = c.get("ticker", "")
        if not ticker:
            continue
        # Only fetch for US stocks (Finnhub primary)
        if ".US" not in ticker and not ticker.isalpha():
            continue

        print(f"  Fetching {c['name']} ({ticker})...")
        q = store_earnings(ticker, c["name"])
        if q > 0:
            results["companies"] += 1
            results["quarters"] += q
        results["total"] += 1

    return results


if __name__ == "__main__":
    print("Backfilling earnings data from Finnhub...")
    results = backfill_all()
    print(f"Done: {results['companies']}/{results['total']} companies, {results['quarters']} quarters")


def _a_ticker_to_baostock(ticker: str) -> str:
    """Convert config ticker to baostock format: '603986' or '603986.SH' → 'sh.603986'"""
    t = ticker.split(".")[0]
    if ticker.endswith(".SH") or (len(t) == 6 and t.startswith(("6", "68"))):
        return f"sh.{t}"
    if ticker.endswith(".SZ") or len(t) == 6:
        return f"sz.{t}"
    return ""


def fetch_a_share_earnings(ticker: str) -> list[dict]:
    """Fetch A-share earnings via baostock."""
    import baostock as bs
    bs.login()
    code = _a_ticker_to_baostock(ticker)
    if not code:
        bs.logout()
        return []

    results = []
    try:
        # Query last 2 years, 4 quarters each
        for year in [2026, 2025]:
            for q in [1, 2, 3, 4]:
                rs = bs.query_profit_data(code=code, year=year, quarter=q)
                if rs.error_code != '0':
                    continue
                while rs.next():
                    row = rs.get_row_data()
                    eps = float(row[7]) if row[7] else 0  # epsTTM column
                    stat_date = row[2]  # statDate
                    if eps and eps != 0:
                        results.append({
                            "period": stat_date,
                            "eps_actual": eps,
                            "eps_estimate": None,
                            "surprise_pct": None,
                        })
    except Exception as e:
        print(f"  Baostock error for {ticker}: {e}")
    finally:
        bs.logout()

    return sorted(results, key=lambda x: x["period"], reverse=True)[:8]


def store_a_share_earnings(ticker: str, config_name: str) -> int:
    """Fetch and store A-share earnings."""
    earnings = fetch_a_share_earnings(ticker)
    if not earnings:
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_actuals (
            ticker TEXT NOT NULL,
            company TEXT NOT NULL,
            period TEXT NOT NULL,
            eps_actual REAL,
            eps_estimate REAL,
            surprise_pct REAL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, period)
        )
    """)
    conn.commit()

    count = 0
    now = datetime.now().isoformat()
    for e in earnings:
        conn.execute(
            """INSERT OR REPLACE INTO earnings_actuals
               (ticker, company, period, eps_actual, eps_estimate, surprise_pct, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, config_name, e["period"], e["eps_actual"],
             e["eps_estimate"], e["surprise_pct"], now)
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def backfill_a_shares() -> dict:
    """Backfill A-share earnings for all companies with A-share tickers."""
    if not CONFIG_PATH.exists():
        return {"error": "no config"}
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies = cfg.get("tracking", {}).get("companies", [])
    results = {"total": 0, "companies": 0, "quarters": 0}
    for c in companies:
        if not c.get("active", True):
            continue
        ticker = c.get("ticker", "")
        if not ticker:
            continue
        # A-share tickers: 600183, 300476, 603986.SH, 002049.SZ, etc.
        t = ticker.split(".")[0]
        if not (len(t) == 6 and t.isdigit()):
            continue
        print(f"  Fetching {c['name']} ({ticker})...")
        q = store_a_share_earnings(ticker, c["name"])
        if q > 0:
            results["companies"] += 1
            results["quarters"] += q
        results["total"] += 1
    return results


if __name__ == "__main__":
    import sys
    if "--a" in sys.argv:
        print("Backfilling A-share earnings from Baostock...")
        results = backfill_a_shares()
        print(f"Done: {results['companies']}/{results['total']} companies, {results['quarters']} quarters")
    else:
        print("Backfilling US earnings from Finnhub...")
        results = backfill_all()
        print(f"Done: {results['companies']}/{results['total']} companies, {results['quarters']} quarters")


FINMIND_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiaG9uZ2ZlaWhzdSIsImVtYWlsIjoiaG9uZ2ZlaWhzdUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.mlL9qvkh65bsDpQcZ7szUREhQsECJB4VVfEQ3MAkxEg"


def fetch_tw_earnings(ticker: str) -> list[dict]:
    """Fetch TW stock earnings via FinMind."""
    stock_id = ticker.split(".")[0]  # 2454.TW → 2454
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={stock_id}&start_date=2024-01-01"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {FINMIND_KEY}",
        "User-Agent": "Hermes/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  FinMind error for {ticker}: {e}")
        return []

    records = data.get("data", [])
    if not records:
        return []

    results = []
    for r in records:
        if r.get("type") != "EPS":
            continue
        eps = r.get("value")
        if eps and eps != 0:
            results.append({
                "period": r.get("date", ""),
                "eps_actual": float(eps),
                "eps_estimate": None,
                "surprise_pct": None,
            })
    return sorted(results, key=lambda x: x["period"], reverse=True)[:8]


def store_tw_earnings(ticker: str, config_name: str) -> int:
    """Fetch and store TW stock earnings."""
    earnings = fetch_tw_earnings(ticker)
    if not earnings:
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS earnings_actuals (
        ticker TEXT NOT NULL, company TEXT NOT NULL, period TEXT NOT NULL,
        eps_actual REAL, eps_estimate REAL, surprise_pct REAL,
        fetched_at TEXT NOT NULL, PRIMARY KEY (ticker, period))""")
    conn.commit()

    count = 0
    now = datetime.now().isoformat()
    for e in earnings:
        conn.execute(
            """INSERT OR REPLACE INTO earnings_actuals
               (ticker, company, period, eps_actual, eps_estimate, surprise_pct, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, config_name, e["period"], e["eps_actual"],
             e["eps_estimate"], e["surprise_pct"], now)
        )
        count += 1
    conn.commit()
    conn.close()
    time.sleep(0.5)
    return count


def backfill_tw() -> dict:
    """Backfill TW stock earnings."""
    if not CONFIG_PATH.exists():
        return {"error": "no config"}
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies = cfg.get("tracking", {}).get("companies", [])
    results = {"total": 0, "companies": 0, "quarters": 0}
    for c in companies:
        if not c.get("active", True):
            continue
        ticker = c.get("ticker", "")
        if not ticker or ".TW" not in ticker:
            continue
        print(f"  Fetching {c['name']} ({ticker})...")
        q = store_tw_earnings(ticker, c["name"])
        if q > 0:
            results["companies"] += 1
            results["quarters"] += q
        results["total"] += 1
    return results


def fetch_earnings_calendar() -> list[dict]:
    """Fetch upcoming earnings dates from Finnhub for tracked US stocks."""
    key = _get_finnhub_key()
    if not key:
        return []

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    results = []
    for c in cfg.get("tracking", {}).get("companies", []):
        if not c.get("active", True): continue
        ticker = c.get("ticker", "")
        if ".US" not in ticker and not ticker.isalpha(): continue
        symbol = ticker.split(".")[0]

        url = f"https://finnhub.io/api/v1/calendar/earnings?from={datetime.now().strftime('%Y-%m-%d')}&to={(datetime.now() + __import__('datetime').timedelta(days=14)).strftime('%Y-%m-%d')}&symbol={symbol}&token={key}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if data and data.get('earningsCalendar'):
                for e in data['earningsCalendar']:
                    results.append({
                        "ticker": symbol,
                        "company": c["name"],
                        "date": e.get("date", ""),
                        "hour": e.get("hour", ""),
                        "eps_estimate": e.get("epsEstimate"),
                        "eps_actual": e.get("epsActual"),
                        "revenue_estimate": e.get("revenueEstimate"),
                    })
            time.sleep(0.3)
        except Exception as e:
            print(f"  Calendar error for {symbol}: {e}")
    return results


def update_calendar_db(calendar: list[dict]):
    """Store earnings calendar in valuation.db."""
    if not calendar:
        return
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS earnings_calendar (
        ticker TEXT, company TEXT, date TEXT, hour TEXT,
        eps_estimate REAL, eps_actual REAL, revenue_estimate REAL,
        updated_at TEXT, PRIMARY KEY (ticker, date))""")
    conn.commit()
    now = datetime.now().isoformat()
    for e in calendar:
        conn.execute("""INSERT OR REPLACE INTO earnings_calendar
            (ticker, company, date, hour, eps_estimate, eps_actual, revenue_estimate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (e["ticker"], e["company"], e["date"], e["hour"],
             e["eps_estimate"], e["eps_actual"], e["revenue_estimate"], now))
    conn.commit()
    conn.close()


def auto_refresh():
    """Incremental refresh: fetch latest quarter for each market, merge into DB."""
    print("Auto-refreshing earnings data...")
    results = {"US": 0, "A": 0, "TW": 0}

    # US: fetch last 2 quarters (in case new one just posted)
    from earnings_fetcher import backfill_all as _us
    us = _us()
    results["US"] = us["quarters"]

    # A-share: re-fetch latest
    from earnings_fetcher import backfill_a_shares as _a
    a = _a()
    results["A"] = a["quarters"]

    # TW: re-fetch
    from earnings_fetcher import backfill_tw as _tw
    tw = _tw()
    results["TW"] = tw["quarters"]

    # Calendar
    cal = fetch_earnings_calendar()
    update_calendar_db(cal)
    results["calendar"] = len(cal)

    return results


if __name__ == "__main__":
    import sys
    if "--a" in sys.argv:
        print("Backfilling A-share earnings...")
        print(backfill_a_shares())
    elif "--tw" in sys.argv:
        print("Backfilling TW earnings...")
        print(backfill_tw())
    elif "--refresh" in sys.argv:
        print("Auto-refreshing...")
        print(auto_refresh())
    else:
        print("Backfilling US earnings...")
        print(backfill_all())
