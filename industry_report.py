#!/usr/bin/env python3
"""
Industry Report Generator - 定期行业分析报告

从多份公司报告中提取行业维度的信号，生成跨公司行业综述。

用法：
  python3 industry_report.py ai-chip           # AI Chip 行业报告
  python3 industry_report.py memory             # HBM/Memory 行业报告
  python3 industry_report.py cowos              # CoWoS 行业报告
  python3 industry_report.py foundry            # Foundry 行业报告
  python3 industry_report.py --all              # 所有活跃行业
  python3 industry_report.py --list             # 列出可用行业
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from llm_client import call_llm
from utils import extract_bank_from_filename

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"

# ============ 行业报告 System Prompt ============

INDUSTRY_REPORT_PROMPT = """你是一位资深半导体行业分析师。请将多份卖方研报摘录综合为一份完整的行业分析报告。下方摘录均围绕 {industry_name} 主题。

请用中文输出结构化行业报告：

## {industry_name} — 行业分析
**报告日期:** {report_date}
**来源:** {source_count} 份研报，覆盖 {company_count} 家公司
**涉及公司:** {companies_list}

### 1. 摘要
[2-3句概述行业层面的核心发现]

### 2. 供需动态
- 当前供给状况及产能利用率
- 需求驱动因素与增长轨迹
- 供需平衡展望（紧张/平衡/过剩）
- 交期与价格趋势

### 3. 主要玩家与竞争格局
| 公司 | 定位/优势 | 近期进展 | 市场份额趋势 |
|------|----------|---------|------------|
| [公司A] | [定位] | [进展] | [趋势] |

### 4. 产能与资本开支
- 已宣布的重大产能扩张
- 主要厂商 Capex 指引
- 新产线投产时间表
- 设备/技术转换

### 5. 技术路线图
- 下一代技术转换
- 关键里程碑（3nm→2nm, HBM3→HBM4 等）
- 性能/效率改进
- 架构变革（chiplet、先进封装等）

### 6. 共识与分歧
- **共识观点:** [多数分析师一致认同]
- **看多情景:** [最乐观预测，哪些投行]
- **看空情景:** [最谨慎预测，哪些投行]
- **核心争议:** [分析师分歧点]

### 7. 催化剂与风险
**近期催化剂（0-6个月）:**
- [催化剂一]

**中期催化剂（6-18个月）:**
- [催化剂一]

**主要风险:**
- [风险一]

### 8. 投资启示
- **最看好标的:** [最受益公司及简要逻辑]
- **观察名单:** [等待入场/出场时机]
- **行业评级:** [超配/中性/低配] 及理由

