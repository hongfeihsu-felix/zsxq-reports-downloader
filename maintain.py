#!/usr/bin/env python3
"""
Report Maintenance - 报告生命周期管理

功能：
  - 过期检测/清理（config 中 maintenance.report_expire_days）
  - 去重（同银行 + 同公司 + 相近日期 → 保留最新）
  - 归档（移至 archive/ 目录）而非直接删除
  - 分析产物一并清理（_analysis.md/json, CONSENSUS_*.md, INDUSTRY_*.md）

用法：
  python3 maintain.py --status            # 查看报告年龄分布
  python3 maintain.py --clean             # 归档过期报告 (safe, moves to archive/)
  python3 maintain.py --dedup             # 查找并去重
  python3 maintain.py --auto              # clean + dedup
  python3 maintain.py --force-delete      # 删除而非归档 (⚠️ 不可逆)
"""

import os
import re
import sys
import json
import shutil
import hashlib
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
ARCHIVE_DIR = REPORT_BASE / "archive"


def load_config():
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def get_expire_days() -> int:
    cfg = load_config()
    return cfg.get("maintenance", {}).get("report_expire_days", 90)


def get_consensus_expire_days() -> int:
    cfg = load_config()
    return cfg.get("maintenance", {}).get("consensus_expire_days", 7)


def file_age_days(path: Path) -> int:
    """文件距今多少天"""
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime).days


def extract_date_from_filename(name: str) -> str:
    """从文件名提取日期 (YYMMDD 格式，末尾6位数字)"""
    m = re.search(r'(\d{6})(?:\.pdf|$)', name)
    return m.group(1) if m else ""


def parse_date(date_str: str) -> datetime:
    """解析 YYMMDD 日期"""
    if len(date_str) == 6:
        try:
            return datetime(2000 + int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6]))
        except (ValueError, IndexError):
            return datetime.min
    return datetime.min


def extract_bank(name: str) -> str:
    """从文件名提取银行"""
    m = re.match(r'^([A-Za-z\s&.]+?)[-（(]', name)
    return m.group(1).strip() if m else ""


def extract_company(name: str) -> str:
    """从文件名提取公司名（银行名之后的内容）"""
    m = re.match(r'^[A-Za-z\s&.]+?[-](.+?)[（(]', name)
    if m:
        return m.group(1).strip()
    return ""


def hash_file(path: Path) -> str:
    """文件 MD5"""
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def scan_reports() -> dict:
    """扫描所有报告及其元数据"""
    reports = []
    for pdf in REPORT_BASE.rglob("*.pdf"):
        if "archive" in str(pdf):
            continue
        if pdf.stem.endswith("_analysis"):
            continue

        name = pdf.name
        date_str = extract_date_from_filename(name)
        report_date = parse_date(date_str) if date_str else datetime.min
        age = file_age_days(pdf)

        reports.append({
            "path": pdf,
            "name": name,
            "date_str": date_str,
            "report_date": report_date,
            "age_days": age,
            "bank": extract_bank(name),
            "company": extract_company(name),
            "size_kb": pdf.stat().st_size / 1024,
            "hash": None  # lazy
        })

    return {"reports": reports, "total": len(reports)}


def find_related_files(pdf_path: Path) -> list[Path]:
    """找出 PDF 相关的所有分析产物"""
    related = []
    stem = pdf_path.stem
    parent = pdf_path.parent

    for pattern in [
        f"{stem}_analysis.md",
        f"{stem}_analysis.json",
    ]:
        f = parent / pattern
        if f.exists():
            related.append(f)

    return related


# ============ Status ============

