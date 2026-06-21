#!/usr/bin/env python3
"""
Pipeline: Download → Analyze → Group → Consensus → Alert

完整投研 pipeline 编排器

用法：
  # 下载并分析今天的报告
  python3 run_pipeline.py

  # 分析指定日期的已有报告（跳过下载）
  python3 run_pipeline.py --date 20260508 --skip-download

  # 仅下载
  python3 run_pipeline.py --download-only

  # 分析后仅生成共识，跳过逐份分析
  python3 run_pipeline.py --date 20260508 --consensus-only
"""

import os
import sys
import re
import json
import fcntl
import argparse
import subprocess
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional
from utils import extract_bank_from_filename
from entity_resolver import normalize_company as _normalize_company

# ============ 路径配置 ============
PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"

def normalize_company(name: str) -> str:
    """公司名 → canonical 名. Compatibility wrapper around entity_resolver."""
    return _normalize_company(name)


# ============ 共识汇总 Prompt ============

CONSENSUS_SYSTEM_PROMPT = """你是一位全球科技对冲基金的资深投资组合经理。你收到了多份关于同一家公司的卖方分析师报告及其**逻辑链聚合数据**。

请基于这些逻辑链数据，用中文输出**溯源式共识报告**（不是泛泛的摘要）：

## 共识摘要：[公司名] ([股票代码])
分析日期：[今天]
报告数量：[N] 份，来自 [投行列表]

### 核心驱动共识（按共识强度排序）
对每个核心驱动因素：
- **驱动**: [驱动因素名称]
- **共识强度**: [full(≥4家) / strong(3家) / partial(2家)]
- **方向**: [看多/看空/中性]
- **投行**: [列举]
- **证据矩阵**: 用表格列出各投行对该驱动的数据支撑
- **产业链传导**: 该驱动如何影响上下游（本公司 → 上游 → 下游 → 竞争对手）
- **与前期变化**: 相对上次报告/市场预期的变化
- **分歧点**: 同一驱动下各投行的观点差异

### 孤点信号（仅1家提及但可能被市场忽略的驱动）
- [列举]

### 产业链传导总图
汇总所有驱动的 impact graph，画出完整传导路径

### 分歧分析
- **方向分歧**: 哪些 driver 在投行间有牛熊分歧
- **幅度分歧**: 同一 driver 的量级判断差异

### 风险矩阵
| 风险 | 逻辑链置信度 | 对冲因素 | 来源 |
|------|------------|---------|------|
标注哪些逻辑链的证据最薄弱

### 操作建议
基于逻辑链强度 + 共识/分歧，给出短期/中期操作建议

以数据驱动。引用具体投行和数字。标注置信度低的推理。少用模糊词。"""


# ============ Pipeline ============

