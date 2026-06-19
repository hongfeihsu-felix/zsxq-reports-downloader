"""
Stock Price Fetcher — 统一实时行情接口
Primary: Finnhub (美股) → Fallback: yfinance (全市场)

用法：
  from data_sources import get_price
  data = get_price("NVDA")    # → Finnhub → yfinance fallback
  data = get_price("2330.TW") # → yfinance (非美股)

Cache: 同一天内不重复拉取（SQLite）
"""

import os
import json
import sqlite3
import time as _time
from pathlib import Path
from datetime import datetime, date
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "industry_metrics.db"

def _get_finnhub_key() -> str:
    """从环境变量或 config.json 读取 Finnhub key"""
    key = os.environ.get("FINNHUB_API_KEY", "")
    if key:
        return key
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            return cfg.get("api_keys", {}).get("finnhub", "")
        except (json.JSONDecodeError, KeyError):
            pass
    return ""

FINNHUB_API_KEY = _get_finnhub_key()


def _is_us_ticker(ticker: str) -> bool:
    """判断是否为美股 ticker"""
    if "." in ticker:
        suffix = ticker.split(".")[-1].upper()
        return suffix in ("US", "") or ticker.endswith(".US")
    # 无后缀的纯字母 ticker 视为美股
    return ticker.isalpha()


def _to_finnhub_symbol(ticker: str) -> str:
    """转为 Finnhub 格式（纯 ticker）"""
    return ticker.replace(".US", "").split(".")[0]


def _to_yf_symbol(ticker: str) -> str:
    """转为 yfinance 格式"""
    if "." in ticker:
        return ticker
    if ticker.isdigit():
        return f"{ticker}.TW"
    return ticker


