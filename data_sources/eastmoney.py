"""
Eastmoney MX API — A 股行情 & 财务数据
东方财富妙想金融数据 API (mx_data skill)

API: POST https://mkapi2.dfcfs.com/finskillshub/api/claw/query
Auth: Header apikey

用法：
  from data_sources.eastmoney import EastmoneyFetcher
  em = EastmoneyFetcher()
  data = em.get_price("300059.SZ")  # 东方财富
  data = em.get_price("600519.SS")  # 贵州茅台
  financials = em.get_financials("600519.SS")
"""

import os
import json
import sqlite3
import requests
from pathlib import Path
from datetime import datetime, date
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "industry_metrics.db"
MX_API_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"


def _get_mx_apikey() -> str:
    key = os.environ.get("MX_APIKEY", "")
    if key:
        return key
    config_path = DB_PATH.parent / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            return cfg.get("api_keys", {}).get("eastmoney_mx", "")
        except (json.JSONDecodeError, KeyError):
            pass
    return ""


def _to_secu_code(ticker: str) -> str:
    """Ticker → 东方财富证券代码格式 (e.g. 300059.SZ → 300059, SZ)"""
    parts = ticker.replace(".SS", ".SH").split(".")
    code = parts[0]
    market = parts[1] if len(parts) > 1 else "SZ"
    return code, market