请用具体数字和投行名称。引用来源投行标注观点出处。"""


def load_config_industries() -> list[dict]:
    """加载行业配置"""
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        return cfg.get("tracking", {}).get("industries", [])
    return []


def collect_industry_data(slug: str) -> dict:
    """收集某行业的所有相关分析"""
    analyses = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_md_path"] = str(f.with_suffix(".md"))
            data["_pdf_name"] = data.get("pdf_name", "")
            analyses.append(data)
        except (json.JSONDecodeError, KeyError):
            continue

    # Filter by industry tag
    relevant = []
    # Get industry keywords from config for broader matching
    industry_kws = set()
    for ind in load_config_industries():
        if ind.get("slug") == slug:
            industry_kws = set(kw.lower() for kw in ind.get("keywords", []))
            break

    for a in analyses:
        parsed = a.get("parsed", {})
        # Method 1: explicit industry_tags match
        tags = parsed.get("industry_tags", {})
        if isinstance(tags, dict):
            tag_match = any(
                any(t.get("slug") == slug for t in layer_tags)
                for layer_tags in tags.values()
            )
        elif isinstance(tags, list):
            tag_match = any(t.get("slug") == slug for t in tags if isinstance(t, dict))
        else:
            tag_match = False

        # Method 2: keyword match in analysis markdown
        kw_match = False
        if industry_kws:
            md_path = a.get("_md_path", "")
            if md_path and Path(md_path).exists():
                md_text = Path(md_path).read_text(encoding="utf-8").lower()
                kw_match = any(kw in md_text for kw in industry_kws)

        if tag_match or kw_match:
            relevant.append(a)

    if not relevant:
        return {"slug": slug, "report_count": 0, "companies": [], "excerpts": []}

    # Collect excerpts
    excerpts = []
    companies = set()
    banks = set()
    for a in relevant:
        md_path = a.get("_md_path", "")
        if md_path and Path(md_path).exists():
            md_text = Path(md_path).read_text(encoding="utf-8")
            # 截取相关片段：summary + key findings + business analysis
            if len(md_text) > 2500:
                # 保留头部（Report Summary + Key Findings）
                head = md_text[:1500]
                # 搜索 Business Analysis 部分
                ba_match = re.search(r'## Business Analysis.*?(?=##|\Z)', md_text, re.DOTALL)
                tail = ba_match.group(0)[:1000] if ba_match else ""
                md_text = head + "\n\n" + tail
            excerpts.append(md_text)

        parsed = a.get("parsed", {})
        company = parsed.get("company", "")
        if company and company != "None":
            companies.add(company)

        pdf_name = a.get("_pdf_name", "")
        bank = extract_bank_from_filename(pdf_name)
        if bank != "?":
            banks.add(bank)

    industry = next((i for i in load_config_industries() if i["slug"] == slug), {})
    return {
        "slug": slug,
        "name": industry.get("name", slug),
        "report_count": len(relevant),
        "companies": sorted(companies),
        "banks": sorted(banks),
        "excerpts": excerpts
    }


class IndustryReportGenerator:
    """行业报告生成器"""

    def generate(self, data: dict) -> str:
        """生成行业报告"""
        if data["report_count"] < 2:
            return (f"## {data['name']} — Insufficient Data\n\n"
                    f"Only {data['report_count']} report found. "
                    f"Need at least 2 reports for industry analysis.")

        print(f"  📊 Generating {data['name']} report "
              f"({data['report_count']} reports, {len(data['companies'])} companies)")

        # Build prompt
        companies_list = ", ".join(data["companies"])
        prompt = INDUSTRY_REPORT_PROMPT.format(
            industry_name=data["name"],
            report_date=datetime.now().strftime("%Y-%m-%d"),
            source_count=data["report_count"],
            company_count=len(data["companies"]),
            companies_list=companies_list
        )

        user_content = (
            f"Industry: {data['name']}\n"
            f"Banks: {', '.join(data['banks'])}\n"
            f"Companies: {companies_list}\n\n"
            + "\n\n---\n\n".join(
                f"### Source {i + 1}\n{excerpt}"
                for i, excerpt in enumerate(data["excerpts"])
            )
        )

        markdown, usage_info = call_llm(prompt, user_content, max_tokens=4096)
        total = usage_info["total_tokens"]
        print(f"     Tokens: {total}  ~${total * 0.28 / 1_000_000:.4f}")

        return markdown


def generate_all_active(generator: IndustryReportGenerator,
                        output_dir: Path = None):
    """为所有有 ≥2 报告的活跃行业生成报告"""
    industries = load_config_industries()
    output_dir = output_dir or REPORT_BASE

    for ind in industries:
        if not ind.get("active", True):
            continue

        data = collect_industry_data(ind["slug"])
        if data["report_count"] < 2:
            print(f"  ⏭️  {ind['name']}: only {data['report_count']} reports, skipping")
            continue

        markdown = generator.generate(data)
        safe_slug = ind["slug"].replace("/", "-")
        out_path = output_dir / f"INDUSTRY_{safe_slug}_{datetime.now().strftime('%Y%m%d')}.md"
        out_path.write_text(markdown, encoding="utf-8")
        print(f"     📄 {out_path}")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Industry Report Generator")
    parser.add_argument("slug", nargs="?", help="Industry slug (e.g., memory, ai-chip, cowos)")
    parser.add_argument("--all", action="store_true", help="Generate for all active industries")
    parser.add_argument("--list", action="store_true", help="List available industries with report counts")
    parser.add_argument("--output-dir", help="Output directory")

    args = parser.parse_args()

    if args.list:
        print(f"\n{'Industry':<35} {'Reports':>8} {'Companies':>8}")
        print(f"{'─'*35} {'─'*8} {'─'*8}")
        for ind in load_config_industries():
            data = collect_industry_data(ind["slug"])
            status = "✅" if data["report_count"] >= 2 else "⏳"
            print(f"{status} {ind['name']:<32} {data['report_count']:>8} "
                  f"{len(data['companies']):>8}")
        print()
        sys.exit(0)

    generator = IndustryReportGenerator()

    if args.all:
        generate_all_active(generator, output_dir=args.output_dir)
    elif args.slug:
        data = collect_industry_data(args.slug)
        if data["report_count"] == 0:
            print(f"❌ No reports found for industry: {args.slug}")
            print("   Use --list to see available industries")
            sys.exit(1)

        markdown = generator.generate(data)
        output_dir = Path(args.output_dir) if args.output_dir else REPORT_BASE
        safe_slug = args.slug.replace("/", "-")
        out_path = output_dir / f"INDUSTRY_{safe_slug}_{datetime.now().strftime('%Y%m%d')}.md"
        out_path.write_text(markdown, encoding="utf-8")
        print(f"     📄 {out_path}")
        print(f"\n{markdown[:500]}...")
    else:
        parser.print_help()
