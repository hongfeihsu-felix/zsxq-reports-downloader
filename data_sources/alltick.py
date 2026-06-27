#!/usr/bin/env python3
"""
AllTick real-time stock data via WebSocket snapshot.
Paid plan required for US + HK real-time access.

Usage:
  from data_sources.alltick import AllTickFetcher
  fetcher = AllTickFetcher()
  quote = fetcher.get_snapshot("AAPL.US")   # US
  quote = fetcher.get_snapshot("00700.HK")   # HK
  quotes = fetcher.get_batch(["AAPL.US", "TSLA.US", "00700.HK"])
"""

import asyncio, json, os
from typing import Optional

ALLTICK_TOKEN = os.environ.get("ALLTICK_TOKEN", "")
WS_URL = "wss://quote.alltick.io/quote-stock-b-ws-api"


def _normalize_code(ticker: str, market: str) -> str:
    """Convert ticker to AllTick format.
    US: 'AVGO.US' → 'AVGO.US' (unchanged)
    HK: '01810.HK' → '1810.HK' (strip leading zeros)
    """
    if market == "HK":
        parts = ticker.split(".")
        num = parts[0].lstrip("0")  # 01810 → 1810, 00700 → 700
        return f"{num}.HK"
    if "." not in ticker:
        return f"{ticker}.US"
    return ticker


class AllTickFetcher:
    """WebSocket + REST quote fetcher for US and HK stocks."""

    def __init__(self, token: str = None):
        self.token = token or ALLTICK_TOKEN
        if not self.token:
            # Try from config
            from pathlib import Path
            cfg_path = Path(__file__).parent.parent / "config.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                self.token = cfg.get("api_keys", {}).get("alltick", "")

    def _sync_get(self, codes: list[str]) -> dict[str, dict]:
        """Connect WS, subscribe, collect one tick per code, disconnect."""
        return asyncio.run(self._async_get(codes))

    async def _async_get(self, codes: list[str]) -> dict[str, dict]:
        result = {c: {"price": None, "change_pct": 0, "errors": []} for c in codes}
        try:
            import websockets
            url = f"{WS_URL}?token={self.token}"
            async with websockets.connect(url) as ws:
                msg = {
                    "type": "subscribe",
                    "data": {
                        "code_list": codes,
                        "data_type": "real_time_trade"
                    }
                }
                await ws.send(json.dumps(msg))
                # Collect responses until we have all or timeout
                received = set()
                for _ in range(len(codes) * 2 + 2):
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=10)
                        d = json.loads(resp)
                        code = d.get("code", "")
                        if code and code in result:
                            result[code] = {
                                "price": float(d.get("price", 0)),
                                "change_pct": float(d.get("change_pct", 0)),
                                "currency": "USD" if ".US" in code else "HKD",
                                "volume": d.get("volume", 0),
                            }
                            received.add(code)
                        if len(received) >= len(codes):
                            break
                    except asyncio.TimeoutError:
                        missing = set(codes) - received
                        for code in missing:
                            result[code].setdefault("errors", []).append("timeout")
                        break
                await ws.close()
        except Exception as e:
            err = f"{type(e).__name__}:{e}"
            for code in codes:
                result[code].setdefault("errors", []).append(err)
        return result

    def get_snapshot(self, code: str) -> Optional[dict]:
        """Get a single stock snapshot quote."""
        results = self._sync_get([code])
        return results.get(code)

    def get_batch(self, codes: list[str]) -> dict[str, dict]:
        """Get batch snapshot quotes for multiple stocks."""
        return self._sync_get(codes)

    _last_request = 0.0

    def _rate_limit(self):
        """Ensure at least 1s between requests (AllTick limit: 1 query/sec)."""
        import time
        now = time.time()
        gap = now - self._last_request
        if gap < 1.0:
            time.sleep(1.0 - gap)
        self._last_request = time.time()

    def get_kline(self, code: str, count: int = 80,
                  kline_type: str = "8") -> list[dict]:
        """Get daily K-line bars via REST API."""
        import requests
        self._rate_limit()
        url = "https://quote.alltick.io/quote-stock-b-api/kline"
        query_num = min(count, 1000)
        params = {
            "token": self.token,
            "query": json.dumps({
                "data": {
                    "code": code,
                    "kline_type": kline_type,
                    "kline_timestamp_end": "0",
                    "query_kline_num": str(query_num),
                    "adjust_type": "0"
                }
            })
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            d = resp.json()
            if d.get("ret") == 200 and d.get("data"):
                bars = []
                for k in d["data"].get("kline_list", []):
                    bars.append({
                        "timestamp": k.get("timestamp", "0"),
                        "close": float(k.get("close_price", 0)),
                        "open": float(k.get("open_price", 0)),
                        "high": float(k.get("high_price", 0)),
                        "low": float(k.get("low_price", 0)),
                        "volume": float(k.get("volume", 0)),
                        "turnover": float(k.get("turnover", 0)),
                    })
                return bars
        except Exception:
            pass
        return []
