#!/usr/bin/env python3
"""
Batch Backtest - 跨目录收集所有分析结果，生成汇总仪表盘

用法：
  python3 backtest.py                          # 汇总所有已有分析
  python3 backtest.py --run-all                # 分析所有未分析 PDF + 汇总
  python3 backtest.py --scan-all               # 仅扫描所有 PDF (快速识别公司)
"""

import re
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
PROJECT_DIR = Path(__file__).parent


def find_all_pdfs() -> list[Path]:
    """找出所有 PDF（跳过分析输出）"""
    pdfs = []
    for pdf in REPORT_BASE.rglob("*.pdf"):
        if pdf.stem.endswith("_analysis"):
            continue
        pdfs.append(pdf)
    return sorted(pdfs)


def find_all_analyses() -> list[dict]:
    """收集所有 _analysis.json"""
    results = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_json_path"] = str(f)
            data["_md_path"] = str(f.with_suffix(".md"))
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def find_all_consensus() -> list[dict]:
    """收集所有 CONSENSUS_*.json/md"""
    results = []
    for f in sorted(REPORT_BASE.rglob("CONSENSUS_*.json")):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            continue
    for f in sorted(REPORT_BASE.rglob("CONSENSUS_*.md")):
        if not f.with_suffix(".json").exists():
            results.append({
                "company": f.stem.replace("CONSENSUS_", "").rsplit("_", 1)[0],
                "markdown": f.read_text(encoding="utf-8"),
                "_md_path": str(f)
            })
    return results


def extract_bank(pdf_name: str) -> str:
    """从 PDF 文件名提取银行名"""
    m = re.match(r'^([A-Za-z\s&.]+?)[-（(]', pdf_name)
    return m.group(1).strip() if m else "Unknown"


def normalize_bank(name: str) -> str:
    """归一化银行名：GS→Goldman Sachs, MS→Morgan Stanley"""
    aliases = {
        "gs": "Goldman Sachs",
        "ms": "Morgan Stanley",
        "jpm": "J.P. Morgan",
        "bofa": "BofA Securities",
        "bofa securities": "BofA Securities",
        "citi": "Citi",
        "citi group": "Citi",
    }
    return aliases.get(name.lower(), name)


def extract_summary_from_consensus(markdown: str) -> dict:
    """从共识 markdown 提取关键指标"""
    info = {}
    # Rating consensus
    m = re.search(r'Rating\s*Consensus[:\s]*\*?\*?(.*?)\*?\*?\s*\(', markdown, re.IGNORECASE)
    if m:
        info["rating_consensus"] = m.group(1).strip()
    # Direction
    m = re.search(r'Consensus\s*Direction[:\s]*\*?\*?(Bullish|Neutral|Bearish)', markdown, re.IGNORECASE)
    if m:
        info["direction"] = m.group(1)
    # Signal
    m = re.search(r'Signal\s*Strength[:\s]*\*?\*?(Strong|Moderate|Weak)', markdown, re.IGNORECASE)
    if m:
        info["signal"] = m.group(1)
    # Upside
    m = re.search(r'(\d+\.?\d*)\s*%\s*upside', markdown, re.IGNORECASE)
    if m:
        info["upside_pct"] = float(m.group(1))
    # TP range
    m = re.search(r'Target\s*Price\s*Range[:\s]*.*?NT\$\s*([\d,]+)\s*[-–—]\s*NT\$\s*([\d,]+)', markdown)
    if m:
        info["tp_low"] = float(m.group(1).replace(",", ""))
        info["tp_high"] = float(m.group(2).replace(",", ""))
    return info


