#!/usr/bin/env python3
"""
Report Renderer — Markdown → Professional HTML Report

将 LLM 分析输出渲染为投行风格的 HTML 报告。
两种主题: light (白天/投行风) / dark (暗色终端风)

用法：
  python3 report_renderer.py <analysis.md>                    # 渲染单份报告
  python3 report_renderer.py --company MediaTek                # 按公司汇总
  python3 report_renderer.py --industry memory                 # 行业报告
  python3 report_renderer.py --topic "HBM4 progress"           # Ad-hoc 主题
"""

import os
import re
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from utils import extract_bank_from_filename

PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
DB_PATH = PROJECT_DIR / "industry_metrics.db"
OUTPUT_DIR = Path.home() / ".hermes" / "reports"

# ============ CSS Themes ============

CSS_LIGHT = """
body{background:#f8fafc;color:#1e293b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px}
h1{font-size:28px;font-weight:800;margin:0 0 4px;background:linear-gradient(135deg,#2563eb,#7c3aed);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.date{color:#64748b;font-size:14px;margin:0 0 32px}
h2{font-size:20px;font-weight:700;margin:32px 0 16px;color:#0f172a;padding-bottom:8px;border-bottom:2px solid #e2e8f0}
h3{font-size:16px;font-weight:600;margin:20px 0 10px;color:#1e40af}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
th{background:#f1f5f9;padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #cbd5e1;white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid #e2e8f0}
tr:hover td{background:#f8fafc}
.up{color:#16a34a;font-weight:600}
.down{color:#dc2626;font-weight:600}
.neutral{color:#64748b}
.tag{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin:0 4px}
.tag-buy{background:#dcfce7;color:#166534}
.tag-sell{background:#fef2f2;color:#991b1b}
.tag-neutral{background:#fef9c3;color:#854d0e}
.card{background:#fff;border-radius:12px;padding:24px;margin:0 0 20px;box-shadow:0 1px 3px rgba(0,0,0,.06);border:1px solid #e2e8f0}
.card-title{font-size:15px;font-weight:700;color:#1e40af;margin:0 0 12px}
.row{display:flex;gap:20px;flex-wrap:wrap;margin:0 0 16px}
.col{flex:1;min-width:200px}
.metric{text-align:center;padding:16px}
.metric .val{font-size:32px;font-weight:800}
.metric .label{font-size:12px;color:#64748b;margin-top:4px}
.consensus{background:#f0fdf4;border-left:4px solid #16a34a;padding:16px 20px;margin:0 0 12px;border-radius:0 8px 8px 0}
.dispute{background:#fef7ed;border-left:4px solid #f59e0b;padding:16px 20px;margin:0 0 12px;border-radius:0 8px 8px 0}
.risk{background:#fef2f2;border-left:4px solid #dc2626;padding:16px 20px;margin:0 0 12px;border-radius:0 8px 8px 0}
.bull-bear{display:flex;gap:16px;flex-wrap:wrap}
.bull{flex:1;min-width:280px;background:#f0fdf4;border-radius:8px;padding:16px}
.bear{flex:1;min-width:280px;background:#fef7ed;border-radius:8px;padding:16px}
.bull h4{color:#166534;font-size:14px;margin:0 0 8px}
.bear h4{color:#92400e;font-size:14px;margin:0 0 8px}
.bar-container{margin:4px 0;height:8px;background:#e2e8f0;border-radius:4px;overflow:hidden}
.bar{height:100%;border-radius:4px}
.conf{color:#dc2626;font-weight:700;font-size:11px;letter-spacing:1px}
.footer{text-align:center;color:#94a3b8;font-size:12px;margin:40px 0 0;padding-top:20px;border-top:1px solid #e2e8f0}
blockquote{border-left:3px solid #3b82f6;margin:12px 0;padding:8px 16px;background:#f8fafc;border-radius:0 6px 6px 0;font-style:italic}
.chain{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:12px;line-height:1.8;margin:10px 0;white-space:pre-wrap;overflow-x:auto}
.cal{display:flex;gap:10px;padding:4px 0;font-size:13px}
.cal-date{color:#2563eb;font-weight:600;min-width:56px}
.src{font-size:10px;color:#94a3b8}
"""