class Pipeline:
    """投研 Pipeline 编排器"""

    def __init__(self, date_str: str = None, report_dir: str = None):
        self.date_str = date_str or datetime.now().strftime("%Y%m%d")
        if report_dir:
            self.report_dir = Path(report_dir)
        else:
            self.report_dir = REPORT_BASE / self.date_str

    # ---- Phase 1: Download ----

    def run_download(self) -> bool:
        """运行下载器"""
        print(f"\n{'=' * 60}")
        print(f"📥 PHASE 1: Download ({self.date_str})")
        print(f"{'=' * 60}")

        downloader = PROJECT_DIR / "zsxq_downloader.py"
        if not downloader.exists():
            print("❌ zsxq_downloader.py not found")
            return False

        try:
            cmd = [sys.executable, str(downloader), "--date", self.date_str]
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_DIR),
                capture_output=True, text=True, timeout=7200
            )
            if result.stderr.strip():
                print("  Downloader stderr:", result.stderr.strip()[-500:])
            if result.returncode != 0:
                print(f"❌ Download failed (exit {result.returncode}):")
                print(result.stderr[-500:])
                return False
            # Log last few lines of downloader stdout for diagnostics
            out_lines = result.stdout.strip().split('\n')
            for line in out_lines[-10:]:
                print(f"  [downloader] {line}")
            print("✅ Download complete")
            return True
        except subprocess.TimeoutExpired:
            print("❌ Download timed out (2hr)")
            return False
        except Exception as e:
            print(f"❌ Download error: {e}")
            return False

    # ---- Phase 2: Analyze ----

    def find_pdfs(self) -> list[Path]:
        """找出尚未分析的 PDF/PPTX 文件"""
        if not self.report_dir.exists():
            return []

        all_files = sorted(self.report_dir.rglob("*.pdf"))
        all_files += sorted(self.report_dir.rglob("*.pptx"))
        all_files += sorted(self.report_dir.rglob("*.xlsx"))
        all_files += sorted(self.report_dir.rglob("*.jpg"))
        all_files += sorted(self.report_dir.rglob("*.jpeg"))
        all_files += sorted(self.report_dir.rglob("*.png"))
        unanalyzed = []
        for pdf in all_files:
            # 跳过 _analysis 输出（避免重复分析自己生成的 markdown）
            stem = pdf.stem
            if stem.endswith("_analysis"):
                continue
            # 跳过已有分析的
            md_file = pdf.parent / f"{stem}_analysis.md"
            if md_file.exists():
                continue
            unanalyzed.append(pdf)
        return unanalyzed

    def run_analysis(self, pdfs: list[Path] = None) -> list[dict]:
        """逐份分析 PDF"""
        if pdfs is None:
            pdfs = self.find_pdfs()

        print(f"\n{'=' * 60}")
        print(f"🔬 PHASE 2: Analysis ({len(pdfs)} PDFs)")
        print(f"{'=' * 60}")

        if not pdfs:
            print("  No new PDFs to analyze")
            return []

        analyzer_script = PROJECT_DIR / "pdf_vision_analyzer.py"

        for i, pdf in enumerate(pdfs, 1):
            print(f"\n[{i}/{len(pdfs)}] {pdf.name}")
            success = False
            for attempt in range(3):
                if attempt > 0:
                    wait = 30 * attempt
                    print(f"  🔄 Retry {attempt+1}/3 after {wait}s...")
                    time.sleep(wait)
                try:
                    result = subprocess.run(
                        [sys.executable, str(analyzer_script), "--no-scan", str(pdf)],
                        cwd=str(PROJECT_DIR),
                        capture_output=True, text=True, timeout=600
                    )
                    if result.returncode != 0:
                        print(f"  ⚠️  Exit {result.returncode}: {result.stderr[-200:]}")
                    else:
                        success = True
                        break
                except subprocess.TimeoutExpired:
                    print(f"  ⏱️  Timeout (attempt {attempt+1}/3)")
                except Exception as e:
                    print(f"  ❌ Error (attempt {attempt+1}/3): {e}")
            if not success:
                if result := locals().get("result"):
                    print(f"  ❌ Failed: {result.stderr[-300:]}")
                print(f"  ❌ Skipping after {3} retries")

        # 收集所有分析结果
        return self.collect_analyses()

    def collect_analyses(self) -> list[dict]:
        """收集所有 _analysis.json 结果"""
        results = []
        if not self.report_dir.exists():
            return results

        for json_file in sorted(self.report_dir.rglob("*_analysis.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                data["_json_path"] = str(json_file)
                data["_md_path"] = str(json_file.with_suffix(".md"))
                results.append(data)
            except (json.JSONDecodeError, KeyError):
                continue

        # Also check Mediatek/ subdirectory and other subdirs
        for subdir in self.report_dir.iterdir():
            if subdir.is_dir():
                for json_file in sorted(subdir.rglob("*_analysis.json")):
                    try:
                        data = json.loads(json_file.read_text(encoding="utf-8"))
                        data["_json_path"] = str(json_file)
                        data["_md_path"] = str(json_file.with_suffix(".md"))
                        results.append(data)
                    except (json.JSONDecodeError, KeyError):
                        continue

        return results

    # ---- Phase 2.5: Logic Extraction ----

    def run_logic_extraction(self) -> int:
        """提取所有已分析报告的逻辑链"""
        print(f"\n{'=' * 60}")
        print(f"🔗 PHASE 2.5: Logic Extraction")
        print(f"{'=' * 60}")

        from logic_extractor import extract_for_report

        analysis_files = list(self.report_dir.rglob("*_analysis.md"))
        # Also check subdirectories
        for subdir in self.report_dir.iterdir():
            if subdir.is_dir():
                analysis_files.extend(subdir.rglob("*_analysis.md"))

        # Deduplicate and filter already-processed
        pending = []
        for f in sorted(set(analysis_files)):
            logic_path = f.parent / f"{f.stem}_logic.json"
            if not logic_path.exists():
                pending.append(f)

        if not pending:
            print("  All reports already have logic chains extracted")
            return 0

        print(f"  {len(pending)} reports to extract")
        ok = 0
        for i, md_path in enumerate(pending, 1):
            print(f"  [{i}/{len(pending)}] {md_path.name[:55]}")
            try:
                chains, _ = extract_for_report(str(md_path))
                if chains:
                    ok += 1
            except Exception as e:
                print(f"    ❌ {e}")

        print(f"  ✅ Logic extraction: {ok}/{len(pending)} successful")
        return ok

    # ---- Phase 3: Group ----

    def group_by_company(self, analyses: list[dict]) -> dict[str, list[dict]]:
        """按公司名分组"""
        groups = defaultdict(list)
        for item in analyses:
            parsed = item.get("parsed", {})
            raw_company = parsed.get("company", "") or ""
            canonical = normalize_company(raw_company)

            # 如果 parsed 中没有公司名，尝试从 pdf_name 提取
            if canonical == "Unknown" or not canonical:
                pdf_name = item.get("pdf_name", "")
                # 从文件名提取：通常是 "Bank-Company（..."
                name_match = re.match(r'^[A-Za-z\s]+?-([A-Za-z\s&.]+?)[（(]', pdf_name)
                if name_match:
                    canonical = normalize_company(name_match.group(1).strip())

            groups[canonical].append(item)

        return dict(groups)

    # ---- Phase 3.5: Logic Aggregation ----

    def run_logic_aggregation(self, groups: dict[str, list[dict]]) -> dict[str, list]:
        """跨报告聚合逻辑链，按 driver 聚类"""
        print(f"\n{'=' * 60}")
        print(f"🔗 PHASE 3.5: Logic Aggregation ({len(groups)} companies)")
        print(f"{'=' * 60}")

        from logic_aggregator import aggregate, format_aggregated_markdown

        results = {}
        for company, reports in sorted(groups.items()):
            if len(reports) < 2:
                print(f"  ⏭️  {company}: only {len(reports)} report, skipping aggregation")
                continue

            print(f"  📊 Aggregating: {company} ({len(reports)} reports)")
            try:
                drivers = aggregate(company)
                results[company] = drivers

                # Save aggregated markdown for consensus input
                md = format_aggregated_markdown(company, drivers)
                safe_name = company.replace(" ", "_").replace(".", "")
                agg_path = self.report_dir / f"AGGREGATED_{safe_name}_{self.date_str}.md"
                agg_path.write_text(md, encoding="utf-8")
                print(f"     📄 {agg_path}")
                print(f"     {len(drivers)} drivers "
                      f"({sum(1 for d in drivers if hasattr(d, 'consensus_level') and d.consensus_level in ('full', 'strong'))} strong consensus)")
            except Exception as e:
                print(f"     ❌ {e}")

        return results

    # ---- Phase 4: Consensus ----

    def generate_consensus(self, company: str, reports: list[dict],
                           aggregated_md: str = "") -> dict:
        """为一家公司生成共识汇总"""
        if len(reports) < 2:
            return {
                "company": company,
                "report_count": len(reports),
                "note": "Need ≥2 reports for consensus",
                "markdown": ""
            }

        print(f"  📊 Generating consensus: {company} ({len(reports)} reports)")

        banks = []
        for r in reports:
            pdf_name = r.get("pdf_name", "")
            bank = extract_bank_from_filename(pdf_name)
            banks.append(bank)

        # Prefer aggregated logic as input; fall back to raw report markdowns
        if aggregated_md:
            user_content = (
                f"Company: {company}\n"
                f"Banks covering: {', '.join(banks)}\n"
                f"Total reports: {len(reports)}\n\n"
                f"# 逻辑链聚合数据 #\n{aggregated_md}"
            )
        else:
            report_texts = []
            for r in reports:
                md_path = r.get("_md_path", "")
                if md_path and Path(md_path).exists():
                    md_text = Path(md_path).read_text(encoding="utf-8")
                    if len(md_text) > 4000:
                        md_text = md_text[:4000] + "\n\n[... truncated ...]"
                    report_texts.append(md_text)
            if not report_texts:
                return {"company": company, "report_count": len(reports),
                        "note": "No readable analysis found", "markdown": ""}
            user_content = (
                f"Company: {company}\n"
                f"Banks covering: {', '.join(banks)}\n"
                f"Total reports: {len(reports)}\n\n"
                + "\n\n---\n\n".join(
                    f"### Report {i + 1}\n{text}"
                    for i, text in enumerate(report_texts)
                )
            )

        from llm_client import call_llm

        user_prompt = user_content

        markdown, usage_info = call_llm(CONSENSUS_SYSTEM_PROMPT, user_prompt, max_tokens=4096)
        total_tokens = usage_info["total_tokens"]
        print(f"     Tokens: {total_tokens}  ~${total_tokens * 0.28 / 1_000_000:.4f}")

        return {
            "company": company,
            "report_count": len(reports),
            "banks": banks,
            "markdown": markdown,
            "usage": usage_info
        }

    def generate_all_consensus(self, groups: dict[str, list[dict]],
                               aggregated: dict[str, list] = None,
                               output_dir: Path = None) -> list[dict]:
        """为所有有 ≥2 报告的公司生成共识（使用聚合逻辑链）"""
        print(f"\n{'=' * 60}")
        print(f"📊 PHASE 4: Consensus Summaries ({len(groups)} companies)")
        print(f"{'=' * 60}")

        if output_dir is None:
            output_dir = self.report_dir
        if aggregated is None:
            aggregated = {}

        results = []
        for company, reports in sorted(groups.items()):
            if len(reports) < 2:
                print(f"  ⏭️  {company}: only {len(reports)} report, skipping consensus")
                continue

            # Try to use aggregated logic as input
            agg_md = ""
            agg_path = output_dir / f"AGGREGATED_{company.replace(' ', '_').replace('.', '')}_{self.date_str}.md"
            if agg_path.exists():
                agg_md = agg_path.read_text(encoding="utf-8")
            elif aggregated.get(company):
                from logic_aggregator import format_aggregated_markdown
                agg_md = format_aggregated_markdown(company, aggregated[company])

            consensus = self.generate_consensus(company, reports, aggregated_md=agg_md)
            results.append(consensus)

            # 保存共识 Markdown
            safe_name = company.replace(" ", "_").replace(".", "")
            md_path = output_dir / f"CONSENSUS_{safe_name}_{self.date_str}.md"
            md_path.write_text(consensus.get("markdown", ""), encoding="utf-8")
            print(f"     📄 {md_path}")

            # 保存 JSON
            json_path = output_dir / f"CONSENSUS_{safe_name}_{self.date_str}.json"
            json_result = {
                k: v for k, v in consensus.items()
                if k != "markdown"
            }
            json_path.write_text(
                json.dumps(json_result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8"
            )

        return results

    # ---- Phase 4.5: Industry Report Update ----

    def run_industry_report_update(self, groups: dict[str, list[dict]]):
        """通过 Company↔Industry Matrix 触发行业报告更新。

        逻辑：任何有新 driver 的公司 → Matrix 查行业 → 触发该行业更新。
        不再维护 hardcoded keyword mapping。
        """
        # Load Matrix
        matrix_path = PROJECT_DIR / "industry_matrix.json"
        if not matrix_path.exists():
            print("  ⚠️  industry_matrix.json not found, skipping industry updates")
            return

        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        co_map = matrix.get("companies", {})

        # Collect industries from companies with new drivers
        from logic_store import load_aggregated_drivers

        industry_triggers = set()
        for company in groups:
            drivers = load_aggregated_drivers(company)
            if not drivers:
                continue
            co_info = co_map.get(company)
            if co_info:
                slug = co_info.get("industry_slug", "")
                if slug:
                    industry_triggers.add(slug)

        if not industry_triggers:
            return

        print(f"\n{'=' * 60}")
        print(f"📊 PHASE 4.5: Industry Report Updates ({len(industry_triggers)} industries)")
        print(f"   Triggered by: {', '.join(sorted(groups.keys()))}")
        print(f"{'=' * 60}")

        for slug in sorted(industry_triggers):
            print(f"  🤖 Extracting: {slug}")
            try:
                result = subprocess.run(
                    [sys.executable, str(PROJECT_DIR / "industry_db.py"), "extract",
                     "--industry", slug],
                    cwd=str(PROJECT_DIR),
                    capture_output=True, text=True, timeout=300
                )
                print(result.stdout.strip())
                if result.stderr.strip():
                    print(f"     ⚠️  {result.stderr.strip()[-200:]}")
            except Exception as e:
                print(f"     ❌ {slug}: {e}")

        # Step 2: regenerate industry markdown reports
        for slug in sorted(industry_triggers):
            print(f"  🔄 Regenerating report: {slug}")
            try:
                result = subprocess.run(
                    [sys.executable, str(PROJECT_DIR / "industry_report.py"), slug],
                    cwd=str(PROJECT_DIR),
                    capture_output=True, text=True, timeout=600
                )
                if result.returncode == 0:
                    print(f"     ✅ {slug} updated")
                else:
                    print(f"     ⚠️  {slug} failed: {result.stderr[-100:]}")
            except Exception as e:
                print(f"     ❌ {slug}: {e}")

    # ---- Main ----

    def run(self, skip_download: bool = False, skip_analysis: bool = False,
            skip_logic: bool = False, skip_aggregation: bool = False,
            skip_consensus: bool = False) -> dict:
        """运行完整 pipeline"""
        start = datetime.now()
        summary = {
            "date": self.date_str,
            "started_at": start.isoformat(),
            "phases": {}
        }

        # Phase 1: Download
        if not skip_download:
            ok = self.run_download()
            summary["phases"]["download"] = "ok" if ok else "failed"

        # Phase 2: Analyze
        if not skip_analysis:
            analyses = self.run_analysis()
            summary["phases"]["analysis"] = f"{len(analyses)} analyzed"
        else:
            analyses = self.collect_analyses()
            print(f"\n📋 Loaded {len(analyses)} existing analyses")

        # Phase 2.1: Auto-index new analyses
        if not skip_analysis:
            try:
                from report_index import ReportIndex
                idx = ReportIndex()
                indexed = 0
                for a in analyses:
                    json_path = a.get("_path", "") or a.get("_json_path", "")
                    if json_path and Path(json_path).exists():
                        doc_id = idx.index_analysis(json_path)
                        if doc_id:
                            indexed += 1
                if indexed:
                    print(f"  📚 Auto-indexed {indexed} new reports to report_index.db")
                    summary["phases"]["indexing"] = f"{indexed} indexed"
                idx.close()
            except Exception as e:
                print(f"  ⚠️ Auto-index warning: {e}")

        # Phase 2.5: Logic Extraction
        if not skip_logic:
            n = self.run_logic_extraction()
            summary["phases"]["logic_extraction"] = f"{n} reports processed"

        # Phase 3: Group
        groups = self.group_by_company(analyses)
        print(f"\n📁 PHASE 3: Groups — {len(groups)} companies, "
              f"{sum(len(v) for v in groups.values())} reports")
        for company, reports in sorted(groups.items(),
                                        key=lambda x: len(x[1]), reverse=True):
            banks = []
            for r in reports:
                pdf_name = r.get("pdf_name", "")
                bank = extract_bank_from_filename(pdf_name)
                banks.append(bank)
            print(f"  {company}: {len(reports)} reports ({', '.join(banks)})")
        summary["phases"]["groups"] = {c: len(r) for c, r in groups.items()}

        # Phase 3.5: Logic Aggregation
        aggregated = {}
        if not skip_aggregation:
            aggregated = self.run_logic_aggregation(groups)
            summary["phases"]["logic_aggregation"] = f"{len(aggregated)} companies aggregated"

        # Phase 4: Consensus
        if not skip_consensus:
            consensus_results = self.generate_all_consensus(groups, aggregated=aggregated)
            summary["phases"]["consensus"] = f"{len(consensus_results)} generated"
        else:
            consensus_results = []

        # Phase 4.5: Industry Report Update (auto-triggered by new logic chains)
        if not skip_aggregation:
            self.run_industry_report_update(groups)
            summary["phases"]["industry_update"] = "triggered"

        # Phase 4.6: Update company persistent reports
        if not skip_consensus and consensus_results:
            try:
                AI_SEMI_DIR = Path.home() / "hermes_reports" / "ai_semiconductor_research"
                from report_index import ReportIndex
                idx = ReportIndex()
                updated = 0
                for cr in consensus_results:
                    company = cr.get("company", "")
                    markdown = cr.get("markdown", "")
                    if not company or not markdown or company == "Unknown":
                        continue
                    safe = company.replace(" ", "_").replace("/", "_").replace(".", "")
                    co_dir = AI_SEMI_DIR / safe
                    co_dir.mkdir(parents=True, exist_ok=True)
                    overview_path = co_dir / f"{safe}_Overview.md"
                    overview_path.write_text(markdown, encoding="utf-8")
                    idx.update_company_overview_path(company, str(overview_path))
                    updated += 1
                if updated:
                    print(f"  📄 Updated {updated} company persistent reports")
                    summary["phases"]["company_reports"] = f"{updated} updated"
                idx.close()
            except Exception as e:
                print(f"  ⚠️ Company report update warning: {e}")

        # Phase 6: Expiration cleanup
        try:
            from report_index import ReportIndex
            idx = ReportIndex()
            exp = idx.mark_expired(dry_run=False)
            if exp["expired_count"] > 0:
                print(f"  🧹 Marked {exp['expired_count']} expired documents")
            removed = idx.remove_expired_overviews(dry_run=False)
            if removed:
                print(f"  🗑️ Removed {len(removed)} expired overview files")
            idx.close()
        except Exception as e:
            print(f"  ⚠️ Expiration cleanup warning: {e}")

        # Summary
        elapsed = (datetime.now() - start).total_seconds()
        summary["elapsed_seconds"] = elapsed
        summary["consensus_count"] = len(consensus_results)

        print(f"\n{'=' * 60}")
        print(f"✅ Pipeline complete in {elapsed:.0f}s")
        print(f"   Reports analyzed: {len(analyses)}")
        print(f"   Companies grouped: {len(groups)}")
        print(f"   Consensus generated: {len(consensus_results)}")
        print(f"{'=' * 60}")

        return summary


# ============ Lock ============

LOCK_FILE = PROJECT_DIR / ".pipeline.lock"


def _acquire_lock():
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        os.close(fd)
        return None


# ============ CLI ============

if __name__ == "__main__":
    lock_fd = _acquire_lock()
    if lock_fd is None:
        print("❌ Pipeline lock held by another instance, exiting.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Investment Research Pipeline"
    )
    parser.add_argument("--date", help="Target date (YYYYMMDD), default: today")
    parser.add_argument("--dir", help="Report directory (overrides --date path)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip Phase 1 (download)")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Skip Phase 2 (analysis), use existing results")
    parser.add_argument("--skip-logic", action="store_true",
                        help="Skip Phase 2.5 (logic extraction)")
    parser.add_argument("--skip-aggregation", action="store_true",
                        help="Skip Phase 3.5 (logic aggregation)")
    parser.add_argument("--skip-consensus", action="store_true",
                        help="Skip Phase 4 (consensus)")
    parser.add_argument("--download-only", action="store_true",
                        help="Only download, skip analysis & consensus")
    parser.add_argument("--logic-only", action="store_true",
                        help="Only run logic extraction + aggregation + consensus")
    parser.add_argument("--push-wechat", action="store_true",
                        help="Push critical alert draft to WeChat after pipeline")
    parser.add_argument("--send-email", action="store_true",
                        help="Send daily report email after pipeline")
    parser.add_argument("--bearish-alert", action="store_true",
                        help="Scan bearish signals for tracked companies and email alert")

    args = parser.parse_args()

    pipeline = Pipeline(date_str=args.date, report_dir=args.dir)

    if args.download_only:
        pipeline.run_download()
    elif args.logic_only:
        pipeline.run(
            skip_download=True, skip_analysis=True,
            skip_logic=False, skip_aggregation=False, skip_consensus=False
        )
    else:
        pipeline.run(
            skip_download=args.skip_download,
            skip_analysis=args.skip_analysis,
            skip_logic=args.skip_logic,
            skip_aggregation=args.skip_aggregation,
            skip_consensus=args.skip_consensus
        )

    if args.push_wechat:
        from wechat_push import scan_critical_alerts, generate_draft_html, push_draft, send_preview
        alerts = scan_critical_alerts()
        if alerts:
            html = generate_draft_html(alerts)
            try:
                media_id = push_draft("Hermes Alert", html)
                send_preview(media_id)
            except Exception as e:
                print(f"⚠️  WeChat push failed: {e}")

    if args.send_email:
        from pipeline_report import collect_report_data, format_html_report, send_email
        data = collect_report_data(pipeline.date_str)
        if "error" not in data:
            html = format_html_report(data)
            send_email(html, pipeline.date_str)

    if args.bearish_alert:
        print(f"\n🔴 Scanning bearish signals for tracked companies...")
        try:
            result = subprocess.run(
                [sys.executable, str(PROJECT_DIR / "bearish_alert.py"),
                 "--hours", "24"],
                cwd=str(PROJECT_DIR),
                capture_output=True, text=True, timeout=30
            )
            print(result.stdout.strip())
            if result.stderr.strip():
                print(f"  ⚠️  {result.stderr.strip()[-200:]}")
        except Exception as e:
            print(f"  ❌ bearish_alert failed: {e}")
