#!/usr/bin/env python3
"""
搜索 CLI — 在终端中搜索报告索引

用法:
  python3 search.py "MediaTek AI ASIC"
  python3 search.py "TSMC capacity" --company TSMC --limit 10
  python3 search.py "CoWoS" --json | jq .
"""

import json
import argparse
from report_index import ReportIndex


def format_results(results: dict) -> str:
    """格式化终端输出"""
    lines = []
    lines.append(f"🔍 \"{results['query']}\" — {results['total']} results")
    lines.append("")

    for i, item in enumerate(results["results"], 1):
        bank = item["bank"] or "?"
        companies = ", ".join(item["companies"]) or "-"
        inds = ", ".join(item.get("industries", [])) or "-"
        date = item["report_date"] or "?"

        lines.append(f"  [{i}] {bank:15s} | {item['pdf_name'][:55]}")
        lines.append(f"       Date: {date}  Companies: {companies}")
        if inds != "-":
            lines.append(f"       Industries: {inds}")
        snippet = item.get("snippet", "")
        if snippet:
            lines.append(f"       {snippet}")
        lines.append("")

    # 聚合面板
    aggs = results.get("aggs", {})
    if aggs:
        lines.append("─" * 60)
        if aggs.get("banks"):
            lines.append("  Banks: " + ", ".join(
                f"{k}({v})" for k, v in sorted(aggs["banks"].items(), key=lambda x: -x[1])[:8]
            ))
        if aggs.get("companies"):
            lines.append("  Companies: " + ", ".join(
                f"{k}({v})" for k, v in sorted(aggs["companies"].items(), key=lambda x: -x[1])[:8]
            ))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Search report index")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--limit", type=int, default=20, help="Max results")
    parser.add_argument("--company", help="Filter by company")
    parser.add_argument("--industry", help="Filter by industry slug")
    parser.add_argument("--bank", help="Filter by bank")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--full", action="store_true", help="Show full summary text")
    args = parser.parse_args()

    idx = ReportIndex()
    results = idx.search(
        args.query,
        limit=args.limit,
        company=args.company,
        industry=args.industry,
        bank=args.bank,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_results(results))

    idx.close()


if __name__ == "__main__":
    main()
