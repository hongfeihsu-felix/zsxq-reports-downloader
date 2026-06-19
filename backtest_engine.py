#!/usr/bin/env python3
"""
Logic Chain Backtesting Engine (Phase 2) — 真实股价回测

用 FinMind 实际股价变动替代 TP 变动，验证 aggregated_drivers 的方向一致性。
计算 30/60/90 天真实命中率，按 consensus_level / bank / driver 维度拆分。

用法:
  python3 backtest_engine.py                    # 全量回测 → 存 backtest.db
  python3 backtest_engine.py --company MediaTek  # 单公司
  python3 backtest_engine.py --stats             # 查看回测统计
  python3 backtest_engine.py --trend             # 命中率趋势 (周级)
"""

import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import re

from finmind_client import get_forward_return, get_price

LOGIC_DB = Path(__file__).parent / "logic_chains.db"
BACKTEST_DB = Path(__file__).parent / "backtest.db"

# Company → ticker mapping for FinMind lookups
COMPANY_TICKER = {
  "TSMC": "TSM",
  "台积电": "TSM",
  "NVIDIA": "NVDA",
  "英伟达": "NVDA",
  "AMD": "AMD",
  "Broadcom": "AVGO",
  "博通": "AVGO",
  "Marvell": "MRVL",
  "MediaTek": "2454",
  "联发科": "2454",
  "MediaTek Inc": "2454",
  "Intel": "INTC",
  "Qualcomm": "QCOM",
  "高通": "QCOM",
  "Micron": "MU",
  "美光": "MU",
  "SK Hynix": "005930",
  "SK海力士": "005930",
  "Samsung": "005930",
  "Apple": "AAPL",
  "Google": "GOOGL",
  "Alphabet": "GOOGL",
  "Microsoft": "MSFT",
  "Amazon": "AMZN",
  "Meta": "META",
  "ASML": "ASML",
  "SMCI": "SMCI",
  "Super Micro": "SMCI",
  "GlobalFoundries": "GFS",
  "Lumentum": "LITE",
  "Coherent": "COHR",
  "Corning": "GLW",
  "SMIC": "0981.HK",
  "中芯国际": "0981.HK",
  "Dell Technologies": "DELL",
  "Palantir": "PLTR",
  "Infineon Technologies Ag": "IFNNY",
  "Qorvo Inc": "QRVO",
  "Kioxia Holdings": "659A.T",
  "Taiyo Yuden": "6976.T",
  "TDK": "6762.T",
  "Soitec Sa": "SOI.PA",
  "Sandisk": "SNDK",
  "Gigadevice": "603986.SS",
  "Gigadevice Semiconductor Inc": "603986.SS",
  "Enlight Renewable Energy Ltd": "ENLT",
  "Hesai Group": "HSAI",
  "Meituan": "3690.HK",
  "Regis Resources Limited": "RRL.AX",
  "MetaX": "",
  "Cambricon": "688256.SS",
  "寒武纪": "688256.SS",
  "META": "META",
  "Wistron": "3231.TW",
  "Victory Giant Technology": "300476.SZ",
  "胜宏科技": "300476.SZ",
  "TFC Optical": "300394.SZ",
  "天孚通信": "300394.SZ",
  "欣兴电子": "3037.TW",
  "臻鼎科技": "4958.TW",
  "Wus（沪士电子股份有限公司）": "002463.SZ",
  "罗博特科智能科技股份有限公司": "300757.SZ",
  "小米集团": "1810.HK",
  "腾讯控股": "0700.HK",
  "阿里巴巴": "9988.HK",
  "华虹半导体有限公司": "1347.HK",
  "Ajinomoto Fine-Techno": "2802.T"
}


