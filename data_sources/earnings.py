"""
Earnings Fetcher — 统一财报接口 + 财报日历

美股: SEC EDGAR + yfinance
台股: yfinance (基础)
A股: akshare (东方财富)

用法：
  from data_sources import get_earnings, get_earnings_calendar

  # 单公司最新财报
  data = get_earnings("TSMC", "2330.TW")

  # 财报日历
  calendar = get_earnings_calendar(["2330.TW", "NVDA", "600519.SS"])
  # → [{"ticker": "2330.TW", "next_date": "2026-07-15", "estimate_eps": ...}, ...]
"""

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

import yfinance as yf

DB_PATH = Path(__file__).parent.parent / "industry_metrics.db"


def _try_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        return None


def _get_finnhub_key() -> str:
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


class EarningsFetcher:
    """财报获取器（带缓存）— Finnhub (US) + yfinance (fallback)"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._finnhub = None
        self._init_cache()

    def _get_finnhub(self):
        if self._finnhub is None:
            key = _get_finnhub_key()
            if key:
                try:
                    import finnhub
                    self._finnhub = finnhub.Client(api_key=key)
                except ImportError:
                    pass
        return self._finnhub

    def _init_cache(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS earnings_data (
                ticker TEXT,
                report_date TEXT,
                period TEXT,
                revenue REAL,
                eps REAL,
                surprise_pct REAL,
                source TEXT,
                data_json TEXT,
                fetched_at TEXT,
                PRIMARY KEY (ticker, period)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                ticker TEXT PRIMARY KEY,
                next_date TEXT,
                estimate_eps REAL,
                last_eps REAL,
                source TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    # ---- US Stocks via yfinance ----

    def _fetch_us(self, ticker: str) -> list[dict]:
        """获取美股财报（yfinance）"""
        try:
            yf_ticker = ticker if "." not in ticker else ticker
            t = yf.Ticker(yf_ticker)

            results = []
            # 最近 4 个季度
            for _, row in t.quarterly_earnings.tail(6).iterrows():
                results.append({
                    "ticker": yf_ticker,
                    "period": str(row.name)[:10] if hasattr(row.name, 'strftime') else str(row.name),
                    "revenue": float(row.get("Revenue", 0) or 0),
                    "eps": float(row.get("Earnings", 0) or 0),
                    "source": "yfinance"
                })

            # 财报日期
            try:
                calendar = t.calendar
                if calendar is not None and not calendar.empty:
                    next_date = calendar.iloc[0].get("Earnings Date", None)
                    if next_date:
                        for r in results:
                            r["next_earnings_date"] = str(next_date)[:10]
            except Exception:
                pass

            return results
        except Exception as e:
            return [{"ticker": ticker, "error": str(e), "source": "yfinance"}]

    # ---- A-shares via akshare ----

    def _fetch_cn(self, ticker: str) -> list[dict]:
        """获取A股财报（akshare）"""
        ak = _try_akshare()
        if ak is None:
            return [{"ticker": ticker, "error": "akshare not installed", "source": "akshare"}]

        try:
            # 剥离后缀 .SS/.SZ
            symbol = ticker.replace(".SS", "").replace(".SZ", "")
            df = ak.stock_yjbb_em(symbol=symbol)
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.head(4).iterrows():
                results.append({
                    "ticker": ticker,
                    "period": str(row.get("报表日期", "")),
                    "revenue": float(row.get("营业总收入", 0) or 0),
                    "eps": float(row.get("基本每股收益", 0) or 0),
                    "source": "akshare"
                })
            return results
        except Exception as e:
            return [{"ticker": ticker, "error": str(e), "source": "akshare"}]

    # ---- Unified API ----

    def get(self, company_name: str, ticker: str, force_refresh: bool = False) -> dict:
        """获取单公司最新财报"""
        # Check cache
        if not force_refresh:
            conn = sqlite3.connect(str(self.db_path))
            row = conn.execute(
                "SELECT data_json FROM earnings_data WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 1",
                (ticker,)
            ).fetchone()
            conn.close()
            if row:
                cached = json.loads(row[0])
                age_days = (datetime.now() - datetime.fromisoformat(cached.get("fetched_at", "2000-01-01"))).days
                if age_days < 1:
                    cached["_cached"] = True
                    return cached

        # Determine market
        if ".SS" in ticker or ".SZ" in ticker:
            quarters = self._fetch_cn(ticker)
        else:
            quarters = self._fetch_us(ticker)

        # Merge with price data for current snapshot
        result = {
            "company": company_name,
            "ticker": ticker,
            "quarters": quarters,
            "fetched_at": datetime.now().isoformat(),
            "_cached": False
        }

        # Cache
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO earnings_data (ticker, report_date, period, revenue, eps, source, data_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, datetime.now().strftime("%Y-%m-%d"), "latest",
             quarters[0].get("revenue", 0) if quarters else 0,
             quarters[0].get("eps", 0) if quarters else 0,
             quarters[0].get("source", "") if quarters else "",
             json.dumps(result, ensure_ascii=False, default=str),
             datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        return result

    def get_calendar(self, tickers: list[str]) -> list[dict]:
        """获取财报日历（最近60天内即将发布的）"""
        results = []
        for ticker in tickers:
            try:
                # 检查缓存
                conn = sqlite3.connect(str(self.db_path))
                row = conn.execute(
                    "SELECT next_date, estimate_eps FROM earnings_calendar WHERE ticker = ?",
                    (ticker,)
                ).fetchone()
                conn.close()

                if row:
                    cached_date = row[0]
                    if cached_date:
                        cached_dt = datetime.fromisoformat(cached_date)
                        if cached_dt > datetime.now() - timedelta(days=7):
                            results.append({
                                "ticker": ticker,
                                "next_date": cached_date,
                                "estimate_eps": row[1],
                                "_cached": True
                            })
                            continue

                # Fetch fresh — try Finnhub first for US tickers
                date_str = None
                estimate = None
                source = "yfinance"

                fh = self._get_finnhub()
                is_us = "." not in ticker or ticker.endswith(".US")
                if fh and is_us:
                    try:
                        symbol = ticker.replace(".US", "").split(".")[0]
                        today = datetime.now()
                        ec = fh.earnings_calendar(
                            symbol=symbol,
                            _from=today.strftime("%Y-%m-%d"),
                            to=(today + timedelta(days=90)).strftime("%Y-%m-%d")
                        )
                        if ec and "earningsCalendar" in ec and ec["earningsCalendar"]:
                            next_earning = ec["earningsCalendar"][0]
                            date_str = next_earning.get("date", "")
                            estimate = next_earning.get("epsEstimate")
                            source = "finnhub"
                    except Exception:
                        pass

                # Fallback to yfinance
                if not date_str:
                    yf_symbol = ticker if "." in ticker else ticker
                    t = yf.Ticker(yf_symbol)
                    try:
                        calendar = t.calendar
                        if calendar is not None and not calendar.empty:
                            next_date = calendar.iloc[0].get("Earnings Date", None)
                            if next_date:
                                date_str = str(next_date)[:10]
                                estimate = float(calendar.iloc[0].get("Earnings Average", 0) or 0)
                    except Exception:
                        pass

                if date_str:
                    conn = sqlite3.connect(str(self.db_path))
                    conn.execute(
                        "INSERT OR REPLACE INTO earnings_calendar (ticker, next_date, estimate_eps, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (ticker, date_str, estimate, source, datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()

                    results.append({
                        "ticker": ticker,
                        "next_date": date_str,
                        "estimate_eps": estimate,
                        "_source": source,
                        "_cached": False
                    })

            except Exception as e:
                results.append({"ticker": ticker, "error": str(e)})

        # Sort by date
        results.sort(key=lambda x: x.get("next_date", "9999"))
        return results

    def this_week_earnings(self, tickers: list[str]) -> list[dict]:
        """本周即将发布财报的公司"""
        calendar = self.get_calendar(tickers)
        today = datetime.now().date()
        week_end = today + timedelta(days=7)

        this_week = []
        for item in calendar:
            if item.get("next_date"):
                try:
                    edate = datetime.fromisoformat(item["next_date"]).date()
                    if today <= edate <= week_end:
                        this_week.append(item)
                except (ValueError, TypeError):
                    pass
        return this_week

    @staticmethod
    def format_for_report(data: dict) -> str:
        """格式化财报为报告摘要文本"""
        if data.get("_cached"):
            pass  # still include data

        quarters = data.get("quarters", [])
        if not quarters or quarters[0].get("error"):
            return f"Earnings: {data['ticker']} — data unavailable"

        lines = [f"Recent Earnings ({data['ticker']}):"]
        for q in quarters[:3]:
            period = q.get("period", "?")
            rev = q.get("revenue", 0)
            eps = q.get("eps", 0)
            src = q.get("source", "")
            if rev > 1e8:
                rev_str = f"{rev / 1e8:.1f}B"
            elif rev > 1e4:
                rev_str = f"{rev / 1e4:.1f}M"
            else:
                rev_str = f"{rev:.0f}"
            lines.append(f"  {period}: Rev {rev_str}, EPS {eps:.2f} [{src}]")

        return "\n".join(lines)


# 全局单例
_fetcher: Optional[EarningsFetcher] = None


def get_earnings(company: str, ticker: str) -> dict:
    global _fetcher
    if _fetcher is None:
        _fetcher = EarningsFetcher()
    return _fetcher.get(company, ticker)


def get_earnings_calendar(tickers: list[str]) -> list[dict]:
    global _fetcher
    if _fetcher is None:
        _fetcher = EarningsFetcher()
    return _fetcher.get_calendar(tickers)
