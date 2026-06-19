#!/usr/bin/env python3
"""
Chart Page Extractor — 从 PDF 报告中提取关键图表页

策略：
  - 扫描版 PDF: 所有页是图片 → 检测 "EXHIBIT/FIGURE/CHART" 关键词 → 保存对应页
  - 文字版 PDF: 检测文本中的图表标记 → 保存对应页
  - 始终保存首页 (封面)
  - 最多 10 页/报告
  - 渲染 200 DPI PNG

用法：
  python3 extract_charts.py <pdf_path>                    # 单份报告
  python3 extract_charts.py --all                          # 所有已分析的 PDF
  python3 extract_charts.py --report <analysis_md_path>    # 回溯单份已分析报告
"""

import re
import sys
import argparse
from pathlib import Path

import fitz  # PyMuPDF

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
CHART_DPI = 200
MAX_CHARTS = 10

# 图表页检测关键词
CHART_KEYWORDS = [
    "exhibit", "figure", "chart", "graph",
    "price trend", "shipment", "revenue", "margin",
    "capacity", "utilization", "market share",
    "qoq", "yoy", "quarterly", "monthly",
    "outlook", "forecast", "guidance",
]


def detect_chart_pages(doc: fitz.Document) -> list[int]:
    """检测哪些页包含图表"""
    chart_pages = []
    page_count = doc.page_count

    for i in range(page_count):
        page = doc[i]
        text = page.get_text("text").lower()

        # 跳过披露/免责声明页
        if any(skip in text for skip in [
            "disclosure appendix", "distribution of",
            "this document does", "disclaimer",
            "important disclosure", "rating structure"
        ]):
            # Still check if it's page 1 (cover)
            if i > 2:
                continue

        # 检测图表关键词
        score = sum(1 for kw in CHART_KEYWORDS if kw in text)

        # 扫描版 PDF: 低文本量但含 embedded images → 更可能是图表页
        images = page.get_images()
        is_image_heavy = len(images) >= 8

        if score >= 2 or is_image_heavy or i == 0:
            chart_pages.append(i)

    # 优先首页 + 高文本得分页
    if 0 not in chart_pages:
        chart_pages.insert(0, 0)

    # 限制数量
    if len(chart_pages) > MAX_CHARTS:
        # 保留首页 + 得分最高的
        scores = {}
        for i in chart_pages:
            text = doc[i].get_text("text").lower()
            scores[i] = sum(1 for kw in CHART_KEYWORDS if kw in text)
        chart_pages.sort(key=lambda i: (i == 0, scores.get(i, 0)), reverse=True)
        chart_pages = chart_pages[:MAX_CHARTS]

    chart_pages.sort()
    return chart_pages


def extract_charts(pdf_path: str, output_dir: str = None) -> list[str]:
    """提取图表页为 PNG"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"  ❌ Not found: {pdf_path}")
        return []

    stem = pdf_path.stem
    if output_dir:
        chart_dir = Path(output_dir) / f"{stem}_charts"
    else:
        chart_dir = pdf_path.parent / f"{stem}_charts"

    # Skip if already extracted
    existing = list(chart_dir.glob("page_*.png")) if chart_dir.exists() else []
    if len(existing) >= 2:
        return [str(p) for p in sorted(existing)]

    doc = fitz.open(str(pdf_path))
    chart_pages = detect_chart_pages(doc)

    chart_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    for page_num in chart_pages:
        page = doc[page_num]
        pix = page.get_pixmap(dpi=CHART_DPI)
        out_path = chart_dir / f"page_{page_num + 1:02d}.png"
        pix.save(str(out_path))
        saved.append(str(out_path))

    doc.close()

    if saved:
        print(f"  📊 {pdf_path.name[:50]}: {len(saved)} charts → {chart_dir.name}/")
    return saved


def backfill_all():
    """回溯所有已分析的 PDF"""
    analysis_files = list(REPORT_BASE.rglob("*_analysis.md"))
    print(f"\n📊 Chart Extraction — Backfill {len(analysis_files)} reports\n")

    total_charts = 0
    for i, md_path in enumerate(sorted(analysis_files), 1):
        stem = md_path.stem.replace("_analysis", "")
        # Find original PDF
        pdf_path = None
        for ext in [".pdf", ".pptx"]:
            candidate = md_path.parent / f"{stem}{ext}"
            if candidate.exists():
                pdf_path = candidate
                break

        if not pdf_path:
            # Try without extension match
            for f in md_path.parent.glob(f"{stem}.*"):
                if f.suffix in (".pdf", ".pptx"):
                    pdf_path = f
                    break

        if not pdf_path:
            continue

        # Check if already has charts
        chart_dir = md_path.parent / f"{stem}_charts"
        if chart_dir.exists() and len(list(chart_dir.glob("page_*.png"))) >= 2:
            continue

        saved = extract_charts(str(pdf_path))
        total_charts += len(saved)

    print(f"\n✅ Total: {total_charts} chart pages extracted")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chart Page Extractor")
    parser.add_argument("path", nargs="?", help="PDF file path")
    parser.add_argument("--all", action="store_true", help="Backfill all analyzed reports")
    parser.add_argument("--report", help="Path to _analysis.md (find original PDF)")
    parser.add_argument("--output-dir", help="Custom output directory")

    args = parser.parse_args()

    if args.all:
        backfill_all()
    elif args.report:
        md_path = Path(args.report)
        stem = md_path.stem.replace("_analysis", "")
        for ext in [".pdf", ".pptx"]:
            pdf_path = md_path.parent / f"{stem}{ext}"
            if pdf_path.exists():
                extract_charts(str(pdf_path))
                break
        else:
            print(f"❌ Original PDF not found for: {md_path.name}")
    elif args.path:
        extract_charts(args.path, args.output_dir)
    else:
        parser.print_help()