def dashboard(analyses: list[dict], consensus_list: list[dict]):
    """生成回测仪表盘"""
    print(f"\n{'=' * 72}")
    print(f"  📊 BACKTEST DASHBOARD  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 72}")

    # 基本信息
    pdf_total = len(find_all_pdfs())
    print(f"\n  Reports: {pdf_total} PDFs | {len(analyses)} analyzed | "
          f"{len(consensus_list)} consensus")

    if not analyses:
        print("\n  No analyses found. Run pipeline first.")
        return

    # ---- Per-company breakdown ----
    from run_pipeline import normalize_company

    by_company: dict[str, list[dict]] = defaultdict(list)
    for a in analyses:
        parsed = a.get("parsed", {})
        company = parsed.get("company", "") or "Unknown"
        canonical = normalize_company(company)
        by_company[canonical].append(a)

    print(f"\n{'─' * 72}")
    print(f"  COVERAGE MAP")
    print(f"{'─' * 72}")
    print(f"  {'Company':<20} {'Reports':>8} {'TP Range':>22} {'Consensus':>14}")
    print(f"  {'─' * 20} {'─' * 8} {'─' * 22} {'─' * 14}")

    for company, items in sorted(by_company.items(), key=lambda x: len(x[1]), reverse=True):
        # 找对应共识
        consensus = next((c for c in consensus_list if c.get("company") == company), None)
        cs_info = {}
        if consensus and consensus.get("markdown"):
            cs_info = extract_summary_from_consensus(consensus["markdown"])

        # 收集 TP 数据
        tps = []
        banks = []
        for item in items:
            parsed = item.get("parsed", {})
            tp = parsed.get("target_price") or {}
            if tp.get("new"):
                tps.append(tp["new"])
            pdf_name = item.get("pdf_name", "")
            bank = normalize_bank(extract_bank(pdf_name))
            banks.append(bank)

        tp_range_str = ""
        if tps:
            tp_range_str = f"{min(tps):,.0f} – {max(tps):,.0f}"

        consensus_str = cs_info.get("direction", "")
        if cs_info.get("signal"):
            consensus_str += f" ({cs_info['signal']})"

        print(f"  {company:<20} {len(items):>8} {tp_range_str:>22} {consensus_str:>14}")

    # ---- Bank coverage ----
    print(f"\n{'─' * 72}")
    print(f"  BANK COVERAGE")
    print(f"{'─' * 72}")

    by_bank: dict[str, int] = defaultdict(int)
    for a in analyses:
        bank = normalize_bank(extract_bank(a.get("pdf_name", "")))
        by_bank[bank] += 1

    for bank, count in sorted(by_bank.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * count
        print(f"  {bank:<20} {count:>3} {bar}")

    # ---- Top Alerts ----
    print(f"\n{'─' * 72}")
    print(f"  TOP SIGNALS")
    print(f"{'─' * 72}")

    alerts_found = []
    for a in analyses:
        parsed = a.get("parsed", {})
        tp = parsed.get("target_price") or {}
        new_tp = tp.get("new")
        old_tp = tp.get("old")
        if new_tp and old_tp and old_tp > 0:
            change = (new_tp - old_tp) / old_tp
            if abs(change) >= 0.3:
                bank = normalize_bank(extract_bank(a.get("pdf_name", "")))
                alerts_found.append({
                    "company": parsed.get("company", "?"),
                    "bank": bank,
                    "change": change,
                    "new_tp": new_tp,
                    "old_tp": old_tp,
                    "currency": tp.get("currency", ""),
                })

    alerts_found.sort(key=lambda x: abs(x["change"]), reverse=True)
    for a in alerts_found[:10]:
        direction = "↑" if a["change"] > 0 else "↓"
        emoji = "🚨" if abs(a["change"]) >= 0.5 else "🔴"
        company_name = a['company'] or '?'
        print(f"  {emoji} {a['bank']:<18} {company_name:<15} "
              f"TP {direction}{abs(a['change']):.0%}  "
              f"{a['old_tp']:,.0f}→{a['new_tp']:,.0f} {a['currency']}")

    # ---- Coverage gap ----
    unanalyzed = [p for p in find_all_pdfs()
                  if not (p.parent / f"{p.stem}_analysis.md").exists()
                  and not (p.parent / f"{p.stem}_analysis.json").exists()]

    print(f"\n{'─' * 72}")
    print(f"  STATUS")
    print(f"{'─' * 72}")
    print(f"  Analyzed:    {len(analyses)}/{pdf_total}")
    print(f"  Consensus:   {len(consensus_list)} companies")
    print(f"  Unanalyzed:  {len(unanalyzed)} PDFs remaining")

    # 按目录分组未分析的
    by_dir = defaultdict(int)
    for p in unanalyzed:
        rel = p.relative_to(REPORT_BASE)
        d = str(rel.parent) if str(rel.parent) != "." else "(base)"
        by_dir[d] += 1
    if by_dir:
        print(f"\n  Remaining by directory:")
        for d, c in sorted(by_dir.items()):
            print(f"    {d:<20} {c} PDFs")

    print(f"\n{'=' * 72}\n")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Dashboard")
    parser.add_argument("--run-all", action="store_true",
                        help="Analyze all unanalyzed PDFs before dashboard")
    parser.add_argument("--scan-all", action="store_true",
                        help="Scan-only all PDFs (fast company identification)")

    args = parser.parse_args()

    analyses = find_all_analyses()
    consensus_list = find_all_consensus()

    if args.scan_all:
        unanalyzed = [p for p in find_all_pdfs()
                      if not (p.parent / f"{p.stem}_analysis.md").exists()]
        print(f"🔍 Scanning {len(unanalyzed)} PDFs...")
        for i, pdf in enumerate(unanalyzed, 1):
            print(f"\n[{i}/{len(unanalyzed)}] {pdf.name}")
            subprocess.run(
                [sys.executable, str(PROJECT_DIR / "pdf_vision_analyzer.py"),
                 "--scan-only", str(pdf)],
                cwd=str(PROJECT_DIR)
            )
        analyses = find_all_analyses()

    elif args.run_all:
        unanalyzed = [p for p in find_all_pdfs()
                      if not (p.parent / f"{p.stem}_analysis.md").exists()]
        print(f"🔬 Analyzing {len(unanalyzed)} PDFs...")
        for i, pdf in enumerate(unanalyzed, 1):
            print(f"\n[{i}/{len(unanalyzed)}] {pdf.name}")
            subprocess.run(
                [sys.executable, str(PROJECT_DIR / "pdf_vision_analyzer.py"),
                 "--no-scan", str(pdf)],
                cwd=str(PROJECT_DIR)
            )
        analyses = find_all_analyses()

    # 如果没有共识但有多报告公司，自动生成共识
    from run_pipeline import Pipeline, normalize_company
    by_company: dict[str, list[dict]] = defaultdict(list)
    for a in analyses:
        parsed = a.get("parsed", {})
        company = parsed.get("company", "") or "Unknown"
        canonical = normalize_company(company)
        by_company[canonical].append(a)

    # 为有 ≥2 报告但无共识的公司自动生成共识
    pipeline = Pipeline()
    new_consensus = 0
    for company, reports in by_company.items():
        if len(reports) < 2:
            continue
        # 检查是否已有共识
        has_consensus = any(c.get("company") == company for c in consensus_list)
        if not has_consensus:
            print(f"📊 Auto-generating consensus: {company}")
            result = pipeline.generate_consensus(company, reports)
            if result.get("markdown"):
                safe_name = company.replace(" ", "_").replace(".", "")
                out = REPORT_BASE / f"CONSENSUS_{safe_name}_backtest.md"
                out.write_text(result["markdown"], encoding="utf-8")
                consensus_list.append(result)
                new_consensus += 1

    if new_consensus:
        print(f"✅ Generated {new_consensus} new consensus")

    # 打印仪表盘
    dashboard(analyses, consensus_list)
