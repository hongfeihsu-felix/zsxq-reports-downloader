#!/usr/bin/env python3
"""
Chokepoint Alert — P0-P11 卡脖子技术报告联动扫描

当 L4 材料供应商有新研报时，通过反向索引快速识别受影响的下游供应链环节。

用法：
  python3 chokepoint_alert.py --date 2026-06-07           # 扫描当日所有新报告
  python3 chokepoint_alert.py --company NAMICS              # 查看某L4公司的下游影响链
  python3 chokepoint_alert.py --list                        # 列出所有跟踪中的L4公司
  python3 chokepoint_alert.py --build-tags                  # 输出所有 report_tags 供研报系统使用
"""

import json
import argparse
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
INDEX_PATH = Path(__file__).parent / "chokepoint_index.json"


def load_index() -> dict:
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def scan_date(date_str: str, index: dict) -> list[dict]:
    """扫描指定日期的所有分析报告，匹配 L4 公司关键词。"""
    date_dir = REPORT_BASE / date_str
    if not date_dir.exists():
        print(f"Report directory not found: {date_dir}")
        return []

    alerts = []
    entries = index["entries"]
    # Build tag → company mapping
    tag_map = defaultdict(set)
    for name, entry in entries.items():
        for tag in entry.get("report_tags", []):
            tag_map[tag.lower()].add(name)

    for md_file in sorted(date_dir.rglob("*_analysis.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        text_lower = text.lower()
        matched_companies = set()

        for tag, companies in tag_map.items():
            if len(tag) < 3:
                continue
            if tag in text_lower:
                matched_companies.update(companies)

        if matched_companies:
            # Extract report metadata
            source = "unknown"
            m = re.search(r"来源[：:]\s*(.+)", text)
            if m:
                source = m.group(1).strip()

            company = "unknown"
            m = re.search(r"公司[：:]\s*(.+)", text)
            if m:
                company = m.group(1).strip()

            alerts.append({
                "report": str(md_file.relative_to(date_dir)),
                "source": source,
                "company": company,
                "matched_l4": sorted(matched_companies),
            })

    return alerts


def show_company_impact(name: str, index: dict):
    """展示单个 L4 公司的完整下游影响链。"""
    entry = index["entries"].get(name)
    if not entry:
        # fuzzy match by name_cn
        for k, v in index["entries"].items():
            if v.get("name_cn", "").lower() == name.lower():
                entry = v
                name = k
                break
    if not entry:
        print(f"L4 company '{name}' not found in chokepoint index")
        return

    print(f"\n=== {entry['name_cn']} ({name}) ===")
    print(f"  国家: {entry['country']}")
    print()
    print("  卡脖子材料:")
    for m in entry["materials"]:
        print(f"    [{m['type']}] {m['material']}")
    print()
    print(f"  下游影响链 ({len(entry['affects'])} 条):")
    for a in entry["affects"]:
        icon = "🔴" if a["impact_level"] == "critical" else "🟡"
        print(f"    {icon} L{a['layer_id']} {a['layer_name']}")
        print(f"       → {a['supplier']} → {a['component']}")
        print(f"       影响级别: {a['impact_level']}")
    if entry.get("downstream_tickers"):
        print(f"\n  下游公司/标的: {', '.join(entry['downstream_tickers'])}")
    if entry.get("report_tags"):
        print(f"\n  报告追踪标签: {', '.join(entry['report_tags'])}")
    print()


def render_alerts_markdown(date_str: str, alerts: list[dict], index: dict) -> str:
    """生成影响链汇总报告 Markdown。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# L4 卡脖子技术 — 报告影响链扫描",
        f"",
        f"**扫描日期:** {date_str}",
        f"**生成时间:** {now}",
        f"**命中报告:** {len(alerts)} 份",
        f"",
        "---",
        "",
    ]

    if not alerts:
        lines.append("本日未发现 L4 卡脖子公司相关的报告。")
        return "\n".join(lines)

    for alert in alerts:
        lines.append(f"## {alert['report']}")
        lines.append(f"**来源:** {alert['source']} | **目标公司:** {alert['company']}")
        lines.append("")
        lines.append(f"**命中 L4 公司:** {', '.join(alert['matched_l4'])}")
        lines.append("")

        for l4_name in alert["matched_l4"]:
            entry = index["entries"].get(l4_name)
            if not entry:
                continue
            lines.append(f"### ⚠ {entry['name_cn']} ({l4_name})")
            for a in entry.get("affects", [])[:3]:
                lines.append(f"- → L{a['layer_id']} {a['layer_name']}"
                             f" → {a['supplier']} → {a['component']}"
                             f" ({a['impact_level']})")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ============ Alert Persistence & Verification ============

import sqlite3

ALERT_DB = Path(__file__).parent / "chokepoint_alerts.db"


def _get_alert_conn():
    conn = sqlite3.connect(str(ALERT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_alert_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chokepoint_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            l4_company TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            source_report TEXT NOT NULL,
            source_company TEXT NOT NULL DEFAULT '',
            downstream_entities TEXT NOT NULL DEFAULT '[]',
            impact_chain_json TEXT NOT NULL DEFAULT '[]',
            verified INTEGER DEFAULT 0,
            verified_date TEXT,
            verified_evidence TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_stats (
            l4_company TEXT PRIMARY KEY,
            total_alerts INTEGER NOT NULL DEFAULT 0,
            verified_alerts INTEGER NOT NULL DEFAULT 0,
            noise_alerts INTEGER NOT NULL DEFAULT 0,
            last_alert_date TEXT,
            quality_score REAL DEFAULT 0
        )
    """)
    conn.commit()


def persist_alerts(date_str: str, alerts: list[dict], index: dict):
    """持久化扫描到的 L4 警报."""
    conn = _get_alert_conn()
    _ensure_alert_table(conn)

    for alert in alerts:
        for l4_name in alert["matched_l4"]:
            entry = index["entries"].get(l4_name, {})
            downstream = json.dumps(entry.get("downstream_tickers", []))
            impact_chain = json.dumps(entry.get("affects", []))

            conn.execute("""
                INSERT OR IGNORE INTO chokepoint_alerts
                (l4_company, alert_date, source_report, source_company,
                 downstream_entities, impact_chain_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (l4_name, date_str, alert["report"], alert["company"],
                  downstream, impact_chain))

            # Update stats
            conn.execute("""
                INSERT INTO alert_stats (l4_company, total_alerts, last_alert_date)
                VALUES (?, 1, ?)
                ON CONFLICT(l4_company) DO UPDATE SET
                    total_alerts = total_alerts + 1,
                    last_alert_date = ?
            """, (l4_name, date_str, date_str))

    conn.commit()
    conn.close()


def verify_alerts(days: int = 30):
    """扫描最近的 logic chains 验证历史警报是否被下游印证."""
    conn = _get_alert_conn()
    _ensure_alert_table(conn)

    logic_conn = sqlite3.connect(str(Path(__file__).parent / "logic_chains.db"))
    logic_conn.row_factory = sqlite3.Row

    # Get unverified alerts
    alerts = conn.execute(
        "SELECT id, l4_company, alert_date, downstream_entities FROM chokepoint_alerts WHERE verified = 0"
    ).fetchall()

    verified_count = 0
    for alert in alerts:
        entities = json.loads(alert["downstream_entities"])
        if not entities:
            continue

        # Check if any downstream entity appeared in recent impacts
        placeholders = ",".join("?" for _ in entities)
        rows = logic_conn.execute(f"""
            SELECT DISTINCT lc.date, lc.company, i.entity, i.effect
            FROM logic_chains lc
            JOIN impacts i ON i.chain_id = lc.id
            WHERE i.entity IN ({placeholders})
            AND lc.date > ?
            ORDER BY lc.date DESC LIMIT 1
        """, (*entities, alert["alert_date"])).fetchall()

        if rows:
            evidence = json.dumps([dict(r) for r in rows])
            conn.execute("""
                UPDATE chokepoint_alerts
                SET verified = 1, verified_date = date('now'), verified_evidence = ?
                WHERE id = ?
            """, (evidence, alert["id"]))
            verified_count += 1

    # Update quality scores
    conn.execute("""
        UPDATE alert_stats SET
            verified_alerts = (SELECT COUNT(*) FROM chokepoint_alerts WHERE l4_company = alert_stats.l4_company AND verified = 1),
            quality_score = CASE WHEN total_alerts > 0
                THEN ROUND(CAST(verified_alerts AS REAL) / total_alerts * 100, 1)
                ELSE 0 END
    """)
    conn.commit()

    logic_conn.close()
    conn.close()
    return verified_count


def get_alert_stats() -> list[dict]:
    """获取 L4 警报质量统计."""
    conn = _get_alert_conn()
    _ensure_alert_table(conn)
    rows = conn.execute(
        "SELECT * FROM alert_stats ORDER BY total_alerts DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Chokepoint Alert — L4 卡脖子技术报告联动")
    parser.add_argument("--date", help="扫描指定日期的报告 (YYYY-MM-DD)")
    parser.add_argument("--company", help="查看某L4公司的下游影响链")
    parser.add_argument("--list", action="store_true", help="列出所有跟踪中的L4公司")
    parser.add_argument("--build-tags", action="store_true", help="输出所有 report_tags")
    parser.add_argument("--verify", action="store_true", help="验证历史警报是否被下游印证")
    parser.add_argument("--alert-stats", action="store_true", help="查看 L4 警报质量统计")
    parser.add_argument("--output", "-o", help="输出到文件 (默认 stdout)")

    args = parser.parse_args()
    index = load_index()

    if args.list:
        entries = sorted(index["entries"].items(), key=lambda x: x[1]["name_cn"])
        print(f"{'公司名(CN)':<16} {'Name':<30} {'Country':<6} {'Materials':>3} {'Affects':>3}")
        print("-" * 75)
        for name, e in entries:
            print(f"{e['name_cn']:<16} {name:<30} {e['country']:<6} {len(e['materials']):>3} {len(e['affects']):>3}")
        print(f"\nTotal: {len(entries)} L4 companies in {index['stats']['by_country']}")

    elif args.build_tags:
        tags = set()
        for name, e in index["entries"].items():
            tags.update(e.get("report_tags", []))
        for t in sorted(tags):
            print(t)

    elif args.company:
        show_company_impact(args.company, index)

    elif args.verify:
        n = verify_alerts()
        print(f"Verified {n} historical alerts against recent logic chains")

    elif args.alert_stats:
        stats = get_alert_stats()
        print(f"{'L4 Company':<25} {'Total':>6} {'Verified':>9} {'Noise':>6} {'Quality':>8}")
        print("-" * 60)
        for s in stats:
            print(f"{s['l4_company']:<25} {s['total_alerts']:>6} {s['verified_alerts']:>9} {s['noise_alerts']:>6} {s['quality_score']:>7.1f}%")

    elif args.date:
        alerts = scan_date(args.date, index)
        if not alerts:
            print(f"No L4 chokepoint mentions found in {args.date} reports.")

        report = render_alerts_markdown(args.date, alerts, index)
        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"Alert written to {args.output}")
        else:
            print(report)

        # Persist alerts
        persist_alerts(args.date, alerts, index)
        print(f"\nPersisted {sum(len(a['matched_l4']) for a in alerts)} alerts to DB")

        # Also save to report directory
        out_path = REPORT_BASE / args.date / f"CHOKEPOINT_ALERT_{args.date}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Alert saved to {out_path}")

    else:
        # Default: show stats summary
        stats = index["stats"]
        print(f"Chokepoint Index: {stats['total_companies']} L4 companies")
        print(f"Countries: {stats['by_country']}")
        print(f"Types: {stats['by_type']}")
        print(f"Monopoly: {stats['by_monopoly_level']}")
        print()
        print("Usage: chokepoint_alert.py --list | --company NAME | --date DATE | --build-tags")


if __name__ == "__main__":
    main()
