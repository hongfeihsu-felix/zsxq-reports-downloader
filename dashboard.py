#!/usr/bin/env python3
"""
Dashboard - L1 行业热力图 + L2 公司详情

用法：
  python3 dashboard.py                    # L1 行业总览 + L2 公司详情
  python3 dashboard.py --level 1          # 仅 L1
  python3 dashboard.py --level 2 --company MediaTek  # 单公司 L2
  python3 dashboard.py --level 2 --company all        # 所有公司 L2
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from utils import extract_bank_from_filename

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"


def load_all_analyses() -> list[dict]:
    results = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_path"] = str(f)
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def load_all_consensus() -> list[dict]:
    results = []
    for f in sorted(REPORT_BASE.rglob("CONSENSUS_*.json")):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


# ============ L1: Industry Heatmap ============

def industry_heatmap(analyses: list[dict]):
    """L1: 行业热力图"""
    # 按行业聚合
    by_industry: dict[str, dict] = defaultdict(lambda: {
        "report_count": 0,
        "companies": set(),
        "tp_changes": [],  # list of (company, change_pct)
        "risk_count": 0,
        "opp_count": 0,
        "banks": set()
    })

    for item in analyses:
        parsed = item.get("parsed", {})
        tags = parsed.get("industry_tags", [])
        company = parsed.get("company", "?")
        tp = parsed.get("target_price") or {}
        risks = parsed.get("risk_signals", [])
        opps = parsed.get("opportunity_signals", [])

        # Extract bank
        import re
        pdf_name = item.get("pdf_name", "")
        bank = extract_bank_from_filename(pdf_name)

        # TP change
        new_tp = tp.get("new")
        old_tp = tp.get("old")
        tp_change = None
        if new_tp and old_tp and old_tp > 0:
            tp_change = (new_tp - old_tp) / old_tp

        for tag in tags:
            slug = tag["slug"]
            ind = by_industry[slug]
            ind["report_count"] += 1
            if company and company != "None":
                ind["companies"].add(company)
            if tp_change is not None:
                ind["tp_changes"].append((company, tp_change))
            ind["risk_count"] += len(risks)
            ind["opp_count"] += len(opps)
            ind["banks"].add(bank)

    # 打印热力图
    print(f"\n{'=' * 78}")
    print(f"  📊 L1: INDUSTRY HEATMAP  —  {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'=' * 78}")

    sorted_inds = sorted(by_industry.items(),
                         key=lambda x: x[1]["report_count"], reverse=True)

    for slug, ind in sorted_inds:
        n = ind["report_count"]
        companies = sorted(ind["companies"])
        banks = sorted(ind["banks"])

        # 信号强度
        tp_changes = ind["tp_changes"]
        avg_tp = sum(abs(c) for _, c in tp_changes) / len(tp_changes) if tp_changes else 0
        bullish = sum(1 for _, c in tp_changes if c > 0)
        bearish = sum(1 for _, c in tp_changes if c < 0)

        # 热度条
        bar_len = min(n * 2, 40)
        if avg_tp >= 0.5:
            bar = "█" * bar_len + " 🔥"
        elif avg_tp >= 0.3:
            bar = "█" * bar_len + " 📈"
        else:
            bar = "█" * bar_len

        # 信号 indicators
        signals = []
        if bullish > bearish:
            signals.append(f"🟢{bullish}↑")
        if bearish > bullish:
            signals.append(f"🔴{bearish}↓")
        if len(companies) >= 3:
            signals.append(f"📋{len(companies)}cos")
        if avg_tp >= 0.5:
            signals.append("🚨TP surge")

        print(f"\n  ┌─ {ind.get('name', slug)}  [{slug}]")
        print(f"  │  Reports: {n}  │  Companies: {', '.join(companies[:6])}  │  {', '.join(signals)}")
        print(f"  │  {bar}")
        print(f"  │  Banks: {', '.join(banks[:6])}")
        if tp_changes:
            top_moves = sorted(tp_changes, key=lambda x: abs(x[1]), reverse=True)[:3]
            moves_str = "  │  TP Moves: " + " | ".join(
                f"{co}: {'+' if ch > 0 else ''}{ch:.0%}" for co, ch in top_moves
            )
            print(moves_str)
        print(f"  └{'─' * 50}")

    # 无标签报告
    untagged = sum(1 for a in analyses
                   if not a.get("parsed", {}).get("industry_tags"))
    if untagged:
        print(f"\n  ⚠️  {untagged} reports with no industry tags")

    print(f"\n{'=' * 78}\n")


# ============ L2: Company Detail ============

def company_detail(analyses: list[dict], company_filter: str = None):
    """L2: 公司详情卡片"""
    from run_pipeline import normalize_company

    # 按公司分组
    by_company: dict[str, list[dict]] = defaultdict(list)
    for item in analyses:
        parsed = item.get("parsed", {})
        company = parsed.get("company", "")
        if not company or company == "None":
            continue
        canonical = normalize_company(company)
        by_company[canonical].append(item)

    if company_filter and company_filter != "all":
        canonical = normalize_company(company_filter)
        by_company = {canonical: by_company.get(canonical, [])}

    if not by_company:
        print("No company data available")
        return

    print(f"\n{'=' * 78}")
    print(f"  📈 L2: COMPANY DETAILS")
    print(f"{'=' * 78}")

    for company, items in sorted(by_company.items(),
                                  key=lambda x: len(x[1]), reverse=True):
        if not items:
            continue

        # 汇总信息
        tickers = set()
        ratings = []
        tps = []
        banks = []
        all_industry_tags: dict[str, int] = defaultdict(int)

        import re
        for item in items:
            parsed = item.get("parsed", {})
            ticker = parsed.get("ticker", "")
            if ticker and ticker != "None":
                tickers.add(ticker)
            rating = parsed.get("rating", "")
            if rating:
                ratings.append(rating)
            tp = parsed.get("target_price") or {}
            if tp.get("new"):
                tps.append((tp["new"], tp.get("old"), tp.get("currency", "")))
            pdf_name = item.get("pdf_name", "")
            bank = extract_bank_from_filename(pdf_name)
            if bank != "?":
                banks.append(bank)
            for tag in parsed.get("industry_tags", []):
                all_industry_tags[tag["slug"]] += tag.get("match_count", 1)

        # TP range
        new_tps = [t[0] for t in tps]
        tp_range = f"{min(new_tps):,.0f} – {max(new_tps):,.0f}" if new_tps else "N/A"
        currencies = set(t[2] for t in tps if t[2])

        # Rating distribution
        rating_dist = defaultdict(int)
        for r in ratings:
            rating_dist[r] += 1
        rating_str = " | ".join(f"{r}:{c}" for r, c in rating_dist.items())

        # TP momentum
        tp_changes = []
        for new, old, cur in tps:
            if old and old > 0:
                tp_changes.append((new - old) / old)
        avg_tp_change = sum(tp_changes) / len(tp_changes) if tp_changes else 0.0
        momentum = ""
        if avg_tp_change >= 0.5:
            momentum = "🚀 Strong Up"
        elif avg_tp_change >= 0.2:
            momentum = "📈 Up"
        elif avg_tp_change <= -0.2:
            momentum = "📉 Down"
        else:
            momentum = "➡️ Stable"

        # Top industry tags
        top_tags = sorted(all_industry_tags.items(), key=lambda x: x[1], reverse=True)[:4]

        # Print card
        tp_delta_str = f"{avg_tp_change:+.0%}"
        print(f"""
  ┌────────────────────────────────────────────────────────────────────────┐
  │  {company:<20} {', '.join(sorted(tickers)):<20}  {momentum:<20}│
  ├────────────────────────────────────────────────────────────────────────┤
  │  Reports: {len(items):<2}  │  TP Range: {tp_range:<20} {','.join(currencies):<6}       │
  │  Ratings: {rating_str:<50} │
  │  Avg TP Δ: {tp_delta_str:<7}  │  Tags: {', '.join(t[0] for t in top_tags):<30} │
  │  Banks: {', '.join(sorted(set(banks))[:6]):<60} │
  ├────────────────────────────────────────────────────────────────────────┤""")

        # Per-report detail
        for item in items:
            parsed = item.get("parsed", {})
            tp = parsed.get("target_price") or {}
            new_tp = tp.get("new")
            old_tp = tp.get("old")
            tp_change_str = ""
            if new_tp and old_tp and old_tp > 0:
                ch = (new_tp - old_tp) / old_tp
                arrow = "↑" if ch > 0 else "↓"
                tp_change_str = f" {arrow}{abs(ch):.0%}"

            pdf_name = item.get("pdf_name", "")[:55]
            bank = extract_bank_from_filename(pdf_name)[:12]
            rating = parsed.get("rating", "?") or "?"
            tags = [t["slug"] for t in parsed.get("industry_tags", [])][:3]

            print(f"  │  {bank:<12} │ {rating:<6} │ TP:{str(new_tp):>8} {tp_change_str:<8} │ {', '.join(tags):<20} │")

        print(f"  └────────────────────────────────────────────────────────────────────────┘")

    print(f"\n{'=' * 78}\n")


# ============ Main ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dashboard")
    parser.add_argument("--level", type=int, choices=[1, 2], default=0,
                        help="Dashboard level (1=Industry, 2=Company, default=both)")
    parser.add_argument("--company", help="Company name filter for L2")

    args = parser.parse_args()
    analyses = load_all_analyses()

    if not analyses:
        print("No analyses found. Run pipeline first.")
        exit(0)

    if args.level == 0 or args.level == 1:
        industry_heatmap(analyses)

    if args.level == 0 or args.level == 2:
        company_detail(analyses, args.company)