def _get_logic_conn():
    conn = sqlite3.connect(str(LOGIC_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _get_bt_conn():
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_real (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            ticker TEXT NOT NULL,
            driver_slug TEXT NOT NULL,
            consensus_level TEXT NOT NULL,
            driver_direction TEXT NOT NULL,
            bank_count INTEGER NOT NULL DEFAULT 0,
            event_date TEXT NOT NULL,
            base_price REAL,
            ret_30d REAL,
            ret_60d REAL,
            ret_90d REAL,
            hit_30d INTEGER DEFAULT 0,
            hit_60d INTEGER DEFAULT 0,
            hit_90d INTEGER DEFAULT 0,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(company, driver_slug)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trend (
            week_start TEXT PRIMARY KEY,
            total_drivers INTEGER,
            hit_rate_30d REAL,
            hit_rate_60d REAL,
            hit_rate_90d REAL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def resolve_ticker(company: str) -> str:
    """Map company name to ticker symbol."""
    return COMPANY_TICKER.get(company, COMPANY_TICKER.get(company.split("/")[0].strip(), ""))


def get_driver_dates(lc_company: str) -> str:
    """Get most recent report date for a company from logic_chains."""
    conn = _get_logic_conn()
    row = conn.execute(
        "SELECT date FROM logic_chains WHERE company = ? ORDER BY date DESC LIMIT 1",
        (lc_company,)
    ).fetchone()
    conn.close()
    return row["date"] if row else None


def _build_company_map() -> dict[str, str]:
    """Build fuzzy mapping from aggregated_drivers company → logic_chains company."""
    logic_conn = _get_logic_conn()
    ad = [r["company"] for r in logic_conn.execute("SELECT DISTINCT company FROM aggregated_drivers").fetchall()]
    lc = [r["company"] for r in logic_conn.execute("SELECT DISTINCT company FROM logic_chains WHERE company != ''").fetchall()]
    logic_conn.close()
    mapping = {}
    for a in ad:
        a_norm = re.sub(r'[^\w]', '', a.lower())
        if len(a_norm) < 3: continue
        for l in lc:
            l_norm = re.sub(r'[^\w]', '', l.lower())
            if a_norm in l_norm or l_norm in a_norm:
                mapping[a] = l
                break
    return mapping



def run_backtest(company_filter: str = None):
    """Run backtest using real stock prices from FinMind."""
    logic_conn = _get_logic_conn()
    bt_conn = _get_bt_conn()
    _ensure_table(bt_conn)

    if company_filter:
        rows = logic_conn.execute(
            "SELECT company, driver_slug, consensus_level, bank_count, aggregated_json FROM aggregated_drivers WHERE company = ?",
            (company_filter,)
        ).fetchall()
    else:
        rows = logic_conn.execute(
            "SELECT company, driver_slug, consensus_level, bank_count, aggregated_json FROM aggregated_drivers"
        ).fetchall()

    company_map = _build_company_map()
    results = []
    skipped = 0

    for row in rows:
        company = row["company"]
        ticker = resolve_ticker(company)
        if not ticker:
            skipped += 1
            continue

        data = json.loads(row["aggregated_json"])
        direction = data.get("direction", "neutral")
        if direction not in ("bullish", "bearish"):
            skipped += 1
            continue

        # Find event date via company name mapping
        lc_company = company_map.get(company, company)
        event_date = get_driver_dates(lc_company)
        if not event_date:
            skipped += 1
            continue

        # Get base price and forward returns
        base_price = get_price(ticker, event_date)
        if base_price is None:
            # Try next trading day
            for offset in [1, 2, 3]:
                alt = (datetime.strptime(event_date, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
                base_price = get_price(ticker, alt)
                if base_price:
                    event_date = alt
                    break
        if base_price is None:
            skipped += 1
            continue

        try:
            ret30 = get_forward_return(ticker, event_date, days=30)
            ret60 = get_forward_return(ticker, event_date, days=60)
            ret90 = get_forward_return(ticker, event_date, days=90)
        except Exception:
            skipped += 1
            continue

        # Determine hit: bullish → positive return, bearish → negative return
        hit30 = 1 if (direction == "bullish" and (ret30 or 0) > 0) or (direction == "bearish" and (ret30 or 0) < 0) else 0
        hit60 = 1 if (direction == "bullish" and (ret60 or 0) > 0) or (direction == "bearish" and (ret60 or 0) < 0) else 0
        hit90 = 1 if (direction == "bullish" and (ret90 or 0) > 0) or (direction == "bearish" and (ret90 or 0) < 0) else 0

        result = {
            "company": company, "ticker": ticker,
            "driver_slug": row["driver_slug"],
            "consensus_level": row["consensus_level"],
            "driver_direction": direction,
            "bank_count": row["bank_count"],
            "event_date": event_date, "base_price": base_price,
            "ret_30d": ret30, "ret_60d": ret60, "ret_90d": ret90,
            "hit_30d": hit30, "hit_60d": hit60, "hit_90d": hit90,
        }
        results.append(result)

        bt_conn.execute("""
            INSERT OR REPLACE INTO backtest_real
            (company, ticker, driver_slug, consensus_level, driver_direction,
             bank_count, event_date, base_price, ret_30d, ret_60d, ret_90d,
             hit_30d, hit_60d, hit_90d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (company, ticker, row["driver_slug"], row["consensus_level"], direction,
              row["bank_count"], event_date, base_price, ret30, ret60, ret90,
              hit30, hit60, hit90))

    bt_conn.commit()
    logic_conn.close()
    bt_conn.close()

    return results, skipped


def get_stats() -> dict:
    """获取回测统计：命中率 × consensus_level × 持仓周期."""
    conn = _get_bt_conn()
    _ensure_table(conn)

    total = conn.execute("SELECT COUNT(*) as c FROM backtest_real WHERE hit_30d IS NOT NULL").fetchone()["c"]
    if total == 0:
        conn.close()
        return {"total": 0}

    # Overall
    row = conn.execute("""
        SELECT
            ROUND(AVG(CASE WHEN hit_30d = 1 THEN 100.0 ELSE 0 END), 1) as hr30,
            ROUND(AVG(CASE WHEN hit_60d = 1 THEN 100.0 ELSE 0 END), 1) as hr60,
            ROUND(AVG(CASE WHEN hit_90d = 1 THEN 100.0 ELSE 0 END), 1) as hr90
        FROM backtest_real WHERE hit_30d IS NOT NULL
    """).fetchone()

    # By consensus level
    by_level = conn.execute("""
        SELECT consensus_level, COUNT(*) as total,
            ROUND(AVG(CASE WHEN hit_30d = 1 THEN 100.0 ELSE 0 END), 1) as hr30,
            ROUND(AVG(CASE WHEN hit_60d = 1 THEN 100.0 ELSE 0 END), 1) as hr60,
            ROUND(AVG(CASE WHEN hit_90d = 1 THEN 100.0 ELSE 0 END), 1) as hr90
        FROM backtest_real WHERE hit_30d IS NOT NULL
        GROUP BY consensus_level
        ORDER BY CASE consensus_level WHEN 'full' THEN 0 WHEN 'strong' THEN 1 WHEN 'partial' THEN 2 ELSE 3 END
    """).fetchall()

    # By company (top 10)
    by_company = conn.execute("""
        SELECT company, ticker, COUNT(*) as total,
            ROUND(AVG(CASE WHEN hit_30d = 1 THEN 100.0 ELSE 0 END), 1) as hr30,
            ROUND(AVG(ret_30d), 1) as avg_ret
        FROM backtest_real WHERE hit_30d IS NOT NULL
        GROUP BY company, ticker HAVING total >= 5
        ORDER BY hr30 DESC LIMIT 10
    """).fetchall()

    conn.close()

    return {
        "total": total,
        "hit_rate_30d": row["hr30"],
        "hit_rate_60d": row["hr60"],
        "hit_rate_90d": row["hr90"],
        "by_level": [dict(r) for r in by_level],
        "by_company": [dict(r) for r in by_company],
    }


def compute_trend():
    """计算周度命中率趋势，存入 backtest_trend。"""
    conn = _get_bt_conn()
    _ensure_table(conn)

    # Group by event week
    rows = conn.execute("""
        SELECT strftime('%Y-%W', event_date) as week, COUNT(*) as total,
            ROUND(AVG(CASE WHEN hit_30d = 1 THEN 100.0 ELSE 0 END), 1) as hr30,
            ROUND(AVG(CASE WHEN hit_60d = 1 THEN 100.0 ELSE 0 END), 1) as hr60,
            ROUND(AVG(CASE WHEN hit_90d = 1 THEN 100.0 ELSE 0 END), 1) as hr90
        FROM backtest_real WHERE hit_30d IS NOT NULL
        GROUP BY week HAVING total >= 3
        ORDER BY week DESC LIMIT 26
    """).fetchall()

    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO backtest_trend (week_start, total_drivers, hit_rate_30d, hit_rate_60d, hit_rate_90d)
            VALUES (?, ?, ?, ?, ?)
        """, (r["week"], r["total"], r["hr30"], r["hr60"], r["hr90"]))

    conn.commit()
    conn.close()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Backtesting with real stock prices")
    parser.add_argument("--company", help="Filter by company")
    parser.add_argument("--stats", action="store_true", help="Show backtest statistics")
    parser.add_argument("--trend", action="store_true", help="Show hit rate trend")
    parser.add_argument("--preload", action="store_true", help="Preload stock data into cache")
    args = parser.parse_args()

    if args.preload:
        from finmind_client import batch_preload
        tickers = sorted(set(COMPANY_TICKER.values()))
        batch_preload(tickers, "2026-01-01", "2026-06-09")
        return

    if args.stats:
        stats = get_stats()
        if stats["total"] == 0:
            print("No backtest data yet. Run: python3 backtest_engine.py")
            return
        print(f"=== Real Price Backtest ===")
        print(f"Total driver-price pairs: {stats['total']}")
        print(f"Hit Rate 30d: {stats['hit_rate_30d']}%  60d: {stats['hit_rate_60d']}%  90d: {stats['hit_rate_90d']}%")
        print()
        print(f"By consensus level:")
        print(f"{'Level':<10} {'Count':>6} {'30d HR':>8} {'60d HR':>8} {'90d HR':>8}")
        for l in stats["by_level"]:
            print(f"  {l['consensus_level']:<8} {l['total']:>6} {l['hr30']:>7}% {l['hr60']:>7}% {l['hr90']:>7}%")
        if stats.get("by_company"):
            print(f"\nTop companies by 30d hit rate:")
            for c in stats["by_company"]:
                print(f"  {c['company']:<15} ({c['ticker']:<6}) {c['total']:>3} drivers, HR30: {c['hr30']}%, avg ret: {c['avg_ret']:+.1f}%")
        return

    if args.trend:
        trend = compute_trend()
        print(f"{'Week':<10} {'Drivers':>7} {'30d HR':>8} {'60d HR':>8} {'90d HR':>8}")
        for r in trend[:12]:
            print(f"  {r['week_start']:<9} {r['total_drivers']:>7} {r['hit_rate_30d']:>7}% {r['hit_rate_60d']:>7}% {r['hit_rate_90d']:>7}%")
        return

    results, skipped = run_backtest(args.company)
    print(f"Backtest: {len(results)} driver-price pairs ({skipped} skipped)")
    hits30 = sum(1 for r in results if r["hit_30d"])
    hits60 = sum(1 for r in results if r["hit_60d"])
    hits90 = sum(1 for r in results if r["hit_90d"])
    total = len([r for r in results if r["hit_30d"] is not None])
    if total > 0:
        print(f"  30d Hit Rate: {round(hits30/total*100,1)}% ({hits30}/{total})")
        print(f"  60d Hit Rate: {round(hits60/total*100,1)}% ({hits60}/{total})")
        print(f"  90d Hit Rate: {round(hits90/total*100,1)}% ({hits90}/{total})")
    compute_trend()
    print("Trend updated.")


if __name__ == "__main__":
    main()
