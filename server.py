#!/usr/bin/env python3
"""
Hermes Dashboard Server — HTML 投研仪表盘

用法：
  python3 server.py                  # 启动 (默认 http://localhost:8899)
  python3 server.py --port 8900      # 自定义端口
  python3 server.py --host 0.0.0.0   # 局域网访问
"""

import os
import re
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from flask import Flask, jsonify, render_template, request
from utils import extract_bank_from_filename
from report_index import ReportIndex
from routes.valuation import bp as valuation_bp

PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
DB_PATH = PROJECT_DIR / "industry_metrics.db"
CONFIG_PATH = PROJECT_DIR / "config.json"

import threading
_config_cache = None
_config_lock = threading.Lock()


def _read_config():
    global _config_cache
    with _config_lock:
        if _config_cache is None:
            _config_cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return dict(_config_cache)


def _write_config(cfg):
    global _config_cache
    with _config_lock:
        _config_cache = cfg
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

app = Flask(__name__, template_folder=str(PROJECT_DIR / "templates"),
            static_folder=str(PROJECT_DIR / "static"), static_url_path="/static")
app.register_blueprint(valuation_bp)


# ============ Data Loaders ============

_analyses_cache = {"data": None, "ts": 0}

def load_analyses() -> list[dict]:
    now = _time.time()
    if _analyses_cache["data"] is not None and (now - _analyses_cache["ts"]) < 600:
        return _analyses_cache["data"]

    results = []
    seen = set()
    for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pdf_name = data.get("pdf_name", f.stem)
            import unicodedata
            norm = unicodedata.normalize('NFKD', pdf_name)
            norm = re.sub(r'[^\w]', '', norm).lower()
            if norm in seen:
                continue
            seen.add(norm)
            data["_path"] = str(f)
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    _analyses_cache["data"] = results
    _analyses_cache["ts"] = now
    return results


_consensus_cache = {"data": None, "ts": 0}

