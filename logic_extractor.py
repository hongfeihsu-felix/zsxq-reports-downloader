#!/usr/bin/env python3
"""
Phase 2.5: Logic Chain Extractor

从 Phase 2 生成的 *_analysis.md 中提取结构化因果逻辑链。
LLM 做提取 + 自校验，输出 *_logic.json。

用法：
  python3 logic_extractor.py <analysis.md>           # 单份
  python3 logic_extractor.py --dir <report_dir>       # 批量处理目录
  python3 logic_extractor.py --company MediaTek       # 处理某公司所有已分析报告
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

from llm_client import call_llm
from logic_schema import LogicChain

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"

EXTRACT_SYSTEM_PROMPT = """你是一位投研逻辑分析师。从下方投行报告分析中提取因果逻辑推导链。

每条逻辑链的要求：

1. **driver**: 一句话核心驱动因素。
   ✅ 好: "AI ASIC订单超预期推动2027年营收上调"（具体+方向+影响）
   ❌ 差: "AI ASIC"（太短）、"公司前景看好"（太泛）
   每个driver应独立且具体，避免与其它driver语义重叠。

2. **direction**: bullish / bearish / neutral

3. **confidence**: high（≥2个独立数据点）/ medium（1个数据点）/ low（推测性无数据）

4. **evidence**: 数据点列表。每个数据点必须:
   - metric: 指标名（中文或英文均可，保持原文风格）
   - value: 具体数值，必须来自原文
   - source: 来源标注（格式: "公司指引" / "GS模型" / "供应链调研" / "行业数据"）
   ⚠️ 无具体数值的定性陈述不算 evidence，归入 effect 描述。

5. **impacts**: 产业链传导，至少1个：
   - entity: 公司/产品名（具体名称，不用通用类别名如"客户"）
   - role: direct / upstream / downstream / competitor
   - effect: 一句话影响描述（含方向+幅度+时间，如"2027年营收预计增长61%"）

6. **change_from_prior**: 如果原文提到前期预期变化则填，否则留空 ""
   （关键词: "此前"、"上次报告"、"上调"、"下调"、"from"、"vs prior"）
   格式: "此前XX，本次调整为YY"

7. **prior_reference**: 引用的前期数据或日期，无则留空 ""

输出规则:
- 只输出 JSON 数组，不要 markdown 包裹
- 提取 2-6 条逻辑链（太少=遗漏，太多=碎片化）
- 每条 evidence.value 必须从原文提取，不得编造
- driver之间避免语义重复（如"ASIC收入上调"和"TPU收入上调"如果同因应合并为一条）
- 如果报告中确实找不到逻辑链，输出 []