CSS_DARK = """
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;margin:0;padding:0}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:22px;font-weight:700;margin:0 0 4px;color:#e6edf3}
.date{color:#8b949e;font-size:12px;margin-bottom:24px}
h2{font-size:15px;font-weight:600;color:#e6edf3;border-bottom:1px solid #30363d;padding-bottom:6px;margin:24px 0 12px}
h3{font-size:13px;font-weight:600;color:#58a6ff;margin:16px 0 6px}
.table-wrap{overflow-x:auto;margin:8px 0 16px;-webkit-overflow-scrolling:touch}
.table-wrap table{min-width:600px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th{background:#161b22;color:#8b949e;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #30363d;font-size:11px;text-transform:uppercase;position:sticky;top:0;z-index:1}
td{padding:8px 10px;border:1px solid #30363d;vertical-align:top}
tr:nth-child(even) td{background:rgba(255,255,255,0.02)}
.up{color:#3fb950;font-weight:600}
.down{color:#f85149;font-weight:600}
.neutral{color:#8b949e}
.tag{display:inline-block;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600;color:#58a6ff;margin-right:4px}
.tag-buy{background:#1f3d2f;color:#3fb950}
.tag-sell{background:#3d1f1f;color:#f85149}
.tag-neutral{background:#3d2f0f;color:#d29922}
.tag-emerging{background:#1f2f3d;color:#58a6ff}
.tag-stable{background:#1f3d2f;color:#3fb950}
.tag-diverging{background:#3d2f0f;color:#d29922}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:0 0 16px}
.card-title{font-size:14px;font-weight:700;color:#58a6ff;margin:0 0 10px}
.row{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 16px}
.col{flex:1;min-width:180px}
.metric{text-align:center;padding:12px}
.metric .val{font-size:28px;font-weight:800}
.metric .label{font-size:11px;color:#8b949e;margin-top:4px}
.consensus{background:#1f3d2f;border-left:4px solid #3fb950;padding:12px 16px;margin:0 0 10px;border-radius:0 6px 6px 0;font-size:13px}
.dispute{background:#3d2f0f;border-left:4px solid #d29922;padding:12px 16px;margin:0 0 10px;border-radius:0 6px 6px 0;font-size:13px}
.risk{background:#3d1f1f;border-left:4px solid #f85149;padding:12px 16px;margin:0 0 10px;border-radius:0 6px 6px 0;font-size:13px}
.bull-bear{display:flex;gap:16px;flex-wrap:wrap}
.bull{flex:1;min-width:280px;background:#1f3d2f;border-radius:8px;padding:16px}
.bear{flex:1;min-width:280px;background:#3d2f0f;border-radius:8px;padding:16px}
.bar-container{margin:4px 0;height:8px;background:#30363d;border-radius:4px;overflow:hidden}
.bar{height:100%;border-radius:4px}
.conf{color:#f85149;font-weight:700;font-size:11px;letter-spacing:1px}
.footer{margin-top:32px;padding-top:16px;border-top:1px solid #30363d;color:#8b949e;font-size:11px;text-align:center}
blockquote{border-left:3px solid #58a6ff;margin:12px 0;padding:8px 16px;background:rgba(88,166,255,0.05);border-radius:0 6px 6px 0;font-style:italic}
.chain{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:12px;line-height:1.8;margin:10px 0;white-space:pre-wrap;overflow-x:auto}
.cal{display:flex;gap:10px;padding:4px 0;font-size:13px}
.cal-date{color:#58a6ff;font-weight:600;min-width:56px}
.src{font-size:10px;color:#8b949e}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.summary-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-align:center}
.summary-card .num{font-size:32px;font-weight:800;line-height:1.2}
.summary-card .lbl{font-size:11px;color:#8b949e;margin-top:4px}
"""


