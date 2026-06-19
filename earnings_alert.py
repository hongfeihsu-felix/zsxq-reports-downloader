#!/usr/bin/env python3
"""Earnings surprise alerts — detect significant beat/miss and push notifications."""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "valuation.db"
THRESHOLD_PCT = 10.0  # Surprise > 10% triggers alert


def scan_surprises() -> list[dict]:
    """Scan recent earnings for significant surprises."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Find surprises from past 30 days
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT * FROM earnings_actuals
           WHERE period >= ? AND surprise_pct IS NOT NULL
           AND ABS(surprise_pct) > ?
           ORDER BY ABS(surprise_pct) DESC""",
        (cutoff, THRESHOLD_PCT)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_alert(earnings: list[dict]) -> str:
    """Format earnings surprises as a push message."""
    if not earnings:
        return ""

    beats = [e for e in earnings if e["surprise_pct"] > 0]
    misses = [e for e in earnings if e["surprise_pct"] < 0]

    lines = ["📊 财报 Surprise 扫描"]
    if beats:
        lines.append(f"\n🟢 Beat ({len(beats)}):")
        for e in beats[:5]:
            lines.append(f"  {e['company']}: +{e['surprise_pct']:.1f}% (实际 {e['eps_actual']} vs 预期 {e['eps_estimate']})")
    if misses:
        lines.append(f"\n🔴 Miss ({len(misses)}):")
        for e in misses[:5]:
            lines.append(f"  {e['company']}: {e['surprise_pct']:.1f}% (实际 {e['eps_actual']} vs 预期 {e['eps_estimate']})")

    return "\n".join(lines)


def push_surprise_alert(dry_run: bool = False) -> dict:
    """Scan surprises and push WeChat alert if significant."""
    surprises = scan_surprises()
    if not surprises:
        return {"status": "no_surprises", "count": 0}

    text = format_alert(surprises)
    print(text)

    if not dry_run:
        try:
            from wechat_push import push_draft
            title = "📊 财报 Surprise 扫描"
            html = text.replace("\n", "<br>").replace("🟢", '<span style="color:#3fb950">🟢</span>').replace("🔴", '<span style="color:#f85149">🔴</span>')
            push_draft(title, f"<p>{html}</p>", digest=text.split('\n')[0])
            return {"status": "pushed", "count": len(surprises)}
        except Exception as e:
            return {"status": "push_failed", "error": str(e)}

    return {"status": "dry_run", "count": len(surprises)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = push_surprise_alert(dry_run=args.dry_run)
    print(f"\nResult: {result}")
