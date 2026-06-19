#!/usr/bin/env python3
"""
Backfill — 将所有已有 _analysis.json 索引到 report_index.db

用法:
  python3 backfill.py                  # 完整回填
  python3 backfill.py --dry-run        # 预览（不写入）
  python3 backfill.py --resume         # 断点续传（跳过已索引）
  python3 backfill.py --rebuild-fts    # 回填后重建 FTS
"""

import json
import argparse
from pathlib import Path
from report_index import ReportIndex, REPORT_BASE


def backfill_all(dry_run: bool = False, resume: bool = False,
                 rebuild_fts: bool = True) -> dict:
    idx = ReportIndex()
    idx.sync_entity_registry()

    all_jsons = sorted(REPORT_BASE.rglob("*_analysis.json"))
    stats = {
        "scanned": len(all_jsons),
        "indexed": 0,
        "skipped": 0,
        "errors": 0,
        "independent": 0,
    }

    print(f"📚 Found {len(all_jsons)} analysis JSON files")
    if dry_run:
        print("   (dry-run mode, no writes)")
    if resume:
        print("   (resume mode, skipping already indexed)")

    for i, jp in enumerate(all_jsons, 1):
        try:
            if dry_run:
                stats["skipped"] += 1
                continue

            if resume:
                data = json.loads(jp.read_text(encoding="utf-8"))
                pdf_name = data.get("pdf_name", jp.stem.replace("_analysis", ""))
                existing = idx.conn.execute(
                    "SELECT id FROM documents WHERE pdf_name=?", (pdf_name,)
                ).fetchone()
                if existing:
                    stats["skipped"] += 1
                    continue

            doc_id = idx.index_analysis(str(jp))
            if doc_id:
                stats["indexed"] += 1
            else:
                stats["errors"] += 1

        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"  ❌ [{i}/{len(all_jsons)}] {jp.name}: {e}")

        if i % 50 == 0:
            print(f"  [{i}/{len(all_jsons)}] indexed={stats['indexed']} skipped={stats['skipped']} errors={stats['errors']}")

    # Also index independent research (.pages, standalone .md)
    for ext in ["*.pages", "*.md"]:
        for fp in sorted(REPORT_BASE.rglob(ext)):
            # Skip analysis/generated files
            if fp.stem.endswith("_analysis") or fp.stem.startswith("CONSENSUS_") or \
               fp.stem.startswith("AGGREGATED_") or fp.stem.startswith("INDUSTRY_"):
                continue
            if dry_run:
                stats["independent"] += 1
                continue
            try:
                doc_id = idx.index_independent_research(str(fp))
                if doc_id:
                    stats["independent"] += 1
            except Exception:
                pass

    if not dry_run and rebuild_fts:
        n = idx.rebuild_fts()
        print(f"  🔄 FTS rebuilt: {n} documents")
        idx.update_report_counts()

    idx.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill report index")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--resume", action="store_true", help="Skip already indexed")
    parser.add_argument("--no-rebuild-fts", action="store_true", help="Don't rebuild FTS")
    args = parser.parse_args()

    stats = backfill_all(
        dry_run=args.dry_run,
        resume=args.resume,
        rebuild_fts=not args.no_rebuild_fts
    )

    print(f"\n{'='*50}")
    print(f"Backfill complete:")
    print(f"  Scanned:  {stats['scanned']}")
    print(f"  Indexed:  {stats['indexed']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Errors:   {stats['errors']}")
    print(f"  Independent: {stats.get('independent', 0)}")


if __name__ == "__main__":
    main()
