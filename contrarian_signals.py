#!/usr/bin/env python3
"""
Contrarian Signal Detection — 反共识信号引擎

从 aggregated_drivers 中识别被市场主流忽视的孤立观点，
按 Evidence权重 × 方向背离度 × 时效性衰减 综合评分。

用法:
  python3 contrarian_signals.py                  # 扫描所有公司，输出 Top 信号
  python3 contrarian_signals.py --company TSMC    # 单公司
  python3 contrarian_signals.py --min-score 5.0   # 最低分数过滤
  python3 contrarian_signals.py --save            # 持久化到 DB
"""

import sqlite3
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent / "logic_chains.db"


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contrarian_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            driver_slug TEXT NOT NULL,
            driver_raw TEXT NOT NULL,
            direction TEXT NOT NULL,
            majority_direction TEXT NOT NULL,
            majority_driver_count INTEGER NOT NULL DEFAULT 0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            impact_count INTEGER NOT NULL DEFAULT 0,
            contrarian_score REAL NOT NULL DEFAULT 0,
            source_bank TEXT NOT NULL DEFAULT '',
            source_date TEXT,
            verified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(company, driver_slug)
        )
    """)
    conn.commit()


def compute_contrarian_score(evidence_count: int, direction: str,
                              majority_direction: str, majority_driver_count: int,
                              days_ago: int) -> float:
    """计算反共识信号强度 (0-10)."""
    score = 0.0

    # 1. Evidence 权重 (0-3分)
    # 平均 evidence 约 3-4 条，>10 条加满分
    evidence_weight = min(evidence_count / 4.0, 1.0) * 3.0
    score += evidence_weight

    # 2. 方向背离度 (0-4分)
    if direction != majority_direction and direction in ("bullish", "bearish"):
        divergence = 2.0  # 方向相反
        if majority_driver_count >= 5:
            divergence += 1.5  # 主流非常强
        if majority_driver_count >= 8:
            divergence += 0.5  # 主流极度一致
        score += min(divergence, 4.0)
    elif direction == majority_direction:
        score += 0  # 方向一致，无背离分
    else:
        score += 1.0  # neutral vs directional

    # 3. 时效性衰减 (0-3分)
    if days_ago <= 7:
        recency = 3.0
    elif days_ago <= 14:
        recency = 2.1
    elif days_ago <= 30:
        recency = 0.9
    else:
        recency = 0.0
    score += recency

    return round(score, 1)


def detect_signals(company_filter: str = None, min_score: float = 0) -> list[dict]:
    """扫描所有 aggregated_drivers，检测反共识信号。"""
    conn = _get_conn()

    # Load all aggregated drivers
    if company_filter:
        rows = conn.execute(
            "SELECT company, driver_slug, bank_count, aggregated_json FROM aggregated_drivers WHERE company = ?",
            (company_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT company, driver_slug, bank_count, aggregated_json FROM aggregated_drivers"
        ).fetchall()

    # Group by company
    company_data = defaultdict(list)
    for row in rows:
        data = json.loads(row["aggregated_json"])
        company_data[row["company"]].append({
            "driver_slug": row["driver_slug"],
            "driver_raw": data.get("canonical", row["driver_slug"]),
            "direction": data.get("direction", "neutral"),
            "consensus_level": "isolated" if row["bank_count"] == 1 else (
                "partial" if row["bank_count"] == 2 else (
                    "strong" if row["bank_count"] == 3 else "full"
                )
            ),
            "bank_count": row["bank_count"],
            "evidence_count": len(data.get("evidence_matrix", [])),
            "impact_count": len(data.get("impact_graph", [])),
            "banks": data.get("banks", []),
            "aggregated_json": data,
        })

    # Get latest dates from logic_chains for recency
    date_rows = conn.execute("""
        SELECT company, MAX(date) as latest_date
        FROM logic_chains GROUP BY company
    """).fetchall()
    company_latest = {r["company"]: r["latest_date"] for r in date_rows}

    today = datetime.now().strftime("%Y-%m-%d")
    signals = []

    for company, drivers in company_data.items():
        if len(drivers) < 2:
            continue  # Need at least 2 drivers to determine majority

        # Determine majority direction
        dir_counts = defaultdict(int)
        for d in drivers:
            if d["direction"] in ("bullish", "bearish"):
                dir_counts[d["direction"]] += 1
        if not dir_counts:
            continue
        majority_dir = max(dir_counts, key=dir_counts.get)
        majority_count = dir_counts[majority_dir]

        # Find isolated drivers
        for d in drivers:
            if d["consensus_level"] not in ("isolated", "partial"):
                continue
            if d["direction"] not in ("bullish", "bearish"):
                continue

            # Only flag if direction differs from majority OR majority is very strong
            if d["direction"] == majority_dir and majority_count < 5:
                continue

            # Calculate recency
            latest = company_latest.get(company, today)
            try:
                latest_dt = datetime.strptime(latest, "%Y-%m-%d")
                days_ago = (datetime.now() - latest_dt).days
            except (ValueError, TypeError):
                days_ago = 14  # default

            score = compute_contrarian_score(
                d["evidence_count"], d["direction"],
                majority_dir, majority_count, days_ago
            )

            if score >= min_score:
                signals.append({
                    "company": company,
                    "driver_slug": d["driver_slug"],
                    "driver_raw": d["driver_raw"],
                    "direction": d["direction"],
                    "majority_direction": majority_dir,
                    "majority_driver_count": majority_count,
                    "evidence_count": d["evidence_count"],
                    "impact_count": d["impact_count"],
                    "contrarian_score": score,
                    "source_bank": d["banks"][0] if d["banks"] else "unknown",
                    "source_date": latest,
                })

    conn.close()

    # Dedup: keep only highest-scored signal per (company, source_bank) pair
    seen = {}
    deduped = []
    for s in sorted(signals, key=lambda s: s["contrarian_score"], reverse=True):
        key = (s["company"], s["source_bank"])
        if key not in seen:
            seen[key] = s
            deduped.append(s)
        elif s["contrarian_score"] > seen[key]["contrarian_score"]:
            deduped.remove(seen[key])
            seen[key] = s
            deduped.append(s)

    deduped.sort(key=lambda s: s["contrarian_score"], reverse=True)
    return deduped


def save_signals(signals: list[dict]):
    """持久化反共识信号到 DB."""
    conn = _get_conn()
    _ensure_table(conn)

    for s in signals:
        conn.execute("""
            INSERT OR REPLACE INTO contrarian_signals
            (company, driver_slug, driver_raw, direction, majority_direction,
             majority_driver_count, evidence_count, impact_count, contrarian_score,
             source_bank, source_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s["company"], s["driver_slug"], s["driver_raw"], s["direction"],
            s["majority_direction"], s["majority_driver_count"],
            s["evidence_count"], s["impact_count"], s["contrarian_score"],
            s["source_bank"], s["source_date"]
        ))

    conn.commit()
    conn.close()


