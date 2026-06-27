#!/usr/bin/env python3
"""
Realtime Alert — monitor holdings, compute composite signals, email report.

Triggered via launchd at:
  A-share: 11:00 Beijing (mid-session)
  US:      23:30 Beijing (11:30 AM ET, mid-session)

Usage:
  python3 realtime_alert.py                          # all markets
  python3 realtime_alert.py --market A                # A-share only
  python3 realtime_alert.py --market US               # US only
  python3 realtime_alert.py --dry-run                 # console only, no email
"""

import json, hashlib, sqlite3, argparse, sys, time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
import io

PROJECT_DIR = Path(__file__).parent
HOLDINGS_DB = PROJECT_DIR / "holdings.db"
LOGIC_DB = PROJECT_DIR / "logic_chains.db"
VALUATION_DB = PROJECT_DIR / "valuation.db"
CONFIG_PATH = PROJECT_DIR / "config.json"
HISTORY_PATH = PROJECT_DIR / "push_history.json"

# ── Config ──────────────────────────────────────────────

def _load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}

CFG = _load_config()
EMAIL = CFG.get("email", {})
ALERT_CFG = CFG.get("realtime_alert", {})
LOGIC_WINDOW_DAYS = ALERT_CFG.get("logic_chain_window_days", 7)

# ── Holdings ────────────────────────────────────────────

def ensure_holdings_schema():
    """Apply lightweight local migrations for holdings alert fields."""
    conn = sqlite3.connect(str(HOLDINGS_DB))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(holdings)").fetchall()}
    if "cost_basis" not in cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN cost_basis REAL NOT NULL DEFAULT 0.0")
        conn.execute(
            "UPDATE holdings SET cost_basis = target_price "
            "WHERE direction = 'SELL' AND cost_basis = 0 AND target_price > 0"
        )
        conn.commit()
    conn.close()