输出格式:
[{"driver": "...", "direction": "bullish", "confidence": "high", "evidence": [{"metric": "...", "value": "...", "source": "..."}], "impacts": [{"entity": "...", "role": "direct", "effect": "..."}], "change_from_prior": "...", "prior_reference": "..."}]"""


def extract_logic_chains(markdown: str, company: str = "",
                          ticker: str = "", bank: str = "",
                          date: str = "") -> list[LogicChain]:
    """Extract logic chains from analysis markdown via LLM."""

    # Trim input if too long
    if len(markdown) > 8000:
        # Keep head + business analysis section + investment conclusion
        head = markdown[:3000]
        tail = markdown[-2000:]
        markdown = head + "\n\n[... middle section truncated ...]\n\n" + tail

    user_prompt = f"Report text:\n\n{markdown}"

    try:
        raw_json, usage = call_llm(EXTRACT_SYSTEM_PROMPT, user_prompt,
                                   max_tokens=4096)
    except Exception as e:
        print(f"  ❌ LLM call failed: {e}")
        return []

    # Parse the JSON from LLM response
    chains = _parse_llm_json(raw_json)
    if not chains:
        return []

    # Convert to LogicChain objects
    result = []
    for item in chains:
        try:
            chain = LogicChain(
                driver=item.get("driver", ""),
                direction=item.get("direction", "neutral"),
                confidence=item.get("confidence", "medium"),
                evidence=item.get("evidence", []),
                impacts=item.get("impacts", []),
                change_from_prior=item.get("change_from_prior", ""),
                prior_reference=item.get("prior_reference", ""),
                bank=bank,
                date=date,
                company=company,
                ticker=ticker
            )
            if chain.driver:  # Require at least a driver
                result.append(chain)
        except Exception:
            continue

    return result


def _parse_llm_json(raw: str) -> list[dict]:
    """Parse JSON from LLM output, handling markdown code fences."""
    import re
    # Strip markdown code fences
    json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', raw)
    if json_match:
        raw = json_match.group(1)
    # Try direct parse
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    # Try finding JSON array in text
    arr_match = re.search(r'\[[\s\S]*\]', raw)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except json.JSONDecodeError:
            pass
    print(f"  ⚠️  Could not parse JSON from LLM response ({len(raw)} chars)")
    return []


def extract_for_report(analysis_md_path: str) -> tuple[list[LogicChain], str]:
    """Extract logic chains for a single analysis markdown file.
    Returns (chains, json_output_path).
    """
    md_path = Path(analysis_md_path)
    if not md_path.exists():
        print(f"  ❌ Not found: {md_path}")
        return [], ""

    markdown = md_path.read_text(encoding="utf-8")

    # Try to get metadata from companion analysis JSON
    json_path = md_path.with_suffix(".json")
    company = bank = date = ticker = ""
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            parsed = data.get("parsed", {})
            company = parsed.get("company", "") or ""
            ticker = parsed.get("ticker", "") or ""
            bank = data.get("pdf_name", "")
            from utils import extract_bank_from_filename
            bank = extract_bank_from_filename(bank)
            date = data.get("analyzed_at", "")[:10]
        except Exception:
            pass

    chains = extract_logic_chains(markdown, company=company, ticker=ticker,
                                  bank=bank, date=date)

    # Save as *_logic.json
    logic_path = md_path.parent / f"{md_path.stem}_logic.json"
    if chains:
        logic_path.write_text(
            json.dumps([c.to_dict() for c in chains], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"  ✅ {len(chains)} logic chains → {logic_path.name}")
        # Also persist to database
        from logic_store import save_logic_chains
        save_logic_chains(chains, str(md_path))
    else:
        print(f"  ⚠️  No logic chains extracted from {md_path.name}")

    return chains, str(logic_path)


def batch_extract(directory: str):
    """Extract logic chains for all analyzed reports in a directory."""
    dir_path = Path(directory)
    analysis_files = sorted(dir_path.rglob("*_analysis.md"))

    # Skip files that already have logic output
    pending = []
    for f in analysis_files:
        logic_file = f.parent / f"{f.stem}_logic.json"
        if not logic_file.exists():
            pending.append(f)

    if not pending:
        print("All reports already have logic chains extracted.")
        return

    print(f"\n🔗 Phase 2.5: Logic Extraction ({len(pending)} reports)\n")

    ok = fail = 0
    for i, md_path in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {md_path.name[:60]}")
        try:
            chains, _ = extract_for_report(str(md_path))
            if chains:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  ❌ {e}")
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logic Chain Extractor")
    parser.add_argument("path", nargs="?", help="Path to _analysis.md file")
    parser.add_argument("--dir", help="Batch process directory")
    parser.add_argument("--company", help="Process all reports for a company")

    args = parser.parse_args()

    if args.dir:
        batch_extract(args.dir)
    elif args.company:
        # Find all analysis md for company
        company_dir = REPORT_BASE
        if company_dir.exists():
            batch_extract(str(company_dir))
    elif args.path:
        chains, out = extract_for_report(args.path)
        if chains:
            for c in chains:
                print(f"  📎 {c.driver} [{c.direction}] ({c.confidence})")
                print(f"     evidence: {len(c.evidence)} pts, impacts: {len(c.impacts)} entities")
    else:
        parser.print_help()