def markdown_to_html(md: str) -> str:
    """Simple markdown → HTML for report content"""
    html = md
    # Bold **text**
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    # Headers
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    # Tables (basic: | cell | cell |)
    html = re.sub(r'^\|(.+)\|$', lambda m: '<tr>' + ''.join(
        f'<td>{c.strip()}</td>' for c in m.group(1).split('|') if c.strip()
    ) + '</tr>', html, flags=re.MULTILINE)
    html = re.sub(r'(<tr>.*?</tr>\n?)+', r'<table>\g<0></table>', html)
    # Separator rows (|---|---|)
    html = re.sub(r'<tr><td>-+</td>(<td>-+</td>)*</tr>', '', html)
    # Blockquotes
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)
    # Lines
    html = re.sub(r'^---$', '<hr>', html, flags=re.MULTILINE)
    # Paragraphs
    paragraphs = html.split('\n\n')
    result = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith('<') and not p.startswith('<strong'):
            result.append(p)
        else:
            result.append(f'<p>{p.replace(chr(10), "<br>")}</p>')
    return '\n'.join(result)


def render_company_report(company: str, analyses: list[dict], theme: str = "light") -> str:
    """为某公司渲染多报告共识 HTML"""
    css = CSS_LIGHT if theme == "light" else CSS_DARK
    today = datetime.now().strftime("%Y-%m-%d")

    # Sort by date
    analyses.sort(key=lambda a: a.get("report_date", ""), reverse=True)
    banks = list(set(a.get("bank", "?") for a in analyses))
    tps = [a.get("tp_new") for a in analyses if a.get("tp_new")]
    ratings = [a.get("rating") for a in analyses if a.get("rating")]

    buy_count = sum(1 for r in ratings if r.lower() in ("buy", "outperform", "overweight"))
    neutral_count = sum(1 for r in ratings if r.lower() in ("neutral", "hold", "equal-weight"))
    median_tp = sorted(tps)[len(tps)//2] if tps else 0

    rows_html = ""
    for a in analyses:
        date = a.get("report_date", "")[-5:]
        rating = a.get("rating", "?")
        tp_new = f"{a.get('tp_new', 0):,.0f}" if a.get("tp_new") else "—"
        tp_old = f"{a.get('tp_old', 0):,.0f}" if a.get("tp_old") else "—"
        currency = a.get("tp_currency", "")
        bank = a.get("bank", "?")
        tag_cls = "tag-buy" if rating.lower() in ("buy", "outperform", "overweight") else ("tag-sell" if rating.lower() in ("sell", "underperform") else "tag-neutral")
        rows_html += f"<tr><td><b>{bank}</b></td><td>{date}</td><td><span class=\"tag {tag_cls}\">{rating}</span></td><td>{tp_new}</td><td>{tp_old}</td><td>{currency}</td></tr>\n"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company} — IB Consensus {today}</title>
<style>{css}</style></head>
<body><div class="wrap">
<p class="conf">CONFIDENTIAL</p>
<h1>{company} — 投行共识分析</h1>
<p class="date">{len(analyses)}份投行研报 · 覆盖 {', '.join(banks[:6])} · 分析日期: {today}</p>

<div class="row">
  <div class="col"><div class="card metric"><div class="val up">{buy_count}/{len(ratings)}</div><div class="label">投行看多</div></div></div>
  <div class="col"><div class="card metric"><div class="val" style="color:#7c3aed">{median_tp:,.0f}</div><div class="label">中位目标价</div></div></div>
</div>

<h2>一、投行评级与目标价</h2>
<table>
<tr><th>投行</th><th>日期</th><th>评级</th><th>目标价</th><th>此前TP</th><th>货币</th></tr>
{rows_html}
</table>