def cmd_status():
    """展示报告年龄分布和维护状态"""
    cfg = load_config()
    expire_days = get_expire_days()
    consensus_expire = get_consensus_expire_days()

    print(f"\n{'=' * 60}")
    print(f"  🗄️  Report Maintenance Status")
    print(f"{'=' * 60}")
    print(f"  Base:       {REPORT_BASE}")
    print(f"  Expire:     {expire_days} days (company reports)")
    print(f"  Consensus:  {consensus_expire} days (consensus/industry)")

    data = scan_reports()
    reports = data["reports"]
    total = data["total"]

    # 年龄分布
    buckets = {"0-7d": 0, "8-30d": 0, "31-90d": 0, "91-180d": 0, "180d+": 0}
    expired = 0
    for r in reports:
        age = r["age_days"]
        if age <= 7:
            buckets["0-7d"] += 1
        elif age <= 30:
            buckets["8-30d"] += 1
        elif age <= 90:
            buckets["31-90d"] += 1
        elif age <= 180:
            buckets["91-180d"] += 1
        else:
            buckets["180d+"] += 1

        if age > expire_days:
            expired += 1

    print(f"\n  PDFs: {total} total")
    print(f"  Age Distribution:")
    for bucket, count in buckets.items():
        bar = "█" * (count * 2)
        print(f"    {bucket:<10} {count:>4}  {bar}")

    print(f"\n  ⚠️  Expired (> {expire_days}d): {expired} PDFs")

    # 分析产物
    analysis_count = len(list(REPORT_BASE.rglob("*_analysis.md")))
    consensus_count = len(list(REPORT_BASE.rglob("CONSENSUS_*.md")))
    industry_count = len(list(REPORT_BASE.rglob("INDUSTRY_*.md")))

    print(f"\n  Artifacts:")
    print(f"    Company analyses:  {analysis_count}")
    print(f"    Consensus reports: {consensus_count}")
    print(f"    Industry reports:  {industry_count}")

    # 过期共识
    old_consensus = 0
    for f in REPORT_BASE.rglob("CONSENSUS_*.md"):
        if file_age_days(f) > consensus_expire:
            old_consensus += 1
    if old_consensus:
        print(f"    ⚠️  Stale consensus: {old_consensus}")

    # 重复检测
    dup_groups = find_duplicates(reports)
    if dup_groups:
        dup_count = sum(len(g) - 1 for g in dup_groups)
        print(f"\n  ⚠️  Potential duplicates: {dup_count} files in {len(dup_groups)} groups")

    # 无分析产物
    unanalyzed = 0
    for r in reports:
        md = r["path"].parent / f"{r['path'].stem}_analysis.md"
        if not md.exists():
            unanalyzed += 1
    print(f"\n  📋 Unanalyzed: {unanalyzed}/{total}")

    # 磁盘使用
    total_size = sum(r["size_kb"] for r in reports) / 1024
    artifact_size = sum(
        f.stat().st_size for f in REPORT_BASE.rglob("*_analysis.md")
    ) / 1024 / 1024
    print(f"\n  💾 Disk: {total_size:.1f} MB PDFs + {artifact_size:.1f} MB artifacts")
    print(f"{'=' * 60}\n")


# ============ Dedup ============

def find_duplicates(reports: list[dict]) -> list[list[dict]]:
    """找出重复报告组 (同银行 + 同公司 + 30天内)"""
    groups: dict[str, list[dict]] = defaultdict(list)

    for r in reports:
        if not r["bank"] or not r["company"]:
            continue
        key = f"{r['bank'].lower()}||{r['company'].lower()}"
        groups[key].append(r)

    dup_groups = []
    for key, items in groups.items():
        if len(items) >= 2:
            # 检查日期是否在 30 天内
            items.sort(key=lambda x: x["report_date"], reverse=True)
            newest = items[0]
            cluster = [newest]
            for item in items[1:]:
                if item["report_date"] != datetime.min and newest["report_date"] != datetime.min:
                    delta = abs((newest["report_date"] - item["report_date"]).days)
                    if delta <= 30:
                        cluster.append(item)
            if len(cluster) >= 2:
                dup_groups.append(cluster)

    return dup_groups


