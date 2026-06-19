#!/usr/bin/env python3
"""
FinMind Price Client — 台美股历史价格获取 + 缓存

FinMind 免费 API，带 token 提高限额。台股用 TaiwanStockPrice，美股用 USStockPrice。
自动缓存到 ~/.hermes_cache/ 避免重复请求。

用法:
  from finmind_client import get_price, get_forward_return, load_config
  px = get_price("NVDA", "2026-05-01")  # 取单日收盘价
  ret = get_forward_return("2330", "2026-05-01", days=30)  # 30日后收益率
"""

import json
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import urllib.request
import urllib.parse

CACHE_DIR = Path.home() / ".hermes_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DB = CACHE_DIR / "finmind_cache.db"

CONFIG_PATH = Path(__file__).parent / "config.json"

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"


def _load_token() -> str:
    """从 config.json 加载 FinMind token."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg.get("api_keys", {}).get("finmind", "")
    except Exception:
        return ""


TOKEN = _load_token()


def _get_cache_conn():
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_cache (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            market TEXT NOT NULL DEFAULT 'US',
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (symbol, date, market)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_symbol_date ON price_cache(symbol, date)
    """)
    conn.commit()
    return conn


def _fetch_finmind(dataset: str, data_id: str, start_date: str, end_date: str,
                   max_retries: int = 3) -> list[dict]:
    """从 FinMind API 拉取数据，带重试。"""
    params = {
        "dataset": dataset,
        "data_id": data_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if TOKEN:
        params["token"] = TOKEN

    url = f"{FINMIND_BASE}?{urllib.parse.urlencode(params)}"

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            if data.get("msg") == "success":
                return data.get("data", [])
        except Exception as e:
            # 402/403 = paid tier, 404 = no data — skip silently
            if any(c in str(e) for c in ["402", "403", "404"]):
                return []
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  WARN: {data_id} fetch failed: {e}")
    return []


def _determine_market(symbol: str) -> str:
    """判断台股还是美股。"""
    if symbol.isdigit() and len(symbol) <= 6:
        return "TW"
    return "US"


def _dataset_for(market: str) -> str:
    return "TaiwanStockPrice" if market == "TW" else "USStockPrice"


def _normalize_row(row: dict, market: str) -> dict:
    """统一台股/美股字段名。"""
    if market == "TW":
        return {
            "date": row["date"],
            "open": float(row["open"]),
            "high": float(row["max"]),
            "low": float(row["min"]),
            "close": float(row["close"]),
            "volume": float(row.get("Trading_Volume", 0)),
        }
    else:
        return {
            "date": row["date"],
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0)),
        }


def fetch_prices(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """拉取历史价格，写入缓存。返回统一格式的行列表。"""
    market = _determine_market(symbol)
    dataset = _dataset_for(market)

    raw = _fetch_finmind(dataset, symbol, start_date, end_date)
    if not raw:
        return []

    rows = [_normalize_row(r, market) for r in raw]

    # Write to cache
    conn = _get_cache_conn()
    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO price_cache (symbol, date, open, high, low, close, volume, market)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], market))
    conn.commit()
    conn.close()

    return rows


def get_price(symbol: str, date_str: str) -> Optional[float]:
    """获取单日收盘价。先查缓存，再调 API。"""
    conn = _get_cache_conn()
    row = conn.execute(
        "SELECT close FROM price_cache WHERE symbol = ? AND date = ?",
        (symbol, date_str)
    ).fetchone()
    conn.close()

    if row:
        return row[0]

    # Not in cache, fetch from API
    end = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")
    rows = fetch_prices(symbol, date_str, end)
    for r in rows:
        if r["date"] == date_str:
            return r["close"]
    return None


def get_forward_return(symbol: str, base_date: str, days: int = 30) -> Optional[float]:
    """计算 base_date 后 days 天的收益率 (百分比)。"""
    base_px = get_price(symbol, base_date)
    if base_px is None:
        return None

    future = (datetime.strptime(base_date, "%Y-%m-%d") + timedelta(days=days + 5)).strftime("%Y-%m-%d")
    fwd_px = get_price(symbol, future)
    if fwd_px is None:
        # Try surrounding dates
        for offset in [4, 3, 2, 1, -1, -2]:
            alt = (datetime.strptime(base_date, "%Y-%m-%d") + timedelta(days=days + offset)).strftime("%Y-%m-%d")
            fwd_px = get_price(symbol, alt)
            if fwd_px is not None:
                break

    if fwd_px is None:
        return None

    return round((fwd_px - base_px) / base_px * 100, 1)


def batch_preload(symbols: list[str], start: str, end: str):
    """批量预加载多只股票的历史数据到缓存。"""
    for sym in symbols:
        try:
            rows = fetch_prices(sym, start, end)
            if rows:
                print(f"  {sym}: {len(rows)} days cached ({rows[0]['date']} ~ {rows[-1]['date']})")
        except Exception as e:
            print(f"  {sym}: ERROR - {e}")
        time.sleep(0.3)  # Rate limit courtesy


if __name__ == "__main__":
    # Quick test
    print("=== FinMind Client Test ===")
    for sym in ["NVDA", "TSM", "2330"]:
        px = get_price(sym, "2026-06-06")
        fwd = get_forward_return(sym, "2026-05-06", days=30) if px else None
        print(f"  {sym}: 6/6 close={px}, 30d fwd return={fwd}")
