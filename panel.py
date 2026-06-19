#!/usr/bin/env python3
"""
Panel & Backtest Framework — 信号时间序列追踪 + 回测骨架

Phase A: Signal Tracking
  - 记录每个 driver 在各时间点的共识强度
  - 追踪 driver 生命周期：emerging → strengthening → consensus → fading
  - 识别信号拐点（从 isolated → full 的跳跃）

Phase B: Backtest Skeleton
  - 存储历史 driver + direction + confidence + date
  - 预留 outcome 字段（实际股价变动、营收结果等）
  - 当积累足够历史数据后可启用回测

用法：
  python3 panel.py --company MediaTek               # 单公司信号面板
  python3 panel.py --all                             # 全公司面板
  python3 panel.py --company MediaTek --timeline     # 时间线视图
  python3 panel.py --signals                         # 全市场信号扫描
"""

import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

from logic_store import (get_conn, getAllLogicChainsForCompany,
                          load_aggregated_drivers, query_by_company)
from logic_aggregator import aggregate

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"


# ============ Data Loading ============

def load_all_logic_chains() -> list[dict]:
    """Load ALL logic chains from JSON files with dates."""
    chains = []
    for f in sorted(REPORT_BASE.rglob("*_logic.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for item in (data if isinstance(data, list) else [data]):
                item["_source_file"] = str(f.relative_to(REPORT_BASE))
                chains.append(item)
        except (json.JSONDecodeError, KeyError):
            continue
    return chains


def load_historical_aggregations() -> dict[str, list[dict]]:
    """Load all AGGREGATED_*.md files grouped by company."""
    results = defaultdict(list)
    for f in sorted(REPORT_BASE.rglob("AGGREGATED_*.md")):
        company = f.stem.replace("AGGREGATED_", "").rsplit("_", 1)[0]
        date_str = f.stem.rsplit("_", 1)[-1]
        results[company].append({
            "date": date_str,
            "path": str(f.relative_to(REPORT_BASE)),
            "size_kb": round(f.stat().st_size / 1024, 1)
        })
    return dict(results)


# ============ Signal Lifecycle ============

SIGNAL_STATES = {
    "emerging": "🆕 首次出现",
    "strengthening": "📈 共识加强",
    "stable": "✅ 共识稳定",
    "diverging": "⚠️ 分歧扩大",
    "fading": "📉 共识减弱",
}


def analyze_signal_lifecycle(company: str) -> list[dict]:
    """Analyze driver lifecycle using Phase 3.5 aggregation (already clusters by LLM)."""
    from logic_aggregator import aggregate

    drivers = aggregate(company)
    if not drivers:
        return []

    signals = []
    for ad in drivers:
        # Determine lifecycle from consensus level + bank count
        if ad.consensus_level == "isolated":
            state = "emerging"
        elif ad.consensus_level == "partial":
            state = "strengthening"
        elif ad.consensus_level == "strong":
            state = "stable" if not ad.disputes else "diverging"
        elif ad.consensus_level == "full":
            state = "stable" if not ad.disputes else "diverging"
        else:
            state = "emerging"

        # Count direction distribution from evidence matrix banks
        directions = {}
        # All banks in this driver agreed on direction (from aggregation)
        directions[ad.direction] = len(ad.banks)

        signals.append({
            "driver": ad.canonical,
            "slug": ad.slug,
            "state": state,
            "dominant_direction": ad.direction,
            "bank_count": len(ad.banks),
            "mention_count": ad.report_count,
            "banks": ad.banks,
            "first_seen": "",  # populated if timeline data available
            "last_seen": "",
            "date_span_days": 0,
            "has_dispute": bool(ad.disputes),
            "disputes": ad.disputes,
            "directions": directions,
            "consensus_level": ad.consensus_level,
            "evidence_count": len(ad.evidence_matrix),
            "impact_count": len(ad.impact_graph),
            "change_consensus": ad.change_consensus[:120] if ad.change_consensus else "",
        })

    # Sort by lifecycle state
    state_order = {"emerging": 0, "diverging": 1, "strengthening": 2, "stable": 3, "fading": 4}
    signals.sort(key=lambda s: (state_order.get(s["state"], 99), -s["bank_count"]))

    return signals


# ============ Timeline ============

def build_timeline(chains: list[dict], company: str = "") -> list[dict]:
    """Build a date-by-date timeline of driver emergence for a company."""
    by_date = defaultdict(lambda: defaultdict(list))

    for c in chains:
        if company and c.get("company", "") != company:
            continue
        date = c.get("date", "")[:10]  # YYYY-MM-DD
        if not date:
            continue
        driver = c.get("driver", "")[:60]
        by_date[date][driver].append({
            "bank": c.get("bank", "?"),
            "direction": c.get("direction", "neutral"),
            "confidence": c.get("confidence", "medium"),
        })

    timeline = []
    for date in sorted(by_date.keys()):
        drivers = by_date[date]
        entry = {
            "date": date,
            "driver_count": len(drivers),
            "drivers": []
        }
        for driver, mentions in drivers.items():
            banks = [m["bank"] for m in mentions]
            directions = [m["direction"] for m in mentions]
            entry["drivers"].append({
                "driver": driver,
                "mention_count": len(mentions),
                "banks": sorted(set(banks)),
                "dominant_direction": max(set(directions), key=directions.count) if directions else "neutral",
            })
        entry["drivers"].sort(key=lambda d: d["mention_count"], reverse=True)
        timeline.append(entry)

    return timeline


# ============ Market Scan ============

def scan_all_signals() -> dict:
    """Scan all companies for emerging and diverging signals."""
    from logic_aggregator import aggregate
    from run_pipeline import normalize_company

    # Find all companies from logic data
    all_chains = load_all_logic_chains()
    by_company = defaultdict(list)
    for c in all_chains:
        company = c.get("company", "")
        if company and company != "None":
            canonical = normalize_company(company)
            by_company[canonical].append(c)

    scan = {"emerging": [], "diverging": [], "high_consensus": []}

    for company in sorted(by_company.keys()):
        signals = analyze_signal_lifecycle(company)
        for s in signals:
            entry = {**s, "company": company}
            if s["state"] == "emerging" and s["bank_count"] >= 1:
                scan["emerging"].append(entry)
            elif s["state"] == "diverging":
                scan["diverging"].append(entry)
            elif s["state"] == "stable" and s["bank_count"] >= 3:
                scan["high_consensus"].append(entry)

    # Sort each category
    scan["emerging"].sort(key=lambda s: -s["bank_count"])
    scan["diverging"].sort(key=lambda s: -s["bank_count"])
    scan["high_consensus"].sort(key=lambda s: -s["bank_count"])

    return scan


# ============ Backtest Skeleton ============

BACKTEST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    driver_slug TEXT NOT NULL,
    driver_text TEXT NOT NULL,
    direction TEXT NOT NULL,       -- bullish / bearish / neutral
    confidence TEXT NOT NULL,      -- high / medium / low
    bank_count INTEGER NOT NULL,
    consensus_level TEXT NOT NULL, -- full / strong / partial / isolated
    first_seen_date TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    outcome_direction TEXT,        -- correct / incorrect / pending (NULL until resolved)
    outcome_note TEXT,             -- what actually happened
    outcome_date TEXT,             -- when outcome was determined
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company, driver_slug, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_company ON signal_snapshots(company);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON signal_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_outcome ON signal_snapshots(outcome_direction);
"""


def init_backtest_db():
    conn = get_conn()
    conn.executescript(BACKTEST_SCHEMA_SQL)
    conn.commit()
    conn.close()


def save_signal_snapshot(company: str, driver_slug: str, driver_text: str,
                          direction: str, confidence: str, bank_count: int,
                          consensus_level: str, first_seen_date: str):
    """Save a snapshot of a signal for future backtesting."""
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        """INSERT OR REPLACE INTO signal_snapshots
           (company, driver_slug, driver_text, direction, confidence,
            bank_count, consensus_level, first_seen_date, snapshot_date)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (company, driver_slug, driver_text, direction, confidence,
         bank_count, consensus_level, first_seen_date, today)
    )
    conn.commit()
    conn.close()


def take_snapshots():
    """Take a snapshot of all current signals for the backtest database."""
    chains = load_all_logic_chains()
    from run_pipeline import normalize_company
    by_company = defaultdict(list)
    for c in chains:
        company = c.get("company", "")
        if company and company != "None":
            canonical = normalize_company(company)
            by_company[canonical].append(c)

    total = 0
    for company in sorted(by_company.keys()):
        try:
            signals = analyze_signal_lifecycle(company)
        except Exception:
            continue
        for s in signals:
            save_signal_snapshot(
                company=company,
                driver_slug=s["slug"],
                driver_text=s["driver"],
                direction=s["dominant_direction"],
                confidence="high" if s["bank_count"] >= 3 else "medium",
                bank_count=s["bank_count"],
                consensus_level="full" if s["bank_count"] >= 4 else (
                    "strong" if s["bank_count"] >= 3 else (
                        "partial" if s["bank_count"] >= 2 else "isolated"
                    )
                ),
                first_seen_date=s["first_seen"]
            )
            total += 1

    print(f"✅ {total} signal snapshots saved for {len(by_company)} companies")
    return total


def get_snapshot_history(company: str = "", days: int = 30) -> list[dict]:
    """Retrieve historical snapshots for comparison."""
    conn = get_conn()
    if company:
        rows = conn.execute(
            """SELECT * FROM signal_snapshots
               WHERE company = ? AND snapshot_date >= date('now', ?)
               ORDER BY snapshot_date DESC, bank_count DESC""",
            (company, f"-{days} days")
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM signal_snapshots
               WHERE snapshot_date >= date('now', ?)
               ORDER BY snapshot_date DESC, bank_count DESC""",
            (f"-{days} days",)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============ Renderers ============

def render_panel_markdown(company: str, signals: list[dict],
                           timeline: list[dict]) -> str:
    """Render signal panel as markdown."""
    state_emoji = {
        "emerging": "🆕", "strengthening": "📈",
        "stable": "✅", "diverging": "⚠️", "fading": "📉"
    }
    dir_emoji = {"bullish": "🟢↑", "bearish": "🔴↓", "neutral": "🟡→"}

    lines = [
        f"# 📊 Signal Panel: {company}",
        f"**{len(signals)}** drivers tracked · {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "---",
        "",
        "## Driver Lifecycle",
        "",
    ]

    for s in signals:
        emoji = state_emoji.get(s["state"], "❓")
        d_emoji = dir_emoji.get(s["dominant_direction"], "")
        dispute = " ⚠️DISPUTE" if s["has_dispute"] else ""

        lines.append(f"### {emoji} {s['driver'][:70]}{dispute}")
        lines.append(f"**State:** {s['state']} | **Direction:** {d_emoji} | "
                     f"**Banks:** {len(s['banks'])} ({', '.join(s['banks'][:4])})")
        lines.append(f"**Mentions:** {s['mention_count']} | "
                     f"**Span:** {s['date_span_days']}d | "
                     f"**First seen:** {s['first_seen']}")

        if s["has_dispute"]:
            lines.append(f"**Direction split:** {s['directions']}")

        lines.append("")

    # Timeline
    if timeline:
        lines.append("---")
        lines.append("")
        lines.append("## Signal Timeline")
        lines.append("")
        for entry in timeline[-7:]:  # Last 7 dates
            lines.append(f"### {entry['date']} ({entry['driver_count']} drivers)")
            for d in entry["drivers"][:5]:
                d_emoji = dir_emoji.get(d["dominant_direction"], "")
                lines.append(f"- {d_emoji} {d['driver'][:60]} "
                             f"({', '.join(d['banks'][:3])})")
            lines.append("")

    return "\n".join(lines)


def render_scan_markdown(scan: dict) -> str:
    """Render market scan as markdown."""
    lines = [
        f"# 🔍 全市场信号扫描 — {datetime.now().strftime('%Y-%m-%d')}",
        "",
    ]

    if scan["emerging"]:
        lines.append("## 🆕 Emerging Signals (new drivers)")
        lines.append("")
        for s in scan["emerging"][:10]:
            lines.append(f"- **{s['company']}**: {s['driver'][:60]}")
            lines.append(f"  {s['bank_count']} banks: {', '.join(s['banks'][:3])}")
        lines.append("")

    if scan["diverging"]:
        lines.append("## ⚠️ Diverging Signals (bull-bear split)")
        lines.append("")
        for s in scan["diverging"][:10]:
            lines.append(f"- **{s['company']}**: {s['driver'][:60]}")
            lines.append(f"  Direction split: {s['directions']} | Banks: {', '.join(s['banks'][:3])}")
        lines.append("")

    if scan["high_consensus"]:
        lines.append("## ✅ High Consensus Signals (≥4 banks)")
        lines.append("")
        for s in scan["high_consensus"][:10]:
            lines.append(f"- **{s['company']}**: {s['driver'][:60]}")
            lines.append(f"  {s['bank_count']} banks agree: {s['dominant_direction']}")
        lines.append("")

    return "\n".join(lines)


# ============ Helpers ============

def _slugify(text: str) -> str:
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '_', slug)
    return slug[:80]


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Panel & Backtest Framework")
    parser.add_argument("--company", help="Company name for signal panel")
    parser.add_argument("--all", action="store_true", help="Panel for all companies")
    parser.add_argument("--signals", action="store_true", help="Full market signal scan")
    parser.add_argument("--timeline", action="store_true", help="Include timeline")
    parser.add_argument("--snapshot", action="store_true", help="Take snapshot for backtest")
    parser.add_argument("--output", help="Save output to file")

    args = parser.parse_args()

    if args.snapshot:
        init_backtest_db()
        take_snapshots()
    elif args.signals:
        scan = scan_all_signals()
        md = render_scan_markdown(scan)
        print(md[:3000])
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
    elif args.company:
        signals = analyze_signal_lifecycle(args.company)
        chains = load_all_logic_chains()
        timeline = build_timeline(chains, args.company) if args.timeline else []
        md = render_panel_markdown(args.company, signals, timeline)
        print(md[:3000])
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
    elif args.all:
        chains = load_all_logic_chains()
        from run_pipeline import normalize_company
        by_company = defaultdict(list)
        for c in chains:
            company = c.get("company", "")
            if company and company != "None":
                canonical = normalize_company(company)
                by_company[canonical].append(c)
        for company in sorted(by_company.keys()):
            try:
                signals = analyze_signal_lifecycle(company)
                emerging = sum(1 for s in signals if s["state"] == "emerging")
                diverging = sum(1 for s in signals if s["state"] == "diverging")
                stable = sum(1 for s in signals if s["state"] == "stable")
                print(f"  {company:<35} {len(signals):>3} drivers  "
                      f"🆕{emerging} ⚠️{diverging} ✅{stable}")
            except Exception as e:
                print(f"  {company:<35} ERROR: {e}")
    else:
        parser.print_help()