def cmd_dedup(dry_run: bool = True, force_delete: bool = False):
    """查找并去重"""
    data = scan_reports()
    dup_groups = find_duplicates(data["reports"])

    if not dup_groups:
        print("✅ No duplicates found")
        return

    total_dup = sum(len(g) - 1 for g in dup_groups)
    print(f"\n⚠️  Found {total_dup} potential duplicates in {len(dup_groups)} groups:\n")

    removed = 0
    for i, group in enumerate(dup_groups, 1):
        newest = group[0]
        old = group[1:]
        print(f"  Group {i}: {newest['bank']} — {newest['company']}")
        print(f"    Keep:  {newest['name'][:60]} ({newest['date_str']})")
        for item in old:
            print(f"    {'🗑️' if not dry_run else '🔍'}  {item['name'][:60]} ({item['date_str']})")

            if not dry_run:
                if force_delete:
                    item["path"].unlink(missing_ok=True)
                    for related in find_related_files(item["path"]):
                        related.unlink(missing_ok=True)
                    print(f"       → Deleted")
                else:
                    dest = ARCHIVE_DIR / item["path"].name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item["path"]), str(dest))
                    for related in find_related_files(item["path"]):
                        rel_dest = ARCHIVE_DIR / related.name
                        shutil.move(str(related), str(rel_dest))
                    print(f"       → Archived to {ARCHIVE_DIR}")
                removed += 1
        print()

    if dry_run:
        print(f"  Run with --dedup --no-dry-run to execute ({total_dup} files)")
    else:
        print(f"  ✅ Removed/archived {removed} duplicate files")


# ============ Clean ============

def cmd_clean(dry_run: bool = True, force_delete: bool = False):
    """清理过期报告"""
    expire_days = get_expire_days()
    consensus_expire = get_consensus_expire_days()
    data = scan_reports()

    expired_pdfs = [r for r in data["reports"] if r["age_days"] > expire_days]
    expired_consensus = [
        f for f in REPORT_BASE.rglob("CONSENSUS_*.md")
        if file_age_days(f) > consensus_expire
    ]
    expired_industry = [
        f for f in REPORT_BASE.rglob("INDUSTRY_*.md")
        if file_age_days(f) > consensus_expire
    ]

    total_expired = len(expired_pdfs) + len(expired_consensus) + len(expired_industry)

    print(f"\n{'=' * 60}")
    print(f"  🧹 Maintenance Clean")
    print(f"{'=' * 60}")
    print(f"  Threshold: {expire_days}d (PDFs) / {consensus_expire}d (consensus)")
    print(f"  Mode: {'DRY RUN' if dry_run else ('DELETE' if force_delete else 'ARCHIVE')}")

    if not total_expired:
        print(f"\n  ✅ Nothing to clean. All reports within expiration window.")
        return

    print(f"\n  To process: {total_expired} items")
    print(f"    Expired PDFs:       {len(expired_pdfs)}")
    print(f"    Stale consensus:   {len(expired_consensus)}")
    print(f"    Stale industry:    {len(expired_industry)}")

    if dry_run:
        print(f"\n  Run with --clean --no-dry-run to execute")
        return

    processed = 0
    for r in expired_pdfs:
        pdf = r["path"]
        related = find_related_files(pdf)

        if force_delete:
            pdf.unlink(missing_ok=True)
            for rel in related:
                rel.unlink(missing_ok=True)
        else:
            dest = ARCHIVE_DIR / pdf.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(dest))
            for rel in related:
                shutil.move(str(rel), str(ARCHIVE_DIR / rel.name))
        processed += 1

    for f in expired_consensus + expired_industry:
        if force_delete:
            f.unlink(missing_ok=True)
        else:
            shutil.move(str(f), str(ARCHIVE_DIR / f.name))
        processed += 1

    action = "Deleted" if force_delete else "Archived"
    print(f"\n  ✅ {action} {processed} items → {ARCHIVE_DIR if not force_delete else '(deleted)'}")
    print(f"{'=' * 60}\n")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report Maintenance")
    parser.add_argument("--status", action="store_true", help="Show report health status")
    parser.add_argument("--clean", action="store_true", help="Remove/archive expired reports")
    parser.add_argument("--dedup", action="store_true", help="Find and remove duplicates")
    parser.add_argument("--auto", action="store_true", help="Run clean + dedup")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute (default is dry run)")
    parser.add_argument("--force-delete", action="store_true",
                        help="Permanently delete instead of archiving")

    args = parser.parse_args()

    dry_run = not args.no_dry_run

    if args.status or (not args.clean and not args.dedup and not args.auto):
        cmd_status()

    if args.dedup or args.auto:
        cmd_dedup(dry_run=dry_run, force_delete=args.force_delete)

    if args.clean or args.auto:
        cmd_clean(dry_run=dry_run, force_delete=args.force_delete)