def load_consensus() -> list[dict]:
    now = _time.time()
    if _consensus_cache["data"] is not None and (now - _consensus_cache["ts"]) < 600:
        return _consensus_cache["data"]

    results = []
    for f in sorted(REPORT_BASE.rglob("CONSENSUS_*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            company = f.stem.replace("CONSENSUS_", "").rsplit("_", 1)[0]
            # Extract key fields
            rating_m = re.search(r'Rating\s*Consensus[:\s]*\*?\*?(.*?)\*?\*?\s*\(', text, re.IGNORECASE)
            direction_m = re.search(r'Consensus\s*Direction[:\s]*\*?\*?(Bullish|Neutral|Bearish)', text, re.IGNORECASE)
            signal_m = re.search(r'Signal\s*Strength[:\s]*\*?\*?(Strong|Moderate|Weak)', text, re.IGNORECASE)
            upside_m = re.search(r'(\d+\.?\d*)\s*%\s*upside', text, re.IGNORECASE)
            tp_m = re.search(r'Average[:\s]*.*?([\d,]+)', text)

            results.append({
                "company": company,
                "rating": rating_m.group(1).strip() if rating_m else "",
                "direction": direction_m.group(1) if direction_m else "",
                "signal": signal_m.group(1) if signal_m else "",
                "upside": float(upside_m.group(1)) if upside_m else 0,
            })
        except Exception:
            continue
    _consensus_cache["data"] = results
    _consensus_cache["ts"] = now
    return results


def load_industry_metrics() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT metric_slug, year, AVG(value) as v, unit, COUNT(*) as n
        FROM data_points WHERE value > 0
        GROUP BY metric_slug, year ORDER BY metric_slug, year
    """).fetchall()
    conn.close()
    result = defaultdict(list)
    for r in rows:
        result[r[0]].append({"year": r[1], "value": round(r[2], 1), "unit": r[3], "points": r[4]})
    return dict(result)


def load_news(limit: int = 10) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT title, source, url, published, topic FROM news_articles ORDER BY published DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [{"title": r[0], "source": r[1], "url": r[2], "published": r[3], "topic": r[4]} for r in rows]


def load_stock_prices() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT symbol, data_json FROM stock_prices ORDER BY symbol"
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        try:
            d = json.loads(r[1])
            d["_symbol"] = r[0]
            results.append(d)
        except json.JSONDecodeError:
            pass
    return results


_stats_cache = {"data": None, "ts": 0}

def _count_downloaded_reports() -> int:
    """Count downloaded source reports from DB without scanning report folders."""
    db_path = PROJECT_DIR / "zsxq_reports.db"
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT COUNT(*) FROM downloaded_files WHERE status='success'"
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _load_dashboard_from_index() -> dict | None:
    """Load homepage counters from report_index.db."""
    try:
        with ReportIndex() as idx:
            return idx.get_dashboard_summary()
    except Exception:
        return None


def load_pipeline_stats() -> dict:
    now = _time.time()
    if _stats_cache["data"] is not None and (now - _stats_cache["ts"]) < 600:
        return _stats_cache["data"]

    indexed = _load_dashboard_from_index()
    total_pdfs = _count_downloaded_reports()
    if indexed:
        analyzed = indexed["analyzed"]
    else:
        analyzed = len(list(REPORT_BASE.rglob("*_analysis.md")))
    if total_pdfs <= 0:
        total_pdfs = analyzed
    result = {
        "total": total_pdfs,
        "analyzed": analyzed,
        "consensus": 0,
        "industry": 0,
        "pct": round(analyzed / total_pdfs * 100, 1) if total_pdfs > 0 else 0
    }
    _stats_cache["data"] = result
    _stats_cache["ts"] = now
    return result


def _best_company_name(co_match: str) -> str:
    """Extract best display name from company_match CSV list."""
    parts = [p.strip() for p in co_match.split(",") if p.strip()]
    if not parts:
        return ""
    # Prefer mixed-case entries (proper names), longest first
    mixed = [p for p in parts if not p.isupper() and not p.islower() and not p.isdigit()]
    if mixed:
        return max(mixed, key=len)
    # Fallback: all-caps ticker-like
    caps = [p for p in parts if p.isupper()]
    if caps:
        return max(caps, key=len)
    # All lowercase — title case it
    return parts[0].title()


def _parse_report_label(name: str, co_match: str, ind_match: str):
    """Return (label, link_url) for a downloaded report."""
    # Try company first
    co_name = _best_company_name(co_match)
    if co_name:
        return co_name, f"/company/{co_name}"

    # Try industry — use first slug
    if ind_match:
        slug = ind_match.split(",")[0].strip()
        return slug, f"/industry/report/{slug}"

    # Fallback: extract from filename
    # Format: "Bank-Company Name（TICKER）Title-YYMMDD.pdf"
    parts = name.split("-")
    if len(parts) >= 2:
        label = parts[1].split("（")[0].split("(")[0].strip()
        if label and len(label) > 1:
            return label[:40], ""

    return name[:40], ""


def get_today_report_rows():
    """Query most recent pipeline downloads, return list of report row dicts."""
    import sqlite3
    from utils import extract_bank_from_filename

    db_path = PROJECT_DIR / "zsxq_reports.db"
    if not db_path.exists():
        return "", []

    conn = sqlite3.connect(str(db_path))

    # Get most recent download_date (pipeline runs ~11pm → yesterday's data)
    date_row = conn.execute(
        "SELECT DISTINCT download_date FROM downloaded_files "
        "WHERE status='success' ORDER BY download_date DESC LIMIT 1"
    ).fetchone()
    if not date_row:
        conn.close()
        return "", []

    latest_date = date_row[0]
    cur = conn.execute(
        "SELECT file_name, industry_match, company_match FROM downloaded_files "
        "WHERE download_date=? AND status='success' ORDER BY download_time DESC",
        (latest_date,)
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "", []

    # Alert info from analysis JSONs
    report_dir = REPORT_BASE / latest_date
    alert_cache = {}
    if report_dir.exists():
        for jf in report_dir.glob("*_analysis.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                parsed = data.get("parsed", {})
                sev = parsed.get("alert_severity", "")
                if sev in ("high", "medium"):
                    pdf_name = data.get("pdf_name", jf.stem.replace("_analysis", ""))
                    alert_cache[pdf_name] = sev
            except Exception:
                pass

    results = []
    for name, ind_match, co_match in rows:
        bank = extract_bank_from_filename(name) or "?"
        label, link = _parse_report_label(name, co_match, ind_match)

        sev = alert_cache.get(name, "")
        if sev == "high":
            alert = "🔴 High"
        elif sev == "medium":
            alert = "🟡 Medium"
        else:
            alert = "—"

        results.append({
            "bank": bank,
            "label": label.strip(),
            "link": link,
            "name": name,
            "alert": alert,
        })

    return latest_date, results


def _load_matrix():
    """加载 Company↔Industry Matrix。"""
    matrix_path = PROJECT_DIR / "industry_matrix.json"
    if matrix_path.exists():
        return json.loads(matrix_path.read_text(encoding="utf-8"))
    return {"companies": {}, "industries": {}, "unmapped": []}


def _render_company_industry_badge(company_name: str) -> str:
    """渲染公司页面上的行业标签，链接到行业页面。"""
    matrix = _load_matrix()
    co_info = matrix.get("companies", {}).get(company_name)
    if not co_info:
        return ""
    slug = co_info.get("industry_slug", "")
    ind_name = co_info.get("industry", "")
    if not slug:
        return ""
    return f' · <a href="/industry/{slug}" style="font-size:12px;color:#58a6ff">🏭 {ind_name}</a>'


def _render_today_table(rows):
    """Render the scrollable pipeline download results table."""
    if not rows:
        return '<div style="color:#8b949e;font-size:12px;padding:12px 0">暂无今日研报数据。运行 pipeline 下载后将自动填充。</div>'

    header = (
        '<div class="report-scroll"><table class="report-table">'
        '<thead><tr>'
        '<th>公司 / 行业</th>'
        '<th>投行</th>'
        '<th>Alert</th>'
        '</tr></thead><tbody>'
    )
    body_parts = []
    for r in rows:
        alert_cls = ""
        if "High" in r["alert"]:
            alert_cls = "alert-high"
        elif "Medium" in r["alert"]:
            alert_cls = "alert-medium"

        label_html = (
            f'<a href="{r["link"]}">{r["label"]}</a>' if r["link"]
            else r["label"]
        )
        body_parts.append(
            f'<tr>'
            f'<td>{label_html}</td>'
            f'<td class="bank">{r["bank"]}</td>'
            f'<td class="{alert_cls}">{r["alert"]}</td>'
            f'</tr>'
        )
    footer = '</tbody></table></div>'

    return header + "".join(body_parts) + footer


# ============ API Endpoints ============

@app.route("/")
def index():
    """统一仪表盘首页"""
    stats = load_pipeline_stats()

    indexed_dashboard = _load_dashboard_from_index()
    companies = set()
    total_alerts = indexed_dashboard["active_alerts"] if indexed_dashboard else 0
    if indexed_dashboard:
        companies.update(indexed_dashboard["companies"])
    else:
        # Fallback for an empty/missing index: legacy filesystem scan.
        from run_pipeline import normalize_company
        analyses = load_analyses()
        garbage = {"in this report", "this report", "median multiples", "note", "none"}
        for a in analyses:
            c = a.get("parsed", {}).get("company", "")
            if c and c != "None" and c.lower().strip() not in garbage:
                companies.add(normalize_company(c))
            if a.get("parsed", {}).get("alert_severity") in ("high", "medium"):
                total_alerts += 1

    # Merge config-tracked companies (includes newly added ones without reports yet)
    try:
        cfg = _read_config()
        for c in cfg.get("tracking", {}).get("companies", []):
            if c.get("active", True):
                companies.add(c["name"])
    except Exception:
        pass

    # Count logic chains from DB
    logic_count = consensus_count = 0
    from logic_store import get_conn
    try:
        conn = get_conn()
        logic_count = conn.execute("SELECT COUNT(*) FROM logic_chains").fetchone()[0]
        consensus_count = conn.execute("SELECT COUNT(*) FROM aggregated_drivers").fetchone()[0]
        conn.close()
    except Exception:
        pass

    # Today's reports (most recent pipeline run)
    today_date, today_rows = get_today_report_rows()
    try:
        today_label = f"{today_date[4:6]}/{today_date[6:8]}"
    except (IndexError, TypeError):
        today_label = datetime.now().strftime("%m/%d")

    # Upcoming earnings calendar
    import sqlite3 as _sql
    earnings_cal = []
    try:
        econn = _sql.connect(str(PROJECT_DIR / "valuation.db"))
        econn.row_factory = _sql.Row
        earnings_cal = [dict(r) for r in econn.execute(
            "SELECT * FROM earnings_calendar WHERE date >= ? ORDER BY date LIMIT 8",
            (datetime.now().strftime("%Y-%m-%d"),)
        ).fetchall()]
        econn.close()
    except Exception:
        pass
    cal_html = ""
    if earnings_cal:
        items = "".join(
            f'<span style="display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 10px;margin:3px;font-size:11px">'
            f'{e["date"][5:]}: <b>{e["company"]}</b>'
            f'{" est " + str(e.get("eps_estimate",""))[:6] if e.get("eps_estimate") else ""}'
            f'</span>'
            for e in earnings_cal
        )
        cal_html = f'<div class="card"><div class="card-title">📅 财报日历 ({len(earnings_cal)} upcoming)</div><div style="line-height:2">{items}</div></div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Hermes Research Dashboard</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}}
.header-sub{{color:#8b949e;font-size:12px;margin-bottom:24px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:0 0 16px}}
.card-title{{font-size:14px;font-weight:700;color:#58a6ff;margin:0 0 12px;border-bottom:1px solid #30363d;padding-bottom:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}}
.stat{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:16px;text-align:center}}
.stat .num{{font-size:28px;font-weight:800;line-height:1.2}}
.stat .lbl{{font-size:11px;color:#8b949e;margin-top:4px}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}}
.nav a{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 20px;color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;transition:border-color .2s}}
.nav a:hover{{border-color:#58a6ff}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}
.company-tag{{display:inline-block;background:#1f2f3d;border:1px solid #30363d;border-radius:4px;padding:4px 10px;margin:3px;font-size:12px;color:#58a6ff;text-decoration:none}}
.company-tag:hover{{border-color:#58a6ff}}
.report-table{{width:100%;border-collapse:collapse;font-size:12px}}
.report-table th{{position:sticky;top:0;background:#0d1117;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid #30363d;z-index:1}}
.report-table td{{padding:7px 10px;border-bottom:1px solid #21262d;white-space:nowrap}}
.report-table tr:hover td{{background:#1a1f2e}}
.report-table .bank{{color:#8b949e;font-size:11px}}
.report-table .alert-high{{color:#f85149;font-weight:600}}
.report-table .alert-medium{{color:#d29922;font-weight:600}}
.report-scroll{{max-height:340px;overflow-y:auto;border:1px solid #21262d;border-radius:6px}}
.report-scroll::-webkit-scrollbar{{width:6px}}
.report-scroll::-webkit-scrollbar-track{{background:#0d1117}}
.report-scroll::-webkit-scrollbar-thumb{{background:#30363d;border-radius:3px}}
</style></head>
<body><div class="wrap">
<h1><img src="/static/logo.png" style="height:36px;vertical-align:middle;margin-right:10px" alt="">Hermes Research Dashboard</h1>
<div class="header-sub">{datetime.now().strftime('%Y-%m-%d %H:%M')} · 投研自动化系统</div>

<div class="grid">
  <div class="stat"><div class="num" style="color:#3fb950">{stats['total']}</div><div class="lbl">📄 报告总数</div></div>
  <div class="stat"><div class="num" style="color:#58a6ff">{stats['analyzed']}</div><div class="lbl">🔬 已分析</div></div>
  <div class="stat"><div class="num" style="color:#d29922">{logic_count}</div><div class="lbl">🔗 逻辑链</div></div>
  <div class="stat"><div class="num" style="color:#58a6ff">{len(companies)}</div><div class="lbl">🏢 覆盖公司</div></div>
  <div class="stat"><div class="num" style="color:#f85149">{total_alerts}</div><div class="lbl">🚨 活跃 Alert</div></div>
  <div class="stat"><div class="num">{consensus_count}</div><div class="lbl">📊 聚合 Driver</div></div>
</div>

<div class="card">
  <div class="card-title">📋 导航</div>
  <div class="nav">
    <a href="/valuation">🏷️ 估值模型</a>
    <a href="/banks">🏦 IB 投行分析</a>
    <a href="/supply-chain">🏭 产业传导图</a>
    <a href="/industry">🏭 行业数据库</a>
    <a href="/industry/chain">🔗 产业链矩阵</a>
    <a href="/chokepoint" style="border-color:#f0883e;color:#f0883e">🔒 卡脖子 P0-P11</a>
    <a href="/consensus" style="border-color:#58a6ff;color:#58a6ff">📊 共识</a>
    <a href="/contrarian" style="border-color:#d29922;color:#d29922">🔍 反共识</a>
    <a href="/backtest" style="border-color:#3fb950;color:#3fb950">📈 回测</a>
    <a href="/holdings" style="border-color:#3fb950;color:#3fb950">📋 持仓</a>
    <a href="/paper" style="border-color:#d29922;color:#d29922">🎯 模拟盘</a>
    <a href="/settings">⚙️ 设置</a>
    <a href="/alerts">🚨 Alerts</a>
    <a href="/macro">📊 Macro</a>
    <a href="/investment-themes">🤖 未来投资方向</a>
  </div>
</div>

<div class="card">
  <div class="card-title">🔍 搜索报告</div>
  <div style="padding:12px 0">
    <form action="/search" method="get" style="display:flex;gap:8px">
      <input type="text" name="q" placeholder="输入关键词、公司名、行业… (例: SMIC 7nm / CoWoS / MediaTek ASIC)"
             style="flex:1;padding:10px 14px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:14px">
      <button type="submit" style="padding:10px 20px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:14px;cursor:pointer">搜索</button>
    </form>
  </div>
</div>

<div class="card">
  <div class="card-title">📰 今日研报 ({today_label}) · {len(today_rows)} 份</div>
  {_render_today_table(today_rows)}
</div>

{cal_html}

<div class="card">
  <div class="card-title">🏢 覆盖公司 ({len(companies)})</div>
  <div>{''.join(f'<a class="company-tag" href="/company/{c}">{c}</a>' for c in sorted(companies))}</div>
</div>

<div style="text-align:center;color:#8b949e;font-size:11px;margin-top:24px">
  Pipeline: Download → Analyze → Logic Extract → Group → Logic Aggregate → Consensus<br>
  <a href="/supply-chain">Supply Chain Graph</a> · <a href="/api/panel/scan">Signal Panel</a> · <a href="/api/alerts">Alerts</a>
</div>
</div></body></html>"""


@app.route("/api/search")
def api_search():
    """JSON 搜索 API"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q parameter required"}), 400
    limit = min(int(request.args.get("limit", 20)), 100)
    idx = ReportIndex()
    results = idx.search(q, limit=limit,
                         company=request.args.get("company") or None,
                         industry=request.args.get("industry") or None,
                         bank=request.args.get("bank") or None)
    idx.close()
    return jsonify(results)


@app.route("/search")
def search_page():
    """搜索页面"""
    q = request.args.get("q", "").strip()
    company = request.args.get("company", "")
    industry = request.args.get("industry", "")
    bank = request.args.get("bank", "")
    page = max(1, int(request.args.get("page", 1)))
    limit = min(int(request.args.get("limit", 20)), 100)

    if not q:
        # 搜索首页 — 空搜索框 + 热门标签
        return _render_search_form()

    offset = (page - 1) * limit
    idx = ReportIndex()
    results = idx.search(q, limit=limit, offset=offset,
                         company=company or None, industry=industry or None,
                         bank=bank or None)
    idx.close()
    return _render_search_results(q, results, page, limit, company, industry, bank)


def _render_search_form() -> str:
    """搜索首页 HTML"""
    idx = ReportIndex()
    stats = idx.get_stats()
    companies = [
        c["canonical_name"] for c in idx.list_entities("company", active_only=True)
        if c.get("report_count", 0) > 0
    ]
    idx.close()

    tag_links = " ".join(
        f'<a class="company-tag" href="/company/{c}">{c}</a>'
        for c in sorted(companies)[:30]
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>搜索报告 — Hermes</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0d1117;color:#e6edf3;line-height:1.6}}
.wrap{{max-width:800px;margin:0 auto;padding:24px 16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:16px 0}}
.card-title{{font-size:16px;font-weight:600;color:#58a6ff;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #21262d}}
.company-tag{{display:inline-block;background:#1f2f3d;border:1px solid #30363d;border-radius:4px;padding:4px 10px;margin:3px;font-size:12px;color:#58a6ff;text-decoration:none}}
.company-tag:hover{{border-color:#58a6ff}}
.stat{{text-align:center;padding:12px}}
.stat .num{{font-size:28px;font-weight:700;color:#58a6ff}}
.stat .lbl{{font-size:12px;color:#8b949e;margin-top:4px}}
.nav{{display:flex;flex-wrap:wrap;gap:8px}}
.nav a{{padding:6px 16px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#58a6ff;text-decoration:none;font-size:13px}}
.nav a:hover{{background:#30363d}}
</style>
</head>
<body>
<div class="wrap">
  <h1 style="margin:24px 0 8px">🔍 搜索报告</h1>
  <p style="color:#8b949e;font-size:13px;margin-bottom:16px">
    已索引 {stats['total_documents']} 份报告，覆盖 {stats['companies']} 家公司 / {stats['banks']} 家投行
  </p>

  <div class="card">
    <form action="/search" method="get">
      <input type="text" name="q" placeholder="输入关键词、公司名、行业…"
             style="width:100%;padding:14px 18px;background:#0d1117;border:2px solid #30363d;border-radius:8px;color:#e6edf3;font-size:16px"
             autofocus>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button type="submit" style="flex:1;padding:12px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:14px;cursor:pointer">🔍 搜索</button>
        <a href="/" style="padding:12px 20px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#8b949e;text-decoration:none;font-size:13px">← Dashboard</a>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="card-title">🏢 覆盖公司</div>
    <div>{tag_links}</div>
  </div>

  <div class="card">
    <div class="card-title">💡 搜索建议</div>
    <div class="nav">
      <a href="/search?q=SMIC">SMIC</a>
      <a href="/search?q=CoWoS">CoWoS</a>
      <a href="/search?q=HBM">HBM</a>
      <a href="/search?q=MediaTek AI ASIC">MediaTek AI ASIC</a>
      <a href="/search?q=TSMC capacity">TSMC Capacity</a>
      <a href="/search?q=NAND ASP">NAND ASP</a>
      <a href="/search?q=Samsung HBM4">Samsung HBM4</a>
      <a href="/search?q=折旧">折旧</a>
    </div>
  </div>
</div>
</body></html>"""


def _render_search_results(query: str, results: dict, page: int, limit: int,
                           company: str, industry: str, bank: str) -> str:
    """搜索结果页 HTML"""
    total = results["total"]
    items = results["results"]
    aggs = results.get("aggs", {})
    total_pages = max(1, (total + limit - 1) // limit)

    # 构建结果列表
    result_rows = []
    for item in items:
        companies_str = ", ".join(item["companies"]) or "-"
        industries_str = ", ".join(item.get("industries", [])) or "-"
        bank_name = item["bank"] or "?"
        date_str = item["report_date"] or "?"
        snippet = item.get("snippet", "")

        # Links
        links = ""
        md_path = item.get("md_path", "")
        if md_path:
            links += f'<a href="/report/view?path={md_path}" target="_blank" style="color:#58a6ff;text-decoration:none;font-size:12px;margin-right:8px">📄 分析</a>'

        result_rows.append(f"""
        <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:14px;margin:8px 0">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
            <strong style="font-size:14px;color:#e6edf3">{item['pdf_name'][:80]}</strong>
            <span style="font-size:11px;color:#8b949e">{date_str}</span>
          </div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:4px">
            🏦 {bank_name} &nbsp; 🏢 {companies_str}
          </div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:4px">
            🏷️ {industries_str}
          </div>
          {f'<div style="font-size:13px;color:#c9d1d9;margin:8px 0;line-height:1.5">{snippet}</div>' if snippet else ''}
          <div style="margin-top:6px">{links}</div>
        </div>""")

    # 过滤面板
    bank_tags = " ".join(
        f'<a href="/search?q={query}&bank={k}" style="font-size:12px;color:#58a6ff;text-decoration:none;margin:2px">🏦 {k}({v})</a>'
        for k, v in sorted(aggs.get("banks", {}).items(), key=lambda x: -x[1])[:10]
    )
    company_tags = " ".join(
        f'<a href="/search?q={query}&company={k}" style="font-size:12px;color:#58a6ff;text-decoration:none;margin:2px">🏢 {k}({v})</a>'
        for k, v in sorted(aggs.get("companies", {}).items(), key=lambda x: -x[1])[:10]
    )

    # 分页
    page_links = ""
    if total_pages > 1:
        for p in range(1, total_pages + 1):
            if p == page:
                page_links += f' <strong style="color:#58a6ff;padding:4px 8px">{p}</strong>'
            elif abs(p - page) <= 3 or p <= 2 or p >= total_pages - 1:
                url = f"/search?q={query}&page={p}"
                if company:
                    url += f"&company={company}"
                if industry:
                    url += f"&industry={industry}"
                if bank:
                    url += f"&bank={bank}"
                page_links += f' <a href="{url}" style="color:#58a6ff;text-decoration:none;padding:4px 8px">{p}</a>'
            elif abs(p - page) == 4:
                page_links += " …"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>搜索: {query} — Hermes</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0d1117;color:#e6edf3;line-height:1.6}}
.wrap{{max-width:900px;margin:0 auto;padding:24px 16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0}}
.card-title{{font-size:15px;font-weight:600;color:#58a6ff;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #21262d}}
mark{{background:#d2991d33;color:#d2991d;padding:0 2px;border-radius:2px}}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;gap:8px;align-items:center;margin:20px 0 12px">
    <a href="/" style="color:#58a6ff;text-decoration:none;font-size:14px">← Dashboard</a>
    <span style="color:#8b949e">|</span>
    <a href="/search" style="color:#58a6ff;text-decoration:none;font-size:14px">新搜索</a>
  </div>

  <form action="/search" method="get" style="margin-bottom:16px">
    <input type="text" name="q" value="{query}" placeholder="搜索…"
           style="width:100%;padding:12px 16px;background:#161b22;border:2px solid #30363d;border-radius:8px;color:#e6edf3;font-size:15px">
  </form>

  <div style="color:#8b949e;font-size:13px;margin-bottom:16px">
    🔍 "{query}" — 找到 <strong style="color:#58a6ff">{total}</strong> 个结果
    {f' (第 {page}/{total_pages} 页)' if total_pages > 1 else ''}
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap">
    <div style="flex:1;min-width:600px">
      {''.join(result_rows)}
      {f'<div style="text-align:center;margin:16px 0;font-size:14px">{page_links}</div>' if page_links else ''}
    </div>
    <div style="width:200px;flex-shrink:0">
      {f'''<div class="card"><div class="card-title">🏦 投行</div>{bank_tags}</div>''' if bank_tags else ''}
      {f'''<div class="card"><div class="card-title">🏢 公司</div>{company_tags}</div>''' if company_tags else ''}
    </div>
  </div>
</div>
</body></html>"""


@app.route("/api/overview")
def api_overview():
    analyses = load_analyses()
    stats = load_pipeline_stats()
    consensus = load_consensus()

    # Count alerts
    critical = high = medium = 0
    for a in analyses:
        parsed = a.get("parsed", {})
        tp = parsed.get("target_price") or {}
        new_tp = tp.get("new")
        old_tp = tp.get("old")
        if new_tp and old_tp and old_tp > 0:
            change = (new_tp - old_tp) / old_tp
            if abs(change) >= 0.5:
                critical += 1
            elif abs(change) >= 0.3:
                high += 1
        if parsed.get("alert_severity") == "medium":
            medium += 1

    # Active companies
    companies = set()
    for a in analyses:
        c = a.get("parsed", {}).get("company", "")
        if c and c != "None":
            companies.add(c)

    # Latest stock prices
    stocks = load_stock_prices()
    prices = []
    for s in stocks[:8]:
        if s.get("price"):
            chg = s.get("change_pct", 0) or 0
            prices.append({
                "name": s.get("name", s["_symbol"])[:20],
                "symbol": s["_symbol"],
                "price": s["price"],
                "change": round(chg, 1),
                "currency": s.get("currency", ""),
                "source": s.get("_source", "")
            })

    return jsonify({
        "stats": stats,
        "companies": len(companies),
        "alerts": {"critical": critical, "high": high, "medium": medium},
        "consensus": len(consensus),
        "prices": prices,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M")
    })


@app.route("/api/companies")
def api_companies():
    analyses = load_analyses()
    from run_pipeline import normalize_company

    by_company = defaultdict(list)
    for a in analyses:
        parsed = a.get("parsed", {})
        company = parsed.get("company", "")
        if not company or company == "None":
            continue
        canonical = normalize_company(company)
        by_company[canonical].append(a)

    result = []
    for company, items in by_company.items():
        tps = []
        ratings = []
        banks = set()
        tags = defaultdict(int)

        for item in items:
            parsed = item.get("parsed", {})
            tp = parsed.get("target_price") or {}
            if tp.get("new"):
                tps.append({"new": tp["new"], "old": tp.get("old"), "currency": tp.get("currency", "")})
            r = parsed.get("rating", "")
            if r:
                ratings.append(r)
            pdf_name = item.get("pdf_name", "")
            bank = extract_bank_from_filename(pdf_name)
            if bank != "?":
                banks.add(bank[:15])
            for layer_tags in parsed.get("industry_tags", {}).values():
                for tag in layer_tags:
                    tags[tag["slug"]] += 1

        tp_changes = []
        for t in tps:
            if t["new"] and t["old"] and t["old"] > 0:
                tp_changes.append((t["new"] - t["old"]) / t["old"])

        avg_change = sum(tp_changes) / len(tp_changes) if tp_changes else 0
        tp_min = min(t["new"] for t in tps) if tps else 0
        tp_max = max(t["new"] for t in tps) if tps else 0
        currency = tps[0]["currency"] if tps else ""

        result.append({
            "company": company,
            "reports": len(items),
            "tp_min": tp_min,
            "tp_max": tp_max,
            "avg_tp_change": round(avg_change * 100, 1),
            "currency": currency,
            "ratings": dict(sorted(defaultdict(int, [(r, ratings.count(r)) for r in set(ratings)]).items())),
            "banks": sorted(banks),
            "top_tags": [t for t, _ in sorted(tags.items(), key=lambda x: x[1], reverse=True)[:3]],
        })

    result.sort(key=lambda x: x["reports"], reverse=True)
    return jsonify(result)


@app.route("/api/industries")
def api_industries():
    analyses = load_analyses()
    metrics = load_industry_metrics()

    # Aggregate by layer
    layers = {}
    for layer_key, layer_cn in [("sector", "赛道"), ("value_chain", "价值链"), ("tech_theme", "技术主题")]:
        layers[layer_key] = {"name": layer_cn, "tags": defaultdict(lambda: {"reports": 0, "companies": set(), "tp_changes": []})}

    for a in analyses:
        parsed = a.get("parsed", {})
        company = parsed.get("company", "")
        tp = parsed.get("target_price") or {}
        new_tp, old_tp = tp.get("new"), tp.get("old")
        tp_change = (new_tp - old_tp) / old_tp if new_tp and old_tp and old_tp > 0 else 0

        tags = parsed.get("industry_tags", {})
        for layer_key in ["sector", "value_chain", "tech_theme"]:
            for tag in (tags.get(layer_key) or []):
                slug = tag["slug"]
                data = layers[layer_key]["tags"][slug]
                data["name"] = tag["name"]
                data["reports"] += 1
                if company and company != "None":
                    data["companies"].add(company)
                if tp_change:
                    data["tp_changes"].append(tp_change)

    result = []
    for layer_key, layer_data in layers.items():
        tags_list = []
        for slug, data in layer_data["tags"].items():
            avg_tp = sum(abs(c) for c in data["tp_changes"]) / len(data["tp_changes"]) if data["tp_changes"] else 0
            tags_list.append({
                "slug": slug,
                "name": data["name"],
                "reports": data["reports"],
                "companies": len(data["companies"]),
                "heat": "hot" if avg_tp >= 0.5 else ("warm" if avg_tp >= 0.3 else "cool"),
                "avg_tp_change": round(avg_tp * 100, 1),
            })
        tags_list.sort(key=lambda x: x["reports"], reverse=True)
        result.append({"layer": layer_key, "name": layer_data["name"], "tags": tags_list})

    return jsonify(result)


@app.route("/api/alerts")
def api_alerts():
    analyses = load_analyses()
    alerts = []

    for a in analyses:
        parsed = a.get("parsed", {})
        pdf_name = a.get("pdf_name", "")
        bank = extract_bank_from_filename(pdf_name)
        bank = bank[:15]
        company = parsed.get("company", "?") or "?"
        tp = parsed.get("target_price") or {}
        new_tp, old_tp = tp.get("new"), tp.get("old")

        # Extract report date from filename (-YYMMDD suffix)
        date_m = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|$)', pdf_name)
        report_date = ""
        if date_m:
            ds = date_m.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"

        if new_tp and old_tp and old_tp > 0 and company not in ("?", "None", ""):
            change = (new_tp - old_tp) / old_tp
            if abs(change) >= 0.3:
                direction = "↑" if change > 0 else "↓"
                severity = "critical" if abs(change) >= 0.5 else "high"
                alerts.append({
                    "severity": severity,
                    "type": "TP Change",
                    "title": f"{bank}: {company} TP {direction}{abs(change):.0%}",
                    "detail": f"{old_tp:,.0f} → {new_tp:,.0f} {tp.get('currency', '')}",
                    "bank": bank,
                    "company": company,
                    "date": report_date,
                    "md_path": a.get("_md_path", a.get("_path", "").replace("_analysis.json", "_analysis.md")),
                })

        sev = parsed.get("alert_severity", "")
        if sev in ("high", "medium") and company not in ("?", "None", ""):
            risk_count = len(parsed.get('risk_signals', []))
            opp_count = len(parsed.get('opportunity_signals', []))
            if risk_count == 0 and opp_count == 0:
                continue  # skip empty alerts
            alerts.append({
                "severity": sev,
                "type": "Signal",
                "title": f"{company}: {parsed.get('alert_title', company)}",
                "detail": f"Risks: {risk_count}, Opps: {opp_count}",
                "bank": bank,
                "company": company,
                "date": report_date,
                "md_path": a.get("_md_path", a.get("_path", "").replace("_analysis.json", "_analysis.md")),
            })

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    alerts.sort(key=lambda x: (x.get("date", ""), severity_order.get(x["severity"], 99)), reverse=True)

    return jsonify(alerts[:20])


@app.route("/api/news")
def api_news():
    news = load_news(limit=15)
    return jsonify(news)


# ── Holdings CRUD ────────────────────────────────────────

HOLDINGS_DB = PROJECT_DIR / "holdings.db"


def _get_holdings_db():
    conn = sqlite3.connect(str(HOLDINGS_DB))
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(holdings)").fetchall()}
    if "cost_basis" not in cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN cost_basis REAL NOT NULL DEFAULT 0.0")
        conn.execute(
            "UPDATE holdings SET cost_basis = target_price "
            "WHERE direction = 'SELL' AND cost_basis = 0 AND target_price > 0"
        )
        conn.commit()
    return conn


@app.route("/api/holdings")
def api_holdings():
    """GET /api/holdings?market=A&active=1 (or active=all for all rows)"""
    conn = _get_holdings_db()
    market = request.args.get("market", "")
    active = request.args.get("active", "1")

    where = []
    params = []
    if active != "all":
        where.append("active = ?")
        params.append(int(active))
    if market and market != "ALL":
        where.append("market = ?")
        params.append(market)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM holdings {where_clause} ORDER BY market, direction, company",
        params
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/holdings", methods=["POST"])
def api_holdings_create():
    """POST /api/holdings — create new holding"""
    data = request.get_json(force=True)
    required = ["direction", "ticker", "company", "market"]
    for f in required:
        if not data.get(f):
            return jsonify({"error": f"Missing field: {f}"}), 400

    conn = _get_holdings_db()
    conn.execute(
        """INSERT INTO holdings (direction, ticker, company, market, shares, target_price, cost_basis, entry_date, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (data["direction"], data["ticker"], data["company"], data["market"],
         data.get("shares", 0), data.get("target_price", 0), data.get("cost_basis", 0),
         data.get("entry_date", ""), data.get("notes", ""))
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"id": new_id, "status": "created"})


@app.route("/api/holdings/<int:holding_id>", methods=["PUT"])
def api_holdings_update(holding_id):
    """PUT /api/holdings/<id> — update fields"""
    data = request.get_json(force=True)
    conn = _get_holdings_db()

    allowed = ["direction", "ticker", "company", "market", "shares",
               "target_price", "cost_basis", "entry_date", "notes", "active"]
    sets = []
    params = []
    for k in allowed:
        if k in data:
            sets.append(f"{k} = ?")
            params.append(data[k])
    if not sets:
        conn.close()
        return jsonify({"error": "No valid fields"}), 400

    sets.append("updated_at = datetime('now','localtime')")
    params.append(holding_id)

    conn.execute(f"UPDATE holdings SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})


@app.route("/api/holdings/<int:holding_id>", methods=["DELETE"])
def api_holdings_delete(holding_id):
    """DELETE /api/holdings/<id> — soft-delete (set active=0)"""
    conn = _get_holdings_db()
    conn.execute(
        "UPDATE holdings SET active = 0, updated_at = datetime('now','localtime') WHERE id = ?",
        (holding_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "archived"})


@app.route("/api/holdings/<int:holding_id>/alerts")
def api_holdings_alerts(holding_id):
    """GET /api/holdings/<id>/alerts — alert history"""
    conn = _get_holdings_db()
    rows = conn.execute(
        "SELECT * FROM alert_log WHERE holding_id = ? ORDER BY created_at DESC LIMIT 20",
        (holding_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/holdings")
def holdings_page():
    """持仓管理页面"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>持仓管理 — Hermes</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,sans-serif;margin:0;padding:0}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.header-sub{{color:#8b949e;font-size:12px;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:0 0 16px}}
.card-title{{font-size:14px;font-weight:700;color:#58a6ff;margin:0 0 12px;border-bottom:1px solid #30363d;padding-bottom:8px}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}}
.nav a{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 16px;color:#58a6ff;text-decoration:none;font-size:13px}}
.nav a:hover{{border-color:#58a6ff}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#0d1117;color:#8b949e;font-weight:600;text-align:left;padding:10px 12px;border-bottom:1px solid #30363d}}
td{{padding:8px 12px;border-bottom:1px solid #21262d}}
tr:hover td{{background:#1a1f2e}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.badge-buy{{background:#1f3d2f;color:#3fb950;border:1px solid #2ea043}}
.badge-sell{{background:#3d1f1f;color:#f85149;border:1px solid #da3633}}
.badge-a{{background:#1f2f3d;color:#58a6ff;border:1px solid #30363d}}
.badge-us{{background:#2f1f3d;color:#d29922;border:1px solid #5a3d6e}}
.badge-hk{{background:#1f3d2f;color:#3fb950;border:1px solid #2ea043}}
.btn{{padding:6px 14px;border-radius:4px;border:none;cursor:pointer;font-size:12px;font-weight:600}}
.btn-add{{background:#238636;color:#fff}}
.btn-edit{{background:#1f2f3d;color:#58a6ff;border:1px solid #30363d}}
.btn-archive{{background:#3d1f1f;color:#f85149;border:1px solid #da3633}}
.btn-save{{background:#238636;color:#fff}}
.btn-cancel{{background:#30363d;color:#8b949e}}
input,select{{background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:6px 10px;color:#e6edf3;font-size:12px}}
input:focus,select:focus{{outline:none;border-color:#58a6ff}}
.modal{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center}}
.modal.active{{display:flex}}
.modal-content{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:24px;min-width:420px;max-width:500px}}
.modal-content h2{{font-size:16px;margin:0 0 16px;color:#58a6ff}}
.form-row{{display:flex;gap:12px;margin-bottom:12px;align-items:center}}
.form-row label{{width:80px;text-align:right;font-size:12px;color:#8b949e;flex-shrink:0}}
.form-row input,.form-row select{{flex:1}}
.form-actions{{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}}
.filter-bar{{display:flex;gap:8px;margin-bottom:12px}}
.filter-bar a{{padding:4px 12px;border-radius:4px;font-size:12px;text-decoration:none;color:#8b949e;background:#0d1117;border:1px solid #30363d}}
.filter-bar a.active{{color:#58a6ff;border-color:#58a6ff;background:#1f2f3d}}
.filter-bar label{{font-size:12px;color:#8b949e;cursor:pointer;display:flex;align-items:center;gap:4px}}
.archived-row td{{opacity:0.5}}
.btn-restore{{background:#1f3d2f;color:#3fb950;border:1px solid #2ea043}}
</style></head>
<body><div class="wrap">
<h1>📋 持仓管理</h1>
<div class="header-sub">维护持仓和拟买入标的 · 系统定时扫描产生操作信号</div>

<div class="nav">
  <a href="/">🏠 Dashboard</a>
  <a href="/consensus">📊 共识</a>
  <a href="/alerts">🚨 Alerts</a>
  <a href="/settings">⚙️ Settings</a>
</div>

<div class="card">
  <div class="card-title">📋 持仓列表 <button class="btn btn-add" onclick="showAddModal()" style="float:right">+ 新增</button></div>
  <div class="filter-bar" style="display:flex;align-items:center;justify-content:space-between">
    <div style="display:flex;gap:8px">
      <a href="/holdings" class="active" id="filter-all">全部</a>
      <a href="/holdings?market=A" id="filter-a">A股</a>
      <a href="/holdings?market=US" id="filter-us">美股</a>
      <a href="/holdings?market=HK" id="filter-hk">港股</a>
    </div>
    <label><input type="checkbox" id="show-archived" onchange="loadHoldings()"> 显示已归档</label>
  </div>
  <table id="holdings-table">
    <thead><tr>
      <th>方向</th><th>Ticker</th><th>公司</th><th>市场</th>
      <th>数量</th><th>目标价</th><th>成本价</th><th>日期</th><th>备注</th><th>操作</th>
    </tr></thead>
    <tbody id="holdings-body"><tr><td colspan="10" style="text-align:center;color:#8b949e">加载中…</td></tr></tbody>
  </table>
</div>

<div style="text-align:center;color:#8b949e;font-size:11px;margin-top:16px">
  持仓数据存储在 holdings.db · 定时预警由 realtime_alert.py 驱动
</div>
</div>

<!-- Add/Edit Modal -->
<div class="modal" id="edit-modal">
  <div class="modal-content">
    <h2 id="modal-title">新增标的</h2>
    <input type="hidden" id="edit-id">
    <div class="form-row"><label>方向</label><select id="edit-direction"><option value="BUY">BUY (拟买入)</option><option value="SELL">SELL (持仓)</option></select></div>
    <div class="form-row"><label>Ticker</label><input id="edit-ticker" placeholder="e.g. 688981.SH"></div>
    <div class="form-row"><label>公司</label><input id="edit-company" placeholder="e.g. 中芯国际"></div>
    <div class="form-row"><label>市场</label><select id="edit-market"><option value="A">A股</option><option value="US">美股</option><option value="HK">港股</option></select></div>
    <div class="form-row"><label>数量</label><input id="edit-shares" type="number" placeholder="股数"></div>
    <div class="form-row"><label>目标价</label><input id="edit-price" type="number" step="0.01" placeholder="BUY 监控目标价"></div>
    <div class="form-row"><label>成本价</label><input id="edit-cost" type="number" step="0.01" placeholder="SELL 持仓成本价"></div>
    <div class="form-row"><label>日期</label><input id="edit-date" type="date"></div>
    <div class="form-row"><label>备注</label><input id="edit-notes" placeholder="可选"></div>
    <div class="form-actions">
      <button class="btn btn-cancel" onclick="closeModal()">取消</button>
      <button class="btn btn-save" onclick="saveHolding()">保存</button>
    </div>
  </div>
</div>

<script>
const MARKET = new URLSearchParams(location.search).get('market') || 'ALL';
document.querySelectorAll('.filter-bar a').forEach(a => {{
  if (a.href.includes('market=' + MARKET) || (MARKET === 'ALL' && a.id === 'filter-all')) a.classList.add('active');
}});

let ALL_HOLDINGS = [];

function loadHoldings() {{
  const showArchived = document.getElementById('show-archived').checked;
  let url = '/api/holdings?active=all';
  if (MARKET !== 'ALL') url += '&market=' + MARKET;
  fetch(url)
    .then(r => r.json())
    .then(data => {{
      ALL_HOLDINGS = data;
      const filtered = showArchived ? data : data.filter(h => h.active === 1);
      renderTable(filtered);
    }});
}}

function renderTable(data) {{
  if (!data.length) {{
    document.getElementById('holdings-body').innerHTML = '<tr><td colspan="10" style="text-align:center;color:#8b949e">暂无记录，点击「+ 新增」添加</td></tr>';
    return;
  }}
  document.getElementById('holdings-body').innerHTML = data.map(h => {{
    const dirBadge = h.direction === 'BUY'
      ? '<span class="badge badge-buy">BUY</span>'
      : '<span class="badge badge-sell">SELL</span>';
    const mktBadge = {{A:'badge-a',US:'badge-us',HK:'badge-hk'}}[h.market] || 'badge-a';
    const mktLabel = {{A:'A股',US:'美股',HK:'港股'}}[h.market] || h.market;
    const rowClass = h.active ? '' : 'archived-row';
    const actions = h.active
      ? `<button class="btn btn-edit" onclick="showEditModal(${{h.id}})">编辑</button>
         <button class="btn btn-archive" onclick="archiveHolding(${{h.id}})">归档</button>`
      : `<button class="btn btn-restore" onclick="restoreHolding(${{h.id}})">恢复</button>`;
    return `<tr class="${{rowClass}}">
      <td>${{dirBadge}}</td>
      <td><code>${{h.ticker}}</code></td>
      <td><b>${{h.company}}</b></td>
      <td><span class="badge ${{mktBadge}}">${{mktLabel}}</span></td>
      <td>${{h.shares.toLocaleString()}}</td>
      <td>${{h.target_price.toFixed(2)}}</td>
      <td>${{(h.cost_basis || 0).toFixed(2)}}</td>
      <td>${{h.entry_date || ''}}</td>
      <td style="color:#8b949e;font-size:12px">${{h.notes || ''}}</td>
      <td>${{actions}}</td>
    </tr>`;
  }}).join('');
}}

function showAddModal() {{
  document.getElementById('modal-title').textContent = '新增标的';
  document.getElementById('edit-id').value = '';
  ['direction','ticker','company','shares','price','cost','date','notes'].forEach(f => {{
    const el = document.getElementById('edit-' + (f === 'price' ? 'price' : f));
    if (el) el.value = '';
  }});
  document.getElementById('edit-direction').value = 'BUY';
  document.getElementById('edit-market').value = 'A';
  document.getElementById('edit-modal').classList.add('active');
}}

function showEditModal(id) {{
  const h = ALL_HOLDINGS.find(r => r.id === id);
  if (!h) return;
  document.getElementById('modal-title').textContent = '编辑标的';
  document.getElementById('edit-id').value = h.id;
  document.getElementById('edit-direction').value = h.direction;
  document.getElementById('edit-ticker').value = h.ticker;
  document.getElementById('edit-company').value = h.company;
  document.getElementById('edit-market').value = h.market;
  document.getElementById('edit-shares').value = h.shares;
  document.getElementById('edit-price').value = h.target_price;
  document.getElementById('edit-cost').value = h.cost_basis || 0;
  document.getElementById('edit-date').value = h.entry_date || '';
  document.getElementById('edit-notes').value = h.notes || '';
  document.getElementById('edit-modal').classList.add('active');
}}

function closeModal() {{
  document.getElementById('edit-modal').classList.remove('active');
}}

function saveHolding() {{
  const id = document.getElementById('edit-id').value;
  const body = {{
    direction: document.getElementById('edit-direction').value,
    ticker: document.getElementById('edit-ticker').value.trim(),
    company: document.getElementById('edit-company').value.trim(),
    market: document.getElementById('edit-market').value,
    shares: parseInt(document.getElementById('edit-shares').value) || 0,
    target_price: parseFloat(document.getElementById('edit-price').value) || 0,
    cost_basis: parseFloat(document.getElementById('edit-cost').value) || 0,
    entry_date: document.getElementById('edit-date').value,
    notes: document.getElementById('edit-notes').value.trim(),
  }};
  if (!body.ticker || !body.company) {{ alert('Ticker 和 公司名不能为空'); return; }}

  const method = id ? 'PUT' : 'POST';
  const url = id ? '/api/holdings/' + id : '/api/holdings';
  fetch(url, {{method, headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}})
    .then(r => r.json())
    .then(d => {{
      if (d.error) alert(d.error); else {{ closeModal(); loadHoldings(); }}
    }});
}}

function archiveHolding(id) {{
  if (!confirm('归档后默认隐藏，可勾选「显示已归档」找回。\\\\n\\\\n确定归档？')) return;
  fetch('/api/holdings/' + id, {{method:'DELETE'}})
    .then(r => r.json())
    .then(() => loadHoldings());
}}

function restoreHolding(id) {{
  fetch('/api/holdings/' + id, {{method:'PUT', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{active:1}})}})
    .then(r => r.json())
    .then(() => loadHoldings());
}}

loadHoldings();
</script>
</body></html>"""


@app.route("/api/reports/<company>")
def api_company_reports(company):
    """获取某公司所有报告的摘要列表"""
    analyses = load_analyses()
    from run_pipeline import normalize_company

    results = []
    for a in analyses:
        parsed = a.get("parsed", {})
        co = parsed.get("company", "") or ""
        if normalize_company(co) != normalize_company(company):
            continue

        pdf_name = a.get("pdf_name", "")
        bank = extract_bank_from_filename(pdf_name)
        bank = bank[:20]

        # Extract date from filename (suffix -YYMMDD)
        date_m = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|$)', pdf_name)
        report_date = ""
        if date_m:
            ds = date_m.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"

        tp = parsed.get("target_price") or {}
        results.append({
            "pdf_name": pdf_name,
            "bank": bank,
            "company": co,
            "ticker": parsed.get("ticker", ""),
            "rating": parsed.get("rating", ""),
            "tp_new": tp.get("new"),
            "tp_old": tp.get("old"),
            "tp_currency": tp.get("currency", ""),
            "report_date": report_date,
            "risk_count": len(parsed.get("risk_signals", [])),
            "opp_count": len(parsed.get("opportunity_signals", [])),
            "industry_tags": [t["slug"] for layer in parsed.get("industry_tags", {}).values() for t in layer],
            "md_path": a.get("_md_path", a.get("_path", "").replace("_analysis.json", "_analysis.md")),
            "json_path": a.get("_path", ""),
        })

    results.sort(key=lambda x: x["report_date"], reverse=True)
    return jsonify(results)


@app.route("/api/report/detail")
def api_report_detail():
    """获取单份报告的完整 markdown 内容 (JSON)"""
    md_path = request.args.get("path", "")
    if not md_path or not Path(md_path).exists():
        return jsonify({"error": "Report not found", "path": md_path}), 404

    text = Path(md_path).read_text(encoding="utf-8")
    return jsonify({"path": md_path, "markdown": text})


@app.route("/report/view")
def view_report_html():
    """HTML 渲染的报告详情页"""
    md_rel = request.args.get("path", "")
    md_path = REPORT_BASE / md_rel if md_rel else None
    if not md_path or not md_path.exists():
        return "<h1>Report not found</h1>", 404

    text = md_path.read_text(encoding="utf-8")

    # Simple markdown → HTML
    html = text
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'^\|(.+)\|$', lambda m: '<tr>' + ''.join(
        f'<td>{c.strip()}</td>' for c in m.group(1).split('|') if c.strip()
    ) + '</tr>', html, flags=re.MULTILINE)
    html = re.sub(r'(<tr>.*?</tr>\n?)+', r'<table>\g<0></table>', html)
    html = re.sub(r'<tr><td>-+</td>(<td>-+</td>)*</tr>', '', html)
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)
    html = "\n".join(
        f"<p>{p.replace(chr(10), '<br>')}</p>" if not p.startswith('<') else p
        for p in html.split('\n\n') if p.strip()
    )

    report_name = md_path.stem.replace("_analysis", "")[:60]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline'; img-src 'self' data:;">
<title>{report_name}</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:900px;margin:0 auto;padding:24px}}
h1{{font-size:18px;color:#58a6ff;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
h2{{font-size:14px;color:#e6edf3;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}}
h3{{font-size:13px;color:#58a6ff;margin:16px 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}}
td{{padding:6px 10px;border:1px solid #30363d}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
blockquote{{border-left:3px solid #58a6ff;margin:12px 0;padding:8px 16px;background:rgba(88,166,255,0.05);border-radius:0 6px 6px 0}}
li{{margin:4px 0}}
p{{margin:8px 0;line-height:1.6}}
a{{color:#58a6ff}}
</style></head>
<body><div class="wrap">
<h1>{report_name}</h1>
<div class="date"><a href="javascript:history.back()">← Back</a></div>
{html}
</div></body></html>"""


# ============ Ad-hoc Report Generation ============

import time as _time
_report_cooldown = 0.0
_REPORT_MIN_INTERVAL = 30  # seconds

@app.route("/api/generate-report", methods=["POST"])
def api_generate_report():
    """根据自由主题生成投行格式报告"""
    global _report_cooldown
    now = _time.time()
    if now - _report_cooldown < _REPORT_MIN_INTERVAL:
        remaining = int(_REPORT_MIN_INTERVAL - (now - _report_cooldown))
        return jsonify({"error": f"Rate limited. Try again in {remaining}s"}), 429

    data = request.get_json()
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "No topic provided"}), 400

    _report_cooldown = now

    # 收集所有分析 markdown
    # 拆解 topic 为关键词（支持中英文）
    import re as _re
    raw_words = _re.findall(r'[一-鿿]+|[a-zA-Z0-9]+', topic.lower())
    keywords = [w for w in raw_words if len(w) > 1]

    # 先用行业标签预筛选（匹配 config 中定义的 industry）
    industry_keywords = {}
    if CONFIG_PATH.exists():
        cfg = _read_config()
        for ind in cfg.get("tracking", {}).get("industries", []):
            industry_keywords[ind["slug"]] = ind.get("keywords", [])

    all_excerpts = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.md")):
        text = f.read_text(encoding="utf-8")
        text_lower = text.lower()

        # Score: any keyword OR industry tag match
        kw_hits = sum(1 for kw in keywords if kw in text_lower)
        ind_hits = 0
        for slug, ikws in industry_keywords.items():
            if any(ikw.lower() in text_lower for ikw in ikws):
                ind_hits += 1

        # Need at least 1 keyword hit OR 2 industry hits
        if kw_hits >= 1 or ind_hits >= 2:
            excerpt = text[:2000]
            # 找最佳匹配段落
            best_kw = None
            for kw in keywords:
                idx = text_lower.find(kw)
                if idx > 0:
                    best_kw = kw
                    break
            if best_kw:
                idx = text_lower.find(best_kw)
                start = max(0, idx - 300)
                end = min(len(text), idx + 1000)
                excerpt = text[start:end]
            all_excerpts.append({
                "source": f.stem[:50],
                "text": excerpt
            })

    if len(all_excerpts) < 2:
        return jsonify({"error": f"Found only {len(all_excerpts)} relevant excerpts. Need ≥2.", "excerpts": len(all_excerpts)}), 404

    # Limit to top 8 most relevant
    all_excerpts = all_excerpts[:8]

    # Build LLM prompt
    report_texts = "\n\n---\n\n".join(
        f"### Source: {e['source']}\n{e['text']}" for e in all_excerpts
    )

    today_str = datetime.now().strftime("%Y-%m-%d")
    source_n = len(all_excerpts)
    system_prompt = f"""你是一位顶级投行的资深半导体行业分析师。请就以下主题撰写专业研究报告：

"{topic}"

核心规则：
- "文不如表，表不如图" — 每个数据点必须在表格行中，不得用段落叙述
- 每个数字必须标注来源投行+日期，如："(GS 05-01)"、"(BofA 04-29)"
- 用 🟢🔴🟡 表示方向性指标
- 共识(✅)与争议(⚠️)分开
- 包含看多vs看空对比

输出以下结构：

# {topic}
> {today_str} | {source_n} 份来源 | **Confidential**

## 一、核心数据
| 指标 | 数据 | QoQ/YoY | 来源 | 日期 |
|------|------|---------|------|------|
[用来源中的具体数据填充]

## 二、对比分析
| 维度 | 来源A | 来源B | 差异 |
|------|-------|-------|------|
[不同投行预测的直接对比]

## 三、共识观点 ✅
1. **[主题一]** — [详细说明] (来源: BankA, BankB)
2. **[主题二]** — [详细说明] (来源: ...)

## 四、争议观点 ⚠️
**争议: [主题]**
- **多头**: [投行名] — [核心论点]
- **空头**: [投行名] — [核心论点]

## 五、看多 vs 看空
| 🐂 看多 | 🐻 看空 |
|--------|--------|
| [上行情景] | [下行情景] |
| [支持投行] | [支持投行] |

## 六、风险矩阵
| 风险 | 概率 | 影响 | 来源 |
|------|------|------|------|
[概率+影响评估]

## 七、数据来源索引
| 数据 | 来源 | 日期 |
|------|------|------|
[每个引用来源]

请用具体数字。每个表格单元格必须有值。数据不确定时标注置信度。"""

    try:
        from llm_client import call_llm

        markdown, _ = call_llm(system_prompt, report_texts, max_tokens=4096)

        return jsonify({
            "topic": topic,
            "sources": len(all_excerpts),
            "markdown": markdown
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ Save Report as HTML ============

@app.route("/api/save-report", methods=["POST"])
def api_save_report():
    """将生成的报告保存为 HTML 文件"""
    data = request.get_json()
    topic = data.get("topic", "Report")
    markdown = data.get("markdown", "")
    theme = data.get("theme", "dark")

    if not markdown:
        return jsonify({"error": "No markdown provided"}), 400

    from report_renderer import render_industry_report, OUTPUT_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = render_industry_report(topic, markdown, sources=1, theme=theme)

    slug = topic.lower().replace(" ", "_")[:50]
    out_path = OUTPUT_DIR / f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    out_path.write_text(html, encoding="utf-8")

    return jsonify({"status": "ok", "path": str(out_path)})


# ============ Industry Database API ============

def _find_charts_for_topic(topic_keywords: list[str], limit: int = 6) -> list[dict]:
    """查找与主题相关的源报告列表"""
    sources = []
    seen_reports = set()
    for chart_dir in sorted(REPORT_BASE.rglob("*_charts")):
        pngs = sorted(chart_dir.glob("page_*.png"))
        report_stem = chart_dir.stem[:-7] if chart_dir.stem.endswith("_charts") else chart_dir.stem
        report_name = report_stem.lower()
        md_path = chart_dir.parent / f"{report_stem}_analysis.md"
        md_text = ""
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8").lower()[:5000]

        score = sum(1 for kw in topic_keywords if kw in report_name or kw in md_text)
        if score >= 1:
            if report_stem not in seen_reports:
                seen_reports.add(report_stem)
                json_path = chart_dir.parent / f"{report_stem}_analysis.json"
                bank = company = ""
                if json_path.exists():
                    try:
                        jd = json.loads(json_path.read_text(encoding="utf-8"))
                        p = jd.get("parsed", {})
                        company = p.get("company", "") or ""
                    except Exception:
                        pass
                bank = extract_bank_from_filename(report_stem)[:25]
                # Find original PDF
                pdf_rel = ""
                for ext in [".pdf", ".pptx", ".xlsx"]:
                    pdf_path = chart_dir.parent / f"{report_stem}{ext}"
                    if pdf_path.exists():
                        pdf_rel = str(pdf_path.relative_to(REPORT_BASE))
                        break
                sources.append({
                    "name": report_stem[:70],
                    "bank": bank,
                    "company": company,
                    "md_rel": str(md_path.relative_to(REPORT_BASE)) if md_path.exists() else "",
                    "pdf_rel": pdf_rel,
                    "chart_count": len(pngs)
                })
        if len(sources) >= limit:
            break
    return sources[:15]


def _render_source_reports(sources: list[dict]) -> str:
    """渲染源报告列表（带 View + PDF 链接）"""
    if not sources:
        return ""
    rows = ""
    for s in sources[:10]:
        bank = s.get("bank", "?")
        co = s.get("company", "")
        co_str = f" — {co}" if co and co != "None" else ""
        from urllib.parse import quote as urlquote
        view_link = f"/report/view?path={urlquote(s.get('md_rel', ''))}" if s.get("md_rel") else ""
        pdf_link = f"/pdf/{urlquote(s.get('pdf_rel', ''))}" if s.get("pdf_rel") else ""
        links = ""
        if view_link:
            links += f'<a href="{view_link}" target="_blank" style="color:#58a6ff">📄 Analysis</a>'
        if pdf_link:
            links += f' &nbsp; <a href="{pdf_link}" target="_blank" style="color:#3fb950">📑 Original PDF</a>'
        rows += f"""<tr>
  <td>{bank}</td>
  <td>{s['name'][:50]}{co_str}</td>
  <td>{links if links else '—'}</td>
</tr>"""
    return f"""<h2>源报告</h2>
<table><tr><th>Bank</th><th>Report</th><th>Links</th></tr>
{rows}</table>"""


def _latest_industry_report(slug: str) -> str:
    """Find latest INDUSTRY_*.md report for a given slug, return path or ''."""
    candidates = sorted(REPORT_BASE.rglob(f"INDUSTRY_{slug}_*.md"),
                        key=lambda x: x.stat().st_mtime, reverse=True)
    return str(candidates[0].relative_to(REPORT_BASE)) if candidates else ""

def _industry_report_link(slug: str, label: str) -> str:
    """Generate HTML link to latest industry analysis report."""
    path = _latest_industry_report(slug)
    if not path:
        return ""
    from datetime import datetime
    mtime = datetime.fromtimestamp((REPORT_BASE / path).stat().st_mtime)
    return f"""<div style="background:#161b22;border:1px solid #3fb950;border-radius:8px;padding:14px 18px;margin:16px 0">
<b style="color:#3fb950">📄 最新{label}行业分析报告</b><br>
<span style="font-size:12px;color:#8b949e">生成时间: {mtime.strftime('%Y-%m-%d %H:%M')} · LLM综合{slug.upper()}行业研报</span><br>
<a href="/industry/report/{slug}" style="font-size:13px;font-weight:600">查看完整报告 →</a>
</div>"""

def _render_industry_html(title: str, tables_html: str, note: str = "",
                          chart_keywords: list[str] = None,
                          industry_slug: str = "") -> str:
    """渲染行业数据库为 HTML 页面"""
    source_section = ""
    if chart_keywords:
        sources = _find_charts_for_topic(chart_keywords)
        source_section = _render_source_reports(sources)

    # Render related companies from Matrix
    co_section = ""
    if industry_slug:
        matrix = _load_matrix()
        ind_info = matrix.get("industries", {}).get(industry_slug, {})
        co_names = ind_info.get("companies", [])
        if co_names:
            co_links = " · ".join(
                f'<a href="/company/{co}" style="font-size:13px">{co}</a>'
                for co in sorted(co_names)
            )
            co_section = f'<h2>🏢 覆盖公司 ({len(co_names)})</h2><div style="line-height:2.2;margin-bottom:20px">{co_links}</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:20px;color:#e6edf3;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:20px}}
h2{{font-size:14px;color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 20px}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px;text-transform:uppercase}}
td{{padding:8px 10px;border:1px solid #30363d;vertical-align:top}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
.up{{color:#3fb950;font-weight:600}}
.down{{color:#f85149;font-weight:600}}
.warn{{color:#d29922}}
.tag{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin:0 2px}}
.tag-hot{{background:#3d1f1f;color:#f85149}}
.tag-warn{{background:#3d2f0f;color:#d29922}}
.tag-ok{{background:#1f3d2f;color:#3fb950}}
.note{{font-size:11px;color:#8b949e;margin:4px 0 16px}}
.footer{{margin-top:24px;padding-top:12px;border-top:1px solid #30363d;color:#8b949e;font-size:11px;text-align:center}}
a{{color:#58a6ff}}
</style></head>
<body><div class="wrap">
<h1>{title}</h1>
<div class="date">Hermes Industry Database · {datetime.now().strftime('%Y-%m-%d')}</div>
{co_section}
{tables_html}
{source_section}
{note}
<div class="footer">Data: Morgan Stanley, Bernstein, BofA, JPM, CLSA, TrendForce · Manual update · <a href="/">← Dashboard</a> · <a href="/industry/chain" style="display:inline;padding:0;background:none;border:none;margin-left:12px">🔗 产业链矩阵</a></div>
</div></body></html>"""


@app.route("/api/industry/<table>")
def api_industry_table(table):
    """获取行业数据库 JSON"""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if table == "hbm":
        vendors = conn.execute("SELECT * FROM hbm_capacity ORDER BY year, vendor").fetchall()
        sd = conn.execute("SELECT * FROM hbm_supply_demand ORDER BY year").fetchall()
        conn.close()
        return jsonify({"capacity": [dict(r) for r in vendors], "supply_demand": [dict(r) for r in sd]})
    elif table == "cowos":
        rows = conn.execute("SELECT * FROM cowos_capacity ORDER BY period").fetchall()
        conn.close()
        return jsonify({"capacity": [dict(r) for r in rows]})
    elif table == "memory":
        rows = conn.execute("SELECT * FROM memory_pricing ORDER BY product, period").fetchall()
        conn.close()
        return jsonify({"pricing": [dict(r) for r in rows]})
    conn.close()
    return jsonify({"error": "Unknown table"}), 404


@app.route("/industry/report/<slug>")
def view_industry_report(slug):
    """动态行业报告页 — 自动加载最新 INDUSTRY_<slug>_*.md"""
    import glob
    pattern = str(REPORT_BASE / f"INDUSTRY_{slug}_*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return f"<h1>No report found for '{slug}'</h1><p>Run pipeline first to generate industry reports.</p>", 404

    md_path = Path(files[0])
    text = md_path.read_text(encoding="utf-8")

    # Simple markdown → HTML
    html = text
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'^\|(.+)\|$', lambda m: '<tr>' + ''.join(
        f'<td>{c.strip()}</td>' for c in m.group(1).split('|') if c.strip()
    ) + '</tr>', html, flags=re.MULTILINE)
    html = re.sub(r'(<tr>.*?</tr>\n?)+', r'<table>\g<0></table>', html)
    html = re.sub(r'<tr><td>-+</td>(<td>-+</td>)*</tr>', '', html)
    html = "\n".join(
        f"<p>{p.replace(chr(10), '<br>')}</p>" if not p.startswith('<') else p
        for p in html.split('\n\n') if p.strip()
    )

    from datetime import datetime
    mtime = datetime.fromtimestamp(md_path.stat().st_mtime)
    slug_display = slug.upper().replace("-", " ")

    # Map slug to display name
    label_map = {
        "memory": "HBM / Memory", "foundry": "Foundry / 代工", "cowos": "CoWoS / 先进封装",
        "ai-chip": "AI Chip", "ai-infra": "AI Infra Supply Chain",
        "interconnect": "Interconnect / Optical", "power": "Power / Energy",
        "compute": "Computing / Datacenter", "llm": "LLM / 大模型",
    }
    label = label_map.get(slug, slug_display)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{label} — Industry Report</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 4px;color:#58a6ff}}
.date{{color:#8b949e;font-size:12px;margin-bottom:20px}}
h2{{font-size:16px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:28px 0 14px;color:#e6edf3}}
h3{{font-size:14px;color:#58a6ff;margin:20px 0 8px}}
p{{line-height:1.7;margin:8px 0;font-size:14px}}
li{{margin:4px 0;font-size:13px;line-height:1.6}}
a{{color:#58a6ff}}
blockquote{{border-left:3px solid #3fb950;padding:8px 16px;margin:12px 0;background:#161b22;border-radius:0 6px 6px 0;font-size:13px;color:#8b949e}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px;text-transform:uppercase}}
td{{padding:8px 10px;border:1px solid #30363d;vertical-align:top;font-size:13px}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
strong{{color:#e6edf3}}
</style></head>
<body><div class="wrap">
<h1>🏭 {label} — 行业分析报告</h1>
<div class="date">生成时间: {mtime.strftime('%Y-%m-%d %H:%M')} · 来源: LLM综合{slug.upper()}卖方研报 · <a href="/industry">← 行业首页</a> · <a href="/">← Dashboard</a>
  <button onclick="pushIndustry('{slug}')"
          style="margin-left:12px;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 12px;color:#58a6ff;cursor:pointer;font-size:12px">📤 Push</button>
  <span id="push-status" style="font-size:11px;color:#8b949e"></span>
</div>
{html}
<script>
const INDUSTRY_OPTIONS = ["LLM / \u5927\u6a21\u578b", "CoWoS / Advanced Packaging", "HBM / Memory", "AI Chip (GPU/TPU/ASIC)", "Foundry / Capacity", "Computing Power / Datacenter", "Power / Energy", "Interconnect / Optical", "AI Infra Supply Chain", "MLCC", "ABF Substrate / IC\u8f7d\u677f", "PCB / CCL (\u5370\u5236\u7535\u8def\u677f&\u8986\u94dc\u677f)"];
function pushIndustry(slug) {{
  var btn = event.target;
  btn.disabled = true;
  btn.textContent = '...';
  fetch('/api/push/industry/' + slug, {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'ok') {{ document.getElementById('push-status').textContent = '✅'; }}
      else {{ document.getElementById('push-status').textContent = '⚠️ ' + (d.reason||''); }}
    }})
    .catch(() => {{ document.getElementById('push-status').textContent = '❌'; }})
    .finally(() => {{ btn.disabled = false; btn.textContent = '📤 Push'; }});
}}
</script>
</div></body></html>"""


@app.route("/industry/ai-datacenter")
def view_ai_datacenter():
    """AI数据中心互连产业链动态 — from Bernstein AI DC Connectivity report"""
    md_path = REPORT_BASE / "20260510/Bernstein-ARTIFICIAL INTELLIGENCE INSIDE THE WAR FOR AI DATA CENTER CONNECTIVITY-260508_analysis.md"
    if not md_path.exists():
        return "<h1>Report pending — run pipeline first</h1>", 404

    md_text = md_path.read_text(encoding="utf-8")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI DC Connectivity</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
h2{{font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px;color:#e6edf3}}
h3{{font-size:13px;color:#58a6ff;margin:16px 0 6px}}
a{{color:#58a6ff}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px}}
td{{padding:8px 10px;border:1px solid #30363d}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
.up{{color:#3fb950;font-weight:600}}
.tag{{display:inline-block;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;color:#58a6ff;margin:2px}}
.note{{font-size:11px;color:#8b949e;margin:4px 0 16px}}
.src{{font-size:10px;color:#8b949e}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin:16px 0}}
</style></head>
<body><div class="wrap">
<h1>🏭 AI数据中心互连 — 产业链动态</h1>
<div class="date">2026-05-08 · Source: Bernstein · <a href="/industry">← Industry</a> · <a href="/">← Dashboard</a>
<button onclick="pushIndustry('ai-datacenter')" style="margin-left:12px;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 12px;color:#58a6ff;cursor:pointer;font-size:12px">📤 Push</button>
<span id="push-status" style="font-size:11px;color:#8b949e"></span>
</div>
<script>
const INDUSTRY_OPTIONS = ["LLM / \u5927\u6a21\u578b", "CoWoS / Advanced Packaging", "HBM / Memory", "AI Chip (GPU/TPU/ASIC)", "Foundry / Capacity", "Computing Power / Datacenter", "Power / Energy", "Interconnect / Optical", "AI Infra Supply Chain", "MLCC", "ABF Substrate / IC\u8f7d\u677f", "PCB / CCL (\u5370\u5236\u7535\u8def\u677f&\u8986\u94dc\u677f)"];
function pushIndustry(slug) {{
  var btn = event.target;
  btn.disabled = true;
  btn.textContent = '...';
  fetch('/api/push/industry/' + slug, {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'ok') {{ document.getElementById('push-status').textContent = '✅'; }}
      else {{ document.getElementById('push-status').textContent = '⚠️ ' + (d.reason||''); }}
    }})
    .catch(() => {{ document.getElementById('push-status').textContent = '❌'; }})
    .finally(() => {{ btn.disabled = false; btn.textContent = '📤 Push'; }});
}}
</script>

<div class="card">
<h2>核心判断</h2>
<p>AI基础设施正从<b style="color:#f85149">"计算限制"转向"连接限制"</b>，互连技术（电气+光学）的战略重要性急剧提升。</p>
</div>

<h2>📊 市场规模</h2>
<div class="table-wrap"><table>
<tr><th>指标</th><th>2025A</th><th>2026E</th><th>2028E</th><th>备注</th></tr>
<tr><td>光模块及相关产品销售</td><td>>$230亿 (+50% YoY)</td><td>—</td><td>—</td><td>AI驱动量价齐升</td></tr>
<tr><td>以太网光模块市场</td><td>~$170亿 (+60% YoY)</td><td>59% CAGR (24-26)</td><td>15% CAGR (26-30)</td><td>短期AI scale-out驱动，长期放缓</td></tr>
<tr><td>CPO销售额</td><td>—</td><td>小规模部署(H2)</td><td>大规模出货</td><td>LightCounting预测</td></tr>
</table></div>

<h2>🔗 技术路线</h2>
<div class="grid2">
<div class="card">
<h3>CPO (共封装光学)</h3>
<p>2026H2起从scale-out网络开始小规模部署<br>
台积电COUPE平台预计成为主流<br>
NVIDIA/Broadcom领跑<br>
CPO交换机成本比传统方案高≥10%</p>
<p class="note">优势: 能效+信号完整性 | 挑战: 制造/成本/可维护性</p>
</div>
<div class="card">
<h3>铜缆连接</h3>
<p>未来3年仍是scale-up主流<br>
CPC技术扩展铜缆生命周期至448 Gbps<br>
立讯精密受益 (Outperform)</p>
</div>
<div class="card">
<h3>硅光子 (SiPho)</h3>
<p>Tower Semi: 2025年收入$228M → 扩产5x, 70%+产能已预订至2028<br>
GF: 2026年AMF收入>$75M, 2030年SiPho目标>$1B</p>
</div>
<div class="card">
<h3>PCB/基板</h3>
<p>高速高密度→多层PCB/HDI/ABF基板结构性增长<br>
2026年后竞争加剧</p>
</div>
</div>

<h2>🏢 关键公司动态</h2>
<table>
<tr><th>公司</th><th>角色</th><th>关键数据</th></tr>
<tr><td><b>Lumentum</b> <span class="tag">LITE</span></td><td>激光器供应商</td><td>CPO订单数亿美元(2027发货) · 扩产>40%</td></tr>
<tr><td><b>Coherent</b> <span class="tag">COHR</span></td><td>激光器供应商</td><td>InP产能3x (FY24-25) · 4"→6"晶圆: 芯片产出3x, 成本-50%</td></tr>
<tr><td><b>Tower Semi</b> <span class="tag">TSEM</span></td><td>硅光子代工</td><td>SiPho收入$228M(2025) · 扩产5x · 70%+产能预订至2028</td></tr>
<tr><td><b>GlobalFoundries</b> <span class="tag">GFS</span></td><td>硅光子代工</td><td>AMF收入>$75M(2026) · 2030年SiPho目标>$1B</td></tr>
<tr><td><b>TSMC</b> <span class="tag">2330</span></td><td>CPO平台</td><td>COUPE平台预计成主流CPO方案</td></tr>
<tr><td><b>Broadcom</b> <span class="tag">AVGO</span></td><td>CPO/NPO领跑者</td><td>与NVIDIA并列CPO技术前沿</td></tr>
<tr><td><b>Luxshare</b></td><td>铜缆连接</td><td>CPC技术受益 · 评级Outperform</td></tr>
</table>

<p class="note">📌 Source: Bernstein — ARTIFICIAL INTELLIGENCE: INSIDE THE WAR FOR AI DATA CENTER CONNECTIVITY (May 8, 2026)</p>

<h2>📋 源报告</h2>
<div class="card">
<div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
<div style="flex:1;min-width:200px">
<b style="color:#58a6ff;font-size:14px">Bernstein</b><br>
<span style="color:#8b949e;font-size:12px">May 8, 2026</span>
<p style="font-size:13px;margin:8px 0;line-height:1.6">
AI基础设施正从计算限制转向<b>连接限制</b>。CPO预计2026H2开始小规模部署，铜缆未来三年仍是scale-up主流。
台积电COUPE平台、Broadcom/NVIDIA领跑CPO；Lumentum/Coherent扩产激光器；Tower/GF扩产硅光子代工。
光模块市场2025年>$230亿(+50% YoY)。高速PCB/HDI/ABF基板结构性增长。
</p>
</div>
<div style="display:flex;flex-direction:column;gap:8px">
<a href="/pdf/20260510/Bernstein-ARTIFICIAL%20INTELLIGENCE%20INSIDE%20THE%20WAR%20FOR%20AI%20DATA%20CENTER%20CONNECTIVITY-260508.pdf" target="_blank" style="display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:6px 14px;color:#58a6ff;text-decoration:none;font-size:12px">📑 原始PDF</a>
<a href="/report/view?path=20260510/Bernstein-ARTIFICIAL%20INTELLIGENCE%20INSIDE%20THE%20WAR%20FOR%20AI%20DATA%20CENTER%20CONNECTIVITY-260508_analysis.md" target="_blank" style="display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:6px 14px;color:#58a6ff;text-decoration:none;font-size:12px">📄 完整分析</a>
</div>
</div>
</div>
</div></body></html>"""


@app.route("/industry")
def view_industry_index():
    """行业数据库首页"""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Count rows per table
    tables = {}
    for tbl in ["hbm_capacity", "hbm_supply_demand", "cowos_capacity", "memory_pricing",
                 "abf_metrics", "pcb_ccl_metrics", "mlcc_metrics"]:
        try:
            n = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()["c"]
            tables[tbl] = n
        except Exception:
            tables[tbl] = 0
    conn.close()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Industry Database</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:800px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
a{{color:#58a6ff;text-decoration:none;font-size:15px;display:block;padding:12px 16px;background:#161b22;border:1px solid #30363d;border-radius:6px;margin:8px 0}}
a:hover{{border-color:#58a6ff}}
a .desc{{font-size:12px;color:#8b949e;display:block;margin-top:4px}}
</style></head>
<body><div class="wrap">
<h1>🏭 行业结构化数据库</h1>
<div class="date">{datetime.now().strftime('%Y-%m-%d')} · <a href="/" style="display:inline;padding:0;background:none;border:none">← Dashboard</a> · <a href="/industry/chain" style="display:inline;padding:0;background:none;border:none;margin-left:12px">🔗 产业链矩阵</a></div>
<a href="/industry/hbm">💾 HBM TSV产能 & 供需比<span class="desc">SK Hynix / Samsung / Micron · TSV产能 · 供需比时间序列 ({tables.get('hbm_capacity',0)+tables.get('hbm_supply_demand',0)} rows)</span></a>
<a href="/industry/cowos">📦 CoWoS产能 & Booking<span class="desc">TSMC CoWoS total/eff · NVIDIA/Google/Broadcom/MediaTek booking ({tables.get('cowos_capacity',0)} rows)</span></a>
<a href="/industry/memory">💰 Memory价格时间序列<span class="desc">DDR5/DDR4/HBM3e/NAND/LPDDR5 合约价 QoQ YoY ({tables.get('memory_pricing',0)} rows)</span></a>
<a href="/industry/abf">📐 ABF载板<span class="desc">欣兴/南电/臻鼎/Ibiden · 营收 · ASP · 利润率 · 供需缺口 ({tables.get('abf_metrics',0)} rows)</span></a>
<a href="/industry/pcb">🖨️ PCB/CCL<span class="desc">AI PCB收入占比 · 环比增长 · CCL ASP · 服务器PCB份额 ({tables.get('pcb_ccl_metrics',0)} rows)</span></a>
<a href="/industry/mlcc">🔌 MLCC<span class="desc">SEMCO/村田/TDK · 营收 · ASP · 利润率 ({tables.get('mlcc_metrics',0)} rows)</span></a>
<a href="/industry/ai-datacenter">🏭 AI Infra Supply Chain<span class="desc">CPO/硅光子/光模块/铜缆/PCB/Cooling/Power · 技术路线 · 关键公司动态</span></a>

	<h2 style="margin-top:28px;font-size:16px;color:#58a6ff">📄 AI 生成行业分析报告 (每日更新)</h2>
	<a href="/industry/report/memory">💾 HBM / Memory<span class="desc">DRAM/NAND/HBM价格 · 供需动态 · 竞争格局 · LLM综合卖方研报</span></a>
	<a href="/industry/report/foundry">🏗️ Foundry / 代工<span class="desc">晶圆代工产能 · 先进制程 · 资本开支 · 国产替代</span></a>
	<a href="/industry/report/cowos">📦 CoWoS / 先进封装<span class="desc">TSMC CoWoS-L/R · 封装产能 · 外溢OSAT · 设备需求</span></a>
	<a href="/industry/report/ai-chip">🧠 AI Chip (GPU/TPU/ASIC)<span class="desc">训练/推理芯片 · 定制ASIC · 国产GPU · 芯片禁令</span></a>
	<a href="/industry/report/ai-infra">🔗 AI Infra Supply Chain<span class="desc">光模块/CPO/硅光子 · 铜缆/PCB/Cooling/Power · 数据中心互连</span></a>
</div></body></html>"""


@app.route("/industry/chain")
def view_industry_chain():
    """AI 产业链矩阵 — 架构图 + NVDA vs Google TPU 对比"""
    from industry_chain_renderer import load_matrix, render_mermaid_diagram, render_summary_json

    matrix = load_matrix()
    summary = render_summary_json(matrix)
    mermaid = render_mermaid_diagram(matrix, focus="all")

    # Build deep_chain lookup: key="{layer_id}:{supplier_name}" for client-side drill-down
    deep_chain_map = {}
    for layer in matrix["layers"]:
        for supplier_list_key in ["nvidia_suppliers", "google_tpu_suppliers", "ascend_suppliers"]:
            for supplier in layer.get(supplier_list_key, []):
                if "deep_chain" in supplier:
                    key = f"{layer['id']}:{supplier['name']}"
                    deep_chain_map[key] = supplier["deep_chain"]

    return render_template("industry_chain.html",
                           meta=matrix["meta"],
                           summary=summary,
                           layers=matrix["layers"],
                           mermaid_diagram=mermaid,
                           deep_chain_map=deep_chain_map)


@app.route("/consensus")
def view_consensus():
    """共识驱动因子页 — 展示 full/strong/partial consensus signals"""
    import sqlite3, json
    conn = sqlite3.connect(str(Path(__file__).parent / "logic_chains.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT company, driver_slug, consensus_level, bank_count, aggregated_json
        FROM aggregated_drivers
        WHERE consensus_level IN ('full','strong','partial')
        ORDER BY CASE consensus_level WHEN 'full' THEN 0 WHEN 'strong' THEN 1 ELSE 2 END,
                 bank_count DESC
    """).fetchall()

    signals = []
    for r in rows:
        data = json.loads(r["aggregated_json"])
        direction = data.get("direction", "neutral")

        # Get first/last report dates for this company's driver cluster
        banks = data.get("banks", [])
        if banks:
            placeholders = ",".join("?" * len(banks))
            dates = conn.execute(f"""
                SELECT MIN(date) as first_date, MAX(date) as last_date
                FROM logic_chains
                WHERE company LIKE ? AND bank IN ({placeholders})
            """, (f"%{r['company']}%", *banks)).fetchone()
        else:
            dates = None

        signals.append({
            "company": r["company"],
            "driver_slug": r["driver_slug"],
            "canonical": data.get("canonical", r["driver_slug"])[:80],
            "consensus": r["consensus_level"],
            "banks": r["bank_count"],
            "direction": direction,
            "impacts": len(data.get("impact_graph", [])),
            "evidence": len(data.get("evidence_matrix", [])),
            "bank_list": ", ".join(data.get("banks", [])[:5]),
            "disputes": len(data.get("disputes", [])),
            "first_date": dates["first_date"] if dates else "",
            "last_date": dates["last_date"] if dates else "",
        })

    conn.close()
    return render_template("consensus.html", signals=signals)


@app.route("/backtest")
def view_backtest():
    """回测结果页"""
    import sqlite3
    conn = sqlite3.connect(str(Path(__file__).parent / "backtest.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM backtest_real ORDER BY ABS(ret_30d) DESC LIMIT 100").fetchall()
    stats = conn.execute("""
        SELECT COUNT(*) as total, ROUND(AVG(CASE WHEN hit_30d=1 THEN 100.0 ELSE 0 END),1) as hr30
        FROM backtest_real
    """).fetchone()
    by_level = conn.execute("""
        SELECT consensus_level, COUNT(*) as n, ROUND(AVG(CASE WHEN hit_30d=1 THEN 100.0 ELSE 0 END),1) as hr
        FROM backtest_real GROUP BY consensus_level ORDER BY n DESC
    """).fetchall()
    conn.close()
    return render_template("backtest.html", results=[dict(r) for r in rows],
                           stats=dict(stats), by_level=[dict(r) for r in by_level])


@app.route("/contrarian")
def view_contrarian():
    """反共识信号检测页"""
    from contrarian_signals import get_top_signals
    signals = get_top_signals(limit=50, min_score=3.0)
    return render_template("contrarian.html", signals=signals)


@app.route("/api/contrarian")
def api_contrarian():
    """反共识信号 API"""
    from contrarian_signals import get_top_signals
    return jsonify(get_top_signals(limit=50, min_score=3.0))


@app.route("/chokepoint")
def view_chokepoint():
    """P0-P11 卡脖子技术总览页"""
    from industry_chain_renderer import build_chokepoint_index, load_matrix
    matrix = load_matrix()
    idx = build_chokepoint_index(matrix)
    return render_template("chokepoint.html",
                           last_updated=idx["last_updated"],
                           stats=idx["stats"],
                           entries=idx["entries"])


@app.route("/api/chokepoint/index")
def api_chokepoint_index():
    """完整反向索引 JSON"""
    from industry_chain_renderer import build_chokepoint_index, load_matrix
    matrix = load_matrix()
    return jsonify(build_chokepoint_index(matrix))


@app.route("/api/chokepoint/<name>")
def api_chokepoint_detail(name):
    """单个 L4 公司详情 + 下游影响链"""
    from industry_chain_renderer import build_chokepoint_index, load_matrix
    matrix = load_matrix()
    idx = build_chokepoint_index(matrix)
    entry = idx["entries"].get(name)
    if not entry:
        return jsonify({"error": f"Chokepoint company '{name}' not found"}), 404
    return jsonify(entry)


@app.route("/api/industry/chain")
def api_industry_chain():
    """JSON API for industry chain matrix"""
    from industry_chain_renderer import load_matrix, render_summary_json
    matrix = load_matrix()
    return jsonify(render_summary_json(matrix))


@app.route("/industry/<table>")
def view_industry_table(table):
    """渲染行业数据库为 HTML 页面"""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if table == "hbm":
        vendors = conn.execute("SELECT * FROM hbm_capacity ORDER BY year, vendor").fetchall()
        sd = conn.execute("SELECT * FROM hbm_supply_demand ORDER BY year").fetchall()
        conn.close()

        vendor_rows = ""
        for r in vendors:
            tb = f"{r['hbm_output_tb']:,.0f}" if r['hbm_output_tb'] else "—"
            vendor_rows += f"<tr><td>{r['vendor']}</td><td>{r['year']}</td><td>{r['tsv_kwpm']:,.0f}</td><td>{r['yield_pct']:.0f}%</td><td>{r['utr_pct']:.0f}%</td><td>{r['hbm_output_mn_gb']:,.0f}</td><td>{tb}</td><td class=\"src\">{r['source'][:40]}</td></tr>"

        sd_rows = ""
        for r in sd:
            s = r['sufficiency_pct']
            tag = "tag-hot" if s < 10 else ("tag-warn" if s < 30 else "tag-ok")
            sd_rows += f"<tr><td>{r['year']}</td><td>{r['hbm_demand_mn_gb']:,.0f}</td><td>{r['hbm_supply_mn_gb']:,.0f}</td><td><span class=\"tag {tag}\">{s:.0f}%</span></td><td class=\"src\">{r['source'][:40]}</td></tr>"

        tables_html = f"""
<h2>HBM TSV 产能 (K wpm)</h2>
<table><tr><th>Vendor</th><th>Year</th><th>TSV (K wpm)</th><th>Yield</th><th>UTR</th><th>Output (Mn Gb/yr)</th><th>Output (TB/yr)</th><th>Source</th></tr>
{vendor_rows}</table>

<h2>HBM 供需比</h2>
<table><tr><th>Year</th><th>Demand (Mn Gb)</th><th>Supply (Mn Gb)</th><th>Sufficiency</th><th>Source</th></tr>
{sd_rows}</table>
<p class=\"note\">⚠️ 2026 HBM sufficiency 仅 2% — 极度紧缺。Source: Morgan Stanley Global Tech Outlook 2026 (Jan 6, 2026)</p>

<h2>J.P. Morgan HBM Model (Jan 16, 2026) — 参考对照</h2>
<p class=\"note\">JPM HBM S-D 模型，口径为 1GB equivalent (calendar year)。与上表 Morgan Stanley 的 Mn Gb 口径不同，供趋势交叉验证。</p>
<div class=\"table-wrap\"><table>
<tr><th>指标</th><th>FY25E</th><th>FY26E</th><th>FY27E</th></tr>
<tr><td>HBM bit demand (mn 1GB eqv, calendar year)</td><td>2,011</td><td>3,408</td><td>4,828</td></tr>
<tr><td>  └─ NVIDIA</td><td>1,374</td><td>1,980</td><td>2,851</td></tr>
<tr><td>  └─ AMD</td><td>182</td><td>352</td><td>492</td></tr>
<tr><td>  └─ Others (ASIC/GOOG/AMZN)</td><td>455</td><td>1,076</td><td>1,484</td></tr>
<tr><td>HBM bit demand (t+4m procurement basis)</td><td>3,881</td><td>6,115</td><td>—</td></tr>
<tr><td>HBM industry revenue ($mn)</td><td>63,522</td><td>96,439</td><td>—</td></tr>
<tr><td>HBM ASP ($/Gb)</td><td>1.8</td><td>1.9</td><td>1.9</td></tr>
<tr style=\"background:rgba(248,81,73,0.08)\"><td><b>S-D glut ratio (CoWoS adjusted)</b></td><td><b>~-16%</b></td><td><b>~-8%</b></td><td><b>~-12%</b></td></tr>
<tr style=\"background:rgba(248,81,73,0.05)\"><td>S-D glut (weeks)</td><td>-3.8</td><td>-8.5</td><td>-13.7</td></tr>
</table></div>
<p class=\"note\">📌 Source: J.P. Morgan — Memory Market Update (Jan 16, 2026). \"Structural HBM shortage until 27E, potentially into 28E.\" 负值 = 供不应求。Glut ratio 趋势与 MS 的 sufficiency 数据方向一致，均指向 2026-2027 极限紧平衡/短缺。</p>
"""
        return _render_industry_html("HBM Capacity & Supply/Demand", tables_html
                                      + _industry_report_link("memory", "HBM/Memory"),
                                      chart_keywords=["hbm", "memory", "sk hynix", "samsung"],
                                      industry_slug="memory")

    elif table == "cowos":
        rows = conn.execute("SELECT * FROM cowos_capacity ORDER BY period").fetchall()
        conn.close()
        cowos_rows = ""
        for r in rows:
            cowos_rows += "<tr>"
            cowos_rows += f"<td><b>{r['period']}</b></td>"
            cowos_rows += f"<td>{r['total_wpm']:,.0f}</td>" if r['total_wpm'] else "<td>—</td>"
            cowos_rows += f"<td>{r['effective_wpm']:,.0f}</td>" if r['effective_wpm'] else "<td>—</td>"
            for col in ['nvidia_wpm', 'google_wpm', 'broadcom_wpm', 'mediatek_wpm', 'amd_wpm', 'intel_wpm', 'other_wpm']:
                v = r[col]
                cowos_rows += f"<td>{v:,.0f}</td>" if v else "<td>—</td>"
            cowos_rows += f"<td class=\"src\">{r['csp_note'] or ''}</td>"
            cowos_rows += "</tr>"

        tables_html = f"""
<h2>CoWoS 产能 + Booking (wpm)</h2>
<table><tr><th>Period</th><th>Total</th><th>Eff</th><th>NVIDIA</th><th>Google</th><th>Broadcom</th><th>MediaTek</th><th>AMD</th><th>Intel</th><th>Other</th><th>CSP Note</th></tr>
{cowos_rows}</table>
<p class=\"note\">📌 CSP 原厂备注: Google TPU v6e/v7e 占 CoWoS ~15% (2025E)。MediaTek 为 Google TPU foundry 代工伙伴 (TSMC CoWoS)。NVIDIA GB200 + Rubin 为主要消耗方 (~70%)。MTK TPU v7 2026E 15K wpm → v8 2027E 110K wpm (BofA)。</p>
"""
        return _render_industry_html("CoWoS Capacity & Booking", tables_html,
                                      chart_keywords=["cowos", "advanced packaging", "mediatek", "nvidia"],
                                      industry_slug="cowos")

    elif table == "memory":
        rows = conn.execute("SELECT * FROM memory_pricing ORDER BY product, period").fetchall()
        conn.close()
        # Group by product
        products = defaultdict(list)
        for r in rows:
            products[r['product']].append(r)

        tables_html = ""
        for product, items in products.items():
            title = product.replace("_", " ")
            pr = ""
            for r in items:
                price = f"${r['price']:.1f}" if r['price'] else "—"
                qoq = f"<span class=\"{'up' if (r['qoq_pct'] or 0) > 0 else 'down'}\">{r['qoq_pct']:+.0f}%</span>" if r['qoq_pct'] is not None else "—"
                yoy = f"<span class=\"{'up' if (r['yoy_pct'] or 0) > 0 else 'down'}\">{r['yoy_pct']:+.0f}%</span>" if r['yoy_pct'] is not None else "—"
                pr += f"<tr><td>{r['period']}</td><td>{price}</td><td>{qoq}</td><td>{yoy}</td><td>{r['price_type']}</td><td class=\"src\">{r['source'][:35]}</td></tr>"

            tables_html += f"<h2>{title}</h2>\n<table><tr><th>Period</th><th>Price (USD)</th><th>QoQ</th><th>YoY</th><th>Type</th><th>Source</th></tr>\n{pr}</table>\n"

        # Add Bernstein Memory Price Trend charts
        chart_dir = "Bernstein-Korea Memory Export Tracker (Mar)-A strong month, suggesting 1Q26 HBM revenue up mid-teens% QoQ for Samsung & SK hynix-260421_charts"
        trend_charts = sorted([
            f for f in (REPORT_BASE / chart_dir).glob("Trend-*.png") if (REPORT_BASE / chart_dir).exists()
        ]) if (REPORT_BASE / chart_dir).exists() else []
        # Also check lowercase
        if (REPORT_BASE / chart_dir).exists():
            trend_charts = sorted((REPORT_BASE / chart_dir).glob("[Tt]rend-*.png"))

        chart_html = ""
        if trend_charts:
            chart_html = '<h2>📈 Bernstein Memory Price Trends (Mar 2026)</h2>\n'
            chart_html += '<p class="note">Korea Memory Export Tracker — 价格趋势图。Source: Bernstein (Apr 21, 2026)</p>\n'
            from urllib.parse import quote as urlquote
            chart_html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:16px;margin:16px 0">\n'
            for cp in trend_charts:
                rel = str(cp.relative_to(REPORT_BASE))
                chart_html += f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px"><img src="/charts/{urlquote(rel)}" style="width:100%;height:auto" loading="lazy" alt="{cp.stem}"></div>\n'
            chart_html += '</div>\n'

        # Find latest LLM-generated industry analysis report
        latest_report = _latest_industry_report("memory")
        report_note = ("<p class=\"note\">📊 数据来源: Bernstein Memory Tracker, TrendForce, BofA, CLSA</p>"
                       + _industry_report_link("memory", "HBM/Memory"))
        return _render_industry_html("Memory Pricing Trends", tables_html + chart_html,
                                     report_note,
                                     chart_keywords=["memory", "dram", "nand", "pricing", "hbm"],
                                     industry_slug="memory")

    elif table in ("abf", "pcb", "mlcc"):
        table_map = {"abf": "abf_metrics", "pcb": "pcb_ccl_metrics", "mlcc": "mlcc_metrics"}
        tbl_name = table_map[table]
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({tbl_name})").fetchall()
                if d[1] not in ("id", "source", "updated_at")]
        rows = conn.execute(f"SELECT * FROM {tbl_name} ORDER BY period, company").fetchall()
        conn.close()
        tbody = ""
        for r in rows:
            td = ""
            for i, c in enumerate(cols):
                val = r[c] if r[c] is not None else "—"
                if isinstance(val, float):
                    val = f"{val:,.1f}"
                td += f"<td>{val}</td>"
            tbody += f"<tr>{td}</tr>"

        th = "".join(f"<th>{c}</th>" for c in cols)
        tables_html = f"<table><tr>{th}</tr>{tbody}</table>"
        titles = {"abf": "ABF 载板指标", "pcb": "PCB/CCL 指标", "mlcc": "MLCC 指标"}
        slugs = {"abf": "abf-substrate", "pcb": "pcb-ccl", "mlcc": "MLCC"}
        return _render_industry_html(titles[table], tables_html, industry_slug=slugs.get(table, ""))

    conn.close()
    return "<h1>Not found</h1>", 404


# ============ Remote Actions API ============

import subprocess as _sp
import threading as _th

_pipeline_status = {"running": False, "last_run": "", "output": ""}

def _run_pipeline_bg():
    global _pipeline_status
    _pipeline_status["running"] = True
    _pipeline_status["output"] = "Starting pipeline..."
    try:
        result = _sp.run(
            ["python3", str(PROJECT_DIR / "run_pipeline.py"), "--skip-download"],
            cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=3600
        )
        _pipeline_status["output"] = result.stdout[-2000:] or result.stderr[-2000:]

        # Auto-push critical alerts after pipeline completes
        try:
            from wechat_push import auto_push_critical_alerts
            pushed = auto_push_critical_alerts(verbose=True)
            if pushed:
                companies = ", ".join(p["company"] for p in pushed)
                _pipeline_status["output"] += f"\n📤 Auto-pushed: {companies}"
        except Exception as e:
            _pipeline_status["output"] += f"\n⚠️ Auto-push error: {e}"
    except Exception as e:
        _pipeline_status["output"] = str(e)
    _pipeline_status["running"] = False
    _pipeline_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")


@app.route("/api/actions/run-pipeline", methods=["POST"])
def api_run_pipeline():
    if _pipeline_status["running"]:
        return jsonify({"status": "already_running"})
    _th.Thread(target=_run_pipeline_bg, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/actions/status")
def api_pipeline_status():
    return jsonify(_pipeline_status)


@app.route("/api/actions/run-report", methods=["POST"])
def api_run_industry_reports():
    data = request.get_json() or {}
    topic = data.get("topic", "all")
    try:
        if topic == "all":
            result = _sp.run(
                ["python3", str(PROJECT_DIR / "industry_report.py"), "--all"],
                cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=600
            )
        else:
            result = _sp.run(
                ["python3", str(PROJECT_DIR / "industry_report.py"), topic],
                cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=300
            )
        return jsonify({"status": "ok", "output": result.stdout[-500:]})
    except Exception as e:
        return jsonify({"status": "error", "output": str(e)})


@app.route("/api/actions/scan", methods=["POST"])
def api_scan_new():
    data = request.get_json() or {}
    import glob as _g
    pdfs = _g.glob(str(REPORT_BASE / "**/*.pdf"), recursive=True)
    unanalyzed = [p for p in pdfs if not Path(p.replace(".pdf", "_analysis.md")).exists()]
    return jsonify({"unanalyzed": len(unanalyzed), "total": len(pdfs)})


# ============ Logic Chain API ============

@app.route("/api/logic/<company>")
def api_logic_company(company):
    """获取某公司聚合后的逻辑链 JSON"""
    from logic_aggregator import aggregate
    from run_pipeline import normalize_company

    # Try to match company name
    drivers = aggregate(company)
    if not drivers:
        return jsonify({"company": company, "drivers": [], "note": "No logic chains found"})

    return jsonify({
        "company": company,
        "drivers": [d.to_dict() for d in drivers],
        "generated_at": datetime.now().isoformat()
    })


@app.route("/api/supply-chain/<driver_slug>")
def api_supply_chain(driver_slug):
    """按 driver slug 查询产业链传导"""
    from logic_store import query_by_driver, query_by_entity

    chains = query_by_driver(driver_slug)
    if not chains:
        return jsonify({"driver": driver_slug, "chains": [], "note": "Not found"})

    # Collect unique entities
    entities = set()
    for c in chains:
        for imp in c.get("impacts", []):
            entities.add(imp.get("entity", ""))

    # Trace ripple effects
    ripple = {}
    for entity in entities:
        impacted = query_by_entity(entity, days=90)
        ripple[entity] = {
            "as_target": len([c for c in chains if entity in str(c)]),
            "downstream_impact": len(impacted)
        }

    return jsonify({
        "driver": driver_slug,
        "chain_count": len(chains),
        "entities": sorted(entities),
        "ripple": ripple
    })


@app.route("/logic/<company>")
def view_logic_dashboard(company):
    """交互式逻辑链仪表盘 HTML (cached aggregation)"""
    from logic_store import getAllLogicChainsForCompany, load_aggregated_drivers

    # Use cached aggregation if available (avoids LLM call on every page load)
    cached = load_aggregated_drivers(company)
    if cached:
        drivers_data = cached
    else:
        from logic_aggregator import aggregate
        drivers = aggregate(company)
        drivers_data = [d.to_dict() for d in drivers]

    all_chains = getAllLogicChainsForCompany(company)
    if not all_chains:
        all_chains = []

    if not drivers_data:
        return f"<h1>No logic chains found for {company}</h1>", 404

    # Build HTML sections
    driver_cards = ""
    for i, ad in enumerate(drivers_data):
        consensus_badge = {"full": "✅✅ Full Consensus", "strong": "✅ Strong",
                           "partial": "⚡ Partial", "isolated": "🔍 Isolated"}
        badge = consensus_badge.get(ad.get("consensus_level", ""), "❓")

        evidence_rows = ""
        for row in ad.get("evidence_matrix", [])[:5]:
            banks_data = " | ".join(f"{k}: {v}" for k, v in sorted(row.items()) if k != "metric")
            evidence_rows += f"<tr><td>{row['metric']}</td><td style='font-size:12px'>{banks_data}</td></tr>"

        impact_rows = ""
        for imp in ad.get("impact_graph", [])[:5]:
            impact_rows += f"<li><b>{imp['entity']}</b> [{imp['role']}] → {imp['effect']} <span class='src'>({', '.join(imp.get('banks', []))})</span></li>"

        disputes_html = ""
        if ad.get("disputes"):
            for d in ad["disputes"]:
                disputes_html += f"""<div class="dispute">
<b>{d['topic']}</b><br>🐂 {d['bull']}<br>🐻 {d['bear']}</div>"""

        driver_cards += f"""
<div class="card">
  <div class="card-title">{i}. {ad.get('canonical', '')} <span class="tag">{badge}</span></div>
  <div style="color:#8b949e;font-size:12px;margin-bottom:12px">
    {ad.get('report_count', 0)} reports | {', '.join(ad.get('banks', [])[:6])} | {ad.get('direction', '')}
  </div>
  <h3>证据矩阵</h3>
  <div class="table-wrap"><table><tr><th>Metric</th><th>Banks</th></tr>{evidence_rows}</table></div>
  <h3>产业链传导</h3>
  <ul>{impact_rows}</ul>
  {f'<h3>与前期变化</h3><p>{ad.get("change_consensus", "")}</p>' if ad.get("change_consensus") else ''}
  {disputes_html}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company} — Logic Chain Trace</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
h2{{font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}}
h3{{font-size:13px;color:#58a6ff;margin:16px 0 6px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:0 0 16px}}
.card-title{{font-size:15px;font-weight:700;color:#58a6ff;margin:0 0 4px}}
.tag{{display:inline-block;background:#1f3d2f;color:#3fb950;border-radius:4px;padding:1px 8px;font-size:11px;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:6px 10px;border:1px solid #30363d;font-size:11px}}
td{{padding:6px 10px;border:1px solid #30363d}}
.dispute{{background:#3d2f0f;border-left:3px solid #d29922;padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0;font-size:13px}}
.src{{font-size:10px;color:#8b949e}}
li{{margin:4px 0;font-size:13px}}
a{{color:#58a6ff}}
</style></head>
<body><div class="wrap">
<h1>{company} — 逻辑链溯源</h1>
<div class="date">{datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(drivers_data)} drivers | <a href="/">← Dashboard</a>
	  <button onclick="pushLogic('{company}')"
	          style="margin-left:16px;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 14px;color:#3fb950;font-weight:600;font-size:12px;cursor:pointer">
	    📤 推送微信
	  </button>
	  <span id="push-status" style="font-size:11px;color:#8b949e;margin-left:8px"></span></div>
{driver_cards}
</div>
	<script>
const INDUSTRY_OPTIONS = ["LLM / \u5927\u6a21\u578b", "CoWoS / Advanced Packaging", "HBM / Memory", "AI Chip (GPU/TPU/ASIC)", "Foundry / Capacity", "Computing Power / Datacenter", "Power / Energy", "Interconnect / Optical", "AI Infra Supply Chain", "MLCC", "ABF Substrate / IC\u8f7d\u677f", "PCB / CCL (\u5370\u5236\u7535\u8def\u677f&\u8986\u94dc\u677f)"];
	function pushLogic(name) {{
	  var btn = event.target;
	  btn.disabled = true;
	  btn.textContent = '推送中...';
	  document.getElementById('push-status').textContent = '';
	  fetch('/api/push/logic/' + encodeURIComponent(name), {{method:'POST'}})
	    .then(r => r.json())
	    .then(d => {{
	      if (d.status === 'ok') {{ document.getElementById('push-status').textContent = '✅ 已推送'; }}
	      else {{ document.getElementById('push-status').textContent = '⚠️ ' + (d.reason || 'failed'); }}
	    }})
	    .catch(e => {{ document.getElementById('push-status').textContent = '❌ Error'; }})
	    .finally(() => {{ btn.disabled = false; btn.textContent = '📤 推送微信'; }});
	}}
	</script>
</body></html>"""


# ============ Panel API ============

@app.route("/api/panel/<company>")
def api_panel_company(company):
    """信号面板 JSON"""
    from panel import analyze_signal_lifecycle
    try:
        signals = analyze_signal_lifecycle(company)
        return jsonify({
            "company": company,
            "driver_count": len(signals),
            "signals": signals,
            "generated_at": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/panel/scan")
def api_panel_scan():
    """全市场信号扫描 JSON"""
    from panel import scan_all_signals
    try:
        scan = scan_all_signals()
        return jsonify({
            "scan": {
                "emerging": scan["emerging"][:20],
                "diverging": scan["diverging"][:20],
                "high_consensus": scan["high_consensus"][:20]
            },
            "generated_at": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ Company Weekly Digest ============

def _render_heat_card(reports):
    """投行热度指标：1周内多家投行密集发报告 = 高热度"""
    from datetime import timedelta

    today = datetime.now().date()
    week_ago = today - timedelta(days=7)
    recent_banks = set()
    for r in reports:
        try:
            rd = datetime.strptime(r["date"], "%Y-%m-%d").date() if r["date"] else None
        except ValueError:
            rd = None
        if rd and rd >= week_ago:
            recent_banks.add(r["bank"])

    n = len(recent_banks)
    if n >= 5:
        heat, color, label = "🔥🔥🔥", "#f85149", "Extreme"
    elif n >= 4:
        heat, color, label = "🔥🔥", "#d29922", "High"
    elif n >= 3:
        heat, color, label = "🔥", "#58a6ff", "Elevated"
    elif n >= 2:
        heat, color, label = "📊", "#3fb950", "Normal"
    else:
        heat, color, label = "📌", "#8b949e", "Low"

    return f'<div class="summary-card" style="border-color:{color}"><div class="num" style="color:{color}">{heat}</div><div class="lbl">🏦 周热度 · {label} ({n} banks)</div></div>'


@app.route("/company/<name>")
def view_company_digest(name):
    """公司周报摘要 — 近期报告要点 + 原始PDF链接"""
    from run_pipeline import normalize_company
    from utils import extract_bank_from_filename
    from logic_store import load_aggregated_drivers

    canonical = normalize_company(name)

    # Collect all analysis data for this company
    reports = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            parsed = d.get("parsed", {})
            co = parsed.get("company", "") or ""
            if normalize_company(co) != canonical:
                continue
        except Exception:
            continue

        pdf_name = d.get("pdf_name", "")
        bank = extract_bank_from_filename(pdf_name)
        tp = parsed.get("target_price") or {}
        rating = parsed.get("rating", "") or "N/A"

        # Find original PDF (search broadly — may be in different subdir)
        stem = f.stem.replace("_analysis", "")
        pdf_rel = ""
        for ext in [".pdf", ".pptx", ".xlsx"]:
            # Try same dir, then parent dirs, then broad search
            for search_dir in [f.parent, f.parent.parent, REPORT_BASE]:
                candidate = search_dir / f"{stem}{ext}"
                if candidate.exists():
                    pdf_rel = str(candidate.relative_to(REPORT_BASE))
                    break
            if pdf_rel:
                break
            # Fallback: broad glob search
            if not pdf_rel:
                matches = list(REPORT_BASE.rglob(f"{stem}{ext}"))
                if matches:
                    pdf_rel = str(matches[0].relative_to(REPORT_BASE))
                    break

        # Extract date from filename (suffix -YYMMDD)
        date_m = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|$)', pdf_name)
        report_date = ""
        if date_m:
            ds = date_m.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"

        # Get first 300 chars of analysis markdown as summary
        md_path = f.parent / f"{stem}_analysis.md"
        summary = ""
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            # Extract the "核心发现" or "报告摘要" section
            findings_m = re.search(r'(?:核心发现|报告摘要|Key Findings)[\s\S]*?(?=##|\Z)',
                                   md_text, re.IGNORECASE)
            if findings_m:
                summary = findings_m.group(0)[:300].strip()
            else:
                summary = md_text[200:500].strip()

        # Check if logic chains exist
        logic_rel = ""
        logic_path = f.parent / f"{stem}_logic.json"
        if logic_path.exists():
            logic_rel = str(logic_path.relative_to(REPORT_BASE))

        reports.append({
            "pdf_name": pdf_name,
            "bank": bank,
            "date": report_date,
            "rating": rating,
            "tp_new": tp.get("new"),
            "tp_old": tp.get("old"),
            "tp_currency": tp.get("currency", ""),
            "summary": summary,
            "pdf_rel": pdf_rel,
            "md_rel": str(md_path.relative_to(REPORT_BASE)) if md_path.exists() else "",
            "logic_rel": logic_rel,
        })

    # Get aggregated drivers
    drivers_data = load_aggregated_drivers(canonical)
    if not drivers_data:
        from logic_aggregator import aggregate
        drivers = aggregate(canonical)
        drivers_data = [d.to_dict() for d in drivers]

    # Sort reports by date descending (newest first)
    reports.sort(key=lambda r: r["date"], reverse=True)

    # Report cards HTML
    report_cards = ""
    for r in reports[:15]:
        tp_str = ""
        if r["tp_new"]:
            tp_str = f"{r['tp_new']:,.0f}"
            if r["tp_old"] and r["tp_old"] > 0:
                chg = (r["tp_new"] - r["tp_old"]) / r["tp_old"] * 100
                arrow = "↑" if chg > 0 else "↓"
                tp_str += f" <span class='{'up' if chg > 0 else 'down'}'>{arrow}{abs(chg):.0f}%</span>"
            tp_str += f" {r['tp_currency']}"

        links = ""
        if r["pdf_rel"]:
            from urllib.parse import quote as urlquote
            links += f'<a href="/pdf/{urlquote(r["pdf_rel"])}" target="_blank">📑 PDF</a> '
        if r["md_rel"]:
            from urllib.parse import quote as urlquote
            links += f'<a href="/report/view?path={urlquote(r["md_rel"])}" target="_blank">📄 Analysis</a> '
        if r["logic_rel"]:
            links += f'<a href="/report/view?path={r["logic_rel"].replace("_logic.json", "_logic.md") if False else ""}" ></a>'

        summary_text = r["summary"].replace("<", "&lt;").replace(">", "&gt;")[:250]
        report_cards += f"""
<div class="rpt-card">
  <div class="rpt-header">
    <span class="rpt-bank">{r['bank']}</span>
    <span class="rpt-date">{r['date']}</span>
    <span class="rpt-rating">{r['rating']}</span>
    <span class="rpt-tp">{tp_str}</span>
  </div>
  <div class="rpt-summary">{summary_text}...</div>
  <div class="rpt-links">{links}</div>
</div>"""

    # Driver highlight
    driver_html = ""
    for ad in drivers_data[:5]:
        badge = {"full": "✅✅", "strong": "✅", "partial": "⚡", "isolated": "🔍"}
        b = badge.get(ad.get("consensus_level", ""), "")
        driver_html += f"""
<div class="driver-item">
  <span class="tag">{b}</span>
  <b>{ad.get('canonical', '')}</b>
  <span style="color:#8b949e;font-size:11px">({ad.get('report_count', 0)} reports · {', '.join(ad.get('banks', [])[:3])})</span>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{canonical} — Weekly Digest</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
h2{{font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}
.rpt-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:0 0 12px}}
.rpt-header{{display:flex;gap:12px;align-items:center;margin-bottom:8px;flex-wrap:wrap}}
.rpt-bank{{font-weight:700;color:#58a6ff;min-width:120px}}
.rpt-date{{color:#8b949e;font-size:12px}}
.rpt-rating{{background:#1f3d2f;color:#3fb950;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.rpt-summary{{font-size:13px;color:#c9d1d9;line-height:1.6;margin:8px 0}}
.rpt-links{{font-size:12px;margin-top:8px}}
.rpt-links a{{margin-right:12px;padding:4px 10px;background:#0d1117;border:1px solid #30363d;border-radius:4px}}
.rpt-links a:hover{{border-color:#58a6ff}}
.driver-item{{padding:6px 0;font-size:13px;border-bottom:1px solid #21262d}}
.up{{color:#3fb950;font-weight:600}}
.down{{color:#f85149;font-weight:600}}
.tag{{display:inline-block;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600;color:#58a6ff;margin-right:4px}}
.src{{font-size:10px;color:#8b949e}}
.summary-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}}
.summary-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-align:center}}
.summary-card .num{{font-size:28px;font-weight:800;line-height:1.2}}
.summary-card .lbl{{font-size:11px;color:#8b949e;margin-top:4px}}
</style></head>
<body><div class="wrap">
<h1>{canonical} — 周报摘要</h1>
<div class="date">{datetime.now().strftime('%Y-%m-%d')} · {len(reports)} reports · <a href="/">← Dashboard</a>{_render_company_industry_badge(canonical)}</div>

<div style="margin:8px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
<a href="/logic/{canonical}" style="display:inline-block;background:#1f3d2f;border:1px solid #3fb950;border-radius:6px;padding:8px 18px;color:#3fb950;font-weight:700;font-size:14px;text-decoration:none">📊 逻辑链溯源 →</a>
<button onclick="pushCompany('{canonical}')" style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 16px;color:#58a6ff;font-weight:600;font-size:13px;cursor:pointer">📤 推送微信</button>
<span id="push-status" style="font-size:12px;color:#8b949e"></span>
</div>
<script>
const INDUSTRY_OPTIONS = ["LLM / \u5927\u6a21\u578b", "CoWoS / Advanced Packaging", "HBM / Memory", "AI Chip (GPU/TPU/ASIC)", "Foundry / Capacity", "Computing Power / Datacenter", "Power / Energy", "Interconnect / Optical", "AI Infra Supply Chain", "MLCC", "ABF Substrate / IC\u8f7d\u677f", "PCB / CCL (\u5370\u5236\u7535\u8def\u677f&\u8986\u94dc\u677f)"];
function pushCompany(name) {{
  var btn = event.target;
  btn.disabled = true;
  btn.textContent = '推送中...';
  document.getElementById('push-status').textContent = '';
  fetch('/api/push/company/' + encodeURIComponent(name), {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'ok') {{ document.getElementById('push-status').textContent = '✅ 已推送'; }}
      else {{ document.getElementById('push-status').textContent = '⚠️ ' + (d.reason || 'failed'); }}
    }})
    .catch(e => {{ document.getElementById('push-status').textContent = '❌ Error'; }})
    .finally(() => {{ btn.disabled = false; btn.textContent = '📤 推送微信'; }});
}}
</script>

<div class="summary-grid">
  <div class="summary-card"><div class="num" style="color:#58a6ff">{len(reports)}</div><div class="lbl">📄 Reports</div></div>
  <div class="summary-card"><div class="num" style="color:#3fb950">{len(set(r['bank'] for r in reports))}</div><div class="lbl">🏦 Banks</div></div>
  {_render_heat_card(reports)}
  <div class="summary-card"><div class="num" style="color:#d29922">{len(drivers_data)}</div><div class="lbl">🔗 Drivers</div></div>
</div>

<h2>🔥 要点 (Hot Drivers)</h2>
{driver_html}
<div style="margin:12px 0"><a href="/logic/{canonical}" style="font-size:14px;font-weight:600">🔗 查看完整逻辑链溯源报告 →</a></div>

<h2>📋 近期报告 ({len(reports)})</h2>
{report_cards}
</div></body></html>"""


# ============ Macro View Dashboard ============

@app.route("/macro")
def view_macro():
    """宏观趋势判断面板 — sector conviction"""
    import sys
    sys.path.insert(0, str(Path.home() / "ClaudeCode/QuanTrading"))
    from core.macro_view import MacroView

    mv = MacroView.from_config()

    cards = ""
    for s in sorted(mv.sectors.values(), key=lambda x: abs(x.conviction), reverse=True):
        pct = int((s.conviction + 1) / 2 * 100)
        color = "#3fb950" if s.conviction > 0.3 else ("#f85149" if s.conviction < -0.3 else "#d29922")
        emoji = "🟢" if s.conviction > 0.3 else ("🔴" if s.conviction < -0.3 else "🟡")
        bar = "█" * int(abs(s.conviction) * 10) + "░" * (10 - int(abs(s.conviction) * 10))

        cards += f"""
<div class="sector-card" style="border-left:4px solid {color}">
  <div class="sector-header">
    <span style="font-size:18px">{emoji}</span>
    <b style="font-size:16px">{s.label}</b>
    <span style="color:{color};font-weight:700;font-size:18px">{s.conviction:+.1f}</span>
  </div>
  <div class="bar">{bar}</div>
  <div class="rationale">{s.rationale or '（无备注）'}</div>
  <div style="font-size:10px;color:#8b949e">更新: {s.updated_at[:16] if s.updated_at else 'N/A'}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Macro View</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:700px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#8b949e;font-size:12px;margin-bottom:24px}}
.sector-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:0 0 12px}}
.sector-header{{display:flex;gap:10px;align-items:center;margin-bottom:8px}}
.bar{{font-family:monospace;color:#8b949e;margin:4px 0;letter-spacing:2px}}
.rationale{{font-size:13px;color:#c9d1d9;margin:8px 0;line-height:1.5}}
a{{color:#58a6ff}}
</style></head>
<body><div class="wrap">
<h1>📊 Macro View — 宏观趋势判断</h1>
<div class="sub">Sector Conviction · 机器无法取代的人为判断 · <a href="/">← Dashboard</a> · <a href="/industry/chain" style="display:inline;padding:0;background:none;border:none;margin-left:12px">🔗 产业链矩阵</a></div>
{cards}
<div style="margin-top:24px;padding:12px;background:#161b22;border:1px solid #30363d;border-radius:8px;font-size:11px;color:#8b949e;line-height:1.6">
<b>使用方法：</b> 编辑 <code>QuanTrading/config/macro_view.json</code><br>
conviction +1（强烈看多）→ 策略全开 | 0（中性）→ 半仓仅网格 | -1（看空）→ 止损暂停<br>
<b>这是系统的核心差异化——宏观趋势判断无法被算法替代</b>
</div>
</div></body></html>"""


# ============ WeChat Push API ============

@app.route("/api/push/company/<name>", methods=["POST"])
def api_push_company(name):
    """推送公司关键Alert草稿到公众号（LLM精简格式）"""
    from run_pipeline import normalize_company
    from wechat_push import scan_critical_alerts, push_draft, generate_push_summary, render_push_html

    canonical = normalize_company(name)
    all_alerts = scan_critical_alerts()
    company_alerts = [a for a in all_alerts if canonical.lower() in a["company"].lower()]

    if not company_alerts:
        return jsonify({"status": "skipped", "reason": f"No critical alerts for {canonical}"})

    # Build structured context for LLM
    alert_lines = []
    for a in company_alerts[:5]:
        direction = "上调" if a["change_pct"] > 0 else "下调"
        alert_lines.append(
            f"{a['bank']}: {a['company']} TP {direction} {abs(a['change_pct']):.0%} "
            f"({a['old_tp']:,.0f}→{a['new_tp']:,.0f} {a['currency']}), "
            f"评级{a['rating']}, 日期{a['date']}, "
            f"分类:{a['category']}, 驱动:{'; '.join(a.get('drivers', [])[:2])}"
        )
    context = f"公司:{canonical}\n告警数:{len(company_alerts)}\n" + "\n".join(alert_lines)

    # Generate concise push content via LLM
    try:
        summary = generate_push_summary(context, content_type="company")
    except Exception as e:
        return jsonify({"status": "error", "reason": f"LLM generation failed: {e}"}), 500

    banks = sorted(set(a["bank"] for a in company_alerts))
    title = f"{canonical} 关键变化 — {', '.join(banks[:3])}"
    html = render_push_html(title, summary, source_info=", ".join(banks))

    try:
        media_id = push_draft(title, html,
                              digest=summary[:80].replace("\n", " ").replace("#", "").strip())
        return jsonify({"status": "ok", "media_id": media_id})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/api/push/logic/<company>", methods=["POST"])
def api_push_logic(company):
    """推送逻辑链仪表盘到公众号（含表格图片）"""
    from logic_store import load_aggregated_drivers
    from wechat_push import (push_draft, send_preview, build_logic_push_html)

    drivers_data = load_aggregated_drivers(company)
    if not drivers_data:
        from logic_aggregator import aggregate
        drivers = aggregate(company)
        drivers_data = [d.to_dict() for d in drivers]

    if not drivers_data:
        return jsonify({"status": "error", "reason": f"No logic chains for {company}"}), 404

    title = f"{company} 逻辑链溯源"
    try:
        html = build_logic_push_html(company, drivers_data)
    except Exception as e:
        return jsonify({"status": "error", "reason": f"HTML build failed: {e}"}), 500

    try:
        digest = f"{len(drivers_data)}个驱动因素 · {sum(d.get('report_count',0) for d in drivers_data)}份报告"
        media_id = push_draft(title, html, digest=digest)
        send_preview(media_id)
        return jsonify({"status": "ok", "media_id": media_id})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/api/push/industry/<slug>", methods=["POST"])
def api_push_industry(slug):
    """推送行业报告草稿到公众号（LLM精简格式）"""
    from wechat_push import push_draft, generate_push_summary, render_push_html

    # Map page slugs to config industry slugs (e.g. hbm→memory)
    slug_variants = {slug}
    cfg = _read_config()
    for ind in cfg.get("tracking", {}).get("industries", []):
        if ind["slug"] == slug or slug in ind.get("keywords", []):
            slug_variants.add(ind["slug"])
        for kw in ind.get("keywords", []):
            if kw.lower() == slug.lower():
                slug_variants.add(ind["slug"])

    report_md = None
    for s in slug_variants:
        for pattern in [f"INDUSTRY_{s}_*.md", f"AGGREGATED_*_{s}_*.md"]:
            candidates = sorted(REPORT_BASE.rglob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
            if candidates:
                report_md = candidates[0]
                break
        if report_md:
            break

    if not report_md:
        return jsonify({"status": "skipped", "reason": f"No report for {slug}"}), 404

    md_text = report_md.read_text(encoding="utf-8")[:3000]

    # Generate concise push content via LLM
    try:
        summary = generate_push_summary(md_text, content_type="industry")
    except Exception as e:
        return jsonify({"status": "error", "reason": f"LLM generation failed: {e}"}), 500

    title = f"{slug.upper()} 行业快讯"
    html = render_push_html(title, summary, source_info=report_md.stem[:50])

    try:
        media_id = push_draft(title, html,
                              digest=summary[:80].replace("\n", " ").replace("#", "").strip())
        return jsonify({"status": "ok", "media_id": media_id})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


# ============ Supply Chain Graph API ============

@app.route("/api/supply-chain-graph")
def api_supply_chain_graph():
    """全局产业传导图 JSON"""
    from supply_chain_graph import SupplyChainGraph, render_graph_json
    graph = SupplyChainGraph().load_all()
    entity = request.args.get("entity", "")
    depth = int(request.args.get("depth", 3))
    data = render_graph_json(graph)
    if entity:
        from supply_chain_graph import normalize_entity
        data["focus"] = entity
        data["paths"] = graph.find_paths(normalize_entity(entity), max_depth=depth)
    return jsonify(data)


@app.route("/supply-chain")
def view_supply_chain():
    """全局产业传导图 HTML"""
    from supply_chain_graph import SupplyChainGraph, render_graph_markdown
    graph = SupplyChainGraph().load_all()
    summary = graph.summary()
    entity = request.args.get("entity", "")

    # Build simple HTML
    rows = ""
    for entity_name, degree in summary["most_connected"][:25]:
        outgoing = graph.get_edges_for(entity_name)
        incoming = graph.get_incoming_to(entity_name)
        up = ", ".join(sorted(set(e[0] for e in incoming))[:2])
        down = ", ".join(sorted(set(e["target"] for e in outgoing))[:2])
        roles = set(e["role"] for e in outgoing)
        rows += f"<tr><td><a href='?entity={entity_name}'>{entity_name}</a></td><td>{degree}</td><td>{up}</td><td>{down}</td><td>{', '.join(roles)}</td></tr>"

    focus_html = ""
    if entity:
        from supply_chain_graph import normalize_entity
        paths = graph.find_paths(normalize_entity(entity), max_depth=3)
        if paths:
            path_items = ""
            for i, path in enumerate(paths[:15], 1):
                chain = " → ".join(
                    f"<b>{p['from']}</b> <span style='color:#8b949e'>[{p['role']}]</span>"
                    for p in path
                )
                chain += f" → <b>{path[-1]['target']}</b>"
                banks = set(p.get("bank", "") for p in path if p.get("bank"))
                path_items += f"<li>{chain}<br><span class='src'>{', '.join(sorted(banks)[:4])}</span></li>"
            focus_html = f"<h2>🔍 {entity} 传导路径</h2><ul>{path_items}</ul>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Supply Chain Graph</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:20px}}
h2{{font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:20px 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px}}
td{{padding:8px 10px;border:1px solid #30363d}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
a{{color:#58a6ff}}
.src{{font-size:10px;color:#8b949e}}
li{{margin:8px 0;font-size:13px;line-height:1.6}}
</style></head>
<body><div class="wrap">
<h1>🏭 全局产业传导图</h1>
<div class="date">{summary['total_entities']} entities · {summary['total_edges']} edges · {len(summary['companies_loaded'])} companies · <a href="/">← Dashboard</a> · <a href="/industry/chain" style="display:inline;padding:0;background:none;border:none;margin-left:12px">🔗 产业链矩阵</a></div>
{focus_html}
<h2>核心传导节点</h2>
<table>
<tr><th>Entity</th><th>Degree</th><th>Upstream</th><th>Downstream</th><th>Roles</th></tr>
{rows}
</table>
</div></body></html>"""


# ============ Alert Push API ============

@app.route("/api/push/alert", methods=["POST"])
def api_push_alert():
    """针对单条Alert生成精简摘要并推送微信公众号（统一模板）"""
    data = request.get_json()
    md_path = data.get("md_path", "")
    title = data.get("title", "")
    bank = data.get("bank", "")
    company = data.get("company", "")

    if not md_path or not Path(md_path).exists():
        return jsonify({"status": "error", "reason": "Report not found"}), 404

    md_text = Path(md_path).read_text(encoding="utf-8")[:3000]

    # Build structured context for LLM
    context = f"投行:{bank}\n公司:{company}\n报告内容:\n{md_text}"

    # Generate concise push content via unified LLM pipeline
    from wechat_push import push_draft, generate_push_summary, render_push_html
    try:
        summary = generate_push_summary(context, content_type="company")
    except Exception as e:
        return jsonify({"status": "error", "reason": f"LLM generation failed: {e}"}), 500

    push_title = title or f"{bank}: {company}"
    html = render_push_html(push_title, summary, source_info=bank)

    try:
        media_id = push_draft(push_title, html, digest=summary[:80].replace("\n", " ").strip())
        return jsonify({"status": "ok", "media_id": media_id})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


# ============ Industry Reports List ============

@app.route("/api/industry-reports")
def api_industry_reports():
    """列出生成的行业报告文件"""
    reports = []
    for f in sorted(REPORT_BASE.rglob("INDUSTRY_*.md"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        reports.append({
            "name": f.stem,
            "path": str(f.relative_to(REPORT_BASE)),
            "size_kb": round(f.stat().st_size / 1024, 1),
            "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        })
    return jsonify(reports)


# ============ IB Analysis Page ============

BANK_ALIASES = {
    "MS": "Morgan Stanley", "GS": "Goldman Sachs",
    "JPM": "J.P. Morgan", "J.P. Morgan": "J.P. Morgan",
    "Bofa": "BofA Securities", "BofA Securities": "BofA Securities",
    "Citi": "CITI", "CITI": "CITI",
    "Morgan Stanley": "Morgan Stanley", "Goldman Sachs": "Goldman Sachs",
    "Bernstein": "Bernstein", "Nomura": "Nomura", "UBS": "UBS",
    "Jefferies": "Jefferies", "HSBC": "HSBC", "Deutsche Bank": "Deutsche Bank",
    "CLSA": "CLSA", "CICC": "CICC",
}


@app.route("/banks")
def view_banks():
    """IB Analysis — browse reports by investment bank."""
    from utils import extract_bank_from_filename

    # Collect all reports grouped by normalized bank
    banks = {}
    for jf in sorted(REPORT_BASE.rglob("*_analysis.json"), reverse=True):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdf = data.get("pdf_name", jf.stem.replace("_analysis", ""))
        raw_bank = extract_bank_from_filename(pdf) or "Other"
        bank = BANK_ALIASES.get(raw_bank, raw_bank)

        parsed = data.get("parsed", {})
        company = parsed.get("company", "") or ""
        rating = parsed.get("rating", "") or ""
        tp = parsed.get("target_price", {}) or {}
        alert_sev = parsed.get("alert_severity", "")

        # Extract report date from filename suffix -YYMMDD
        date_m = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|$)', pdf)
        report_date = f"20{date_m.group(1)[:2]}-{date_m.group(1)[2:4]}-{date_m.group(1)[4:6]}" if date_m else jf.parent.name

        # Relative paths for links
        md_rel = f"{jf.parent.name}/{jf.stem.replace('_analysis', '')}_analysis.md"
        pdf_rel = ""
        stem = jf.stem.replace("_analysis", "")
        for ext in [".pdf", ".pptx", ".xlsx"]:
            candidate = jf.parent / f"{stem}{ext}"
            if candidate.exists():
                pdf_rel = f"{jf.parent.name}/{stem}{ext}"
                break

        # Extract topic from filename: "Bank-Report Title-YYMMDD.pdf" → "Report Title"
        topic = ""
        if pdf:
            # Remove bank prefix: "Bank-"
            m = re.match(r'^[A-Za-z\s&.]+?[-](.+)$', pdf)
            if m:
                rest = m.group(1)
                # Remove date suffix: "-YYMMDD" at end (before .pdf/.pptx)
                rest = re.sub(r'-\d{6}(?:\.\w+)?$', '', rest)
                topic = rest.strip()[:60]

        banks.setdefault(bank, []).append({
            "name": pdf[:80],
            "company": str(company)[:50],
            "topic": topic,
            "rating": str(rating)[:15],
            "tp_new": tp.get("new", ""),
            "tp_currency": tp.get("currency", ""),
            "alert": alert_sev,
            "md_rel": md_rel,
            "pdf_rel": pdf_rel,
            "date": report_date,
        })

    # Sort each bank's reports by date descending
    for bank in banks:
        banks[bank].sort(key=lambda r: r["date"], reverse=True)

    # Build bank list HTML (left panel)
    sorted_banks = sorted(
        [(b, r) for b, r in banks.items() if b != "?"],
        key=lambda x: -len(x[1])
    )
    bank_list = ""
    for i, (bank, reports) in enumerate(sorted_banks):
        active = "active" if i == 0 else ""
        safe_bank = bank.replace("'", "\\'").replace('"', '&quot;')
        bank_list += f'<div class="bank-item {active}" onclick="selectBank(\'{safe_bank}\')" data-bank="{safe_bank}">{bank}<span class="count">{len(reports)}</span></div>'

    # Build reports JSON for JS
    banks_json = json.dumps({b: r for b, r in sorted_banks}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>IB Analysis</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;overflow:hidden}}
.header{{display:flex;align-items:center;gap:12px;padding:12px 20px;background:#161b22;border-bottom:1px solid #30363d}}
.header h1{{font-size:16px}}
.header a{{color:#58a6ff;font-size:12px;text-decoration:none}}
.panels{{display:flex;height:calc(100vh - 50px)}}
.panel{{overflow-y:auto;padding:12px}}
.panel::-webkit-scrollbar{{width:4px}}
.panel::-webkit-scrollbar-thumb{{background:#30363d;border-radius:2px}}

/* Left: IB list */
.panel-left{{width:200px;min-width:200px;background:#0d1117;border-right:1px solid #30363d}}
.bank-item{{padding:8px 12px;cursor:pointer;font-size:13px;border-radius:4px;margin:2px 0;display:flex;justify-content:space-between;align-items:center}}
.bank-item:hover{{background:#1a1f2e}}
.bank-item.active{{background:#1f3d2f;color:#3fb950;font-weight:700}}
.bank-item .count{{font-size:11px;color:#8b949e;background:#21262d;border-radius:10px;padding:1px 8px}}
.bank-item.active .count{{background:#238636;color:#fff}}

/* Center: report list */
.panel-center{{width:320px;min-width:320px;background:#0d1117;border-right:1px solid #30363d}}
.report-item{{padding:8px 10px;cursor:pointer;font-size:12px;border-bottom:1px solid #21262d;border-radius:2px}}
.report-item:hover{{background:#1a1f2e}}
.report-item.active{{background:#161b22;border-left:2px solid #58a6ff}}
.report-item .co{{color:#58a6ff;font-size:11px}}
.report-item .meta{{color:#8b949e;font-size:10px;margin-top:2px}}
.report-item .alert-high{{color:#f85149}}
.report-item .alert-medium{{color:#d29922}}
.panel-center .info{{color:#8b949e;font-size:12px;padding:20px;text-align:center}}

/* Right: analysis */
.panel-right{{flex:1;background:#0d1117;padding:20px}}
.panel-right .empty{{color:#8b949e;text-align:center;padding:60px 20px;font-size:14px}}
.analysis-content{{font-size:13px;line-height:1.7;max-width:800px}}
.analysis-content h2{{font-size:15px;color:#58a6ff;margin:16px 0 8px;border-bottom:1px solid #21262d;padding-bottom:4px}}
.analysis-content h3{{font-size:13px;color:#c9d1d9;margin:12px 0 6px}}
.analysis-content p{{margin:6px 0;color:#c9d1d9}}
.analysis-content ul, .analysis-content ol{{margin:4px 0 4px 20px;color:#c9d1d9}}
.analysis-content li{{margin:2px 0}}
.analysis-content table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:12px}}
.analysis-content th{{background:#161b22;color:#8b949e;text-align:left;padding:4px 8px;border:1px solid #30363d}}
.analysis-content td{{padding:4px 8px;border:1px solid #21262d}}
.analysis-content strong{{color:#e6edf3}}
.report-links{{display:flex;gap:8px;margin:16px 0}}
.report-links a{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:6px 14px;color:#58a6ff;text-decoration:none;font-size:12px}}
.report-links a:hover{{border-color:#58a6ff}}
</style></head>
<body>
<div class="header">
<h1>🏦 IB Analysis</h1>
<a href="/">← Dashboard</a>
</div>
<div class="panels">
<div class="panel panel-left" id="bankList">{bank_list}</div>
<div class="panel panel-center" id="reportList"><div class="info">← 选择投行查看报告</div></div>
<div class="panel panel-right" id="analysisView"><div class="empty">← 选择报告查看分析</div></div>
</div>
<script>
const BANKS = {banks_json};

function selectBank(name) {{
    document.querySelectorAll('.bank-item').forEach(el => el.classList.remove('active'));
    document.querySelector('[data-bank="' + name + '"]').classList.add('active');

    const reports = BANKS[name] || [];
    let html = '<div style="padding:8px 12px;color:#8b949e;font-size:11px;border-bottom:1px solid #30363d">' + reports.length + ' reports</div>';
    reports.forEach((r, i) => {{
        const alertTag = r.alert === 'high' ? ' <span class="alert-high">🔴</span>' : (r.alert === 'medium' ? ' <span class="alert-medium">🟡</span>' : '');
        const tpStr = r.tp_new ? ' TP:' + r.tp_new + (r.tp_currency || '') : '';
        const label = r.topic || r.company || r.name.split('-')[1] || r.name;
        html += '<div class="report-item" onclick="selectReport(' + i + ')" data-idx="' + i + '">'
            + '<div class="co">' + label.substring(0,40) + alertTag + '</div>'
            + '<div class="meta">' + r.date + ' · ' + (r.rating || 'N/A') + tpStr + '</div>'
            + '</div>';
    }});
    document.getElementById('reportList').innerHTML = html;
    document.getElementById('analysisView').innerHTML = '<div class="empty">← 选择报告查看分析</div>';
    window._currentBank = name;
}}

function selectReport(idx) {{
    document.querySelectorAll('#reportList .report-item').forEach(el => el.classList.remove('active'));
    document.querySelector('#reportList [data-idx="' + idx + '"]').classList.add('active');

    const reports = BANKS[window._currentBank] || [];
    const r = reports[idx];
    if (!r) return;

    // Fetch analysis markdown
    fetch('/report/view?path=' + encodeURIComponent(r.md_rel))
        .then(resp => resp.text())
        .then(html => {{
            // Extract body content
            const m = html.match(/<body[^>]*>([\\s\\S]*?)<\\/body>/i);
            const content = m ? m[1] : html;
            let links = '';
            if (r.pdf_rel) links += '<a href="/pdf/' + encodeURIComponent(r.pdf_rel) + '" target="_blank">📑 原始PDF</a>';
            links += '<a href="/report/view?path=' + encodeURIComponent(r.md_rel) + '" target="_blank">📄 完整分析</a>';
            document.getElementById('analysisView').innerHTML = '<div class="report-links">' + links + '</div><div class="analysis-content">' + content + '</div>';
        }})
        .catch(() => {{
            document.getElementById('analysisView').innerHTML = '<div class="empty">加载失败</div>';
        }});
}}

// Auto-select first bank
document.addEventListener('DOMContentLoaded', function() {{
    const first = document.querySelector('.bank-item');
    if (first) selectBank(first.dataset.bank);
}});
</script>
</body></html>"""


# ============ Static File Serving ============

@app.route("/charts/<path:rel_path>")
def serve_chart(rel_path):
    chart_path = REPORT_BASE / rel_path
    if not chart_path.exists():
        return "Not found", 404
    from flask import send_file
    return send_file(str(chart_path), mimetype="image/png")


@app.route("/pdf/<path:rel_path>")
def serve_pdf(rel_path):
    pdf_path = REPORT_BASE / rel_path
    if not pdf_path.exists():
        return "Not found", 404
    from flask import send_file
    return send_file(str(pdf_path), mimetype="application/pdf")



# ============ Settings Page ============

@app.route("/settings", methods=["GET", "POST"])
def view_settings():
    """设置管理页面"""
    msg = ""
    if request.method == "POST":
        action = request.form.get("action", "")
        cfg = _read_config()

        if action == "add-company":
            name = request.form.get("name", "").strip()
            ticker = request.form.get("ticker", "").strip()
            industry = request.form.get("industry", "").strip()
            keywords = [k.strip() for k in request.form.get("keywords", "").split(",") if k.strip()]
            if name:
                existing = next((c for c in cfg.setdefault("tracking", {}).setdefault("companies", []) if c["name"] == name), None)
                if not existing:
                    cfg["tracking"]["companies"].append({
                        "name": name, "ticker": ticker,
                        "keywords": keywords or [name.lower()],
                        "industry": industry, "active": True
                    })
                    _write_config(cfg)
                    msg = f"Added: {name}"

        elif action == "edit-company":
            old_name = request.form.get("old_name", "").strip()
            name = request.form.get("name", "").strip()
            ticker = request.form.get("ticker", "").strip()
            industry = request.form.get("industry", "").strip()
            keywords = [k.strip() for k in request.form.get("keywords", "").split(",") if k.strip()]
            if name and old_name:
                for c in cfg.get("tracking", {}).get("companies", []):
                    if c["name"] == old_name:
                        c["name"] = name
                        if ticker: c["ticker"] = ticker
                        c["industry"] = industry
                        if keywords: c["keywords"] = keywords
                        _write_config(cfg)
                        msg = f"Updated: {name} -> {industry}"
                        break

        elif action == "toggle-company":
            name = request.form.get("name", "")
            for c in cfg.get("tracking", {}).get("companies", []):
                if c["name"] == name:
                    c["active"] = not c.get("active", True)
                    _write_config(cfg)
                    msg = f"{name} -> {'active' if c['active'] else 'paused'}"
                    break

        elif action == "delete-company":
            name = request.form.get("name", "")
            cfg["tracking"]["companies"] = [
                c for c in cfg.get("tracking", {}).get("companies", []) if c["name"] != name
            ]
            _write_config(cfg)
            msg = f"Deleted: {name}"

        elif action == "add-industry":
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip() or name.lower().replace(" ", "-")
            keywords = [k.strip() for k in request.form.get("keywords", "").split(",") if k.strip()]
            if name:
                industries = cfg.setdefault("tracking", {}).setdefault("industries", [])
                existing = next((i for i in industries if i["slug"] == slug), None)
                if not existing:
                    industries.append({"name": name, "slug": slug, "keywords": keywords, "active": True})
                    _write_config(cfg)
                    msg = f"Added industry: {name}"

        elif action == "toggle-industry":
            slug = request.form.get("slug", "")
            for i in cfg.get("tracking", {}).get("industries", []):
                if i["slug"] == slug:
                    i["active"] = not i.get("active", True)
                    _write_config(cfg)
                    msg = f"{i['name']} -> {'active' if i['active'] else 'paused'}"
                    break

        elif action == "delete-industry":
            slug = request.form.get("slug", "")
            cfg["tracking"]["industries"] = [
                i for i in cfg.get("tracking", {}).get("industries", []) if i["slug"] != slug
            ]
            _write_config(cfg)
            msg = f"Deleted: {slug}"

    cfg = _read_config()
    companies = cfg.get("tracking", {}).get("companies", [])
    industries = cfg.get("tracking", {}).get("industries", [])

    co_rows = ""
    for c in sorted(companies, key=lambda x: (not x.get("active", True), x["name"])):
        active = c.get("active", True)
        status = "✅" if active else "⏸️"
        kws = ", ".join(c.get("keywords", [])[:5])
        co_rows += f"""<tr class=\"{'' if active else 'paused'}\">
<td>{status} {c['name']}</td><td>{c.get('ticker', '')}</td><td>{c.get('industry', '')}</td>
<td style=\"font-size:11px\">{kws}</td>
<td style=\"white-space:nowrap\">
  <button class=\"btn-sm\" onclick=\"editCompany('{c['name']}', '{c.get('ticker', '')}', '{c.get('industry', '')}', '{','.join(c.get('keywords', []))}')\">✏️</button>
  <form method=\"POST\" style=\"display:inline\"><input type=\"hidden\" name=\"action\" value=\"toggle-company\"><input type=\"hidden\" name=\"name\" value=\"{c['name']}\"><button class=\"btn-sm\">{'⏸️ Pause' if active else '▶️ Activate'}</button></form>
  <form method=\"POST\" style=\"display:inline\" onsubmit=\"return confirm('Delete {c['name']}?')\"><input type=\"hidden\" name=\"action\" value=\"delete-company\"><input type=\"hidden\" name=\"name\" value=\"{c['name']}\"><button class=\"btn-sm btn-del\">🗑️</button></form>
</td></tr>"""

    ind_rows = ""
    for ind in sorted(industries, key=lambda x: (not x.get("active", True), x["name"])):
        active = ind.get("active", True)
        status = "✅" if active else "⏸️"
        kws = ", ".join(ind.get("keywords", [])[:5])
        ind_rows += f"""<tr>
<td>{status} {ind['name']}</td><td><code>{ind.get('slug', '')}</code></td>
<td style=\"font-size:11px\">{kws}</td>
<td style=\"white-space:nowrap\">
  <form method=\"POST\" style=\"display:inline\"><input type=\"hidden\" name=\"action\" value=\"toggle-industry\"><input type=\"hidden\" name=\"slug\" value=\"{ind['slug']}\"><button class=\"btn-sm\">{'⏸️ Pause' if active else '▶️ Activate'}</button></form>
  <form method=\"POST\" style=\"display:inline\" onsubmit=\"return confirm('Delete {ind['name']}?')\"><input type=\"hidden\" name=\"action\" value=\"delete-industry\"><input type=\"hidden\" name=\"slug\" value=\"{ind['slug']}\"><button class=\"btn-sm btn-del\">🗑️</button></form>
</td></tr>"""

    msg_html = f'<div class="msg">{msg}</div>' if msg else ""
    ind_names = json.dumps([i['name'] for i in industries if i.get('active', True)])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Settings</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}
h2{{font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}}
.date{{color:#8b949e;font-size:12px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
th{{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px}}
td{{padding:8px 10px;border:1px solid #30363d}}
tr:nth-child(even) td{{background:rgba(255,255,255,0.02)}}
tr.paused td{{opacity:0.4}}
a{{color:#58a6ff}}
input,button{{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:5px 10px;font-size:12px;font-family:inherit}}
button{{cursor:pointer;font-weight:600}}
button:hover{{border-color:#58a6ff}}
.btn-sm{{padding:2px 8px;font-size:11px}}
.btn-del{{color:#f85149;border-color:#3d1f1f}}
.add-form{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:8px 0 16px}}
.add-form input{{min-width:80px}}
.msg{{background:#1f3d2f;border:1px solid #3fb950;border-radius:4px;padding:8px 14px;margin:8px 0;font-size:13px;color:#3fb950}}
.modal{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:100;justify-content:center;align-items:center}}
.modal.active{{display:flex}}
.modal-box{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:24px;min-width:400px;max-width:500px}}
.modal-box h3{{margin:0 0 16px;font-size:16px}}
.modal-box input{{width:100%;margin:6px 0 12px;padding:8px 10px;font-size:13px}}
.modal-box select{{width:100%;margin:6px 0 12px;padding:8px 10px;font-size:13px;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#e6edf3;font-family:inherit}}
.modal-box .btn-row{{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}}
</style></head>
<body><div class="wrap">
<h1>⚙️ Settings</h1>
<div class="date"><a href="/">← Dashboard</a> · <a href="/industry/chain" style="display:inline;padding:0;background:none;border:none;margin-left:12px">🔗 产业链矩阵</a></div>
{msg_html}

<h2>➕ Add Company</h2>
<form method="POST" class="add-form">
  <input type="hidden" name="action" value="add-company">
  <input name="name" placeholder="Company name*" required>
  <input name="ticker" placeholder="Ticker (e.g. NVDA.US)" style="max-width:140px">
  <input name="industry" placeholder="Industry" style="max-width:120px">
  <input name="keywords" placeholder="Keywords (comma-sep)" style="min-width:200px">
  <button>Add</button>
</form>

<h2>➕ Add Industry</h2>
<form method="POST" class="add-form">
  <input type="hidden" name="action" value="add-industry">
  <input name="name" placeholder="Industry name*" required>
  <input name="slug" placeholder="slug" style="max-width:140px">
  <input name="keywords" placeholder="Keywords (comma-sep)" style="min-width:300px">
  <button>Add</button>
</form>

<h2>🏢 Companies ({len(companies)})</h2>
<div class="table-wrap"><table>
<tr><th>Name</th><th>Ticker</th><th>Industry</th><th>Keywords</th><th>Actions</th></tr>
{co_rows}
</table></div>

<h2>🏭 Industries ({len(industries)})</h2>
<div class="table-wrap"><table>
<tr><th>Name</th><th>Slug</th><th>Keywords</th><th>Actions</th></tr>
{ind_rows}
</table></div>

<div id="editModal" class="modal">
<div class="modal-box">
<h3>✏️ Edit Company</h3>
<form method="POST" id="editForm">
<input type="hidden" name="action" value="edit-company">
<input type="hidden" name="old_name" id="editOldName">
<label>Name</label><input name="name" id="editName" required>
<label>Ticker</label><input name="ticker" id="editTicker">
<label>Industry</label><select name="industry" id="editIndustry" required></select>
<label>Keywords (comma-sep)</label><input name="keywords" id="editKeywords">
<div class="btn-row">
<button type="button" onclick="document.getElementById('editModal').classList.remove('active')">Cancel</button>
<button type="submit" style="background:#238636;border-color:#238636">Save</button>
</div>
</form>
</div>
</div>
<script>
const INDUSTRY_OPTIONS = {ind_names};
function editCompany(name, ticker, industry, keywords) {{
  document.getElementById('editOldName').value = name;
  document.getElementById('editName').value = name;
  document.getElementById('editTicker').value = ticker;
  var sel = document.getElementById('editIndustry');
  sel.innerHTML = INDUSTRY_OPTIONS.map(function(i) {{ return '<option' + (i === industry ? ' selected' : '') + '>' + i + '</option>'; }}).join('');
  document.getElementById('editKeywords').value = keywords;
  document.getElementById('editModal').classList.add('active');
}}
document.getElementById('editModal').addEventListener('click', function(e) {{ if (e.target === this) this.classList.remove('active'); }});
</script>
</div></body></html>"""


@app.route("/paper")
def view_paper_trading():
    """模拟盘 Dashboard — 持仓/交易/盈亏"""
    import glob as _g
    log_dir = Path.home() / "ClaudeCode" / "QuanTrading" / "data"
    today_str = datetime.now().strftime("%Y%m%d")

    today_trades = []
    today_file = log_dir / f"paper_trades_{today_str}.json"
    if today_file.exists():
        try:
            today_trades = json.loads(today_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    all_trades = []
    for f in sorted(_g.glob(str(log_dir / "paper_trades_*.json"))):
        try:
            all_trades.extend(json.loads(open(f, encoding="utf-8").read()))
        except Exception:
            pass

    buys = [t for t in today_trades if t["direction"] == "buy"]
    sells = [t for t in today_trades if t["direction"] == "sell"]
    total_pnl = sum(t.get("pnl", 0) for t in today_trades)
    wins = [t for t in today_trades if t.get("pnl", 0) > 0]
    win_rate = len(wins)/max(len(sells),1)*100 if sells else 0

    positions = {}
    for t in all_trades:
        sym = t["symbol"]
        if sym not in positions:
            positions[sym] = dict(buys=0, sells=0, cost=0.0, revenue=0.0)
        if t["direction"] == "buy":
            positions[sym]["buys"] += t["quantity"]
            positions[sym]["cost"] += t["quantity"] * t["price"]
        else:
            positions[sym]["sells"] += t["quantity"]
            positions[sym]["revenue"] += t["quantity"] * t["price"]

    portfolio = [
        ("600183.SH", "生益科技", 500, "PCB/CCL"),
        ("002436.SZ", "兴森科技", 1500, "IC Substrate"),
        ("002409.SZ", "雅克科技", 450, "Chip Materials"),
        ("688072.SH", "拓荆科技", 200, "Hybrid Bonding"),
        ("688981.SH", "中芯国际", 600, "Foundry"),
        ("300408.SZ", "三环集团", 1500, "MLCC"),
    ]

    # Get current prices for floating PnL
    current_prices = {}
    try:
        import requests as _req
        for ticker, _, _, _ in portfolio:
            resp = _req.get(
                f"http://124.220.26.164:8766/bars",
                params={"symbol": ticker, "start": today_str, "end": today_str, "interval": "1d"},
                timeout=5
            )
            bars = resp.json().get("bars", [])
            if bars:
                current_prices[ticker] = bars[-1]["close"]
    except Exception:
        pass

    pos_rows = ""
    total_realized = 0.0
    total_floating = 0.0
    for ticker, name, shares, sector in portfolio:
        pos = positions.get(ticker, dict(buys=0, sells=0, cost=0.0, revenue=0.0))
        held = pos["buys"] - pos["sells"]
        avg_cost = pos["cost"]/pos["buys"] if pos["buys"] > 0 else 0

        # Realized PnL: from closed trades
        closed_trades = [t for t in all_trades if t["symbol"] == ticker and t["direction"] == "sell"]
        realized = sum(t.get("pnl", 0) for t in closed_trades)
        total_realized += realized

        # Floating PnL: (current_price - avg_cost) * held
        cur_price = current_prices.get(ticker, avg_cost)
        floating = (cur_price - avg_cost) * held if held > 0 else 0
        total_floating += floating

        fp_str = f"¥{floating:+,.2f}"
        fp_color = "#3fb950" if floating > 0 else "#f85149" if floating < 0 else "#8b949e"

        rp_str = f"¥{realized:+,.2f}" if realized != 0 else "—"
        rp_color = "#3fb950" if realized > 0 else "#f85149" if realized < 0 else "#8b949e"

        cost_str = f"¥{avg_cost:,.2f}"
        cur_str = f"¥{cur_price:,.2f}" if cur_price != avg_cost else "—"

        pos_rows += "<tr><td><b>{}</b><br><span style='color:#656d76;font-size:11px'>{}</span></td><td>{}</td><td>{:,}</td><td>{}</td><td>{}</td><td style='color:{};font-weight:600'>{}</td><td style='color:{};font-weight:600'>{}</td></tr>".format(
            name, ticker, sector, held, cost_str, cur_str, fp_color, fp_str, rp_color, rp_str)

    # Total PnL
    total_pnl = total_realized + total_floating

    trade_rows = ""
    for t in today_trades[-30:]:
        icon = "🟢" if t["direction"] == "buy" else "🔴"
        pnl = t.get("pnl", None)
        pnl_str = '<span style="color:{}">{:+,.2f}</span>'.format("#3fb950" if (pnl or 0) > 0 else "#f85149", pnl) if pnl is not None else "—"
        st = (t.get("strategy","") or "")
        rsn = (t.get("reason","") or "")[:30]
        trade_rows += "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>¥{:.2f}</td><td>{}</td><td style='font-size:11px;color:#656d76'>{} {}</td></tr>".format(icon, (t.get("time","") or "")[11:19], t["symbol"], t["direction"], t["quantity"], t["price"], pnl_str, st, rsn)

    cumulative_pnl = sum(t.get("pnl", 0) for t in all_trades)
    total_color = "#3fb950" if total_pnl > 0 else "#f85149" if total_pnl < 0 else "#8b949e"
    cum_color = "#3fb950" if cumulative_pnl > 0 else "#f85149" if cumulative_pnl < 0 else "#8b949e"

    # ── Eval metrics ──
    import sys as _sys
    _qdir = str(Path.home() / "ClaudeCode" / "QuanTrading")
    if _qdir not in _sys.path:
        _sys.path.insert(0, _qdir)
    try:
        from core.paper_eval import evaluate
        eval_data = evaluate(str(log_dir))
    except Exception:
        eval_data = {"error": "eval unavailable", "readiness": {"score": 0, "checklist": []},
                     "daily": [], "strategies": [], "risk": {}}
    readiness = eval_data.get("readiness", {"score": 0, "checklist": []})
    rscore = readiness.get("score", 0)
    rcolor = "#3fb950" if rscore >= 70 else "#d29922" if rscore >= 40 else "#f85149"
    rlabel = "待评估" if rscore == 0 else ("可上线" if rscore >= 70 else "观望" if rscore >= 40 else "不建议")
    chk_html = ""
    for item in readiness.get("checklist", []):
        icon = "✅" if item["pass"] else "❌"
        chk_html += '<tr><td>{}</td><td style="color:#{}">{}</td><td style="color:#656d76;font-size:12px">{}</td></tr>'.format(
            icon, "3fb950" if item["pass"] else "f85149", item["label"], item["detail"])

    daily_html = ""
    for d in eval_data.get("daily", [])[-10:]:
        dc = "#3fb950" if d["pnl"] > 0 else "#f85149" if d["pnl"] < 0 else "#8b949e"
        daily_html += '<tr><td>{}</td><td>{}</td><td style="color:{}">¥{:+,.0f}</td><td style="color:{}">¥{:+,.0f}</td><td>{:+.1f}%</td><td>{:.1f}%</td><td>{}:{}</td></tr>'.format(
            d["date"][5:], d["trades"], dc, d["pnl"], dc, d["cumulative_pnl"],
            d["return_pct"], d["drawdown_pct"], d["wins"], d["losses"])

    strat_html = ""
    for s in eval_data.get("strategies", []):
        sc = "#3fb950" if s["total_pnl"] > 0 else "#f85149" if s["total_pnl"] < 0 else "#8b949e"
        strat_html += '<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td style="color:{}">¥{:+,.0f}</td><td>{}%</td><td>¥{:,.0f}</td><td>¥{:,.0f}</td><td>{:.1f}x</td></tr>'.format(
            s["name"], s["total_signals"], s["total_trades"], sc, s["total_pnl"],
            s["win_rate"], s["avg_win"], s["avg_loss"], s["win_loss_ratio"])

    risk = eval_data.get("risk", {})
    dd_color = "#3fb950" if abs(risk.get("max_drawdown_pct", 0)) < 2 else "#f85149"
    risk_html = '<div class="grid">'
    risk_html += '<div class="stat"><div class="num" style="color:{}">{:.1f}%</div><div class="lbl">最大回撤</div></div>'.format(
        dd_color, abs(risk.get("max_drawdown_pct", 0)))
    risk_html += '<div class="stat"><div class="num">{}</div><div class="lbl">盈利日 / 亏损日</div></div>'.format(
        "{} / {}".format(risk.get("profitable_days", 0), risk.get("losing_days", 0)))
    risk_html += '<div class="stat"><div class="num">{}</div><div class="lbl">单日止损触发</div></div>'.format(
        risk.get("stop_loss_triggers", 0))
    risk_html += '<div class="stat"><div class="num" style="color:{}">{}</div><div class="lbl">最大连亏天数</div></div>'.format(
        "#f85149" if risk.get("circuit_breaker_triggered") else "#3fb950",
        risk.get("max_consecutive_losing_days", 0))
    risk_html += '</div>'

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>模拟盘 - DayTrader</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:0;padding:20px}}
.wrap{{max-width:1100px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 4px}}
.header-sub{{color:#8b949e;font-size:12px;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:0 0 16px}}
.card-title{{font-size:14px;font-weight:700;color:#58a6ff;margin:0 0 12px;border-bottom:1px solid #30363d;padding-bottom:8px}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}}
.nav a{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 16px;color:#58a6ff;text-decoration:none;font-size:13px}}
.nav a:hover{{border-color:#58a6ff}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#0d1117;color:#8b949e;font-weight:600;text-align:left;padding:10px 12px;border-bottom:1px solid #30363d}}
td{{padding:8px 12px;border-bottom:1px solid #21262d}}
tr:hover td{{background:#1a1f2e}}
.stat{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:16px;text-align:center}}
.stat .num{{font-size:24px;font-weight:800;line-height:1.2}}
.stat .lbl{{font-size:11px;color:#8b949e;margin-top:4px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}}
</style></head>
<body><div class="wrap">
<h1>模拟盘 Paper Trading</h1>
<div class="header-sub">DayTrader | QMT实时数据 | 初始1,000,000 | 自动执行</div>

<div class="nav">
  <a href="/">Dashboard</a>
  <a href="/holdings">持仓</a>
  <a href="/chokepoint">卡脖子</a>
  <a href="/consensus">共识</a>
</div>

<div class="grid">
  <div class="stat"><div class="num" style="color:#58a6ff">{today_trades}</div><div class="lbl">今日交易</div></div>
  <div class="stat"><div class="num" style="color:#3fb950">{total_floating:+,.0f}</div><div class="lbl">浮动盈亏</div></div>
  <div class="stat"><div class="num" style="color:{total_color}">{total_realized:+,.0f}</div><div class="lbl">已实现盈亏</div></div>
  <div class="stat"><div class="num">{all_trades}</div><div class="lbl">累计交易</div></div>
  <div class="stat"><div class="num">{win_rate:.1f}%</div><div class="lbl">胜率</div></div>
  <div class="stat"><div class="num">6</div><div class="lbl">持仓标的</div></div>
</div>

<div class="card">
  <div class="card-title">持仓概览</div>
  <table>
    <thead><tr><th>标的</th><th>赛道</th><th>持仓</th><th>成本</th><th>现价</th><th>浮动盈亏</th><th>已实现</th></tr></thead>
    <tbody>{pos_rows}</tbody>
  </table>
</div>

<div class="card">
  <div class="card-title">今日交易记录 ({today_trades_len})</div>
  {trade_table}
</div>

<div class="card">
  <div class="card-title">📊 实盘准入评估</div>
  <div class="grid">
    <div class="stat"><div class="num" style="font-size:36px;color:{rcolor}">{rscore}</div><div class="lbl">准入评分 / 100</div></div>
    <div class="stat"><div class="num" style="font-size:36px;color:{rcolor}">{rlabel}</div><div class="lbl">当前判定</div></div>
  </div>
  <table style="margin-top:12px">
    <thead><tr><th></th><th>指标</th><th>详情</th></tr></thead>
    <tbody>{chk_html}</tbody>
  </table>
  <div style="color:#656d76;font-size:11px;margin-top:8px">
    评分规则: 累计盈利(25) + 日胜率(15) + 最大回撤(15) + 无熔断(15) + 无止损(15) + 策略活跃(15)<br>
    ≥70 可上线 · 40-69 观望 · &lt;40 不建议
  </div>
</div>

<div class="card">
  <div class="card-title">每日收益 ({total_days} 交易日)</div>
  {daily_table}
</div>

<div class="card">
  <div class="card-title">策略表现</div>
  {strat_table}
</div>

<div class="card">
  <div class="card-title">风险评估</div>
  {risk_html}
</div>

<div style="text-align:center;color:#656d76;font-size:11px;margin-top:16px">
  Data: {today_str} | <a href="/paper">Refresh</a>
</div>
</div></body></html>""".format(
        today_trades=len(today_trades),
        total_floating=total_floating,
        total_realized=total_realized,
        total_color=total_color,
        total_pnl=total_pnl,
        win_rate=win_rate,
        all_trades=len(all_trades),
        pos_rows=pos_rows,
        today_trades_len=len(today_trades),
        trade_table='<table><thead><tr><th></th><th>时间</th><th>标的</th><th>方向</th><th>数量</th><th>价格</th><th>盈亏</th><th>备注</th></tr></thead><tbody>{}</tbody></table>'.format(trade_rows) if today_trades else '<p style="color:#656d76">暂无交易记录 — 周一开盘后自动运行</p>',
        today_str=today_str,
        rscore=rscore,
        rcolor=rcolor,
        rlabel=rlabel,
        chk_html=chk_html,
        total_days=eval_data.get("total_days", 0),
        daily_table='<table><thead><tr><th>日期</th><th>交易</th><th>P&L</th><th>累计</th><th>收益%</th><th>回撤%</th><th>W:L</th></tr></thead><tbody>{}</tbody></table>'.format(daily_html) if daily_html else '<p style="color:#656d76">暂无日收益数据</p>',
        strat_table='<table><thead><tr><th>策略</th><th>信号</th><th>交易</th><th>P&L</th><th>胜率</th><th>均盈</th><th>均亏</th><th>盈亏比</th></tr></thead><tbody>{}</tbody></table>'.format(strat_html) if strat_html else '<p style="color:#656d76">暂无策略数据</p>',
        risk_html=risk_html,
    )
    return html

@app.route("/investment-themes")
def view_investment_themes():
    """未来投资方向 — 孙宇晨四大赛道：人形机器人 / 低空经济 / 空间计算 / 太空探索"""
    themes_path = PROJECT_DIR / "investment_themes.json"
    data = json.loads(themes_path.read_text(encoding="utf-8"))
    all_themes = data["themes"]

    # Determine active theme
    active_id = request.args.get("t", all_themes[0]["id"])

    # Build tab bar
    tabs_html = ""
    for t in all_themes:
        active_class = "tab-active" if t["id"] == active_id else ""
        tabs_html += f"""<a href="/investment-themes?t={t['id']}" class="tab-btn {active_class}">{t['icon']} {t['name']}</a>"""

    # Render all themes as hidden panels, show active one
    all_panels_html = ""
    for theme in all_themes:
        display = "" if theme["id"] == active_id else "display:none"

        # Build category cards
        categories_html = ""
        for cat in theme["categories"]:
            overseas_rows = ""
            for s in cat["overseas"]:
                overseas_rows += f"""<tr>
                    <td class="rank">#{s['rank']}</td>
                    <td><b>{s['name']}</b><div class="ticker">{s['ticker']}</div></td>
                    <td class="note">{s['note']}</td>
                </tr>"""
            china_rows = ""
            for s in cat["china"]:
                china_rows += f"""<tr>
                    <td class="rank">#{s['rank']}</td>
                    <td><b>{s['name']}</b><div class="ticker">{s['ticker']}</div></td>
                    <td class="note">{s['note']}</td>
                </tr>"""
            tech_tags = "".join(f"<span class=\"tech-tag\">{t}</span>" for t in cat["key_tech"])
            categories_html += f"""<div class="card">
                <div class="card-title">{cat['icon']} {cat['name']} <span class="value-note">{cat['value_note']}</span></div>
                <div class="tech-row">{tech_tags}</div>
                <div class="supplier-grid">
                    <div class="supplier-col">
                        <div class="col-title">🌍 海外 Top 3</div>
                        <table class="supplier-table">{overseas_rows}</table>
                    </div>
                    <div class="supplier-col">
                        <div class="col-title">🇨🇳 中国 Top 3</div>
                        <table class="supplier-table">{china_rows}</table>
                    </div>
                </div>
            </div>"""

        milestones_html = ""
        for m in theme["overview"]["milestones"]:
            milestones_html += f"""<div class="milestone">
                <div class="ms-year">{m['year']}</div>
                <div class="ms-event">{m['event']}</div>
            </div>"""

        notes_html = "".join(f"<div class=\"note-item\">{n}</div>" for n in theme["notes"])

        all_panels_html += f"""<div class="theme-panel" id="panel-{theme['id']}" style="{display}">
            <h1>{theme['icon']} {theme['name']} <span class="time-badge">{theme['time_span']}</span></h1>
            <div class="subtitle">{theme['subtitle']}</div>

            <div class="overview-box">
                <div class="overview-text">{theme['overview']['logic']}</div>
                <div class="timeline">{milestones_html}</div>
            </div>

            {categories_html}

            <div class="notes-section">
                <div class="notes-title">⚠️ 风险提示与关注主线</div>
                {notes_html}
            </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>未来投资方向 — 孙宇晨四大赛道</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Helvetica Neue','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 4px;color:#f0f6fc}}
.subtitle{{color:#8b949e;font-size:14px;margin-bottom:4px}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}

.tab-bar{{display:flex;gap:0;flex-wrap:wrap;margin:0 0 20px 0;border-bottom:2px solid #30363d}}
.tab-btn{{display:inline-block;padding:10px 18px;font-size:14px;color:#8b949e;background:none;border:none;border-bottom:2px solid transparent;margin-bottom:-2px;cursor:pointer;transition:all .2s}}
.tab-btn:hover{{color:#e6edf3;border-bottom-color:#484f58}}
.tab-active{{color:#58a6ff!important;border-bottom-color:#58a6ff!important;font-weight:600}}
.time-badge{{font-size:13px;color:#8b949e;font-weight:400;margin-left:8px}}

.header-sub{{color:#8b949e;font-size:12px;margin-bottom:16px}}
.header-sub a{{font-size:12px}}

.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:16px 0}}
.card-title{{font-size:15px;font-weight:700;color:#58a6ff;margin-bottom:12px}}
.value-note{{font-size:11px;color:#8b949e;font-weight:400;margin-left:8px}}

.overview-box{{background:linear-gradient(135deg,#161b22 0%,#1a2332 100%);border:1px solid #30363d;border-radius:8px;padding:20px;margin:16px 0}}
.overview-text{{font-size:14px;color:#c9d1d9;line-height:1.7;margin-bottom:16px}}
.timeline{{display:flex;gap:12px;flex-wrap:wrap}}
.milestone{{flex:1;min-width:150px;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:12px}}
.ms-year{{font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:4px}}
.ms-event{{font-size:12px;color:#8b949e;line-height:1.5}}

.tech-row{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}}
.tech-tag{{display:inline-block;padding:4px 12px;background:rgba(88,166,255,0.1);border:1px solid rgba(88,166,255,0.25);border-radius:4px;font-size:12px;color:#a5d8ff}}

.supplier-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.supplier-col{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:14px}}
.col-title{{font-size:13px;font-weight:700;color:#e6edf3;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid #30363d}}
.supplier-table{{width:100%;border-collapse:collapse}}
.supplier-table td{{padding:8px 10px;font-size:13px;border-bottom:1px solid #21262d;vertical-align:top}}
.supplier-table tr:last-child td{{border-bottom:none}}
.supplier-table .rank{{color:#58a6ff;font-weight:700;width:30px;font-size:14px}}
.supplier-table .ticker{{font-size:11px;color:#8b949e;margin-top:2px}}
.supplier-table .note{{font-size:12px;color:#8b949e;line-height:1.4}}

.notes-section{{background:#161b22;border:1px solid #f0883e50;border-radius:8px;padding:16px;margin:16px 0}}
.notes-title{{font-size:14px;font-weight:700;color:#f0883e;margin-bottom:8px}}
.note-item{{font-size:12px;color:#8b949e;padding:3px 0;line-height:1.5}}

@media (max-width:768px){{.supplier-grid{{grid-template-columns:1fr}}.timeline{{flex-direction:column}}.tab-btn{{font-size:12px;padding:8px 12px}}}}
</style></head>
<body><div class="wrap">
<div class="header-sub"><a href="/">← Dashboard</a> · 更新于 {data['last_updated']} · 孙宇晨2026四大投资赛道</div>
<div class="tab-bar">{tabs_html}</div>
{all_panels_html}
<div style="text-align:center;padding:24px;color:#484f58;font-size:12px">
    <a href="/">← 返回 Dashboard</a><br>
    数据仅供参考，不构成投资建议
</div>
</div></body></html>"""


# ============ Main ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Dashboard Server")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\n  ⚡ Hermes Dashboard")
    print(f"  http://{args.host}:{args.port}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=args.host, port=args.port, debug=args.debug)