class StockPriceFetcher:
    """行情获取器 — Finnhub (primary) + yfinance (fallback)"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._finnhub = None
        self._init_cache()

    def _get_finnhub(self):
        if self._finnhub is None and FINNHUB_API_KEY:
            try:
                import finnhub
                self._finnhub = finnhub.Client(api_key=FINNHUB_API_KEY)
            except ImportError:
                pass
        return self._finnhub

    def _init_cache(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_prices (
                symbol TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _cache_get(self, symbol: str) -> Optional[dict]:
        today = date.today().isoformat()
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT data_json, fetched_at FROM stock_prices WHERE symbol = ?",
            (symbol,)
        ).fetchone()
        conn.close()
        if row:
            data = json.loads(row[0])
            if row[1].startswith(today):
                return data
        return None

    def _cache_set(self, symbol: str, data: dict):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO stock_prices (symbol, data_json, fetched_at) VALUES (?, ?, ?)",
            (symbol, json.dumps(data, ensure_ascii=False, default=str), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    # ---- Finnhub (Primary for US) ----

    def _fetch_finnhub(self, ticker: str) -> Optional[dict]:
        """通过 Finnhub 获取美股行情"""
        fh = self._get_finnhub()
        if not fh:
            return None

        symbol = _to_finnhub_symbol(ticker)
        try:
            quote = fh.quote(symbol)
            if not quote or quote.get("c", 0) == 0:
                return None

            current = float(quote["c"])
            prev_close = float(quote.get("pc", current))
            change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0

            # Company profile for name & market cap
            name = ticker
            market_cap = None
            pe_ratio = None
            try:
                profile = fh.company_profile2(symbol=symbol)
                name = profile.get("name", ticker)
                market_cap = profile.get("marketCapitalization")  # in millions USD
                if market_cap:
                    market_cap = market_cap * 1e6  # convert to raw
            except Exception:
                pass

            # Key metrics for P/E
            try:
                metrics = fh.company_basic_financials(symbol, "all")
                metric_data = metrics.get("metric", {})
                pe_ratio = metric_data.get("peBasicExclExtraTTM") or metric_data.get("peTTM")
            except Exception:
                pass

            return {
                "symbol": symbol,
                "name": name,
                "price": round(current, 2),
                "change_pct": round(change_pct, 2),
                "currency": "USD",
                "market_cap": market_cap,
                "pe_ratio": round(float(pe_ratio), 1) if pe_ratio else None,
                "52w_high": float(quote.get("h", 0)) if quote.get("h") else None,
                "52w_low": float(quote.get("l", 0)) if quote.get("l") else None,
                "volume": quote.get("v"),
                "exchange": "US",
                "_source": "finnhub",
                "_cached": False
            }
        except Exception:
            return None

    # ---- yfinance (Fallback / Non-US) ----

    def _fetch_yfinance(self, ticker: str) -> Optional[dict]:
        """通过 yfinance 获取行情"""
        import yfinance as yf
        yf_symbol = _to_yf_symbol(ticker)

        last_error = None
        for attempt in range(3):
            try:
                t = yf.Ticker(yf_symbol)
                info = t.info
                hist = t.history(period="5d")
                if hist.empty:
                    last_error = "No data"
                    if attempt < 2:
                        _time.sleep(2 * (attempt + 1))
                    continue

                current = hist["Close"].iloc[-1]
                prev_close = info.get("previousClose") or hist["Close"].iloc[-2]
                change_pct = ((current - prev_close) / prev_close * 100) if prev_close and prev_close > 0 else 0

                return {
                    "symbol": yf_symbol,
                    "name": info.get("longName") or info.get("shortName", ""),
                    "price": round(float(current), 2),
                    "change_pct": round(float(change_pct), 2),
                    "currency": info.get("currency", ""),
                    "market_cap": info.get("marketCap"),
                    "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                    "52w_high": info.get("fiftyTwoWeekHigh"),
                    "52w_low": info.get("fiftyTwoWeekLow"),
                    "volume": info.get("volume"),
                    "exchange": info.get("exchange", ""),
                    "_source": "yfinance",
                    "_cached": False
                }
            except Exception as e:
                last_error = str(e)
                if attempt < 2:
                    _time.sleep(3 * (attempt + 1))

        return {"symbol": yf_symbol, "error": last_error or "Unknown",
                "_source": "yfinance", "_cached": False}

    # ---- Unified API ----

    def get(self, ticker: str, force_refresh: bool = False) -> dict:
        """获取单只股票行情 — Finnhub → yfinance fallback"""
        cache_key = _to_yf_symbol(ticker)

        if not force_refresh:
            cached = self._cache_get(cache_key)
            if cached:
                cached["_cached"] = True
                return cached

        # US stocks: try Finnhub first
        if _is_us_ticker(ticker):
            data = self._fetch_finnhub(ticker)
            if data and "error" not in data:
                self._cache_set(cache_key, data)
                return data
            # Finnhub failed → fallback to yfinance
            data = self._fetch_yfinance(ticker)
            if data:
                self._cache_set(cache_key, data)
            return data

        # A-shares (.SS/.SZ): use Eastmoney MX
        if ".SS" in ticker or ".SZ" in ticker:
            from .eastmoney import EastmoneyFetcher
            em = EastmoneyFetcher()
            data = em.get_price(ticker)
            if data and "error" not in data:
                self._cache_set(cache_key, data)
                return data
            # Fallback to yfinance for A-shares
            data = self._fetch_yfinance(ticker)
            if data:
                self._cache_set(cache_key, data)
            return data

        # Non-US: yfinance directly
        data = self._fetch_yfinance(ticker)
        if data:
            self._cache_set(cache_key, data)
        return data

    def get_batch(self, tickers: list[str]) -> dict[str, dict]:
        results = {}
        for t in tickers:
            results[t] = self.get(t)
            if len(results) > 1:
                _time.sleep(0.5)  # Finnhub rate limit: 60/min
        return results

    @staticmethod
    def format_for_report(data: dict) -> str:
        if data.get("error"):
            return f"Stock: {data['symbol']} — data unavailable"
        lines = [
            f"Stock: {data.get('name', data['symbol'])} ({data['symbol']})",
            f"Price: {data['price']} {data.get('currency', '')}",
        ]
        if data.get("change_pct"):
            lines.append(f"Change: {'+' if data['change_pct'] >= 0 else ''}{data['change_pct']}%")
        if data.get("pe_ratio"):
            lines.append(f"P/E: {data['pe_ratio']}")
        if data.get("market_cap"):
            mc = data["market_cap"]
            if mc >= 1e12:
                lines.append(f"Market Cap: {mc / 1e12:.1f}T")
            elif mc >= 1e9:
                lines.append(f"Market Cap: {mc / 1e9:.1f}B")
        src = data.get("_source", "")
        if src:
            lines.append(f"[{src}]")
        return " | ".join(lines)


# 全局单例
_fetcher: Optional[StockPriceFetcher] = None


def get_price(ticker: str, force_refresh: bool = False) -> dict:
    global _fetcher
    if _fetcher is None:
        _fetcher = StockPriceFetcher()
    return _fetcher.get(ticker, force_refresh)