<div class="footer">
<p>数据来源: {', '.join(banks[:8])}</p>
<p>分析日期: {today} · Hermes AI Research · 仅供参考，不构成投资建议</p>
<p class="conf">CONFIDENTIAL</p>
</div>
</div></body></html>"""


def render_industry_report(topic: str, markdown: str, sources: int, theme: str = "dark") -> str:
    """渲染行业主题报告"""
    css = CSS_DARK if theme == "dark" else CSS_LIGHT
    today = datetime.now().strftime("%Y-%m-%d")
    body = markdown_to_html(markdown)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{topic} — {today}</title>
<style>{css}</style></head>
<body><div class="wrap">
<h1>{topic}</h1>
<p class="date">{today} | {sources} sources | Confidential</p>
{body}
<div class="footer">
<p>Hermes AI Research · Generated {today}</p>
<p>本报告仅供投资参考，不构成投资建议。</p>
</div>
</div></body></html>"""


def render_logic_chain_report(company: str, theme: str = "dark") -> str:
    """渲染逻辑链溯源报告为 HTML"""
    css = CSS_DARK if theme == "dark" else CSS_LIGHT
    today = datetime.now().strftime("%Y-%m-%d")

    from logic_aggregator import aggregate, format_aggregated_markdown
    from logic_store import getAllLogicChainsForCompany

    drivers = aggregate(company)
    chains = getAllLogicChainsForCompany(company)

    if not drivers:
        return f"<h1>No logic chains found for {company}</h1>"

    # Build driver sections
    sections = ""
    for i, ad in enumerate(drivers):
        consensus_badge = {"full": "Full Consensus (≥4)", "strong": "Strong (3)",
                           "partial": "Partial (2)", "isolated": "Isolated (1)"}

        # Evidence matrix table
        ev_rows = ""
        banks_set = sorted(set(b for row in ad.evidence_matrix for b in row if b != "metric"))
        if ad.evidence_matrix:
            header = "<tr><th>Metric</th>" + "".join(f"<th>{b}</th>" for b in banks_set) + "</tr>"
            ev_rows += header
            for row in ad.evidence_matrix[:10]:
                cells = f"<td>{row.get('metric', '')}</td>"
                for b in banks_set:
                    v = row.get(b, "—")
                    cells += f"<td>{v}</td>"
                ev_rows += f"<tr>{cells}</tr>"

        # Impact graph
        impact_items = ""
        for imp in ad.impact_graph:
            banks_str = f"<span class='src'>({', '.join(imp.get('banks', []))})</span>" if imp.get('banks') else ""
            impact_items += f"<li><b>{imp['entity']}</b> [{imp['role']}] → {imp['effect']} {banks_str}</li>"

        # Disputes
        disputes_html = ""
        if ad.disputes:
            for d in ad.disputes:
                disputes_html += f"""
<div class="dispute">
  <b>{d['topic']}</b><br>
  🐂 <span class="up">{d['bull']}</span><br>
  🐻 <span class="down">{d['bear']}</span>
</div>"""

        sections += f"""
<div class="card">
  <div class="card-title">{i}. {ad.canonical}</div>
  <p class="date">{consensus_badge.get(ad.consensus_level, '?')} | {', '.join(ad.banks[:8])} | {ad.report_count} reports | {ad.direction}</p>

  <h2>证据矩阵</h2>
  <div class="table-wrap"><table>{ev_rows}</table></div>

  <h2>产业链传导</h2>
  <ul>{impact_items}</ul>
  {f'<h2>与前期变化</h2><p>{ad.change_consensus}</p>' if ad.change_consensus else ''}
  {disputes_html}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company} — Logic Chain Trace</title>
<style>{css}</style></head>
<body><div class="wrap">
<p class="conf">CONFIDENTIAL</p>
<h1>{company} — 逻辑链溯源报告</h1>
<p class="date">{today} | {len(drivers)} drivers | {len(chains)} logic chains | Hermes AI Research</p>
{sections}
<div class="footer">
<p>数据来源: 卖方研报逻辑链提取 + 跨报告聚合</p>
<p>本报告仅供投资参考，不构成投资建议。</p>
<p class="conf">CONFIDENTIAL</p>
</div>
</div></body></html>"""


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report Renderer")
    parser.add_argument("path", nargs="?", help="Path to _analysis.md file")
    parser.add_argument("--company", help="Company name for consensus report")
    parser.add_argument("--industry", help="Industry slug for industry report")
    parser.add_argument("--topic", help="Custom topic")
    parser.add_argument("--type", choices=["report", "logic-chain"], default="report",
                        help="Report type: report (default) or logic-chain")
    parser.add_argument("--theme", choices=["light", "dark"], default="dark")
    parser.add_argument("--output", help="Output HTML path")

    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.path:
        md_text = Path(args.path).read_text(encoding="utf-8")
        html = render_industry_report(
            topic=Path(args.path).stem[:60],
            markdown=md_text,
            sources=1,
            theme=args.theme
        )
        out = args.output or str(OUTPUT_DIR / f"{Path(args.path).stem}.html")
        Path(out).write_text(html, encoding="utf-8")
        print(f"✅ {out}")

    elif args.company and args.type == "logic-chain":
        html = render_logic_chain_report(args.company, args.theme)
        safe_name = args.company.replace(" ", "_")
        out = args.output or str(OUTPUT_DIR / f"{safe_name}_logic_chain.html")
        Path(out).write_text(html, encoding="utf-8")
        print(f"✅ {out} (logic chain trace)")

    elif args.company:
        # Load analyses for company
        from run_pipeline import normalize_company
        analyses = []
        for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                p = d.get("parsed", {})
                if normalize_company(p.get("company", "")) == normalize_company(args.company):
                    pdf_name = d.get("pdf_name", "")
                    bank = extract_bank_from_filename(pdf_name)
                    date_m = re.search(r'(\d{6})', pdf_name)
                    report_date = f"20{date_m.group(1)[:2]}-{date_m.group(1)[2:4]}-{date_m.group(1)[4:6]}" if date_m else ""
                    tp = p.get("target_price") or {}
                    analyses.append({
                        "bank": bank,
                        "report_date": report_date,
                        "rating": p.get("rating", ""),
                        "tp_new": tp.get("new"),
                        "tp_old": tp.get("old"),
                        "tp_currency": tp.get("currency", ""),
                    })
            except Exception:
                continue

        html = render_company_report(args.company, analyses, args.theme)
        out = args.output or str(OUTPUT_DIR / f"{args.company.replace(' ','_')}_consensus.html")
        Path(out).write_text(html, encoding="utf-8")
        print(f"✅ {out} ({len(analyses)} reports)")

    elif args.industry or args.topic:
        # Generate via LLM + render
        topic = args.industry or args.topic
        # Call LLM via same logic as api_generate_report
        from llm_client import call_llm
        all_excerpts = []
        for f in sorted(REPORT_BASE.rglob("*_analysis.md")):
            text = f.read_text(encoding="utf-8")
            if topic.lower() in text.lower() or any(kw.lower() in text.lower() for kw in topic.split() if len(kw) > 3):
                all_excerpts.append({"source": f.stem[:50], "text": text[:2000]})
        all_excerpts = all_excerpts[:8]

        if len(all_excerpts) < 2:
            print(f"Only {len(all_excerpts)} sources found. Need >= 2.")
            exit(1)

        report_texts = "\n\n---\n\n".join(f"### {e['source']}\n{e['text']}" for e in all_excerpts)
        system_prompt = f"""You are a senior semiconductor analyst. Write a professional research report on "{topic}".
Use table-first format. Every data point must cite source bank and date.
Structure: Executive Summary → Data Tables → Consensus/Dispute → Bull/Bear → Risk Matrix → Source Index.
Output in markdown with tables."""
        markdown, _ = call_llm(system_prompt, report_texts, max_tokens=4096)

        html = render_industry_report(topic, markdown, len(all_excerpts), args.theme)
        slug = topic.lower().replace(" ", "_")[:40]
        out = args.output or str(OUTPUT_DIR / f"{slug}_{datetime.now().strftime('%Y%m%d')}.html")
        Path(out).write_text(html, encoding="utf-8")
        print(f"✅ {out} ({len(all_excerpts)} sources)")

    else:
        parser.print_help()