def fetch_holdings(market_filter: str = "ALL") -> list[dict]:
    """Load active holdings from holdings.db."""
    ensure_holdings_schema()
    conn = sqlite3.connect(str(HOLDINGS_DB))
    conn.row_factory = sqlite3.Row
    if market_filter and market_filter != "ALL":
        rows = conn.execute(
            "SELECT * FROM holdings WHERE active = 1 AND market = ? ORDER BY direction, company",
            (market_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE active = 1 ORDER BY market, direction, company"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Price ───────────────────────────────────────────────

def get_current_price(ticker: str, market: str) -> dict:
    """Get real-time price. Returns {price, change_pct, currency} or {} on failure."""
    result = {}
    errors = []
    try:
        if market == "US" or market == "HK":
            # 1) AllTick WebSocket (primary, paid real-time)
            try:
                from data_sources.alltick import AllTickFetcher, _normalize_code
                code = _normalize_code(ticker, market)
                fetcher = AllTickFetcher()
                data = fetcher.get_snapshot(code)
                if data and data.get("price"):
                    result = {
                        "price": float(data["price"]),
                        "change_pct": float(data.get("change_pct", 0)),
                        "currency": data.get("currency", "USD" if market == "US" else "HKD"),
                        "source": "alltick",
                    }
                else:
                    detail = ";".join((data or {}).get("errors", []))
                    errors.append(f"alltick:no_data:{detail}" if detail else "alltick:no_data")
            except Exception as e:
                errors.append(f"alltick:{type(e).__name__}:{e}")

            # 2) Finnhub fallback for US
            if not result and market == "US":
                try:
                    import requests, os
                    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "socks5://127.0.0.1:7897"
                    proxies = {"http": proxy, "https": proxy}
                    fkey = CFG.get("api_keys", {}).get("finnhub", "")
                    if fkey:
                        resp = requests.get(
                            f"https://finnhub.io/api/v1/quote",
                            params={"symbol": ticker.split(".")[0], "token": fkey},
                            timeout=10, proxies=proxies
                        )
                        d = resp.json()
                        if d.get("c"):
                            result = {"price": float(d["c"]), "change_pct": float(d.get("dp", 0)),
                                      "currency": "USD", "source": "finnhub"}
                        else:
                            errors.append(f"finnhub:no_price:{d}")
                except Exception as e:
                    errors.append(f"finnhub:{type(e).__name__}:{e}")

            # 3) yfinance fallback for HK
            if not result and market == "HK":
                try:
                    import yfinance as yf
                    t = yf.Ticker(ticker)
                    info = t.info or {}
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                    prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
                    if not price:
                        fi = t.fast_info
                        price = fi.get("lastPrice")
                        prev = fi.get("previousClose")
                    if price:
                        chg = ((price - prev) / prev * 100) if prev else 0
                        result = {"price": float(price), "change_pct": round(float(chg), 2),
                                  "currency": "HKD", "source": "yfinance"}
                    else:
                        errors.append("yfinance:no_price")
                except Exception as e:
                    errors.append(f"yfinance:{type(e).__name__}:{e}")
        else:
            # A-share: QMT bridge → Eastmoney → baostock
            price = None
            chg = 0.0
            QMT_HOST = "124.220.26.164"
            QMT_PORT = 8766
            today_str = datetime.now().strftime("%Y%m%d")

            # 1) QMT bridge real-time
            try:
                import requests
                session = requests.Session()
                session.trust_env = False
                resp = session.get(
                    f"http://{QMT_HOST}:{QMT_PORT}/bars",
                    params={"symbol": ticker, "start": today_str, "end": today_str, "interval": "1m"},
                    timeout=5
                )
                bars_data = resp.json().get("bars", [])
                if bars_data:
                    last = bars_data[-1]
                    price = float(last.get("close", 0))
                    prev_close = _get_prev_close_from_qmt(ticker, today_str)
                    if prev_close:
                        chg = round((price - prev_close) / prev_close * 100, 2)
                        chg_basis = "prev_close"
                    else:
                        first = bars_data[0]
                        open_px = float(first.get("open", price))
                        chg = round((price - open_px) / open_px * 100, 2) if open_px else 0.0
                        chg_basis = "intraday_open"
                    result = {"price": price, "change_pct": chg, "currency": "CNY",
                              "source": "qmt_bridge", "change_basis": chg_basis}
                else:
                    errors.append("qmt_bridge:no_bars")
            except Exception as e:
                errors.append(f"qmt_bridge:{type(e).__name__}:{e}")

            # 2) Eastmoney
            if price is None:
                try:
                    from data_sources.eastmoney import EastmoneyFetcher
                    em = EastmoneyFetcher()
                    data = em.get_price(ticker)
                    if data and data.get("price") is not None:
                        price = float(data["price"])
                        chg = float(data.get("change_pct", 0))
                        result = {"price": price, "change_pct": round(chg, 2), "currency": "CNY",
                                  "source": "eastmoney", "change_basis": "prev_close"}
                    else:
                        errors.append("eastmoney:no_data")
                except Exception as e:
                    errors.append(f"eastmoney:{type(e).__name__}:{e}")

            # 3) baostock last-resort (T+1 delayed)
            if price is None:
                try:
                    import baostock as bs
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        bs.login()
                    code = ticker.split(".")[0]
                    exchange = "sh" if code.startswith("6") or code.startswith("68") else "sz"
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        rs = bs.query_history_k_data_plus(
                            f"{exchange}.{code}",
                            "date,close,pctChg",
                            start_date=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
                            end_date=datetime.now().strftime("%Y-%m-%d"),
                            frequency="d", adjustflag="1"
                        )
                        bars = []
                        while (rs.error_code == '0') & rs.next():
                            bars.append(rs.get_row_data())
                        bs.logout()
                    if bars:
                        price = float(bars[-1][1])
                        chg = float(bars[-1][2]) if bars[-1][2] else 0.0
                        result = {"price": price, "change_pct": round(chg, 2), "currency": "CNY",
                                  "source": "baostock_stale", "stale": True,
                                  "change_basis": "prev_close"}
                    else:
                        errors.append("baostock:no_bars")
                except Exception as e:
                    errors.append(f"baostock:{type(e).__name__}:{e}")

            if price is not None:
                result.setdefault("price", price)
                result.setdefault("change_pct", round(chg, 2))
                result.setdefault("currency", "CNY")
    except Exception as e:
        errors.append(f"unexpected:{type(e).__name__}:{e}")
    if errors and result:
        result["warnings"] = errors
    if errors and not result:
        return {"errors": errors}
    return result


def _get_prev_close_from_qmt(ticker: str, today_str: str) -> float | None:
    try:
        import requests
        QMT_HOST = "124.220.26.164"
        QMT_PORT = 8766
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        session = requests.Session()
        session.trust_env = False
        resp = session.get(
            f"http://{QMT_HOST}:{QMT_PORT}/bars",
            params={"symbol": ticker, "start": start, "end": today_str, "interval": "daily"},
            timeout=5
        )
        bars = resp.json().get("bars", [])
        prev = []
        for b in bars:
            ts = str(b.get("timestamp") or b.get("date") or "")
            if today_str not in ts.replace("-", ""):
                prev.append(b)
        if prev:
            return float(prev[-1].get("close", 0) or 0) or None
    except Exception:
        return None
    return None


# ── Research Signals ────────────────────────────────────

def _resolve_company_name(ticker: str) -> tuple[str | None, float]:
    """Map ticker → (canonical company name, AH premium ratio).

    For A-share stocks cross-listed with H-shares, returns premium > 1.0
    to adjust H-share consensus TP upward for A-share valuation.
    """
    from entity_resolver import resolve_company
    match = resolve_company(ticker)
    if not match:
        return None, 1.0
    return match.name, match.ah_premium


def get_research_signals(company: str | None, ticker: str) -> dict:
    """Query logic_chains for recent bearish/bullish counts and consensus TP.

    Returns:
        {bearish_high, bearish_medium, bullish_high, bullish_medium,
         consensus_tp, ratings, research_score}
    """
    result = {
        "bearish_high": 0, "bearish_medium": 0,
        "bullish_high": 0, "bullish_medium": 0,
        "consensus_tp": None, "ratings": {}, "research_score": 0.0,
        "top_bearish": [], "top_bullish": [],
    }

    # Logic chains
    if company:
        try:
            conn = sqlite3.connect(str(LOGIC_DB))
            conn.row_factory = sqlite3.Row
            cutoff = (datetime.now() - timedelta(days=LOGIC_WINDOW_DAYS)).strftime("%Y-%m-%d")
            rows = conn.execute(
                """SELECT * FROM logic_chains
                   WHERE (company LIKE ? OR company = ? OR ticker = ?)
                   AND date >= ? AND direction IN ('bearish','bullish')
                   ORDER BY date DESC""",
                (f"%{company}%", company, ticker.split(".")[0], cutoff)
            ).fetchall()
            conn.close()

            for r in rows:
                d = dict(r)
                direction = d.get("direction", "")
                confidence = d.get("confidence", "medium")
                driver = (d.get("driver_slug") or "")[:80]
                bank = d.get("bank", "?") or "?"

                if direction == "bearish":
                    if confidence == "high":
                        result["bearish_high"] += 1
                    else:
                        result["bearish_medium"] += 1
                    if len(result["top_bearish"]) < 3:
                        result["top_bearish"].append(f"{bank}: {driver}")
                elif direction == "bullish":
                    if confidence == "high":
                        result["bullish_high"] += 1
                    else:
                        result["bullish_medium"] += 1
                    if len(result["top_bullish"]) < 3:
                        result["top_bullish"].append(f"{bank}: {driver}")
        except Exception:
            pass

    # Valuation consensus TP
    try:
        co = company or ticker.split(".")[0]
        from valuation_store import ValuationStore
        store = ValuationStore(VALUATION_DB)
        vdicts = store.get_by_company(co, months=3)
        store.close()

        if vdicts:
            from valuation_consensus import compute_consensus
            # Normalize field names
            for v in vdicts:
                if "tp_new" not in v:
                    v["tp_new"] = v.get("tp", v.get("target_price"))
                if "pe" not in v:
                    v["pe"] = v.get("pe_current")
            consensus = compute_consensus(vdicts)
            if consensus.get("cs_tp"):
                result["consensus_tp"] = consensus["cs_tp"]
            result["ratings"] = consensus.get("ratings", {})
    except Exception:
        pass

    # Score
    score = 0.0
    score += result["bullish_high"] * 2 + result["bullish_medium"] * 1
    score -= result["bearish_high"] * 2 + result["bearish_medium"] * 1
    result["research_score"] = round(score, 1)
    return result


# ── Technicals ──────────────────────────────────────────

def get_daily_bars(ticker: str, market: str, days: int = 80) -> list:
    """Fetch daily bars. Returns list of dicts with close/high/low/volume/open."""
    import baostock as bs
    end = datetime.now()
    start = end - timedelta(days=days + 10)

    bars = []
    try:
        import requests as _req, os as _os
        proxy = _os.environ.get("HTTPS_PROXY") or _os.environ.get("HTTP_PROXY") or "socks5://127.0.0.1:7897"
        proxies = {"http": proxy, "https": proxy}

        if market == "US" or market == "HK":
            # AllTick REST kline (primary, paid real-time)
            try:
                from data_sources.alltick import AllTickFetcher, _normalize_code
                code = _normalize_code(ticker, market)
                fetcher = AllTickFetcher()
                klines = fetcher.get_kline(code, count=days)
                if klines:
                    bars = klines
            except Exception:
                pass
            # Fallback: yfinance
            if not bars:
                try:
                    import yfinance as yf
                    sym = ticker if market == "HK" else ticker.split(".")[0]
                    t = yf.Ticker(sym)
                    hist = t.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
                    for idx, row in hist.iterrows():
                        bars.append({
                            "close": float(row["Close"]), "open": float(row["Open"]),
                            "high": float(row["High"]), "low": float(row["Low"]),
                            "volume": float(row["Volume"]),
                        })
                except Exception:
                    pass
        else:
            # A-share: use baostock directly
            bs.login()
            code = ticker.split(".")[0]
            exchange = "sh" if code.startswith("6") or code.startswith("68") else "sz"
            rs = bs.query_history_k_data_plus(
                f"{exchange}.{code}",
                "date,open,high,low,close,volume",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency="d", adjustflag="2"
            )
            while (rs.error_code == '0') & rs.next():
                r = rs.get_row_data()
                bars.append({
                    "close": float(r[4]), "open": float(r[1]),
                    "high": float(r[2]), "low": float(r[3]),
                    "volume": float(r[5]),
                })
            bs.logout()
    except Exception:
        pass

    return bars


from signal_scorer import compute_technicals, compute_sell_signal, compute_buy_signal


# ── Email ───────────────────────────────────────────────

def format_email_html(results: list[dict]) -> str:
    """Format signal report as HTML email with proper tables."""
    now = datetime.now()
    hour = now.hour
    if 9 <= hour < 12:
        session = "A-share / HK Mid-Session"
    elif 21 <= hour or hour < 5:
        session = "US Mid-Session"
    else:
        session = "Manual Run"

    sell_items = [r for r in results if r["direction"] == "SELL"]
    buy_items = [r for r in results if r["direction"] == "BUY"]
    sell_count = sum(1 for r in sell_items if r["signal"]["action"] == "SELL")
    buy_now_count = sum(1 for r in buy_items if r["signal"]["action"] == "BUY_NOW")
    watch_count = sum(1 for r in buy_items if r["signal"]["action"] == "WATCH")

    total_pnl = 0.0
    for r in sell_items:
        p = r.get("price", {}).get("price", 0)
        cost = r.get("cost_basis", 0)
        shares = r.get("shares", 0)
        if p and cost and shares:
            total_pnl += (p - cost) * shares

    def _row_style(signal):
        if signal["action"] == "SELL": return "background:#ffebe9"
        if signal["action"] in ("BUY","BUY_NOW"): return "background:#e6ffec"
        return ""

    def _icon(action):
        return {"BUY":"🟢","BUY_NOW":"🟢","SELL":"🔴","WATCH":"🟡"}.get(action,"⚪")

    def _price(r):
        p = r.get("price", {}).get("price", 0)
        return f"¥{p:,.2f}" if r["market"] != "US" else f"${p:,.2f}"

    def _chg(r):
        chg = r.get("price", {}).get("change_pct", 0)
        color = "#f85149" if chg < 0 else "#3fb950"
        return f'<span style="color:{color}">{chg:+.1f}%</span>' if chg else "-"

    parts = [f"""
<html><body style="background:#ffffff;color:#1f2328;font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:20px">
<div style="max-width:900px;margin:0 auto">
<h2 style="margin:0 0 4px">📊 Holdings Signal</h2>
<p style="color:#656d76;font-size:13px;margin:0 0 20px">{now.strftime('%Y-%m-%d %H:%M')} · {session}</p>

<div style="background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:16px;margin-bottom:16px">
<table style="width:100%;border-collapse:collapse;font-size:13px">
<tr>
  <td style="padding:8px 16px;color:#656d76">Positions</td>
  <td style="padding:8px 16px;font-weight:700">{len(sell_items)}</td>
  <td style="padding:8px 16px;color:#656d76">Watchlist</td>
  <td style="padding:8px 16px;font-weight:700">{len(buy_items)}</td>
  <td style="padding:8px 16px;color:#cf222e;font-weight:700">SELL {sell_count}</td>
  <td style="padding:8px 16px;color:#1a7f37;font-weight:700">BUY {buy_now_count}</td>
  <td style="padding:8px 16px;color:#9a6700">WATCH {watch_count}</td>
</tr>
</table>
</div>
"""]

    if sell_items:
        rows_html = ""
        for r in sorted(sell_items, key=lambda x: x["signal"]["score"]):
            s = r["signal"]
            ind = s.get("details", "")
            if s.get("top_bearish"):
                ind += f' <span style="color:#cf222e">⚠️{s["top_bearish"][0][:40]}</span>'
            rows_html += f"""<tr style="{_row_style(s)}">
  <td style="padding:6px 10px">{_icon(s['action'])} {r['company']}<br><span style="color:#656d76;font-size:11px">{r['ticker']} {r.get('ma3_regime','')=='up' and '📈' or r.get('ma3_regime','')=='down' and '📉' or ''}</span></td>
  <td style="padding:6px 10px;text-align:right;font-weight:600">{_price(r)}<br><span style="font-size:11px">{_chg(r)}</span></td>
  <td style="padding:6px 10px;text-align:center;font-weight:700">{s['action']}</td>
  <td style="padding:6px 10px;text-align:right;font-weight:700">{s['score']:+.1f}</td>
  <td style="padding:6px 10px;font-size:11px;color:#656d76">{ind}</td>
</tr>"""
        parts.append(f"""
<div style="background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:16px;margin-bottom:16px">
<h3 style="margin:0 0 12px;font-size:14px;color:#cf222e">🔴 持仓预警</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<thead><tr style="background:#ffffff">
  <th style="padding:8px 10px;text-align:left;color:#656d76">标的</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">现价</th>
  <th style="padding:8px 10px;text-align:center;color:#656d76">信号</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">评分</th>
  <th style="padding:8px 10px;text-align:left;color:#656d76">关键指标</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table></div>""")

    if buy_items:
        rows_html = ""
        for r in sorted(buy_items, key=lambda x: x["signal"]["score"], reverse=True):
            s = r["signal"]
            target = r.get("target_price", 0)
            p = r.get("price", {}).get("price", 0)
            gap = f"{(p/target-1)*100:+.1f}%" if target and p else "-"
            gap_color = "#3fb950" if gap.startswith("-") else "#d29922"
            ind = f"Target: {target:.0f}"
            if s.get("top_bullish"):
                ind += f' <span style="color:#1a7f37">📈{s["top_bullish"][0][:40]}</span>'
            rows_html += f"""<tr style="{_row_style(s)}">
  <td style="padding:6px 10px">{_icon(s['action'])} {r['company']}<br><span style="color:#656d76;font-size:11px">{r['ticker']} {r.get('ma3_regime','')=='up' and '📈' or r.get('ma3_regime','')=='down' and '📉' or ''}</span></td>
  <td style="padding:6px 10px;text-align:right;font-weight:600">{_price(r)}</td>
  <td style="padding:6px 10px;text-align:right">{target:,.2f}</td>
  <td style="padding:6px 10px;text-align:right"><span style="color:{gap_color}">{gap}</span></td>
  <td style="padding:6px 10px;text-align:center;font-weight:700">{s['action']}</td>
  <td style="padding:6px 10px;text-align:right;font-weight:700">{s['score']:+.1f}</td>
  <td style="padding:6px 10px;font-size:11px;color:#656d76">{ind}</td>
</tr>"""
        parts.append(f"""
<div style="background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:16px;margin-bottom:16px">
<h3 style="margin:0 0 12px;font-size:14px;color:#1a7f37">🟢 买入监控</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<thead><tr style="background:#ffffff">
  <th style="padding:8px 10px;text-align:left;color:#656d76">标的</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">现价</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">目标价</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">差距</th>
  <th style="padding:8px 10px;text-align:center;color:#656d76">信号</th>
  <th style="padding:8px 10px;text-align:right;color:#656d76">评分</th>
  <th style="padding:8px 10px;text-align:left;color:#656d76">备注</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table></div>""")

    # Macro event risk warnings
    from macro_events import get_event_risk
    risk = get_event_risk()
    macro_html = ""
    if risk["warnings"]:
        warn_items = "".join(f'<li>{w}</li>' for w in risk["warnings"])
        macro_html = f"""
<div style="background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:12px;margin-bottom:16px">
  <span style="color:#9a6700;font-weight:700">⚠️ Macro Event Risk — Score: {risk["score"]:.1f}</span>
  <ul style="margin:8px 0 0;color:#9a6700;font-size:12px">{warn_items}</ul>
</div>"""

    pnl_color = "#f85149" if total_pnl < 0 else "#3fb950"
    parts.append(f"""
<div style="text-align:center;color:#656d76;font-size:12px;margin-top:16px">
  Portfolio P&amp;L: <span style="color:{pnl_color};font-weight:700">{total_pnl:+,.0f}</span> | {now.strftime('%Y-%m-%d %H:%M')}<br>
  Auto-generated by Hermes Realtime Alert
</div></div></body></html>""")

    # Insert macro warning after header but before tables
    if macro_html:
        parts.insert(2, macro_html)
    return "\n".join(parts)


def format_email(results: list[dict]) -> str:
    """Plain text format kept as fallback."""
    return format_email_html(results)


def send_email(html_body: str) -> bool:
    """Send HTML signal report email with plain-text fallback."""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import smtplib

    if not html_body.strip():
        return True

    recipient = EMAIL.get("recipient_email", "")
    sender = EMAIL.get("sender_email", "")
    if not recipient or not sender or not EMAIL.get("smtp_server"):
        print("⚠️  Email not configured.")
        return False

    # Generate plain text fallback
    import re
    plain = re.sub(r'<[^>]+>', '', html_body)
    plain = re.sub(r'\n\s+', '\n', plain)

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient

    now_str = datetime.now().strftime("%Y-%m-%d")
    sell_c = html_body.count("SELL")
    buy_c = html_body.count("BUY_NOW")
    msg["Subject"] = f"📊 Holdings Signal — {now_str} (S:{sell_c} B:{buy_c})"

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP(EMAIL["smtp_server"], EMAIL["smtp_port"])
        server.starttls()
        server.login(sender, EMAIL.get("sender_password", ""))
        server.send_message(msg)
        server.quit()
        print(f"📧 Holdings signal email sent to {recipient}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


def format_health_email(failures: list[dict], market: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = ""
    for f in failures:
        errors = "<br>".join(f.get("errors", [])[:4]) or "unknown"
        rows += f"""<tr>
  <td style="padding:6px 10px">{f['company']}<br><span style="color:#656d76;font-size:11px">{f['ticker']}</span></td>
  <td style="padding:6px 10px">{f['market']}</td>
  <td style="padding:6px 10px;color:#cf222e;font-size:11px">{errors}</td>
</tr>"""
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:20px">
<h2 style="color:#cf222e">🔴 Realtime Alert Data Source Failure</h2>
<p>{now} · market={market} · failed={len(failures)}</p>
<table style="border-collapse:collapse;font-size:13px">
<thead><tr style="background:#f6f8fa"><th style="padding:8px 10px">标的</th><th style="padding:8px 10px">市场</th><th style="padding:8px 10px">错误</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""

# ── Dedup ───────────────────────────────────────────────

def _signal_key(holding: dict) -> str:
    raw = f"{holding['ticker']}|{datetime.now().strftime('%Y-%m-%d')}|{holding['signal']['action']}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_sent_keys() -> set[str]:
    if not HISTORY_PATH.exists():
        return set()
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return set()
    return {h.get("key", "") for h in history if h.get("type") == "realtime_alert"}


def record_sent(results: list[dict]):
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            history = []

    for r in results:
        history.append({
            "type": "realtime_alert",
            "key": _signal_key(r),
            "ticker": r["ticker"],
            "direction": r["direction"],
            "action": r["signal"]["action"],
            "score": r["signal"]["score"],
            "pushed_at": time.time(),
        })

    HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False))


def log_alert(holding_id: int, signal: dict, price: float) -> int | None:
    """Persist alert to alert_log table."""
    try:
        conn = sqlite3.connect(str(HOLDINGS_DB))
        cur = conn.execute(
            """INSERT INTO alert_log (holding_id, signal, score, research_score, technical_score,
               valuation_score, price, details, sent)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (holding_id, signal["action"], signal["score"],
             signal.get("research_score", 0), signal.get("technical_score", 0),
             signal.get("valuation_score", 0), price,
             json.dumps(signal.get("details", ""), ensure_ascii=False))
        )
        conn.commit()
        alert_id = cur.lastrowid
        conn.close()
        return alert_id
    except Exception as e:
        print(f"⚠️ alert_log write failed: {e}")
        return None


def mark_alerts_sent(alert_ids: list[int]):
    if not alert_ids:
        return
    conn = sqlite3.connect(str(HOLDINGS_DB))
    placeholders = ",".join("?" for _ in alert_ids)
    conn.execute(f"UPDATE alert_log SET sent = 1 WHERE id IN ({placeholders})", alert_ids)
    conn.commit()
    conn.close()


# ── Main ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Realtime Holdings Alert")
    parser.add_argument("--market", choices=["A", "US", "HK", "ALL"], default="ALL")
    parser.add_argument("--dry-run", action="store_true",
                        help="Console only, no email")
    parser.add_argument("--force", action="store_true",
                        help="Skip trading day check")
    args = parser.parse_args()

    # Skip weekends for scheduled runs (manual --force overrides)
    if not args.force:
        wd = datetime.now().weekday()
        if wd >= 5:  # Saturday=5, Sunday=6
            print(f"⏭️  Skipping — weekend (weekday={wd})")
            return

    # 1. Fetch holdings
    holdings = fetch_holdings(market_filter=args.market)
    if not holdings:
        print(f"⚠️  No active holdings found for market={args.market}")
        return

    print(f"📋 Processing {len(holdings)} holdings ({args.market})...")

    # 2. Process each holding
    results = []
    failures = []
    alert_ids_by_key = {}
    for h in holdings:
        ticker = h["ticker"]
        market = h["market"]
        company = h["company"]
        direction = h["direction"]
        target_price = h.get("target_price", 0)

        print(f"  {direction} {company} ({ticker})...", end=" ")

        # Price
        price_data = get_current_price(ticker, market)
        current_price = price_data.get("price")
        if not current_price:
            errors = price_data.get("errors", ["no price"])
            failures.append({
                "id": h["id"], "ticker": ticker, "company": company,
                "market": market, "errors": errors,
            })
            print(f"⚠️ no price, skip ({'; '.join(errors[:2])})")
            continue

        # Research
        co_name, ah_premium = _resolve_company_name(ticker)
        research = get_research_signals(co_name, ticker)
        # Apply AH premium to consensus TP
        if ah_premium != 1.0 and research.get("consensus_tp"):
            research["consensus_tp"] = research["consensus_tp"] * ah_premium

        # Technicals
        bars = get_daily_bars(ticker, market, days=80)
        technicals = compute_technicals(bars, session_hour=datetime.now().hour) if bars else {}

        # MA3 daily regime (A-share: 三日线不能破)
        regime = "auto"
        if bars and len(bars) >= 3:
            ma3 = sum(b["close"] for b in bars[-3:]) / 3
            last_close = bars[-1]["close"]
            regime = "up" if last_close > ma3 else "down"

        # Signal
        if direction == "SELL":
            signal = compute_sell_signal(research, technicals, current_price,
                                          cost_basis=h.get("cost_basis", 0))
            # MA3 regime modifier: downtrend → stronger sell signal
            if regime == "down":
                signal["score"] = min(1.0, signal.get("score", 0) + 0.3)
                signal["details"] = f'📉 MA3↓ {signal.get("details", "")}'
            elif regime == "up":
                signal["score"] = max(0.0, signal.get("score", 0) - 0.2)
        else:
            signal = compute_buy_signal(research, technicals, current_price, target_price)
            # MA3 regime modifier: uptrend → stronger buy signal
            if regime == "up":
                signal["score"] = min(1.0, signal.get("score", 0) + 0.2)
                signal["details"] = f'📈 MA3↑ {signal.get("details", "")}'
            elif regime == "down":
                signal["score"] = max(0.0, signal.get("score", 0) - 0.3)

        result = {
            "id": h["id"], "ticker": ticker, "company": company,
            "market": market, "direction": direction,
            "shares": h["shares"], "target_price": target_price,
            "cost_basis": h.get("cost_basis", 0),
            "price": price_data, "signal": signal,
            "ma3_regime": regime,
        }
        results.append(result)

        # Log to DB only for real sends. Dry-run must be side-effect free.
        if not args.dry_run:
            alert_id = log_alert(h["id"], signal, current_price)
            if alert_id:
                alert_ids_by_key[_signal_key(result)] = alert_id

        print(f"{signal['action']} ({signal['score']:+.1f})")

    if not results:
        print("⚠️  No results generated.")
        if failures and not args.dry_run:
            ok = send_email(format_health_email(failures, args.market))
            print("✅ Data-source failure alert sent." if ok else "❌ Data-source failure alert failed.")
        elif failures:
            print(json.dumps(failures, ensure_ascii=False, indent=2))
        return

    # 3. Dedup
    sent_keys = load_sent_keys()
    new_results = [r for r in results if _signal_key(r) not in sent_keys]

    if not new_results and not args.dry_run:
        print("✅ All signals already sent today.")
        if failures:
            ok = send_email(format_health_email(failures, args.market))
            print("✅ Partial data-source failure alert sent."
                  if ok else "❌ Partial data-source failure alert failed.")
        return

    # 4. Format
    body = format_email(results if args.dry_run else new_results)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN — Signal Report")
        print("=" * 60)
        print(body)
        print("\n🏃 Dry run — email not sent.")
        if failures:
            print("\nPartial data-source failures:")
            print(json.dumps(failures, ensure_ascii=False, indent=2))
        return

    # 5. Send
    ok = send_email(body)
    if ok:
        record_sent(new_results)
        mark_alerts_sent([alert_ids_by_key[_signal_key(r)] for r in new_results
                          if _signal_key(r) in alert_ids_by_key])
        print(f"✅ Report sent ({len(new_results)} signals).")
        if failures:
            health_ok = send_email(format_health_email(failures, args.market))
            print("✅ Partial data-source failure alert sent."
                  if health_ok else "❌ Partial data-source failure alert failed.")
    else:
        print("❌ Email send failed.")



if __name__ == "__main__":
    main()
