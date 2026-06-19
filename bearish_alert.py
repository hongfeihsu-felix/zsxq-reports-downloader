#!/usr/bin/env python3
"""
Bearish Signal Alert — scan logic_chains for tracked-company bearish signals and email.

Triggered after pipeline run. Deduplicates via push_history.json.

用法:
  python3 bearish_alert.py                     # scan last 24h, email if new
  python3 bearish_alert.py --hours 48           # wider window
  python3 bearish_alert.py --dry-run            # console only, no email
  python3 bearish_alert.py --company SMIC       # single company
"""

import json
import hashlib
import smtplib
import sqlite3
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "logic_chains.db"
CONFIG_PATH = PROJECT_DIR / "config.json"
HISTORY_PATH = PROJECT_DIR / "push_history.json"

# ── Config ──────────────────────────────────────────────

def _load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}

CFG = _load_config()
EMAIL = CFG.get("email", {})
TRACKED = CFG.get("tracking", {}).get("companies", [])

# Build company name → canonical mapping (lowercase keys)
TRACKED_LOOKUP: dict[str, dict] = {}
for c in TRACKED:
    name = c["name"].lower()
    TRACKED_LOOKUP[name] = c
    for kw in c.get("keywords", []):
        TRACKED_LOOKUP[kw.lower()] = c
    ticker = c.get("ticker", "").split(".")[0]
    if ticker:
        TRACKED_LOOKUP[ticker.lower()] = c


# ── Query ───────────────────────────────────────────────

def scan_bearish_signals(hours: int = 24) -> list[dict]:
    """Query logic_chains for bearish signals within the time window."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    query = """SELECT id, company, bank, date, driver_slug, driver_raw, direction,
                      confidence, ticker, created_at
               FROM logic_chains
               WHERE direction = 'bearish'
                 AND created_at >= ?
               ORDER BY created_at DESC"""
    rows = conn.execute(query, (cutoff,)).fetchall()
    conn.close()

    signals = []
    for r in rows:
        d = dict(r)
        signals.append(d)

    return signals


# ── Match Tracked Companies ─────────────────────────────

def match_tracked(signals: list[dict]) -> list[dict]:
    """Filter signals to only those matching tracked companies."""
    matched = []
    for sig in signals:
        company = (sig.get("company") or "").strip()
        ticker = (sig.get("ticker") or "").strip()

        # Direct match
        canonical = TRACKED_LOOKUP.get(company.lower())
        if not canonical and ticker:
            canonical = TRACKED_LOOKUP.get(ticker.lower())

        # Fuzzy: check if any tracked keyword appears in company name
        if not canonical:
            for kw, c in TRACKED_LOOKUP.items():
                if len(kw) >= 4 and kw in company.lower():
                    canonical = c
                    break

        if canonical:
            sig["_canonical_name"] = canonical["name"]
            sig["_canonical_ticker"] = canonical.get("ticker", "")
            matched.append(sig)

    return matched


# ── Dedup ───────────────────────────────────────────────

def _signal_key(sig: dict) -> str:
    """Unique key for dedup: canonical_company + driver_slug hash."""
    raw = f"{sig.get('_canonical_name','')}|{sig.get('driver_slug','')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_sent_keys() -> set[str]:
    """Load previously sent signal keys from push_history."""
    if not HISTORY_PATH.exists():
        return set()
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return set()
    return {h.get("key", "") for h in history if h.get("type") == "bearish_alert"}


def record_sent(signals: list[dict]):
    """Append sent signal keys to push_history."""
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            history = []

    for sig in signals:
        history.append({
            "type": "bearish_alert",
            "key": _signal_key(sig),
            "company": sig.get("_canonical_name", ""),
            "bank": sig.get("bank", ""),
            "driver": (sig.get("driver_slug") or "")[:100],
            "pushed_at": time.time(),
        })

    HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False))


# ── Format ──────────────────────────────────────────────

def format_email(signals: list[dict]) -> str:
    """Format bearish signals as email body."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"🔴 Bearish Signals — {today}",
        "",
        f"检测到 {len(signals)} 条看空信号，涉及 {len(set(s['_canonical_name'] for s in signals))} 家关注公司。",
        "",
    ]

    # Group by company
    by_company = defaultdict(list)
    for s in signals:
        by_company[s["_canonical_name"]].append(s)

    for name, items in sorted(by_company.items()):
        lines.append(f"{'─' * 50}")
        lines.append(f"  📌 {name} ({len(items)} signals)")
        lines.append(f"{'─' * 50}")

        for s in items:
            bank = s.get("bank") or "?"
            confidence = s.get("confidence") or "medium"
            conf_icon = "🔴" if confidence == "high" else "🟡"
            driver = (s.get("driver_slug") or s.get("driver_raw") or "")[:120]

            lines.append(f"  {conf_icon} [{bank}] {driver}")
            lines.append(f"     confidence: {confidence}")
            lines.append("")

    lines.append(f"{'─' * 50}")
    lines.append("此邮件由 Hermes 投研系统自动生成。")
    return "\n".join(lines)