def get_top_signals(limit: int = 20, min_score: float = 0) -> list[dict]:
    """从 DB 获取 Top 反共识信号."""
    conn = _get_conn()
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM contrarian_signals WHERE contrarian_score >= ? ORDER BY contrarian_score DESC LIMIT ?",
        (min_score, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Contrarian Signal Detection")
    parser.add_argument("--company", help="Filter by company")
    parser.add_argument("--min-score", type=float, default=4.0, help="Minimum score (default: 4.0)")
    parser.add_argument("--save", action="store_true", help="Persist to DB")
    parser.add_argument("--top", type=int, default=20, help="Show top N signals")
    args = parser.parse_args()

    signals = detect_signals(args.company, args.min_score)
    print(f"Found {len(signals)} contrarian signals (score >= {args.min_score})")
    print()

    for i, s in enumerate(signals[:args.top]):
        arrow = "↑" if s["direction"] == "bullish" else "↓"
        majority_arrow = "↑" if s["majority_direction"] == "bullish" else "↓"
        print(f"#{i+1} [{s['contrarian_score']:.1f}] {s['company']}: {arrow} '{s['driver_raw'][:55]}'")
        print(f"    主流: {majority_arrow} ({s['majority_driver_count']} banks) | "
              f"{s['source_bank']} 独唱反调 | evidence:{s['evidence_count']} impacts:{s['impact_count']}")
        print()

    if args.save:
        save_signals(signals)
        print(f"Saved {len(signals)} signals to DB")


if __name__ == "__main__":
    main()