class EastmoneyFetcher:
    """东方财富妙想 A 股数据获取器"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.api_key = _get_mx_apikey()
        self._init_cache()

    def _init_cache(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS a_share_prices (
                code TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _call_api(self, query: str) -> Optional[dict]:
        """调用 MX API"""
        if not self.api_key:
            return None
        try:
            resp = requests.post(
                MX_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "apikey": self.api_key
                },
                json={"toolQuery": query},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def _parse_data_tables(self, response: dict) -> list[dict]:
        """解析 MX API 返回的 dataTableDTOList"""
        if not response:
            return []

        # Navigate: response → data → data → searchDataResultDTO
        srd = None
        try:
            inner = response.get("data", {})
            # Handle double-wrapped data
            if "searchDataResultDTO" in inner:
                srd = inner["searchDataResultDTO"]
            elif "data" in inner and "searchDataResultDTO" in inner["data"]:
                srd = inner["data"]["searchDataResultDTO"]
        except (KeyError, TypeError, AttributeError):
            pass

        if not srd:
            return []

        tables = srd.get("dataTableDTOList", [])
        results = []

        for table in tables:
            code = table.get("code", "")
            name = table.get("entityName", "")

            # Build field_name mapping from nameMap
            name_map = table.get("nameMap", {})
            # Also try field
            field = table.get("field", {})
            if field:
                name_map[field.get("returnCode", "")] = field.get("returnName", "")

            # Parse raw table data (numeric, no units)
            tbl = table.get("rawTable", table.get("table", {}))
            head = tbl.get("headName", [])

            for field_id, values in tbl.items():
                if field_id == "headName":
                    continue
                field_name = name_map.get(field_id, field_id)

                # Handle both single value and list
                vals = values if isinstance(values, list) else [values]

                for i, val in enumerate(vals):
                    period = head[i] if i < len(head) else "latest"
                    # Clean up value (remove units like 元, %, etc.)
                    clean_val = str(val).replace("元", "").replace("%", "").replace(",", "").strip()
                    try:
                        num_val = float(clean_val)
                    except (ValueError, TypeError):
                        num_val = None

                    results.append({
                        "code": code,
                        "name": name,
                        "field": field_id,
                        "field_name": field_name,
                        "value": num_val,
                        "raw_value": str(val),
                        "period": str(period)
                    })

        return results

    def get_price(self, ticker: str) -> dict:
        """获取 A 股实时行情"""
        # Check cache
        today = date.today().isoformat()
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT data_json, fetched_at FROM a_share_prices WHERE code = ?",
            (ticker,)
        ).fetchone()
        conn.close()
        if row:
            data = json.loads(row[0])
            if row[1].startswith(today):
                data["_cached"] = True
                return data

        code, market = _to_secu_code(ticker)
        code_suffix = f"{code}.{market.replace('SH', 'SS')}"

        result = {
            "symbol": code_suffix,
            "name": "",
            "price": None,
            "change_pct": None,
            "pe_ratio": None,
            "market_cap": None,
            "currency": "CNY",
            "_source": "eastmoney_mx",
            "_cached": False
        }

        # MX API returns one metric per call, so we batch queries
        queries = {
            "price": f"{ticker} 收盘价",
            "change_pct": f"{ticker} 涨跌幅",
            "pe_ratio": f"{ticker} 市盈率",
            "market_cap": f"{ticker} 总市值",
        }
        name_set = False

        for key, query in queries.items():
            response = self._call_api(query)
            if not response:
                continue
            parsed = self._parse_data_tables(response)
            for p in parsed:
                if not name_set and p.get("name"):
                    result["name"] = p["name"]
                    name_set = True
                val = p.get("value")
                if val is not None:
                    if key == "price":
                        result["price"] = round(val, 2)
                    elif key == "change_pct":
                        result["change_pct"] = round(val, 2)
                    elif key == "pe_ratio":
                        result["pe_ratio"] = round(val, 2)
                    elif key == "market_cap":
                        result["market_cap"] = val
                break  # Only take first value for each query

        if result["price"]:
            self._cache_set(ticker, result)
            return result

        # Fallback to akshare
        return self._fallback_akshare(ticker)

    def _fallback_akshare(self, ticker: str) -> dict:
        """akshare fallback"""
        try:
            import akshare as ak
            code = ticker.replace(".SS", "").replace(".SZ", "")
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                result = {
                    "symbol": ticker,
                    "name": r.get("名称", ""),
                    "price": float(r["最新价"]) if r.get("最新价") else None,
                    "change_pct": float(r["涨跌幅"]) if r.get("涨跌幅") else None,
                    "pe_ratio": float(r["市盈率-动态"]) if r.get("市盈率-动态") else None,
                    "market_cap": float(r["总市值"]) if r.get("总市值") else None,
                    "currency": "CNY",
                    "_source": "akshare",
                    "_cached": False
                }
                self._cache_set(ticker, result)
                return result
        except Exception:
            pass

        return {"symbol": ticker, "error": "No data",
                "_source": "eastmoney_mx", "_cached": False}

    def get_financials(self, ticker: str) -> dict:
        """获取 A 股财务数据"""
        response = self._call_api(f"{ticker} 最近4个季度 单季度营业总收入 单季度净利润 单季度基本每股收益")
        if not response:
            return {"ticker": ticker, "error": "API unavailable", "quarters": []}

        parsed = self._parse_data_tables(response)
        if not parsed:
            return {"ticker": ticker, "quarters": [], "note": "no data returned"}

        # Group by period
        periods = {}
        for p in parsed:
            period = p.get("period", "?")
            if period not in periods:
                periods[period] = {}
            periods[period][p.get("field_name", "")] = p.get("value")

        quarters = []
        for period, vals in sorted(periods.items(), reverse=True)[:4]:
            # Match field names by substring (e.g., "单季度.营业总收入" contains "营业总" or "收入")
            def find_field(*keywords):
                for fname, fval in vals.items():
                    if all(kw in fname for kw in keywords):
                        return fval
                return None

            quarters.append({
                "period": period,
                "revenue": find_field("营业总", "收入") or find_field("营业"),
                "net_profit": find_field("净利润") or find_field("净利"),
                "eps": find_field("每股收益"),
            })

        return {
            "ticker": ticker,
            "name": parsed[0].get("name", "") if parsed else "",
            "quarters": quarters,
            "_source": "eastmoney_mx"
        }

    def search_sector(self, keyword: str) -> list[dict]:
        """搜索行业/概念板块股票"""
        response = self._call_api(f"A股{keyword}概念股 最新价 涨跌幅 市盈率")
        if not response:
            return []

        parsed = self._parse_data_tables(response)
        # Group by stock code
        stocks = {}
        for p in parsed:
            code = p.get("code", "")
            if code not in stocks:
                stocks[code] = {"code": code, "name": p.get("name", "")}
            fn = p.get("field_name", "")
            if "最新价" in fn:
                stocks[code]["price"] = p.get("value")
            elif "涨跌幅" in fn:
                stocks[code]["change_pct"] = p.get("value")
            elif "市盈率" in fn:
                stocks[code]["pe_ratio"] = p.get("value")

        return list(stocks.values())

    def _cache_set(self, ticker: str, data: dict):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO a_share_prices (code, data_json, fetched_at) VALUES (?, ?, ?)",
            (ticker, json.dumps(data, ensure_ascii=False, default=str), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    @staticmethod
    def format_for_report(data: dict) -> str:
        if data.get("error"):
            return f"A-Share: {data['symbol']} — data unavailable"
        return (
            f"A-Share: {data.get('name', data['symbol'])} ({data['symbol']}) | "
            f"Price: {data.get('price', '?')} CNY | "
            f"Change: {data.get('change_pct', 0):+.1f}% | "
            f"P/E: {data.get('pe_ratio', '?')}"
        )


# ============ CLI Test ============

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Eastmoney MX API Test")
    parser.add_argument("ticker", nargs="?", default="300059.SZ", help="A-share ticker")
    parser.add_argument("--financials", action="store_true", help="Get financials")
    parser.add_argument("--sector", help="Search sector (e.g., 半导体, 芯片)")

    args = parser.parse_args()
    em = EastmoneyFetcher()

    if args.sector:
        stocks = em.search_sector(args.sector)
        print(f"\n{args.sector} 板块 ({len(stocks)} stocks):")
        for s in stocks[:10]:
            print(f"  {s['code']:<12} {s['name']:<15} ¥{s.get('price', '?')} "
                  f"({s.get('change_pct', 0)}%)")
    elif args.financials:
        data = em.get_financials(args.ticker)
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        data = em.get_price(args.ticker)
        print(em.format_for_report(data))
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