def format_console(signals: list[dict]) -> str:
    """Console output format."""
    if not signals:
        return "✅ No bearish signals for tracked companies."

    lines = [
        f"\n🔴 Bearish Signals — {len(signals)} signals, "
        f"{len(set(s['_canonical_name'] for s in signals))} companies",
    ]
    for s in signals:
        bank = (s.get("bank") or "?")[:12]
        name = s.get("_canonical_name", "?")
        driver = (s.get("driver_slug") or "")[:80]
        conf = s.get("confidence", "?")
        lines.append(f"  [{bank}] {name}: {driver} ({conf})")

    return "\n".join(lines)


# ── Send ────────────────────────────────────────────────

def send_bearish_alert(signals: list[dict], recipient: str = None) -> bool:
    """Send bearish signal email notification."""
    if not signals:
        print("📧 No bearish signals, skipping email.")
        return True

    body = format_email(signals)
    recipient = recipient or EMAIL.get("recipient_email", "")

    if not recipient or not EMAIL.get("smtp_server"):
        print("⚠️  Email not configured. Skipping send.")
        return False

    msg = MIMEMultipart()
    msg["From"] = EMAIL.get("sender_email", "")
    msg["To"] = recipient

    high_count = sum(1 for s in signals if s.get("confidence") == "high")
    today = datetime.now().strftime("%Y-%m-%d")
    msg["Subject"] = (
        f"🔴 Hermes Bearish Alert — {today} "
        f"({len(signals)} signals, {high_count} high-confidence)"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        server = smtplib.SMTP(EMAIL["smtp_server"], EMAIL["smtp_port"])
        server.starttls()
        server.login(EMAIL["sender_email"], EMAIL["sender_password"])
        server.send_message(msg)
        server.quit()
        print(f"📧 Bearish alert email sent to {recipient}")
        return True
    except Exception as e:
        print(f"❌ Email send failed: {e}")
        return False


# ── Main ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bearish Signal Alert")
    parser.add_argument("--hours", type=int, default=24, help="Scan window in hours")
    parser.add_argument("--company", help="Filter to single company")
    parser.add_argument("--dry-run", action="store_true", help="Console only, no email")
    parser.add_argument("--recipient", help="Email recipient override")
    args = parser.parse_args()

    # Scan
    raw = scan_bearish_signals(hours=args.hours)
    if not raw:
        print("✅ No bearish signals found in window.")
        return

    # Match tracked companies
    matched = match_tracked(raw)
    if not matched:
        print(f"✅ Found {len(raw)} bearish signals, but none match tracked companies.")
        return

    # Filter by canonical company name if specified
    if args.company:
        matched = [s for s in matched
                   if args.company.lower() in s.get("_canonical_name", "").lower()]

    # Dedup
    sent_keys = load_sent_keys()
    new_signals = [s for s in matched if _signal_key(s) not in sent_keys]

    if not new_signals:
        print(f"✅ {len(matched)} matched signals, all previously sent. Skipping.")
        return

    print(f"📋 {len(matched)} matched → {len(new_signals)} new (deduped).")

    # Console output
    print(format_console(new_signals))

    if args.dry_run:
        print("\n🏃 Dry run — email not sent.")
        return

    # Send email
    ok = send_bearish_alert(new_signals, recipient=args.recipient)
    if ok:
        record_sent(new_signals)


if __name__ == "__main__":
    main()
